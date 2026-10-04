"""Personal data never reaches the LLM.

Every request to a model passes through a PrivacyGuard. Personal data found in
the description is replaced by labels such as [ФИО_1] or [ТЕЛЕФОН_1] before the
request leaves the machine; labels in the model's answer are turned back into
the original fragments locally, so the diagram shows real data and quotes still
match the description.

Layers, from cheapest to last resort:
1. rules for structured identifiers, with checksums where they exist
   (ИНН, ОГРН, card numbers by Luhn), phones, e-mail, passport, СНИЛС, accounts,
   addresses, birth dates, cadastral and document numbers;
2. a local NER model (Natasha/Slovnet, works offline) for names, plus rules for
   names with initials or a patronymic;
3. the analyst: any fragment can be hidden by hand; uncertain findings (names,
   addresses, document numbers) can be released, structured identifiers cannot;
4. egress check: right before sending, every outgoing message is scanned by the
   rules again, and whatever is left is masked as well.

The same layer removes invisible characters (zero-width, bidi overrides, Unicode
tag characters used to smuggle hidden text) and hides instructions addressed to
the AI (prompt injection) from the model.
"""
from __future__ import annotations

import re
import threading
from collections import Counter
from dataclasses import dataclass

# kind: (label, title, releasable). Releasable findings may be uncertain, so the
# analyst can send them anyway; structured identifiers are always hidden.
KINDS = {
    "analyst": ("СКРЫТО", "Скрыто вручную", False),
    "injection": ("КОМАНДА", "Команда для ИИ", True),
    "email": ("EMAIL", "E-mail", False),
    "phone": ("ТЕЛЕФОН", "Телефон", False),
    "passport": ("ПАСПОРТ", "Паспорт", False),
    "snils": ("СНИЛС", "СНИЛС", False),
    "inn": ("ИНН", "ИНН", False),
    "ogrn": ("ОГРН", "ОГРН", False),
    "card": ("КАРТА", "Банковская карта", False),
    "account": ("СЧЕТ", "Банковский счёт", False),
    "personal_account": ("ЛИЦЕВОЙ_СЧЕТ", "Лицевой счёт", False),
    "birth_date": ("ДАТА_РОЖДЕНИЯ", "Дата рождения", False),
    "cadastral": ("КАДАСТР", "Кадастровый номер", False),
    "address": ("АДРЕС", "Адрес", True),
    "doc_number": ("НОМЕР", "Номер документа", True),
    "person": ("ФИО", "ФИО", True),
}
PRIORITY = {kind: i for i, kind in enumerate(KINDS)}   # earlier wins when findings overlap
LABEL_KIND = {label: kind for kind, (label, _, _) in KINDS.items()}

# Placeholders are restored inside model answers, including Python string literals,
# so a hidden fragment never contains quotes, backslashes or line breaks.
UNSAFE = re.compile(r"[\"'\\\n\r]")

# --------------------------------------------------------------------------- invisible text
INVISIBLE = re.compile("[­᠎​-‏‪-‮⁠-⁤⁦-⁩"
                       "︀-️﻿\U000e0000-\U000e007f]")


def clean_text(text: str) -> tuple[str, dict]:
    """Remove invisible characters; decode text smuggled with Unicode tag characters."""
    found = INVISIBLE.findall(text)
    hidden = "".join(chr(ord(c) - 0xE0000) for c in found if 0xE0020 <= ord(c) <= 0xE007E)
    return INVISIBLE.sub("", text), {"removed": len(found), "hidden_message": hidden.strip()}


# --------------------------------------------------------------------------- rules
@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    kind: str
    text: str
    source: str = "rule"          # rule | ner | analyst


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _inn_ok(s: str) -> bool:
    d = [int(c) for c in _digits(s)]
    def check(coefs, digits):
        return sum(c * x for c, x in zip(coefs, digits)) % 11 % 10
    if len(d) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[9]
    if len(d) == 12:
        return (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[10]
                and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[11])
    return False


def _ogrn_ok(s: str) -> bool:
    d = _digits(s)
    if len(d) == 13:
        return int(d[:12]) % 11 % 10 == int(d[12])
    if len(d) == 15:
        return int(d[:14]) % 13 % 10 == int(d[14])
    return False


def _luhn_ok(s: str) -> bool:
    d = [int(c) for c in _digits(s)][::-1]
    return len(d) >= 13 and sum(x if i % 2 == 0 else (x * 2 - 9 if x > 4 else x * 2)
                                for i, x in enumerate(d)) % 10 == 0


