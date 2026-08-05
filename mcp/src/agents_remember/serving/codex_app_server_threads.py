"""Multiplexed thread demux for one Codex app-server connection.

Owns which threads exist on the connection, which of them is the seat's own, what each is
doing, and the bounded indexes that let an item- or delta-only frame be attributed to one of
them. The adapter owns the snapshot and the wire; everything here is about identity.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from agents_remember.errors import CodexAppServerError
from agents_remember.serving.codex_agent_lifecycle import merge_agent_status
from agents_remember.serving.codex_app_server_protocol import JsonObject, RequestId
from agents_remember.serving.codex_app_server_state import (
    CodexServerInteraction,
    required_text,
)
from agents_remember.serving.harness_control_models import (
    ControlOperationRef,
    PendingInteraction,
)

THREAD_REGISTRY_LIMIT = 64
ITEM_THREAD_INDEX_LIMIT = 1024
PENDING_INTERACTIONS_PER_THREAD = 16


@dataclass
class CodexThreadState:
    """Per-thread demux state on one multiplexed app-server connection.

    The codex app-server auto-attaches sub-agent thread listeners to the seat's
    connection, so turn/item/approval traffic for many threads arrives interleaved.
    The parent thread is the session thread (turn writes stay parent-only); any other
    threadId is auto-registered from traffic with status ``unresolved`` until
    parent-thread collab evidence (collabAgentToolCall/subAgentActivity) binds its
    identity. Agent turns never carry a ControlOperationRef: turn/start writes are
    parent-only, so agent completions record ``None`` as the operation.
    """

    thread_id: str
    is_parent: bool
    status: str
    agent_path: str | None = None
    active_turn_id: str | None = None
    turn_operations: dict[str, ControlOperationRef] = field(default_factory=dict)
    completed_turns: OrderedDict[str, ControlOperationRef | None] = field(
        default_factory=OrderedDict
    )
    unbound_completions: dict[str, JsonObject] = field(default_factory=dict)
    pending_interactions: OrderedDict[RequestId, CodexServerInteraction] = field(
        default_factory=OrderedDict
    )
    """Concurrent server->client requests keyed by rpc id, exactly how the vendor
    tracks them (the codex TUI keeps one app-global pending map keyed by approval
    id): a second request while another is pending is normal traffic, never an
    error."""

    @property
    def pending_interaction(self) -> CodexServerInteraction | None:
        """The thread's oldest pending interaction (the pre-multiplex singular view)."""

        return next(iter(self.pending_interactions.values()), None)


