"""Optional Camunda 8 (Zeebe) compatibility markup for the exported BPMN (off by default).

Adds modeler:executionPlatform, makes processes executable, gives service tasks a zeebe:taskDefinition
and user tasks zeebe:userTask. Branch conditions stay as text: Camunda needs FEEL expressions, so the
result is a starting point for an engineer, not a deployable process. The file stays XSD-valid.
"""
from __future__ import annotations

import re

from lxml import etree

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
ZEEBE = "http://camunda.org/schema/zeebe/1.0"
MODELER = "http://camunda.org/schema/modeler/1.0"


def to_camunda8(xml: str, version: str = "8.6.0") -> tuple[str, list[str]]:
    root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
    nsmap = dict(root.nsmap)
    nsmap.update({"zeebe": ZEEBE, "modeler": MODELER})
    new_root = etree.Element(root.tag, nsmap=nsmap, attrib=dict(root.attrib))
    new_root.extend(list(root))
    root = new_root
    root.set(f"{{{MODELER}}}executionPlatform", "Camunda Cloud")
    root.set(f"{{{MODELER}}}executionPlatformVersion", version)
    notes = []
    for proc in root.iter(f"{{{BPMN}}}process"):
        if proc.find(f"{{{BPMN}}}*") is not None:
            proc.set("isExecutable", "true")

    def ext(el):
        e = el.find(f"{{{BPMN}}}extensionElements")
        if e is None:
            e = etree.Element(f"{{{BPMN}}}extensionElements")
            docs = el.findall(f"{{{BPMN}}}documentation")
            el.insert(len(docs), e)                       # after documentation, before incoming
        return e
    for st in root.iter(f"{{{BPMN}}}serviceTask", f"{{{BPMN}}}sendTask", f"{{{BPMN}}}scriptTask",
                        f"{{{BPMN}}}businessRuleTask"):
        job = re.sub(r"[^a-z0-9]+", "-", (st.get("id") or "job").lower()).strip("-")
        etree.SubElement(ext(st), f"{{{ZEEBE}}}taskDefinition", type=job)
    for ut in root.iter(f"{{{BPMN}}}userTask"):
        etree.SubElement(ext(ut), f"{{{ZEEBE}}}userTask")
    conds = list(root.iter(f"{{{BPMN}}}conditionExpression"))
    if conds:
        notes.append(f"Условий ветвей: {len(conds)} — они записаны текстом; для исполнения задайте FEEL-выражения.")
    notes.append("Типы заданий (zeebe:taskDefinition) сгенерированы из id шагов — привяжите к своим воркерам.")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode(), notes
