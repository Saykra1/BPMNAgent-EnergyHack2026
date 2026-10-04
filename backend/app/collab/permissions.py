"""Team permissions adapted to the existing BPMN XML representation."""
from __future__ import annotations

from lxml import etree

PERMISSIONS = [
    {"id": "view", "name": "Просмотр проектов", "group": "project", "hint": "Открывать схемы команды"},
    {"id": "edit_content", "name": "Текст и названия", "group": "project", "hint": "Править описание и подписи"},
    {"id": "edit_bpmn", "name": "Редактирование схемы", "group": "project", "hint": "Добавлять и менять элементы BPMN"},
    {"id": "generate", "name": "Генерация ИИ", "group": "project", "hint": "Создавать и уточнять схему через модель"},
    {"id": "edit_code", "name": "Код построения", "group": "project", "hint": "Менять Python-код схемы"},
    {"id": "create_projects", "name": "Создание проектов", "group": "team", "hint": "Создавать проекты команды"},
    {"id": "delete_projects", "name": "Удаление проектов", "group": "project", "hint": "Удалять проекты"},
    {"id": "manage_members", "name": "Участники", "group": "team", "hint": "Приглашать и исключать людей"},
    {"id": "manage_roles", "name": "Роли", "group": "team", "hint": "Настраивать права участников"},
]
ALL = {p["id"] for p in PERMISSIONS}
PROJECT_PERMS = {p["id"] for p in PERMISSIONS if p["group"] == "project"}
TEAM_PERMS = ALL - PROJECT_PERMS
MANAGING = {"manage_members", "manage_roles"}
DEFAULT_ROLES = [
    {"name": "Редактор", "color": "#176d56", "permissions": ["view", "edit_content", "edit_bpmn", "generate", "edit_code", "create_projects"]},
    {"name": "Аналитик", "color": "#9b6b27", "permissions": ["view", "edit_content", "generate"]},
    {"name": "Наблюдатель", "color": "#66746f", "permissions": ["view"]},
]


def normalize(perms) -> list[str]:
    out = {p for p in (perms or []) if p in ALL}
    if out & (PROJECT_PERMS - {"view"}):
        out.add("view")
    return [p["id"] for p in PERMISSIONS if p["id"] in out]


def _semantic_xml(xml: str) -> tuple[bytes, bytes]:
    """Return structure and content signatures; omit BPMN DI coordinates from both."""
    root = etree.fromstring(xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    if etree.QName(root).localname != "definitions":
        raise ValueError("Нужен BPMN 2.0 XML")
    structure = []
    content = []
    for element in root.iter():
        qname = etree.QName(element)
        if qname.namespace and any(x in qname.namespace for x in ("BPMN/20100524/DI", "OMG/20100524/DI", "OMG/20100524/DC")):
            continue
        tag = qname.localname
        attrs = {etree.QName(k).localname: v for k, v in element.attrib.items()}
        structure.append((tag, attrs.get("id", ""), attrs.get("sourceRef", ""), attrs.get("targetRef", ""),
                          attrs.get("processRef", "")))
        content.append((tag, tuple(sorted(attrs.items())), (element.text or "").strip()))
    return repr(structure).encode(), repr(content).encode()


def changed_parts(old_xml: str, new_xml: str, old_text: str, new_text: str) -> set[str]:
    parts = set()
    if old_text != new_text:
        parts.add("text")
    if old_xml == new_xml:
        return parts
    if not old_xml or not new_xml:
        return parts | {"structure", "content"}
    try:
        old_s, old_c = _semantic_xml(old_xml)
        new_s, new_c = _semantic_xml(new_xml)
    except (ValueError, etree.XMLSyntaxError):
        return parts | {"structure", "content"}
    if old_s != new_s:
        parts.add("structure")
    if old_c != new_c:
        parts.add("content")
    if not parts:
        parts.add("layout")
    return parts


NEEDS = {
    "structure": ({"edit_bpmn", "generate"}, "менять структуру схемы"),
    "content": ({"edit_content", "edit_bpmn", "generate"}, "менять подписи и данные BPMN"),
    "text": ({"edit_content", "generate"}, "менять описание процесса"),
    "layout": ({"edit_bpmn"}, "двигать элементы схемы"),
    "code": ({"edit_code"}, "менять код построения"),
}


def forbidden_changes(parts: set[str], perms: set[str]) -> list[str]:
    return [why for part in sorted(parts) for need, why in [NEEDS[part]] if not need & perms]
