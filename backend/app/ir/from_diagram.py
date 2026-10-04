"""Diagram (built from code or imported BPMN XML) -> IR.

Closes the loop "edit on the canvas -> edit by dialogue": the current BPMN from bpmn-js is imported,
turned back into the IR, the LLM edits the IR, and the result is rebuilt. Ids are preserved.
"""
from __future__ import annotations

from ..bpmn.diagram import Diagram
from ..bpmn.importer import bpmn_to_code
from ..llm.plan import Plan, check_plan
from ..sandbox import run_code

KIND_TO_TYPE = {
    "task": "task", "userTask": "user_task", "serviceTask": "service_task", "scriptTask": "script_task",
    "manualTask": "manual_task", "sendTask": "send_task", "receiveTask": "receive_task",
    "businessRuleTask": "business_rule_task", "subProcess": "subprocess",
    "exclusiveGateway": "exclusive_gateway", "parallelGateway": "parallel_gateway",
    "inclusiveGateway": "inclusive_gateway", "eventBasedGateway": "event_based_gateway",
    "startEvent": "start_event", "endEvent": "end_event",
}


def diagram_to_plan(d: Diagram) -> Plan:
    root_pool = d.processes[d.root_process].pool
    participants, organization = [], None
    pool_participant: dict[str, str] = {}
    for pool in d.pools.values():
        proc = d.processes.get(pool.process) if pool.process else None
        if pool.id == root_pool:
            organization = pool.name
            if not proc.lanes:
                participants.append({"id": pool.id, "name": pool.name, "external": False})
                pool_participant[proc.id] = pool.id
        elif proc is None or not proc.lanes:
            participants.append({"id": pool.id, "name": pool.name, "external": True})
            if proc is not None:
                pool_participant[proc.id] = pool.id
        if proc is not None:
            for lid in proc.lanes:
                participants.append({"id": lid, "name": d.lanes[lid].name, "external": pool.id != root_pool})

    skip = {n.id for n in d.nodes.values() if n.auto}
    reserved = {}
    for rid, kind in ((d.root_start, "startEvent"), (d.root_end, "endEvent")):
        n = d.nodes.get(rid)
        if n is not None and n.kind == kind and n.container == d.root_process:
            reserved[rid] = "start" if kind == "startEvent" else "end"

    elements = []
    for n in d.nodes.values():
        if n.id in skip or n.id in reserved:
            continue
        if n.kind in KIND_TO_TYPE:
            t = KIND_TO_TYPE[n.kind]
        elif n.kind == "intermediateThrowEvent":
            t = "message_throw_event"
        else:
            t = "timer_event" if n.event_definition == "timer" else "message_event"
        in_sub = n.container not in d.processes
        participant = None
        if not in_sub:
            participant = n.lane or pool_participant.get(n.container)
        details = dict(n.details or {})
        el = {"id": n.id, "type": t, "name": n.name, "participant": participant,
              "parent": n.container if in_sub else None, "group": n.group,
              "event": n.event_definition if t in ("start_event", "end_event") else None}
        for key in ("source_quote", "assumption", "deadline"):
            el[key] = details.get(key, "") or ""
        el["documents"] = details.get("documents", []) or []
        for key in ("duration_min", "wait_min", "sla_hours", "accountable"):
            if details.get(key) is not None:
                el[key] = details[key]
        for key in ("consulted", "informed"):
            if details.get(key):
                el[key] = details[key]
        el["estimate"] = bool(details.get("estimate"))
        elements.append(el)
    rid = lambda x: reserved.get(x, x)  # noqa: E731
    flows = [{"from": rid(f.source), "to": rid(f.target), "label": f.name or None,
              "default": f.default, "probability": f.probability}
             for f in d.sequence_flows() if f.source not in skip and f.target not in skip]
    message_flows = [{"from": rid(f.source), "to": rid(f.target), "label": f.name or None}
                     for f in d.message_flows()]
    groups = [{"id": g.id, "name": g.name} for g in d.groups.values()]
    plan = Plan.model_validate({
        "title": d.processes[d.root_process].name or d.name, "organization": organization,
        "participants": participants, "elements": elements, "flows": flows,
        "message_flows": message_flows, "groups": groups})
    check_plan(plan)
    return plan


def xml_to_plan(xml: str) -> Plan:
    from ..bpmn.validator import normalize
    d = run_code(bpmn_to_code(xml)).diagram
    normalize(d)
    return diagram_to_plan(d)
