"""FastAPI backend + static web UI."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, Field

from .bpmn.importer import ImportErrorBPMN, bpmn_to_code
from .bpmn.xsd import validate_xsd
from .config import ROOT, find_env_file, get_settings
from .llm.client import make_client
from .pipeline import Pipeline
from .llm.plan import parse_plan, PlanError
from .sandbox import SandboxError
from .insights import inspect_xml
from .jev import JevReviewError, review_source_links, review_audit_items
from .privacy import clean_text, ner_available, preview
from .privacy_client import ProtectedClient, protected_request
from .collab import routes as collab
from .conflict_review import review as review_conflicts
from lxml import etree

FRONTEND = ROOT / "frontend"
EXAMPLES = ROOT / "examples"
CleanText = Annotated[str, AfterValidator(lambda value: clean_text(value)[0])]

app = FastAPI(title="BPMN Agent", version="1.0")
app.include_router(collab.router)
app.middleware("http")(collab.guard)


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
    p = Pipeline(ProtectedClient(llm) if llm else None, s.runs_dir, s.max_repairs)
    p.llm_error = error
    _state.update(key=key, pipeline=p, error=error, settings=s)
    return p


def _require_llm() -> Pipeline:
    pipeline = _pipeline()
    if pipeline.llm is None:
        raise HTTPException(503, "Генерация недоступна: " + (
            pipeline.llm_error or "задайте LLM_PROVIDER и LLM_API_KEY в файле .env"))
    return pipeline


class PrivacyOptions(BaseModel):
    hide: list[str] = Field(default_factory=list, max_length=100)
    show: list[str] = Field(default_factory=list, max_length=200)
    trusted: list[str] = Field(default_factory=list, max_length=100)


class PrivacyRequest(BaseModel):
    text: str = Field(default="", max_length=30000)
    privacy: PrivacyOptions = Field(default_factory=PrivacyOptions)


class ConflictRequest(BaseModel):
    text: CleanText = Field(min_length=10, max_length=20000)
    privacy: PrivacyOptions = Field(default_factory=PrivacyOptions)


class GenerateRequest(BaseModel):
    text: CleanText = Field(min_length=10, max_length=20000)
    mode: str = "two_stage"          # two_stage | direct
    privacy: PrivacyOptions = Field(default_factory=PrivacyOptions)


class RefineRequest(BaseModel):
    instruction: CleanText = Field(min_length=2, max_length=4000)
    code: str = ""
    xml: str | None = None           # current (possibly hand-edited) diagram
    text: CleanText = ""
    privacy: PrivacyOptions = Field(default_factory=PrivacyOptions)


class CodeRequest(BaseModel):
    code: str


class XmlRequest(BaseModel):
    xml: str


class PlanRequest(BaseModel):
    plan: dict
    text: str = Field(default="", max_length=30000)


class InspectRequest(XmlRequest):
    text: str = Field(default="", max_length=30000)
    privacy: PrivacyOptions = Field(default_factory=PrivacyOptions)


class AuditGap(BaseModel):
    id: str = Field(max_length=100)
    fragment: str = Field(max_length=1000)
    candidates: list[str] = Field(default_factory=list, max_length=4)


class AuditBranch(BaseModel):
    id: str = Field(max_length=100)
    excerpt: str = Field(max_length=1200)
    gateway: str = Field(max_length=180)
    condition: str = Field(max_length=180)
    destination: str = Field(max_length=180)


class JevAuditRequest(BaseModel):
    gaps: list[AuditGap] = Field(default_factory=list, max_length=12)
    branches: list[AuditBranch] = Field(default_factory=list, max_length=12)
    privacy: PrivacyOptions = Field(default_factory=PrivacyOptions)


@app.post("/api/privacy")
def privacy_preview(req: PrivacyRequest):
    text, invisible = clean_text(req.text)
    return {**preview(text, req.privacy.hide, req.privacy.show, req.privacy.trusted),
            "invisible": invisible, "clean_text": text}


@app.post("/api/conflicts")
def conflicts(req: ConflictRequest):
    try:
        return review_conflicts(req.text, _require_llm().llm, req.privacy.model_dump())
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/prepare")
def prepare(req: GenerateRequest):
    with protected_request(req.text, **req.privacy.model_dump()) as guard:
        result = _require_llm().prepare(req.text)
        result["privacy"] = guard.report()
        return result


@app.post("/api/from-plan")
def from_plan(req: PlanRequest):
    try:
        return _pipeline().from_plan(parse_plan(req.plan), req.text).to_dict()
    except (PlanError, KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/inspect")
def inspect(req: InspectRequest):
    try:
        return inspect_xml(req.xml, req.text)
    except (ImportErrorBPMN, SandboxError, etree.XMLSyntaxError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/jev-review")
def jev_review(req: InspectRequest):
    try:
        return review_source_links(req.xml, req.text, get_settings(), privacy=req.privacy.model_dump())
    except JevReviewError as e:
        raise HTTPException(503, str(e))
    except (ImportErrorBPMN, SandboxError, etree.XMLSyntaxError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/jev-audit")
def jev_audit(req: JevAuditRequest):
    try:
        return review_audit_items([item.model_dump() for item in req.gaps],
                                  [item.model_dump() for item in req.branches], get_settings(),
                                  privacy=req.privacy.model_dump())
    except JevReviewError as e:
        raise HTTPException(503, str(e))


@app.get("/api/health")
def health():
    p = _pipeline()
    s = _state["settings"]
    return {"ok": True, "llm": p.llm is not None, "provider": s.llm_provider, "llm_error": _state["error"],
            "env_file": s.env_file,
            "model": getattr(p.llm, "model", None), "max_repairs": s.max_repairs,
            "jev": s.jev_enabled and s.llm_base_url.rstrip("/") == "https://openrouter.ai/api/v1",
            "privacy": {"ner": ner_available()}}


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
    with protected_request(req.text, **req.privacy.model_dump()) as guard:
        res = _require_llm().generate(req.text, req.mode)
        return {**res.to_dict(), "privacy": guard.report()}


@app.post("/api/refine")
def refine(req: RefineRequest):
    pipeline = _require_llm()
    code = req.code
    if req.xml:
        try:
            code = bpmn_to_code(req.xml)   # keep the analyst's manual edits
        except ImportErrorBPMN as e:
            raise HTTPException(400, str(e))
    if not code.strip():
        raise HTTPException(400, "Нет текущей диаграммы для изменения")
    with protected_request(req.text, **req.privacy.model_dump(), instruction=req.instruction) as guard:
        result = pipeline.refine(req.text, code, req.instruction).to_dict()
        result["privacy"] = guard.report()
        return result


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


@app.get("/join/{token}")
def join_page(token: str):
    return FileResponse(FRONTEND / "index.html")