class CodexThreadRegistry:
    """The threads on one connection, their agent identities, and the item->thread index.

    ``session_thread_id`` is read on every lookup rather than captured, because a reconnect
    can hand the adapter a different parent thread id. ``on_register`` fires when a new agent
    thread appears or an old one is evicted, which is when the published registry has changed.
    """

    def __init__(
        self,
        *,
        session_thread_id: Callable[[], str | None],
        completed_turn_limit: int,
        on_register: Callable[[], None],
    ) -> None:
        self._session_thread_id = session_thread_id
        self._completed_turn_limit = completed_turn_limit
        self._on_register = on_register
        self._states: OrderedDict[str, CodexThreadState] = OrderedDict()
        self._item_threads: OrderedDict[str, str] = OrderedDict()

    def parent(self) -> CodexThreadState:
        """The session/parent thread's demux state, registered on first use."""

        thread_id = self._session_thread_id()
        if thread_id is None:
            raise CodexAppServerError("Codex thread identity is not established")
        state = self._states.get(thread_id)
        if state is None:
            state = CodexThreadState(thread_id=thread_id, is_parent=True, status="active")
            self._states[thread_id] = state
        elif not state.is_parent:
            state.is_parent = True
        return state

    def resolve(self, params: Mapping[str, object], *, context: str) -> CodexThreadState:
        """Demux one notification to its thread state.

        A missing/non-text ``threadId`` is a shape error and fails closed exactly as the
        old ``_validate_thread`` did; a well-formed foreign threadId is never an error --
        it auto-registers as an ``unresolved`` agent thread until collab evidence binds
        its identity.
        """

        thread_id = required_text(params, "threadId", context=context)
        state = self._states.get(thread_id)
        if state is not None:
            self._states.move_to_end(thread_id)
            return state
        if thread_id == self._session_thread_id():
            return self.parent()
        if len(self._states) >= THREAD_REGISTRY_LIMIT:
            # Evict the oldest settled/unresolved agent thread first: an actively-turning agent or one holding a pending approval is
            # never evicted. When nothing is evictable this raises, and the message loop
            # degrades the frame to raw evidence instead of failing the bridge.
            evictable = next(
                (
                    key
                    for key, entry in self._states.items()
                    if not entry.is_parent
                    and entry.pending_interaction is None
                    and entry.active_turn_id is None
                ),
                None,
            )
            if evictable is None:
                raise CodexAppServerError("Codex thread registry is full")
            del self._states[evictable]
        state = CodexThreadState(thread_id=thread_id, is_parent=False, status="unresolved")
        self._states[thread_id] = state
        self._on_register()
        return state

    def state(self, thread_id: str) -> CodexThreadState | None:
        """The registered state for one thread id, without registering anything."""

        return self._states.get(thread_id)

    def thread_ids(self) -> tuple[str, ...]:
        """Every registered thread id, oldest touch first."""

        return tuple(self._states)

    def retire_turn(
        self,
        state: CodexThreadState,
        turn_id: str,
        operation: ControlOperationRef | None,
    ) -> None:
        """Release live correlation and retain only the bounded terminal duplicate window."""

        state.turn_operations.pop(turn_id, None)
        state.completed_turns[turn_id] = operation
        state.completed_turns.move_to_end(turn_id)
        while len(state.completed_turns) > self._completed_turn_limit:
            state.completed_turns.popitem(last=False)

    def interaction_thread(self, interaction_id: str) -> tuple[CodexThreadState, RequestId] | None:
        """The (thread, rpc id) owning ``interaction_id`` (answers route by request id)."""

        for state in self._states.values():
            for rpc_id, pending in state.pending_interactions.items():
                if pending.pending.interaction_id == interaction_id:
                    return state, rpc_id
        return None

    def pending_interactions(self) -> tuple[PendingInteraction, ...]:
        """Every thread's pending interactions, each agent one labelled with its identity."""

        pendings = []
        for state in self._states.values():
            for pending in state.pending_interactions.values():
                raw = dict(pending.pending.raw)
                if not state.is_parent:
                    raw["agentLabel"] = self._agent_label(state)
                pendings.append(replace(pending.pending, raw=raw))
        return tuple(pendings)

    def agent_registry(self) -> JsonObject:
        """The bounded agent registry the serving projector reads out of ``snapshot.raw``."""

        registry: JsonObject = {}
        for thread_id, state in self._states.items():
            if state.is_parent:
                continue
            entry: JsonObject = {"status": state.status}
            if state.agent_path is not None:
                entry["agentPath"] = state.agent_path
            registry[thread_id] = entry
        return registry

    def learn_item_thread(self, params: Mapping[str, object]) -> None:
        """Learn the item->thread demux index from item traffic; malformed shapes are skipped."""

        thread_id = params.get("threadId")
        item = params.get("item")
        if not isinstance(thread_id, str) or not thread_id or not isinstance(item, Mapping):
            return
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            return
        self._item_threads[item_id] = thread_id
        self._item_threads.move_to_end(item_id)
        while len(self._item_threads) > ITEM_THREAD_INDEX_LIMIT:
            self._item_threads.popitem(last=False)

    def route_delta_params(self, method: str, params: JsonObject) -> JsonObject:
        """Bind a delta frame's thread from the item index when the frame itself lacks it."""

        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            return params
        if "/delta" not in method and not method.endswith("patchUpdated"):
            return params
        item_id = params.get("itemId")
        if not isinstance(item_id, str):
            return params
        bound = self._item_threads.get(item_id)
        if bound is None:
            return params
        return {**params, "threadId": bound}

    def learn_collab_identity(self, item: Mapping[str, object]) -> bool:
        """Bind agent identity from collab items; reports whether the registry changed.

        Parent-thread items model the collaboration itself: ``collabAgentToolCall``
        carries ``receiverThreadIds``/``agentsStates`` and ``subAgentActivity`` carries
        ``agentThreadId`` + ``agentPath``. Well-formed entries bind the registry;
        anything else is left as raw evidence for the projector.
        """

        item_type = item.get("type")
        if item_type == "subAgentActivity":
            return self._learn_sub_agent_activity(item)
        if item_type == "collabAgentToolCall":
            return self._learn_collab_tool_call(item)
        return False

    def _learn_sub_agent_activity(self, item: Mapping[str, object]) -> bool:
        """Bind one sub-agent's thread, path and kind from a ``subAgentActivity`` item.

        Returns whether the registry changed. The thread id is the only required field: a path or
        kind that is missing or malformed leaves that facet as it was rather than failing the item.
        """

        agent_thread_id = item.get("agentThreadId")
        if not isinstance(agent_thread_id, str) or not agent_thread_id:
            return False
        state = self._states.get(agent_thread_id) or self.resolve(
            {"threadId": agent_thread_id}, context="subAgentActivity item"
        )
        agent_path = item.get("agentPath")
        if isinstance(agent_path, str) and agent_path:
            state.agent_path = agent_path
        kind = item.get("kind")
        if isinstance(kind, str) and kind:
            state.status = merge_agent_status(state.status, kind)
        return True

    def _learn_collab_tool_call(self, item: Mapping[str, object]) -> bool:
        """Register receiver threads and merge reported agent states from a ``collabAgentToolCall``.

        Returns whether the registry changed. ``receiverThreadIds`` may mint a thread because the
        call names who it is addressed to; ``agentsStates`` only updates threads that are already
        registered, so a status about an unknown thread is left to cross as raw evidence.
        """

        learned = False
        receivers = item.get("receiverThreadIds")
        if isinstance(receivers, list):
            for receiver in receivers:
                if isinstance(receiver, str) and receiver:
                    self.resolve({"threadId": receiver}, context="collabAgentToolCall item")
                    learned = True
        agents_states = item.get("agentsStates")
        if isinstance(agents_states, Mapping):
            for agent_thread_id, agent_state in agents_states.items():
                state = self._states.get(agent_thread_id)
                if state is None or not isinstance(agent_state, Mapping):
                    continue
                status = agent_state.get("status")
                if isinstance(status, str) and status:
                    state.status = merge_agent_status(state.status, status)
                    learned = True
        return learned

    def _agent_label(self, state: CodexThreadState) -> str:
        """The bound identity evidence for one agent thread, or the fallback label."""

        if state.agent_path is not None:
            return state.agent_path
        return f"agent {state.thread_id[:8]}"
