"""Successor re-evaluation and address-bound brief evidence for canonical seats."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

from agents_remember.application.structural.dispatch_transaction import (
    DispatchEvidenceRuntime,
    reconcile_dispatch_evidence,
)
from agents_remember.application.terminal_tools import (
    SpawnedBy,
    SpawnOverrides,
    SpawnSeat,
    spawn_agent_session_tool,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from structural_seat_test_support import (
    FakeHost,
    detected_harness,
    structural_config,
    write_structural_settings,
    write_structural_topology,
)


class StructuralSeatSuccessionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sprint, self.master, _leaf = write_structural_topology(self.root)
        write_structural_settings(self.root)
        self.config = structural_config(self.root)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.root))
        self.host = FakeHost()
        self.overrides = SpawnOverrides(
            host=cast(TerminalHost, self.host),
            which=detected_harness,
        )

    def test_dead_incumbent_probe_reselects_live_heir_before_admitting_a_third(self) -> None:
        primary = spawn_agent_session_tool(
            self.config,
            seat=SpawnSeat(
                task_document_ref=self.sprint,
                level="portfolio",
                env={"AR_SPAWN_ROLE": "architect"},
            ),
            spawned_by=SpawnedBy(caller_kind="plane"),
            overrides=self.overrides,
        )
        staged = spawn_agent_session_tool(
            self.config,
            seat=SpawnSeat(
                replacement_for_task_document_ref=self.sprint,
                level="portfolio",
                env={"AR_SPAWN_ROLE": "architect"},
            ),
            spawned_by=SpawnedBy(caller_kind="plane"),
            overrides=self.overrides,
        )
        primary_entry = self.catalog.get(str(primary["session"]))
        assert primary_entry is not None
        self.host.known.remove(primary_entry.tmux_name)

        third = spawn_agent_session_tool(
            self.config,
            seat=SpawnSeat(
                task_document_ref=self.sprint,
                level="portfolio",
                env={"AR_SPAWN_ROLE": "architect"},
            ),
            spawned_by=SpawnedBy(caller_kind="plane"),
            overrides=self.overrides,
        )

        self.assertEqual(third["status"], "seat-taken")
        self.assertEqual(third["ownerSession"], staged["session"])
        self.assertEqual(self.catalog.get(str(primary["session"])).status, "exited")  # type: ignore[union-attr]
        self.assertEqual(self.catalog.get(str(staged["session"])).status, "running")  # type: ignore[union-attr]
        self.assertEqual(len(self.host.ensured), 2)

    def test_brief_receipt_survives_same_seat_promotion_but_not_cross_seat_move(self) -> None:
        staged = TerminalCatalogEntry(
            id="replacement",
            label="replacement",
            kind="harness",
            harness="claude",
            lifecycle_id=None,
            cwd=self.root,
            tmux_name="ar-replacement",
            command=("claude",),
            created_at="2026-08-25T00:00:00+00:00",
            last_attached_at="2026-08-25T00:00:00+00:00",
            status="running",
            replacement_for_task_document_ref=self.sprint,
            seat_role="architect",
            spawned_by_kind="plane",
            dispatch_brief_entry_id="brief-a",
        )

        promoted = staged.with_task_binding(self.sprint, "architect")
        moved = promoted.with_task_binding(self.master, "manager")

        self.assertEqual(promoted.dispatch_brief_entry_id, "brief-a")
        self.assertIsNone(promoted.replacement_for_task_document_ref)
        self.assertIsNone(moved.dispatch_brief_entry_id)
        self.catalog.upsert(moved)
        outcome = reconcile_dispatch_evidence(
            DispatchEvidenceRuntime(
                document=self.master,
                role="manager",
                catalog=self.catalog,
                inbox_store=OperatorInboxStore(observer_root(self.config)),
            ),
            owner_id=moved.id,
        )
        self.assertIsNone(outcome)

    def test_same_document_role_change_clears_address_bound_receipt(self) -> None:
        entry = replace(
            self._plain_entry(),
            task_document_ref=self.sprint,
            seat_role="architect",
            dispatch_brief_entry_id="brief-a",
        )

        moved = entry.with_task_binding(self.sprint, "strategist")

        self.assertIsNone(moved.dispatch_brief_entry_id)

    def _plain_entry(self) -> TerminalCatalogEntry:
        return TerminalCatalogEntry(
            id="seat",
            label="seat",
            kind="harness",
            harness="claude",
            lifecycle_id=None,
            cwd=self.root,
            tmux_name="ar-seat",
            command=("claude",),
            created_at="2026-08-25T00:00:00+00:00",
            last_attached_at="2026-08-25T00:00:00+00:00",
            status="running",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
