"""Deterministic lane-aware layered layout (Sugiyama-style) for BPMN.

Why not plain ELK: BPMN needs lanes/pools as first-class constraints, gateway
exits from top/bottom, loops routed around content and message flows between
pools. A small specialised layered layout gives stable, reproducible output
without a JS runtime on the backend.

Steps (per plane):
1. cycle breaking (DFS from start events, back edges = loops);
2. longest-path layering on the DAG (sequence + message flows), sources pulled right;
3. dummy nodes for edges spanning several layers (keeps branches from crossing tasks);
4. row assignment inside each lane: branches of a split go to consecutive rows,
   merges return to the topmost incoming row;
5. coordinates: columns sized by the widest element, lanes sized by row count;
6. orthogonal edge routing, loops routed through a corridor at the lane bottom.
Collapsed subprocesses get their own plane (drill-down in bpmn-js).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .diagram import SUBPROCESS_KIND, Diagram, Flow, Node

TASK_W, TASK_H = 120, 80
GW = 50
EV = 36
COL_GAP = 70
ROW_H = 110
LANE_TOP = 20
LANE_BOTTOM = 14
LOOP_STEP = 10
POOL_HEADER = 30
LANE_HEADER = 30
POOL_GAP = 50
CONTENT_PAD = 30
ORIGIN_X, ORIGIN_Y = 60, 60
DUMMY_W = 16


@dataclass
class Bounds:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h


@dataclass
class Plane:
    element: str | None                     # None => main plane (collaboration or root process)
    shapes: dict[str, Bounds] = field(default_factory=dict)
    edges: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    labels: dict[str, Bounds] = field(default_factory=dict)
    horizontal: set[str] = field(default_factory=set)   # pools/lanes drawn horizontally


@dataclass
class LayoutResult:
    planes: list[Plane]


def node_size(n: Node) -> tuple[int, int]:
    if n.is_task:
        return TASK_W, TASK_H
    if n.is_gateway:
        return GW, GW
    return EV, EV


# ----------------------------------------------------------------------------- engine
class _Engine:
    """Layering + row assignment for one plane."""

    def __init__(self, d: Diagram, node_ids: list[str], lane_of: dict[str, str]):
        self.d = d
        self.ids = node_ids
        self.idx = {n: i for i, n in enumerate(node_ids)}
        self.lane_of = lane_of
        idset = set(node_ids)
        self.seq = [f for f in d.sequence_flows() if f.source in idset and f.target in idset]
        # boundary events are layered right after their host (virtual edge, never drawn)
        self.seq += [Flow(f"__att_{n}", d.nodes[n].attached_to, n) for n in node_ids
                     if d.nodes[n].kind == "boundaryEvent" and d.nodes[n].attached_to in idset]
        self.msg = [f for f in d.message_flows() if f.source in idset and f.target in idset]
        self.back: set[str] = set()
        self.layer: dict[str, int] = {}
        self.row: dict[str, int] = {}          # item -> row (items = node ids or dummy ids)
        self.item_layer: dict[str, int] = {}
        self.item_lane: dict[str, str] = {}
        self.chains: dict[str, list[str]] = {}  # flow id -> dummy ids

    def run(self):
        self._break_cycles()
        self._layering()
        self._dummies()
        self._rows()
        return self

    def _break_cycles(self):
        out = defaultdict(list)
        for f in self.seq + self.msg:
            out[f.source].append(f)
        starts = [n for n in self.ids if self.d.nodes[n].kind == "startEvent"]
        no_in = [n for n in self.ids if not any(f.target == n for f in self.seq)]
        roots = starts + [n for n in no_in if n not in starts] + self.ids
        state: dict[str, int] = {}  # 1 = on stack, 2 = done
        for r in roots:
            if r in state:
                continue
            stack = [(r, iter(out[r]))]
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
                    self.back.add(f.id)
                elif t not in state:
                    state[t] = 1
                    stack.append((t, iter(out[t])))

    def _layering(self):
        fwd = [f for f in self.seq + self.msg if f.id not in self.back]
        preds, succs = defaultdict(list), defaultdict(list)
        for f in fwd:
            preds[f.target].append(f.source)
            succs[f.source].append(f.target)
        indeg = {n: len(preds[n]) for n in self.ids}
        order, queue = [], [n for n in self.ids if indeg[n] == 0]
        while queue:
            v = queue.pop(0)
            order.append(v)
            for s in succs[v]:
                indeg[s] -= 1
                if indeg[s] == 0:
                    queue.append(s)
        layer = {}
        for v in order:
            layer[v] = max((layer[p] + 1 for p in preds[v]), default=0)
        # pull sources (e.g. start events of secondary pools) next to their successor
        for v in reversed(order):
            if not preds[v] and succs[v]:
                layer[v] = max(layer[v], min(layer[s] for s in succs[v]) - 1)
        self.layer = layer
        self.fwd_seq = [f for f in self.seq if f.id not in self.back]

    def _dummies(self):
        for n in self.ids:
            self.item_layer[n] = self.layer[n]
            self.item_lane[n] = self.lane_of[n]
        for f in self.fwd_seq:
            ls, lt = self.layer[f.source], self.layer[f.target]
            chain = []
            for k in range(ls + 1, lt):
                did = f"__dummy_{f.id}_{k}"
                self.item_layer[did] = k
                self.item_lane[did] = self.lane_of[f.source]
                chain.append(did)
            self.chains[f.id] = chain

    def _rows(self):
        # item-level forward edges (through dummies), in creation order
        item_succ = defaultdict(list)
        item_pred = defaultdict(list)
        for f in self.fwd_seq:
            seq = [f.source] + self.chains[f.id] + [f.target]
            for a, b in zip(seq, seq[1:]):
                item_succ[a].append(b)
                item_pred[b].append(a)
        by_layer = defaultdict(list)
        for it, l in self.item_layer.items():
            by_layer[l].append(it)
        creation = {it: i for i, it in enumerate(self.item_layer)}
        for l in sorted(by_layer):
            prefs = {}
            for it in by_layer[l]:
                lane = self.item_lane[it]
                cands = []
                for p in item_pred[it]:
                    if p not in self.row or self.item_lane[p] != lane:
                        continue
                    same_lane_children = [c for c in item_succ[p] if self.item_lane[c] == lane]
                    off = same_lane_children.index(it) if it in same_lane_children else 0
                    cands.append(self.row[p] + off)
                prefs[it] = min(cands) if cands else 0
            groups = defaultdict(list)
            for it in by_layer[l]:
                groups[self.item_lane[it]].append(it)
            for lane, items in groups.items():
                items.sort(key=lambda x: (prefs[x], x.startswith("__dummy"), creation[x]))
                last = -1
                for it in items:
                    r = max(prefs[it], last + 1)
                    self.row[it] = r
                    last = r


# ----------------------------------------------------------------------------- planes
class _PlaneBuilder:
    def __init__(self, d: Diagram, node_ids: list[str], lanes: list[dict], plane: Plane, with_pools: bool):
        """lanes: ordered list of dicts {key, kind: 'lane'|'process'|'blackbox', pool, process}"""
        self.d = d
        self.node_ids = node_ids
        self.lanes = lanes
        self.p = plane
        self.with_pools = with_pools

    def build(self):
        d = self.d
        lane_of = {}
        for nid in self.node_ids:
            n = d.nodes[nid]
            lane_of[nid] = n.lane if n.lane else n.container
        eng = _Engine(d, self.node_ids, lane_of).run()
        self.eng = eng

        # column widths
        n_layers = max(eng.item_layer.values(), default=0) + 1
        col_w = [DUMMY_W] * n_layers
        for nid in self.node_ids:
            w, _ = node_size(d.nodes[nid])
            col_w[eng.layer[nid]] = max(col_w[eng.layer[nid]], w)
        any_lanes = any(ln["kind"] == "lane" for ln in self.lanes)
        x0 = ORIGIN_X + (POOL_HEADER if self.with_pools else 0) + (LANE_HEADER if any_lanes else 0) + CONTENT_PAD
        col_left, x = [], x0
        for w in col_w:
            col_left.append(x)
            x += w + COL_GAP
        content_right = x - COL_GAP + CONTENT_PAD
        self.col_left, self.col_w = col_left, col_w
        self.content_right = content_right

        # loops per lane (for bottom corridor)
        loops_by_lane = defaultdict(list)
        for f in eng.seq:
            if f.id in eng.back:
                ls, lt = lane_of[f.source], lane_of[f.target]
                key = ls if self._lane_index(ls) >= self._lane_index(lt) else lt
                loops_by_lane[key].append(f.id)
        # room above the first row for annotations, so a long footnote stays inside its lane
        annotated_top: dict[str, float] = {}
        for a in d.annotations.values():
            if a.target in lane_of and eng.row.get(a.target) == 0:
                key = lane_of[a.target]
                annotated_top[key] = max(annotated_top.get(key, 0), 55, _annotation_size(a.text)[1] - 1)

        # lane geometry
        rows_by_lane = defaultdict(int)
        for it, r in eng.row.items():
            rows_by_lane[eng.item_lane[it]] = max(rows_by_lane[eng.item_lane[it]], r + 1)
        y = ORIGIN_Y
        self.lane_box: dict[str, Bounds] = {}
        self.lane_top: dict[str, float] = {}
        pool_extent: dict[str, list[float]] = {}
        prev_pool = None
        for ln in self.lanes:
            key = ln["key"]
            if prev_pool is not None and ln["pool"] != prev_pool:
                y += POOL_GAP
            prev_pool = ln["pool"]
            if ln["kind"] == "blackbox":
                h = 70
            else:
                top = LANE_TOP + annotated_top.get(key, 0)
                h = top + max(rows_by_lane[key], 1) * ROW_H + LANE_BOTTOM + LOOP_STEP * len(loops_by_lane[key])
                self.lane_top[key] = y + top
            self.lane_box[key] = Bounds(0, y, 0, h)
            if ln["pool"]:
                ext = pool_extent.setdefault(ln["pool"], [y, y + h])
                ext[1] = y + h
            y += h
        self.loops_by_lane = loops_by_lane

        # pool / lane shapes
        if self.with_pools:
            for pool_id, (top, bottom) in pool_extent.items():
                self.p.shapes[pool_id] = Bounds(ORIGIN_X, top, content_right - ORIGIN_X, bottom - top)
                self.p.horizontal.add(pool_id)
        for ln in self.lanes:
            b = self.lane_box[ln["key"]]
            lx = ORIGIN_X + (POOL_HEADER if self.with_pools else 0)
            b.x, b.w = lx, content_right - lx
            if ln["kind"] == "lane":
                self.p.shapes[ln["key"]] = Bounds(b.x, b.y, b.w, b.h)
                self.p.horizontal.add(ln["key"])

        # node positions
        def center(it):
            l, lane = eng.item_layer[it], eng.item_lane[it]
            cx = col_left[l] + col_w[l] / 2
            cy = self.lane_top[lane] + eng.row[it] * ROW_H + ROW_H / 2
            return cx, cy

        self.center = center
        for nid in self.node_ids:
            w, h = node_size(d.nodes[nid])
            cx, cy = center(nid)
            self.p.shapes[nid] = Bounds(cx - w / 2, cy - h / 2, w, h)
        per_host = defaultdict(int)
        for nid in self.node_ids:                       # boundary events sit on the host's bottom border
            n = d.nodes[nid]
            if n.kind == "boundaryEvent" and n.attached_to in self.p.shapes:
                host = self.p.shapes[n.attached_to]
                k = per_host[n.attached_to]
                per_host[n.attached_to] += 1
                self.p.shapes[nid] = Bounds(host.right - EV - 8 - k * (EV + 6), host.bottom - EV / 2, EV, EV)

        self._route_sequence()
        self._route_messages()
        self._groups_and_annotations()
        self._node_labels()
        return self.p

    def _node_labels(self):
        """External labels of gateways/events: below by default, above if an edge uses the bottom."""
        S = self.p.shapes
        touch_bottom, touch_top = set(), set()
        for fid, pts in self.p.edges.items():
            f = self.d.flows.get(fid)
            if f is None:
                continue
            for nid, pt in ((f.source, pts[0]), (f.target, pts[-1])):
                b = S.get(nid)
                if b is None or nid not in self.d.nodes:
                    continue
                if abs(pt[1] - b.bottom) < 1 and abs(pt[0] - b.cx) < 1:
                    touch_bottom.add(nid)
                if abs(pt[1] - b.y) < 1 and abs(pt[0] - b.cx) < 1:
                    touch_top.add(nid)
        for nid in self.node_ids:
            n = self.d.nodes[nid]
            if n.is_task or not n.name:
                continue
            b = S[nid]
            w = 96
            lines = max(1, -(-len(n.name) * 6 // w))
            h = 14 * lines
            if n.kind == "boundaryEvent":
                self.p.labels[nid] = Bounds(b.right + 2, b.bottom - 4, w, h)
                continue
            if nid in touch_bottom and nid not in touch_top:
                self.p.labels[nid] = Bounds(b.cx - w / 2, b.y - h - 6, w, h)
            elif nid in touch_bottom and nid in touch_top:
                self.p.labels[nid] = Bounds(b.right + 4, b.y - h, w, h)
            else:
                self.p.labels[nid] = Bounds(b.cx - w / 2, b.bottom + 6, w, h)

    def _lane_index(self, key):
        for i, ln in enumerate(self.lanes):
            if ln["key"] == key:
                return i
        return 0

    # ------------------------------------------------------------------ routing
    def _col_free(self, layer, y1, y2, skip=()) -> bool:
        """A vertical segment at the centre of column `layer` between y1 and y2 hits no node."""
        lo, hi = sorted((y1, y2))
        for nid in self.col_nodes.get(layer, ()):
            if nid in skip:
                continue
            b = self.p.shapes[nid]
            if b.y - 4 <= hi and b.bottom + 4 >= lo:
                return False
        return True

    def _gap_x(self, layer) -> float:
        """x of a vertical segment in the gap before `layer`, spread to avoid overlaps."""
        k = self.gap_use[layer]
        self.gap_use[layer] += 1
        off = [0, -8, 8, -16, 16, -24, 24][k % 7]
        return self.col_left[layer] - COL_GAP / 2 + off

    def _route_sequence(self):
        d, eng, S = self.d, self.eng, self.p.shapes
        self.col_nodes = defaultdict(list)
        for nid in self.node_ids:
            if d.nodes[nid].kind != "boundaryEvent":
                self.col_nodes[eng.layer[nid]].append(nid)
        self.gap_use = defaultdict(int)
        loop_slot = defaultdict(int)
        for f in eng.seq:
            if f.id.startswith("__att_"):
                continue
            s, t = S[f.source], S[f.target]
            sg, tg = d.nodes[f.source].is_gateway, d.nodes[f.target].is_gateway
            ls, lt = eng.item_lane[f.source], eng.item_lane[f.target]
            if d.nodes[f.source].kind == "boundaryEvent" and f.id not in eng.back:
                yb = s.bottom + 22
                host_layer = eng.layer.get(d.nodes[f.source].attached_to, eng.layer[f.source])
                if t.cy > yb and self._col_free(host_layer, s.bottom, t.cy, skip=(d.nodes[f.source].attached_to,)):
                    pts = [(s.cx, s.bottom), (s.cx, t.cy), (t.x, t.cy)] if t.x > s.cx + 10 else \
                        [(s.cx, s.bottom), (s.cx, t.y)]
                else:
                    mx = self._gap_x(eng.layer[f.target])
                    pts = [(s.cx, s.bottom), (s.cx, yb), (mx, yb), (mx, t.cy), (t.x, t.cy)]
                self._set_edge(f, pts)
                continue
            if f.id in eng.back:
                key = ls
                k = loop_slot[key]
                loop_slot[key] += 1
                box = self.lane_box[key]
                ycor = box.bottom - LANE_BOTTOM / 2 - LOOP_STEP * k - 4
                xo = s.right + 14 + 6 * k
                xi = t.x - 14 - 6 * k
                if sg and self._col_free(eng.layer[f.source], s.bottom, ycor, skip=(f.source,)):
                    pts = [(s.cx, s.bottom), (s.cx, ycor)]
                else:
                    pts = [(s.right, s.cy), (xo, s.cy), (xo, ycor)]
                if tg and lt == key and self._col_free(eng.layer[f.target], t.bottom, ycor, skip=(f.target,)):
                    pts += [(t.cx, ycor), (t.cx, t.bottom)]
                else:
                    pts += [(xi, ycor), (xi, t.cy), (t.x, t.cy)]
                self._set_edge(f, pts)
                continue
            chain = eng.chains.get(f.id, [])
            items = chain + [f.target]
            nxt = items[0]
            nxt_y = self.center(nxt)[1] if nxt in chain else t.cy
            if (sg and abs(nxt_y - s.cy) > 1
                    and self._col_free(eng.layer[f.source], s.cy, nxt_y, skip=(f.source,))):
                pts = [(s.cx, s.bottom if nxt_y > s.cy else s.y), (s.cx, nxt_y)]
            else:
                pts = [(s.right, s.cy)]
            for dm in chain:
                vx, vy = self.center(dm)
                cx, cy = pts[-1]
                if abs(cy - vy) > 1:
                    mx = self._gap_x(eng.item_layer[dm])
                    pts += [(mx, cy), (mx, vy)]
                pts.append((vx, vy))
            cx, cy = pts[-1]
            tl = eng.layer[f.target]
            if abs(cy - t.cy) <= 1:
                pts.append((t.x, t.cy))
            elif tg and self._col_free(tl, cy, t.cy, skip=(f.target,)):
                pts += [(t.cx, cy), (t.cx, t.bottom if cy > t.cy else t.y)]
            else:
                mx = self._gap_x(tl)
                pts += [(mx, cy), (mx, t.cy), (t.x, t.cy)]
            self._set_edge(f, pts)

    def _route_messages(self):
        """Message flows: vertical between pools; jog into a column gap when a node is in the way."""
        S, d = self.p.shapes, self.d
        lay = self.eng.layer
        k_mid = 0
        for f in d.message_flows():
            if f.source not in S or f.target not in S:
                continue
            s, t = S[f.source], S[f.target]
            s_node, t_node = f.source in d.nodes, f.target in d.nodes
            down = t.cy > s.cy
            xs = s.cx if s_node else t.cx
            xt = t.cx if t_node else xs
            sy = s.bottom if down else s.y
            ty = t.y if down else t.bottom
            sp = S.get(d.pool_of_ref(f.source))
            tp = S.get(d.pool_of_ref(f.target))
            s_bound = (sp.bottom if down else sp.y) if sp else sy
            t_bound = (tp.y if down else tp.bottom) if tp else ty
            step = 12 if down else -12
            pts = [(xs, sy)]
            xv = xs
            if s_node and f.source in lay and not self._col_free(lay[f.source], sy, s_bound, skip=(f.source,)):
                l = lay[f.source]
                gx = (self.col_left[l + 1] - COL_GAP / 2) if l + 1 < len(self.col_left) else s.right + COL_GAP / 2
                pts += [(xs, sy + step), (gx, sy + step)]
                xv = gx
            xv_t = xt
            tail = [(xt, ty)]
            if t_node and f.target in lay and not self._col_free(lay[f.target], ty, t_bound, skip=(f.target,)):
                gt = self.col_left[lay[f.target]] - COL_GAP / 2
                tail = [(gt, ty - step), (xt, ty - step), (xt, ty)]
                xv_t = gt
            if not t_node:
                xv_t = xv
                tail = [(xv, ty)]
            if not s_node:
                xv = xv_t
                pts = [(xv, sy)]
            if abs(xv - xv_t) > 1:
                midy = (s_bound + POOL_GAP / 2) if down else (s_bound - POOL_GAP / 2)
                midy += (6 * (k_mid % 4)) * (1 if down else -1)
                k_mid += 1
                pts += [(xv, midy), (xv_t, midy)]
            pts += tail
            self._set_edge(f, pts)

    def _set_edge(self, f, pts):
        clean = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - clean[-1][0]) < 0.5 and abs(p[1] - clean[-1][1]) < 0.5:
                continue
            clean.append(p)
        # drop collinear middle points
        out = [clean[0]]
        for i in range(1, len(clean) - 1):
            a, b, c = out[-1], clean[i], clean[i + 1]
            if (abs(a[0] - b[0]) < 0.5 and abs(b[0] - c[0]) < 0.5) or (abs(a[1] - b[1]) < 0.5 and abs(b[1] - c[1]) < 0.5):
                continue
            out.append(b)
        if len(clean) > 1:
            out.append(clean[-1])
        if len(out) == 1:
            out.append(out[0])
        self.p.edges[f.id] = out
        if f.name:
            self.p.labels[f.id] = _message_label(out, f.name) if f.kind == "message" else _edge_label(out, f.name)

    def _groups_and_annotations(self):
        d, S = self.d, self.p.shapes
        for g in d.groups.values():
            members = [S[n.id] for n in d.nodes.values() if n.group == g.id and n.id in S]
            if not members:
                continue
            pad = 16
            x1 = min(b.x for b in members) - pad
            y1 = min(b.y for b in members) - pad - 8
            x2 = max(b.right for b in members) + pad
            y2 = max(b.bottom for b in members) + pad
            self.p.shapes[g.id] = Bounds(x1, y1, x2 - x1, y2 - y1)
        for a in d.annotations.values():
            if a.target not in S:
                continue
            t = S[a.target]
            w, h = _annotation_size(a.text)
            x = max(t.x, min(t.cx + 10, self.content_right - w - 10))   # keep it inside the pool
            b = Bounds(x, t.y - h - 20, w, h)
            self.p.shapes[a.id] = b
            self.p.edges[f"Association_{a.id}"] = [(t.cx, t.y), (b.x, b.bottom)]


def _annotation_size(text: str) -> tuple[int, int]:
    """Short notes stay narrow; long ones (contradiction footnotes) get a wider box."""
    w, per_line = (150, 24) if len(text) <= 72 else (240, 38)
    return w, 14 * max(1, (len(text) + per_line - 1) // per_line) + 14


def _message_label(pts, name) -> Bounds:
    """Label to the right of the longest vertical segment."""
    w, h = min(120, max(24, 7 * len(name))), 14 * max(1, (len(name) + 17) // 18)
    best = max(zip(pts, pts[1:]), key=lambda ab: abs(ab[1][1] - ab[0][1]) if abs(ab[0][0] - ab[1][0]) < 1 else -1)
    (x0, y0), (_, y1) = best
    return Bounds(x0 + 6, (y0 + y1) / 2 - h / 2, w, h)


def _edge_label(pts, name) -> Bounds:
    """Place the condition label next to where the branch separates from its siblings."""
    lines = max(1, (len(name) + 17) // 18)
    w = min(120, max(24, 7 * len(name)))
    h = 14 * lines
    (x0, y0), (x1, y1) = pts[0], pts[1]
    if abs(x0 - x1) < 0.5 and len(pts) > 2:      # vertical exit from gateway: label at the corner
        avail = abs(pts[2][0] - x1) - 14
        if avail < w:
            w = max(44, avail)
            h = 14 * max(1, -(-len(name) * 7 // int(w)))
        return Bounds(x1 + 8, y1 - h - 3, w, h)
    if abs(x0 - x1) < 0.5:
        return Bounds(x0 + 6, (y0 + y1) / 2 - h / 2, w, h)
    if len(pts) > 2 and abs(x1 - pts[2][0]) < 0.5 and abs(x1 - x0) < 60:   # short stub then vertical
        y2 = pts[2][1]
        if y2 > y1:
            return Bounds(x1 + 5, y1 + 6, w, h)
        if y2 < y1:
            return Bounds(x1 + 5, y1 - h - 6, w, h)
    return Bounds(x0 + 6, y0 - h - 3, w, h)


# ----------------------------------------------------------------------------- entry point
def layout(d: Diagram) -> LayoutResult:
    planes = [_layout_main(d)]
    for n in d.nodes.values():
        if n.kind == SUBPROCESS_KIND:
            planes.append(_layout_sub(d, n.id))
    return LayoutResult(planes)


def _layout_main(d: Diagram) -> Plane:
    with_pools = bool(d.pools)
    lanes: list[dict] = []
    if with_pools:
        for pool in d.pools.values():
            if pool.process is None:
                lanes.append({"key": pool.id, "kind": "blackbox", "pool": pool.id, "process": None})
                continue
            proc = d.processes[pool.process]
            if proc.lanes:
                for lid in proc.lanes:
                    lanes.append({"key": lid, "kind": "lane", "pool": pool.id, "process": proc.id})
            else:
                lanes.append({"key": proc.id, "kind": "process", "pool": pool.id, "process": proc.id})
    for proc in d.processes.values():
        if proc.pool is None and (any(n.container == proc.id for n in d.nodes.values()) or not with_pools):
            lanes.append({"key": proc.id, "kind": "process", "pool": None, "process": proc.id})
    top = set(d.processes)
    node_ids = [n.id for n in d.nodes.values() if n.container in top]
    plane = Plane(None)
    _PlaneBuilder(d, node_ids, lanes, plane, with_pools).build()
    return plane


def _layout_sub(d: Diagram, sub_id: str) -> Plane:
    node_ids = [n.id for n in d.nodes.values() if n.container == sub_id]
    lanes = [{"key": sub_id, "kind": "process", "pool": None, "process": sub_id}]
    plane = Plane(sub_id)
    _PlaneBuilder(d, node_ids, lanes, plane, with_pools=False).build()
    return plane
