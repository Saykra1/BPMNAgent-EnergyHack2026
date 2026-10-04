"""HTTP API of process runs. State lives in <runs_dir>/_instances/<id>/: state.json, process.bpmn,
attachments/. Every request loads the state, applies one action under a per-run lock and saves it."""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from lxml import etree
from pydantic import BaseModel, Field

from ..bpmn.importer import ImportErrorBPMN
from ..ir.from_diagram import xml_to_plan
from ..llm.plan import PlanError
from ..sandbox import SandboxError
from .engine import ProcessRun, RunError
from .script import ScriptError, check_syntax, eval_check, render_template, run_script

router = APIRouter(prefix="/api/run")
_settings: Callable = None
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
MAX_FILE = 10 * 1024 * 1024
ID_RE = re.compile(r"^[0-9a-f]{12}$")


def bind(settings_factory: Callable) -> APIRouter:
    global _settings
    _settings = settings_factory
    return router


def _dir(run_id: str) -> Path:
    if not ID_RE.match(run_id or ""):
        raise HTTPException(404, "Запуск не найден")
    return Path(_settings().runs_dir) / "_instances" / run_id


def _lock(run_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(run_id, threading.Lock())


def _plan(xml: str):
    try:
        return xml_to_plan(xml)
    except (PlanError, ImportErrorBPMN, SandboxError, etree.XMLSyntaxError, ValueError) as e:
        raise HTTPException(400, f"Не удалось прочитать процесс: {e}")


def _load(run_id: str) -> tuple[ProcessRun, Path]:
    d = _dir(run_id)
    if not (d / "state.json").exists():
        raise HTTPException(404, "Запуск не найден")
    state = json.loads((d / "state.json").read_text("utf-8"))
    return ProcessRun(state, _plan((d / "process.bpmn").read_text("utf-8"))), d


def _save(run: ProcessRun, d: Path):
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(run.s, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(d / "state.json")


class StartRequest(BaseModel):
    xml: str = Field(max_length=5_000_000)
    variables: dict = Field(default_factory=dict)


class CompleteRequest(BaseModel):
    token: str
    text: str = Field(default="", max_length=50_000)
    values: dict = Field(default_factory=dict)
    author: str = Field(default="", max_length=200)
    file_name: str | None = Field(default=None, max_length=200)
    file_base64: str | None = None


class ChooseRequest(BaseModel):
    token: str
    options: list[int]


class RetryRequest(BaseModel):
    xml: str | None = Field(default=None, max_length=5_000_000)


class CodeRequest(BaseModel):
    code: str = Field(default="", max_length=20_000)
    mode: str = "exec"             # exec | check | template
    variables: dict = Field(default_factory=dict)


@router.post("/start")
def start(req: StartRequest):
    if len(json.dumps(req.variables, ensure_ascii=False)) > 200_000:
        raise HTTPException(400, "Слишком большие исходные данные")
    plan = _plan(req.xml)
    run_id = uuid.uuid4().hex[:12]
    run = ProcessRun.new(run_id, plan, req.variables)
    d = _dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "process.bpmn").write_text(req.xml, "utf-8")
    with _lock(run_id):
        run.advance()
        _save(run, d)
    return run.public()


@router.get("/{run_id}")
def get(run_id: str):
    run, _ = _load(run_id)
    return run.public()


@router.post("/{run_id}/complete")
def complete(run_id: str, req: CompleteRequest):
    with _lock(run_id):
        run, d = _load(run_id)
        attachment = None
        if req.file_base64:
            try:
                data = base64.b64decode(req.file_base64, validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(400, "Файл повреждён")
            if len(data) > MAX_FILE:
                raise HTTPException(400, "Файл больше 10 МБ")
            name = Path(req.file_name or "file").name
            safe = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE)[:120] or "file"
            n = len(run.s["documents"]) + 1
            stored = f"{n:02d}_{safe}"
            attachment = {"name": name, "stored": stored, "size": len(data)}
        try:
            run.complete(req.token, req.text, req.values, req.author.strip(), attachment)
        except RunError as e:
            raise HTTPException(400, str(e))
        if attachment:
            (d / "attachments").mkdir(exist_ok=True)
            (d / "attachments" / attachment["stored"]).write_bytes(data)
        _save(run, d)
    return run.public()


@router.post("/{run_id}/choose")
def choose(run_id: str, req: ChooseRequest):
    with _lock(run_id):
        run, d = _load(run_id)
        try:
            run.choose(req.token, req.options)
        except RunError as e:
            raise HTTPException(400, str(e))
        _save(run, d)
    return run.public()


@router.post("/{run_id}/retry")
def retry(run_id: str, req: RetryRequest):
    """Re-run the failed step; with `xml` the corrected diagram (code, checks) replaces the old one."""
    with _lock(run_id):
        run, d = _load(run_id)
        if req.xml:
            plan = _plan(req.xml)
            err = run.s.get("error") or {}
            if err.get("node") and err["node"] not in {e.id for e in plan.elements}:
                raise HTTPException(400, "В исправленной схеме нет шага, на котором произошла ошибка")
            (d / "process.bpmn").write_text(req.xml, "utf-8")
            run = ProcessRun(run.s, plan)
        try:
            run.retry()
        except RunError as e:
            raise HTTPException(400, str(e))
        _save(run, d)
    return run.public()


def _doc(run: ProcessRun, n: int) -> dict:
    for doc in run.s["documents"]:
        if doc["n"] == n:
            return doc
    raise HTTPException(404, "Документ не найден")


def _download(data: bytes, filename: str, media: str) -> Response:
    from urllib.parse import quote
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename=\"{_ascii(filename)}\"; "
                                                    f"filename*=UTF-8''{quote(filename)}"})


