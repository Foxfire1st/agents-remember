"""Claude active projector: stream-json frames -> items.

Schema authority: the locked stream-json fixtures (2.1.207/2.1.210), the
adapter's parsed frame surface, and the Anthropic content-block grammar
(text/thinking/tool_use/tool_result). Assistant messages keep their text and
thinking blocks; ``tool_use`` blocks become stable-ID tool-call items keyed by
the native block id; ``tool_result`` blocks upsert the same item. Result frames
mint turn-result items and canonical terminal evidence.

Claude has no native history page (stream/replay-only by design); the active
projection hydrates from the live evidence window only, and user submissions
arrive through the adapter's *exact submission echo* — the replay-correlated
record the adapter builds from the authority's own submission (original text,
exact request id, replay message uuid) — never from a flattened projection of
native assistant/tool semantics. Provenance is resolved independently through
the submission-provenance batch.

Sub-agents: inner frames of an Agent-tool sub-agent
stream as ordinary assistant/user frames distinguished by
``parent_tool_use_id`` (the spawning Agent tool_use id), carrying
``subagent_type``/``task_description``; the lifecycle rides ``system`` frames
``task_started``/``task_progress``/``task_notification`` whose ``task_id`` is
the on-disk agent id (``subagents/agent-<task_id>.jsonl``, the .meta.json
``toolUseId`` is the join key) plus ``background_tasks_changed`` carrying the
running background-task set. All shapes probe-locked on the installed claude
2.1.220 (2026-07-26 live stream-json probes, foreground and
``run_in_background`` Agent calls). The ``task_id`` ↔ ``tool_use_id`` binding
is cross-frame evidence held in one bounded session-keyed registry; it only
ever ENRICHES the agent dimension from the harness's own task_* frames and
never fabricates identity — an unbound frame keeps ``agent_id`` = the join key
with status ``unknown``.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agents_remember.serving.conversation.models import (
    ConversationAgentRef,
    ConversationAgentStatus,
    ConversationContentBlock,
    ConversationCorrelation,
    ConversationItem,
    DiffBlock,
    MarkdownBlock,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
)
from agents_remember.serving.conversation.projectors.common import (
    ItemPhase,
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
    MapperOutput,
    TerminalOutcomeValue,
    UnmappableShape,
    harness_provenance,
    optional_text,
    required_list,
    required_object,
    required_text,
    unknown_input_provenance,
)
from agents_remember.serving.harness_control_models import (
    AR_TERMINAL_OUTCOME_KEY,
    EvidenceFrame,
)

HARNESS = "claude"

_CANCEL_REASONS = {"cancelled", "interrupted", "user_cancelled"}

# The installed Claude Code (2.1.216+) slash-command lifecycle contract. Each
# submitted ``/command`` emits a stable ``command_uuid`` triple queued -> started -> completed. It
# is a first-class typed frame (validated against this exact 3-state contract), not a tolerated
# stranger — the later native slash-command surface consumes it as settlement
# evidence, correlated by ``command_uuid`` to the replayed command envelope. Until that surface
# lands the lifecycle mints no timeline item (the native ``result``/history already renders the
# command), so it never floods as unknown-vendor.
_COMMAND_LIFECYCLE_STATES = frozenset({"queued", "started", "completed"})

# -- Sub-agent identity binding ---------------------------

_MAX_AGENT_BINDING_SESSIONS = 128
_MAX_AGENT_BINDINGS_PER_SESSION = 64

# task_notification ``status`` vocabulary. The 2.1.220 probe carried ``completed``;
# anything outside this table stays honest as ``unknown`` instead of being guessed.
_NOTIFICATION_AGENT_STATUS: dict[str, ConversationAgentStatus] = {
    "completed": "completed",
    "failed": "failed",
    "error": "failed",
    "stopped": "interrupted",
    "killed": "interrupted",
    "cancelled": "interrupted",
    "interrupted": "interrupted",
}

_NOTIFICATION_ITEM_PHASE: dict[ConversationAgentStatus, ItemPhase] = {
    "completed": "completed",
    "failed": "failed",
    "interrupted": "interrupted",
}


@dataclass(frozen=True)
class _AgentBinding:
    """One bound sub-agent identity; later task_* evidence replaces it wholesale."""

    task_id: str
    join_key: str | None
    subagent_type: str | None
    description: str | None
    status: ConversationAgentStatus


class _AgentBindingRegistry:
    """Bounded session-keyed task_id ↔ tool_use_id bindings from task_* evidence."""

    def __init__(self) -> None:
        self._sessions: OrderedDict[str, OrderedDict[str, _AgentBinding]] = OrderedDict()

    def lookup(
        self,
        session_id: str,
        *,
        join_key: str | None = None,
        task_id: str | None = None,
    ) -> _AgentBinding | None:
        bindings = self._sessions.get(session_id)
        if bindings is None:
            return None
        if task_id is not None:
            return bindings.get(task_id)
        if join_key is not None:
            for binding in bindings.values():
                if binding.join_key == join_key:
                    return binding
        return None

    def record(self, session_id: str, binding: _AgentBinding) -> None:
        bindings = self._sessions.get(session_id)
        if bindings is None:
            if len(self._sessions) >= _MAX_AGENT_BINDING_SESSIONS:
                self._sessions.popitem(last=False)
            bindings = OrderedDict()
            self._sessions[session_id] = bindings
        else:
            self._sessions.move_to_end(session_id)
        if binding.task_id in bindings:
            del bindings[binding.task_id]
        elif len(bindings) >= _MAX_AGENT_BINDINGS_PER_SESSION:
            bindings.popitem(last=False)
        bindings[binding.task_id] = binding


_AGENT_BINDINGS = _AgentBindingRegistry()


def _session_key(raw: Mapping[str, object]) -> str:
    # Every 2.1.220 stream frame carries the vendor session id. Id-less frames (older
    # fixtures) share one bucket, which stays honest: bindings there are exactly what
    # the stream itself evidenced, never cross-session guesses.
    return optional_text(raw.get("session_id")) or ""


def _sidechain_agent_ref(raw: Mapping[str, object]) -> ConversationAgentRef | None:
    """The roster identity for a frame keyed by ``parent_tool_use_id``.

    The bound agent id wins once task_* evidence bound the join key; before that the
    join key itself is the honest id, with the frame's own ``subagent_type`` as role.
    """

    join_key = optional_text(raw.get("parent_tool_use_id"))
    if join_key is None:
        return None
    role = optional_text(raw.get("subagent_type"))
    binding = _AGENT_BINDINGS.lookup(_session_key(raw), join_key=join_key)
    if binding is None:
        return ConversationAgentRef(
            agent_id=join_key, role=role, join_key=join_key, status="unknown"
        )
    return ConversationAgentRef(
        agent_id=binding.task_id,
        role=role or binding.subagent_type,
        join_key=join_key,
        status=binding.status,
    )


def _spawned_agent_ref(session_id: str, tool_use_id: str) -> ConversationAgentRef | None:
    """The roster identity when a tool_result settles a spawning Agent call."""

    binding = _AGENT_BINDINGS.lookup(session_id, join_key=tool_use_id)
    if binding is None:
        return None
    return ConversationAgentRef(
        agent_id=binding.task_id,
        role=binding.subagent_type,
        join_key=tool_use_id,
        status=binding.status,
    )


def map_evidence_frame(
    frame: EvidenceFrame,
    *,
    evidence_ref: str,
    parent_thread_id: str | None = None,  # noqa: ARG001 - multiplexed-harness demux context; claude encodes sub-agent identity in-band (parent_tool_use_id)
) -> list[MapperOutput]:
    """Map one full stream-json frame; unrecognized types stay preserved."""

    raw = frame.raw
    frame_type = optional_text(raw.get("type"))
    recognize = _SILENT_FRAME_CONTRACTS.get(frame_type or "")
    if recognize is not None:
        recognize(raw)
        return []
    if frame_type == "assistant":
        return _map_assistant(raw, created_at=frame.created_at, evidence_ref=evidence_ref)
    if frame_type == "result":
        return _map_result(raw, created_at=frame.created_at)
    if frame_type == "user":
        return _map_tool_carrier(raw, created_at=frame.created_at, evidence_ref=evidence_ref)
    if frame_type == "system":
        return _map_system(raw, created_at=frame.created_at, sequence=frame.sequence)
    return [
        MappedUnknownVendor(
            item_id=f"claude-event-{frame.sequence}",
            vendor_type=f"claude:{frame_type or 'unknown'}",
            safe_summary=f"claude frame of type {frame_type or 'unknown'}",
            created_at=frame.created_at,
        )
    ]


def _require_command_lifecycle(raw: Mapping[str, object]) -> None:
    """Strictly recognize the 3-state slash-command lifecycle."""

    required_text(raw.get("command_uuid"), "claude command_lifecycle.command_uuid")
    state = required_text(raw.get("state"), "claude command_lifecycle.state")
    if state not in _COMMAND_LIFECYCLE_STATES:
        raise UnmappableShape(
            f"claude command_lifecycle.state {state!r} is not a documented lifecycle state"
        )


def _require_rate_limit_event(raw: Mapping[str, object]) -> None:
    """Strictly recognize rate-limit telemetry, which feeds L3 exactly like codex rateLimits."""

    required_object(raw.get("rate_limit_info"), "claude rate_limit_event.rate_limit_info")


# Frame types with a known contract that mints NO timeline row. Recognizing them by name (rather
# than letting them fall to unknown-vendor) is what keeps an ordinary claude session flood-free;
# validating their shape anyway is what surfaces a genuine future drift as an honest failure
# instead of silent tolerance.
_SILENT_FRAME_CONTRACTS: dict[str, Callable[[Mapping[str, object]], None]] = {
    "command_lifecycle": _require_command_lifecycle,
    "rate_limit_event": _require_rate_limit_event,
}


def _map_system(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
    sequence: int,
) -> list[MapperOutput]:
    """Map the agent-lifecycle system subtypes.

    api_retry/status keep feeding the canonical status service via the snapshot, and
    every other subtype observed on 2.1.220 (init, task_updated, hook_*, ...) keeps
    dropping silently exactly as before. A MALFORMED task_* frame is vendor shape
    drift: it degrades to preserved unknown-vendor (the string-content precedent —
    a frame on every agent spawn must never kill the projection), never a guess.
    """

    subtype = optional_text(raw.get("subtype"))
    mapper: Callable[[Mapping[str, object]], list[MapperOutput]] | None = None
    if subtype in ("task_started", "task_progress", "task_notification"):
        mapper = lambda frame: _map_task_lifecycle(subtype, frame, created_at=created_at)  # noqa: E731
    elif subtype == "background_tasks_changed":
        mapper = lambda frame: _map_background_tasks_changed(frame, created_at=created_at)  # noqa: E731
    if mapper is None:
        return []
    try:
        return mapper(raw)
    except UnmappableShape:
        return [
            MappedUnknownVendor(
                item_id=f"claude-event-{sequence}",
                vendor_type=f"claude-system:{subtype}",
                safe_summary=f"malformed claude {subtype} frame preserved",
                created_at=created_at,
            )
        ]


def _map_task_lifecycle(
    subtype: str,
    raw: Mapping[str, object],
    *,
    created_at: str | None,
) -> list[MapperOutput]:
    """One roster item per agent, upserted across started → progress → notification.

    task_started additionally tags the spawning Agent tool-call item with the bound
    roster identity: the 2.1.220 probes show task_started preceding the Agent
    tool_result carrier in BOTH foreground and run_in_background ordering, so the
    tool call is honestly still streaming here and the later tool_result upsert
    settles it. task_updated ({task_id, patch:{status,...}}) duplicates the
    notification's terminal signal without the join key and stays unmapped.

    Each decision the frame carries lives in one named helper below — the identity
    defaults, the usage shape check, the subtype's status/phase, the emitted blocks,
    the task_started tagging item — so this body is the fixed order they happen in:
    resolve identity, validate, decide the lifecycle state, record the binding, emit.
    That order is load-bearing: the lookup above has to read the PREVIOUS binding
    before the record below replaces it wholesale.
    """

    task_id = required_text(raw.get("task_id"), f"claude {subtype}.task_id")
    session_id = _session_key(raw)
    binding = _AGENT_BINDINGS.lookup(session_id, task_id=task_id)
    identity = _resolve_task_identity(raw, binding)
    usage = _require_task_usage(subtype, raw)
    summary = optional_text(raw.get("summary"))
    last_tool_name = optional_text(raw.get("last_tool_name"))
    join_key, agent_status, phase = _task_lifecycle_state(subtype, raw, join_key=identity.join_key)
    _AGENT_BINDINGS.record(
        session_id,
        _AgentBinding(
            task_id=task_id,
            join_key=join_key,
            subagent_type=identity.subagent_type,
            description=identity.retained_description,
            status=agent_status,
        ),
    )
    blocks = _task_lifecycle_blocks(
        description=identity.description,
        summary=summary,
        usage_block=_task_usage_block(usage, last_tool_name),
    )
    outputs: list[MapperOutput] = [
        MappedItem(
            item=ConversationItem(
                item_id=f"claude-agent-{task_id}",
                revision=1,
                global_ordinal=1,
                lane="harness",
                source="harness-live",
                provenance=harness_provenance(
                    f"claude stream-json system/{subtype} frame", observed_at=created_at
                ),
                role="system",
                kind="notice",
                phase=phase,
                blocks=blocks,
                agent=ConversationAgentRef(
                    agent_id=task_id,
                    role=identity.subagent_type,
                    join_key=join_key,
                    status=agent_status,
                ),
                created_at=created_at,
            )
        )
    ]
    if subtype == "task_started" and join_key is not None:
        outputs.append(
            _agent_identity_tag_item(
                task_id,
                join_key=join_key,
                subagent_type=identity.subagent_type,
                created_at=created_at,
            )
        )
    return outputs


@dataclass(frozen=True)
class _TaskIdentity:
    """The roster identity one task_* frame resolves to: frame evidence over binding."""

    join_key: str | None
    subagent_type: str | None
    description: str | None
    # What the replacing binding record keeps, which is deliberately NOT always what
    # the roster row displays; see _resolve_task_identity.
    retained_description: str | None


def _resolve_task_identity(
    raw: Mapping[str, object],
    binding: _AgentBinding | None,
) -> _TaskIdentity:
    """Resolve a task_* frame's identity fields against the binding it already has.

    Every field prefers the frame's own evidence and falls back to what earlier
    task_* evidence already proved, because the later frames are sparse: a
    task_notification carries neither ``subagent_type`` nor ``description``, and its
    roster upsert must not blank what task_started filled in. Nothing is guessed — a
    field absent from both the frame and the binding stays None.
    """

    if binding is None:
        bound_join_key, bound_type, bound_description = None, None, None
    else:
        bound_join_key = binding.join_key
        bound_type = binding.subagent_type
        bound_description = binding.description
    frame_description = optional_text(raw.get("description"))
    return _TaskIdentity(
        join_key=optional_text(raw.get("tool_use_id")) or bound_join_key,
        subagent_type=optional_text(raw.get("subagent_type")) or bound_type,
        description=frame_description or bound_description,
        # task_started's description is the task's own; a progress frame's
        # description is a transient activity label, so the record keeps the first.
        retained_description=bound_description or frame_description,
    )


def _require_task_usage(subtype: str, raw: Mapping[str, object]) -> Mapping[str, object] | None:
    """The frame's optional ``usage`` telemetry, validated as the vendor-owned object it is.

    Absent is normal (task_started carries none); present-but-not-an-object is vendor
    shape drift, which the caller degrades to preserved unknown-vendor.
    """

    usage_raw = raw.get("usage")
    if usage_raw is None:
        return None
    if not isinstance(usage_raw, Mapping):
        raise UnmappableShape(f"claude {subtype}.usage must be an object")
    return usage_raw


def _task_lifecycle_state(
    subtype: str,
    raw: Mapping[str, object],
    *,
    join_key: str | None,
) -> tuple[str | None, ConversationAgentStatus, ItemPhase]:
    """The join key, agent status and item phase this lifecycle subtype settles on.

    started and progress are both honestly still running; only a notification is
    terminal, and its ``status`` word is table-driven so anything outside the probed
    vocabulary stays ``unknown`` instead of being guessed. The join key is returned
    rather than merely passed through because task_started is the binding authority:
    there the id is REQUIRED evidence, not the optional default the other subtypes
    inherit from the binding.
    """

    if subtype == "task_started":
        # The binding authority: both ids are required evidence on this frame.
        return (
            required_text(raw.get("tool_use_id"), "claude task_started.tool_use_id"),
            "running",
            "streaming",
        )
    if subtype == "task_progress":
        return (join_key, "running", "streaming")
    # task_notification
    status_text = required_text(raw.get("status"), "claude task_notification.status")
    agent_status = _NOTIFICATION_AGENT_STATUS.get(status_text, "unknown")
    return (join_key, agent_status, _NOTIFICATION_ITEM_PHASE.get(agent_status, "unknown"))


def _task_usage_block(
    usage: Mapping[str, object] | None,
    last_tool_name: str | None,
) -> ToolOutputBlock | None:
    """The roster row's telemetry block, or None when the frame carried neither half.

    The two halves are independent — a progress frame can carry usage without a last
    tool name — so each key is emitted only when the frame actually evidenced it.
    """

    if usage is None and last_tool_name is None:
        return None
    data: dict[str, object] = {}
    if usage is not None:
        data["usage"] = usage
    if last_tool_name is not None:
        data["lastToolName"] = last_tool_name
    return ToolOutputBlock(block_id="usage", data=data)


def _task_lifecycle_blocks(
    *,
    description: str | None,
    summary: str | None,
    usage_block: ToolOutputBlock | None,
) -> tuple[ConversationContentBlock, ...]:
    """The roster row's content blocks, in the order every upsert re-emits them."""

    blocks: list[ConversationContentBlock] = []
    if description:
        blocks.append(TextBlock(block_id="description", text=description))
    if summary:
        blocks.append(TextBlock(block_id="summary", text=summary))
    if usage_block is not None:
        blocks.append(usage_block)
    return tuple(blocks)


