"""Per-run journal: every request, LLM exchange, generated code and produced file.

runs/<timestamp>_<id>/
  input.txt, plan.json, code_<n>.py, errors_<n>.txt, result.bpmn, llm.jsonl, meta.json
The eval bench and the analyst can replay or inspect any run.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from .privacy_client import active_guard


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
        self.meta: dict = {"id": self.id, "kind": kind, "started": time.time(), "events": []}

    def write(self, name: str, content: str) -> None:
        if self.dir:
            try:
                (self.dir / name).write_text(content, encoding="utf-8")
            except OSError as exc:
                print(f"[bpmn-agent] Не удалось сохранить журнал: {exc}")
                self.dir = None

    def event(self, stage: str, **data) -> None:
        self.meta["events"].append({"t": round(time.time() - self.meta["started"], 3), "stage": stage, **data})

    def llm(self, stage: str, system: str, messages: list[dict], response) -> None:
        if self.dir:
            guard = active_guard()
            rec = {"stage": stage, "model": response.model, "latency_s": round(response.latency_s, 2),
                   "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                   "messages": [{**m, "content": guard.mask(m["content"])} for m in messages] if guard else messages,
                   "response": guard.mask(response.text) if guard else response.text}
            try:
                with (self.dir / "llm.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError as exc:
                print(f"[bpmn-agent] Не удалось сохранить обмен с моделью: {exc}")
                self.dir = None
        self.event(stage, latency_s=round(response.latency_s, 2), output_tokens=response.output_tokens)

    def finish(self, **summary) -> None:
        self.meta["duration_s"] = round(time.time() - self.meta["started"], 3)
        self.meta.update(summary)
        self.write("meta.json", json.dumps(self.meta, ensure_ascii=False, indent=2, default=str))
