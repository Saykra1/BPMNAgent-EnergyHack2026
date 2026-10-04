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
    "start_event", "end_event", "timer_event", "message_event", "message_throw_event", "boundary_timer",
]

RESERVED = {"start", "end"}


class Participant(BaseModel):
    id: str = Field(description="Короткий латинский id, например p_client")
    name: str = Field(description="Роль, подразделение, система или внешняя организация")
    external: bool = Field(False, description="true — отдельная внешняя организация (отдельный пул)")


class Element(BaseModel):
    id: str = Field(description="Уникальный латинский id: t1, gw_docs, end_reject")
    type: ElementType
    name: str = Field("", description="Глагол в инфинитиве + объект, 2–6 слов; у шлюза — вопрос")
    participant: str | None = Field(None, description="id участника-исполнителя (дорожки)")
    parent: str | None = Field(None, description="id подпроцесса, если шаг внутри него")
    group: str | None = None
    event: str | None = Field(None, description="для start/end: message | timer | error | terminate | signal")
    source_quote: str = Field("", description="Дословный фрагмент описания, обосновывающий шаг")
    assumption: str = Field("", description="Что домыслено (если шага нет в тексте явно)")
    deadline: str = Field("", description="Срок из текста с точкой отсчёта, например «20 рабочих дней с уведомления»")
    documents: list[str] = Field(default_factory=list)
    # --- analytics (filled by the analyst or estimated by the LLM with estimate=true) ---
    duration_min: float | None = Field(None, description="Длительность работы, минут")
    wait_min: float | None = Field(None, description="Ожидание перед шагом, минут")
    sla_hours: float | None = Field(None, description="Нормативный срок шага, часов")
    estimate: bool = Field(False, description="true — длительности оценены моделью, а не взяты из текста")
    # --- boundary timer (SLA / escalation) ---
    attached_to: str | None = Field(None, description="Для boundary_timer: id задачи, к которой прикреплён таймер")
    interrupting: bool = Field(False, description="Для boundary_timer: прерывает ли задачу (обычно false)")
    # --- RACI (participant ids); responsible = participant ---
    accountable: str | None = None
    consulted: list[str] = Field(default_factory=list)
    informed: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _none_name(cls, v):
        return v or ""


