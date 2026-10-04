"""Block code: a small, safe subset of Python, interpreted by walking the AST.

Service tasks carry a few lines of code that read and change process variables; gateway branches
carry a check (one expression). The code comes from a .bpmn file, i.e. from anyone, so it is never
passed to exec/eval: this module interprets the AST itself.

- Only data: None, bool, int, float, str, list, tuple, dict. No import, def, class, lambda, try,
  with, global, attributes (except whitelisted methods of str/list/dict), dunder names.
- Statements: assignment (also `x[k] = v`, `a, b = ...`, `+=`), if/elif/else, for, while, break,
  continue, pass, function calls. Expressions: arithmetic, comparisons, and/or/not, `x if c else y`,
  indexes and slices, f-strings, list/dict literals, list comprehensions and generator expressions.
- Limits: a step budget (stops infinite loops), size of strings/lists, magnitude of numbers, code length.
Errors are ScriptError with a line number and a message in Russian, ready to show to the analyst.
"""
from __future__ import annotations

import ast
import datetime as _dt
import math
import operator
import re

MAX_STEPS = 20_000
MAX_LEN = 100_000
MAX_CODE = 10_000
MAX_INT = 10 ** 30
MAX_LOG = 200


class ScriptError(Exception):
    def __init__(self, message: str, line: int | None = None):
        self.message, self.line = message, line
        super().__init__(f"Строка {line}: {message}" if line else message)


class ScriptFail(ScriptError):
    """Raised by fail("...") in block code: a business error, not a programming one."""


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


# ----------------------------------------------------------------------------------- built-ins
def _date(s):
    if isinstance(s, str):
        try:
            return _dt.date.fromisoformat(s[:10])
        except ValueError:
            pass
    raise ScriptError(f"ожидалась дата в формате ГГГГ-ММ-ДД, получено {s!r}")


def _add_days(d, n):
    if not isinstance(n, (int, float)) or isinstance(n, bool) or abs(n) > 100_000:
        raise ScriptError("add_days: второй аргумент — число дней")
    return (_date(d) + _dt.timedelta(days=int(n))).isoformat()


def _add_workdays(d, n):
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 10_000:
        raise ScriptError("add_workdays: второй аргумент — целое число рабочих дней от 0")
    day = _date(d)
    while n:
        day += _dt.timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day.isoformat()


def _range(*a):
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in a) or not 1 <= len(a) <= 3:
        raise ScriptError("range: нужны 1–3 целых числа")
    r = range(*a)
    if len(r) > MAX_LEN:
        raise ScriptError(f"range: слишком много значений (больше {MAX_LEN})")
    return list(r)


def _round(x, n=0):
    return round(x, n)


SAFE_FUNCS = {
    "len": len, "min": min, "max": max, "sum": sum, "abs": abs, "round": _round,
    "int": int, "float": float, "str": str, "bool": bool, "list": list, "dict": dict,
    "sorted": sorted, "any": any, "all": all, "range": _range,
    "enumerate": lambda x, start=0: [list(p) for p in enumerate(x, start)],
    "zip": lambda *x: [list(p) for p in zip(*x)],
    "reversed": lambda x: list(reversed(x)),
    "today": lambda: _dt.date.today().isoformat(),
    "now": lambda: _dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
    "add_days": _add_days, "add_workdays": _add_workdays,
    "days_between": lambda a, b: (_date(b) - _date(a)).days,
    "ceil": math.ceil, "floor": math.floor,
}
# also available, but handled by the interpreter: log(...), fail(msg), value(name, default)
SPECIAL_FUNCS = {"log", "fail", "value"}

METHODS = {
    str: {"lower", "upper", "strip", "lstrip", "rstrip", "split", "join", "replace", "startswith",
          "endswith", "find", "count", "capitalize", "title", "isdigit", "isalpha"},
    list: {"append", "extend", "pop", "index", "count", "insert", "remove", "copy", "sort", "reverse"},
    dict: {"get", "keys", "values", "items", "copy", "update", "pop", "setdefault"},
}
DATA = (type(None), bool, int, float, str, list, tuple, dict)

BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
          ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
CMPOPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
          ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
          ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not}

STATEMENTS_HELP = ("Разрешены присваивания, if/elif/else, for, while, break, continue, pass и вызовы "
                   "функций; без import, def, class, lambda, try и with.")


