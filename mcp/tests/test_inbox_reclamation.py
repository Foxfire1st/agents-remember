"""Regressions for confirmed-gone supervisor inbox reclamation (260712-TRH-L5)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.controlplane.supervisor_signals import SupervisorSignalCooldownStore
from agents_remember.observer.store import EventStore
from agents_remember.serving.inbox_reclamation import (
    CONFIRMED_GONE_REASON,
    TmuxSessionNameSnapshot,
    plan_confirmed_gone_reclamation,
    snapshot_tmux_session_names,
)
from agents_remember.serving.supervisor import run_supervisor_sweep
from agents_remember.serving.supervisor_heartbeat import SupervisorHeartbeatStore
from agents_remember.serving.supervisor_models import SupervisorContext
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _catalog_entry(subject: str, status: str) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=subject,
        label=subject,
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{subject}",
        command=("codex",),
        created_at=NOW.isoformat(),
        last_attached_at=NOW.isoformat(),
        status=status,  # type: ignore[arg-type]
        terminated_at=NOW.isoformat() if status == "terminated" else None,
    )


def _inbox_entry(
    entry_id: str,
    *,
    subject: str | None = "subject-1",
    kind: str = "nudge",
    created_by: str = "supervisor",
    created_at: datetime = NOW,
) -> OperatorInboxEntry:
    return create_operator_inbox_entry(
        entry_id=entry_id,
        now=created_at.isoformat(),
        lifecycle_id=None,
        agent_id="manager-1",
        ask=f"ask-{entry_id}",
        response=f"body-{entry_id}",
        created_by=created_by,
        created_via="cli",
        message_kind=kind,  # type: ignore[arg-type]
        subject_agent_id=subject,
    )


class ConfirmedGonePolicyTests(unittest.TestCase):
    def test_tmux_snapshot_classifies_list_no_server_and_command_failure(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ar-seat-1\nother\n", stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            listed = snapshot_tmux_session_names()
        self.assertEqual(listed.names, frozenset({"ar-seat-1", "other"}))
        self.assertEqual(listed.evidence, "tmux-session-list")

        no_server = mock.Mock(returncode=1, stdout="", stderr="no server running on /tmp/tmux")
        with mock.patch("subprocess.run", return_value=no_server):
            absent = snapshot_tmux_session_names()
        self.assertTrue(absent.ok)
        self.assertEqual(absent.names, frozenset())
        self.assertEqual(absent.evidence, "tmux-no-server")

        with mock.patch("subprocess.run", side_effect=OSError("tmux unavailable")):
            failed = snapshot_tmux_session_names()
        self.assertFalse(failed.ok)
        self.assertEqual(failed.evidence, "tmux-command-failed")

    def test_only_terminated_catalog_status_is_direct_gone_proof(self) -> None:
        current = {
            status: _inbox_entry(status, subject=f"subject-{status}")
            for status in ("terminated", "running", "landed", "exited")
        }
        plan = plan_confirmed_gone_reclamation(
            current,
            catalog_entries=[
                _catalog_entry(f"subject-{status}", status)
                for status in ("terminated", "running", "landed", "exited")
            ],
            snapshotter=lambda: self.fail("catalog-present subjects must not probe tmux"),
        )
        self.assertEqual(plan.resolve_reasons, {"terminated": CONFIRMED_GONE_REASON})
        self.assertEqual(plan.kept_row_count, 3)
        self.assertEqual(plan.evidence_class, "catalog-terminal")

    def test_compacted_tombstone_uses_one_deduplicated_tmux_snapshot(self) -> None:
        calls = 0

        def snapshot() -> TmuxSessionNameSnapshot:
            nonlocal calls
            calls += 1
            return TmuxSessionNameSnapshot(frozenset(), "tmux-session-list")

        current = {
            "n1": _inbox_entry("n1", subject="old-seat"),
            "n2": _inbox_entry("n2", subject="old-seat", kind="escalation"),
        }
        plan = plan_confirmed_gone_reclamation(
            current,
            catalog_entries=[],
            snapshotter=snapshot,
        )
        self.assertEqual(calls, 1)
        self.assertEqual(set(plan.resolve_reasons), {"n1", "n2"})
        self.assertEqual(plan.unique_subject_count, 1)
        self.assertEqual(plan.evidence_class, "tmux-name-absence")

    def test_tmux_presence_and_indeterminate_snapshot_both_keep_rows(self) -> None:
        current = {"n1": _inbox_entry("n1", subject="seat-1")}
        for snapshot in (
            TmuxSessionNameSnapshot(frozenset({"ar-seat-1"}), "tmux-session-list"),
            TmuxSessionNameSnapshot(frozenset(), "tmux-command-failed"),
        ):
            with self.subTest(evidence=snapshot.evidence):
                plan = plan_confirmed_gone_reclamation(
                    current,
                    catalog_entries=[],
                    snapshotter=lambda snapshot=snapshot: snapshot,
                )
                self.assertEqual(plan.resolve_reasons, {})
                self.assertEqual(plan.kept_row_count, 1)

    def test_protected_kinds_subjectless_and_model_authored_rows_are_excluded(self) -> None:
        protected = (
            "message",
            "gate-response",
            "turn-report",
            "master-handover",
            "degradation-alert",
            "decision-item",
            "decision-ruling",
            "dispatch-brief",
        )
        rows = {f"kind-{kind}": _inbox_entry(f"kind-{kind}", kind=kind) for kind in protected}
        rows["subjectless"] = _inbox_entry("subjectless", subject=None)
        rows["model"] = _inbox_entry("model", created_by="model")
        plan = plan_confirmed_gone_reclamation(
            rows,
            catalog_entries=[],
            snapshotter=lambda: self.fail("no protected row may request tmux evidence"),
        )
        self.assertEqual(plan.candidate_row_count, 0)
        self.assertEqual(plan.resolve_reasons, {})


class ReconcileAndCompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OperatorInboxStore(Path(self.tmp.name))

    def test_terminal_fold_dominates_a_physically_later_stale_pending_snapshot(self) -> None:
        pending = _inbox_entry("n1")
        self.store.append(pending)
        self.store.mark_ladder_resolved(
            "n1",
            now=(NOW + timedelta(seconds=1)).isoformat(),
            reason=CONFIRMED_GONE_REASON,
        )
        self.store.append(
            pending.model_copy(update={"ts": (NOW + timedelta(seconds=2)).isoformat()})
        )

        _removed, current, resolved = self.store.reconcile_and_compact(
            now=NOW + timedelta(seconds=3),
            reconcile=lambda _current: {},
        )
        self.assertEqual(current, {})
        self.assertEqual(resolved, ())
        self.assertEqual(self.store.read(), [])

    def test_consumed_snapshot_remains_authoritative_when_resolver_targets_same_id(self) -> None:
        self.store.append(_inbox_entry("n1"))
        self.store.consume(
            "n1",
            now=(NOW + timedelta(seconds=1)).isoformat(),
            consumed_by="model",
            consumed_via="cli",
        )
        _removed, current, resolved = self.store.reconcile_and_compact(
            now=NOW + timedelta(seconds=2),
            reconcile=lambda _current: {"n1": CONFIRMED_GONE_REASON},
        )
        self.assertEqual(resolved, ())
        self.assertEqual(current["n1"].state, "consumed")

    def test_existing_pending_ttl_fallback_is_unchanged(self) -> None:
        self.store.append(_inbox_entry("ancient", subject=None, created_at=NOW - timedelta(days=3)))
        self.store.append(_inbox_entry("fresh", subject=None, created_at=NOW - timedelta(hours=1)))
        _removed, current, _resolved = self.store.reconcile_and_compact(
            now=NOW,
            reconcile=lambda _current: {},
        )
        self.assertEqual(set(current), {"fresh"})

    def test_removed_count_is_persisted_folded_id_delta_not_transient_snapshot_count(self) -> None:
        self.store.append(_inbox_entry("n1"))
        persisted_before = set(self.store.current())

        removed, current, resolved = self.store.reconcile_and_compact(
            now=NOW,
            reconcile=lambda _current: {"n1": CONFIRMED_GONE_REASON},
        )

        persisted_after = set(self.store.current())
        self.assertEqual(len(resolved), 1)
        self.assertEqual(removed, 1)
        self.assertEqual(removed, len(persisted_before - persisted_after))
        self.assertEqual(current, {})
        self.assertEqual(self.store.read(), [])


class SupervisorReclamationIntegrationTests(unittest.TestCase):
    def test_sweep_compacts_before_redelivery_and_emits_one_body_free_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer_root = root / "observer"
            catalog = TerminalCatalog(root / "catalog.json")
            catalog.upsert(_catalog_entry("gone-seat", "terminated"))
            inbox_store = OperatorInboxStore(observer_root)
            inbox_store.append(_inbox_entry("n1", subject="gone-seat"))
            event_store = EventStore(observer_root)

            class _Host:
                def has_session(self, _tmux_name: str) -> bool:
                    return True

                def terminate(self, _sid: str, *, tmux_name: str | None = None) -> None:
                    pass

            class _Paster:
                def paste(self, *_args: object, **_kwargs: object) -> PasteResult:
                    raise AssertionError("reclaimed row must not be redelivered")

            ctx = SupervisorContext(
                catalog=catalog,
                host=cast(TerminalHost, _Host()),
                paster=cast(TerminalPaster, _Paster()),
                inbox_store=inbox_store,
                expectation_store=ExpectationRowStore(observer_root),
                nudge_store=OrchestrationNudgeStore(observer_root),
                signal_cooldown_store=SupervisorSignalCooldownStore(observer_root),
                event_store=event_store,
                heartbeat_store=SupervisorHeartbeatStore(observer_root),
                coordination_root=root,
                tmux_name_snapshotter=lambda: self.fail("terminal proof must not probe tmux"),
            )
            result = run_supervisor_sweep(ctx, now=NOW)

            self.assertEqual(inbox_store.read(), [])
            self.assertFalse(any(f.source_id == "n1" for f in result.findings))
            events = [
                event
                for event in event_store.read(None)
                if event.kind == "orchestration.supervisor.inbox-compacted"
            ]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].data["removed"], 1)
            self.assertEqual(events[0].data["resolvedRowCount"], 1)
            self.assertEqual(events[0].data["uniqueSubjectCount"], 1)
            self.assertEqual(events[0].data["keptCandidateCount"], 0)
            self.assertEqual(events[0].data["evidenceClass"], "catalog-terminal")
            self.assertNotIn("body-n1", str(events[0].data))

    def test_three_noop_sweeps_with_kept_candidate_emit_no_compaction_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer_root = root / "observer"
            catalog = TerminalCatalog(root / "catalog.json")
            catalog.upsert(_catalog_entry("live-seat", "running"))
            inbox_store = OperatorInboxStore(observer_root)
            inbox_store.append(
                _inbox_entry("n1", subject="live-seat").model_copy(
                    update={"nextAttemptAt": (NOW + timedelta(hours=1)).isoformat()}
                )
            )
            event_store = EventStore(observer_root)

            class _Host:
                def has_session(self, _tmux_name: str) -> bool:
                    return True

                def terminate(self, _sid: str, *, tmux_name: str | None = None) -> None:
                    pass

            class _Paster:
                def paste(self, *_args: object, **_kwargs: object) -> PasteResult:
                    raise AssertionError("future-backoff row must not be redelivered")

            ctx = SupervisorContext(
                catalog=catalog,
                host=cast(TerminalHost, _Host()),
                paster=cast(TerminalPaster, _Paster()),
                inbox_store=inbox_store,
                expectation_store=ExpectationRowStore(observer_root),
                nudge_store=OrchestrationNudgeStore(observer_root),
                signal_cooldown_store=SupervisorSignalCooldownStore(observer_root),
                event_store=event_store,
                heartbeat_store=SupervisorHeartbeatStore(observer_root),
                coordination_root=root,
                tmux_name_snapshotter=lambda: self.fail("catalog-present subject must not probe"),
            )

            for offset in (0, 10, 20):
                run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=offset))

            self.assertEqual(set(inbox_store.current()), {"n1"})
            events = [
                event
                for event in event_store.read(None)
                if event.kind == "orchestration.supervisor.inbox-compacted"
            ]
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
