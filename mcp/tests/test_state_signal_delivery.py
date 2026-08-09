"""Availability-gate and landed-terminality unit tests for the state-signal delivery path."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    OperatorInboxEntry,
    create_operator_inbox_entry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import RedeliveryFloor
from agents_remember.serving.harness_control_models import SubmissionReceipt
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    DeliveryAdmission,
    InboxDeliveryLog,
    deliver_inbox_entry,
)
from agents_remember.serving.terminal import TerminalHost, TerminalHostSeams
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster

T1 = "2026-07-13T15:41:00+00:00"
T2 = "2026-07-13T15:42:00+00:00"


def _manager(**overrides: object) -> TerminalCatalogEntry:
    fields: dict[str, object] = dict(
        id="manager-1",
        label="Chat manager-1",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name="ar-manager-1",
        command=("codex",),
        created_at="2026-07-13T00:00:00+00:00",
        last_attached_at="2026-07-13T00:00:00+00:00",
        status="running",
        control_state="ready",
        control_endpoint=Path("/tmp/manager.sock"),
        turn_state="turn-ended",
        turn_state_changed_at=T1,
    )
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)  # type: ignore[arg-type]


def _signal_entry(*, message_kind: str = "state-signal") -> OperatorInboxEntry:
    return create_operator_inbox_entry(
        InboxMessage(
            ask="Agent notifier observed state-signal: completed",
            response="worker done",
            message_kind=message_kind,  # type: ignore[arg-type]
        ),
        entry_id="signal-1",
        now=T1,
        routing=InboxRouting(
            address=InboxAddress(lifecycle_id=None, agent_id="manager-1", recipient_role="manager")
        ),
        poster=InboxPoster(created_by="agent-notifier", created_via="cli", sender_role="system"),
    )


class _Paster:
    def paste(
        self,
        _tmux_name: str,
        _text: str,
        *,
        submit: bool = False,
        **_kwargs: object,
    ) -> PasteResult:
        return PasteResult(delivered=True, submitted=submit)


class StateSignalDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.catalog = TerminalCatalog(root / "terminal-sessions.json")
        self.store = OperatorInboxStore(root / "observer")
        self.host = TerminalHost(TerminalHostSeams(tmux_probe=lambda _name: True))
        self.catalog.upsert(_manager())
        self.store.append(_signal_entry())

    def _deliver(
        self,
        entry: OperatorInboxEntry,
        *,
        admission: DeliveryAdmission | None = None,
        now: str = T2,
    ) -> OperatorInboxEntry:
        return deliver_inbox_entry(
            InboxDeliveryLog(
                store=self.store,
                entry=entry,
                at=now,
                floor=RedeliveryFloor(current=self.store.current()),
            ),
            sessions=HostedSessionRuntime(catalog=self.catalog, host=self.host),
            paster=cast(TerminalPaster, _Paster()),  # type: ignore[arg-type]
            admission=admission or DeliveryAdmission(),
        )

    def test_boundary_gate_holds_when_target_is_working(self) -> None:
        self.catalog.upsert(
            _manager(
                turn_state="working",
                turn_state_changed_at=T1,
            )
        )
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
        ) as submit:
            recorded = self._deliver(
                self.store.current()["signal-1"],
                admission=DeliveryAdmission(boundary=True),
            )
        submit.assert_not_called()
        self.assertEqual(recorded.deliveryState, "queued")
        self.assertEqual(recorded.adapterDeliveryState, "queued")
        self.assertIsNotNone(recorded.nextAttemptAt)
        self.assertFalse(state_signal_landed(recorded))

    def test_state_signal_landed_is_unreachable_via_a_non_boundary_gated_push(self) -> None:
        self.catalog.upsert(
            _manager(
                turn_state="working",
                turn_state_changed_at=T1,
            )
        )
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
        ) as submit:
            recorded = self._deliver(
                self.store.current()["signal-1"],
                admission=DeliveryAdmission(),
            )
        submit.assert_not_called()
        self.assertEqual(recorded.deliveryState, "queued")
        self.assertEqual(recorded.adapterDeliveryState, "queued")
        self.assertFalse(state_signal_landed(recorded))

    def test_boundary_gate_allows_at_turn_ended_and_acceptance_is_terminal(self) -> None:
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            return_value=SubmissionReceipt(
                request_id="signal-1",
                acceptance="immediate",
                submitted_at=T2,
                accepted_at=T2,
            ),
        ) as submit:
            recorded = self._deliver(
                self.store.current()["signal-1"],
                admission=DeliveryAdmission(boundary=True),
            )
        submit.assert_called_once()
        self.assertEqual(recorded.deliveryState, "delivered")
        self.assertEqual(recorded.adapterDeliveryState, "accepted")
        self.assertTrue(state_signal_landed(recorded))
        self.assertIsNone(recorded.nextAttemptAt)
        self.assertEqual(
            self.store.list_redeliverable(now=datetime.fromisoformat(T2), rate_limit_seconds=900.0),
            [],
        )

    def test_busy_adapter_queued_acceptance_is_not_terminal(self) -> None:
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            return_value=SubmissionReceipt(
                request_id="signal-1",
                acceptance="queued",
                submitted_at=T2,
                accepted_at=T2,
            ),
        ) as submit:
            recorded = self._deliver(
                self.store.current()["signal-1"],
                admission=DeliveryAdmission(boundary=True),
            )
        submit.assert_called_once()
        self.assertEqual(recorded.adapterDeliveryState, "queued")
        self.assertFalse(state_signal_landed(recorded))
        self.assertIsNotNone(recorded.nextAttemptAt)

    def test_landed_state_signal_is_not_redeliverable_or_ladder_eligible(self) -> None:
        self.catalog.upsert(
            _manager(
                turn_state="turn-ended",
                turn_state_changed_at=T1,
            )
        )
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            return_value=SubmissionReceipt(
                request_id="signal-1",
                acceptance="immediate",
                submitted_at=T2,
                accepted_at=T2,
            ),
        ):
            self._deliver(
                self.store.current()["signal-1"],
                admission=DeliveryAdmission(boundary=True),
            )
        landed = self.store.current()["signal-1"]
        self.assertTrue(state_signal_landed(landed))
        self.assertEqual(
            self.store.list_redeliverable(now=datetime.fromisoformat(T2), rate_limit_seconds=900.0),
            [],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
