"""BPMN 2.0 XML -> DIAGRAM API code.

Lets the analyst edit the diagram by hand in bpmn.io and then continue the
dialogue with the assistant: the edited file is converted back to code, so the
LLM edits exactly what the analyst sees. Also allows importing any existing
.bpmn file. Layout is recomputed; groups are not imported.
"""
from __future__ import annotations

import re
import json

from lxml import etree

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
NS = {"b": BPMN}

TASK_METHODS = {
    "task": "add_task", "userTask": "add_user_task", "scriptTask": "add_script_task",
    "serviceTask": "add_service_task", "manualTask": "add_manual_task", "sendTask": "add_send_task",
    "receiveTask": "add_receive_task", "businessRuleTask": "add_business_rule_task",
    "callActivity": "add_task",
    "exclusiveGateway": "add_exclusive_gateway", "parallelGateway": "add_parallel_gateway",
    "inclusiveGateway": "add_inclusive_gateway", "eventBasedGateway": "add_event_based_gateway",
    "complexGateway": "add_inclusive_gateway",
}
EVENT_DEFS = {"timerEventDefinition": "timer", "messageEventDefinition": "message",
              "errorEventDefinition": "error", "terminateEventDefinition": "terminate",
              "signalEventDefinition": "signal", "escalationEventDefinition": "escalation",
              "conditionalEventDefinition": "conditional"}


class ImportErrorBPMN(ValueError):
    pass


def _local(el) -> str:
    return etree.QName(el).localname


def _name(el) -> str:
    return re.sub(r"\s+", " ", el.get("name") or "").strip()