def _guard(v, line=None):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int) and abs(v) > MAX_INT:
        raise ScriptError("слишком большое число", line)
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise ScriptError("результат вычисления не является числом (деление на ноль или переполнение)", line)
    if isinstance(v, (str, list, tuple, dict)) and len(v) > MAX_LEN:
        raise ScriptError(f"слишком длинное значение (больше {MAX_LEN} элементов)", line)
    if not isinstance(v, DATA):
        raise ScriptError(f"недопустимый тип значения {type(v).__name__}", line)
    return v


class Interpreter:
    def __init__(self, variables: dict | None = None, max_steps: int = MAX_STEPS):
        self.vars: dict = dict(variables or {})
        self.scopes: list[dict] = []           # comprehension variables
        self.steps = 0
        self.max_steps = max_steps
        self.logs: list[str] = []
        self.line: int | None = None

    # ------------------------------------------------------------------ helpers
    def tick(self, node):
        self.line = getattr(node, "lineno", self.line)
        self.steps += 1
        if self.steps > self.max_steps:
            raise ScriptError(f"превышен лимит {self.max_steps} шагов вычисления — возможно, бесконечный цикл",
                              self.line)

    def fail(self, node, msg):
        raise ScriptError(msg, getattr(node, "lineno", self.line))

    def lookup(self, node, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        if name in self.vars:
            return self.vars[name]
        if name in SAFE_FUNCS or name in SPECIAL_FUNCS:
            self.fail(node, f"{name} — функция, её нужно вызывать: {name}(...)")
        if name in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[name]
        self.fail(node, f"переменная «{name}» не задана. Задайте её на предыдущем шаге или используйте "
                        f"value(\"{name}\", значение_по_умолчанию)")

    # ------------------------------------------------------------------ statements
    def exec_block(self, body):
        for stmt in body:
            self.exec_stmt(stmt)

    def exec_stmt(self, s):
        self.tick(s)
        if isinstance(s, ast.Assign):
            value = self.eval(s.value)
            for t in s.targets:
                self.assign(t, value)
        elif isinstance(s, ast.AugAssign):
            op = BINOPS.get(type(s.op))
            if op is None:
                self.fail(s, "этот оператор не поддерживается")
            cur = self.eval(s.target if not isinstance(s.target, ast.Name) else ast.Name(s.target.id, ast.Load(),
                                                                                       lineno=s.lineno))
            self.assign(s.target, self.binop(s, op, cur, self.eval(s.value)))
        elif isinstance(s, ast.AnnAssign) and s.value is not None:
            self.assign(s.target, self.eval(s.value))
        elif isinstance(s, ast.Expr):
            self.eval(s.value)
        elif isinstance(s, ast.If):
            self.exec_block(s.body if self.truth(self.eval(s.test)) else s.orelse)
        elif isinstance(s, ast.For):
            items = self.iterable(s, self.eval(s.iter))
            for item in items:
                self.assign(s.target, item)
                try:
                    self.exec_block(s.body)
                except _Break:
                    break
                except _Continue:
                    continue
            else:
                self.exec_block(s.orelse)
        elif isinstance(s, ast.While):
            while self.truth(self.eval(s.test)):
                self.tick(s)
                try:
                    self.exec_block(s.body)
                except _Break:
                    break
                except _Continue:
                    continue
            else:
                self.exec_block(s.orelse)
        elif isinstance(s, ast.Break):
            raise _Break
        elif isinstance(s, ast.Continue):
            raise _Continue
        elif isinstance(s, ast.Pass):
            pass
        else:
            self.fail(s, f"конструкция {type(s).__name__} не поддерживается. {STATEMENTS_HELP}")

    def assign(self, t, value):
        if isinstance(t, ast.Name):
            if t.id.startswith("__") or t.id in SAFE_FUNCS or t.id in SPECIAL_FUNCS:
                self.fail(t, f"имя «{t.id}» занято, выберите другое")
            if self.scopes and t.id in self.scopes[-1]:
                self.scopes[-1][t.id] = value
            else:
                self.vars[t.id] = value
        elif isinstance(t, (ast.Tuple, ast.List)):
            items = self.iterable(t, value)
            if len(items) != len(t.elts):
                self.fail(t, f"нужно {len(t.elts)} значения, получено {len(items)}")
            for sub, v in zip(t.elts, items):
                self.assign(sub, v)
        elif isinstance(t, ast.Subscript):
            obj = self.eval(t.value)
            key = self.eval(t.slice)
            if not isinstance(obj, (list, dict)):
                self.fail(t, "присваивать по индексу можно только элементу списка или словаря")
            try:
                obj[key] = value
            except (IndexError, KeyError, TypeError) as e:
                self.fail(t, f"нельзя присвоить по индексу {key!r}: {e}")
            _guard(obj, self.line)
        else:
            self.fail(t, "слева от «=» может быть имя, кортеж имён или элемент x[...]")

    # ------------------------------------------------------------------ expressions
    def truth(self, v):
        return bool(v)

    def iterable(self, node, v):
        if isinstance(v, dict):
            return list(v.keys())
        if isinstance(v, (list, tuple, str)):
            return list(v)
        self.fail(node, f"по значению типа {type(v).__name__} нельзя пройти циклом")

    def binop(self, node, op, a, b):
        if op is operator.mod and isinstance(a, str):
            self.fail(node, "форматирование через % не поддерживается — используйте f-строки: f\"{x}\"")
        if op is operator.mul:
            for seq, n in ((a, b), (b, a)):
                if isinstance(seq, (str, list, tuple)) and isinstance(n, int) and len(seq) * max(n, 0) > MAX_LEN:
                    self.fail(node, "слишком длинный результат умножения")
        if op is operator.pow and isinstance(b, (int, float)) and isinstance(a, (int, float)):
            if abs(b) > 1000 or (abs(a) > 1 and abs(b) * math.log2(abs(a)) > 100):
                self.fail(node, "слишком большая степень")
        try:
            return _guard(op(a, b), getattr(node, "lineno", self.line))
        except ZeroDivisionError:
            self.fail(node, "деление на ноль")
        except TypeError:
            self.fail(node, f"операция невозможна для {type(a).__name__} и {type(b).__name__}")
        except OverflowError:
            self.fail(node, "переполнение числа")

    def eval(self, n):
        self.tick(n)
        if isinstance(n, ast.Constant):
            if not isinstance(n.value, DATA):
                self.fail(n, "недопустимая константа")
            return n.value
        if isinstance(n, ast.Name):
            if n.id.startswith("__"):
                self.fail(n, "имена с __ запрещены")
            return self.lookup(n, n.id)
        if isinstance(n, ast.BinOp):
            op = BINOPS.get(type(n.op))
            if op is None:
                self.fail(n, "битовые операции не поддерживаются")
            return self.binop(n, op, self.eval(n.left), self.eval(n.right))
        if isinstance(n, ast.UnaryOp):
            v = self.eval(n.operand)
            if isinstance(n.op, ast.Not):
                return not v
            if isinstance(n.op, ast.USub) and isinstance(v, (int, float)):
                return -v
            if isinstance(n.op, ast.UAdd) and isinstance(v, (int, float)):
                return v
            self.fail(n, "унарная операция невозможна для этого значения")
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                v = True
                for x in n.values:
                    v = self.eval(x)
                    if not v:
                        return v
                return v
            v = False
            for x in n.values:
                v = self.eval(x)
                if v:
                    return v
            return v
        if isinstance(n, ast.Compare):
            left = self.eval(n.left)
            for op, right_node in zip(n.ops, n.comparators):
                right = self.eval(right_node)
                try:
                    if not CMPOPS[type(op)](left, right):
                        return False
                except TypeError:
                    self.fail(n, f"нельзя сравнить {type(left).__name__} и {type(right).__name__}")
                left = right
            return True
        if isinstance(n, ast.IfExp):
            return self.eval(n.body) if self.truth(self.eval(n.test)) else self.eval(n.orelse)
        if isinstance(n, (ast.List, ast.Tuple)):
            out = []
            for e in n.elts:
                if isinstance(e, ast.Starred):
                    out.extend(self.iterable(e, self.eval(e.value)))
                else:
                    out.append(self.eval(e))
            return _guard(out if isinstance(n, ast.List) else tuple(out), self.line)
        if isinstance(n, ast.Dict):
            out = {}
            for k, v in zip(n.keys, n.values):
                if k is None:
                    d = self.eval(v)
                    if not isinstance(d, dict):
                        self.fail(n, "** можно применять только к словарю")
                    out.update(d)
                else:
                    key = self.eval(k)
                    if not isinstance(key, (str, int, float, bool, type(None))):
                        self.fail(n, "ключ словаря — строка или число")
                    out[key] = self.eval(v)
            return _guard(out, self.line)
        if isinstance(n, ast.Subscript):
            obj = self.eval(n.value)
            if isinstance(n.slice, ast.Slice):
                parts = [self.eval(p) if p is not None else None for p in (n.slice.lower, n.slice.upper, n.slice.step)]
                if not isinstance(obj, (str, list, tuple)):
                    self.fail(n, "срез возможен только у строки или списка")
                try:
                    return obj[slice(*parts)]
                except (TypeError, ValueError) as e:
                    self.fail(n, f"неверный срез: {e}")
            key = self.eval(n.slice)
            try:
                return obj[key]
            except KeyError:
                self.fail(n, f"нет ключа {key!r}")
            except IndexError:
                self.fail(n, f"индекс {key!r} вне списка")
            except TypeError:
                self.fail(n, f"значение типа {type(obj).__name__} нельзя индексировать так")
        if isinstance(n, ast.JoinedStr):
            out = []
            for part in n.values:
                if isinstance(part, ast.Constant):
                    out.append(str(part.value))
                elif isinstance(part, ast.FormattedValue):
                    v = self.eval(part.value)
                    spec = ""
                    if part.format_spec is not None:
                        if not all(isinstance(p, ast.Constant) for p in part.format_spec.values):
                            self.fail(n, "формат в f-строке должен быть постоянным, например {x:.2f}")
                        spec = "".join(str(p.value) for p in part.format_spec.values)
                        if re.search(r"\d{4,}", spec):
                            self.fail(n, "слишком большая ширина в формате f-строки")
                    try:
                        out.append(format(v, spec) if spec else str(v))
                    except (ValueError, TypeError):
                        self.fail(n, f"неверный формат «{spec}»")
            return _guard("".join(out), self.line)
        if isinstance(n, (ast.ListComp, ast.GeneratorExp)):
            return _guard(self.comprehension(n), self.line)
        if isinstance(n, ast.Call):
            return self.call(n)
        if isinstance(n, ast.Attribute):
            self.fail(n, "обращение к атрибутам запрещено (разрешены только вызовы методов строк, списков и словарей)")
        self.fail(n, f"выражение {type(n).__name__} не поддерживается")

    def comprehension(self, n):
        if len(n.generators) != 1 or n.generators[0].is_async:
            self.fail(n, "поддерживается только один for внутри [... for ... in ...]")
        gen = n.generators[0]
        items = self.iterable(n, self.eval(gen.iter))
        out = []
        self.scopes.append({})
        try:
            for item in items:
                self.tick(n)
                if isinstance(gen.target, ast.Name):
                    self.scopes[-1][gen.target.id] = item
                else:
                    self.assign(gen.target, item)
                if all(self.truth(self.eval(c)) for c in gen.ifs):
                    out.append(self.eval(n.elt))
                    if len(out) > MAX_LEN:
                        self.fail(n, "слишком длинный список")
        finally:
            self.scopes.pop()
        return out

    def call(self, n):
        args = []
        for a in n.args:
            if isinstance(a, ast.Starred):
                args.extend(self.iterable(a, self.eval(a.value)))
            else:
                args.append(self.eval(a))
        kwargs = {}
        for kw in n.keywords:
            if kw.arg is None:
                self.fail(n, "распаковка **kwargs запрещена")
            kwargs[kw.arg] = self.eval(kw.value)
        f = n.func
        if isinstance(f, ast.Name):
            if f.id == "log":
                if len(self.logs) < MAX_LOG:
                    self.logs.append(" ".join(str(a) for a in args)[:1000])
                return None
            if f.id == "fail":
                raise ScriptFail(str(args[0]) if args else "шаг завершён с ошибкой", n.lineno)
            if f.id == "value":
                if not args or not isinstance(args[0], str):
                    self.fail(n, "value(\"имя\", по_умолчанию)")
                for scope in reversed(self.scopes):
                    if args[0] in scope:
                        return scope[args[0]]
                return self.vars.get(args[0], args[1] if len(args) > 1 else None)
            fn = SAFE_FUNCS.get(f.id)
            if fn is None:
                self.fail(n, f"функции {f.id}() нет. Доступны: {', '.join(sorted(set(SAFE_FUNCS) | SPECIAL_FUNCS))}")
            if f.id == "sorted" and "key" in kwargs:
                self.fail(n, "sorted(..., key=...) не поддерживается")
        elif isinstance(f, ast.Attribute):
            obj = self.eval(f.value)
            allowed = next((m for t, m in METHODS.items() if type(obj) is t), set())
            if f.attr not in allowed:
                self.fail(n, f"метод .{f.attr}() недоступен для {type(obj).__name__}")
            if f.attr == "sort" and kwargs:
                self.fail(n, "sort() без параметров")
            fn = getattr(obj, f.attr)
        else:
            self.fail(n, "вызывать можно только функции по имени или методы значений")
        try:
            result = fn(*args, **kwargs)
        except ScriptError as e:
            raise ScriptError(e.message, n.lineno)
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:
            self.fail(n, f"ошибка в {getattr(f, 'id', getattr(f, 'attr', ''))}(): {e}")
        if f.__class__ is ast.Attribute and f.attr in ("keys", "values", "items"):
            result = [list(x) if isinstance(x, tuple) else x for x in result]
        return _guard(result, n.lineno)


# ----------------------------------------------------------------------------------- public API
def _parse(code: str, mode: str):
    code = code or ""
    if len(code) > MAX_CODE:
        raise ScriptError(f"код длиннее {MAX_CODE} символов")
    try:
        return ast.parse(code, mode=mode)
    except SyntaxError as e:
        raise ScriptError(f"синтаксическая ошибка: {e.msg}", e.lineno)
    except (ValueError, RecursionError, MemoryError) as e:
        raise ScriptError(f"не удалось разобрать код: {e}")


def check_syntax(code: str, mode: str = "exec") -> str | None:
    """None if the code parses and uses only supported constructs (statically), else the message."""
    try:
        tree = _parse(code, "eval" if mode == "check" else "exec")
    except ScriptError as e:
        return str(e)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Lambda, ast.Try, ast.With, ast.Global, ast.Nonlocal, ast.Raise, ast.Delete,
                             ast.Await, ast.Yield, ast.YieldFrom, ast.SetComp, ast.DictComp, ast.Set,
                             ast.NamedExpr, ast.Return, ast.Assert)):
            return f"Строка {getattr(node, 'lineno', '?')}: конструкция {type(node).__name__} не поддерживается. " \
                   + STATEMENTS_HELP
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return f"Строка {node.lineno}: имена с __ запрещены"
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return f"Строка {node.lineno}: обращение к «{node.attr}» запрещено"
    return None


