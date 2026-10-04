"""Orchestration: text -> JSON plan -> Python code -> graph -> checks -> layout -> BPMN XML.

LLM is used only for the plan and the code. Everything after the sandbox is
deterministic. Failures at any deterministic stage are fed back to the LLM
(repair loop, at most `max_repairs` rounds); if the loop is exhausted, the plan
is compiled deterministically, and as a last resort a lenient build is returned
with warnings so the analyst always gets an editable diagram.
"""
from __future__ import annotations

import ast
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .bpmn.layout import layout
from .bpmn.serializer import to_xml
from .bpmn.validator import Issue, errors_of, format_issues_for_llm, normalize, validate
from .bpmn.xsd import validate_xsd
from .conflicts import add_footnotes, open_conflicts, planner_decisions
from .llm import prompts
from .llm.client import LLMClient, LLMError
from .llm.plan import ConflictSpec, Plan, PlanError, compile_plan, extract_json, parse_plan
from .privacy import PrivacyGuard
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
    source: str                       # llm | llm_repaired | plan_compiler | lenient | conflict
    message: str = ""
    summary: str = ""
    conflicts: list[dict] = field(default_factory=list)     # undecided pairs: the diagram is not built
    resolutions: list[dict] = field(default_factory=list)   # analyst decisions applied to this result
    privacy: dict = field(default_factory=dict)             # what was hidden from the model (no personal data)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _counts(guard: PrivacyGuard | None) -> dict:
    """Privacy summary for meta.json: counts only."""
    return {k: v for k, v in guard.report().items() if k != "masked_text"} if guard else {}