def _agent_identity_tag_item(
    task_id: str,
    *,
    join_key: str,
    subagent_type: str | None,
    created_at: str | None,
) -> MappedItem:
    """The task_started upsert that tags the spawning Agent tool-call with the bound identity.

    It targets the tool-call item the parent timeline already minted (its item id IS
    the Agent ``tool_use`` id) and carries identity only — the call is honestly still
    streaming at task_started, and the later tool_result upsert settles it.
    """

    return MappedItem(
        item=ConversationItem(
            item_id=join_key,
            revision=1,
            global_ordinal=1,
            lane="harness",
            source="harness-live",
            provenance=harness_provenance(
                "claude stream-json task_started frame (agent identity binding)",
                observed_at=created_at,
            ),
            role="tool",
            kind="tool-call",
            phase="streaming",
            # Block-union upsert: the tool_use/tool_result blocks the parent
            # timeline already minted survive untouched.
            blocks=(),
            correlation=ConversationCorrelation(tool_call_id=join_key),
            agent=ConversationAgentRef(
                agent_id=task_id,
                role=subagent_type,
                join_key=join_key,
                status="running",
            ),
            created_at=created_at,
        )
    )


def _map_background_tasks_changed(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
) -> list[MapperOutput]:
    """Roster reconciliation from the running background-task set.

    Shape (2.1.220 probe): ``tasks`` is the FULL set of currently running background
    tasks as ``{task_id, task_type, description}`` — no tool_use_id join, no status.
    It can only honestly REGISTER a task the task_* evidence never bound (a replay
    window that opened mid-agent); for already-bound tasks the task_* frames are the
    richer authority and this frame mints nothing. An empty set asserts "nothing
    running" without distinguishing completed from killed, so it reconciles nothing.
    """

    tasks = required_list(raw.get("tasks"), "claude background_tasks_changed.tasks")
    session_id = _session_key(raw)
    outputs: list[MapperOutput] = []
    for position, entry in enumerate(tasks):
        entry_object = required_object(entry, f"claude background_tasks_changed.tasks[{position}]")
        task_id = required_text(
            entry_object.get("task_id"), "claude background_tasks_changed.task_id"
        )
        if _AGENT_BINDINGS.lookup(session_id, task_id=task_id) is not None:
            continue
        description = optional_text(entry_object.get("description"))
        _AGENT_BINDINGS.record(
            session_id,
            _AgentBinding(
                task_id=task_id,
                join_key=None,
                subagent_type=None,
                description=description,
                status="running",
            ),
        )
        outputs.append(
            MappedItem(
                item=ConversationItem(
                    item_id=f"claude-agent-{task_id}",
                    revision=1,
                    global_ordinal=1,
                    lane="harness",
                    source="harness-live",
                    provenance=harness_provenance(
                        "claude stream-json background_tasks_changed frame",
                        observed_at=created_at,
                    ),
                    role="system",
                    kind="notice",
                    phase="streaming",
                    blocks=(
                        (TextBlock(block_id="description", text=description),)
                        if description
                        else ()
                    ),
                    agent=ConversationAgentRef(agent_id=task_id, status="running"),
                    created_at=created_at,
                )
            )
        )
    return outputs


