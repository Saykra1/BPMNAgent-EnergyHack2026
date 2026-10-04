"""Token engine over the IR: shared by Monte Carlo simulation and test-path enumeration.

Semantics (simplified BPMN): a task/event delays the token by wait + duration and passes it on;
exclusive / event-based gateways pick one branch (via `choose`); parallel gateways split to all
branches and join by waiting for all incoming flows; inclusive gateways split to a chosen subset and
the matching join waits for exactly that many tokens. Loops are bounded by `max_visits`.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from ..llm.plan import Plan
from .graph import IRGraph
from .lint import _join_of


class NeedChoice(Exception):
    def __init__(self, gateway: str, options: list[tuple]):
        self.gateway, self.options = gateway, options


@dataclass
class Trace:
    total: float = 0.0
    visits: list[str] = field(default_factory=list)                 # nodes in completion order
    decisions: list[tuple[str, tuple[int, ...]]] = field(default_factory=list)
    flows: list[tuple[str, str]] = field(default_factory=list)
    busy: dict[str, float] = field(default_factory=dict)              # participant -> work minutes
    node_time: dict[str, float] = field(default_factory=dict)         # node -> minutes spent (last visit)
    ends: list[str] = field(default_factory=list)
    truncated: bool = False


class Engine:
    def __init__(self, plan: Plan, max_visits: int = 3):
        self.plan = plan
        self.g = IRGraph.build(plan)
        self.max_visits = max_visits
        self.back = self.g.back_edges()
        self.or_join: dict[str, str] = {}
        for e in plan.elements:
            if e.type == "inclusive_gateway" and len(self.g.out[e.id]) > 1:
                join, _ = _join_of(self.g, e.id)
                if join:
                    self.or_join[e.id] = join
        # boundary timers attached to tasks (escalations), if the IR has them
        self.boundaries: dict[str, list] = {}
        for e in plan.elements:
            host = getattr(e, "attached_to", None)
            if e.type == "boundary_timer" and host:
                self.boundaries.setdefault(host, []).append(e)

    def options(self, gw: str) -> list[tuple]:
        """Choices at a gateway as tuples of outgoing flow indexes."""
        outs = self.g.out[gw]
        t = self.g.type(gw)
        if t == "inclusive_gateway":
            n = min(len(outs), 4)
            return [tuple(i for i in range(n) if mask >> i & 1) for mask in range(1, 2 ** n)]
        return [(i,) for i in range(len(outs))]

    def run(self, choose, duration, start_nodes: list[str] | None = None) -> Trace:
        """choose(gateway, options, visit_no) -> option; duration(node) -> (wait, work) minutes."""
        g = self.g
        tr = Trace()
        visits: dict[str, int] = {}
        join_count: dict[str, list[float]] = {}
        or_expected: dict[str, int] = {}
        participant = {e.id: e.participant for e in self.plan.elements}
        starts = start_nodes or [s for s in g.starts() if g.out[s]] or g.starts()
        heap: list[tuple[float, int, str]] = []
        seq = 0
        for s in starts:
            heapq.heappush(heap, (0.0, seq, s))
            seq += 1
        while heap:
            t, _, node = heapq.heappop(heap)
            typ = g.type(node)
            ins = g.inc[node]
            # --- joins ------------------------------------------------------------------------
            if typ == "parallel_gateway" and len(ins) > 1:
                arrived = join_count.setdefault(node, [])
                arrived.append(t)
                if len(arrived) < len(ins):
                    continue
                t = max(arrived)
                join_count[node] = []
            elif typ == "inclusive_gateway" and len(ins) > 1 and node in or_expected:
                arrived = join_count.setdefault(node, [])
                arrived.append(t)
                if len(arrived) < or_expected[node]:
                    continue
                t = max(arrived)
                join_count[node] = []
                del or_expected[node]
            visits[node] = visits.get(node, 0) + 1
            if visits[node] > self.max_visits + 1:
                tr.truncated = True
                continue
            # --- work -------------------------------------------------------------------------
            wait, work = duration(node) if node in g.elements else (0.0, 0.0)
            done = t + wait + work
            if work and participant.get(node):
                tr.busy[participant[node]] = tr.busy.get(participant[node], 0.0) + work
            if node in g.elements:
                tr.node_time[node] = wait + work
                tr.visits.append(node)
                for b in self.boundaries.get(node, []):
                    limit = (b.sla_hours or 0) * 60
                    if limit and wait + work > limit:      # SLA breached -> escalation path starts
                        tr.visits.append(b.id)
                        for f in g.out[b.id]:
                            tr.flows.append((b.id, f.target))
                            heapq.heappush(heap, (t + limit, seq, f.target))
                            seq += 1
            outs = g.out[node]
            if g.is_end(node) or not outs:
                tr.ends.append(node)
                tr.total = max(tr.total, done)
                continue
            # --- routing ----------------------------------------------------------------------
            if typ in ("exclusive_gateway", "event_based_gateway", "inclusive_gateway") and len(outs) > 1:
                opts = self.options(node)
                # never take a loop edge more often than allowed
                if visits[node] > self.max_visits:
                    safe = [o for o in opts if all((node, outs[i].target) not in self.back for i in o)]
                    opts = safe or opts
                pick = choose(node, opts, visits[node])
                tr.decisions.append((node, pick))
                chosen = [outs[i] for i in pick]
                if typ == "inclusive_gateway" and node in self.or_join:
                    or_expected[self.or_join[node]] = len(chosen)
            else:
                chosen = outs                                   # parallel split or plain sequence
            for f in chosen:
                tr.flows.append((node, f.target))
                heapq.heappush(heap, (done, seq, f.target))
                seq += 1
        return tr
