"""FastAPI backend + static web UI."""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .bpmn.importer import ImportErrorBPMN, bpmn_to_code
from .bpmn.xsd import validate_xsd
from .config import ROOT, find_env_file, get_settings
from .llm.client import make_client
from .pipeline import Pipeline

FRONTEND = ROOT / "frontend"
EXAMPLES = ROOT / "examples"

app = FastAPI(title="BPMN Agent", version="1.0")


_state: dict = {"key": None, "pipeline": None, "error": None, "settings": None}


def _env_key():
    path = find_env_file()
    return (str(path), path.stat().st_mtime) if path else None


def _pipeline() -> Pipeline:
    """Built once and rebuilt automatically when the .env file changes (no restart needed)."""
    key = _env_key()
    if _state["pipeline"] is not None and _state["key"] == key:
        return _state["pipeline"]
    s = get_settings()
    error = None
    try:
        llm = make_client(s)
        if hasattr(llm, "verbose"):
            llm.verbose = True        # log every Gemini attempt / fallback to the server console
        print(f"[bpmn-agent] .env: {s.env_file or 'не найден'} · LLM: {s.llm_provider} · {getattr(llm, 'model', '')}")
    except Exception as e:  # noqa: BLE001 - missing key / package: UI still works without LLM
        llm = None
        where = f"файл настроек: {s.env_file}" if s.env_file else (
            f"файл .env не найден — создайте его в {ROOT} (copy .env.example .env)")
        error = f"{e} [LLM_PROVIDER={s.llm_provider}; {where}]"
        print(f"[bpmn-agent] LLM недоступен: {error}")
    p = Pipeline(llm, s.runs_dir, s.max_repairs)
    p.llm_error = error
    _state.update(key=key, pipeline=p, error=error, settings=s)
    return p


class GenerateRequest(BaseModel):
    text: str = Field(min_length=10, max_length=20000)
    mode: str = "two_stage"          # two_stage | direct


class RefineRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=4000)
    code: str = ""
    xml: str | None = None           # current (possibly hand-edited) diagram
    text: str = ""


class CodeRequest(BaseModel):
    code: str


class XmlRequest(BaseModel):
    xml: str


@app.get("/api/health")
def health():
    p = _pipeline()
    s = _state["settings"]
    return {"ok": True, "llm": p.llm is not None, "provider": s.llm_provider, "llm_error": _state["error"],
            "env_file": s.env_file,
            "model": getattr(p.llm, "model", None), "max_repairs": s.max_repairs}


@app.get("/api/examples")
def examples():
    out = []
    for d in sorted(EXAMPLES.glob("*/")):
        f = d / "input.txt"
        if not f.exists():
            continue
        meta = json.loads((d / "meta.json").read_text("utf-8")) if (d / "meta.json").exists() else {}
        out.append({"id": d.name, "title": meta.get("title", d.name), "text": f.read_text("utf-8"),
                    "has_result": (d / "result.bpmn").exists()})
    return out


@app.get("/api/examples/{ex_id}")
def example_result(ex_id: str):
    d = EXAMPLES / ex_id
    if not (d / "result.bpmn").exists() or "/" in ex_id or ".." in ex_id:
        raise HTTPException(404, "Нет сохранённого результата")
    read = lambda n: (d / n).read_text("utf-8") if (d / n).exists() else None  # noqa: E731
    plan = read("plan.json")
    return {"xml": read("result.bpmn"), "code": read("code.py") or "", "plan": json.loads(plan) if plan else None,
            "text": read("input.txt"), "report": json.loads(read("report.json") or "{}")}


@app.post("/api/generate")
def generate(req: GenerateRequest):
    res = _pipeline().generate(req.text, req.mode)
    return res.to_dict()


@app.post("/api/refine")
def refine(req: RefineRequest):
    code = req.code
    if req.xml:
        try:
            code = bpmn_to_code(req.xml)   # keep the analyst's manual edits
        except ImportErrorBPMN as e:
            raise HTTPException(400, str(e))
    if not code.strip():
        raise HTTPException(400, "Нет текущей диаграммы для изменения")
    return _pipeline().refine(req.text, code, req.instruction).to_dict()


@app.post("/api/build")
def build_code(req: CodeRequest):
    return _pipeline().from_code(req.code, "manual_code").to_dict()


@app.post("/api/import")
def import_bpmn(req: XmlRequest):
    try:
        code = bpmn_to_code(req.xml)
    except ImportErrorBPMN as e:
        raise HTTPException(400, str(e))
    return _pipeline().from_code(code, "import").to_dict()


@app.post("/api/validate")
def validate(req: XmlRequest):
    errs = validate_xsd(req.xml)
    return {"xsd_valid": not errs, "xsd_errors": errs}


@app.get("/api/runs")
def runs(limit: int = 30):
    s = get_settings()
    out = []
    if s.runs_dir.exists():
        for d in sorted(s.runs_dir.iterdir(), reverse=True)[:limit]:
            m = d / "meta.json"
            if m.exists():
                meta = json.loads(m.read_text("utf-8"))
                meta.pop("events", None)
                out.append(meta)
    return out


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
