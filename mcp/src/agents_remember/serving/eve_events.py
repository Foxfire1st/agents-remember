"""Translate eve's documented stream events into the normalized AR adapter event model.

Three rules govern every mapping below, and each is a protocol guarantee rather than a
preference:

1. **Deltas are display, completions are content.** ``message.appended``/``reasoning.appended``
   carry incremental text; ``message.completed``/``reasoning.completed`` carry the authoritative
   finalized block. The adapter materializes a block from its completion, so a block is never
   rendered twice, and a turn cancelled before its completion contributes no assistant text --
   which is exactly what eve discards from durable history on cancellation.
2. **Turn boundaries are not session boundaries.** ``turn.completed``/``turn.failed``/
   ``turn.cancelled`` settle one turn; ``session.waiting`` parks the session while leaving it
   usable for the next message; only ``session.completed``/``session.failed`` retire it.
3. **Identity is exact.** Every turn-scoped event must name a turn this session already opened.
   An event naming an unknown turn is refused instead of applied, so an interleaved child or
   parallel session can never mutate this session's state.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AcceptanceState,
    ActivityState,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    ControlState,
)
from agents_remember.models.conversations.evidence import AR_EVIDENCE_KEY
from agents_remember.serving.eve_interactions import (
    EveInteractionQueue,
    authorization_interaction_id,
)
from agents_remember.serving.eve_protocol import (
    AUTHORIZATION_COMPLETED_EVENT_TYPE,
    AUTHORIZATION_REQUIRED_EVENT_TYPE,
    INTERACTION_REQUEST_EVENT_TYPE,
    INTERACTION_RESOLVED_EVENT_TYPE,
    EveStreamEvent,
)
from agents_remember.serving.harness_control_models import (
    AdapterCapability,
    AdapterEvent,
    TerminalResult,
    TranscriptEntry,
)

Clock = Callable[[], str]

TOOL_ENTRY_LIMIT = 512
"""Bounded tool-call bookkeeping for one mapper.

