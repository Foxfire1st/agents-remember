"""Independent provider-frame scripts replayed through the structural control port."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlOperationRef,
)
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_KEY,
    AR_TERMINAL_OUTCOME_KEY,
)
from agents_remember.serving.harness_control_models import TranscriptEntry

NOW = "2026-07-18T08:00:00Z"


class AdapterReplayPort(Protocol):
    """Minimum caller-scripted surface; it owns no provider settlement policy."""

    vendor_id: str
    current: AdapterSnapshot | None
    active_turn: str | None
    operations: list[ControlOperationRef | None]
    transcript_sequence: int

    def emit(
        self,
        kind: str,
        raw: Mapping[str, object],
        *,
        transcript: tuple[TranscriptEntry, ...] = (),
        snapshot: AdapterSnapshot | None = None,
        operation: ControlOperationRef | None = None,
    ) -> None: ...


def replay_codex_terminal(adapter: AdapterReplayPort, outcome: str) -> None:
    """Replay one observed Codex ``turn/completed`` frame."""

    turn = adapter.active_turn or "turn-unknown"
    adapter.active_turn = None
    adapter.emit(
        "completed",
        {
            "codexMethod": "turn/completed",
            AR_EVIDENCE_KEY: {
                "threadId": adapter.vendor_id,
                "turn": {"id": turn, "status": outcome, "items": []},
            },
        },
        snapshot=replace(adapter.current, activity="idle", raw={}) if adapter.current else None,
        operation=_current_operation(adapter),
    )


def replay_pi_terminal(
    adapter: AdapterReplayPort,
    stop_reason: str,
    *,
    text: str | None = None,
) -> None:
    """Replay Pi's terminal message frame followed by its independent release frame."""

    if text is None:
        adapter.emit(
            "pi:message_end",
            {
                "piEvent": {"type": "message_end"},
                AR_EVIDENCE_KEY: {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "stopReason": stop_reason,
                    },
                },
            },
            snapshot=_idle_snapshot(adapter),
        )
    else:
        replay_pi_message_end(adapter, text=text, stop_reason=stop_reason)
    replay_pi_release(adapter)


def replay_pi_message_end(adapter: AdapterReplayPort, *, text: str, stop_reason: str) -> None:
    """Replay one content-bearing Pi ``message_end`` without releasing the operation."""

    adapter.transcript_sequence += 1
    frame = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stopReason": stop_reason,
        },
    }
    adapter.emit(
        "transcript",
        {"piEvent": {"type": "message_end"}, AR_EVIDENCE_KEY: dict(frame)},
        transcript=(
            TranscriptEntry(
                sequence=adapter.transcript_sequence,
                role="assistant",
                text=text,
                created_at=NOW,
                raw=dict(frame),
            ),
        ),
        snapshot=_idle_snapshot(adapter),
    )


def replay_pi_release(adapter: AdapterReplayPort) -> None:
    """Replay Pi's operation-correlated ``agent_end`` release."""

    adapter.emit(
        "completed",
        {"piEvent": {"type": "agent_end"}},
        snapshot=_idle_snapshot(adapter),
        operation=_current_operation(adapter),
    )


def replay_claude_terminal(adapter: AdapterReplayPort, outcome: str) -> None:
    """Replay one independently specified Claude result frame."""

    adapter.emit(
        "completed",
        {
            "terminalOutcome": outcome,
            AR_EVIDENCE_KEY: {
                "type": "result",
                "subtype": "success" if outcome == "completed" else "error_during_execution",
                "is_error": outcome != "completed",
                "terminal_reason": "aborted_streaming" if outcome == "cancelled" else None,
                "session_id": adapter.vendor_id,
                "uuid": f"claude-result-{outcome}",
                AR_TERMINAL_OUTCOME_KEY: outcome,
            },
        },
        snapshot=_idle_snapshot(adapter),
        operation=_current_operation(adapter),
    )


def _idle_snapshot(adapter: AdapterReplayPort) -> AdapterSnapshot | None:
    return replace(adapter.current, activity="idle") if adapter.current else None


def _current_operation(adapter: AdapterReplayPort) -> ControlOperationRef | None:
    return adapter.operations[-1] if adapter.operations else None