def _phone_ok(s: str) -> bool:
    n = len(_digits(s))
    return 11 <= n <= 13 if s.lstrip().startswith(("+", "8")) else 10 <= n <= 11


def _near(text: str, start: int, pattern: str, before: int = 60) -> bool:
    return re.search(pattern, text[max(0, start - before):start], re.I) is not None


SEP = r"[\s\-–]*"
RULES: list[tuple[str, re.Pattern, int, object]] = [
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-zА-Яа-яЁё]{2,}"), 0, None),
    ("phone", re.compile(rf"(?<![\w+])(?:\+{SEP}\d{{1,3}}|8){SEP}\(?\d{{2,5}}\)?(?:{SEP}\d){{5,7}}(?!\d)"), 0,
     lambda m, t: _phone_ok(m.group())),
    ("phone", re.compile(rf"(?<![\w(])\(\d{{3,5}}\)(?:{SEP}\d){{5,7}}(?!\d)"), 0, lambda m, t: _phone_ok(m.group())),
    ("phone", re.compile(r"(?i)(?:тел(?:ефон\w*)?|моб(?:ильн\w*)?|сот(?:ов\w*)?|факс|whatsapp|telegram|телеграм)"
                         r"\.?\s*(?:[:№]\s*)?(\+?\d[\d\s\-–()]{3,18}\d)"), 1,
     lambda m, t: 5 <= len(_digits(m.group(1))) <= 13),
    ("snils", re.compile(r"(?<!\d)\d{3}[\-\s]\d{3}[\-\s]\d{3}[\-\s]\d{2}(?!\d)"), 0, None),
    ("snils", re.compile(r"(?i)СНИЛС\s*[:№]?\s*(\d{11})(?!\d)"), 1, None),
    ("passport", re.compile(r"(?i)сери[яи]\s*\d{2}\s?\d{2}\s*(?:№|номер|N)\s*\d{6}(?!\d)"), 0, None),
    ("passport", re.compile(r"(?i)код\w*\s+подразделени\w*\s*:?\s*(\d{3}[-\s]?\d{3})(?!\d)"), 1, None),
    ("passport", re.compile(r"(?i)дат\w*\s+выдачи\s*:?\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4}(?:\s*г\.)?)"), 1,
     lambda m, t: _near(t, m.start(), r"паспорт", 150)),
    ("passport", re.compile(r"(?<!\d)\d{2}\s?\d{2}\s?(?:№\s?)?\d{6}(?!\d)"), 0,
     lambda m, t: _near(t, m.start(), r"паспорт")),
    ("inn", re.compile(r"(?i)ИНН\s*[:№]?\s*(\d{12}|\d{10})(?!\d)"), 1, None),
    ("ogrn", re.compile(r"(?i)ОГРН(?:ИП)?\s*[:№]?\s*(\d{15}|\d{13})(?!\d)"), 1, None),
    ("card", re.compile(r"(?<!\d)\d{4}(?:[\s\-]?\d{4}){3}(?!\d)"), 0, lambda m, t: _luhn_ok(m.group())),
    ("card", re.compile(r"(?i)карт\w*[^.\n\d]{0,20}(\d{4}(?:[\s\-]?\d{4}){3})(?!\d)"), 1, None),
    ("account", re.compile(r"(?<!\d)\d{20}(?!\d)"), 0, None),
    ("account", re.compile(r"(?i)(?:р/с|к/с|расч[её]тн\w*\s+сч[её]т\w*|корр\w*\s+сч[её]т\w*)\s*[:№]?\s*"
                           r"(\d[\d\s]{18,26}\d)"), 1, lambda m, t: len(_digits(m.group(1))) == 20),
    ("personal_account", re.compile(r"(?<![\w/])(?:[Лл]ицев\w*\s+[Сс]ч[её]т\w*(?:\s+[а-яё]+){0,2}?|л/с|Л/С|ЛС)"
                                    r"\s*(?:№|N|No\.?)?\s*:?\s*"
                                    r"([A-Za-zА-ЯЁа-яё0-9][\w\-/]*\d[\w\-/]*)"), 1, None),
    ("inn", re.compile(r"(?<!\d)(?:\d{12}|\d{10})(?!\d)"), 0, lambda m, t: _inn_ok(m.group())),
    ("ogrn", re.compile(r"(?<!\d)(?:\d{15}|\d{13})(?!\d)"), 0, lambda m, t: _ogrn_ok(m.group())),
    ("birth_date", re.compile(r"(?i)(?:дат\w*\s+рождения|д\.\s?р\.|родил(?:ся|ась)|г\.\s?р\.)\s*:?\s*"
                              r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}\s+[а-я]+\s+\d{4}(?:\s?г\.?)?)"), 1, None),
    ("birth_date", re.compile(r"(?i)(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4})\s*(?:г\.?\s?р\.|года\s+рождения)"), 1, None),
    ("cadastral", re.compile(r"(?<!\d)\d{2}:\d{2}:\d{6,7}:\d{1,6}(?!\d)"), 0, None),
    ("doc_number", re.compile(
        r"(?i)(?<![а-яё])(?:договор|контракт|заявк|заявлени|обращени|квитанци|полис|удостоверени|свидетельств|"
        r"доверенност|сч[её]т|акт|талон|наряд)[а-яё\-]*(?:\s+(?:[а-яё\-]+|от\s+\d{1,2}\.\d{1,2}\.\d{2,4}(?:\s*г\.)?)){0,3}?"
        r"\s*(?:№|N\s|No\.?|номер\s)\s*"
        r"([A-Za-zА-ЯЁа-яё0-9]*\d(?:[\w\-/.]*\w)?)"), 1, None),
]

