"""Graph view of the IR (Plan) used by the linter, analytics, simulation and path enumeration."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from ..llm.plan import Element, Plan

TASK_TYPES = {"task", "user_task", "service_task", "script_task", "manual_task", "send_task",
              "receive_task", "business_rule_task", "subprocess"}
GATEWAY_TYPES = {"exclusive_gateway", "parallel_gateway", "inclusive_gateway", "event_based_gateway"}
CONDITIONAL = {"exclusive_gateway", "inclusive_gateway"}
TOP = "__top__"


@dataclass
class IRGraph:
    plan: Plan
    elements: dict[str, Element] = field(default_factory=dict)
    out: dict[str, list] = field(default_factory=lambda: defaultdict(list))   # id -> [FlowSpec]
    inc: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    container: dict[str, str] = field(default_factory=dict)                    # id -> parent or TOP
    attached: dict[str, list] = field(default_factory=lambda: defaultdict(list))  # host -> boundary ids

    @classmethod
    def build(cls, plan: Plan) -> "IRGraph":
        g = cls(plan)
        g.elements = {e.id: e for e in plan.elements}
        for e in plan.elements:
            g.container[e.id] = e.parent or TOP
        g.container["start"] = g.container["end"] = TOP
        for f in plan.flows:
            g.out[f.source].append(f)
            g.inc[f.target].append(f)
        for e in plan.elements:
            if e.type == "boundary_timer" and e.attached_to:
                g.attached[e.attached_to].append(e.id)
        return g

    # --------------------------------------------------------------- basics
    def type(self, nid: str) -> str:
        if nid == "start":
            return "start_event"
        if nid == "end":
            return "end_event"
        return self.elements[nid].type if nid in self.elements else "?"

    def name(self, nid: str) -> str:
        if nid in ("start", "end"):
            return "Начало" if nid == "start" else "Конец"
        e = self.elements.get(nid)
        if e and not e.name:
            return {"parallel_gateway": "параллельный шлюз", "exclusive_gateway": "слияние ветвей",
                    "inclusive_gateway": "инклюзивный шлюз", "end_event": "завершение",
                    "start_event": "начало"}.get(e.type, e.id)
        return e.name if e else nid

    def label(self, nid: str) -> str:
        return f"{nid} «{self.name(nid)}»"

    def nodes_in(self, container: str) -> list[str]:
        ids = [e.id for e in self.plan.elements if (e.parent or TOP) == container]
        if container == TOP:
            ids = ["start"] + ids + ["end"]
        return ids

    def containers(self) -> list[str]:
        return [TOP] + [e.id for e in self.plan.elements if e.type == "subprocess"]

    def is_start(self, nid: str) -> bool:
        return nid == "start" or self.type(nid) == "start_event"

    def is_end(self, nid: str) -> bool:
        return nid == "end" or self.type(nid) == "end_event"

    def used_reserved(self) -> set[str]:
        used = set()
        for f in self.plan.flows:
            used |= {f.source, f.target} & {"start", "end"}
        return used

    def starts(self, container: str = TOP) -> list[str]:
        nodes = self.nodes_in(container)
        if container != TOP:          # subprocess: implicit start before nodes without incoming
            return [n for n in nodes if not self.inc[n] and self.type(n) != "boundary_timer"]
        res = [n for n in nodes if self.type(n) == "start_event" and n != "start"]
        if self.out["start"] or not res:
            res = ["start"] + res
        return res

    # --------------------------------------------------------------- traversal
    def reachable(self, sources, skip_back: bool = False) -> set[str]:
        seen, q = set(), deque(sources)
        while q:
            cur = q.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            q.extend(f.target for f in self.out[cur])
            q.extend(self.attached.get(cur, []))
        return seen

    def back_edges(self) -> set[tuple[str, str]]:
        """Edges closing a cycle in a DFS from the start nodes (loops / rework)."""
        state: dict[str, int] = {}
        back: set[tuple[str, str]] = set()
        roots = []
        for c in self.containers():
            roots += self.starts(c)
        roots += [e.id for e in self.plan.elements]
        for r in roots:
            if r in state:
                continue
            stack = [(r, iter(self.out[r]))]
            state[r] = 1
            while stack:
                v, it = stack[-1]
                f = next(it, None)
                if f is None:
                    state[v] = 2
                    stack.pop()
                    continue
                t = f.target
                if state.get(t) == 1:
                    back.add((f.source, f.target))
                elif t not in state:
                    state[t] = 1
                    stack.append((t, iter(self.out[t])))
        return back

    def topo_order(self, container: str = TOP) -> list[str]:
        """Nodes of a container in execution order (DAG after removing loop edges)."""
        back = self.back_edges()
        nodes = self.nodes_in(container)
        node_set = set(nodes)
        indeg = {n: 0 for n in nodes}
        for f in self.plan.flows:
            if f.source in node_set and f.target in node_set and (f.source, f.target) not in back:
                indeg[f.target] += 1
        for host, bs in self.attached.items():
            for b in bs:
                if b in indeg and host in node_set:
                    indeg[b] += 1
        order, q = [], deque([n for n in nodes if indeg[n] == 0])
        seen = set()
        while q:
            v = q.popleft()
            if v in seen:
                continue
            seen.add(v)
            order.append(v)
            for f in self.out[v]:
                if (f.source, f.target) in back or f.target not in node_set:
                    continue
                indeg[f.target] -= 1
                if indeg[f.target] <= 0:
                    q.append(f.target)
            for b in self.attached.get(v, []):
                if b in indeg:
                    indeg[b] -= 1
                    if indeg[b] <= 0:
                        q.append(b)
        order += [n for n in nodes if n not in seen]
        if "end" in order:                       # the reserved end goes last
            order.remove("end")
            order.append("end")
        return order

    def nearest_split(self, nid: str, limit: int = 300) -> str | None:
        cur, seen = nid, set()
        for _ in range(limit):
            if cur in seen:
                return None
            seen.add(cur)
            if self.type(cur) in GATEWAY_TYPES and len(self.out[cur]) > 1:
                return cur
            ins = self.inc[cur]
            if len(ins) != 1:
                return None
            cur = ins[0].source
        return None

    def numbered_steps(self) -> list[tuple[int, str]]:
        """Tasks/events in execution order with 1-based numbers (for «после шага 3»)."""
        steps = []
        for c in self.containers():
            for n in self.topo_order(c):
                if n in self.elements and self.type(n) not in GATEWAY_TYPES:
                    steps.append(n)
        return [(i + 1, n) for i, n in enumerate(steps)]
