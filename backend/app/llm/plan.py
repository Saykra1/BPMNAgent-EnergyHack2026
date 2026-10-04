"""JSON process plan: the structured intermediate representation produced by the planner.

The plan is validated here (schema + cross references). It is also compiled
deterministically into DIAGRAM API code (`compile_plan`) which serves as a
reference / fallback for the LLM code generator.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

ElementType = Literal[
    "task", "user_task", "service_task", "script_task", "manual_task", "send_task", "receive_task",
    "business_rule_task", "subprocess",
    "exclusive_gateway", "parallel_gateway", "inclusive_gateway", "event_based_gateway",
    "start_event", "end_event", "timer_event", "message_event", "message_throw_event",
]

RESERVED = {"start", "end"}


class Participant(BaseModel):
    id: str
    name: str
    external: bool = False   # separate organisation -> separate pool


class Element(BaseModel):
    id: str
    type: ElementType
    name: str = ""
    participant: str | None = None
    parent: str | None = None      # id of a subprocess element
    group: str | None = None
    event: str | None = None       # for start/end events: message | timer | error | terminate | signal
    source_quote: str = ""
    assumption: str = ""
    deadline: str = ""
    documents: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _none_name(cls, v):
        return v or ""


class FlowSpec(BaseModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    label: str | None = None

    model_config = {"populate_by_name": True}


class GroupSpec(BaseModel):
    id: str
    name: str


class ConflictSpec(BaseModel):
    """Two rules of the description that cannot both hold; quotes are checked against the text later."""
    topic: str = ""
    rule_a: str = ""
    rule_b: str = ""


class Plan(BaseModel):
    title: str = "Процесс"
    organization: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    elements: list[Element]
    flows: list[FlowSpec]
    message_flows: list[FlowSpec] = Field(default_factory=list)
    groups: list[GroupSpec] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    conflicts: list[ConflictSpec] = Field(default_factory=list)

    @field_validator("conflicts", mode="before")
    @classmethod
    def _none_conflicts(cls, v):
        return v or []


class PlanError(ValueError):
    pass


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM answer (tolerates ``` fences and prose)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise PlanError("В ответе нет JSON-объекта")
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise PlanError(f"Некорректный JSON: {e}") from e


def parse_plan(text_or_dict) -> Plan:
    data = extract_json(text_or_dict) if isinstance(text_or_dict, str) else text_or_dict
    try:
        plan = Plan.model_validate(data)
    except ValidationError as e:
        msgs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:15]]
        raise PlanError("План не соответствует схеме:\n" + "\n".join(msgs)) from e
    check_plan(plan)
    plan.assumptions += normalize_plan(plan)
    return plan


def normalize_plan(plan: Plan) -> list[str]:
    """Safe fixes of the plan; returns notes for the analyst.

    A participant marked external but joined to the process by control flows is a lane, not a
    separate pool: between pools BPMN allows only messages, so such flows would break the route.
    """
    owner = {e.id: e.participant for e in plan.elements}
    external = {p.id: p for p in plan.participants if p.external}
    notes = []
    for f in plan.flows:
        a, b = owner.get(f.source), owner.get(f.target)
        for side, other in ((a, b), (b, a)):
            if side in external and other != side:
                participant = external.pop(side)
                participant.external = False
                notes.append(f"Участник «{participant.name}» показан дорожкой основного пула: его шаги связаны "
                             "с процессом последовательно, а не сообщениями.")
    return notes


def check_plan(plan: Plan) -> None:
    errors: list[str] = []
    pids = [p.id for p in plan.participants]
    if len(set(pids)) != len(pids):
        errors.append("id участников повторяются")
    ids = [e.id for e in plan.elements]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errors.append(f"id элементов повторяются: {sorted(dup)}")
    bad_reserved = RESERVED & set(ids)
    if bad_reserved:
        errors.append(f"id {sorted(bad_reserved)} зарезервированы для общего начала/конца — не объявляйте их в elements")
    known = set(ids) | RESERVED
    subprocesses = {e.id for e in plan.elements if e.type == "subprocess"}
    parents = {e.id: e.parent for e in plan.elements}
    for e in plan.elements:
        seen = {e.id}
        parent = e.parent
        while parent in parents:
            if parent in seen:
                errors.append(f"элемент {e.id}: циклическая вложенность подпроцессов")
                break
            seen.add(parent)
            parent = parents[parent]
    groups = {g.id for g in plan.groups}
    if len(groups) != len(plan.groups):
        errors.append("id групп повторяются")
    for e in plan.elements:
        if e.participant and e.participant not in pids:
            errors.append(f"элемент {e.id}: неизвестный участник {e.participant!r}")
        if e.parent and e.parent not in subprocesses:
            errors.append(f"элемент {e.id}: parent {e.parent!r} не является подпроцессом")
        if e.group and e.group not in groups:
            errors.append(f"элемент {e.id}: неизвестная группа {e.group!r}")
        if not e.name and e.type not in ("parallel_gateway", "start_event", "end_event", "exclusive_gateway",
                                         "inclusive_gateway", "event_based_gateway"):
            errors.append(f"элемент {e.id}: пустое название")
    for f in plan.flows:
        for ref in (f.source, f.target):
            if ref not in known:
                errors.append(f"связь {f.source}->{f.target}: неизвестный элемент {ref!r}")
    for f in plan.message_flows:
        for ref in (f.source, f.target):
            if ref not in known and ref not in pids:
                errors.append(f"сообщение {f.source}->{f.target}: неизвестный элемент/участник {ref!r}")
    if not plan.elements:
        errors.append("в плане нет элементов")
    if errors:
        raise PlanError("Ошибки в плане:\n" + "\n".join(f"- {x}" for x in errors))


# ----------------------------------------------------------------------------- compiler
_METHOD = {
    "task": "add_task", "user_task": "add_user_task", "service_task": "add_service_task",
    "script_task": "add_script_task", "manual_task": "add_manual_task", "send_task": "add_send_task",
    "receive_task": "add_receive_task", "business_rule_task": "add_business_rule_task",
    "subprocess": "create_subprocess",
    "exclusive_gateway": "add_exclusive_gateway", "parallel_gateway": "add_parallel_gateway",
    "inclusive_gateway": "add_inclusive_gateway", "event_based_gateway": "add_event_based_gateway",
}


def _var(s: str) -> str:
    v = re.sub(r"\W", "_", s, flags=re.ASCII)
    if not v or v[0].isdigit():
        v = "n_" + v
    return v


def compile_plan(plan: Plan) -> str:
    """Deterministic plan -> DIAGRAM code. Mirrors what the LLM code generator is asked to do."""
    q = repr
    lines = [f"# {plan.title}"]
    internal = [p for p in plan.participants if not p.external]
    external = [p for p in plan.participants if p.external]
    used_ext = {e.participant for e in plan.elements if e.participant}
    container: dict[str, str] = {}
    if internal or external:
        org = plan.organization or plan.title
        if len(internal) >= 1:
            lines.append(f"pool_main, lanes_main = DIAGRAM.add_pool(ROOT_PROCESS_ID, "
                         f"[{', '.join(q(p.name) for p in internal)}], {q(org)})")
            for i, p in enumerate(internal):
                container[p.id] = f"lanes_main[{i}]"
        for index, p in enumerate(external):
            pool_var = f"pool_external_{index}"
            if p.id in used_ext:
                lines.append(f"{pool_var}, lanes_external_{index} = DIAGRAM.add_pool(ROOT_PROCESS_ID, [], "
                             f"{q(p.name)})")
                container[p.id] = pool_var
            else:
                lines.append(f"{pool_var} = DIAGRAM.add_black_box_pool({q(p.name)})")
                container[p.id] = pool_var
    default_parent = container[internal[0].id] if internal else "ROOT_PROCESS_ID"
    group_vars = {g.id: f"group_{i}" for i, g in enumerate(plan.groups)}
    for g in plan.groups:
        lines.append(f"{group_vars[g.id]} = DIAGRAM.add_group({q(g.name)}, {default_parent})")

    var = {"start": "ROOT_START_TASK_ID", "end": "ROOT_END_TASK_ID"}
    # IDs are data, not Python names: punctuation, keywords and collisions are safe.
    var.update({e.id: f"node_{i}" for i, e in enumerate(plan.elements)})
    parents = {e.id: e.parent for e in plan.elements}
    def depth(element):
        parent, count = element.parent, 0
        while parent:
            count += 1
            parent = parents[parent]
        return count
    order = sorted(plan.elements, key=lambda e: (depth(e), e.type != "subprocess"))
    for e in order:
        v = var[e.id]
        if e.parent:
            parent = var[e.parent]
        elif e.group:
            parent = group_vars[e.group]
        elif e.participant and e.participant in container:
            parent = container[e.participant]
        else:
            parent = default_parent
        if e.type in _METHOD:
            lines.append(f"{v} = DIAGRAM.{_METHOD[e.type]}({q(e.name)}, {parent})")
        elif e.type == "start_event":
            lines.append(f"{v} = DIAGRAM.add_start_event({q(e.name)}, {parent}, {q(e.event)})")
        elif e.type == "end_event":
            lines.append(f"{v} = DIAGRAM.add_end_event({q(e.name)}, {parent}, {q(e.event)})")
        elif e.type == "timer_event":
            lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(e.name)}, {parent}, 'timer')")
        elif e.type == "message_event":
            lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(e.name)}, {parent}, 'message')")
        elif e.type == "message_throw_event":
            lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(e.name)}, {parent}, 'message', True)")
        if e.source_quote or e.assumption or e.deadline or e.documents:
            lines.append(f"DIAGRAM.set_details({v}, {q(e.source_quote)}, {q(e.assumption)}, "
                         f"{q(e.deadline)}, {e.documents!r})")
    for f in plan.flows:
        label = f", {q(f.label)}" if f.label else ""
        lines.append(f"DIAGRAM.add_link({var[f.source]}, {var[f.target]}{label})")
    for f in plan.message_flows:
        src = var.get(f.source) or container.get(f.source)
        dst = var.get(f.target) or container.get(f.target)
        label = f", {q(f.label)}" if f.label else ""
        lines.append(f"DIAGRAM.add_message_link({src}, {dst}{label})")
    return "\n".join(lines) + "\n"