# ФИО by form: surname + initials, initials + surname, a patronymic.
ROLE_WORDS = set("""заявитель заявителя специалист инженер мастер начальник руководитель директор менеджер клиент
потребитель абонент оператор диспетчер инспектор сотрудник работник бухгалтер юрист главный ведущий старший
если затем после когда далее также гражданин гражданка господин госпожа исполнитель ответственный контролёр
контролер электромонтёр электромонтер монтёр монтер курьер кассир секретарь агент представитель собственник
владелец арендатор покупатель продавец юрисконсульт экономист аналитик техник электрик водитель бригадир прораб
заместитель председатель член эксперт куратор координатор администратор""".split())
SURNAME = (r"[А-ЯЁ][а-яё]*(?:(?:ов|ев|ёв|ин|ын)(?:а|у|ым|ом|е|ой|ою|ых)?"
           r"|(?:ск|цк)(?:ий|ого|ому|им|ом|ая|ой|ую|ою))")
PERSON_RULES = [
    re.compile(r"(?<![\w.])[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?"),
    re.compile(r"(?<![\w.])[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.\s?)?[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)?"),
    # «Иван Сергеевич», «Анне Владимировне», «Петрову Ивану Сергеевичу»: the patronymic in every case.
    # A word in front is taken only when it looks like a surname, so «Позвонить Анне Владимировне» keeps the verb.
    re.compile(r"(?<![\w])(?:" + SURNAME + r"\s+)?[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+"
               r"(?:(?:ович|евич|ьич)(?:а|у|ем|е)?|(?:овн|евн|ичн|иничн)(?:а|ы|е|у|ой|ою))"
               r"(?:\s+[А-ЯЁ][а-яё]+(?:ов|ев|ёв|ин|ын|ский|цкий|ова|ева|ёва|ина|ына|ская|цкая)"
               r"(?:а|у|ым|ом|е|ой|ую)?)?(?![\w])"),
]
PERSON_CONTEXT = re.compile(r"(?<![\w-])(?:[Гг]ражданин\w*|г-н|г-жа|[Гг]осподин\w*|[Гг]оспож\w*|ИП|тов\.)\s+"
                            r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)")
NOT_PERSON = re.compile(r"(?i)(энерго|сет[ьи]|сбыт|банк|строй|ком|групп|холдинг|центр|инвест|сервис|трест|про)$")

# Addresses: a chain of components; masked when it has a street, or a house number together with
# another component («д. 5, кв. 12»). A lone «участок 5» or «дом 3» is not an address.
HOUSE_NUMBER = r"(?:,?\s*\d+[а-яА-Я]?(?:[/\-]\d+[а-яА-Я]?)?(?![\d\w.,]*\s*(?:раз|час|дн|кВ|кв\.\s*м|%)))?"
STREET_CASES = (r"(?:улиц(?:е|у|ы|ей)|проспект(?:а|е|у|ом)|переул(?:ка|ке|ку|ком)|бульвар(?:а|е|у|ом)|"
                r"площад(?:и|ью)|набережн(?:ой|ую)|проезд(?:а|е|у|ом)|микрорайон(?:а|е|у|ом)|тупик(?:а|е|у|ом)|"
                r"алле(?:и|е|ю|ей)|шоссе)")