def _ascii(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem if dot else name).strip("_") or "file"
    return f"{safe}.{ext}" if dot and re.fullmatch(r"[A-Za-z0-9]{1,8}", ext) else safe


@router.get("/{run_id}/documents/{n}")
def document(run_id: str, n: int):
    run, _ = _load(run_id)
    doc = _doc(run, n)
    return _download(run.document_docx(doc), doc["file"],
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/{run_id}/attachments/{n}")
def attachment(run_id: str, n: int):
    run, d = _load(run_id)
    doc = _doc(run, n)
    if not doc.get("attachment"):
        raise HTTPException(404, "У документа нет приложения")
    path = d / "attachments" / doc["attachment"]["stored"]
    if not path.is_file():
        raise HTTPException(404, "Файл приложения не найден")
    return _download(path.read_bytes(), doc["attachment"]["name"], "application/octet-stream")


@router.get("/{run_id}/archive")
def archive(run_id: str):
    """All documents, attachments, the journal, final data and the diagram in one zip."""
    run, d = _load(run_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("журнал.md", run.journal_markdown())
        z.writestr("данные.json", json.dumps(run.s["variables"], ensure_ascii=False, indent=2))
        z.writestr("процесс.bpmn", (d / "process.bpmn").read_text("utf-8"))
        for doc in run.s["documents"]:
            z.writestr(f"документы/{doc['file']}", run.document_docx(doc))
            att = doc.get("attachment")
            if att and (d / "attachments" / att["stored"]).is_file():
                z.write(d / "attachments" / att["stored"], f"приложения/{att['stored']}")
    return _download(buf.getvalue(), f"запуск_{run_id}.zip", "application/zip")


@router.post("/code")
def try_code(req: CodeRequest):
    """Editor helper: check block code / a branch check / a template on sample data."""
    if req.mode == "template":
        return {"ok": True, "text": render_template(req.code, req.variables)}
    msg = check_syntax(req.code, "check" if req.mode == "check" else "exec")
    if msg:
        return {"ok": False, "error": msg}
    try:
        if req.mode == "check":
            return {"ok": True, "result": eval_check(req.code, req.variables)}
        variables, logs = run_script(req.code, req.variables)
    except ScriptError as e:
        return {"ok": False, "error": str(e)}
    changed = {k: v for k, v in variables.items() if req.variables.get(k, object()) != v}
    return {"ok": True, "variables": variables, "changed": changed, "logs": logs}