def map_transcript_echo(
    entry: Mapping[str, object],
    *,
    evidence_ref: str,
) -> list[MapperOutput]:
    """Map the adapter's exact submission echo to a user message item.

    Only ``role="user"`` entries are consumed here; the echo is the authority's
    own submission record (original text, exact request id, replay uuid), so
    the item is complete without native-frame projection. Provenance is
    resolved by the engine through the submission-provenance batch.
    """

    if entry.get("role") != "user":
        raise UnmappableShape("claude transcript echo is only consumed for user submissions")
    item_id = required_text(entry.get("vendorCorrelationId"), "claude replay correlation uuid")
    request_id = optional_text(entry.get("requestId"))
    created_at = optional_text(entry.get("createdAt"))
    return [
        MappedItem(
            item=ConversationItem(
                item_id=item_id,
                revision=1,
                global_ordinal=1,
                lane="unknown-input",
                source="native-history",
                provenance=unknown_input_provenance(
                    "agents-remember submission echo (claude replay correlation)",
                    observed_at=created_at,
                ),
                role="user",
                kind="message",
                phase="completed",
                blocks=(TextBlock(block_id="text", text=str(entry.get("text") or "")),),
                correlation=(
                    ConversationCorrelation(request_id=request_id) if request_id else None
                ),
                created_at=created_at,
                evidence_ref=evidence_ref,
            )
        )
    ]


