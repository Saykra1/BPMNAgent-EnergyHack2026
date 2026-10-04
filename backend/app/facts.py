"""Facts on the diagram that the description does not contain.

A model knows industry regulations and tends to fill gaps from memory: a
deadline from a decree, a document from a typical procedure, a threshold in a
branch condition. Such facts look plausible, so they are checked
deterministically: every number (also written in words), date, reference to a
regulation and document name on the diagram must be found in the description.
"""
from __future__ import annotations

import re

TASK_KINDS = {"task", "userTask", "serviceTask", "scriptTask", "manualTask",
              "sendTask", "receiveTask", "businessRuleTask", "subProcess"}

NUMBER = re.compile(r"(?<![\w.,])\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?(?![\w])|\d+(?:[.,]\d+)?")
DATE_TIME = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}[./]\d{2,4}(?!\d)|(?<!\d)\d{1,2}:\d{2}(?!\d)")
NORM = re.compile(r"(?i)(?:\bПП\b|постановлени\w*|\bФЗ\b|федеральн\w*\s+закон\w*|приказ\w*|\bГОСТ\b|СНиП|"
                  r"\bСП\b|\bПУЭ\b|ПТЭЭП|распоряжени\w*)[^,;()]{0,30}?(?:№\s*)?\d[\d\-./]*"
                  r"|\b\d+-ФЗ\b|\bПП-\d+")
