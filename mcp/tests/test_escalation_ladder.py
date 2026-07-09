"""Tests for the escalation ladder walker (260707-HFX2-L4, R2/R3)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.escalation_ladder import (
    MAX_RUNG,
    next_step,
    rung_due,
    seat_is_suspect,
)
from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.orphan_policy import find_orphaned_workers
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
T0 = "2026-07-08T11:00:00+00:00"


def _entry(**overrides: object):
    base: dict[str, object] = dict(
        entry_id="e1",
        now=T0,
        lifecycle_id=None,
        agent_id="worker-1",
        ask="ask",
        response="resp",
        created_by="system",
        created_via="cli",
        message_kind="escalation",
        recipient_role="worker",
    )
    base.update(overrides)
    return create_operator_inbox_entry(**base)  # type: ignore[arg-type]


def _catalog_entry(session_id: str, **overrides: object) -> TerminalCatalogEntry:
    base: dict[str, object] = {
        "id": session_id,
        "label": session_id,
        "kind": "harness",
        "harness": "codex",
        "lifecycle_id": None,
        "cwd": Path("/workspace"),
        "tmux_name": f"ar-{session_id}",
        "command": ("codex",),
        "created_at": T0,
        "last_attached_at": T0,
        "status": "running",
    }
    base.update(overrides)
    return TerminalCatalogEntry(**base)  # type: ignore[arg-type]


class RungDueTests(unittest.TestCase):
    def test_rung_zero_uses_sla_seconds_anchored_at_creation(self) -> None:
        entry = _entry()
        self.assertFalse(rung_due(entry, now=NOW, sla_seconds=7200.0, rung_seconds=60.0))
        self.assertTrue(rung_due(entry, now=NOW, sla_seconds=30.0, rung_seconds=60.0))

    def test_later_rung_anchors_at_escalated_at_not_creation(self) -> None:
        entry = _entry().model_copy(
            update={"rung": 1, "escalatedAt": (NOW - timedelta(minutes=20)).isoformat()}
        )
        self.assertFalse(rung_due(entry, now=NOW, sla_seconds=30.0, rung_seconds=3600.0))
        self.assertTrue(rung_due(entry, now=NOW, sla_seconds=30.0, rung_seconds=600.0))

    def test_consumed_row_never_advances(self) -> None:
        entry = _entry().model_copy(update={"state": "consumed"})
        self.assertFalse(rung_due(entry, now=NOW, sla_seconds=0.001, rung_seconds=0.001))

    def test_max_rung_never_advances_further(self) -> None:
        entry = _entry().model_copy(
            update={"rung": MAX_RUNG, "escalatedAt": (NOW - timedelta(days=1)).isoformat()}
        )
        self.assertFalse(rung_due(entry, now=NOW, sla_seconds=1.0, rung_seconds=1.0))


class NextStepTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = TerminalCatalog(Path(tmp.name) / "catalog.json")

    def test_rung_one_renudges_the_original_addressee(self) -> None:
        entry = _entry(agent_id="worker-1", recipient_role="worker")
        step = next_step(self.catalog, entry)
        self.assertEqual(step.rung, 1)
        self.assertEqual(step.action, "renudge")
        self.assertEqual(step.owner.agent_id, "worker-1")
        self.assertEqual(step.owner.role, "worker")

    def test_rung_two_skip_levels_to_owners_owner(self) -> None:
        self.catalog.upsert(_catalog_entry("orchestrator-1", spawn_role="orchestrator"))
        self.catalog.upsert(
            _catalog_entry(
                "manager-1", spawn_role="manager", spawned_by_session="orchestrator-1"
            )
        )
        self.catalog.upsert(
            _catalog_entry("worker-1", spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = _entry(agent_id="worker-1").model_copy(update={"rung": 1})
        step = next_step(self.catalog, entry)
        self.assertEqual(step.rung, 2)
        self.assertEqual(step.action, "skip-level")
        self.assertEqual(step.owner.agent_id, "orchestrator-1")

    def test_rung_two_with_no_further_owner_jumps_straight_to_architect(self) -> None:
        # A manager-addressed row: the manager's own owner is the orchestrator, which has no
        # further owner recorded -- the "owner's owner" hits the hierarchy ceiling, so the walker
        # jumps straight to rung 3 rather than stalling at an unaddressable rung 2.
        self.catalog.upsert(_catalog_entry("orchestrator-1", spawn_role="orchestrator"))
        self.catalog.upsert(
            _catalog_entry("manager-1", spawn_role="manager", spawned_by_session="orchestrator-1")
        )
        entry = _entry(agent_id="manager-1", recipient_role="manager").model_copy(update={"rung": 1})
        step = next_step(self.catalog, entry)
        self.assertEqual(step.rung, MAX_RUNG)
        self.assertEqual(step.action, "architect-attention")
        self.assertEqual(step.owner.role, "architect")

    def test_rung_three_lands_on_the_live_architect_seat(self) -> None:
        # Ruled terminal (developer, 2026-07-09): the ladder ends at the architect seat -- the
        # session the human actually talks to -- so custody can be mechanically acked.
        self.catalog.upsert(_catalog_entry("architect-1", spawn_role="architect"))
        entry = _entry(agent_id="worker-1").model_copy(update={"rung": 2})
        step = next_step(self.catalog, entry)
        self.assertEqual(step.rung, 3)
        self.assertEqual(step.action, "architect-attention")
        self.assertEqual(step.owner.role, "architect")
        self.assertEqual(step.owner.agent_id, "architect-1")

    def test_rung_three_without_an_architect_seat_stays_role_addressed(self) -> None:
        # Graceful degradation: no architect session attached -> the row is role-addressed and
        # waits, level-triggered, for the next architect session instead of targeting a phantom.
        entry = _entry(agent_id="worker-1").model_copy(update={"rung": 2})
        step = next_step(self.catalog, entry)
        self.assertEqual(step.rung, 3)
        self.assertEqual(step.action, "architect-attention")
        self.assertEqual(step.owner.role, "architect")
        self.assertIsNone(step.owner.agent_id)

    def test_rung_never_exceeds_max_rung(self) -> None:
        entry = _entry(agent_id="worker-1").model_copy(update={"rung": MAX_RUNG})
        step = next_step(self.catalog, entry)
        self.assertEqual(step.rung, MAX_RUNG)
        self.assertEqual(step.action, "architect-attention")


class SeatSuspectTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = TerminalCatalog(Path(tmp.name) / "catalog.json")

    def test_none_agent_id_is_never_suspect(self) -> None:
        self.assertFalse(seat_is_suspect(self.catalog, None, now=NOW, stale_seconds=60.0))

    def test_dead_seat_is_suspect(self) -> None:
        self.assertTrue(seat_is_suspect(self.catalog, "ghost", now=NOW, stale_seconds=60.0))

    def test_stale_past_cutoff_is_suspect(self) -> None:
        self.catalog.upsert(
            _catalog_entry(
                "worker-1",
                turn_state="stale",
                turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
            )
        )
        self.assertTrue(seat_is_suspect(self.catalog, "worker-1", now=NOW, stale_seconds=60.0))

    def test_recently_stale_is_not_yet_suspect(self) -> None:
        self.catalog.upsert(
            _catalog_entry(
                "worker-1", turn_state="stale", turn_state_changed_at=NOW.isoformat()
            )
        )
        self.assertFalse(seat_is_suspect(self.catalog, "worker-1", now=NOW, stale_seconds=60.0))

    def test_live_non_stale_seat_is_not_suspect(self) -> None:
        self.catalog.upsert(_catalog_entry("worker-1"))
        self.assertFalse(seat_is_suspect(self.catalog, "worker-1", now=NOW, stale_seconds=60.0))

    def test_architect_seat_is_never_suspect_even_when_stale(self) -> None:
        # Ruled (developer, 2026-07-09): the architect seat is the human's own session -- its
        # silence means the human is away, and retiring/respawning it cannot produce the human.
        self.catalog.upsert(
            _catalog_entry(
                "architect-1",
                spawn_role="architect",
                turn_state="stale",
                turn_state_changed_at=(NOW - timedelta(hours=6)).isoformat(),
            )
        )
        self.assertFalse(
            seat_is_suspect(self.catalog, "architect-1", now=NOW, stale_seconds=60.0)
        )


class OrphanPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = TerminalCatalog(Path(tmp.name) / "catalog.json")

    def test_finds_running_workers_of_the_named_manager(self) -> None:
        self.catalog.upsert(_catalog_entry("manager-1", spawn_role="manager"))
        self.catalog.upsert(
            _catalog_entry("worker-1", spawn_role="worker", spawned_by_session="manager-1")
        )
        self.catalog.upsert(
            _catalog_entry(
                "worker-2",
                spawn_role="worker",
                spawned_by_session="manager-1",
                status="terminated",
            )
        )
        self.catalog.upsert(
            _catalog_entry("worker-3", spawn_role="worker", spawned_by_session="other-manager")
        )
        orphans = find_orphaned_workers(self.catalog, manager_agent_id="manager-1")
        self.assertEqual([entry.id for entry in orphans], ["worker-1"])


if __name__ == "__main__":
    unittest.main()
