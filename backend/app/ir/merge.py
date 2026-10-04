"""Long documents: split into context-sized parts and merge the per-part IRs into one, without duplicates.

Merging is deterministic: participants are unified by normalized name, element ids are prefixed per
part, and the parts are chained — whatever led to "end" in part k now leads to the first steps of
part k+1. Repeated steps (same name and performer in consecutive parts, typical at part borders)
are collapsed.
"""
from __future__ import annotations

import re

from ..llm.plan import Plan, check_plan


def split_text(text: str, max_chars: int = 6000) -> list[str]:
    """Split by paragraphs (then sentences) so that each part fits the model context."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", p)
        buf = ""
        for s in sentences:
            while len(s) > max_chars:                      # pathological: no sentence breaks
                pieces.append(s[:max_chars])
                s = s[max_chars:]
            if len(buf) + len(s) + 1 > max_chars and buf:
                pieces.append(buf)
                buf = ""
            buf = (buf + " " + s).strip()
        if buf:
            pieces.append(buf)
    parts, buf = [], ""
    for p in pieces:
        if len(buf) + len(p) + 2 > max_chars and buf:
            parts.append(buf)
            buf = ""
        buf = (buf + "\n\n" + p).strip()
    if buf:
        parts.append(buf)
    return parts


def _norm(s: str) -> str:
    return re.sub(r"[^\wё]+", " ", (s or "").lower()).strip()


def merge_plans(plans: list[Plan]) -> Plan:
    if len(plans) == 1:
        return plans[0]
    participants: dict[str, dict] = {}          # normalized name -> participant
    pid_map: list[dict[str, str]] = []
    for k, plan in enumerate(plans):
        mapping = {}
        for p in plan.participants:
            key = _norm(p.name)
            if key not in participants:
                pid = p.id if all(x["id"] != p.id for x in participants.values()) else f"{p.id}_{k + 1}"
                participants[key] = {"id": pid, "name": p.name, "external": p.external}
            mapping[p.id] = participants[key]["id"]
        pid_map.append(mapping)

    elements, flows, message_flows, groups = [], [], [], []
    assumptions, questions = [], []
    prev_tail: list[tuple[str, str | None]] = []           # (source, label) leading to end in part k-1
    last_names: dict[tuple[str, str | None], str] = {}       # (name, participant) -> id of previous part
    for k, plan in enumerate(plans):
        pre = f"p{k + 1}_"
        ids = {e.id: pre + e.id for e in plan.elements}
        alias: dict[str, str] = {}
        for e in plan.elements:
            d = e.model_dump()
            d["id"] = ids[e.id]
            d["participant"] = pid_map[k].get(e.participant, e.participant) if e.participant else None
            d["parent"] = ids.get(e.parent) if e.parent else None
            d["group"] = (pre + e.group) if e.group else None
            key = (_norm(e.name), d["participant"])
            if (e.type not in ("exclusive_gateway", "parallel_gateway", "inclusive_gateway",
                               "event_based_gateway") and key[0] and key in last_names
                    and not e.parent):
                alias[d["id"]] = last_names[key]            # same step repeated at the part border
                continue
            elements.append(d)
        resolve = lambda x: alias.get(ids.get(x, x), ids.get(x, x))   # noqa: E731
        heads = [(resolve(f.target), f.label) for f in plan.flows if f.source == "start"]
        tails = []
        for f in plan.flows:
            fd = {"from": resolve(f.source), "to": resolve(f.target), "label": f.label,
                  "default": f.default, "probability": f.probability}
            if f.source == "start" and k > 0:
                continue                                     # replaced by the chain from part k-1
            if f.target == "end" and k < len(plans) - 1:
                tails.append((fd["from"], f.label))
                continue
            if fd["from"] != fd["to"]:
                flows.append(fd)
        if k > 0:
            for src, label in prev_tail:
                for head, _ in heads:
                    if src != head:
                        flows.append({"from": src, "to": head, "label": label})
            if not prev_tail:
                for head, _ in heads:
                    flows.append({"from": "start", "to": head})
        prev_tail = tails
        for e in plan.elements:
            if e.id in ids and ids[e.id] not in alias:
                last_names[(_norm(e.name), pid_map[k].get(e.participant, e.participant))] = ids[e.id]
        for f in plan.message_flows:
            message_flows.append({"from": resolve(f.source) if f.source in ids else pid_map[k].get(f.source, f.source),
                                  "to": resolve(f.target) if f.target in ids else pid_map[k].get(f.target, f.target),
                                  "label": f.label})
        groups += [{"id": pre + g.id, "name": g.name} for g in plan.groups]
        assumptions += [a for a in plan.assumptions if a not in assumptions]
        questions += [q for q in plan.questions if q not in questions]
    seen, uniq = set(), []
    for f in flows:
        key = (f["from"], f["to"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    merged = Plan.model_validate({
        "title": plans[0].title, "organization": plans[0].organization,
        "participants": list(participants.values()), "elements": elements, "flows": uniq,
        "message_flows": message_flows, "groups": groups,
        "assumptions": assumptions + [f"Документ обработан частями ({len(plans)}), результаты объединены."],
        "questions": questions})
    check_plan(merged)
    return merged
