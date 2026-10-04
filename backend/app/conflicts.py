"""Self-contradicting descriptions: the diagram is not drawn until the analyst picks the senior rule.

The planner reports pairs of incompatible rules as quotes. A pair blocks the
pipeline only when both quotes are found in the description and cover different
fragments: a paraphrase or a pair of overlapping quotes is a model guess, not
evidence. After the decision the losing rule stays on the diagram as a text
annotation, so the description still gets fixed.
"""
from __future__ import annotations

import ast
import re

QUOTE_MARKS = " \t\r\n«»\"“”„'‘’"
MAX_QUOTE = 160


def find_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Span of `quote` in `text`, ignoring outer quote marks and whitespace differences."""
    words = quote.strip(QUOTE_MARKS).split()
    if not words:
        return None
    m = re.search(r"\s+".join(map(re.escape, words)), text)
    return m.span() if m else None


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _same_pair(spans, other) -> bool:
    (a, b), (x, y) = spans, other
    return (_overlap(a, x) and _overlap(b, y)) or (_overlap(a, y) and _overlap(b, x))


def open_conflicts(conflicts, text: str, resolutions=()) -> list[dict]:
    """Grounded, undecided conflicts with quotes replaced by the exact fragments of `text`."""
    decided = []
    for r in resolutions:
        spans = find_quote(r["rule_a"], text), find_quote(r["rule_b"], text)
        if None not in spans:
            decided.append(spans)
    out, seen = [], []
    for c in conflicts:
        spans = find_quote(c.rule_a, text), find_quote(c.rule_b, text)
        if None in spans or _overlap(*spans):
            continue
        if any(_same_pair(spans, other) for other in decided + seen):
            continue
        seen.append(spans)
        (a0, a1), (b0, b1) = spans
        out.append({"id": f"c{len(out) + 1}", "topic": c.topic.strip(),
                    "rule_a": text[a0:a1], "rule_b": text[b0:b1]})
    return out


def _senior(r: dict) -> tuple[str, str]:
    return (r["rule_a"], r["rule_b"]) if r["chosen"] == "a" else (r["rule_b"], r["rule_a"])


def planner_decisions(resolutions) -> str:
    """Analyst decisions appended to the planner request."""
    lines = []
    for r in resolutions:
        topic = r.get("topic") or "Противоречие"
        if r["chosen"] == "both":
            lines.append(f"- {topic}. Это не противоречие, правила относятся к разным случаям — "
                         f"учитывай оба: «{r['rule_a']}» и «{r['rule_b']}».")
        else:
            senior, junior = _senior(r)
            lines.append(f"- {topic}. Старшее правило: «{senior}». Правило «{junior}» не применяй.")
    return ("\n\nРешения аналитика по противоречиям в описании (строй план по ним и не включай эти пары "
            "в conflicts):\n" + "\n".join(lines)) if lines else ""


def _short(quote: str) -> str:
    q = " ".join(quote.split()).rstrip(" .;,")
    return q if len(q) <= MAX_QUOTE else q[:MAX_QUOTE - 1].rstrip() + "…"


def footnote(r: dict) -> str:
    senior, junior = _senior(r)
    topic = " ".join((r.get("topic") or "").split())
    return (f"Противоречие в описании{': ' + topic if topic else ''}. Принято: «{_short(senior)}». "
            f"Не применено: «{_short(junior)}» — поправьте текст описания.")


_TASKS = {"add_task", "add_user_task", "add_service_task", "add_script_task", "add_manual_task",
          "add_send_task", "add_receive_task", "add_business_rule_task", "create_subprocess"}


def _diagram_call(node) -> str | None:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "DIAGRAM"):
        return node.func.attr
    return None


def _anchor(code: str, r: dict, text: str) -> str:
    """Variable of the step whose source quote overlaps the senior rule (then the other one)."""
    quoted, first_task = [], None
    try:
        body = ast.parse(code).body
    except SyntaxError:
        body = []
    for stmt in body:
        if isinstance(stmt, ast.Assign) and _diagram_call(stmt.value) in _TASKS:
            target = stmt.targets[0]
            if first_task is None and isinstance(target, ast.Name):
                first_task = target.id
        if isinstance(stmt, ast.Expr) and _diagram_call(stmt.value) == "set_details":
            args = stmt.value.args
            if (len(args) >= 2 and isinstance(args[0], ast.Name) and isinstance(args[1], ast.Constant)
                    and isinstance(args[1].value, str) and args[1].value):
                span = find_quote(args[1].value, text)
                if span:
                    quoted.append((args[0].id, span))
    for rule in _senior(r):
        span = find_quote(rule, text)
        if span:
            for var, quote_span in quoted:
                if _overlap(span, quote_span):
                    return var
    return first_task or "ROOT_START_TASK_ID"


def add_footnotes(code: str, resolutions, text: str) -> str:
    """Append a text annotation for every decided contradiction (none when both rules hold)."""
    lines = [f"DIAGRAM.add_annotation({footnote(r)!r}, {_anchor(code, r, text)})"
             for r in resolutions if r["chosen"] in ("a", "b")]
    if not lines:
        return code
    return code.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
