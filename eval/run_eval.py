"""Eval bench: rerun the whole pipeline on a fixed set of descriptions and score the result.

Usage:
  python eval/run_eval.py                 # through the configured LLM (LLM_* in .env)
  python eval/run_eval.py --offline       # reference plans only (checks the deterministic part)
  python eval/run_eval.py --modes two_stage direct --render   # compare modes, check in bpmn-js

Metrics per case: built / XSD-valid / opens in bpmn-js without warnings, repair rounds, source
(llm, llm_repaired, plan_compiler, lenient), latency, structural recall (expected participants
found among lanes/pools, expected gateway kinds present, task count, loops), validator warnings.
Writes eval/results/<timestamp>.json and .md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.llm.plan import compile_plan, parse_plan  # noqa: E402
from app.pipeline import Pipeline, build  # noqa: E402
from app.sandbox import run_code  # noqa: E402
from app.bpmn.validator import normalize  # noqa: E402


def structure(code: str) -> dict:
    d = run_code(code).diagram
    normalize(d)
    names = [ln.name for ln in d.lanes.values()] + [p.name for p in d.pools.values()]
    kinds = {n.kind for n in d.nodes.values()}
    tasks = sum(1 for n in d.nodes.values() if n.is_task)
    # loop = any flow whose target appears before its source in a DFS order
    order, seen, loop = {}, set(), False

    def dfs(v):
        nonlocal loop
        seen.add(v)
        order[v] = True
        for f in d.outgoing(v):
            if order.get(f.target):
                loop = True
            elif f.target not in seen:
                dfs(f.target)
        order[v] = False

    for n in d.nodes.values():
        if n.kind == "startEvent" and n.id not in seen:
            dfs(n.id)
    return {"names": names, "kinds": sorted(kinds), "tasks": tasks, "loop": loop}


def score(expect: dict, st: dict) -> dict:
    found = [p for p in expect.get("participants", []) if any(p.lower() in n.lower() for n in st["names"])]
    kinds = [k for k in expect.get("kinds", []) if k in st["kinds"]]
    checks = {
        "participants": len(found) / max(1, len(expect.get("participants", []))),
        "gateways": len(kinds) / max(1, len(expect.get("kinds", []))),
        "tasks": 1.0 if st["tasks"] >= expect.get("min_tasks", 0) else st["tasks"] / expect["min_tasks"],
    }
    if expect.get("loop"):
        checks["loop"] = 1.0 if st["loop"] else 0.0
    checks["total"] = round(sum(checks.values()) / len(checks), 3)
    checks["missing_participants"] = [p for p in expect.get("participants", []) if p not in found]
    return checks


def bpmnjs_check(xml: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".bpmn", delete=False, encoding="utf-8") as f:
        f.write(xml)
    p = subprocess.run(["node", str(ROOT / "tools" / "render.mjs"), f.name], capture_output=True, text=True)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": p.stderr[-300:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use reference plans, no LLM")
    ap.add_argument("--modes", nargs="+", default=["two_stage"])
    ap.add_argument("--render", action="store_true", help="also import into bpmn-js (node + playwright)")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    cases = json.loads((ROOT / "eval" / "cases.json").read_text("utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    s = get_settings()
    pipe = None
    if not args.offline:
        from app.llm.client import make_client
        pipe = Pipeline(make_client(s), s.runs_dir, s.max_repairs)

    rows = []
    for mode in (["reference"] if args.offline else args.modes):
        for c in cases:
            text = c.get("text") or (ROOT / c["input"]).read_text("utf-8")
            t0 = time.time()
            if args.offline:
                if "reference_plan" not in c:
                    continue
                plan = parse_plan((ROOT / c["reference_plan"]).read_text("utf-8"))
                r = build(compile_plan(plan), plan.title)
                res = {"ok": r.ok, "xml": r.xml, "code": r.code, "xsd_errors": r.xsd_errors,
                       "issues": [i.to_dict() for i in r.issues], "source": "reference", "attempts": []}
            else:
                res = pipe.generate(text, mode).to_dict()
            row = {"id": c["id"], "mode": mode, "built": bool(res["xml"]), "ok": res["ok"],
                   "xsd_valid": bool(res["xml"]) and not res["xsd_errors"], "source": res["source"],
                   "repairs": sum(1 for a in res["attempts"] if a["stage"].startswith("repair")),
                   "warnings": sum(1 for i in res["issues"] if i["level"] == "warning"),
                   "seconds": round(time.time() - t0, 2)}
            if res["xml"]:
                row["score"] = score(c["expect"], structure(res["code"]))
                if args.render:
                    row["bpmnjs"] = bpmnjs_check(res["xml"])
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))

    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + ("_offline" if args.offline else "")
    (out_dir / f"{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), "utf-8")
    md = ["| case | mode | built | XSD | bpmn-js | source | repairs | score | sec |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        bj = r.get("bpmnjs", {})
        md.append(f"| {r['id']} | {r['mode']} | {'✓' if r['built'] else '✗'} | {'✓' if r['xsd_valid'] else '✗'} | "
                  f"{'✓' if bj.get('ok') and not bj.get('warnings') else ('–' if not bj else '✗')} | {r['source']} | "
                  f"{r['repairs']} | {r.get('score', {}).get('total', '–')} | {r['seconds']} |")
    n = len(rows) or 1
    md.append("")
    md.append(f"Построено: {sum(r['built'] for r in rows)}/{len(rows)}, XSD-валидно: {sum(r['xsd_valid'] for r in rows)}/{len(rows)}, "
              f"средний структурный балл: {round(sum(r.get('score', {}).get('total', 0) for r in rows) / n, 3)}, "
              f"с первой попытки: {sum(r['source'] in ('llm', 'reference') for r in rows)}/{len(rows)}")
    (out_dir / f"{stamp}.md").write_text("\n".join(md), "utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
