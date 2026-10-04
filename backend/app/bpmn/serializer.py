"""Diagram + layout -> BPMN 2.0 XML (semantic model + BPMN DI)."""
from __future__ import annotations

import json
from lxml import etree

from .diagram import SUBPROCESS_KIND, Diagram
from .layout import LayoutResult, Plane

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI = "http://www.omg.org/spec/BPMN/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
DI = "http://www.omg.org/spec/DD/20100524/DI"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
NSMAP = {"bpmn": BPMN, "bpmndi": BPMNDI, "dc": DC, "di": DI, "xsi": XSI}

EVENT_DEF_TAG = {
    "timer": "timerEventDefinition",
    "message": "messageEventDefinition",
    "error": "errorEventDefinition",
    "signal": "signalEventDefinition",
    "terminate": "terminateEventDefinition",
    "escalation": "escalationEventDefinition",
    "conditional": "conditionalEventDefinition",
}
COND_GATEWAYS = {"exclusiveGateway", "inclusiveGateway"}


def _b(tag: str) -> str:
    return f"{{{BPMN}}}{tag}"


def _r(v: float) -> str:
    return str(int(round(v)))


def to_xml(d: Diagram, lay: LayoutResult, exporter_version: str = "1.0") -> str:
    root = etree.Element(_b("definitions"), nsmap=NSMAP)
    root.set("id", "Definitions_1")
    root.set("targetNamespace", "http://bpmn.io/schema/bpmn")
    root.set("exporter", "BPMN Agent")
    root.set("exporterVersion", exporter_version)
    if d.name:
        root.set("name", d.name)        # the process title: pools carry the organization's name

    if d.groups:
        cat = etree.SubElement(root, _b("category"), id="Category_1")
        for g in d.groups.values():
            etree.SubElement(cat, _b("categoryValue"), id=f"CategoryValue_{g.id}", value=g.name)

    collab_id = None
    if d.pools:
        collab_id = "Collaboration_1"
        collab = etree.SubElement(root, _b("collaboration"), id=collab_id)
        for pool in d.pools.values():
            attrs = {"id": pool.id, "name": pool.name}
            if pool.process:
                attrs["processRef"] = pool.process
            etree.SubElement(collab, _b("participant"), **attrs)
        for f in d.message_flows():
            el = etree.SubElement(collab, _b("messageFlow"), id=f.id, sourceRef=f.source, targetRef=f.target)
            if f.name:
                el.set("name", f.name)

    for proc in d.processes.values():
        has_content = any(n.container == proc.id for n in d.nodes.values())
        if not has_content and proc.pool is None:
            continue
        pel = etree.SubElement(root, _b("process"), id=proc.id, isExecutable="false")
        if proc.id == d.root_process and d.performers:     # performers directory travels with the file
            etree.SubElement(pel, _b("documentation"), textFormat="application/json").text = (
                "BPMN_AGENT_PERFORMERS:" + json.dumps(d.performers, ensure_ascii=False))
        if proc.name:
            pel.set("name", proc.name)
        if proc.lanes:
            ls = etree.SubElement(pel, _b("laneSet"), id=f"LaneSet_{proc.id}")
            for lid in proc.lanes:
                lane = d.lanes[lid]
                lel = etree.SubElement(ls, _b("lane"), id=lid, name=lane.name)
                for n in d.nodes.values():
                    if n.container == proc.id and n.lane == lid:
                        etree.SubElement(lel, _b("flowNodeRef")).text = n.id
        _emit_container(d, pel, proc.id)

    # ----- DI
    for i, plane in enumerate(lay.planes):
        dia = etree.SubElement(root, f"{{{BPMNDI}}}BPMNDiagram", id=f"BPMNDiagram_{i + 1}")
        element = plane.element or collab_id or d.root_process
        pl = etree.SubElement(dia, f"{{{BPMNDI}}}BPMNPlane", id=f"BPMNPlane_{i + 1}", bpmnElement=element)
        _emit_plane(d, pl, plane)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode("utf-8")