The retained map exists only to label an ``action.result`` with the tool name its call already
carried; a call whose entry has been evicted still produces its result entry from the frame's own
fields, so the bound degrades a label and never an event.
"""

EVE_PROTOCOL_ID = "eve-http-session/v1"


@dataclass(frozen=True)
class EveSnapshotInputs:
    """Everything one published snapshot is built from besides the mapper's own state.

    The stream cursor is deliberately NOT here: the mapper is the single owner of the position it
    publishes, so a caller cannot publish a snapshot that disagrees with the cursor its own events
    advanced.
    """

    control: ControlState = "ready"
    activity: ActivityState | None = None
    acceptance: AcceptanceState | None = None
    extras: Mapping[str, object] | None = None
    capabilities: frozenset[AdapterCapability] | None = None


@dataclass(frozen=True)
class EveEventPayload:
    """What one emitted adapter event carries besides its kind and the native frame.

    A combination belongs to a specific native event, so it is chosen once as a payload instead of
    being threaded as five independently defaulted switches through every handler.
    """

    snapshot: AdapterSnapshot | None = None
    transcript: tuple[TranscriptEntry, ...] = ()
    evidence: EveStreamEvent | None = None
    operation: ControlOperationRef | None = None
    terminal: TerminalResult | None = None


EMPTY_EVENT_PAYLOAD = EveEventPayload()


class EveEventMapper:
    """Own one eve session's normalized state, transcript accumulator, and pending interactions."""

    def __init__(
        self,
        identity: ControlIdentity,
        *,
        interaction_limit: int,
        clock: Clock,
    ) -> None:
        self._identity = identity
        self._clock = clock
        self._event_sequence = 0
        self._transcript_sequence = 0
        self._session_id: str | None = None
        self._session_terminal: TerminalResult | None = None
        self._turns: set[str] = set()
        self._tool_names: OrderedDict[str, str] = OrderedDict()
        self._interactions = EveInteractionQueue(limit=interaction_limit, clock=clock)
        self._cursor = 0
        self._snapshot: AdapterSnapshot | None = None

    @property
    def snapshot(self) -> AdapterSnapshot:
        if self._snapshot is None:
            raise HarnessControlError("eve event mapper has no state snapshot")
        return self._snapshot

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    @property
    def interactions(self) -> EveInteractionQueue:
        return self._interactions

    @property
    def session_terminal(self) -> TerminalResult | None:
        return self._session_terminal

    # -- published state ------------------------------------------------------------------

    def publish(self, inputs: EveSnapshotInputs) -> AdapterSnapshot:
        """Build and retain one snapshot from explicit inputs plus the mapper's own state."""

        activity, acceptance = self._derive(inputs)
        raw: dict[str, object] = {
            "vendorProtocol": EVE_PROTOCOL_ID,
            "streamCursor": self._cursor,
            **dict(inputs.extras or {}),
        }
        self._snapshot = AdapterSnapshot(
            identity=self._identity,
            control=inputs.control,
            activity=activity,
            acceptance=acceptance,
            vendor_session_id=self._session_id,
            pending_interaction=self._interactions.head(),
            last_event_sequence=self._event_sequence,
            raw=raw,
        )
        return self._snapshot

    def bind_session(self, session_id: str) -> AdapterSnapshot:
        """Record the durable session id native evidence proved, and publish it.

        The identity becomes observable at the moment it is proved, not at the first event the
        session happens to emit. A bridge that has attached to an existing durable session must be
        able to report which one before any new event arrives.
        """

        if self._session_id is not None and self._session_id != session_id:
            raise HarnessControlError("eve session identity changed inside one bridge epoch")
        self._session_id = session_id
        return self.publish(EveSnapshotInputs(activity="running", acceptance="queued"))

    def publish_dispatching(self) -> AdapterSnapshot:
        """Publish the accepted-but-not-yet-observed window after a native write."""

        return self.publish(
            EveSnapshotInputs(
                activity="running",
                acceptance="queued",
            )
        )

    def disconnected(self, detail: str) -> AdapterEvent:
        self.publish(
            EveSnapshotInputs(
                control="disconnected",
                activity="unknown",
                acceptance="unknown",
            )
        )
        return self._next_event(
            "disconnected",
            {"type": "disconnect", "detail": detail},
            _snapshot_payload(self.snapshot),
        )

    def failed(self, detail: str) -> AdapterEvent:
        self.publish(
            EveSnapshotInputs(
                control="failed",
                activity="unknown",
                acceptance="rejected",
            )
        )
        return self._next_event(
            "failed", {"type": "failed", "detail": detail}, _snapshot_payload(self.snapshot)
        )

    def reconnected(self) -> AdapterEvent:
        self.publish(EveSnapshotInputs())
        return self._next_event("state", {"type": "reconnected"}, _snapshot_payload(self.snapshot))

    def response_payload(self, interaction_id: str, response: str) -> dict[str, object]:
        """Build the strict ``inputResponses`` entry for one pending request.

        eve validates a response against ``{requestId, optionId?|text?}``. A response naming one of
        the request's own options is an ``optionId``; anything else is free text, which eve accepts
        only where the request allows it.
        """

        pending = self._interactions.get(interaction_id)
        if pending is None:
            raise HarnessControlError(f"eve interaction {interaction_id!r} is not pending")
        if response in pending.choices:
            return {"requestId": interaction_id, "optionId": response}
        return {"requestId": interaction_id, "text": response}

    def complete_response(self, interaction_id: str) -> AdapterEvent:
        """Drop one answered request and publish the resulting pending set."""

        if not self._interactions.resolve(interaction_id):
            raise HarnessControlError(f"eve interaction {interaction_id!r} is not pending")
        self.publish(EveSnapshotInputs())
        return self._next_event(
            "state",
            {"type": "response.submitted", "requestId": interaction_id},
            _snapshot_payload(self.snapshot),
        )

    # -- translation ----------------------------------------------------------------------

    def translate(self, event: EveStreamEvent, *, cursor: int) -> AdapterEvent:
        """Map one native event; the caller advances its persisted cursor after this returns."""

        self._cursor = max(self._cursor, cursor)
        handler = _HANDLERS.get(event.type)
        if handler is None:
            return self._vendor_detail(event)
        return handler(self, event)

    def _session_started(self, event: EveStreamEvent) -> AdapterEvent:
        if self._session_id is None:
            raise HarnessControlError("eve session.started arrived before a durable session id")
        self.publish(EveSnapshotInputs())
        return self._next_event("state", event.raw_frame(), _frame_payload(self.snapshot, event))

    def _turn_started(self, event: EveStreamEvent) -> AdapterEvent:
        self._turns.add(self._require_turn_id(event))
        self.publish(EveSnapshotInputs(activity="running", acceptance="immediate"))
        return self._next_event("state", event.raw_frame(), _frame_payload(self.snapshot, event))

    def _message_appended(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        return self._delta_event(event, "messageDelta", "message")

    def _reasoning_appended(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        return self._delta_event(event, "reasoningDelta", "reasoning")

    def _delta_event(self, event: EveStreamEvent, key: str, block: str) -> AdapterEvent:
        delta = _required_text(event, key)
        entry = self._entry(
            "assistant",
            delta,
            event,
            raw_extra={"block": block, "streaming": True},
        )
        return self._next_event(
            "delta", event.raw_frame(), _frame_payload(None, event, transcript=(entry,))
        )

    def _message_completed(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        message = event.data.get("message")
        if message is None:
            # eve's documented intentional-silence delivery: the block is final and empty.
            return self._next_event("state", event.raw_frame(), _frame_payload(None, event))
        if not isinstance(message, str):
            raise HarnessControlError("eve message.completed requires string or null message")
        entry = self._entry(
            "assistant",
            message,
            event,
            raw_extra={"block": "message", "finishReason": _optional_text(event, "finishReason")},
        )
        return self._next_event(
            "transcript", event.raw_frame(), _frame_payload(None, event, transcript=(entry,))
        )

    def _reasoning_completed(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        reasoning = event.data.get("reasoning")
        if not isinstance(reasoning, str):
            raise HarnessControlError("eve reasoning.completed requires a string reasoning block")
        entry = self._entry("assistant", reasoning, event, raw_extra={"block": "reasoning"})
        return self._next_event(
            "transcript", event.raw_frame(), _frame_payload(None, event, transcript=(entry,))
        )

    def _actions_requested(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        actions = _required_sequence(event, "actions")
        presentation = event.data.get("presentation")
        labels: Mapping[str, object] = presentation if isinstance(presentation, Mapping) else {}
        entries = tuple(
            self._action_entry(action, event, labels)
            for action in actions
            if isinstance(action, Mapping)
        )
        if not entries:
            raise HarnessControlError("eve actions.requested carried no readable action")
        return self._next_event(
            "transcript", event.raw_frame(), _frame_payload(None, event, transcript=entries)
        )

    def _action_result(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        result = _required_mapping(event, "result")
        entry = self._tool_result_entry(result, event, partial=False)
        return self._next_event(
            "transcript", event.raw_frame(), _frame_payload(None, event, transcript=(entry,))
        )

    def _action_partial(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        result = _required_mapping(event, "result")
        entry = self._tool_result_entry(result, event, partial=True)
        return self._next_event(
            "transcript", event.raw_frame(), _frame_payload(None, event, transcript=(entry,))
        )

    def _tool_result_entry(
        self,
        result: Mapping[str, object],
        event: EveStreamEvent,
        *,
        partial: bool,
    ) -> TranscriptEntry:
        call_id = _map_text(result, "callId")
        tool_name = _map_text(result, "toolName") or self._tool_names.get(call_id) or call_id
        return self._entry(
            "result",
            _render_output(result.get("output")),
            event,
            raw_extra={
                "toolName": tool_name,
                "callId": call_id,
                "status": _optional_text(event, "status")
                or ("partial" if partial else "completed"),
                "partial": partial,
            },
            correlation_id=call_id or None,
        )

    def _interaction_requested(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        for request in _required_sequence(event, "requests"):
            if not isinstance(request, Mapping):
                raise HarnessControlError("eve input.requested entries must be objects")
            self._interactions.add_input_request(request)
        self.publish(EveSnapshotInputs(activity="blocked", acceptance="rejected"))
        return self._next_event("state", event.raw_frame(), _frame_payload(self.snapshot, event))

    def _interaction_resolved(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        resolved: list[str] = []
        for resolution in _required_sequence(event, "resolutions"):
            if not isinstance(resolution, Mapping):
                raise HarnessControlError("eve input.resolved entries must be objects")
            request_id = _map_text(resolution, "requestId")
            if request_id and self._interactions.resolve(request_id):
                resolved.append(request_id)
        self.publish(EveSnapshotInputs())
        return self._next_event(
            "state",
            {**event.raw_frame(), "resolvedRequestIds": resolved},
            _frame_payload(self.snapshot, event),
        )

    def _authorization_required(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        name = _optional_text(event, "name") or "authorization"
        description = _optional_text(event, "description") or f"authorization required: {name}"
        self._interactions.add_authorization(
            name=name,
            description=description,
            raw=event.raw_frame(),
        )
        self.publish(EveSnapshotInputs(activity="blocked", acceptance="rejected"))
        return self._next_event("state", event.raw_frame(), _frame_payload(self.snapshot, event))

    def _authorization_completed(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        name = _optional_text(event, "name") or "authorization"
        self._interactions.resolve(authorization_interaction_id(name))
        self.publish(EveSnapshotInputs())
        return self._next_event("state", event.raw_frame(), _frame_payload(self.snapshot, event))

    def _turn_completed(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        self._tool_names.clear()
        self.publish(EveSnapshotInputs(activity="settling", acceptance="queued"))
        return self._next_event("state", event.raw_frame(), _frame_payload(self.snapshot, event))

    def _turn_failed(self, event: EveStreamEvent) -> AdapterEvent:
        return self._turn_settled(event, outcome="failed")

    def _turn_cancelled(self, event: EveStreamEvent) -> AdapterEvent:
        return self._turn_settled(event, outcome="cancelled")

    def _turn_settled(
        self,
        event: EveStreamEvent,
        *,
        outcome: Literal["completed", "failed", "cancelled"],
    ) -> AdapterEvent:
        turn_id = self._require_turn_id(event)
        self._turns.discard(turn_id)
        self._tool_names.clear()
        self.publish(EveSnapshotInputs(activity="idle", acceptance="queued"))
        terminal = TerminalResult(
            outcome=outcome,
            completed_at=self._clock(),
            detail=_optional_text(event, "message"),
            raw=event.raw_frame(),
        )
        return self._next_event(
            "completed" if outcome == "completed" else outcome,
            event.raw_frame(),
            _frame_payload(self.snapshot, event, terminal=terminal),
        )

    def _session_waiting(self, event: EveStreamEvent) -> AdapterEvent:
        self._tool_names.clear()
        self.publish(EveSnapshotInputs(activity="idle", acceptance="queued"))
        terminal = TerminalResult(
            outcome="completed",
            completed_at=self._clock(),
            detail="eve session parked and ready for the next message",
            raw=event.raw_frame(),
        )
        return self._next_event(
            "completed",
            event.raw_frame(),
            _frame_payload(self.snapshot, event, terminal=terminal),
        )

    def _session_ended(self, event: EveStreamEvent) -> AdapterEvent:
        outcome: Literal["completed", "failed"] = (
            "completed" if event.type == "session.completed" else "failed"
        )
        self._session_terminal = TerminalResult(
            outcome=outcome,
            completed_at=self._clock(),
            detail=_optional_text(event, "message"),
            raw=event.raw_frame(),
        )
        self.publish(
            EveSnapshotInputs(
                activity="unknown",
                acceptance="rejected" if outcome == "failed" else "unknown",
            )
        )
        return self._next_event(
            "completed" if outcome == "completed" else "failed",
            event.raw_frame(),
            _frame_payload(self.snapshot, event, terminal=self._session_terminal),
        )

    def _step_failed(self, event: EveStreamEvent) -> AdapterEvent:
        self._require_turn_id(event)
        entry = self._entry(
            "system",
            _failure_text(event),
            event,
            raw_extra={"block": "step.failed"},
        )
        return self._next_event(
            "transcript", event.raw_frame(), _frame_payload(None, event, transcript=(entry,))
        )

    def _compaction_event(self, event: EveStreamEvent) -> AdapterEvent:
        self.publish(
            EveSnapshotInputs(
                activity="settling" if event.type == "compaction.requested" else "idle",
                acceptance="queued",
            )
        )
        return self._next_event("state", event.raw_frame(), _frame_payload(self.snapshot, event))

    def _vendor_detail(self, event: EveStreamEvent) -> AdapterEvent:
        """Carry an uninterpreted event as vendor detail; never as state this adapter cannot prove."""

        return self._next_event(f"eve:{event.type}", event.raw_frame(), _frame_payload(None, event))

    # -- internals ------------------------------------------------------------------------

    def _derive(self, inputs: EveSnapshotInputs) -> tuple[ActivityState, AcceptanceState]:
        """Combine explicit inputs with what the session itself is currently doing.

        Precedence is deliberate and total: a caller that states both axes wins, then a pending
        input request (which refuses further deliveries), then a stated activity, then the
        control state, then the session's own liveness. A caller may therefore state one axis and
        let the other follow the session.
        """

        if inputs.activity is not None and inputs.acceptance is not None:
            return inputs.activity, inputs.acceptance
        if self._interactions:
            return "blocked", "rejected"
        if inputs.activity is not None:
            return inputs.activity, _ACCEPTANCE_FOR_ACTIVITY[inputs.activity]
        return _CONTROL_ACTIVITY.get(inputs.control) or self._session_activity()

    def _session_activity(self) -> tuple[ActivityState, AcceptanceState]:
        if self._session_terminal is not None:
            return "unknown", "unknown"
        if self._turns:
            return "running", "queued"
        return "idle", "immediate"

    def _require_turn_id(self, event: EveStreamEvent) -> str:
        """Refuse a turn-scoped event that does not name a turn this session already opened.

        ``turn.started`` is the event that opens a turn, so it is the one turn-scoped event whose
        identity is established by its own arrival; every later event must match one already open.
        """

        turn_id = event.turn_id
        if turn_id is None:
            raise HarnessControlError(f"eve {event.type} event carries no turn identity")
        if turn_id not in self._turns and event.type != "turn.started":
            raise HarnessControlError(
                f"eve {event.type} event names turn {turn_id!r}, which this session never opened"
            )
        return turn_id

    def _action_entry(
        self,
        action: Mapping[str, object],
        event: EveStreamEvent,
        labels: Mapping[str, object],
    ) -> TranscriptEntry:
        call_id = _map_text(action, "callId")
        kind = _map_text(action, "kind") or "tool-call"
        tool_name = _map_text(action, "toolName") or _map_text(action, "name") or kind
        if call_id:
            self._tool_names[call_id] = tool_name
            while len(self._tool_names) > TOOL_ENTRY_LIMIT:
                self._tool_names.popitem(last=False)
        label_entry = labels.get(call_id) if call_id else None
        label = _map_text(label_entry, "label") if isinstance(label_entry, Mapping) else ""
        return self._entry(
            "interaction",
            label or tool_name,
            event,
            raw_extra={
                "toolName": tool_name,
                "actionKind": kind,
                "callId": call_id,
                "input": action.get("input"),
            },
            correlation_id=call_id or None,
        )

    def _entry(
        self,
        role: Literal["user", "assistant", "system", "interaction", "result"],
        text: str,
        event: EveStreamEvent,
        *,
        raw_extra: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> TranscriptEntry:
        self._transcript_sequence += 1
        return TranscriptEntry(
            sequence=self._transcript_sequence,
            role=role,
            text=text,
            created_at=self._clock(),
            vendor_correlation_id=correlation_id,
            raw={
                "eveEventType": event.type,
                "eveEventIndex": event.index,
                "eveEventId": event.event_id,
                "turnId": event.turn_id,
                **dict(raw_extra or {}),
            },
        )

    def _next_event(
        self,
        kind: str,
        frame: Mapping[str, object],
        payload: EveEventPayload = EMPTY_EVENT_PAYLOAD,
    ) -> AdapterEvent:
        snapshot = payload.snapshot
        transcript = payload.transcript
        evidence = payload.evidence
        terminal = payload.terminal
        self._event_sequence += 1
        if snapshot is not None:
            snapshot = replace(snapshot, last_event_sequence=self._event_sequence)
            self._snapshot = snapshot
        raw: dict[str, object] = {"eveEvent": dict(frame)}
        if evidence is not None:
            raw[AR_EVIDENCE_KEY] = dict(frame)
            raw["eveEventIndex"] = evidence.index
            raw["eveEventId"] = evidence.event_id
        if terminal is not None:
            raw["terminalResult"] = {
                "outcome": terminal.outcome,
                "completedAt": terminal.completed_at,
                "detail": terminal.detail,
            }
            if transcript:
                transcript = tuple(replace(entry, terminal_result=terminal) for entry in transcript)
        return AdapterEvent(
            sequence=self._event_sequence,
            kind=kind,
            identity=self._identity,
            created_at=self._clock(),
            snapshot=snapshot,
            transcript=transcript,
            raw=raw,
            operation=payload.operation,
        )


def _snapshot_payload(snapshot: AdapterSnapshot) -> EveEventPayload:
    return EveEventPayload(snapshot=snapshot)


def _frame_payload(
    snapshot: AdapterSnapshot | None,
    event: EveStreamEvent,
    *,
    transcript: tuple[TranscriptEntry, ...] = (),
    terminal: TerminalResult | None = None,
) -> EveEventPayload:
    return EveEventPayload(
        snapshot=snapshot,
        transcript=transcript,
        evidence=event,
        terminal=terminal,
    )


_CONTROL_ACTIVITY: dict[ControlState, tuple[ActivityState, AcceptanceState]] = {
    "failed": ("unknown", "rejected"),
    "disconnected": ("unknown", "unknown"),
    "unsupported": ("unknown", "unsupported"),
}
"""Activity a control state implies on its own; an unlisted state defers to the session."""


_ACCEPTANCE_FOR_ACTIVITY: dict[ActivityState, AcceptanceState] = {
    "idle": "immediate",
    "running": "queued",
    "settling": "queued",
    "blocked": "rejected",
    "unknown": "unknown",
}


_HANDLERS: dict[str, Callable[[EveEventMapper, EveStreamEvent], AdapterEvent]] = {
    "session.started": EveEventMapper._session_started,
    "turn.started": EveEventMapper._turn_started,
    "message.appended": EveEventMapper._message_appended,
    "reasoning.appended": EveEventMapper._reasoning_appended,
    "message.completed": EveEventMapper._message_completed,
    "reasoning.completed": EveEventMapper._reasoning_completed,
    "actions.requested": EveEventMapper._actions_requested,
    "action.result": EveEventMapper._action_result,
    "action.partial": EveEventMapper._action_partial,
    INTERACTION_REQUEST_EVENT_TYPE: EveEventMapper._interaction_requested,
    INTERACTION_RESOLVED_EVENT_TYPE: EveEventMapper._interaction_resolved,
    AUTHORIZATION_REQUIRED_EVENT_TYPE: EveEventMapper._authorization_required,
    AUTHORIZATION_COMPLETED_EVENT_TYPE: EveEventMapper._authorization_completed,
    "turn.completed": EveEventMapper._turn_completed,
    "turn.failed": EveEventMapper._turn_failed,
    "turn.cancelled": EveEventMapper._turn_cancelled,
    "step.failed": EveEventMapper._step_failed,
    "session.waiting": EveEventMapper._session_waiting,
    "session.completed": EveEventMapper._session_ended,
    "session.failed": EveEventMapper._session_ended,
    "compaction.requested": EveEventMapper._compaction_event,
    "compaction.completed": EveEventMapper._compaction_event,
    "context.cleared": EveEventMapper._compaction_event,
}
"""Event type to handler. Types absent from this table stay additive vendor detail:
``message.received``, ``step.started``, ``step.completed``, ``result.completed``,
``subagent.*``, ``approval.*`` and ``action.input.appended`` cross as evidence without being
reinterpreted into AR state this adapter cannot prove."""


def _required_text(event: EveStreamEvent, key: str) -> str:
    value = event.data.get(key)
    if not isinstance(value, str):
        raise HarnessControlError(f"eve {event.type} requires a string {key}")
    return value


def _optional_text(event: EveStreamEvent, key: str) -> str | None:
    value = event.data.get(key)
    return value if isinstance(value, str) and value else None


def _required_sequence(event: EveStreamEvent, key: str) -> Sequence[object]:
    value = event.data.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise HarnessControlError(f"eve {event.type} requires a {key} array")
    return value


def _required_mapping(event: EveStreamEvent, key: str) -> Mapping[str, object]:
    value = event.data.get(key)
    if not isinstance(value, Mapping):
        raise HarnessControlError(f"eve {event.type} requires a {key} object")
    return cast(Mapping[str, object], value)


def _map_text(raw: Mapping[str, object] | None, key: str) -> str:
    if raw is None:
        return ""
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def _failure_text(event: EveStreamEvent) -> str:
    code = _optional_text(event, "code") or event.type
    message = _optional_text(event, "message") or ""
    return f"{code}: {message}".strip(": ").strip()


def _render_output(output: object) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(output)
