"""Semantic validation and deterministic normalization of a Diagram.

`normalize` applies safe automatic fixes (reported as level="fix").
`validate` returns errors (block the result -> repair loop) and warnings.
Messages are phrased so they can be fed straight back to the LLM.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field

from .diagram import SUBPROCESS_KIND, Diagram


@dataclass
class Issue:
    level: str          # "error" | "warning" | "fix"
    code: str
    message: str
    elements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _label(d: Diagram, nid: str) -> str:
    n = d.nodes.get(nid)
    if n is None:
        p = d.pools.get(nid)
        return f"пул «{p.name}»" if p else nid
    return f"{nid} «{n.name}»" if n.name else nid


def _flow_nodes(d: Diagram, container: str):
    return [n for n in d.nodes.values() if n.container == container]


def _containers(d: Diagram) -> list[str]:
    return list(d.processes) + [n.id for n in d.nodes.values() if n.kind == SUBPROCESS_KIND]


def _remove_node(d: Diagram, nid: str) -> None:
    d.nodes.pop(nid, None)
    for fid in [f.id for f in d.flows.values() if f.source == nid or f.target == nid]:
        d.flows.pop(fid)
    for aid in [a.id for a in d.annotations.values() if a.target == nid]:
        d.annotations.pop(aid)


# --------------------------------------------------------------------------- normalize
def normalize(d: Diagram, lenient: bool = False) -> list[Issue]:
    """Apply deterministic fixes. With lenient=True also patch structural gaps
    (used after the repair loop is exhausted so the user still gets a diagram)."""
    fixes: list[Issue] = []

    # 0. once pools exist, every process with content must be in a pool
    if d.pools:
        for proc in d.processes.values():
            if proc.pool is None and any(n.container == proc.id for n in d.nodes.values()):
                pool_id = d._id("Participant")
                from .diagram import Pool
                d.pools[pool_id] = Pool(pool_id, proc.name, proc.id)
                proc.pool = pool_id
                fixes.append(Issue("fix", "F_ADD_POOL", f"Процесс «{proc.name}» помещён в отдельный пул", [pool_id]))

    # 1. duplicate sequence flows
    seen: dict[tuple[str, str, str], str] = {}
    for f in list(d.flows.values()):
        key = (f.kind, f.source, f.target)
        if key in seen:
            keep = d.flows[seen[key]]
            if not keep.name and f.name:
                keep.name = f.name
            d.flows.pop(f.id)
            fixes.append(Issue("fix", "F_DUP_FLOW", f"Удалена дублирующая связь {_label(d, f.source)} → "
                                                    f"{_label(d, f.target)}", [f.id]))
        else:
            seen[key] = f.id

    # 2. flows that cross pools must be message flows; message flows inside one pool -> sequence
    for f in list(d.flows.values()):
        if f.kind == "sequence":
            if d.process_of(f.source) != d.process_of(f.target):
                ps, pt = d.pool_of_ref(f.source), d.pool_of_ref(f.target)
                if ps and pt and ps != pt:
                    f.kind = "message"
                    fixes.append(Issue("fix", "F_SEQ_TO_MSG", f"Связь между пулами {_label(d, f.source)} → "
                                       f"{_label(d, f.target)} преобразована в поток сообщений", [f.id]))
        else:
            ps, pt = d.pool_of_ref(f.source), d.pool_of_ref(f.target)
            if (ps == pt and f.source in d.nodes and f.target in d.nodes
                    and d.nodes[f.source].container == d.nodes[f.target].container):
                f.kind = "sequence"
                fixes.append(Issue("fix", "F_MSG_TO_SEQ", f"Поток сообщений внутри одного пула "
                                   f"{_label(d, f.source)} → {_label(d, f.target)} заменён на поток управления",
                                   [f.id]))

    # 3. unused predefined start/end when the model created its own
    rs, re_ = d.root_start, d.root_end
    if rs in d.nodes and not d.outgoing(rs) and not d.incoming(rs):
        others = [n for n in _flow_nodes(d, d.nodes[rs].container) if n.kind == "startEvent" and n.id != rs]
        if others:
            _remove_node(d, rs)
            fixes.append(Issue("fix", "F_UNUSED_START", "Удалено неиспользуемое стартовое событие по умолчанию"))
    if re_ in d.nodes and not d.incoming(re_) and not d.outgoing(re_):
        others = [n for n in _flow_nodes(d, d.nodes[re_].container) if n.kind == "endEvent" and n.id != re_]
        if others:
            _remove_node(d, re_)
            fixes.append(Issue("fix", "F_UNUSED_END", "Удалено неиспользуемое конечное событие по умолчанию"))

    # 4. implicit start/end events inside subprocesses and secondary pools
    for c in _containers(d):
        nodes = _flow_nodes(d, c)
        if not nodes:
            continue
        is_sub = c in d.nodes
        if not is_sub and not lenient and c == d.root_process:
            continue  # main process: missing start/end goes to the repair loop
        if not any(n.kind == "startEvent" for n in nodes):
            heads = [n for n in nodes if not d.incoming(n.id) and n.kind not in ("endEvent",)]
            if heads:
                parent = c if is_sub else (heads[0].lane or c)
                sid = d._add_node("startEvent", "", parent, "StartEvent", auto=True)
                for h in heads:
                    d.add_link(sid, h.id)
                fixes.append(Issue("fix", "F_ADD_START", f"Добавлено стартовое событие в "
                                   f"{'подпроцесс' if is_sub else 'процесс'} {_container_label(d, c)}", [sid]))
        nodes = _flow_nodes(d, c)
        if not any(n.kind == "endEvent" for n in nodes):
            tails = [n for n in nodes if not d.outgoing(n.id) and n.kind != "startEvent"]
            if tails:
                parent = c if is_sub else (tails[-1].lane or c)
                eid = d._add_node("endEvent", "", parent, "EndEvent", auto=True)
                for t in tails:
                    d.add_link(t.id, eid)
                fixes.append(Issue("fix", "F_ADD_END", f"Добавлено конечное событие в "
                                   f"{'подпроцесс' if is_sub else 'процесс'} {_container_label(d, c)}", [eid]))

    if lenient:
        fixes += _lenient_fixes(d)

    # 5. lane assignment for lane-less nodes in processes that have lanes
    for pid, proc in d.processes.items():
        if not proc.lanes:
            for n in _flow_nodes(d, pid):
                n.lane = None
            continue
        pending = [n for n in _flow_nodes(d, pid) if n.lane is None]
        changed = True
        while pending and changed:
            changed = False
            for n in list(pending):
                neigh = [d.nodes[f.target].lane for f in d.outgoing(n.id) if d.nodes[f.target].lane] + \
                        [d.nodes[f.source].lane for f in d.incoming(n.id) if d.nodes[f.source].lane]
                if n.kind == "endEvent":
                    neigh = [d.nodes[f.source].lane for f in d.incoming(n.id) if d.nodes[f.source].lane] or neigh
                if neigh:
                    n.lane = neigh[0]
                    pending.remove(n)
                    changed = True
        for n in pending:
            n.lane = proc.lanes[0]

    # 6. empty lanes / groups
    for pid, proc in d.processes.items():
        used = {n.lane for n in _flow_nodes(d, pid)}
        for lid in list(proc.lanes):
            if lid not in used and len(proc.lanes) > 1:
                proc.lanes.remove(lid)
                lane = d.lanes.pop(lid)
                fixes.append(Issue("fix", "F_EMPTY_LANE", f"Удалена пустая дорожка «{lane.name}»", [lid]))
    for gid in list(d.groups):
        if not any(n.group == gid for n in d.nodes.values()):
            g = d.groups.pop(gid)
            fixes.append(Issue("fix", "F_EMPTY_GROUP", f"Удалена пустая группа «{g.name}»", [gid]))
    return fixes


def _container_label(d: Diagram, c: str) -> str:
    if c in d.nodes:
        return f"«{d.nodes[c].name}»"
    return f"«{d.processes[c].name}»"


def _lenient_fixes(d: Diagram) -> list[Issue]:
    """Last-resort structural patches so that a usable diagram is always produced."""
    fixes: list[Issue] = []
    for c in _containers(d):
        nodes = _flow_nodes(d, c)
        if not nodes:
            continue
        starts = [n for n in nodes if n.kind == "startEvent"]
        ends = [n for n in nodes if n.kind == "endEvent"]
        for n in nodes:
            if n.kind == "startEvent":
                continue
            if not d.incoming(n.id) and starts and n.kind != "endEvent":
                d.add_link(starts[0].id, n.id)
                fixes.append(Issue("fix", "F_LINK_ORPHAN", f"Узел {_label(d, n.id)} без входящих связей "
                                   "подключён к стартовому событию", [n.id]))
        for n in nodes:
            if n.kind == "endEvent":
                continue
            if not d.outgoing(n.id) and ends:
                d.add_link(n.id, ends[0].id)
                fixes.append(Issue("fix", "F_LINK_DANGLING", f"Узел {_label(d, n.id)} без исходящих связей "
                                   "подключён к конечному событию", [n.id]))
        for n in nodes:
            if n.kind == "startEvent":
                for f in d.incoming(n.id):
                    d.flows.pop(f.id)
                    fixes.append(Issue("fix", "F_START_IN", "Удалена входящая связь стартового события", [f.id]))
            if n.kind == "endEvent":
                for f in d.outgoing(n.id):
                    d.flows.pop(f.id)
                    fixes.append(Issue("fix", "F_END_OUT", "Удалена исходящая связь конечного события", [f.id]))
    return fixes


# --------------------------------------------------------------------------- validate
def _nearest_split(d: Diagram, flow_source: str, limit: int = 200) -> str | None:
    """Walk backwards from a flow source to the nearest split gateway."""
    cur, steps, seen = flow_source, 0, set()
    while steps < limit and cur not in seen:
        seen.add(cur)
        n = d.nodes[cur]
        if n.is_gateway and len(d.outgoing(cur)) > 1:
            return cur
        ins = d.incoming(cur)
        if len(ins) != 1:
            return None
        cur = ins[0].source
        steps += 1
    return None


def validate(d: Diagram) -> list[Issue]:
    issues: list[Issue] = []
    E = lambda code, msg, els=(): issues.append(Issue("error", code, msg, list(els)))  # noqa: E731
    W = lambda code, msg, els=(): issues.append(Issue("warning", code, msg, list(els)))  # noqa: E731

    work_nodes = [n for n in d.nodes.values() if not n.is_event]
    if not work_nodes:
        E("E_EMPTY", "Диаграмма не содержит ни одной задачи или шлюза — процесс не построен")

    for f in d.sequence_flows():
        cs, ct = d.nodes[f.source].container, d.nodes[f.target].container
        if cs != ct:
            E("E_CROSS_CONTAINER",
              f"Связь {_label(d, f.source)} → {_label(d, f.target)} пересекает границу подпроцесса. "
              "Связывайте снаружи сам подпроцесс, а внутри — его элементы.", [f.id])

    for c in _containers(d):
        nodes = _flow_nodes(d, c)
        if not nodes:
            if c in d.nodes:
                W("W_EMPTY_SUBPROCESS", f"Подпроцесс {_label(d, c)} пуст", [c])
            continue
        where = f"в подпроцессе {_container_label(d, c)}" if c in d.nodes else f"в процессе {_container_label(d, c)}"
        starts = [n for n in nodes if n.kind == "startEvent"]
        ends = [n for n in nodes if n.kind == "endEvent"]
        if not starts:
            E("E_NO_START", f"Нет стартового события {where}")
        if not ends:
            E("E_NO_END", f"Нет конечного события {where}")
        for n in nodes:
            ins, outs = d.incoming(n.id), d.outgoing(n.id)
            if n.kind == "startEvent":
                if ins:
                    E("E_START_INCOMING", f"У стартового события {_label(d, n.id)} есть входящие связи", [n.id])
                if not outs:
                    E("E_DANGLING_OUT", f"Стартовое событие {_label(d, n.id)} ни с чем не связано. "
                                        "Добавьте DIAGRAM.add_link от него к первому шагу.", [n.id])
                continue
            if n.kind == "endEvent":
                if outs:
                    E("E_END_OUTGOING", f"У конечного события {_label(d, n.id)} есть исходящие связи", [n.id])
                if not ins:
                    E("E_DANGLING_IN", f"В конечное событие {_label(d, n.id)} не ведёт ни одна связь", [n.id])
                continue
            if not ins:
                E("E_DANGLING_IN", f"В узел {_label(d, n.id)} не ведёт ни одна связь — он недостижим", [n.id])
            if not outs:
                E("E_DANGLING_OUT", f"Из узла {_label(d, n.id)} не выходит ни одной связи — процесс обрывается. "
                                    "Свяжите его со следующим шагом или с конечным событием.", [n.id])
            if not n.name and not n.is_gateway:
                W("W_NO_NAME", f"У элемента {n.id} нет названия", [n.id])
            if n.is_task and len(n.name) > 80:
                W("W_LONG_NAME", f"Слишком длинное название задачи {n.id} ({len(n.name)} символов)", [n.id])
            if n.is_gateway:
                if len(ins) <= 1 and len(outs) <= 1 and ins and outs:
                    W("W_TRIVIAL_GATEWAY", f"Шлюз {_label(d, n.id)} имеет один вход и один выход — он лишний",
                      [n.id])
                if len(ins) > 1 and len(outs) > 1:
                    W("W_MIXED_GATEWAY", f"Шлюз {_label(d, n.id)} одновременно сливает и разветвляет потоки; "
                                         "лучше разделить на два шлюза", [n.id])
                if n.kind in ("exclusiveGateway", "inclusiveGateway") and len(outs) > 1:
                    unlabeled = [f for f in outs if not f.name and not f.default]
                    if unlabeled:
                        W("W_UNLABELED_BRANCH", f"Не все ветви шлюза {_label(d, n.id)} подписаны условием "
                                                "(третий аргумент add_link)", [f.id for f in unlabeled])
                if n.kind == "eventBasedGateway":
                    for f in outs:
                        t = d.nodes[f.target]
                        if t.kind not in ("intermediateCatchEvent", "receiveTask"):
                            E("E_EVENT_GATEWAY_TARGET", f"После событийного шлюза {_label(d, n.id)} должны идти "
                                                        f"промежуточные события, а не {_label(d, t.id)}", [f.id])

        # reachability from starts
        if starts:
            seen = set()
            q = deque(s.id for s in starts)
            while q:
                cur = q.popleft()
                if cur in seen:
                    continue
                seen.add(cur)
                q.extend(f.target for f in d.outgoing(cur))
            unreachable = [n for n in nodes if n.id not in seen and d.incoming(n.id)]
            for n in unreachable:
                E("E_UNREACHABLE", f"Узел {_label(d, n.id)} недостижим из стартового события (замкнутый цикл "
                                   "без входа)", [n.id])

    # join/split consistency
    for n in d.nodes.values():
        if not n.is_gateway or len(d.incoming(n.id)) < 2:
            continue
        splits = {_nearest_split(d, f.source) for f in d.incoming(n.id)}
        splits.discard(None)
        if len(splits) == 1:
            s = d.nodes[splits.pop()]
            if n.kind == "parallelGateway" and s.kind == "exclusiveGateway":
                E("E_DEADLOCK", f"Параллельный шлюз-слияние {_label(d, n.id)} ждёт все ветви, но они "
                                f"порождены исключающим шлюзом {_label(d, s.id)} — процесс зависнет. "
                                "Используйте add_exclusive_gateway для слияния.", [n.id, s.id])
            elif n.kind == "exclusiveGateway" and s.kind == "parallelGateway":
                W("W_MULTI_MERGE", f"Ветви параллельного шлюза {_label(d, s.id)} сливаются исключающим шлюзом "
                                   f"{_label(d, n.id)} — следующие шаги выполнятся несколько раз. "
                                   "Обычно для слияния используют add_parallel_gateway.", [n.id, s.id])

    for f in d.message_flows():
        if d.pool_of_ref(f.source) is None or d.pool_of_ref(f.target) is None:
            E("E_MESSAGE_NO_POOL", "Потоки сообщений допустимы только между пулами; создайте пулы через add_pool",
              [f.id])
    return issues


def errors_of(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


def format_issues_for_llm(issues: list[Issue]) -> str:
    return "\n".join(f"- [{i.code}] {i.message}" for i in issues if i.level == "error")