PLACE_NAME = r"[А-ЯЁ][а-яё][\wё\-]*"           # «Мира», «Дружбы»; not an abbreviation like «ПС», «РЭС»
CROSS_NAMES = r"(?:улиц\w*\s+|проспект\w*\s+)?[А-ЯЁа-яё][а-яё\-]{2,}(?:\s+" + PLACE_NAME + r")?"
ADDRESS_PARTS = [
    ("postal", re.compile(r"(?<!\d)\d{6}(?!\d)")),
    ("region", re.compile(r"[А-ЯЁ][а-яё\-]+(?:ая|ий|ой|ый)\s+(?:обл\.|область|край|р-н|район)"
                          r"|(?:[Рр]есп\.|[Рр]еспублика)\s+[А-ЯЁ][а-яё\-]+")),
    ("settlement", re.compile(r"(?<![\w])(?:г\.|гор\.|город|пос\.|посёлок|поселок|пгт\.?|с\.|село|дер\.|деревня|"
                              r"д\.(?=\s*[А-ЯЁ])|ст-ца|станица|снт|СНТ|ДНТ|х\.|хутор)\s*«?[А-ЯЁ][\wё\-]*"
                              r"(?:[\s\-][А-ЯЁ][\wё\-]*)?»?")),
    ("street", re.compile(r"(?<![\w])(?:ул\.|улица|пр-т|пр\.|просп\.|проспект|пер\.|переулок|ш\.|шоссе|наб\.|"
                          r"набережная|б-р|бул\.|бульвар|пл\.|площадь|проезд|пр-д|мкр\.?|микрорайон|туп\.|тупик|"
                          r"аллея)\s*(?:\d+-?[яйо]\s+)?[А-ЯЁ0-9][\wё\-.]*(?:\s+[А-ЯЁ][\wё\-]*)?" + HOUSE_NUMBER +
                          r"|[А-ЯЁ][\wё\-]+\s+(?:улица|проспект|переулок|шоссе|бульвар|проезд|набережная|площадь|"
                          r"ул\.|пр-т|просп\.|пер\.|ш\.|б-р|наб\.|пл\.)" + HOUSE_NUMBER)),
    # Case forms («по улице Дружбы», «на Садовой улице»): only with a capitalised name, so that
    # «на улице выполняются работы», «на площади 50 м²» or «площади ПС» stay process text.
    ("street", re.compile(r"(?<![\w])" + STREET_CASES + r"\s+(?:\d+-?[яйо]\s+)?" + PLACE_NAME +
                          r"(?:\s+" + PLACE_NAME + r")?" + HOUSE_NUMBER +
                          r"|(?<![\w])" + PLACE_NAME + r"\s+" + STREET_CASES + r"(?![\w])" + HOUSE_NUMBER)),
    # A place named by a crossing: «на перекрёстке Машерова и Дружбы», «на углу Ленина и Мира».
    # After «перекрёсток» the names may be typed in lower case; «угол» and «пересечение» need a capitalised
    # name («в углу помещения», «пересечение охранной зоны ЛЭП» are process text).
    ("street", re.compile(r"(?i:(?<![\w])перекр[её]ст(?:ок|ка|ке|ку|ком))\s+" + CROSS_NAMES +
                          r"(?:\s+и\s+" + CROSS_NAMES + r")?"
                          r"|(?<![\w])(?:углу|пересечени(?:е|я|и|ем))\s+(?:улиц\w*\s+|проспект\w*\s+)?" + PLACE_NAME +
                          r"(?:\s+и\s+" + PLACE_NAME + r")?")),
    ("house", re.compile(r"(?<![\w])(?:д\.|дом|корп\.|корпус|к\.|стр\.|строение|кв\.|квартира|оф\.|офис|"
                         r"пом\.|помещение|лит\.|литера|уч\.|участок)\s*№?\s*\d+[а-яА-Я]?(?:[/\-]\d+[а-яА-Я]?)?")),
]
ADDRESS_GAP = re.compile(r"^[\s,]*$")