def _map_assistant(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
    evidence_ref: str,
) -> list[MapperOutput]:
    item_id = required_text(raw.get("uuid"), "claude assistant frame uuid")
    message = required_object(raw.get("message"), "claude assistant frame.message")
    content = required_list(message.get("content"), "claude assistant message.content")
    agent = _sidechain_agent_ref(raw)
    outputs: list[MapperOutput] = []
    blocks: list = []
    for position, raw_block in enumerate(content):
        block = required_object(raw_block, "claude assistant content block")
        block_type = required_text(block.get("type"), "claude content block type")
        if block_type == "text":
            blocks.append(
                MarkdownBlock(
                    block_id=f"text-{position}",
                    markdown=str(block.get("text") or ""),
                )
            )
        elif block_type == "thinking":
            blocks.append(
                ThinkingBlock(
                    block_id=f"thinking-{position}",
                    markdown=str(block.get("thinking") or ""),
                )
            )
        elif block_type == "tool_use":
            outputs.extend(
                _map_tool_use(
                    block,
                    parent_item_id=item_id,
                    created_at=created_at,
                    agent=agent,
                )
            )
        else:
            blocks.append(
                UnknownVendorBlock(
                    block_id=f"unknown-{position}",
                    vendor_type=f"claude-block:{block_type}",
                    safe_summary=f"claude assistant content block of type {block_type}",
                    evidence_ref=f"{evidence_ref}:b{position}",
                )
            )
    if blocks:
        outputs.insert(
            0,
            MappedItem(
                item=ConversationItem(
                    item_id=item_id,
                    revision=1,
                    global_ordinal=1,
                    lane="harness",
                    source="harness-live",
                    provenance=harness_provenance(
                        "claude stream-json assistant frame", observed_at=created_at
                    ),
                    role="assistant",
                    kind="message",
                    phase="completed",
                    blocks=tuple(blocks),
                    agent=agent,
                    created_at=created_at,
                    evidence_ref=evidence_ref,
                )
            ),
        )
    return outputs


