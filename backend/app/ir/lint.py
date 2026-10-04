"""Deterministic process linter over the IR, with automatic fixes where they are safe.

Every issue: level (error | warning | info), code, element, message, hint, fixable.
`autofix` applies the fixable ones and returns a new, re-validated Plan plus the list of fixes.
`questions` turns gaps into clarifying questions (performer, branch condition, "otherwise", end).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

from ..llm.plan import Plan, check_plan
from .graph import CONDITIONAL, GATEWAY_TYPES, TASK_TYPES, TOP, IRGraph

NEGATIVE_LABEL = re.compile(r"\b(нет|иначе|отказ|не\s|неполн|недостат|откл|false|no)\b", re.I)


@dataclass
class LintIssue:
    level: str
    code: str
    element: str | None
    message: str
    hint: str
    fixable: bool = False
    question: str | None = None      # clarifying question for the analyst/customer
    blocking: bool = False           # in "ask" mode the diagram is not built until answered

    def to_dict(self) -> dict:
        return asdict(self)


def lint(plan: Plan) -> list[LintIssue]:
    g = IRGraph.build(plan)
    issues: list[LintIssue] = []

    def add(*a, **kw):
        issues.append(LintIssue(*a, **kw))

    top_nodes = [n for n in g.nodes_in(TOP) if n not in ("start", "end")]
    has_participants = bool(plan.participants)

    # --- start / end -------------------------------------------------------------------------
    starts = g.starts(TOP)
    if top_nodes and not any(g.out[s] for s in starts):
        add("error", "E_NO_START", None, "Процесс ни с чего не начинается: нет связи от начала.",
            "Свяжите начало процесса с первым шагом.", fixable=True)
    explicit_starts = [n for n in starts if g.out[n]]
    plain = [n for n in explicit_starts if n == "start" or not g.elements[n].event]
    if len(plain) > 1:
        add("warning", "W_MULTI_START", plain[1], f"Несколько стартовых событий без указания причины: "
            f"{', '.join(g.label(n) for n in plain)}.",
            "Оставьте одно начало или укажите тип события (сообщение, таймер) у каждого.")
    ends_used = bool(g.inc["end"]) or any(g.type(n) == "end_event" and g.inc[n] for n in top_nodes)
    if top_nodes and not ends_used:
        add("error", "E_NO_END", None, "У процесса нет конца: ни одна связь не ведёт к завершению.",
            "Подключите последний шаг к концу процесса.", fixable=True,
            question="Чем и после какого шага завершается процесс?", blocking=True)

    # --- connectivity ------------------------------------------------------------------------
    for c in g.containers():
        nodes = [n for n in g.nodes_in(c) if n not in ("start", "end")]
        is_top = c == TOP
        reach = g.reachable(g.starts(c))
        for n in nodes:
            t = g.type(n)
            ins, outs = g.inc[n], g.out[n]
            if t == "end_event":
                if outs:
                    add("error", "E_END_OUTGOING", n, f"У конечного события {g.label(n)} есть исходящие связи.",
                        "Конечное событие завершает ветку — уберите исходящие связи.", fixable=True)
            elif not outs and is_top and t != "boundary_timer":
                add("error", "E_DEAD_END", n, f"Тупик: из {g.label(n)} нет перехода дальше.",
                    "Укажите следующий шаг или завершите ветку конечным событием.", fixable=n in reach,
                    question=f"Что происходит после шага «{g.name(n)}»?", blocking=True)
            if t == "start_event":
                continue
            if t == "boundary_timer":
                if not outs:
                    add("error", "E_BOUNDARY_NO_PATH", n, f"У таймера {g.label(n)} нет пути эскалации.",
                        "Свяжите таймер со шагом эскалации (например, «Эскалировать руководителю»).")
                continue
            if not ins and is_top:
                add("error", "E_NO_INCOMING", n, f"В {g.label(n)} не ведёт ни одна связь — шаг недостижим.",
                    "Добавьте переход из предыдущего шага или удалите элемент.",
                    fixable=not outs)                  # isolated element -> safe to remove
            elif ins and n not in reach:
                add("error", "E_UNREACHABLE", n, f"{g.label(n)} недостижим из начала процесса (замкнутый цикл).",
                    "Добавьте вход в этот участок из основного потока.")

    # --- gateways ----------------------------------------------------------------------------
    for n, e in g.elements.items():
        if e.type not in GATEWAY_TYPES:
            continue
        ins, outs = g.inc[n], g.out[n]
        if len(ins) <= 1 and len(outs) == 1 and ins:
            if e.type in CONDITIONAL:
                add("warning", "W_SINGLE_BRANCH", n, f"У шлюза {g.label(n)} только одна ветка — "
                    "неясно, что происходит при другом ответе.",
                    "Добавьте альтернативную ветку или уберите шлюз.",
                    question=f"Что происходит, если ответ на вопрос «{e.name or 'условие'}» отрицательный?",
                    blocking=True)
            else:
                add("warning", "W_TRIVIAL_GATEWAY", n, f"Шлюз {g.label(n)} с одним входом и одним выходом лишний.",
                    "Удалите шлюз, соединив соседние шаги напрямую.", fixable=True)
        if e.type in CONDITIONAL and len(outs) > 1:
            unlabeled = [f for f in outs if not f.label and not f.default]
            if unlabeled:
                add("warning", "W_NO_CONDITION", n, f"Не у всех ветвей шлюза {g.label(n)} указано условие.",
                    "Подпишите условие каждой ветви или отметьте одну ветку как «иначе».",
                    fixable=len(unlabeled) == 1 and not any(f.default for f in outs),
                    question=f"При каком условии после «{e.name or n}» выбирается переход к "
                             f"«{g.name(unlabeled[0].target)}»?", blocking=True)
            elif not any(f.default for f in outs):
                negative = [f for f in outs if f.label and NEGATIVE_LABEL.search(f.label + " ")]
                add("info", "I_NO_DEFAULT", n, f"У шлюза {g.label(n)} нет ветки по умолчанию («иначе»).",
                    "Отметьте ветку, которая выбирается, если ни одно условие не выполнено.",
                    fixable=len(negative) == 1,
                    question=f"Что происходит, если ни одно из условий шлюза «{e.name or n}» не выполнено?")
        if e.type == "parallel_gateway" and len(outs) > 1:
            _check_and_split(g, n, add)
        if len(ins) > 1 and e.type == "parallel_gateway":
            splits = {g.nearest_split(f.source) for f in ins} - {None}
            if len(splits) == 1 and g.type(next(iter(splits))) == "exclusive_gateway":
                add("error", "E_DEADLOCK", n, f"Параллельное слияние {g.label(n)} ждёт все ветви, но их "
                    f"порождает исключающий шлюз {g.label(next(iter(splits)))} — процесс зависнет.",
                    "Сделайте слияние исключающим шлюзом.", fixable=True)
        if len(ins) > 1 and len(outs) > 1:
            add("info", "I_MIXED_GATEWAY", n, f"Шлюз {g.label(n)} одновременно сливает и разветвляет потоки.",
                "Для читаемости разделите его на два шлюза.")

    # --- performers, names -------------------------------------------------------------------
    for n, e in g.elements.items():
        if e.type in TASK_TYPES:
            if has_participants and not e.participant and not e.parent:
                prev = _predecessor_participant(g, n)
                add("warning", "W_NO_PERFORMER", n, f"У шага {g.label(n)} не указан исполнитель.",
                    "Назначьте участника (дорожку).", fixable=prev is not None,
                    question=f"Кто выполняет шаг «{e.name or n}»?", blocking=True)
            if not e.name.strip():
                add("warning", "W_UNNAMED", n, f"У шага {n} нет названия.", "Назовите шаг «глагол + объект».")
        elif e.type in ("end_event", "timer_event", "message_event") and not e.name.strip() and g.inc[n]:
            add("info", "I_UNNAMED_EVENT", n, f"У события {n} нет названия.", "Назовите результат или ожидание.")
        external = {p.id for p in plan.participants if p.external}
        for f in g.out[n]:
            tgt = g.elements.get(f.target)
            if e.participant and tgt and tgt.participant and tgt.participant != e.participant and (
                    e.participant in external or tgt.participant in external):
                add("warning", "W_CROSS_POOL_FLOW", n, f"Поток управления {g.label(n)} → {g.label(f.target)} "
                        "пересекает границу организаций.", "Между пулами используйте поток сообщений.",
                        fixable=True)
    names = Counter(e.name.strip().lower() for e in plan.elements if e.type in TASK_TYPES and e.name.strip())
    for e in plan.elements:
        if e.type in TASK_TYPES and e.name.strip() and names[e.name.strip().lower()] > 1:
            add("info", "I_DUP_NAME", e.id, f"Название «{e.name}» повторяется у нескольких шагов.",
                "Если это разные действия — уточните названия; если одно — используйте один шаг с циклом.")
            names[e.name.strip().lower()] = 0          # report once per name
    return issues


def _predecessor_participant(g: IRGraph, n: str) -> str | None:
    for f in g.inc[n]:
        src = g.elements.get(f.source)
        if src and src.participant:
            return src.participant
    for f in g.out[n]:
        tgt = g.elements.get(f.target)
        if tgt and tgt.participant:
            return tgt.participant
    return None


def _join_of(g: IRGraph, split: str) -> tuple[str | None, list]:
    """First node reachable from every branch of a split (ignoring loop edges) and the branch
    edges that enter it."""
    back = g.back_edges()
    branches = [f.target for f in g.out[split]]
    orders = []
    for b in branches:
        order, seen, stack = [], set(), [b]
        while stack:
            v = stack.pop(0)
            if v in seen:
                continue
            seen.add(v)
            order.append(v)
            stack.extend(f.target for f in g.out[v] if (f.source, f.target) not in back)
        orders.append(order)
    if not orders:
        return None, []
    common = [n for n in orders[0] if all(n in o for o in orders[1:])]
    if not common:
        return None, []
    join = common[0]
    region = set()
    for o in orders:
        region |= set(o[:o.index(join)])
    entering = [f for f in g.inc[join] if f.source in region or f.source == split]
    return join, entering


def _check_and_split(g: IRGraph, n: str, add) -> None:
    join, entering = _join_of(g, n)
    if join is None:
        add("warning", "W_AND_NO_JOIN", n, f"Параллельные ветви шлюза {g.label(n)} не сходятся.",
            "Добавьте параллельный шлюз слияния перед общим продолжением или подтвердите, "
            "что ветви завершаются независимо.")
    elif g.type(join) != "parallel_gateway" and not g.is_end(join):
        add("warning", "W_AND_NO_JOIN", n, f"Ветви параллельного шлюза {g.label(n)} сходятся в "
            f"{g.label(join)} без параллельного слияния — шаг выполнится несколько раз.",
            "Добавьте параллельный шлюз слияния перед этим шагом.",
            fixable=len(entering) == len(g.out[n]) and len(entering) > 1)


# ------------------------------------------------------------------------------------- fixes
def autofix(plan: Plan, codes: set[str] | None = None, elements: set[str] | None = None
            ) -> tuple[Plan, list[str]]:
    """Apply safe fixes (optionally only for given issue codes / elements). Returns (plan, applied)."""
    applied: list[str] = []
    data = plan.model_dump(by_alias=True)
    for _ in range(6):                    # fixes can unlock others; iterate to a fixpoint
        current = Plan.model_validate(data)
        todo = [i for i in lint(current) if i.fixable and (codes is None or i.code in codes)
                and (elements is None or i.element in elements or i.element is None)]
        if not todo:
            break
        changed = False
        for issue in todo:
            msg = _apply(data, issue, IRGraph.build(Plan.model_validate(data)))
            if msg:
                applied.append(msg)
                changed = True
                break                     # graph changed: re-lint before the next fix
        if not changed:
            break
    result = Plan.model_validate(data)
    check_plan(result)
    return result, applied


def _flows(data, src=None, dst=None):
    return [f for f in data["flows"] if (src is None or f["from"] == src) and (dst is None or f["to"] == dst)]


def _new_id(data, base: str) -> str:
    ids = {e["id"] for e in data["elements"]} | {"start", "end"}
    i, cand = 1, base
    while cand in ids:
        i += 1
        cand = f"{base}_{i}"
    return cand


def _apply(data: dict, issue: LintIssue, g: IRGraph) -> str | None:
    n = issue.element
    if issue.code == "E_NO_START":
        firsts = [e["id"] for e in data["elements"] if not e.get("parent") and not g.inc[e["id"]]
                  and e["type"] != "end_event"]
        if firsts:
            data["flows"].insert(0, {"from": "start", "to": firsts[0]})
            return f"Начало процесса связано с шагом {g.label(firsts[0])}"
    if issue.code in ("E_NO_END",):
        reach = g.reachable(g.starts(TOP))
        sinks = [e["id"] for e in data["elements"] if not e.get("parent") and not g.out[e["id"]]
                 and e["type"] != "end_event" and e["id"] in reach]
        for s in sinks:
            data["flows"].append({"from": s, "to": "end"})
        if sinks:
            return "К концу процесса подключены: " + ", ".join(g.label(s) for s in sinks)
    if issue.code == "E_DEAD_END" and n:
        data["flows"].append({"from": n, "to": "end"})
        return f"Тупик {g.label(n)} подключён к концу процесса"
    if issue.code == "E_END_OUTGOING" and n:
        data["flows"] = [f for f in data["flows"] if f["from"] != n]
        return f"Удалены исходящие связи конечного события {g.label(n)}"
    if issue.code == "E_NO_INCOMING" and n and not g.out[n]:
        data["elements"] = [e for e in data["elements"] if e["id"] != n]
        return f"Удалён изолированный элемент {g.label(n)}"
    if issue.code == "W_TRIVIAL_GATEWAY" and n:
        fin, fout = _flows(data, dst=n)[0], _flows(data, src=n)[0]
        data["flows"] = [f for f in data["flows"] if f is not fin and f is not fout]
        data["flows"].append({"from": fin["from"], "to": fout["to"], "label": fin.get("label")})
        data["elements"] = [e for e in data["elements"] if e["id"] != n]
        return f"Удалён лишний шлюз {g.label(n)}"
    if issue.code == "W_NO_CONDITION" and n:
        unl = [f for f in _flows(data, src=n) if not f.get("label") and not f.get("default")]
        if len(unl) == 1:
            unl[0]["label"], unl[0]["default"] = "Иначе", True
            return f"Неподписанная ветка шлюза {g.label(n)} отмечена как «Иначе» (по умолчанию)"
    if issue.code == "I_NO_DEFAULT" and n:
        neg = [f for f in _flows(data, src=n) if f.get("label") and NEGATIVE_LABEL.search(f["label"] + " ")]
        if len(neg) == 1:
            neg[0]["default"] = True
            return f"Ветка «{neg[0]['label']}» шлюза {g.label(n)} отмечена как ветка по умолчанию"
    if issue.code == "E_DEADLOCK" and n:
        for e in data["elements"]:
            if e["id"] == n:
                e["type"] = "exclusive_gateway"
        return f"Слияние {g.label(n)} заменено на исключающий шлюз (устранён deadlock)"
    if issue.code == "W_NO_PERFORMER" and n:
        p = _predecessor_participant(g, n)
        for e in data["elements"]:
            if e["id"] == n and p:
                e["participant"] = p
                e["assumption"] = (e.get("assumption") or "") + " Исполнитель назначен по соседнему шагу."
                return f"Шагу {g.label(n)} назначен исполнитель по соседнему шагу"
    if issue.code == "W_AND_NO_JOIN" and n:
        join, entering = _join_of(g, n)
        if join and len(entering) == len(g.out[n]):
            jid = _new_id(data, f"gw_join_{n}")
            split = next(e for e in data["elements"] if e["id"] == n)
            data["elements"].append({"id": jid, "type": "parallel_gateway", "name": "",
                                     "participant": split.get("participant"), "parent": split.get("parent")})
            keys = {(f.source, f.target) for f in entering}
            for f in data["flows"]:
                if (f["from"], f["to"]) in keys:
                    f["to"] = jid
            data["flows"].append({"from": jid, "to": join})
            return f"Добавлено параллельное слияние перед {g.label(join)}"
    if issue.code == "W_CROSS_POOL_FLOW" and n:
        moved = [f for f in data["flows"] if f["from"] == n]
        for f in moved:
            tgt = next((e for e in data["elements"] if e["id"] == f["to"]), None)
            src = next(e for e in data["elements"] if e["id"] == n)
            if tgt and tgt.get("participant") != src.get("participant"):
                data["flows"].remove(f)
                data.setdefault("message_flows", []).append({"from": f["from"], "to": f["to"],
                                                             "label": f.get("label")})
                return f"Связь {g.label(n)} → {g.label(f['to'])} между организациями стала сообщением"
    return None


# ---------------------------------------------------------------------------------- questions
def questions(plan: Plan, issues: list[LintIssue] | None = None) -> list[dict]:
    """Clarifying questions: from lint gaps (deterministic) + those the model asked itself."""
    issues = lint(plan) if issues is None else issues
    out, seen = [], set()
    for i in issues:
        if i.question and i.question not in seen:
            seen.add(i.question)
            out.append({"question": i.question, "element": i.element, "code": i.code,
                        "blocking": i.blocking, "source": "linter"})
    for q in plan.questions:
        if q not in seen:
            seen.add(q)
            out.append({"question": q, "element": None, "code": "LLM", "blocking": False, "source": "model"})
    return out


def summary(issues: list[LintIssue]) -> dict:
    c = Counter(i.level for i in issues)
    return {"errors": c["error"], "warnings": c["warning"], "info": c["info"],
            "fixable": sum(1 for i in issues if i.fixable)}
