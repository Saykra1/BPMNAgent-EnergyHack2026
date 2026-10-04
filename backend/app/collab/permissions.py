"""What a team role can allow, and server-side check of what a project save changes.

The team head (owner) can do everything. Everyone else gets the union of the permissions of their
roles; project permissions only count from roles whose scope covers that project. A member without
roles sees nothing in the team.
"""
from __future__ import annotations

PERMISSIONS: list[dict] = [
    {"id": "view", "name": "Видеть проекты", "group": "project",
     "hint": "Открывать схемы, регламенты и документы проектов из области видимости роли"},
    {"id": "edit_content", "name": "Названия, описания, документы", "group": "project",
     "hint": "Менять названия, описания, сроки, документы, исполнителей, подписи веток, текст процесса"},
    {"id": "edit_bpmn", "name": "Редактировать схему", "group": "project",
     "hint": "Добавлять, удалять и переносить блоки, связи, дорожки; менять тип блока"},
    {"id": "generate", "name": "Генерация с помощью ИИ", "group": "project",
     "hint": "Строить схему из текста и править её словами (LLM)"},
    {"id": "edit_code", "name": "Код блоков и проверки", "group": "project",
     "hint": "Код автоматических шагов и проверки веток развилок"},
    {"id": "run", "name": "Запуск процесса", "group": "project",
     "hint": "Запускать процесс, выполнять шаги и формировать документы о работе"},
    {"id": "create_projects", "name": "Создавать проекты", "group": "team",
     "hint": "Новый проект попадает в область видимости роли"},
    {"id": "delete_projects", "name": "Удалять проекты", "group": "project",
     "hint": "Удалять проекты из области видимости роли"},
    {"id": "manage_members", "name": "Участники и приглашения", "group": "team",
     "hint": "Приглашать и исключать участников, выдавать им роли (кроме управляющих)"},
    {"id": "manage_roles", "name": "Настройка ролей", "group": "team",
     "hint": "Создавать, менять и удалять роли (кроме управляющих прав)"},
]
ALL = {p["id"] for p in PERMISSIONS}
PROJECT_PERMS = {p["id"] for p in PERMISSIONS if p["group"] == "project"}
TEAM_PERMS = ALL - PROJECT_PERMS
MANAGING = {"manage_members", "manage_roles"}         # only the head may hand these out

DEFAULT_ROLES = [
    {"name": "Разработчик", "color": "#176d56",
     "permissions": ["view", "edit_content", "edit_bpmn", "generate", "edit_code", "run", "create_projects"]},
    {"name": "Администратор", "color": "#9b6b27", "permissions": ["view", "edit_content"]},
    {"name": "Наблюдатель", "color": "#66746f", "permissions": ["view"]},
]


def normalize(perms) -> list[str]:
    """Known permissions only; any project permission implies seeing the project."""
    out = {p for p in (perms or []) if p in ALL}
    if out & (PROJECT_PERMS - {"view"}):
        out.add("view")
    return [p["id"] for p in PERMISSIONS if p["id"] in out]


# ----------------------------------------------------------------------------- save checks
def _signatures(xml: str):
    from ..ir.from_diagram import xml_to_plan
    plan = xml_to_plan(xml)
    structure = (
        tuple(sorted((p.id, p.external) for p in plan.participants)),
        tuple(sorted((e.id, e.type, e.parent or "", e.participant or "", e.attached_to or "", e.event or "",
                      e.group or "") for e in plan.elements)),
        tuple(sorted((f.source, f.target, f.default) for f in plan.flows)),
        tuple(sorted((f.source, f.target) for f in plan.message_flows)),
    )
    content = (
        plan.title, plan.organization,
        tuple(sorted((p.id, p.name) for p in plan.participants)),
        tuple(sorted((e.id, e.name, e.description, e.deadline, tuple(e.documents), e.source_quote, e.assumption,
                      e.duration_min, e.wait_min, e.sla_hours, e.estimate, e.accountable, tuple(e.consulted),
                      tuple(e.informed), e.performer or "", e.report, tuple(e.fields)) for e in plan.elements)),
        tuple(sorted((f.source, f.target, f.label or "", f.probability) for f in plan.flows)),
        tuple(sorted((f.source, f.target, f.label or "") for f in plan.message_flows)),
        tuple((p.id, p.name, p.role or "", p.position, p.contacts) for p in plan.performers),
        tuple(sorted((g.id, g.name) for g in plan.groups)),
    )
    code = (tuple(sorted((e.id, e.code) for e in plan.elements if e.code)),
            tuple(sorted((f.source, f.target, f.check) for f in plan.flows if f.check)))
    return structure, content, code


def changed_parts(old_xml: str, new_xml: str, old_text: str, new_text: str) -> set[str]:
    """Which kinds of data a save changes: structure, content, code, text, layout."""
    parts: set[str] = set()
    if (old_text or "") != (new_text or ""):
        parts.add("text")
    if (old_xml or "") == (new_xml or ""):
        return parts
    if not (old_xml or "").strip() or not (new_xml or "").strip():
        return parts | {"structure", "content", "code"}
    try:
        old, new = _signatures(old_xml), _signatures(new_xml)
    except Exception:  # noqa: BLE001 — unreadable diagram: treat as changing everything
        return parts | {"structure", "content", "code"}
    for name, a, b in zip(("structure", "content", "code"), old, new):
        if a != b:
            parts.add(name)
    if not parts & {"structure", "content", "code"}:
        parts.add("layout")
    return parts


NEEDS = {
    "structure": ({"edit_bpmn", "generate"}, "менять структуру схемы (блоки, связи, дорожки)"),
    "content": ({"edit_content", "generate"}, "менять названия, описания и документы"),
    "code": ({"edit_code"}, "менять код блоков и проверки развилок"),
    "text": ({"edit_content", "generate"}, "менять текстовое описание процесса"),
    "layout": ({"edit_bpmn"}, "передвигать элементы схемы"),
}


def forbidden_changes(parts: set[str], perms: set[str]) -> list[str]:
    return [why for part in sorted(parts) for need, why in [NEEDS[part]] if not need & perms]