def _map_tool_use(
    block: Mapping[str, object],
    *,
    parent_item_id: str,
    created_at: str | None,
    agent: ConversationAgentRef | None = None,
) -> list[MapperOutput]:
    tool_id = required_text(block.get("id"), "claude tool_use id")
    name = required_text(block.get("name"), "claude tool_use name")
    return [
        MappedItem(
            item=ConversationItem(
                item_id=tool_id,
                revision=1,
                global_ordinal=1,
                parent_item_id=parent_item_id,
                lane="harness",
                source="harness-live",
                provenance=harness_provenance(
                    "claude stream-json tool_use block", observed_at=created_at
                ),
                role="tool",
                kind="tool-call",
                phase="streaming",
                blocks=(
                    ToolInputBlock(
                        block_id="input",
                        summary=name,
                        data=block.get("input"),
                    ),
                    *_tool_mutation_diff_blocks(name, block.get("input")),
                ),
                correlation=ConversationCorrelation(tool_call_id=tool_id),
                agent=agent,
                created_at=created_at,
            )
        )
    ]


def _edit_diff_blocks(
    tool_input: Mapping[str, object],
) -> tuple[ConversationContentBlock, ...]:
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        return ()
    return (
        DiffBlock(
            block_id="diff-0",
            path=optional_text(tool_input.get("file_path")),
            old_text=old,
            new_text=new,
        ),
    )


