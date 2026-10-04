"""In-memory BPMN graph and the DIAGRAM API exposed to LLM-generated code.

The LLM never writes XML. It writes short Python programs against this API
(see `API_METHODS` and docs/api.md); the program is executed in the sandbox,
and the resulting graph is validated, laid out and serialized deterministically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

TASK_KINDS = {
    "task", "userTask", "scriptTask", "serviceTask", "manualTask",
    "sendTask", "receiveTask", "businessRuleTask",
}
GATEWAY_KINDS = {"exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway"}
EVENT_KINDS = {"startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent"}
SUBPROCESS_KIND = "subProcess"
EVENT_DEFINITIONS = {"timer", "message", "error", "signal", "terminate", "escalation", "conditional"}


_NCNAME_BAD = re.compile(r"[^\w.\-]", re.UNICODE)


def xml_id(raw: str) -> str:
    """Turn an arbitrary IR id into a valid XML ID (NCName)."""
    clean = _NCNAME_BAD.sub("_", raw.strip())
    if not clean or not (clean[0].isalpha() or clean[0] == "_"):
        clean = "id_" + clean
    return clean


class DiagramError(ValueError):
    """Raised when generated code calls the API incorrectly.

    Messages are written for the LLM: they say what was wrong and how to fix it.
    """


@dataclass
class Node:
    id: str
    kind: str
    name: str
    container: str            # process id or subprocess id
    lane: str | None = None   # lane id (only for nodes directly in a process)
    group: str | None = None
    event_definition: str | None = None
    auto: bool = False        # created by post-processing, not by the model
    details: dict = field(default_factory=dict)

    @property
    def is_task(self) -> bool:
        return self.kind in TASK_KINDS or self.kind == SUBPROCESS_KIND

    @property
    def is_gateway(self) -> bool:
        return self.kind in GATEWAY_KINDS

    @property
    def is_event(self) -> bool:
        return self.kind in EVENT_KINDS


@dataclass
class Flow:
    id: str
    source: str
    target: str
    name: str = ""
    kind: str = "sequence"  # "sequence" | "message"
    default: bool = False    # default ("otherwise") branch of an exclusive/inclusive gateway
    probability: float | None = None


@dataclass
class Lane:
    id: str
    name: str
    process: str


@dataclass
class Pool:
    id: str            # participant id
    name: str
    process: str | None  # None => black-box pool


@dataclass
class Process:
    id: str
    name: str
    pool: str | None = None
    lanes: list[str] = field(default_factory=list)


@dataclass
class Group:
    id: str
    name: str
    container: str
    lane: str | None = None


@dataclass
class Annotation:
    id: str
    text: str
    target: str
    container: str


class Diagram:
    """Process graph built by generated code. All `add_*` methods return ids (strings)."""

    def __init__(self, name: str = "Процесс"):
        self.name = name
        self.nodes: dict[str, Node] = {}
        self.flows: dict[str, Flow] = {}
        self.processes: dict[str, Process] = {}
        self.pools: dict[str, Pool] = {}
        self.lanes: dict[str, Lane] = {}
        self.groups: dict[str, Group] = {}
        self.annotations: dict[str, Annotation] = {}
        self._counters: dict[str, int] = {}
        self.root_process = self._new_process(name)
        # ids "start"/"end" match the reserved IR ids, so diagram ids == IR ids
        self.root_start = self._add_node("startEvent", "Начало", self.root_process, "StartEvent", id="start")
        self.root_end = self._add_node("endEvent", "Конец", self.root_process, "EndEvent", id="end")

    DETAIL_NUMBERS = ("duration_min", "wait_min", "sla_hours")

    def set_details(self, target, source_quote="", assumption="", deadline="", documents=None,
                    duration_min=None, wait_min=None, sla_hours=None, estimate=False,
                    accountable=None, consulted=None, informed=None):
        if target not in self.nodes:
            raise DiagramError("Карточку можно добавить только к шагу, шлюзу или событию")
        if not all(isinstance(v, str) for v in (source_quote, assumption, deadline)):
            raise DiagramError("Цитата, допущение и срок должны быть строками")
        if documents is None:
            documents = []
        if not isinstance(documents, list) or not all(isinstance(v, str) for v in documents):
            raise DiagramError("Документы должны быть списком строк")
        details = dict(source_quote=source_quote, assumption=assumption, deadline=deadline, documents=documents)
        for key, value in (("duration_min", duration_min), ("wait_min", wait_min), ("sla_hours", sla_hours)):
            if value is not None:
                if not isinstance(value, (int, float)) or value < 0:
                    raise DiagramError(f"{key} должен быть неотрицательным числом")
                details[key] = value
        if estimate:
            details["estimate"] = True
        if accountable is not None:
            if not isinstance(accountable, str):
                raise DiagramError("accountable должен быть строкой (id участника)")
            details["accountable"] = accountable
        for key, value in (("consulted", consulted), ("informed", informed)):
            if value:
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise DiagramError(f"{key} должен быть списком строк")
                details[key] = value
        self.nodes[target].details = details
        return target

    # ------------------------------------------------------------------ ids
    def _id(self, prefix: str) -> str:
        while True:
            n = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = n
            cand = f"{prefix}_{n}"
            if not self._taken(cand):
                return cand

    def _taken(self, ident: str) -> bool:
        return any(ident in coll for coll in (self.nodes, self.flows, self.processes, self.pools,
                                              self.lanes, self.groups, self.annotations))

    def _explicit_id(self, ident) -> str:
        """Caller-supplied id (e.g. from the IR): must be a valid XML NCName and unique."""
        if not isinstance(ident, str) or not ident.strip():
            raise DiagramError(f"id должен быть непустой строкой, получено {ident!r}")
        clean = xml_id(ident)
        if clean in (getattr(self, "root_start", None), getattr(self, "root_end", None)) \
                and clean in self.nodes and not any(clean in (f.source, f.target) for f in self.flows.values()):
            del self.nodes[clean]          # explicit start/end replaces the untouched default one
        if self._taken(clean):
            raise DiagramError(f"id {clean!r} уже используется")
        return clean

    def _new_process(self, name: str) -> str:
        pid = self._id("Process")
        self.processes[pid] = Process(pid, name)
        return pid

    # ------------------------------------------------------------- parents
    def _check_name(self, name, what: str) -> str:
        if not isinstance(name, str):
            raise DiagramError(f"{what}: имя должно быть строкой, получено {type(name).__name__}")
        return name.strip()

    def resolve_parent(self, parent) -> tuple[str, str | None, str | None]:
        """Map any accepted parent id to (container, lane, group)."""
        if not isinstance(parent, str):
            raise DiagramError(
                f"Родитель должен быть id процесса/дорожки/группы/подпроцесса (строка), получено {parent!r}. "
                "Если add_pool вернул список дорожек, передавайте элемент списка, например lanes[0]."
            )
        if parent in self.processes:
            return parent, None, None
        if parent in self.lanes:
            lane = self.lanes[parent]
            return lane.process, lane.id, None
        if parent in self.pools:
            pool = self.pools[parent]
            if pool.process is None:
                raise DiagramError(f"Пул {parent} создан как «чёрный ящик» и не может содержать элементы")
            return pool.process, None, None
        if parent in self.groups:
            g = self.groups[parent]
            return g.container, g.lane, g.id
        if parent in self.nodes and self.nodes[parent].kind == SUBPROCESS_KIND:
            return parent, None, None
        raise DiagramError(
            f"Неизвестный родитель {parent!r}. Допустимы ROOT_PROCESS_ID, id дорожки из add_pool, "
            "id пула, id группы или id подпроцесса."
        )

    def _add_node(self, kind: str, name: str, parent: str, prefix: str,
                  event_definition: str | None = None, auto: bool = False, id=None) -> str:
        container, lane, group = self.resolve_parent(parent)
        nid = self._explicit_id(id) if id is not None else self._id(prefix)
        self.nodes[nid] = Node(nid, kind, name, container, lane, group, event_definition, auto)
        return nid

    # ------------------------------------------------------------ public API
    def add_task(self, name, parent, id=None):
        return self._add_node("task", self._check_name(name, "add_task"), parent, "Task", id=id)

    def add_user_task(self, name, parent, id=None):
        return self._add_node("userTask", self._check_name(name, "add_user_task"), parent, "UserTask", id=id)

    def add_script_task(self, name, parent, id=None):
        return self._add_node("scriptTask", self._check_name(name, "add_script_task"), parent, "ScriptTask", id=id)

    def add_service_task(self, name, parent, id=None):
        return self._add_node("serviceTask", self._check_name(name, "add_service_task"), parent, "ServiceTask", id=id)

    def add_manual_task(self, name, parent, id=None):
        return self._add_node("manualTask", self._check_name(name, "add_manual_task"), parent, "ManualTask", id=id)

    def add_send_task(self, name, parent, id=None):
        return self._add_node("sendTask", self._check_name(name, "add_send_task"), parent, "SendTask", id=id)

    def add_receive_task(self, name, parent, id=None):
        return self._add_node("receiveTask", self._check_name(name, "add_receive_task"), parent, "ReceiveTask", id=id)

    def add_business_rule_task(self, name, parent, id=None):
        return self._add_node("businessRuleTask", self._check_name(name, "add_business_rule_task"),
                              parent, "BusinessRuleTask", id=id)

    def create_subprocess(self, name, parent, id=None):
        return self._add_node(SUBPROCESS_KIND, self._check_name(name, "create_subprocess"), parent, "SubProcess", id=id)

    def add_exclusive_gateway(self, name, parent, id=None):
        return self._add_node("exclusiveGateway", self._check_name(name, "add_exclusive_gateway"),
                              parent, "Gateway", id=id)

    def add_parallel_gateway(self, name, parent, id=None):
        return self._add_node("parallelGateway", self._check_name(name, "add_parallel_gateway"),
                              parent, "Gateway", id=id)

    def add_inclusive_gateway(self, name, parent, id=None):
        return self._add_node("inclusiveGateway", self._check_name(name, "add_inclusive_gateway"),
                              parent, "Gateway", id=id)

    def add_event_based_gateway(self, name, parent, id=None):
        return self._add_node("eventBasedGateway", self._check_name(name, "add_event_based_gateway"),
                              parent, "Gateway", id=id)

    def _event_def(self, kind, allowed: Iterable[str]) -> str | None:
        if kind is None or kind == "":
            return None
        if kind not in allowed:
            raise DiagramError(f"Тип события {kind!r} не поддерживается, допустимо: {sorted(allowed)} или None")
        return kind

    def add_start_event(self, name, parent, kind=None, id=None):
        ed = self._event_def(kind, {"message", "timer", "signal", "conditional"})
        return self._add_node("startEvent", self._check_name(name, "add_start_event"), parent,
                              "StartEvent", ed, id=id)

    def add_end_event(self, name, parent, kind=None, id=None):
        ed = self._event_def(kind, {"message", "error", "terminate", "signal", "escalation"})
        return self._add_node("endEvent", self._check_name(name, "add_end_event"), parent, "EndEvent", ed,
                              id=id)

    def add_intermediate_event(self, name, parent, kind="timer", throw=False, id=None):
        allowed = {"message", "signal", "escalation"} if throw else {"message", "timer", "signal", "conditional"}
        ed = self._event_def(kind, allowed)
        k = "intermediateThrowEvent" if throw else "intermediateCatchEvent"
        return self._add_node(k, self._check_name(name, "add_intermediate_event"), parent, "Event", ed, id=id)

    def add_pool(self, parent, lanes, name=None):
        """Create a pool (participant) with lanes. Returns (pool_id, [lane_ids])."""
        if isinstance(lanes, str):
            lanes = [lanes]
        if not isinstance(lanes, (list, tuple)) or not all(isinstance(x, str) for x in lanes):
            raise DiagramError("add_pool: второй аргумент — список названий дорожек (строк)")
        container, _, _ = self.resolve_parent(parent)
        if container not in self.processes:
            raise DiagramError("add_pool: пул можно создать только на уровне процесса (ROOT_PROCESS_ID), "
                               "не внутри подпроцесса")
        pool_name = name if isinstance(name, str) and name.strip() else (
            lanes[0] if len(lanes) == 1 else self.processes[container].name)
        process = self.processes[container]
        if process.pool is not None:
            # The process already has a pool: this is another participant with its own process.
            container = self._new_process(pool_name)
            process = self.processes[container]
        pool_id = self._id("Participant")
        self.pools[pool_id] = Pool(pool_id, pool_name, container)
        process.pool = pool_id
        process.name = pool_name
        lane_ids = []
        if len(lanes) > 1 or (len(lanes) == 1 and name):
            for lname in lanes:
                lid = self._id("Lane")
                self.lanes[lid] = Lane(lid, lname.strip(), container)
                process.lanes.append(lid)
                lane_ids.append(lid)
        else:
            # single lane named like the pool: the pool itself is the container
            lane_ids = [pool_id] * len(lanes)
        return pool_id, lane_ids

    def add_black_box_pool(self, name):
        """External participant shown as an empty pool (only message flows go to it)."""
        pool_id = self._id("Participant")
        self.pools[pool_id] = Pool(pool_id, self._check_name(name, "add_black_box_pool"), None)
        return pool_id

    def add_group(self, name, parent):
        container, lane, _ = self.resolve_parent(parent)
        gid = self._id("Group")
        self.groups[gid] = Group(gid, self._check_name(name, "add_group"), container, lane)
        return gid

    def add_annotation(self, text, target):
        if target not in self.nodes:
            raise DiagramError(f"add_annotation: неизвестный элемент {target!r}")
        aid = self._id("TextAnnotation")
        self.annotations[aid] = Annotation(aid, self._check_name(text, "add_annotation"), target,
                                           self.nodes[target].container)
        return aid

    def _check_endpoint(self, ref, what: str) -> None:
        if not isinstance(ref, str) or ref not in self.nodes:
            hint = ""
            if isinstance(ref, str) and (ref in self.lanes or ref in self.processes or ref in self.groups):
                hint = " Это id контейнера, а связывать можно только узлы (задачи, шлюзы, события)."
            raise DiagramError(f"add_link: {what} {ref!r} не является узлом диаграммы.{hint}")

    def add_link(self, source, target, name=None, default=False, probability=None):
        """Sequence flow. default=True marks the gateway's default ("иначе") branch;
        probability (0..1) is used only by analytics / simulation."""
        self._check_endpoint(source, "источник")
        self._check_endpoint(target, "цель")
        if source == target:
            raise DiagramError(f"add_link: связь узла {source} самого с собой запрещена")
        if default and not self.nodes[source].kind in ("exclusiveGateway", "inclusiveGateway"):
            raise DiagramError("add_link: default=True допустим только для исходящей связи "
                               "исключающего или инклюзивного шлюза")
        if probability is not None and (not isinstance(probability, (int, float)) or not 0 <= probability <= 1):
            raise DiagramError("add_link: probability должна быть числом от 0 до 1")
        if default and any(f.default for f in self.outgoing(source)):
            raise DiagramError(f"add_link: у шлюза {source} уже есть ветка по умолчанию")
        label = name.strip() if isinstance(name, str) else ""
        fid = self._id("Flow")
        self.flows[fid] = Flow(fid, source, target, label, "sequence", bool(default), probability)
        return fid

    def add_message_link(self, source, target, name=None):
        for ref, what in ((source, "источник"), (target, "цель")):
            if not isinstance(ref, str) or (ref not in self.nodes and ref not in self.pools):
                raise DiagramError(f"add_message_link: {what} {ref!r} не является узлом или пулом")
        label = name.strip() if isinstance(name, str) else ""
        fid = self._id("MessageFlow")
        self.flows[fid] = Flow(fid, source, target, label, "message")
        return fid

    # -------------------------------------------------------------- queries
    def process_of(self, node_id: str) -> str:
        """Top-level process containing the node (walks up through subprocesses)."""
        c = self.nodes[node_id].container
        while c not in self.processes:
            c = self.nodes[c].container
        return c

    def pool_of_ref(self, ref: str) -> str | None:
        if ref in self.pools:
            return ref
        return self.processes[self.process_of(ref)].pool

    def children(self, container: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.container == container]

    def sequence_flows(self) -> list[Flow]:
        return [f for f in self.flows.values() if f.kind == "sequence"]

    def message_flows(self) -> list[Flow]:
        return [f for f in self.flows.values() if f.kind == "message"]

    def incoming(self, node_id: str) -> list[Flow]:
        return [f for f in self.flows.values() if f.kind == "sequence" and f.target == node_id]

    def outgoing(self, node_id: str) -> list[Flow]:
        return [f for f in self.flows.values() if f.kind == "sequence" and f.source == node_id]

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for n in self.nodes.values():
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        return {
            "nodes": len(self.nodes),
            "sequence_flows": len(self.sequence_flows()),
            "message_flows": len(self.message_flows()),
            "pools": len(self.pools),
            "lanes": len(self.lanes),
            "by_kind": kinds,
        }


# Public method names callable from generated code (used by the sandbox whitelist).
API_METHODS = frozenset({
    "add_task", "add_user_task", "add_script_task", "add_service_task", "add_manual_task",
    "add_send_task", "add_receive_task", "add_business_rule_task", "create_subprocess",
    "add_exclusive_gateway", "add_parallel_gateway", "add_inclusive_gateway", "add_event_based_gateway",
    "add_start_event", "add_end_event", "add_intermediate_event",
    "add_pool", "add_black_box_pool", "add_group", "add_annotation",
    "add_link", "add_message_link", "set_details",
})
