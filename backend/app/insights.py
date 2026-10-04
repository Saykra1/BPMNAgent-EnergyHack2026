"""Read-only inspection of the current XML; no normalization or silent repairs."""
from __future__ import annotations

import re
from lxml import etree

from .bpmn.importer import BPMN, NS, bpmn_to_code
from .bpmn.validator import validate
from .bpmn.xsd import validate_xsd
from .sandbox import run_code
from .facts import unsupported_facts

GUIDANCE = {
    "E_DEADLOCK": ("Процесс может зависнуть", "Слияние ждёт все ветви, хотя была выбрана только одна. Проверьте тип шлюза слияния."),
    "W_MULTI_MERGE": ("Следующий шаг может повториться", "Параллельные ветви нужно синхронизировать, если следующий шаг должен выполняться один раз."),
    "E_DANGLING_IN": ("До шага нельзя дойти", "Добавьте входящий переход из нужного шага или удалите лишний элемент."),
    "E_DANGLING_OUT": ("Процесс обрывается", "Укажите следующий шаг или завершите этот исход конечным событием."),
    "E_UNREACHABLE": ("Шаг недостижим", "Проверьте маршрут от начала процесса до этого шага."),
    "W_UNLABELED_BRANCH": ("Непонятно, какую ветвь выбрать", "Подпишите условия всех альтернатив, например «Документы полные» и «Нужна доработка»."),
    "E_NO_START": ("Не задано начало", "Добавьте стартовое событие в этот процесс или подпроцесс."),
    "E_NO_END": ("Не задан результат", "Добавьте конечное событие для каждого допустимого исхода."),
    "W_MIXED_GATEWAY": ("Сложно понять шлюз", "Разделите слияние и разветвление на два шлюза."),
    "E_EVENT_GATEWAY_TARGET": ("Неверное ожидание события", "После событийного шлюза должны стоять ожидаемые события или задачи получения сообщения."),
}

ENERGY_CHECKS = [
    ("documents", "Неполный комплект документов", r"документ|заявк", r"неполн|не хватает|дополн|комплект|доработ",
     "Что происходит при неполном комплекте документов? Кто уведомляет заявителя и куда возвращается процесс после дополнения?"),
    ("deadline", "Истечение срока", r"срок|\d+\s*(?:рабоч|календар|дн|час)|ожидан", r"просроч|истеч|не предостав|не поступ|аннулир",
     "От какого события считается срок? Что происходит, если ответ или документы не поступили вовремя, и кто уведомляет заявителя?"),
    ("capacity", "Недостаток мощности / отказ", r"мощност|присоедин|техническ.{0,15}услов", r"недостат|отказ|увелич.{0,15}мощност|нет мощност",
     "Что делаем при недостатке мощности или невозможности присоединения? Кто принимает решение и уведомляет заявителя?"),
    ("inspection", "Замечания и повторный осмотр", r"осмотр|инспектор|замечан|приёмк", r"устран|повтор|доработ",
     "Если при осмотре найдены замечания, кто их устраняет и к какому шагу возвращается процесс для повторной проверки?"),
    ("outage", "Отключение и восстановление питания", r"авари|отключ|обесточ|резерв.{0,12}питан", r"восстанов|резерв|уведом",
     "Как уведомляют потребителей, что делают при невозможности быстро восстановить питание и кто подтверждает восстановление?"),
]


def inspect_xml(xml: str, text: str = "") -> dict:
    root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
    variables = {}
    result = run_code(bpmn_to_code(xml, variables))
    d = result.diagram
    reverse = {result.variables[v]: original for original, v in variables.items()
               if v in result.variables and isinstance(result.variables[v], str)}
    # Importing creates two default events. Ignore only those absent from the original XML.
    for node_id in (d.root_start, d.root_end):
        if node_id not in reverse and not d.incoming(node_id) and not d.outgoing(node_id):
            d.nodes.pop(node_id, None)
    for el in root.iter(f"{{{BPMN}}}sequenceFlow"):
        for flow in d.sequence_flows():
            if (reverse.get(flow.source) == el.get("sourceRef") and
                    reverse.get(flow.target) == el.get("targetRef") and flow.name == (el.get("name") or "")):
                reverse[flow.id] = el.get("id")
    issues = []
    for issue in validate(d):
        item = issue.to_dict()
        item["elements"] = [reverse[e] for e in issue.elements if e in reverse]
        item["title"], item["advice"] = GUIDANCE.get(issue.code, ("Проверьте схему", issue.message))
        issues.append(item)
    cards = []
    for node in d.nodes.values():
        if node.id not in reverse:
            continue
        details = node.details
        quote = details.get("source_quote", "")
        start = text.find(quote) if quote else -1
        cards.append({"id": reverse[node.id], "name": node.name, "kind": node.kind,
                      "role": d.lanes[node.lane].name if node.lane in d.lanes else "",
                      **details, "source_found": start >= 0,
                      "source_start": start, "source_end": start + len(quote) if start >= 0 else -1})
    labels = [{"id": el.get("id"), "text": el.get("name") or ""} for el in root.iter()
              if isinstance(el.tag, str) and el.tag.startswith("{" + BPMN + "}") and el.get("id")]
    context = text + " " + " ".join(v["text"] for v in labels)
    checks = []
    for key, title, trigger, evidence, question in ENERGY_CHECKS:
        applicable = bool(re.search(trigger, context, re.I))
        matches = [el["id"] for el in labels if re.search(evidence, el["text"], re.I)]
        checks.append({"id": key, "title": title, "question": question, "elements": matches,
                       "status": "not_applicable" if not applicable else "mentioned" if matches else "review"})
    unsupported = sorted({etree.QName(el).localname for el in root.iter()
                          if isinstance(el.tag, str) and etree.QName(el).localname in
                          {"boundaryEvent", "complexGateway", "callActivity", "transaction", "adHocSubProcess",
                           "dataObjectReference", "dataStoreReference"}})
    return {"issues": issues, "cards": cards, "unsupported_facts": unsupported_facts(d, text, reverse),
            "energy_checks": checks,
            "xsd_errors": validate_xsd(xml), "unsupported": unsupported,
            "note": "Проверка исключений ищет упоминания, а не доказывает полноту маршрутов или соответствие нормативам."}