def code_strings(code: str) -> str:
    """String literals of DIAGRAM code, one per line, so names typed on the canvas are checked as text."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    return "\n".join(node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip())


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
    def _open_log(self, kind: str, text: str, privacy: dict | None) -> RunLog:
        """A run journal with its privacy guard: personal data of `text` is known before any request."""
        log = RunLog(self.runs_dir, kind)
        log.guard = PrivacyGuard(text, **(privacy or {}))
        log.write("input.txt", text)
        return log

    def _ask(self, log: RunLog, stage: str, system: str, messages: list[dict], json_mode=False) -> str:
        """The only way to a model: personal data is masked before sending and restored in the answer."""
        if self.llm is None:
            raise LLMError("LLM не настроен: " + (self.llm_error or "задайте LLM_PROVIDER/LLM_API_KEY в .env"))
        if log.guard is None:     # fail closed: a run without a guard still never sends raw text
            log.guard = PrivacyGuard("\n".join(m["content"] for m in messages))
        outgoing = log.guard.mask_messages(messages)
        if log.guard.originals:
            system += prompts.PRIVACY_NOTE
        resp = self.llm.complete(system, outgoing, json_mode=json_mode)
        log.llm(stage, system, outgoing, resp)
        return log.guard.unmask(resp.text)

    def plan(self, text: str, log: RunLog, attempts: list[dict], resolutions=()) -> Plan:
        content = prompts.PLANNER_USER.format(text=text) + planner_decisions(resolutions)
        messages = [{"role": "user", "content": content}]
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

    def _find_conflicts(self, text: str, log: RunLog) -> list[ConflictSpec]:
        """A narrow second look for contradictions: the planner alone misses some among all its tasks."""
        try:
            answer = self._ask(log, "conflicts", prompts.CONFLICT_SYSTEM,
                              [{"role": "user", "content": prompts.PLANNER_USER.format(text=text)}], json_mode=True)
            found = extract_json(answer).get("conflicts") or []
            return [ConflictSpec.model_validate(c) for c in found if isinstance(c, dict)]
        except (LLMError, PlanError, ValueError) as e:     # the planner's own check still applies
            log.event("conflicts_failed", error=str(e)[:300])
            return []

    def _plan_and_conflicts(self, text: str, log: RunLog, attempts: list[dict], resolutions) -> tuple[Plan, list[dict]]:
        """Plan and the contradiction check run side by side, so the check adds no waiting time."""
        with ThreadPoolExecutor(2) as pool:
            extra = pool.submit(self._find_conflicts, text, log)
            plan = self.plan(text, log, attempts, resolutions)
            self._ground_quotes(plan, text)
            return plan, open_conflicts(plan.conflicts + extra.result(), text, resolutions)

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
    def prepare(self, text: str, resolutions=(), privacy: dict | None = None) -> dict:
        """One planning pass, exposed for analyst review before diagram construction."""
        log = self._open_log("interview", text, privacy)
        attempts = []
        try:
            plan, conflicts = self._plan_and_conflicts(text, log, attempts, resolutions)
            log.write("plan.json", plan.model_dump_json(indent=2, by_alias=True))
            log.finish(ok=True, conflicts=conflicts, privacy=_counts(log.guard))
            return {"ok": True, "plan": plan.model_dump(by_alias=True), "run_id": log.id, "conflicts": conflicts,
                    "privacy": log.guard.report()}
        except (LLMError, PlanError) as e:
            log.finish(ok=False, error=str(e), privacy=_counts(log.guard))
            return {"ok": False, "message": str(e), "run_id": log.id}

    @staticmethod
    def _ground_quotes(plan: Plan, text: str):
        for element in plan.elements:
            if element.source_quote and element.source_quote not in text:
                element.source_quote = ""
                element.assumption = (element.assumption + " Основание в исходном тексте не подтверждено.").strip()

    @staticmethod
    def _with_footnotes(result: BuildResult, title: str, source: str, resolutions, text: str) -> BuildResult:
        """Keep every decided contradiction on the diagram as a text annotation."""
        if not result.xml:
            return result
        code = add_footnotes(result.code, resolutions, text)
        if code == result.code:
            return result
        noted = build(code, title, lenient=source == "lenient")
        return noted if noted.xml else result

    def _stop_on_conflicts(self, log, t0, plan: Plan, attempts, conflicts, resolutions) -> PipelineResult:
        """The description contradicts itself: ask the analyst instead of guessing."""
        log.write("conflicts.json", json.dumps(conflicts, ensure_ascii=False, indent=2))
        log.finish(ok=False, source="conflict", conflicts=conflicts, privacy=_counts(log.guard))
        topics = "; ".join(c["topic"] for c in conflicts if c["topic"])
        return PipelineResult(False, None, "", json.loads(plan.model_dump_json(by_alias=True)), [], [], attempts,
                              {}, plan.assumptions, plan.questions, log.id, round(time.time() - t0, 2),
                              "conflict", "Описание противоречит само себе" + (f": {topics}" if topics else ""),
                              conflicts=conflicts, resolutions=list(resolutions), privacy=log.guard.report())

    def from_plan(self, plan: Plan, text: str = "", resolutions=()) -> PipelineResult:
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
        result = self._with_footnotes(result, plan.title, source, resolutions, text)
        res = self._finish(log, t0, result, plan, attempts, source)
        res.resolutions = list(resolutions)
        return res

    def generate(self, text: str, mode: str = "two_stage", resolutions=(), privacy: dict | None = None) -> PipelineResult:
        log = self._open_log("generate", text, privacy)
        if resolutions:
            log.write("resolutions.json", json.dumps(list(resolutions), ensure_ascii=False, indent=2))
        t0 = time.time()
        attempts: list[dict] = []
        plan: Plan | None = None
        title = "Процесс"
        try:
            if mode == "two_stage":
                plan, conflicts = self._plan_and_conflicts(text, log, attempts, resolutions)
                if conflicts:
                    return self._stop_on_conflicts(log, t0, plan, attempts, conflicts, resolutions)
                title = plan.title
                plan_json = plan.model_dump_json(indent=1, by_alias=True, exclude_none=True,
                                                 exclude={"conflicts"})
                system = prompts.CODEGEN_SYSTEM
                messages = [{"role": "user", "content": prompts.CODEGEN_USER.format(plan=plan_json)}]
            else:
                system = prompts.DIRECT_SYSTEM
                messages = [{"role": "user", "content": prompts.PLANNER_USER.format(text=text)}]
            result, repairs = self._code_loop(log, system, messages, title, attempts)
            source = "llm" if repairs == 0 else "llm_repaired"
        except (LLMError, PlanError) as e:
            log.finish(ok=False, error=str(e), privacy=_counts(log.guard))
            return PipelineResult(False, None, "", plan.model_dump(by_alias=True) if plan else None, [], [],
                                  attempts, {}, [], [], log.id, round(time.time() - t0, 2), "error", str(e),
                                  privacy=log.guard.report())

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
        result = self._with_footnotes(result, title, source, resolutions, text)
        res = self._finish(log, t0, result, plan, attempts, source)
        res.resolutions = list(resolutions)
        return res

    def refine(self, text: str, code: str, instruction: str, privacy: dict | None = None) -> PipelineResult:
        log = self._open_log("refine", text, privacy)
        log.guard.learn(instruction, own=True)   # the analyst's request is a command by design
        log.guard.learn(code_strings(code))      # labels edited by hand on the canvas are text too
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
            log.finish(ok=False, error=str(e), privacy=_counts(log.guard))
            return PipelineResult(False, None, code, None, [], [], attempts, {}, [], [], log.id,
                                  round(time.time() - t0, 2), "error", str(e), privacy=log.guard.report())
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
            privacy=log.guard.report() if log.guard else {},
        )
        log.finish(ok=res.ok, source=source, stats=result.stats, privacy=_counts(log.guard),
                   repairs=sum(1 for a in attempts if a["stage"].startswith("repair")),
                   errors=[i.to_dict() for i in result.issues if i.level == "error"],
                   xsd_errors=result.xsd_errors)
        return res