def _multi_edit_diff_blocks(
    tool_input: Mapping[str, object],
) -> tuple[ConversationContentBlock, ...]:
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        return ()
    path = optional_text(tool_input.get("file_path"))
    blocks: list[ConversationContentBlock] = []
    for index, raw_edit in enumerate(edits):
        if not isinstance(raw_edit, Mapping):
            continue
        old = raw_edit.get("old_string")
        new = raw_edit.get("new_string")
        if isinstance(old, str) and isinstance(new, str):
            blocks.append(
                DiffBlock(
                    block_id=f"diff-{index}",
                    path=path,
                    old_text=old,
                    new_text=new,
                )
            )
    return tuple(blocks)


def _write_diff_blocks(
    tool_input: Mapping[str, object],
) -> tuple[ConversationContentBlock, ...]:
    content = tool_input.get("content")
    if not isinstance(content, str):
        return ()
    # The old file state never crosses the wire, so this is honestly the written
    # content (all-additions), not a fabricated against-disk diff.
    return (
        DiffBlock(
            block_id="diff-0",
            path=optional_text(tool_input.get("file_path")),
            new_text=content,
        ),
    )


def _notebook_edit_diff_blocks(
    tool_input: Mapping[str, object],
) -> tuple[ConversationContentBlock, ...]:
    new_source = tool_input.get("new_source")
    if not isinstance(new_source, str):
        return ()
    return (
        DiffBlock(
            block_id="diff-0",
            path=optional_text(tool_input.get("notebook_path")),
            new_text=new_source,
        ),
    )


