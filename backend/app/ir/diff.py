"""Before/after comparison of two IR versions (dialogue edits, version history)."""
from __future__ import annotations

from ..llm.plan import Plan

FIELDS = ("type", "name", "participant", "parent", "event", "deadline", "duration_min", "sla_hours")


def diff_plans(before: Plan, after: Plan) -> dict:
    b = {e.id: e for e in before.elements}
    a = {e.id: e for e in after.elements}
    added = [{"id": i, "name": a[i].name, "type": a[i].type} for i in a if i not in b]
    removed = [{"id": i, "name": b[i].name, "type": b[i].type} for i in b if i not in a]
    changed = []
    for i in a:
        if i in b:
            fields = {f: [getattr(b[i], f), getattr(a[i], f)] for f in FIELDS if getattr(b[i], f) != getattr(a[i], f)}
            if fields:
                changed.append({"id": i, "name": a[i].name, "fields": fields})
    fb = {(f.source, f.target): f for f in before.flows}
    fa = {(f.source, f.target): f for f in after.flows}
    flows_added = [{"from": s, "to": t, "label": fa[(s, t)].label} for s, t in fa if (s, t) not in fb]
    flows_removed = [{"from": s, "to": t, "label": fb[(s, t)].label} for s, t in fb if (s, t) not in fa]
    flows_changed = [{"from": s, "to": t, "label": [fb[(s, t)].label, fa[(s, t)].label]}
                     for s, t in fa if (s, t) in fb and (fb[(s, t)].label != fa[(s, t)].label
                                                         or fb[(s, t)].default != fa[(s, t)].default)]
    pb = {p.id: p.name for p in before.participants}
    pa = {p.id: p.name for p in after.participants}
    return {
        "added": added, "removed": removed, "changed": changed,
        "flows_added": flows_added, "flows_removed": flows_removed, "flows_changed": flows_changed,
        "participants_added": [{"id": k, "name": v} for k, v in pa.items() if k not in pb],
        "participants_removed": [{"id": k, "name": v} for k, v in pb.items() if k not in pa],
        "empty": not (added or removed or changed or flows_added or flows_removed or flows_changed
                      or pa.keys() != pb.keys()),
        # ids to highlight on the new diagram
        "highlight": {"added": [x["id"] for x in added], "changed": [x["id"] for x in changed]
                      + sorted({f["to"] for f in flows_added if f["to"] in a} - {x["id"] for x in added})},
    }


def describe(diff: dict) -> str:
    parts = []
    if diff["added"]:
        parts.append("добавлено: " + ", ".join(f"«{x['name'] or x['id']}»" for x in diff["added"]))
    if diff["removed"]:
        parts.append("удалено: " + ", ".join(f"«{x['name'] or x['id']}»" for x in diff["removed"]))
    if diff["changed"]:
        parts.append("изменено: " + ", ".join(f"«{x['name'] or x['id']}»" for x in diff["changed"]))
    if diff["flows_added"] or diff["flows_removed"]:
        parts.append(f"связей +{len(diff['flows_added'])}/−{len(diff['flows_removed'])}")
    return "; ".join(parts) or "изменений нет"