class FlowSpec(BaseModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    label: str | None = Field(None, description="Условие ветви шлюза («Да», «Документы неполные»)")
    default: bool = Field(False, description="Ветка «иначе» исключающего/инклюзивного шлюза")
    probability: float | None = Field(None, ge=0, le=1, description="Вероятность ветви (для аналитики)")

    model_config = {"populate_by_name": True}


class GroupSpec(BaseModel):
    id: str
    name: str


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


class PlanError(ValueError):
    pass


def repair_json(candidate: str) -> str:
    """Cheap deterministic fixes for typical LLM JSON glitches (before asking the model again)."""
    s = re.sub(r"//[^\n\"]*$", "", candidate, flags=re.M)          # // comments at line end
    s = re.sub(r",\s*([}\]])", r"\1", s)                          # trailing commas
    s = re.sub(r"\bTrue\b", "true", re.sub(r"\bFalse\b", "false", re.sub(r"\bNone\b", "null", s)))
    opened = s.count("{") - s.count("}")
    opened_b = s.count("[") - s.count("]")
    if 0 < opened <= 5 and 0 <= opened_b <= 5:                     # truncated answer: close brackets
        s = s.rstrip().rstrip(",") + "]" * opened_b + "}" * opened
    return s


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM answer (tolerates ``` fences, prose, small glitches)."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = m.group(1) if m else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1:
            raise PlanError("В ответе нет JSON-объекта")
        candidate = text[start:end + 1] if end > start else text[start:]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        try:
            data = json.loads(repair_json(candidate))
        except json.JSONDecodeError:
            raise PlanError(f"Некорректный JSON: {e}") from e
    if not isinstance(data, dict):
        raise PlanError("Ожидался JSON-объект")
    return data


_TYPE_ALIASES = {
    "usertask": "user_task", "servicetask": "service_task", "scripttask": "script_task",
    "manualtask": "manual_task", "sendtask": "send_task", "receivetask": "receive_task",
    "businessruletask": "business_rule_task", "sub_process": "subprocess",
    "xor": "exclusive_gateway", "exclusivegateway": "exclusive_gateway", "gateway": "exclusive_gateway",
    "and": "parallel_gateway", "parallelgateway": "parallel_gateway",
    "or": "inclusive_gateway", "inclusivegateway": "inclusive_gateway", "eventbasedgateway": "event_based_gateway",
    "startevent": "start_event", "start": "start_event", "endevent": "end_event", "end": "end_event",
    "timer": "timer_event", "timerevent": "timer_event", "intermediate_timer": "timer_event",
    "messageevent": "message_event", "message": "message_event", "activity": "task", "step": "task",
}
_VALID_TYPES = set(ElementType.__args__)


def normalize_ir_dict(data: dict) -> dict:
    """Coerce frequent shape mistakes of weaker models into the IR schema (deterministic)."""
    data = dict(data)
    for key in ("participants", "elements", "flows", "message_flows", "groups", "assumptions", "questions"):
        if data.get(key) is None:
            data.pop(key, None)
    parts = []
    for i, p in enumerate(data.get("participants", [])):
        if isinstance(p, str):
            p = {"id": f"p{i + 1}", "name": p}
        if isinstance(p, dict):
            p = dict(p)
            p.setdefault("id", f"p{i + 1}")
            p.setdefault("name", p["id"])
            parts.append(p)
    if "participants" in data:
        data["participants"] = parts
    by_name = {p["name"]: p["id"] for p in parts if isinstance(p.get("name"), str)}
    elements = []
    for i, e in enumerate(data.get("elements", [])):
        if not isinstance(e, dict):
            continue
        e = dict(e)
        e.setdefault("id", f"el{i + 1}")
        t = str(e.get("type", "task")).strip()
        key = re.sub(r"[\s\-]", "_", t).lower()
        if key not in _VALID_TYPES:
            key = _TYPE_ALIASES.get(key.replace("_", ""), _TYPE_ALIASES.get(key, key))
        if key not in _VALID_TYPES:
            e["assumption"] = (str(e.get("assumption") or "") + f" Тип «{t}» заменён на задачу.").strip()
            key = "task"
        e["type"] = key
        if e.get("participant") in by_name:          # participant given by name instead of id
            e["participant"] = by_name[e["participant"]]
        for k in ("documents", "consulted", "informed"):
            if isinstance(e.get(k), str):
                e[k] = [e[k]] if e[k] else []
            elif e.get(k) is None:
                e.pop(k, None)
        for k in ("source_quote", "assumption", "deadline", "name"):
            if e.get(k) is None:
                e[k] = ""
        elements.append(e)
    if "elements" in data:
        data["elements"] = elements
    for key in ("flows", "message_flows"):
        flows = []
        for f in data.get(key, []):
            if not isinstance(f, dict):
                continue
            f = dict(f)
            if "from" not in f and "source" in f:
                f["from"] = f.pop("source")
            if "to" not in f and "target" in f:
                f["to"] = f.pop("target")
            if "label" not in f and isinstance(f.get("condition"), str):
                f["label"] = f.pop("condition")
            if f.get("label") == "":
                f["label"] = None
            flows.append(f)
        if key in data:
            data[key] = flows
    return data


def ir_json_schema() -> dict:
    """JSON Schema of the IR used for structured LLM output."""
    return Plan.model_json_schema(by_alias=True)


def parse_plan(text_or_dict) -> Plan:
    data = extract_json(text_or_dict) if isinstance(text_or_dict, str) else text_or_dict
    if not isinstance(data, dict):
        raise PlanError("Ожидался JSON-объект плана")
    data = normalize_ir_dict(data)
    try:
        plan = Plan.model_validate(data)
    except ValidationError as e:
        msgs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:15]]
        raise PlanError("План не соответствует схеме:\n" + "\n".join(msgs)) from e
    check_plan(plan)
    return plan


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
    types = {e.id: e.type for e in plan.elements}
    for f in plan.flows:
        for ref in (f.source, f.target):
            if ref not in known:
                errors.append(f"связь {f.source}->{f.target}: неизвестный элемент {ref!r}")
        if f.default and types.get(f.source) not in ("exclusive_gateway", "inclusive_gateway"):
            errors.append(f"связь {f.source}->{f.target}: default=true допустим только у исключающего/"
                          "инклюзивного шлюза")
    for gw in {f.source for f in plan.flows if f.default}:
        if sum(1 for f in plan.flows if f.source == gw and f.default) > 1:
            errors.append(f"шлюз {gw}: больше одной ветки по умолчанию")
    for f in plan.flows:
        if f.source == f.target:
            errors.append(f"связь {f.source}->{f.target}: петля на самого себя")
    for f in plan.message_flows:
        for ref in (f.source, f.target):
            if ref not in known and ref not in pids:
                errors.append(f"сообщение {f.source}->{f.target}: неизвестный элемент/участник {ref!r}")
    clash = set(pids) & set(ids)
    if clash:
        errors.append(f"id участников совпадают с id элементов: {sorted(clash)}")
    by_id = {e.id: e for e in plan.elements}
    targets = {f.target for f in plan.flows}
    for e in plan.elements:
        if e.type == "boundary_timer":
            host = by_id.get(e.attached_to or "")
            if host is None or host.type not in ("task", "user_task", "service_task", "script_task", "manual_task",
                                                 "send_task", "receive_task", "business_rule_task", "subprocess"):
                errors.append(f"элемент {e.id}: boundary_timer должен быть прикреплён (attached_to) к задаче")
            elif host.parent != e.parent:
                errors.append(f"элемент {e.id}: таймер и задача {host.id} должны быть в одном контейнере")
            if e.id in targets:
                errors.append(f"элемент {e.id}: в граничное событие не могут входить связи")
            if not e.sla_hours:
                errors.append(f"элемент {e.id}: у boundary_timer задайте sla_hours (срок в часах)")
        elif e.attached_to:
            errors.append(f"элемент {e.id}: attached_to допустим только у boundary_timer")
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


def iso_duration(hours: float | None) -> str:
    """SLA in hours -> ISO 8601 duration for timerEventDefinition (P3D, PT4H, PT90M)."""
    if not hours:
        return "PT1H"
    if hours % 24 == 0:
        return f"P{int(hours // 24)}D"
    if float(hours).is_integer():
        return f"PT{int(hours)}H"
    return f"PT{int(round(hours * 60))}M"


def parse_iso_hours(value: str) -> float | None:
    m = re.fullmatch(r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?)?", (value or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi = (float(x) if x else 0.0 for x in m.groups())
    return d * 24 + h + mi / 60


def compile_plan(plan: Plan, annotate_assumptions: bool = False) -> str:
    """Deterministic IR -> DIAGRAM API code (the BPMN builder input). Element ids are preserved,
    so diagram ids == IR ids (stable across edits, used for diffs and highlighting)."""
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
                         f"[{', '.join(q(p.name) for p in internal)}], {q(org)}, "
                         f"ids=[{', '.join(q(p.id) for p in internal)}])")
            for i, p in enumerate(internal):
                container[p.id] = f"lanes_main[{i}]"
        for index, p in enumerate(external):
            pool_var = f"pool_external_{index}"
            if p.id in used_ext:
                lines.append(f"{pool_var}, lanes_external_{index} = DIAGRAM.add_pool(ROOT_PROCESS_ID, [], "
                             f"{q(p.name)}, id={q(p.id)})")
                container[p.id] = pool_var
            else:
                lines.append(f"{pool_var} = DIAGRAM.add_black_box_pool({q(p.name)}, id={q(p.id)})")
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
        ident = f"id={q(e.id)}"
        if e.type in _METHOD:
            lines.append(f"{v} = DIAGRAM.{_METHOD[e.type]}({q(e.name)}, {parent}, {ident})")
        elif e.type == "start_event":
            lines.append(f"{v} = DIAGRAM.add_start_event({q(e.name)}, {parent}, {q(e.event)}, {ident})")
        elif e.type == "end_event":
            lines.append(f"{v} = DIAGRAM.add_end_event({q(e.name)}, {parent}, {q(e.event)}, {ident})")
        elif e.type == "timer_event":
            lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(e.name)}, {parent}, 'timer', {ident})")
        elif e.type == "message_event":
            lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(e.name)}, {parent}, 'message', {ident})")
        elif e.type == "message_throw_event":
            lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(e.name)}, {parent}, 'message', True, {ident})")
        elif e.type == "boundary_timer":
            continue                                  # emitted after all hosts exist (below)
        extra = {k: getattr(e, k) for k in ("duration_min", "wait_min", "sla_hours", "accountable")
                 if getattr(e, k) is not None}
        extra.update({k: getattr(e, k) for k in ("consulted", "informed") if getattr(e, k)})
        if e.estimate:
            extra["estimate"] = True
        if e.source_quote or e.assumption or e.deadline or e.documents or extra:
            kw = "".join(f", {k}={v!r}" for k, v in extra.items())
            lines.append(f"DIAGRAM.set_details({v}, {q(e.source_quote)}, {q(e.assumption)}, "
                         f"{q(e.deadline)}, {e.documents!r}{kw})")
        if annotate_assumptions and e.assumption:
            lines.append(f"DIAGRAM.add_annotation({q('Допущение: ' + e.assumption[:160])}, {v})")
    for e in plan.elements:
        if e.type == "boundary_timer":
            v = var[e.id]
            lines.append(f"{v} = DIAGRAM.add_boundary_timer({q(e.name)}, {var[e.attached_to]}, "
                         f"{q(iso_duration(e.sla_hours))}, {e.interrupting!r}, id={q(e.id)})")
            lines.append(f"DIAGRAM.set_details({v}, {q(e.source_quote)}, {q(e.assumption)}, {q(e.deadline)}, "
                         f"{e.documents!r}, sla_hours={e.sla_hours!r})")
    for f in plan.flows:
        label = f", {q(f.label)}" if f.label else ""
        if f.default:
            label += (", None" if not f.label else "") + ", default=True"
        if f.probability is not None:
            label += f", probability={f.probability!r}"
        lines.append(f"DIAGRAM.add_link({var[f.source]}, {var[f.target]}{label})")
    for f in plan.message_flows:
        src = var.get(f.source) or container.get(f.source)
        dst = var.get(f.target) or container.get(f.target)
        label = f", {q(f.label)}" if f.label else ""
        lines.append(f"DIAGRAM.add_message_link({src}, {dst}{label})")
    return "\n".join(lines) + "\n"