_TOOL_MUTATION_DIFF_MAPPERS: dict[
    str,
    Callable[[Mapping[str, object]], tuple[ConversationContentBlock, ...]],
] = {
    "Edit": _edit_diff_blocks,
    "MultiEdit": _multi_edit_diff_blocks,
    "Write": _write_diff_blocks,
    "NotebookEdit": _notebook_edit_diff_blocks,
}


def _tool_mutation_diff_blocks(
    name: str, tool_input: object
) -> tuple[ConversationContentBlock, ...]:
    """Diff blocks for Claude's file-mutating tools, derived from the tool_use input.

    The input already carries the exact change (old/new strings for Edit, the written
    content for Write), so the projection shows WHAT changed — the changed line sets the
    other harnesses render via DiffBlock — not just that something changed. Only the
    harness's own input is re-shaped; nothing is diffed against disk state we never saw.
    Unknown input shapes contribute no diff (the raw ToolInputBlock still carries them).

    Shape checks are required at this boundary because Claude tool input is vendor-owned
    data. A malformed or unsupported shape retains its raw ToolInputBlock and contributes
    no synthesized diff.
    """

    if not isinstance(tool_input, Mapping):
        return ()
    mapper = _TOOL_MUTATION_DIFF_MAPPERS.get(name)
    if mapper is None:
        return ()
    return mapper(tool_input)


def _map_tool_carrier(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
    evidence_ref: str,
) -> list[MapperOutput]:
    """Map a non-replay user frame (the tool_result carrier)."""

    if raw.get("isReplay") is True:
        raise UnmappableShape("replayed user frames are consumed as submission echoes")
    message = required_object(raw.get("message"), "claude user frame.message")
    content = message.get("content")
    if isinstance(content, str):
        # Claude Code records local slash-command turns (<command-name>…, <local-command-stdout>,
        # caveat wrappers) as user frames whose content is a bare STRING, not a block list. One
        # such frame must never kill the projection (a mid-transcript /effort
        # record crash-looped the replay — generation churn, dead cursors, "structured surface
        # unavailable" for the whole session). Preserve it instead.
        return [
            MappedUnknownVendor(
                item_id=f"claude-user-{raw.get('uuid') or evidence_ref.rsplit(':', 1)[-1]}-text",
                vendor_type="claude-user-content:text",
                safe_summary=f"claude user text frame: {content.strip()[:80] or 'empty'}",
                created_at=created_at,
            )
        ]
    if not isinstance(content, list):
        raise UnmappableShape("claude user frame content must be a list of blocks")
    agent = _sidechain_agent_ref(raw)
    session_id = _session_key(raw)
    outputs: list[MapperOutput] = []
    sidechain_text_blocks: list[ConversationContentBlock] = []
    saw_tool_result = False
    for position, raw_block in enumerate(content):
        block = required_object(raw_block, "claude user content block")
        block_type = required_text(block.get("type"), "claude content block type")
        if block_type == "tool_result":
            saw_tool_result = True
            tool_use_id = optional_text(block.get("tool_use_id"))
            block_agent = agent
            if block_agent is None and tool_use_id is not None:
                # A parent-timeline result settling a bound Agent call carries the
                # roster identity the task_* evidence bound to that join key.
                block_agent = _spawned_agent_ref(session_id, tool_use_id)
            outputs.append(_map_tool_result(block, created_at=created_at, agent=block_agent))
        elif block_type == "text" and agent is not None:
            # A sidechain user frame's text is the sub-agent's own input record (the
            # 2.1.220 probe shows the task prompt echo as the first sidechain user
            # frame); with --forward-subagent-text its replies cross too. Parent
            # frames keep the unknown-vendor path below — user text there is only
            # ever the replay echo, consumed via map_transcript_echo.
            sidechain_text_blocks.append(
                TextBlock(block_id=f"text-{position}", text=str(block.get("text") or ""))
            )
        else:
            outputs.append(
                MappedUnknownVendor(
                    item_id=f"claude-user-{raw.get('uuid') or evidence_ref.rsplit(':', 1)[-1]}-{position}",
                    vendor_type=f"claude-user-block:{block_type}",
                    safe_summary=f"claude user-frame content block of type {block_type}",
                    created_at=created_at,
                )
            )
    if sidechain_text_blocks:
        outputs.insert(
            0,
            MappedItem(
                item=ConversationItem(
                    item_id=required_text(raw.get("uuid"), "claude user frame uuid"),
                    revision=1,
                    global_ordinal=1,
                    lane="harness",
                    source="harness-live",
                    provenance=harness_provenance(
                        "claude stream-json sidechain user frame", observed_at=created_at
                    ),
                    role="user",
                    kind="message",
                    phase="completed",
                    blocks=tuple(sidechain_text_blocks),
                    agent=agent,
                    created_at=created_at,
                    evidence_ref=evidence_ref,
                )
            ),
        )
    if not saw_tool_result and not outputs:
        raise UnmappableShape("claude user frame carried no mappable blocks")
    return outputs