def _emit_container(d: Diagram, parent_el, container: str) -> None:
    for n in d.nodes.values():
        if n.container != container:
            continue
        el = etree.SubElement(parent_el, _b(n.kind), id=n.id)
        if n.name:
            el.set("name", n.name)
        if n.kind == "boundaryEvent":
            el.set("attachedToRef", n.attached_to)
            el.set("cancelActivity", "true" if n.interrupting else "false")
        default = next((f.id for f in d.outgoing(n.id) if f.default), None)
        if default and n.kind in COND_GATEWAYS:
            el.set("default", default)
        details = dict(n.details or {})
        description = details.pop("description", "")
        if description:                                   # plain BPMN documentation, readable by any tool
            etree.SubElement(el, _b("documentation")).text = description
        if details:
            etree.SubElement(el, _b("documentation"), textFormat="application/json").text = (
                "BPMN_AGENT_DETAILS:" + json.dumps(details, ensure_ascii=False))
        for f in d.incoming(n.id):
            etree.SubElement(el, _b("incoming")).text = f.id
        for f in d.outgoing(n.id):
            etree.SubElement(el, _b("outgoing")).text = f.id
        if n.event_definition:
            ed = etree.SubElement(el, _b(EVENT_DEF_TAG[n.event_definition]), id=f"{n.id}_ed")
            if n.event_definition == "conditional":
                c = etree.SubElement(ed, _b("condition"))
                c.set(f"{{{XSI}}}type", "bpmn:tFormalExpression")
            if n.event_definition == "timer" and n.timer:
                td = etree.SubElement(ed, _b("timeDuration"))
                td.set(f"{{{XSI}}}type", "bpmn:tFormalExpression")
                td.text = n.timer
        if n.kind == SUBPROCESS_KIND:
            _emit_container(d, el, n.id)
    for f in d.sequence_flows():
        if d.nodes[f.source].container != container:
            continue
        el = etree.SubElement(parent_el, _b("sequenceFlow"), id=f.id, sourceRef=f.source, targetRef=f.target)
        flow_details = {k: v for k, v in (("probability", f.probability), ("check", f.check)) if v not in (None, "")}
        if flow_details:
            etree.SubElement(el, _b("documentation"), textFormat="application/json").text = (
                "BPMN_AGENT_DETAILS:" + json.dumps(flow_details, ensure_ascii=False))
        if f.name:
            el.set("name", f.name)
            if (d.nodes[f.source].kind in COND_GATEWAYS and len(d.outgoing(f.source)) > 1
                    and not f.default):
                ce = etree.SubElement(el, _b("conditionExpression"))
                ce.set(f"{{{XSI}}}type", "bpmn:tFormalExpression")
                ce.text = f.name
    for a in d.annotations.values():
        if a.container != container:
            continue
        ta = etree.SubElement(parent_el, _b("textAnnotation"), id=a.id)
        etree.SubElement(ta, _b("text")).text = a.text
        etree.SubElement(parent_el, _b("association"), id=f"Association_{a.id}", sourceRef=a.target, targetRef=a.id)
    for g in d.groups.values():
        if g.container != container:
            continue
        etree.SubElement(parent_el, _b("group"), id=g.id, categoryValueRef=f"CategoryValue_{g.id}")


def _bounds(parent, b):
    etree.SubElement(parent, f"{{{DC}}}Bounds", x=_r(b.x), y=_r(b.y), width=_r(b.w), height=_r(b.h))


def _emit_plane(d: Diagram, pl, plane: Plane) -> None:
    def shape(el_id, b, **extra):
        s = etree.SubElement(pl, f"{{{BPMNDI}}}BPMNShape", id=f"{el_id}_di", bpmnElement=el_id, **extra)
        _bounds(s, b)
        return s

    order = (
        [k for k in plane.shapes if k in d.pools]
        + [k for k in plane.shapes if k in d.lanes]
        + [k for k in plane.shapes if k in d.nodes]
        + [k for k in plane.shapes if k in d.annotations]
        + [k for k in plane.shapes if k in d.groups]
    )
    for k in order:
        b = plane.shapes[k]
        extra = {}
        if k in plane.horizontal:
            extra["isHorizontal"] = "true"
        if k in d.nodes and d.nodes[k].kind == SUBPROCESS_KIND:
            extra["isExpanded"] = "false"
        sh = shape(k, b, **extra)
        if k in plane.labels:
            lab = etree.SubElement(sh, f"{{{BPMNDI}}}BPMNLabel")
            _bounds(lab, plane.labels[k])
    for fid, pts in plane.edges.items():
        e = etree.SubElement(pl, f"{{{BPMNDI}}}BPMNEdge", id=f"{fid}_di", bpmnElement=fid)
        for x, y in pts:
            etree.SubElement(e, f"{{{DI}}}waypoint", x=_r(x), y=_r(y))
        if fid in plane.labels:
            lab = etree.SubElement(e, f"{{{BPMNDI}}}BPMNLabel")
            _bounds(lab, plane.labels[fid])
