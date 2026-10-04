"""Safe execution of LLM-generated diagram code.

Two layers:
1. AST whitelist: the program may only contain assignments and calls of
   `DIAGRAM.<api_method>(...)` with literal / variable arguments. No imports,
   attributes, loops, function definitions, comprehensions or dunder access —
   so the program is straight-line and cannot touch anything but the API.
2. Execution with empty builtins in a worker thread with a timeout and a
   statement budget.
Errors are returned with the offending line so they can be fed back to the LLM.
"""
from __future__ import annotations

import ast
import threading
from dataclasses import dataclass, field

from .bpmn.diagram import API_METHODS, Diagram, DiagramError

MAX_STATEMENTS = 600
TIMEOUT_S = 5.0
PRESET_NAMES = ("ROOT_PROCESS_ID", "ROOT_START_TASK_ID", "ROOT_END_TASK_ID")


class SandboxError(Exception):
    def __init__(self, message: str, line: int | None = None, code_line: str | None = None):
        self.message = message
        self.line = line
        self.code_line = code_line
        super().__init__(self.render())

    def render(self) -> str:
        if self.line is None:
            return self.message
        return f"Строка {self.line}: {self.message}\n    {self.code_line or ''}".rstrip()


@dataclass
class SandboxResult:
    diagram: Diagram
    variables: dict[str, object] = field(default_factory=dict)


def _strip_code_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code


class _Checker(ast.NodeVisitor):
    def __init__(self, lines: list[str]):
        self.lines = lines

    def fail(self, node: ast.AST, msg: str):
        line = getattr(node, "lineno", None)
        code_line = self.lines[line - 1].strip() if line and line <= len(self.lines) else None
        raise SandboxError(msg, line, code_line)

    def check_module(self, tree: ast.Module):
        if len(tree.body) > MAX_STATEMENTS:
            raise SandboxError(f"Слишком длинная программа: {len(tree.body)} операторов (лимит {MAX_STATEMENTS})")
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    self.check_target(t)
                if isinstance(stmt.value, ast.Call):
                    self.check_call(stmt.value)
                else:                      # alias such as `lane_client = lanes[0]`: names and indexes only
                    self.check_value(stmt.value)
            elif isinstance(stmt, ast.Expr):
                if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                    continue  # docstring-like comment
                self.check_call(stmt.value)
            elif isinstance(stmt, ast.Pass):
                continue
            else:
                self.fail(stmt, f"Запрещённая конструкция {type(stmt).__name__}. Разрешены только присваивания "
                                "и вызовы DIAGRAM.<метод>(...), без import, циклов, функций и условий.")

    def check_target(self, t: ast.AST):
        if isinstance(t, ast.Name):
            if t.id.startswith("__") or t.id in PRESET_NAMES or t.id == "DIAGRAM":
                self.fail(t, f"Нельзя присваивать переменной {t.id}")
            return
        if isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self.check_target(e)
            return
        self.fail(t, "Слева от '=' допускаются только имена переменных или кортеж имён")

    def check_call(self, node: ast.AST):
        if not isinstance(node, ast.Call):
            self.fail(node, "Ожидался вызов DIAGRAM.<метод>(...)")
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "DIAGRAM"):
            self.fail(node, "Можно вызывать только методы объекта DIAGRAM")
        if f.attr not in API_METHODS:
            self.fail(node, f"Метода DIAGRAM.{f.attr} нет в API. Доступные: {', '.join(sorted(API_METHODS))}")
        for a in node.args:
            self.check_value(a)
        for kw in node.keywords:
            if kw.arg is None:
                self.fail(node, "Распаковка **kwargs запрещена")
            self.check_value(kw.value)

    def check_value(self, v: ast.AST):
        if isinstance(v, ast.Constant) and (v.value is None or isinstance(v.value, (str, int, float, bool))):
            return
        if isinstance(v, ast.Name):
            if v.id.startswith("__"):
                self.fail(v, "Имена с __ запрещены")
            return
        if isinstance(v, (ast.List, ast.Tuple)):
            for e in v.elts:
                self.check_value(e)
            return
        if isinstance(v, ast.Subscript) and isinstance(v.value, ast.Name):
            idx = v.slice
            if isinstance(idx, ast.Constant) and isinstance(idx.value, int):
                return
            if isinstance(idx, ast.UnaryOp) and isinstance(idx.op, ast.USub) and isinstance(idx.operand, ast.Constant):
                return
        if isinstance(v, ast.JoinedStr):
            self.fail(v, "f-строки запрещены, используйте обычные строковые литералы")
        self.fail(v, f"Недопустимый аргумент ({type(v).__name__}). Разрешены строки, числа, None, "
                     "переменные, списки и индексы вида lanes[0]")


def check_code(code: str) -> ast.Module:
    code = _strip_code_fences(code)
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise SandboxError(f"Синтаксическая ошибка: {e.msg}", e.lineno, (e.text or "").strip())
    _Checker(code.splitlines()).check_module(tree)
    return tree


def run_code(code: str, diagram_name: str = "Процесс", timeout: float = TIMEOUT_S) -> SandboxResult:
    code = _strip_code_fences(code)
    tree = check_code(code)
    lines = code.splitlines()
    diagram = Diagram(diagram_name)
    env: dict[str, object] = {
        "__builtins__": {},
        "DIAGRAM": diagram,
        "ROOT_PROCESS_ID": diagram.root_process,
        "ROOT_START_TASK_ID": diagram.root_start,
        "ROOT_END_TASK_ID": diagram.root_end,
    }
    outcome: dict[str, BaseException] = {}

    def worker():
        # Execute statement by statement so that errors carry the right line.
        for stmt in tree.body:
            mod = ast.Module(body=[stmt], type_ignores=[])
            try:
                exec(compile(mod, "<diagram>", "exec"), env)  # noqa: S102 - AST-whitelisted
            except BaseException as e:  # noqa: BLE001
                line = getattr(stmt, "lineno", None)
                code_line = lines[line - 1].strip() if line else None
                if isinstance(e, DiagramError):
                    outcome["error"] = SandboxError(str(e), line, code_line)
                elif isinstance(e, NameError):
                    outcome["error"] = SandboxError(
                        f"{e}. Переменную нужно определить раньше, чем использовать", line, code_line)
                elif isinstance(e, (TypeError, ValueError, IndexError)):
                    outcome["error"] = SandboxError(f"{type(e).__name__}: {e}", line, code_line)
                else:
                    outcome["error"] = SandboxError(f"{type(e).__name__}: {e}", line, code_line)
                return

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise SandboxError(f"Превышен таймаут выполнения {timeout} с")
    if "error" in outcome:
        raise outcome["error"]
    variables = {k: v for k, v in env.items() if k not in ("__builtins__", "DIAGRAM")}
    return SandboxResult(diagram, variables)
