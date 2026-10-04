"""AI action journal: every request, LLM exchange, generated IR/code and produced file.

runs/<timestamp>_<id>/
  input.txt, ir.json, code_<n>.py, errors_<n>.txt, result.bpmn, llm.jsonl, meta.json
meta.json holds the summary: model(s), prompt versions, tokens, timings, errors found,
number of self-repair rounds. Secrets (API keys, bearer tokens) are scrubbed before writing;
personal data is already masked before it reaches the model (see pii.py).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

_SECRET = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|token|secret|password)(\"?\s*[:=]\s*\"?)([^\s\",]{6,})"
    r"|\b(sk-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|AQ\.[0-9A-Za-z_\-]{20,}|t1\.[0-9A-Za-z_\-.]{20,})")


def scrub(text: str) -> str:
    def repl(m: re.Match) -> str:
        if m.group(4):
            return m.group(4)[:4] + "…[скрыто]"
        return m.group(1) + m.group(2) + "[скрыто]"
    return _SECRET.sub(repl, text)


class RunLog:
    def __init__(self, runs_dir: Path | None, kind: str):
        self.id = time.strftime("%Y%m%d-%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.kind = kind
        self.dir = (runs_dir / self.id) if runs_dir else None
        if self.dir:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"[bpmn-agent] Журнал запуска недоступен: {exc}")
                self.dir = None
        self.meta: dict = {"id": self.id, "kind": kind, "started": time.time(), "events": [],
                           "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0,
                           "models": [], "prompts": {}}

    def write(self, name: str, content: str) -> None:
        if self.dir:
            try:
                (self.dir / name).write_text(scrub(content), encoding="utf-8")
            except OSError as exc:
                print(f"[bpmn-agent] Не удалось сохранить журнал: {exc}")
                self.dir = None

    def event(self, stage: str, **data) -> None:
        self.meta["events"].append({"t": round(time.time() - self.meta["started"], 3), "stage": stage, **data})

    def prompts(self, versions: dict) -> None:
        self.meta["prompts"].update(versions)

    def llm(self, stage: str, messages: list[dict], response, schema: bool = False) -> None:
        m = self.meta
        m["llm_calls"] += 1
        m["input_tokens"] += response.input_tokens or 0
        m["output_tokens"] += response.output_tokens or 0
        m["llm_seconds"] = round(m["llm_seconds"] + response.latency_s, 2)
        if response.model not in m["models"]:
            m["models"].append(response.model)
        if self.dir:
            rec = {"stage": stage, "model": response.model, "latency_s": round(response.latency_s, 2),
                   "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                   "output_mode": getattr(response, "mode", None), "schema": schema,
                   "notes": getattr(response, "notes", []), "messages": messages, "response": response.text}
            try:
                with (self.dir / "llm.jsonl").open("a", encoding="utf-8") as f:
                    f.write(scrub(json.dumps(rec, ensure_ascii=False)) + "\n")
            except OSError as exc:
                print(f"[bpmn-agent] Не удалось сохранить обмен с моделью: {exc}")
                self.dir = None
        self.event(stage, model=response.model, latency_s=round(response.latency_s, 2),
                   input_tokens=response.input_tokens, output_tokens=response.output_tokens)

    def finish(self, **summary) -> None:
        self.meta["duration_s"] = round(time.time() - self.meta["started"], 3)
        self.meta.update(summary)
        self.write("meta.json", json.dumps(self.meta, ensure_ascii=False, indent=2, default=str))


def read_run(runs_dir: Path, run_id: str) -> dict:
    """Full journal entry (meta + LLM exchanges) for the journal page / JSON export."""
    if not re.fullmatch(r"[\w\-]+", run_id):
        raise FileNotFoundError(run_id)
    d = runs_dir / run_id
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    calls = []
    if (d / "llm.jsonl").exists():
        for line in (d / "llm.jsonl").read_text("utf-8").splitlines():
            if line.strip():
                calls.append(json.loads(line))
    files = sorted(p.name for p in d.iterdir() if p.is_file())
    return {"meta": meta, "llm_calls": calls, "files": files}