def _jsonable(v, depth=0):
    if depth > 20:
        raise ScriptError("слишком глубокая вложенность данных")
    if isinstance(v, tuple):
        v = list(v)
    if isinstance(v, list):
        return [_jsonable(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x, depth + 1) for k, x in v.items()}
    return v


def run_script(code: str, variables: dict, max_steps: int = MAX_STEPS) -> tuple[dict, list[str]]:
    """Runs block code; returns (new variables, log lines). Raises ScriptError / ScriptFail."""
    msg = check_syntax(code)
    if msg:
        raise ScriptError(msg)
    tree = _parse(code, "exec")
    it = Interpreter(variables, max_steps)
    try:
        it.exec_block(tree.body)
    except (_Break, _Continue):
        raise ScriptError("break/continue вне цикла", it.line)
    except RecursionError:
        raise ScriptError("слишком сложное выражение", it.line)
    out = {k: _jsonable(v) for k, v in it.vars.items() if not k.startswith("_")}
    return out, it.logs


def eval_check(expr: str, variables: dict) -> bool:
    """Gateway branch check: one expression over the process variables."""
    msg = check_syntax(expr, "check")
    if msg:
        raise ScriptError(msg)
    tree = _parse(expr, "eval")
    it = Interpreter(variables)
    try:
        return bool(it.eval(tree.body))
    except RecursionError:
        raise ScriptError("слишком сложное выражение")


def eval_value(expr: str, variables: dict):
    """Value of one expression (used for {placeholders} in document templates)."""
    msg = check_syntax(expr, "check")
    if msg:
        raise ScriptError(msg)
    try:
        return Interpreter(variables, 2_000).eval(_parse(expr, "eval").body)
    except RecursionError:
        raise ScriptError("слишком сложное выражение")


def render_template(template: str, variables: dict) -> str:
    """Replaces {expression} with its value; {{ and }} are literal braces. Unknown values are marked."""
    def sub(m):
        expr = m.group(1).strip()
        try:
            v = eval_value(expr, variables)
        except ScriptError:
            return f"‹{expr}: не задано›"
        if isinstance(v, float):
            return f"{v:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
        if isinstance(v, bool):
            return "да" if v else "нет"
        return "" if v is None else str(v)
    text = (template or "").replace("{{", "\x00").replace("}}", "\x01")
    text = re.sub(r"\{([^{}\n]{1,200})\}", sub, text)
    return text.replace("\x00", "{").replace("\x01", "}")
