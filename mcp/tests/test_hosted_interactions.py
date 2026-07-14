from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import decide_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    PendingInteraction,
)
from agents_remember.serving.hosted_interactions import HostedInteractionSynchronizer
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

NOW = "2026-07-14T10:00:00+00:00"


def _entry(root: Path) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id="worker-1",
        label="Worker",
        kind="harness",
        harness="codex",
        lifecycle_id="L1",
        cwd=root,
        tmux_name="ar-worker-1",
        command=("codex",),
        created_at=NOW,
        last_attached_at=NOW,
        status="running",
        control_state="ready",
        control_endpoint=root / "control.sock",
        control_protocol="ar-harness-control/v1",
    )


def _snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control="ready",
        activity="blocked",
        acceptance="rejected",
        pending_interaction=PendingInteraction(
            interaction_id="approval-1",
            kind="approval",
            prompt="Allow this action?",
            created_at=NOW,
            choices=("allow", "deny"),
        ),
    )


def test_pending_interaction_round_trips_through_durable_gate(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    synchronizer = HostedInteractionSynchronizer(tmp_path)
    synchronizer.observe(entry, _snapshot(entry))
    store = GateStore(tmp_path)
    gate = next(iter(store.current("L1").values()))
    assert gate.kind == "agent-question"
    assert gate.packet["adapterInteraction"]["interactionId"] == "approval-1"

    store.append(
        decide_gate(
            gate,
            decision="approve",
            by="developer",
            via="dashboard",
            note="allow",
            now=NOW,
        )
    )
    with mock.patch(
        "agents_remember.serving.hosted_interactions.respond_control_interaction"
    ) as respond:
        synchronizer.observe(entry, _snapshot(entry))
    respond.assert_called_once_with(entry, interaction_id="approval-1", response="allow")
    assert store.current("L1")[gate.id].state == "applied"


def test_disappeared_interaction_expires_the_current_open_gate(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    synchronizer = HostedInteractionSynchronizer(tmp_path)
    store = GateStore(tmp_path)

    first = _snapshot(entry)
    synchronizer.observe(entry, first)
    first_gate = next(iter(store.current("L1").values()))
    store.append(
        decide_gate(
            first_gate,
            decision="approve",
            by="developer",
            via="dashboard",
            note="allow",
            now=NOW,
        )
    )
    with mock.patch(
        "agents_remember.serving.hosted_interactions.respond_control_interaction"
    ):
        synchronizer.observe(entry, first)
    assert store.current("L1")[first_gate.id].state == "applied"

    second = replace(
        first,
        pending_interaction=PendingInteraction(
            interaction_id="approval-2",
            kind="approval",
            prompt="Allow the second action?",
            created_at=NOW,
            choices=("allow", "deny"),
        ),
    )
    synchronizer.observe(entry, second)
    second_gate = next(
        gate
        for gate in store.current("L1").values()
        if gate.packet["adapterInteraction"]["interactionId"] == "approval-2"
    )
    synchronizer.observe(entry, replace(second, pending_interaction=None))
    assert store.current("L1")[first_gate.id].state == "applied"
    assert store.current("L1")[second_gate.id].state == "expired"


def test_transcript_completion_updates_but_never_consumes_inbox(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    inbox = OperatorInboxStore(tmp_path)
    row = create_operator_inbox_entry(
        entry_id="inbox-1",
        now=NOW,
        lifecycle_id="L1",
        agent_id="worker-1",
        ask="Continue",
        response="Please continue",
        created_by="manager-1",
        created_via="cli",
    ).model_copy(
        update={
            "deliveryState": "delivered",
            "adapterDeliveryState": "accepted",
            "adapterRequestId": "inbox-1",
        }
    )
    inbox.append(row)
    transcript = (
        {
            "sequence": 1,
            "role": "result",
            "text": "done",
            "createdAt": NOW,
            "requestId": "inbox-1",
            "vendorCorrelationId": "vendor-1",
            "terminalResult": {"outcome": "completed", "completedAt": NOW},
        },
    )
    with mock.patch(
        "agents_remember.serving.hosted_interactions.read_control_transcript",
        return_value=transcript,
    ):
        HostedInteractionSynchronizer(tmp_path).observe(
            entry,
            AdapterSnapshot(
                identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
                control="ready",
                activity="idle",
                acceptance="immediate",
            ),
        )
    completed = inbox.current()["inbox-1"]
    assert completed.adapterDeliveryState == "completed"
    assert completed.adapterCompletedAt == NOW
    assert completed.state == "pending"
