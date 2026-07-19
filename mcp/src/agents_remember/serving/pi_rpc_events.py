"""Documented Pi RPC event, activity, transcript, and extension-UI mapping."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Literal, cast

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    AdapterEvent,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    InteractionResponse,
    PendingInteraction,
    TranscriptEntry,
)
from agents_remember.serving.pi_rpc_protocol import (
    PI_RPC_ACTIVITY_EVENTS,
    PI_RPC_DIALOG_METHODS,
    PI_RPC_FIRE_AND_FORGET_METHODS,
    PI_RPC_PROTOCOL,
    PiSessionState,
    extension_response,
    pi_extension_display_text,
    pi_message_text,
    pi_queue_update_count,
    required_pi_text,
)

Clock = Callable[[], str]


class PiRpcEventMapper:
    """Own normalized Pi event state and one bounded durable dialog queue."""

    def __init__(
        self,
        identity: ControlIdentity,
        *,
        interaction_limit: int,
        clock: Clock,
    ) -> None:
        self._identity = identity
        self._interaction_limit = interaction_limit
        self._clock = clock
        self._dialogs: OrderedDict[str, Mapping[str, object]] = OrderedDict()
        self._event_sequence = 0
        self._transcript_sequence = 0
        self._snapshot: AdapterSnapshot | None = None

    @property
    def snapshot(self) -> AdapterSnapshot:
        if self._snapshot is None:
            raise HarnessControlError("Pi RPC event mapper has no state snapshot")
        return self._snapshot

    @property
    def retained_interaction_count(self) -> int:
        return len(self._dialogs)

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    def apply_state(self, state: PiSessionState, *, cursor: str | None) -> AdapterSnapshot:
        pending = self._pending_interaction()
        activity = _state_activity(state, blocked=pending is not None)
        acceptance = (
            "rejected"
            if pending is not None
            else (
                "queued"
                if state.is_streaming or state.is_compacting or state.pending_message_count
                else "immediate"
            )
        )
        self._snapshot = AdapterSnapshot(
            identity=self._identity,
            control="ready",
            activity=activity,
            acceptance=acceptance,
            vendor_session_id=state.session_id,
            pending_interaction=pending,
            last_event_sequence=self._event_sequence,
            raw={
                **dict(state.raw),
                "vendorProtocol": PI_RPC_PROTOCOL,
                "entryCursor": cursor,
            },
        )
        return self._snapshot

    def mark_prompt_accepted(self) -> None:
        """Close the response-to-agent_start idle window after an immediate prompt ack."""

        self._snapshot = replace(self.snapshot, activity="running", acceptance="immediate")

    def response_payload(self, response: InteractionResponse) -> dict[str, object]:
        request = self._dialogs.get(response.interaction_id)
        if request is None:
            raise HarnessControlError("Pi extension UI response has no pending request")
        return extension_response(
            request, interaction_id=response.interaction_id, response=response.response
        )

    def complete_response(self, interaction_id: str) -> None:
        self._dialogs.pop(interaction_id)
        pending = self._pending_interaction()
        self._snapshot = replace(
            self.snapshot,
            activity="blocked" if pending is not None else "settling",
            acceptance="rejected" if pending is not None else "queued",
            pending_interaction=pending,
        )

    def clear(self) -> None:
        self._dialogs.clear()

    def translate(
        self,
        frame: Mapping[str, object],
        *,
        settled_state: PiSessionState | None = None,
        cursor: str | None = None,
        operation: ControlOperationRef | None = None,
    ) -> AdapterEvent:
        event_type = frame.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise HarnessControlError("Pi RPC event requires a non-empty type")
        if event_type == "agent_settled":
            return self._settled_event(
                frame,
                settled_state,
                cursor=cursor,
                operation=operation,
            )
        if event_type == "extension_ui_request":
            return self._extension_event(frame)
        if event_type == "message_end":
            return self._message_event(frame)
        transition = PI_RPC_ACTIVITY_EVENTS.get(event_type)
        if transition is not None:
            return self._state_event(frame, activity=transition[0], acceptance=transition[1])
        if event_type == "queue_update":
            return self._queue_event(frame)
        return self._next_event(f"pi:{event_type}", frame, evidence=frame)

    def disconnected(self, error: HarnessAdapterDisconnectedError) -> AdapterEvent:
        self._snapshot = replace(
            self.snapshot,
            control="disconnected",
            activity="unknown",
            acceptance="unknown",
            raw={**self.snapshot.raw, "disconnect": str(error)},
        )
        return self._next_event("disconnected", {"type": "disconnect"}, snapshot=self.snapshot)

    def failed(self, error: HarnessControlError) -> AdapterEvent:
        self._snapshot = replace(
            self.snapshot,
            control="failed",
            activity="unknown",
            acceptance="rejected",
            raw={**self.snapshot.raw, "adapterError": str(error)},
        )
        return self._next_event("failed", {"type": "failed"}, snapshot=self.snapshot)

    def reconnected(self) -> AdapterEvent:
        return self._next_event("state", {"type": "reconnected"}, snapshot=self.snapshot)

    def _settled_event(
        self,
        frame: Mapping[str, object],
        state: PiSessionState | None,
        *,
        cursor: str | None,
        operation: ControlOperationRef | None,
    ) -> AdapterEvent:
        if state is None:
            raise HarnessControlError("Pi agent_settled requires a correlated get_state snapshot")
        if state.is_streaming or state.is_compacting or state.pending_message_count:
            raise HarnessControlError("Pi agent_settled contradicted get_state activity")
        if operation is None:
            raise HarnessControlError("Pi agent_settled has no active ordinary operation")
        self._dialogs.clear()
        snapshot = self.apply_state(state, cursor=cursor)
        return self._next_event(
            "completed",
            frame,
            snapshot=snapshot,
            operation=operation,
        )

    def _extension_event(self, frame: Mapping[str, object]) -> AdapterEvent:
        interaction_id = required_pi_text(frame, "id")
        method = required_pi_text(frame, "method")
        if method in PI_RPC_DIALOG_METHODS:
            self._add_dialog(interaction_id, frame)
            return self._next_event("state", frame, snapshot=self.snapshot)
        if method not in PI_RPC_FIRE_AND_FORGET_METHODS:
            raise HarnessControlError(f"unsupported Pi extension UI method: {method}")
        entry = self._transcript_entry(
            role="interaction", text=f"{method}: {pi_extension_display_text(frame)}", raw=frame
        )
        return self._next_event("transcript", frame, transcript=(entry,))

    def _add_dialog(self, interaction_id: str, frame: Mapping[str, object]) -> None:
        if interaction_id in self._dialogs:
            raise HarnessControlError(f"duplicate Pi extension UI id: {interaction_id}")
        if len(self._dialogs) >= self._interaction_limit:
            raise HarnessControlError("Pi extension UI dialog queue reached its bounded limit")
        self._dialogs[interaction_id] = dict(frame)
        pending = self._pending_interaction()
        self._snapshot = replace(
            self.snapshot,
            activity="blocked",
            acceptance="rejected",
            pending_interaction=pending,
        )

    def _message_event(self, frame: Mapping[str, object]) -> AdapterEvent:
        message = frame.get("message")
        if not isinstance(message, dict):
            raise HarnessControlError("Pi RPC message_end requires a message object")
        role, text = pi_message_text(cast(Mapping[str, object], message))
        entry = self._transcript_entry(role=role, text=text, raw=frame)
        return self._next_event("transcript", frame, transcript=(entry,), evidence=frame)

    def _queue_event(self, frame: Mapping[str, object]) -> AdapterEvent:
        pending = pi_queue_update_count(frame)
        activity = self.snapshot.activity if pending == 0 else "running"
        acceptance = self.snapshot.acceptance if pending == 0 else "queued"
        self._snapshot = replace(
            self.snapshot,
            activity=activity,
            acceptance=acceptance,
            raw={
                **self.snapshot.raw,
                "pendingMessageCount": pending,
                "piEvent": dict(frame),
            },
        )
        return self._next_event("state", frame, snapshot=self.snapshot)

    def _state_event(
        self,
        frame: Mapping[str, object],
        *,
        activity: Literal["running", "settling"],
        acceptance: Literal["queued"],
    ) -> AdapterEvent:
        self._snapshot = replace(
            self.snapshot,
            activity=activity,
            acceptance=acceptance,
            raw={**self.snapshot.raw, "piEvent": dict(frame)},
        )
        return self._next_event("state", frame, snapshot=self.snapshot)

    def _next_event(
        self,
        kind: str,
        raw: Mapping[str, object],
        *,
        snapshot: AdapterSnapshot | None = None,
        transcript: tuple[TranscriptEntry, ...] = (),
        operation: ControlOperationRef | None = None,
        evidence: Mapping[str, object] | None = None,
    ) -> AdapterEvent:
        self._event_sequence += 1
        if snapshot is not None:
            snapshot = replace(snapshot, last_event_sequence=self._event_sequence)
            self._snapshot = snapshot
        event_raw: dict[str, object] = {"piEvent": dict(raw)}
        if evidence is not None:
            # The full frame rides the reserved key into the evidence buffer; the status-quo
            # piEvent key keeps its exact current shape for snapshot consumers.
            event_raw[AR_EVIDENCE_KEY] = dict(evidence)
        return AdapterEvent(
            sequence=self._event_sequence,
            kind=kind,
            identity=self._identity,
            created_at=self._clock(),
            snapshot=snapshot,
            transcript=transcript,
            raw=event_raw,
            operation=operation,
        )

    def _pending_interaction(self) -> PendingInteraction | None:
        if not self._dialogs:
            return None
        interaction_id, request = next(iter(self._dialogs.items()))
        options = request.get("options")
        choices = (
            tuple(item for item in options if isinstance(item, str))
            if isinstance(options, list)
            else ()
        )
        return PendingInteraction(
            interaction_id=interaction_id,
            kind=required_pi_text(request, "method"),
            prompt=pi_extension_display_text(request),
            created_at=self._clock(),
            choices=choices,
            raw=dict(request),
        )

    def _transcript_entry(
        self,
        *,
        role: Literal["user", "assistant", "interaction"],
        text: str,
        raw: Mapping[str, object],
    ) -> TranscriptEntry:
        self._transcript_sequence += 1
        return TranscriptEntry(
            sequence=self._transcript_sequence,
            role=role,
            text=text,
            created_at=self._clock(),
            raw=dict(raw),
        )


def _state_activity(
    state: PiSessionState, *, blocked: bool
) -> Literal["blocked", "settling", "running", "idle"]:
    if blocked:
        return "blocked"
    if state.is_compacting:
        return "settling"
    if state.is_streaming:
        return "running"
    if state.pending_message_count:
        return "settling"
    return "idle"
