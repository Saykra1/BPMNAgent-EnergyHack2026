"""HTTP endpoints of the analyst toolkit: linter, regulation, document import, PII, analytics,
simulation, RACI, test paths, templates, exports, AI journal. All tools work on the current diagram
(BPMN XML from the canvas, so manual edits are respected) or on an IR passed explicitly."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from lxml import etree
from pydantic import BaseModel, Field

from . import pii
from .bpmn.camunda import to_camunda8
from .bpmn.importer import ImportErrorBPMN
from .docimport import DocumentError, extract_base64
from .ir import analytics as an
from .ir.diff import describe, diff_plans
from .ir.from_diagram import xml_to_plan
from .ir.lint import autofix, lint, questions, summary
from .ir.merge import split_text
from .ir.sop import build_sop, to_docx, to_markdown
from .llm.plan import Plan, PlanError, ir_json_schema, parse_plan
from .runlog import read_run
from .sandbox import SandboxError

TEMPLATES = Path(__file__).parent / "templates"
router = APIRouter(prefix="/api")
_pipeline: Callable = None          # injected by main
_settings: Callable = None


def bind(pipeline_factory: Callable, settings_factory: Callable) -> APIRouter:
    global _pipeline, _settings
    _pipeline, _settings = pipeline_factory, settings_factory
    return router


class Source(BaseModel):
    xml: str | None = None
    plan: dict | None = None
    text: str = Field(default="", max_length=200_000)


def plan_of(src: Source) -> Plan:
    try:
        if src.plan:
            return parse_plan(src.plan)
        if src.xml:
            return xml_to_plan(src.xml)
    except (PlanError, ImportErrorBPMN, SandboxError, etree.XMLSyntaxError, ValueError) as e:
        raise HTTPException(400, f"Не удалось прочитать процесс: {e}")
    raise HTTPException(400, "Нет процесса: постройте или откройте диаграмму")


def _file(content: bytes | str, filename: str, media: str) -> Response:
    from urllib.parse import quote
    data = content.encode("utf-8") if isinstance(content, str) else content
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


def _slug(title: str) -> str:
    import re
    return re.sub(r"[^\w]+", "_", title, flags=re.UNICODE).strip("_")[:60] or "process"


# ------------------------------------------------------------------------------- IR
@router.get("/ir-schema")
def ir_schema():
    return ir_json_schema()


@router.post("/ir")
def ir(src: Source, download: bool = False):
    plan = plan_of(src)
    data = plan.model_dump(by_alias=True, exclude_defaults=True)
    if download:
        return _file(json.dumps(data, ensure_ascii=False, indent=2), _slug(plan.title) + ".ir.json",
                     "application/json")
    return {"plan": data}


# ------------------------------------------------------------------------------- linter
@router.post("/lint")
def lint_api(src: Source):
    plan = plan_of(src)
    issues = lint(plan)
    return {"lint": [i.to_dict() for i in issues], "summary": summary(issues),
            "clarifications": questions(plan, issues), "plan": plan.model_dump(by_alias=True)}


class FixRequest(Source):
    codes: list[str] | None = None
    elements: list[str] | None = None


@router.post("/autofix")
def autofix_api(req: FixRequest):
    before = plan_of(req)
    try:
        after, applied = autofix(before, set(req.codes) if req.codes else None,
                                 set(req.elements) if req.elements else None)
    except PlanError as e:
        raise HTTPException(400, str(e))
    if not applied:
        return {"applied": [], "message": "Автоматически исправить нечего."}
    res = _pipeline().from_plan(after, req.text).to_dict()
    res["fixes"] = applied
    res["diff"] = diff_plans(before, after)
    res["summary"] = "Исправлено: " + "; ".join(applied)
    return res


# ------------------------------------------------------------------------------- regulation, explanation
class SopRequest(Source):
    format: str = "md"          # md | docx


@router.post("/sop")
def sop(req: SopRequest):
    plan = plan_of(req)
    blocks = build_sop(plan)
    if req.format == "docx":
        return _file(to_docx(blocks), _slug(plan.title) + "_регламент.docx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    md = to_markdown(blocks)
    if req.format == "md_file":
        return _file(md, _slug(plan.title) + "_регламент.md", "text/markdown")
    return {"markdown": md, "title": plan.title}


class ExplainRequest(Source):
    use_llm: bool = True


@router.post("/explain")
def explain(req: ExplainRequest):
    return _pipeline().explain(plan_of(req), req.use_llm)


# ------------------------------------------------------------------------------- documents, PII
class UploadRequest(BaseModel):
    filename: str = Field(max_length=300)
    content_base64: str = Field(max_length=25_000_000)


@router.post("/upload")
def upload(req: UploadRequest):
    try:
        info = extract_base64(req.filename, req.content_base64)
    except DocumentError as e:
        raise HTTPException(400, str(e))
    from .pipeline import CHUNK_CHARS
    info["parts"] = len(split_text(info["text"], CHUNK_CHARS))
    return info


class TextRequest(BaseModel):
    text: str = Field(default="", max_length=200_000)


@router.post("/pii")
def pii_preview(req: TextRequest):
    s = _settings()
    masked, items = pii.mask(req.text, addresses=s.pii_mask_addresses, objects=s.pii_mask_objects)
    return {"enabled": s.pii_mask, "masked_text": masked, "items": [m.to_dict() for m in items],
            "onprem_only": s.llm_onprem_only}


# ------------------------------------------------------------------------------- analytics
class AnalyticsRequest(Source):
    runs: int = Field(default=1000, ge=50, le=20000)
    seed: int = 7
    metric: str = "total"       # total | work | wait


@router.post("/analytics")
def analytics(req: AnalyticsRequest):
    plan = plan_of(req)
    result = an.analyze(plan, req.runs, req.seed, req.metric if req.metric in ("total", "work", "wait") else "total")
    result["plan"] = plan.model_dump(by_alias=True)
    return result


class CompareRequest(BaseModel):
    as_is: Source
    to_be: Source
    runs: int = Field(default=1000, ge=50, le=20000)


@router.post("/compare")
def compare(req: CompareRequest):
    return an.compare(plan_of(req.as_is), plan_of(req.to_be), req.runs)


@router.post("/estimate")
def estimate(src: Source):
    """Durations for steps without them (LLM, marked as estimates) → rebuilt diagram."""
    plan = plan_of(src)
    est = _pipeline().estimate(plan)
    res = _pipeline().from_plan(est["plan"], src.text).to_dict()
    res.update({"estimated": est["estimated"], "estimate_source": est["source"], "comments": est["comments"],
                "summary": f"Оценены длительности {len(est['estimated'])} шагов "
                           f"({'моделью' if est['source'] == 'llm' else 'типовыми значениями'})"})
    return res


@router.post("/recommend")
def recommend(src: Source):
    plan = plan_of(src)
    return _pipeline().recommend(plan, an.analyze(plan, 500), lint(plan))


class PathsRequest(Source):
    limit: int = Field(default=40, ge=1, le=200)
    format: str = "json"        # json | csv | md


@router.post("/paths")
def paths(req: PathsRequest):
    plan = plan_of(req)
    result = an.enumerate_paths(plan, req.limit)
    if req.format == "csv":
        return _file("﻿" + an.paths_csv(result), _slug(plan.title) + "_тест-сценарии.csv", "text/csv")
    if req.format == "md":
        return _file(an.paths_markdown(result, plan.title), _slug(plan.title) + "_тест-сценарии.md", "text/markdown")
    return result


class RaciRequest(Source):
    format: str = "json"        # json | csv | xlsx


@router.post("/raci")
def raci(req: RaciRequest):
    plan = plan_of(req)
    m = an.raci(plan)
    if req.format == "csv":
        return _file("﻿" + an.raci_csv(m), _slug(plan.title) + "_RACI.csv", "text/csv")
    if req.format == "xlsx":
        return _file(an.raci_xlsx(m, plan.title), _slug(plan.title) + "_RACI.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return m


# ------------------------------------------------------------------------------- templates
def _template(tid: str) -> dict:
    f = TEMPLATES / f"{tid}.json"
    if not f.is_file() or "/" in tid or ".." in tid:
        raise HTTPException(404, "Нет такого шаблона")
    return json.loads(f.read_text("utf-8"))


@router.get("/templates")
def templates():
    out = []
    for f in sorted(TEMPLATES.glob("*.json")):
        meta = json.loads(f.read_text("utf-8")).get("template", {})
        out.append({"id": f.stem, "title": meta.get("title", f.stem), "description": meta.get("description", "")})
    return out


@router.get("/templates/{tid}")
def template(tid: str):
    data = _template(tid)
    data.pop("template", None)
    plan = parse_plan(data)
    res = _pipeline().from_plan(plan).to_dict()
    res["summary"] = f"Шаблон «{plan.title}» открыт — его можно править словами или на холсте."
    return res


# ------------------------------------------------------------------------------- exports
class ExportRequest(Source):
    format: str = "camunda"     # camunda | ir


@router.post("/export")
def export(req: ExportRequest):
    if req.format == "ir":
        return ir(req, download=True)
    if req.format == "camunda":
        if not req.xml:
            raise HTTPException(400, "Нужен BPMN XML текущей схемы")
        try:
            xml, notes = to_camunda8(req.xml)
        except etree.XMLSyntaxError as e:
            raise HTTPException(400, str(e))
        title = "process"
        try:
            title = xml_to_plan(req.xml).title
        except Exception:  # noqa: BLE001
            pass
        resp = _file(xml, _slug(title) + "_camunda8.bpmn", "application/xml")
        resp.headers["X-Notes"] = json.dumps(notes, ensure_ascii=True)
        return resp
    raise HTTPException(400, "Формат: camunda | ir")


@router.post("/diff")
def diff(req: CompareRequest):
    d = diff_plans(plan_of(req.as_is), plan_of(req.to_be))
    d["summary"] = describe(d)
    return d


# ------------------------------------------------------------------------------- AI journal
@router.get("/runs/{run_id}")
def run_detail(run_id: str):
    try:
        return read_run(_settings().runs_dir, run_id)
    except (FileNotFoundError, json.JSONDecodeError):
        raise HTTPException(404, "Запись журнала не найдена")


@router.get("/runs/{run_id}/export")
def run_export(run_id: str):
    data = run_detail(run_id)
    return _file(json.dumps(data, ensure_ascii=False, indent=2), f"run_{run_id}.json", "application/json")