def _map_tool_result(
    block: Mapping[str, object],
    *,
    created_at: str | None,
    agent: ConversationAgentRef | None = None,
) -> MappedItem:
    tool_use_id = required_text(block.get("tool_use_id"), "claude tool_result tool_use_id")
    content = block.get("content")
    text_parts: list[str] = []
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text") or ""))
    is_error = block.get("is_error") is True
    return MappedItem(
        item=ConversationItem(
            item_id=tool_use_id,
            revision=1,
            global_ordinal=1,
            lane="harness",
            source="harness-live",
            provenance=harness_provenance(
                "claude stream-json tool_result block", observed_at=created_at
            ),
            role="tool",
            kind="tool-call",
            phase="failed" if is_error else "completed",
            blocks=(
                ToolOutputBlock(
                    block_id="output",
                    text="\n".join(text_parts) if text_parts else None,
                    data={"isError": is_error},
                ),
            ),
            correlation=ConversationCorrelation(tool_call_id=tool_use_id),
            agent=agent,
            created_at=created_at,
        )
    )


def _map_result(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
) -> list[MapperOutput]:
    uuid = required_text(raw.get("uuid"), "claude result frame uuid")
    outcome: TerminalOutcomeValue
    # The adapter-attributed correlated classification is the authority when present: claude
    # answers an accepted interrupt with a plain error_during_execution/is_error result, so
    # only the adapter's accepted-interrupt correlation distinguishes interrupted from failed.
    # Native-frame classification remains the fallback for stamp-less evidence (older fixtures,
    # foreign streams).
    stamped = raw.get(AR_TERMINAL_OUTCOME_KEY)
    if stamped == "completed":
        outcome = "completed"
    elif stamped == "cancelled":
        outcome = "interrupted"
    elif stamped == "failed":
        outcome = "failed"
    elif raw.get("subtype") == "success" and raw.get("is_error") is False:
        outcome = "completed"
    elif raw.get("terminal_reason") in _CANCEL_REASONS:
        outcome = "interrupted"
    else:
        outcome = "failed"
    detail: str | None = None
    result_text = raw.get("result")
    errors = raw.get("errors")
    if isinstance(result_text, str):
        detail = result_text
    elif isinstance(errors, list):
        detail = "\n".join(item for item in errors if isinstance(item, str))
    stop_reason = optional_text(raw.get("stop_reason")) or optional_text(raw.get("terminal_reason"))
    outputs: list[MapperOutput] = [
        MappedItem(
            item=ConversationItem(
                item_id=f"{uuid}:result",
                revision=1,
                global_ordinal=1,
                lane="harness",
                source="harness-live",
                provenance=harness_provenance(
                    "claude stream-json result frame", observed_at=created_at
                ),
                role="system",
                kind="turn-result",
                phase=outcome,
                blocks=(),
                created_at=created_at,
            )
        ),
        MappedTurnOutcome(outcome=outcome, turn_id=None, stop_reason=stop_reason or detail),
    ]
    return outputs


__all__ = ["HARNESS", "map_evidence_frame", "map_transcript_echo"]