def bpmn_to_code(xml: str, id_variables: dict | None = None) -> str:
    try:
        root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise ImportErrorBPMN(f"Файл не является корректным XML: {e}") from e
    if _local(root) != "definitions":
        raise ImportErrorBPMN("Корневой элемент должен быть bpmn:definitions")

    q = repr
    lines = ["# Импортировано из BPMN-файла"]
    var: dict[str, str] = {}
    used: set[str] = set()

    def vname(el_id: str, hint: str) -> str:
        base = re.sub(r"\W", "_", el_id, flags=re.ASCII).strip("_").lower() or hint
        if base[0].isdigit():
            base = f"{hint}_{base}"
        v, k = base, 1
        while v in used:
            k += 1
            v = f"{base}_{k}"
        used.add(v)
        return v

    processes = {p.get("id"): p for p in root.findall("b:process", NS)}
    participants = root.findall("b:collaboration/b:participant", NS)
    proc_parent: dict[str, str] = {}
    lane_of_node: dict[str, str] = {}
    first = True
    for part in participants:
        pref = part.get("processRef")
        pv = vname(part.get("id"), "pool")
        var[part.get("id")] = pv
        if pref and pref in processes:
            proc = processes[pref]
            lanes = [ln for ln in proc.iterfind(".//b:lane", NS)
                     if ln.find("b:childLaneSet", NS) is None]
            names = [_name(ln) or f"Дорожка {i + 1}" for i, ln in enumerate(lanes)]
            lv = f"lanes_{pv}"
            lines.append(f"{pv}, {lv} = DIAGRAM.add_pool(ROOT_PROCESS_ID, [{', '.join(q(n) for n in names)}], "
                         f"{q(_name(part) or 'Пул')})")
            for i, ln in enumerate(lanes):
                for ref in ln.findall("b:flowNodeRef", NS):
                    lane_of_node[ref.text.strip()] = f"{lv}[{i}]"
            proc_parent[pref] = pv
            first = False
        else:
            lines.append(f"{pv} = DIAGRAM.add_black_box_pool({q(_name(part) or 'Участник')})")
    for pid in processes:
        if pid not in proc_parent:
            if not first:
                continue  # process not referenced by any participant
            proc_parent[pid] = "ROOT_PROCESS_ID"
            first = False

    flows: list[str] = []

    def emit(container_el, parent_expr: str, in_process: bool):
        for el in container_el:
            if not isinstance(el.tag, str):
                continue
            tag = _local(el)
            el_id = el.get("id")
            if not el_id:
                continue
            parent = lane_of_node.get(el_id, parent_expr) if in_process else parent_expr
            if tag in TASK_METHODS:
                v = vname(el_id, "n")
                var[el_id] = v
                lines.append(f"{v} = DIAGRAM.{TASK_METHODS[tag]}({q(_name(el))}, {parent}, id={q(el_id)})")
            elif tag in ("subProcess", "transaction", "adHocSubProcess"):
                v = vname(el_id, "sp")
                var[el_id] = v
                lines.append(f"{v} = DIAGRAM.create_subprocess({q(_name(el))}, {parent}, id={q(el_id)})")
                emit(el, v, False)
            elif tag in ("startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent",
                         "boundaryEvent"):
                kind = None
                for child in el:
                    if isinstance(child.tag, str) and _local(child) in EVENT_DEFS:
                        kind = EVENT_DEFS[_local(child)]
                v = vname(el_id, "ev")
                var[el_id] = v
                if tag == "startEvent":
                    k = kind if kind in ("message", "timer", "signal", "conditional") else None
                    lines.append(f"{v} = DIAGRAM.add_start_event({q(_name(el))}, {parent}, {q(k)}, id={q(el_id)})")
                elif tag == "endEvent":
                    k = kind if kind in ("message", "error", "terminate", "signal", "escalation") else None
                    lines.append(f"{v} = DIAGRAM.add_end_event({q(_name(el))}, {parent}, {q(k)}, id={q(el_id)})")
                elif tag == "intermediateThrowEvent":
                    k = kind if kind in ("message", "signal", "escalation") else "message"
                    lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(_name(el))}, {parent}, {q(k)}, True, "
                                 f"id={q(el_id)})")
                else:
                    k = kind if kind in ("message", "timer", "signal", "conditional") else "timer"
                    lines.append(f"{v} = DIAGRAM.add_intermediate_event({q(_name(el))}, {parent}, {q(k)}, "
                                 f"id={q(el_id)})")
            elif tag == "sequenceFlow":
                flows.append(("seq", el.get("sourceRef"), el.get("targetRef"), _name(el), el.get("id")))
            elif tag == "textAnnotation":
                pass

    for pid, pexpr in proc_parent.items():
        emit(processes[pid], pexpr, True)
    for mf in root.findall("b:collaboration/b:messageFlow", NS):
        flows.append(("msg", mf.get("sourceRef"), mf.get("targetRef"), _name(mf), mf.get("id")))

    defaults = {el.get("default") for el in root.iter() if el.get("default")}
    for kind, s, t, label, fid in flows:
        if s not in var or t not in var:
            continue  # e.g. boundary attachments or unsupported elements
        lab = f", {q(label)}" if label else ""
        if kind == "seq" and fid in defaults:
            lab += (", None" if not label else "") + ", default=True"
        method = "add_link" if kind == "seq" else "add_message_link"
        lines.append(f"DIAGRAM.{method}({var[s]}, {var[t]}{lab})")

    for ta in root.iter(f"{{{BPMN}}}textAnnotation"):
        text_el = ta.find("b:text", NS)
        text = (text_el.text or "").strip() if text_el is not None else ""
        for assoc in root.iter(f"{{{BPMN}}}association"):
            src, dst = assoc.get("sourceRef"), assoc.get("targetRef")
            other = src if dst == ta.get("id") else dst if src == ta.get("id") else None
            if other in var and text:
                lines.append(f"DIAGRAM.add_annotation({q(text)}, {var[other]})")
    for el in root.iter():
        if el.get("id") not in var or _local(el) == "participant":
            continue
        for doc in el.findall("b:documentation", NS):
            raw = doc.text or ""
            if not raw.startswith("BPMN_AGENT_DETAILS:"):
                continue
            try:
                data = json.loads(raw.removeprefix("BPMN_AGENT_DETAILS:"))
                values = [data.get(k, "") for k in ("source_quote", "assumption", "deadline")]
                documents = data.get("documents", [])
                if not all(isinstance(v, str) for v in values) or not isinstance(documents, list):
                    continue
                if not all(isinstance(v, str) for v in documents):
                    continue
                extra = {k: data[k] for k in ("duration_min", "wait_min", "sla_hours")
                         if isinstance(data.get(k), (int, float)) and data[k] >= 0}
                if data.get("estimate") is True:
                    extra["estimate"] = True
                if isinstance(data.get("accountable"), str):
                    extra["accountable"] = data["accountable"]
                for k in ("consulted", "informed"):
                    if isinstance(data.get(k), list) and all(isinstance(v, str) for v in data[k]):
                        extra[k] = data[k]
                kw = "".join(f", {k}={v!r}" for k, v in extra.items())
                lines.append(f"DIAGRAM.set_details({var[el.get('id')]}, "
                             f"{', '.join(repr(v) for v in values)}, {documents!r}{kw})")
            except (ValueError, AttributeError):
                continue
    if id_variables is not None:
        id_variables.update(var)
    return "\n".join(lines) + "\n"