# Prompt injection: instructions addressed to the AI instead of process text.
INJECTION = re.compile(
    r"(?i)(?:игнорируй|проигнорируй|забудь|отмени|не\s+учитывай|не\s+обращай\s+внимани\w*\s+на)\s+"
    r"(?:\w+\s+){0,3}(?:инструкц|указани|правил|промпт|prompt|ограничени|запрет)"
    r"|(?:инструкци\w*|указани\w*|примечани\w*|команд\w*|задани\w*|просьб\w*)\s+(?:для|к)\s+"
    r"(?:ии|ai|ассистент\w*|нейросет\w*|языков\w*\s+модел\w*|gpt|llm|chatgpt|claude|gemini)\b"
    r"|(?:^|[\s,.:;])(?:ты|вы)\s*[—–\-]?\s*(?:теперь\s+)?(?:ии|ассистент|языковая\s+модель|нейросеть|бот|chatgpt)\b"
    r"|(?:^|\n)\s*(?:system|assistant|developer|ассистент)\s*:"
    r"|<\|?(?:im_start|im_end|system|endoftext)\|?>|\[/?INST\]|<<SYS>>"
    r"|\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)?\s*"
    r"(?:instructions?|prompts?|rules?)\b|\byou\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|chatbot|gpt)\b"
    r"|\bsystem\s+prompt\b|\bjailbreak\b"
    r"|не\s+(?:показывай|сообщай|упоминай|отражай|говори|выводи)\w*[^.!?\n]{0,60}"
    r"(?:аналитик|пользовател|отч[её]т|человек|заказчик)"
    r"|(?:выведи|покажи|раскрой|напиши|повтори)\s+(?:(?:свой|свои|сво[её])\s+(?:системн\w*\s+)?|системн\w*\s+)"
    r"(?:промпт|prompt|инструкци)"
    # Orders to whoever builds the diagram: process text describes actions in the third person.
    r"|(?:добавь|включи|вставь|удали|убери|измени|замени|дорисуй|пропусти)\s+(?:\w+\s+){0,3}?"
    r"(?:шаг|этап|задач|действи|ветв)"
    r"|(?:добавь|включи|вставь|удали|убери|измени|замени|дорисуй|пропусти)(?:те)?\s+(?:[\w«»]+\s+){0,6}?"
    r"(?:в|на|из|со?)\s+(?:схем|план|диаграмм)"
    r"|(?:в|на)\s+(?:схем|план|диаграмм)\w*\s+(?:\w+\s+){0,2}?(?:добавь|включи|вставь|укажи|отрази)\b")
IMPERATIVE = re.compile(r"(?i)^\s*(?:добавь|удали|измени|вставь|сделай|напиши|выведи|включи|убери|замени|создай|"
                        r"поставь|укажи|отправь|переведи|ответь|покажи|скрой|раскрой|перечисли|верни|выполни|"
                        r"запусти|открой|назначь|пропусти|одобри|add|remove|insert|delete|output|print|reveal)\w*\b")
ABBREVIATIONS = set("руб коп тыс млн млрд г гг ул д кв стр корп пр просп пер обл им см рис тел т е п пп ст гл".split())


