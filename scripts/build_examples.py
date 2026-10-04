"""Rebuild examples/*: plan.json -> code.py -> result.bpmn (+ report.json, result.png/svg).

Usage:
  python scripts/build_examples.py            # deterministic: reference plan -> plan compiler
  python scripts/build_examples.py --llm      # full pipeline through the configured LLM
  python scripts/build_examples.py --render   # also render PNG/SVG with bpmn-js (needs node + playwright)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.llm.client import make_client  # noqa: E402
from app.llm.plan import compile_plan, parse_plan  # noqa: E402
from app.pipeline import Pipeline, build  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("only", nargs="*")
    args = ap.parse_args()
    s = get_settings()
    pipe = Pipeline(make_client(s), s.runs_dir, s.max_repairs) if args.llm else None
    for d in sorted((ROOT / "examples").glob("*/")):
        if args.only and d.name not in args.only:
            continue
        text = (d / "input.txt").read_text("utf-8")
        if pipe:
            res = pipe.generate(text)
            if res.plan:
                (d / "plan.json").write_text(json.dumps(res.plan, ensure_ascii=False, indent=2), "utf-8")
            code, xml, ok = res.code, res.xml, res.ok
            report = {"source": res.source, "issues": res.issues, "xsd_errors": res.xsd_errors,
                      "stats": res.stats, "assumptions": res.assumptions, "questions": res.questions,
                      "run_id": res.run_id, "duration_s": res.duration_s}
        else:
            if not (d / "plan.json").exists():
                print(f"{d.name}: пропущен — нет эталонного plan.json (пример для генерации через модель)")
                continue
            plan = parse_plan((d / "plan.json").read_text("utf-8"))
            code = compile_plan(plan)
            r = build(code, plan.title)
            code, xml, ok = r.code, r.xml, r.ok
            report = {"source": "reference_plan+plan_compiler", "issues": [i.to_dict() for i in r.issues],
                      "xsd_errors": r.xsd_errors, "stats": r.stats, "assumptions": plan.assumptions,
                      "questions": plan.questions, "error": r.error}
        (d / "code.py").write_text(code, "utf-8")
        if xml:
            (d / "result.bpmn").write_text(xml, "utf-8")
        (d / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        line = f"{d.name}: ok={ok} stats={report['stats']}"
        if args.render and xml:
            p = subprocess.run(["node", str(ROOT / "tools" / "render.mjs"), str(d / "result.bpmn"),
                                str(d / "result.png"), str(d / "result.svg")], capture_output=True, text=True)
            line += f" render={p.stdout.strip()}"
        print(line)
        for i in report["issues"]:
            if i["level"] != "fix":
                print("   ", i["level"], i["code"], i["message"])


if __name__ == "__main__":
    main()
