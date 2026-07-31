"""Behavioural tests for the Pi RPC event mapper (``serving.pi_rpc_events``).

The mapper is the only thing that decides what one Pi wire frame *means* to the bridge: whether it
republishes the snapshot, appends a durable transcript entry, or crosses as evidence only. Nothing
downstream can recover a frame the mapper classified wrongly, so these drive real frames through
``translate`` and assert the classification, the snapshot it leaves behind, and the queue arithmetic
that decides whether the seat is accepting input.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_models import ControlIdentity
from agents_remember.serving.pi_rpc_events import PiRpcEventMapper
from agents_remember.serving.pi_rpc_protocol import PiSessionState

NOW = "2026-07-31T10:00:00+00:00"


def _state(**overrides: object) -> PiSessionState:
    fields: dict[str, object] = {
        "session_id": "pi-thread-1",
        "session_file": None,
        "is_streaming": False,
        "is_compacting": False,
        "pending_message_count": 0,
        "thinking_level": "medium",
        "model_key": "pi-default",
        "raw": {"sessionId": "pi-thread-1"},
    }
    fields.update(overrides)
    return PiSessionState(**fields)  # type: ignore[arg-type]


@pytest.fixture
def mapper() -> PiRpcEventMapper:
    made = PiRpcEventMapper(
        ControlIdentity(ar_session_id="pi-1", tmux_name="ar-pi-1", created_at=NOW),
        interaction_limit=4,
        clock=lambda: NOW,
    )
    made.apply_state(_state(), cursor="entry-0")
    return made


def test_a_fire_and_forget_extension_ui_call_becomes_one_transcript_entry(
    mapper: PiRpcEventMapper,
) -> None:
    # ``notify`` and friends need no answer, so they must NOT enter the bounded dialog queue (which
    # would block the seat waiting for a reply that is never coming). They are recorded as what
    # they are: one interaction line in the transcript, with the seat still accepting input.
    event = mapper.translate(
        {"type": "extension_ui_request", "id": "ui-1", "method": "notify", "message": "build done"}
    )

    assert event.kind == "transcript"
    assert [(entry.role, entry.text) for entry in event.transcript] == [
        ("interaction", "notify: build done")
    ]
    assert mapper.retained_interaction_count == 0
    assert mapper.snapshot.pending_interaction is None
    assert mapper.snapshot.acceptance == "immediate"
    # A fire-and-forget call is not new state, so the event republishes no snapshot.
    assert event.snapshot is None


def test_a_dialog_extension_ui_call_blocks_the_seat_instead(mapper: PiRpcEventMapper) -> None:
    event = mapper.translate(
        {"type": "extension_ui_request", "id": "ui-2", "method": "confirm", "message": "proceed?"}
    )

    assert event.kind == "state"
    assert event.transcript == ()
    assert mapper.retained_interaction_count == 1
    assert mapper.snapshot.activity == "blocked"
    assert mapper.snapshot.acceptance == "rejected"


def test_an_unsupported_extension_ui_method_is_refused(mapper: PiRpcEventMapper) -> None:
    with pytest.raises(HarnessControlError) as excinfo:
        mapper.translate({"type": "extension_ui_request", "id": "ui-3", "method": "teleport"})
    assert "unsupported Pi extension UI method: teleport" in str(excinfo.value)


def test_a_queue_update_with_work_pending_marks_the_seat_running_and_queued(
    mapper: PiRpcEventMapper,
) -> None:
    # Pi reports its steering and follow-up queues separately; the seat is busy if EITHER holds
    # anything, so the count is their sum and the acceptance it implies is "queued", not "immediate".
    event = mapper.translate(
        {
            "type": "queue_update",
            "steering": [{"id": "s1"}],
            "followUp": [{"id": "f1"}, {"id": "f2"}],
        }
    )

    assert event.kind == "state"
    assert event.snapshot is not None
    assert event.snapshot.activity == "running"
    assert event.snapshot.acceptance == "queued"
    assert event.snapshot.raw["pendingMessageCount"] == 3
    assert mapper.snapshot.raw["piEvent"] == {
        "type": "queue_update",
        "steering": [{"id": "s1"}],
        "followUp": [{"id": "f1"}, {"id": "f2"}],
    }


def test_an_empty_queue_update_leaves_the_current_activity_alone(
    mapper: PiRpcEventMapper,
) -> None:
    # Draining the queue does not by itself mean the turn ended -- only the activity events say
    # that -- so an empty queue_update must not overwrite the activity the mapper already knows.
    mapper.apply_state(_state(is_streaming=True), cursor="entry-1")
    before = mapper.snapshot.activity

    event = mapper.translate({"type": "queue_update", "steering": [], "followUp": []})

    assert event.snapshot is not None
    assert event.snapshot.activity == before
    assert event.snapshot.raw["pendingMessageCount"] == 0


def test_a_queue_update_without_both_arrays_is_refused(mapper: PiRpcEventMapper) -> None:
    with pytest.raises(HarnessControlError) as excinfo:
        mapper.translate({"type": "queue_update", "steering": []})
    assert "requires steering and followUp arrays" in str(excinfo.value)
