"""Orchestration: text -> JSON plan -> Python code -> graph -> checks -> layout -> BPMN XML.

LLM is used only for the plan and the code. Everything after the sandbox is
deterministic. Failures at any deterministic stage are fed back to the LLM
(repair loop, at most `max_repairs` rounds); if the loop is exhausted, the plan
is compiled deterministically, and as a last resort a lenient build is returned
with warnings so the analyst always gets an editable diagram.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .bpmn.layout import layout
from .bpmn.serializer import to_xml
from .bpmn.validator import Issue, errors_of, format_issues_for_llm, normalize, validate
from .bpmn.xsd import validate_xsd
from .llm import prompts
from .llm.client import LLMClient, LLMError
from .llm.plan import Plan, PlanError, compile_plan, parse_plan
from .runlog import RunLog
from .sandbox import SandboxError, run_code

MAX_PLAN_REPAIRS = 2


@dataclass
class BuildResult:
    ok: bool
    code: str
    xml: str | None = None
    issues: list[Issue] = field(default_factory=list)
    xsd_errors: list[str] = field(default_factory=list)
    error: str | None = None          # sandbox error
    stats: dict = field(default_factory=dict)

    def errors_for_llm(self) -> str:
        if self.error:
            return self.error
        parts = [format_issues_for_llm(self.issues)]
        if self.xsd_errors:
            parts.append("Ошибки XSD:\n" + "\n".join(self.xsd_errors[:10]))
        return "\n".join(p for p in parts if p)


def build(code: str, title: str = "Процесс", lenient: bool = False) -> BuildResult:
    """Deterministic part of the pipeline for one piece of code."""
    try:
        res = run_code(code, title)
    except SandboxError as e:
        return BuildResult(False, code, error=e.render())
    d = res.diagram
    issues = normalize(d, lenient=lenient)
    issues += validate(d)
    errs = errors_of(issues)
    if errs and not lenient:
        return BuildResult(False, code, issues=issues, stats=d.stats())
    xml = to_xml(d, layout(d))
    xsd_errors = validate_xsd(xml)
    return BuildResult(not errs and not xsd_errors, code, xml, issues, xsd_errors, stats=d.stats())


@dataclass
class PipelineResult:
    ok: bool
    xml: str | None
    code: str
    plan: dict | None
    issues: list[dict]
    xsd_errors: list[str]
    attempts: list[dict]
    stats: dict
    assumptions: list[str]
    questions: list[str]
    run_id: str
    duration_s: float
    source: str                       # llm | llm_repaired | plan_compiler | lenient
    message: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _strip_fences(text: str) -> str:
    t = text.strip()
    if "```" in t:
        start = t.find("```")
        nl = t.find("\n", start)
        end = t.find("```", nl + 1)
        if nl != -1:
            t = t[nl + 1:end if end != -1 else None]
    return t.strip() + "\n"


class Pipeline:
    def __init__(self, llm: LLMClient | None, runs_dir=None, max_repairs: int = 3):
        self.llm = llm
        self.runs_dir = runs_dir
        self.max_repairs = max_repairs
        self.llm_error: str | None = None     # why the LLM client could not be created

    # ------------------------------------------------------------------ LLM stages
    def _ask(self, log: RunLog, stage: str, system: str, messages: list[dict], json_mode=False) -> str:
        if self.llm is None:
            raise LLMError("LLM не настроен: " + (self.llm_error or "задайте LLM_PROVIDER/LLM_API_KEY в .env"))
        resp = self.llm.complete(system, messages, json_mode=json_mode)
        log.llm(stage, system, messages, resp)
        return resp.text

    def plan(self, text: str, log: RunLog, attempts: list[dict]) -> Plan:
        messages = [{"role": "user", "content": prompts.PLANNER_USER.format(text=text)}]
        last_err = None
        for i in range(MAX_PLAN_REPAIRS + 1):
            answer = self._ask(log, "plan" if i == 0 else f"plan_repair_{i}", prompts.PLANNER_SYSTEM,
                               messages, json_mode=True)
            try:
                plan = parse_plan(answer)
                log.write("plan.json", plan.model_dump_json(indent=2, by_alias=True))
                attempts.append({"stage": "plan", "ok": True})
                return plan
            except PlanError as e:
                last_err = str(e)
                attempts.append({"stage": "plan", "ok": False, "error": last_err})
                messages += [{"role": "assistant", "content": answer},
                             {"role": "user", "content": prompts.PLANNER_REPAIR.format(errors=last_err)}]
        raise PlanError(last_err or "Не удалось построить план")

    def _code_loop(self, log: RunLog, system: str, messages: list[dict], title: str,
                   attempts: list[dict]) -> tuple[BuildResult, int]:
        result = None
        for i in range(self.max_repairs + 1):
            stage = "codegen" if i == 0 else f"repair_{i}"
            code = _strip_fences(self._ask(log, stage, system, messages))
            log.write(f"code_{i}.py", code)
            result = build(code, title)
            attempts.append({"stage": stage, "ok": result.ok, "code": code,
                             "error": None if result.ok else result.errors_for_llm()})
            if result.ok:
                return result, i
            log.write(f"errors_{i}.txt", result.errors_for_llm())
            # continue the dialogue so the model sees its own code and the errors
            messages = messages + [
                {"role": "assistant", "content": code},
                {"role": "user", "content": prompts.REPAIR_USER.format(code=code, errors=result.errors_for_llm())},
            ]
        return result, self.max_repairs

    # ------------------------------------------------------------------ public entry points
    def prepare(self, text: str) -> dict:
        """One planning pass, exposed for analyst review before diagram construction."""
        log = RunLog(self.runs_dir, "interview")
        log.write("input.txt", text)
        attempts = []
        try:
            plan = self.plan(text, log, attempts)
            self._ground_quotes(plan, text)
            log.write("plan.json", plan.model_dump_json(indent=2, by_alias=True))
            log.finish(ok=True)
            return {"ok": True, "plan": plan.model_dump(by_alias=True), "run_id": log.id}
        except (LLMError, PlanError) as e:
            log.finish(ok=False, error=str(e))
            return {"ok": False, "message": str(e), "run_id": log.id}

    @staticmethod
    def _ground_quotes(plan: Plan, text: str):
        for element in plan.elements:
            if element.source_quote and element.source_quote not in text:
                element.source_quote = ""
                element.assumption = (element.assumption + " Основание в исходном тексте не подтверждено.").strip()

    def from_plan(self, plan: Plan, text: str = "") -> PipelineResult:
        log = RunLog(self.runs_dir, "reviewed_plan")
        t0 = time.time()
        self._ground_quotes(plan, text)
        log.write("input.txt", text)
        log.write("plan.json", plan.model_dump_json(indent=2, by_alias=True))
        result = build(compile_plan(plan), plan.title)
        attempts = [{"stage": "plan_compiler", "ok": result.ok,
                     "error": None if result.ok else result.errors_for_llm()}]
        source = "reviewed_plan"
        if not result.ok:
            result = build(result.code, plan.title, lenient=True)
            source = "lenient"
        return self._finish(log, t0, result, plan, attempts, source)

    def generate(self, text: str, mode: str = "two_stage") -> PipelineResult:
        log = RunLog(self.runs_dir, "generate")
        log.write("input.txt", text)
        t0 = time.time()
        attempts: list[dict] = []
        plan: Plan | None = None
        title = "Процесс"
        try:
            if mode == "two_stage":
                plan = self.plan(text, log, attempts)
                self._ground_quotes(plan, text)
                title = plan.title
                plan_json = plan.model_dump_json(indent=1, by_alias=True, exclude_none=True)
                system = prompts.CODEGEN_SYSTEM
                messages = [{"role": "user", "content": prompts.CODEGEN_USER.format(plan=plan_json)}]
            else:
                system = prompts.DIRECT_SYSTEM
                messages = [{"role": "user", "content": prompts.PLANNER_USER.format(text=text)}]
            result, repairs = self._code_loop(log, system, messages, title, attempts)
            source = "llm" if repairs == 0 else "llm_repaired"
        except (LLMError, PlanError) as e:
            log.finish(ok=False, error=str(e))
            return PipelineResult(False, None, "", plan.model_dump(by_alias=True) if plan else None, [], [],
                                  attempts, {}, [], [], log.id, round(time.time() - t0, 2), "error", str(e))

        if not result.ok and plan is not None:
            compiled = compile_plan(plan)
            fb = build(compiled, title)
            attempts.append({"stage": "plan_compiler", "ok": fb.ok, "code": compiled,
                             "error": None if fb.ok else fb.errors_for_llm()})
            if fb.ok:
                result, source = fb, "plan_compiler"
        if not result.ok:
            lenient = build(result.code, title, lenient=True)
            if lenient.xml:
                result, source = lenient, "lenient"
        return self._finish(log, t0, result, plan, attempts, source)

    def refine(self, text: str, code: str, instruction: str) -> PipelineResult:
        log = RunLog(self.runs_dir, "refine")
        log.write("input.txt", text)
        log.write("instruction.txt", instruction)
        log.write("code_before.py", code)
        t0 = time.time()
        attempts: list[dict] = []
        messages = [{"role": "user", "content": prompts.REFINE_USER.format(
            text=text or "(нет)", code=code, instruction=instruction)}]
        try:
            result, repairs = self._code_loop(log, prompts.REFINE_SYSTEM, messages, "Процесс", attempts)
            source = "llm" if repairs == 0 else "llm_repaired"
        except LLMError as e:
            log.finish(ok=False, error=str(e))
            return PipelineResult(False, None, code, None, [], [], attempts, {}, [], [], log.id,
                                  round(time.time() - t0, 2), "error", str(e))
        if not result.ok:
            lenient = build(result.code, "Процесс", lenient=True)
            if lenient.xml:
                result, source = lenient, "lenient"
        res = self._finish(log, t0, result, None, attempts, source)
        first = res.code.lstrip().splitlines()[0] if res.code.strip() else ""
        if first.startswith("# Изменения:"):
            res.summary = first[len("# Изменения:"):].strip()
        return res

    def from_code(self, code: str, kind: str = "code") -> PipelineResult:
        log = RunLog(self.runs_dir, kind)
        t0 = time.time()
        result = build(code)
        source = "manual"
        if not result.ok:
            lenient = build(code, lenient=True)
            if lenient.xml:
                result, source = lenient, "lenient"
        return self._finish(log, t0, result, None, [{"stage": kind, "ok": result.ok}], source)

    def _finish(self, log, t0, result: BuildResult, plan: Plan | None, attempts, source) -> PipelineResult:
        if result.xml:
            log.write("result.bpmn", result.xml)
        log.write("final_code.py", result.code)
        res = PipelineResult(
            ok=result.xml is not None and not result.xsd_errors,
            xml=result.xml,
            code=result.code,
            plan=json.loads(plan.model_dump_json(by_alias=True)) if plan else None,
            issues=[i.to_dict() for i in result.issues],
            xsd_errors=result.xsd_errors,
            attempts=attempts,
            stats=result.stats,
            assumptions=plan.assumptions if plan else [],
            questions=plan.questions if plan else [],
            run_id=log.id,
            duration_s=round(time.time() - t0, 2),
            source=source,
            message="" if result.xml else (result.errors_for_llm() or "Не удалось построить диаграмму"),
        )
        log.finish(ok=res.ok, source=source, stats=result.stats,
                   repairs=sum(1 for a in attempts if a["stage"].startswith("repair")),
                   errors=[i.to_dict() for i in result.issues if i.level == "error"],
                   xsd_errors=result.xsd_errors)
        return res