NUMBER_WORDS = [(re.compile(p), v) for p, v in [
    (r"одиннадцат\w*", 11), (r"двенадцат\w*", 12), (r"тринадцат\w*", 13), (r"четырнадцат\w*", 14),
    (r"пятнадцат\w*", 15), (r"шестнадцат\w*", 16), (r"семнадцат\w*", 17), (r"восемнадцат\w*", 18),
    (r"девятнадцат\w*", 19), (r"двадцат\w*", 20), (r"тридцат\w*", 30), (r"сорок\w*", 40),
    (r"пят\w*десят\w*", 50), (r"шест\w*десят\w*", 60), (r"сем\w*десят\w*", 70), (r"вос\w*десят\w*", 80),
    (r"девяност\w*", 90), (r"сто|ста", 100),
    (r"один|одного|одному|одним|одном|одна|одной|одну|одно", 1), (r"два|две|двух|двум|двумя", 2),
    (r"три|тр[её]х|тр[её]м|тремя", 3), (r"четыре|четыр[её]х|четыр[её]м|четырьмя", 4), (r"пят[ьи]|пятью", 5),
    (r"шест[ьи]|шестью", 6), (r"сем[ьи]|семью", 7), (r"восемь|восьми|восемью|восьмью", 8),
    (r"девят[ьи]|девятью", 9), (r"десят[ьи]|десятью", 10), (r"полтор\w*|полутор\w*", 1.5),
    # ordinals: «на первом этапе» supports «Этап 1»
    (r"перв\w*", 1), (r"втор\w*", 2), (r"трет\w*", 3), (r"четв[её]рт\w*", 4), (r"седьм\w*", 7),
    (r"(?:пят|шест|восьм|девят|десят)(?:ый|ой|ого|ому|ым|ом|ая|ую|ое|ые|ых)", None),
]]
ORDINAL_STEMS = {"пят": 5, "шест": 6, "восьм": 8, "девят": 9, "десят": 10}
MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
TEXT_DATE = re.compile(r"(?i)(?<!\d)(\d{1,2})\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]|июн\w*|июл\w*|"
                       r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\s+(\d{4})")
STOP = {"для", "при", "без", "или", "над", "под", "про", "как", "что", "это", "его", "все", "всех", "который"}
FIELD_TITLES = {"name": "Название", "deadline": "Срок", "document": "Документ", "condition": "Условие ветви"}


def _date_key(value: str) -> tuple | None:
    """(day, month, year) of «15.03.2025», «15/03/25» or «15 марта 2025»."""
    m = TEXT_DATE.fullmatch(value.strip())
    if m:
        month = next(i for i, stem in enumerate(MONTHS, 1) if m.group(2).lower().startswith(stem))
        day, year = int(m.group(1)), int(m.group(3))
    else:
        parts = re.split(r"[./]", value)
        if len(parts) != 3:
            return None
        day, month, year = map(int, parts)
    return day, month, year + 2000 if year < 100 else year


def _number(value: str) -> str:
    v = float(re.sub(r"[  ]", "", value).replace(",", "."))
    return str(int(v)) if v.is_integer() else f"{v:g}"


def _word_number(word: str) -> float | None:
    for pattern, value in NUMBER_WORDS:
        if pattern.fullmatch(word):
            return value if value is not None else next(v for stem, v in ORDINAL_STEMS.items() if word.startswith(stem))
    return None


def text_numbers(text: str) -> set[str]:
    """Numbers of the description, including those written in words («десяти», «двадцати пяти»)."""
    found = {_number(m.group()) for m in NUMBER.finditer(DATE_TIME.sub(" ", text))}
    words = re.findall(r"[а-яё]+", text.lower())
    for i, word in enumerate(words):
        value = _word_number(word)
        if value is None:
            continue
        found.add(_number(str(value)))
        following = _word_number(words[i + 1]) if i + 1 < len(words) else None
        if value % 10 == 0 and 20 <= value <= 90 and following and following < 10 and following == int(following):
            found.add(str(int(value + following)))
    return found


def _stems(text: str) -> set[str]:
    words = re.findall(r"[а-яa-z]{3,}", text.lower().replace("ё", "е"))
    return {w[:5] for w in words if w not in STOP}


def _squeeze(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


class FactChecker:
    def __init__(self, text: str):
        self.text = text
        self.numbers = text_numbers(text)
        self.stems = _stems(text)
        found = [m.group() for m in DATE_TIME.finditer(text)] + [m.group() for m in TEXT_DATE.finditer(text)]
        self.times = {v for v in found if ":" in v}
        self.dates = {_date_key(v) for v in found if ":" not in v} - {None}

    def numbers_problems(self, value: str) -> list[tuple[str, str]]:
        """(fragment, kind) for dates, numbers and norms of `value` that the description lacks."""
        problems = [(m.group(), "date") for m in DATE_TIME.finditer(value)
                    if (m.group() not in self.times if ":" in m.group() else _date_key(m.group()) not in self.dates)]
        rest = DATE_TIME.sub(" ", value)
        norms = [(m.start(), m.end(), m.group()) for m in NORM.finditer(rest)]
        for m in NUMBER.finditer(rest):
            if _number(m.group()) in self.numbers:
                continue
            norm = next((n for a, b, n in norms if a <= m.start() < b), None)
            item = (norm.strip(), "norm") if norm else (m.group(), "number")
            if item not in problems:
                problems.append(item)
        return problems

    def document_supported(self, document: str) -> bool:
        words = _stems(document)
        return not words or len(words & self.stems) / len(words) >= 0.6

    def check(self, field: str, value: str) -> list[dict]:
        """At most one fact per value; its reason names every missing piece."""
        problems = self.numbers_problems(value)
        if field == "document" and not problems and not self.document_supported(value):
            problems = [(value, "document")]
        if not problems:
            return []
        reasons = {
            "norm": "Ссылки на норматив «{}» нет в описании: модель могла взять её из своих знаний.",
            "date": "Даты «{}» нет в описании.",
            "number": "Числа {} нет в описании.",
            "document": "Документ «{}» в описании не упоминается.",
        }
        kinds = [kind for _, kind in problems]
        kind = next((k for k in ("norm", "document", "date") if k in kinds), "number")
        return [{"field": field, "value": value, "details": [fragment for fragment, _ in problems], "kind": kind,
                 "reason": " ".join(reasons[k].format(fragment) for fragment, k in problems)}]


def unsupported_facts(d, text: str, reverse: dict) -> list[dict]:
    """Facts of nodes and branch conditions absent from `text`; ids are the original BPMN ids."""
    if not text.strip():
        return []
    checker = FactChecker(text)
    facts = []
    def add(found, element_id, owner_id, name, assumption):
        for f in found:
            note = _squeeze(assumption)
            acknowledged = bool(note) and (_squeeze(f["value"]) in note
                                           or all(_squeeze(d) in note for d in f["details"]))
            facts.append({**f, "id": element_id, "owner": owner_id, "name": name,
                          "field_title": FIELD_TITLES[f["field"]], "acknowledged": acknowledged,
                          "editable": f["field"] in ("deadline", "document")})
    for node in d.nodes.values():
        if node.id not in reverse:
            continue
        details = node.details or {}
        assumption = details.get("assumption", "")
        found = checker.check("name", node.name or "") + checker.check("deadline", details.get("deadline", ""))
        for document in details.get("documents", []):
            found += checker.check("document", document)
        add(found, reverse[node.id], reverse[node.id], node.name, assumption)
    for flow in d.sequence_flows():
        if flow.name and flow.id in reverse and flow.source in reverse:
            add(checker.check("condition", flow.name), reverse[flow.id], reverse[flow.source],
                d.nodes[flow.source].name or "Развилка", "")
    return facts


def trust(card: dict, facts: list[dict]) -> str:
    """grounded | assumption | unbased | neutral — what the «Проверка фактов» mode paints."""
    if any(not f["acknowledged"] for f in facts):
        return "unbased"
    if card["kind"] in TASK_KINDS:
        if facts or card.get("assumption"):
            return "assumption"
        return "grounded" if card.get("source_found") else "unbased"
    return "grounded" if card.get("source_found") else "neutral"