def _sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans that survive «quotes» and abbreviations such as «руб.» or «ул.»."""
    spans, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "«":
            depth += 1
        elif ch == "»":
            depth = max(0, depth - 1)
        boundary = ch == "\n"
        if ch in ".!?" and depth == 0 and (i + 1 == len(text) or text[i + 1].isspace()):
            word = re.search(r"([\w-]*)$", text[start:i]).group(1)
            boundary = not (ch == "." and (word.lower() in ABBREVIATIONS or len(word) == 1))
        if boundary:
            if text[start:i + 1].strip():
                spans.append((start, i + 1))
            start, depth = i + 1, 0 if ch == "\n" else depth
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _rule_findings(text: str) -> list[Finding]:
    out = []
    for kind, pattern, group, check in RULES:
        for m in pattern.finditer(text):
            if check is None or check(m, text):
                out.append(Finding(m.start(group), m.end(group), kind, m.group(group)))
    for pattern in PERSON_RULES:
        for m in pattern.finditer(text):
            value = m.group()
            first = value.split()[0]
            if first.lower() in ROLE_WORDS:          # «Заявитель Петров Иван Сергеевич»
                value = value[len(first):].lstrip()
            start = m.end() - len(value)
            if len(value.split()) >= 2 or "." in value:
                out.append(Finding(start, m.end(), "person", value))
    for m in PERSON_CONTEXT.finditer(text):
        out.append(Finding(m.start(1), m.end(1), "person", m.group(1)))
    out += _address_findings(text)
    out += _issuer_findings(text)
    return out


def _address_findings(text: str) -> list[Finding]:
    parts = sorted(((m.start(), m.end(), name) for name, p in ADDRESS_PARTS for m in p.finditer(text)),
                   key=lambda x: (x[0], -x[1]))
    chains, current = [], []
    for start, end, name in parts:
        if current and start < current[-1][1]:
            continue                                   # overlapping component
        if current and not ADDRESS_GAP.match(text[current[-1][1]:start]):
            chains.append(current)
            current = []
        current.append((start, end, name))
    if current:
        chains.append(current)
    return [Finding(c[0][0], c[-1][1], "address", text[c[0][0]:c[-1][1]]) for c in chains
            if any(name == "street" for _, _, name in c)
            or (len(c) >= 2 and any(name == "house" for _, _, name in c))]


# Passport issue details: who issued it and when. Taken only next to the word «паспорт» and only when the
# fragment names an issuing authority or starts with a date, so «паспорт трансформатора выдан после
# испытаний» stays process text.
ISSUED = re.compile(r"(?i)(?<![а-яё])выдан[аоы]?(?![а-яё])\s*:?\s*")
AUTHORITY = re.compile(r"УФМС|ФМС|ГУВМ|УВМ|МВД|ОВД|УВД|МФЦ|(?i:милици|полици|паспортн\w*\s+стол|внутренних\s+дел)")
DATE = r"\d{1,2}[./]\d{1,2}[./]\d{2,4}(?:\s*г\.)?"
ISSUE_END = re.compile(r"[);\n]|,\s*(?=(?i:код|дат|прожива|зарегистр|тел|e-?mail|адрес|снилс|инн)(?![а-яё]))")
# abbreviations followed by a name («гор. Москве», «им. Ленина»): their dot does not end the issuer;
# «по Тверской обл.» does
NAME_PREFIXES = {"г", "гор", "пос", "ул", "д", "им", "ст", "с", "дер", "респ", "мкр", "пр", "просп", "пер"}
MAX_ISSUER = 160


def _issuer_findings(text: str) -> list[Finding]:
    out = []
    for m in ISSUED.finditer(text):
        if not _near(text, m.start(), r"паспорт", 120):
            continue
        start = m.end()
        stop = ISSUE_END.search(text, start, start + MAX_ISSUER)
        end = stop.start() if stop else min(len(text), start + MAX_ISSUER)
        for dot in re.finditer(r"\.(?=\s+[А-ЯЁA-Z]|\s*$)", text[start:end]):   # sentence end, not «гор. Москве»
            word = re.search(r"(\w+)$", text[start:start + dot.start()])
            if not word or word.group(1).lower() not in NAME_PREFIXES:
                end = start + dot.start()
                break
        value = text[start:end].rstrip(" ,.")
        if value and (AUTHORITY.search(value) or re.match(DATE, value)):
            out.append(Finding(start, start + len(value), "passport", value))
    return out


def _injection_findings(text: str) -> list[Finding]:
    sentences = _sentences(text)
    flagged = set()
    for i, (start, end) in enumerate(sentences):
        if INJECTION.search(text[start:end]):
            flagged.add(i)
            j = i + 1                                  # imperatives right after the marker belong to it
            while j < len(sentences) and IMPERATIVE.match(text[sentences[j][0]:sentences[j][1]]):
                flagged.add(j)
                j += 1
    for a, b in zip(sorted(flagged), sorted(flagged)[1:]):
        if b - a == 2 and "\n" not in text[sentences[a][0]:sentences[b][1]]:
            flagged.add(a + 1)                     # one sentence wedged between two commands
    out = []
    for i in sorted(flagged):
        start, end = sentences[i]
        raw = text[start:end]
        lead = len(raw) - len(raw.lstrip())
        out.append(Finding(start + lead, end, "injection", raw.strip()))
    return out


# --------------------------------------------------------------------------- NER
class _Ner:
    """Natasha/Slovnet NER, loaded once; None when the package is not installed."""
    _lock = threading.Lock()
    _model = None
    _failed = False

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._model is None and not cls._failed:
                try:
                    from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter
                    cls._model = (Doc, Segmenter(), NewsNERTagger(NewsEmbedding()))
                except Exception as exc:  # noqa: BLE001 - optional dependency; rules still work
                    print(f"[bpmn-agent] NER недоступен, имена ищутся только по правилам: {exc}")
                    cls._failed = True
            return cls._model

    @classmethod
    def persons(cls, text: str) -> list[Finding]:
        model = cls.get()
        if model is None:
            return []
        Doc, segmenter, tagger = model
        with cls._lock:
            doc = Doc(text)
            doc.segment(segmenter)
            doc.tag_ner(tagger)
        out = []
        for span in doc.spans:
            value, start = span.text, span.start
            first = value.split()[0] if value.split() else ""
            if first.lower() in ROLE_WORDS and " " in value:
                value = value[len(first):].lstrip()
                start = span.stop - len(value)
            if span.type != "PER" or value.isupper() or value.lower() in ROLE_WORDS:
                continue
            if " " not in value and NOT_PERSON.search(value):
                continue                               # «Рязаньэнерго» is a company
            out.append(Finding(start, span.stop, "person", value, "ner"))
        return out


def ner_available() -> bool:
    return _Ner.get() is not None


# --------------------------------------------------------------------------- detection
def _split_unsafe(f: Finding) -> list[Finding]:
    """Cut a finding at quotes and line breaks so restored values never break code literals."""
    out, offset = [], 0
    for piece in UNSAFE.split(f.text):
        stripped = piece.strip(" ,;:")
        if len(stripped) >= 2:
            start = f.start + offset + piece.index(stripped)
            out.append(Finding(start, start + len(stripped), f.kind, stripped, f.source))
        offset += len(piece) + 1
    return out


def detect(text: str, hide=(), show=(), ner: bool = True, injections: bool = True, trusted=()) -> list[Finding]:
    """Non-overlapping findings in `text`; released fragments (`show`) are left out.

    `trusted` are the analyst's own instructions (typed in the edit box or as answers): they are meant to
    command, so they are never taken for prompt injection; personal data inside them is still hidden.
    """
    found = [Finding(m.start(), m.end(), "analyst", m.group(), "analyst")
             for fragment in hide if fragment.strip()
             for m in re.finditer(re.escape(fragment.strip()), text)]
    if injections:
        own = _spans_of(trusted, text)
        found += [f for f in _injection_findings(text) if not any(f.start < b and a < f.end for a, b in own)]
    found += _rule_findings(text)
    if ner:
        found += _Ner.persons(text)
    found = [piece for f in found for piece in _split_unsafe(f)]
    free = _spans_of(show, text)
    found = [f for f in found if not (KINDS[f.kind][2] and any(f.start < b and a < f.end for a, b in free))]
    chosen: list[Finding] = []
    for f in sorted(found, key=lambda f: (PRIORITY[f.kind], -(f.end - f.start), f.start)):
        if all(f.end <= c.start or f.start >= c.end for c in chosen):
            chosen.append(f)
    return sorted(chosen, key=lambda f: f.start)


def _spans_of(fragments, text: str) -> list[tuple[int, int]]:
    return [m.span() for fragment in fragments if fragment.strip()
            for m in re.finditer(_flexible(fragment.strip()), text)]


def released(text: str, show=()) -> list[Finding]:
    """Findings the analyst chose to send anyway (shown in the preview)."""
    free = _spans_of(show, text)
    if not free:
        return []
    return [f for f in detect(text) if KINDS[f.kind][2] and any(f.start < b and a < f.end for a, b in free)]


# --------------------------------------------------------------------------- guard
def _flexible(fragment: str) -> str:
    return r"\s+".join(map(re.escape, fragment.split()))


class PrivacyGuard:
    """Masks every message sent to a model and restores the labels in its answers."""

    def __init__(self, text: str = "", hide=(), show=(), trusted=()):
        self.hide = [h for h in (s.strip() for s in hide) if h]
        self.show = [s.strip() for s in show if s.strip()]
        self.trusted = [t.strip() for t in trusted if t.strip()]
        self.originals: dict[str, str] = {}      # placeholder -> fragment
        self.placeholders: dict[str, str] = {}   # fragment -> placeholder
        self.kinds: dict[str, str] = {}          # placeholder -> kind
        self.counters: Counter = Counter()
        self.requests = 0
        self.egress_caught = 0
        self._pattern: re.Pattern | None = None
        self.text = text          # the description; reported back masked, never sent as is
        self.injections: list[str] = []
        self.learn(text)

    # -- learning what to hide
    def learn(self, text: str, own: bool = False) -> list[Finding]:
        """Detect personal data in natural text written by a person; `own` marks the analyst's instruction."""
        if not text.strip():
            return []
        findings = detect(text, self.hide, self.show, injections=not own, trusted=self.trusted)
        for f in findings:
            self._remember(f.text, f.kind)
            if f.kind == "injection" and f.text not in self.injections:
                self.injections.append(f.text)
        return findings

    def _remember(self, fragment: str, kind: str) -> str:
        if fragment in self.placeholders:
            return self.placeholders[fragment]
        self.counters[kind] += 1
        placeholder = f"[{KINDS[kind][0]}_{self.counters[kind]}]"
        self.placeholders[fragment] = placeholder
        self.originals[placeholder] = fragment
        self.kinds[placeholder] = kind
        self._pattern = None
        return placeholder

    def _known(self) -> re.Pattern | None:
        if self._pattern is None and self.placeholders:
            alternatives = sorted(self.placeholders, key=len, reverse=True)
            self._pattern = re.compile(r"(?<![\w])(?:" + "|".join(map(_flexible, alternatives)) + r")(?![\w])")
        return self._pattern

    def _lookup(self, matched: str) -> str:
        if matched in self.placeholders:
            return self.placeholders[matched]
        squeezed = " ".join(matched.split())
        return next((p for f, p in self.placeholders.items() if " ".join(f.split()) == squeezed), "[СКРЫТО]")

    def _replace_known(self, content: str) -> str:
        pattern = self._known()
        return pattern.sub(lambda m: self._lookup(m.group()), content) if pattern else content

    # -- outgoing
    def mask(self, content: str) -> str:
        out = self._replace_known(content)
        # Egress check: whatever the rules still see right before sending is hidden too.
        fresh = detect(out, show=self.show, ner=False, injections=False)
        if fresh:
            for f in fresh:
                self._remember(f.text, f.kind)
            self.egress_caught += len(fresh)
            out = self._replace_known(out)
        return out

    def mask_messages(self, messages: list[dict]) -> list[dict]:
        """System messages are our own prompts and pass as is; everything else is masked."""
        self.requests += 1
        return [m if m.get("role") == "system" else {**m, "content": self.mask(m["content"])} for m in messages]

    # -- incoming
    def unmask(self, text: str) -> str:
        if not self.originals:
            return text
        labels = "|".join(sorted((re.escape(l) for l in LABEL_KIND), key=len, reverse=True))
        def restore(m):
            placeholder = f"[{m.group(1).upper().replace('Ё', 'Е')}_{m.group(2)}]"
            return self.originals.get(placeholder, m.group())
        text = re.sub(rf"\[\s*({labels})\s*[_ ]\s*(\d+)\s*\](?:[а-яё]{{1,3}}(?![а-яё]))?", restore, text, flags=re.I)
        return re.sub(rf"(?<![\w\[])({labels})_(\d+)(?![\w\]])", restore, text, flags=re.I)

    # -- reporting (no personal data inside)
    def masked_text(self) -> str:
        return self._replace_known(self.text)

    def report(self) -> dict:
        kinds = Counter(self.kinds.values())
        return {
            "requests": self.requests,
            "hidden": [{"kind": k, "title": KINDS[k][1], "count": kinds[k]} for k in KINDS if kinds[k]],
            "egress_caught": self.egress_caught,
            "injections": len(self.injections),
            "ner": ner_available(),
            "masked_text": self.masked_text(),
        }


def preview(text: str, hide=(), show=(), trusted=()) -> dict:
    """What the model will see: findings with positions, released fragments and the masked text."""
    guard = PrivacyGuard(text, hide, show, trusted)
    pattern = guard._known()
    spans = []
    if pattern:
        for m in pattern.finditer(text):
            placeholder = guard._lookup(m.group())
            kind = guard.kinds[placeholder]
            spans.append({"start": m.start(), "end": m.end(), "kind": kind, "title": KINDS[kind][1],
                          "placeholder": placeholder, "text": m.group(), "releasable": KINDS[kind][2]})
    free = [{"start": f.start, "end": f.end, "kind": f.kind, "title": KINDS[f.kind][1], "text": f.text}
            for f in released(text, show)]
    return {"findings": spans, "released": free, "masked_text": guard.masked_text(), "ner": ner_available()}
