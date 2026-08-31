"""Commit-point-aware recovery for the canonical spawn-and-brief transaction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.application.structural import agent_tools, dispatch_transaction
from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
)
from agents_remember.application.terminal_tools import SpawnOverrides
from agents_remember.controlplane import operator_inbox_transitions
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.models.structural.agent import DispatchAgentRequest
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    DispatchBriefReceiptStore,
    TerminalCatalog,
    terminal_catalog_path,
)
from structural_seat_test_support import (
    FakeHost,
    detected_harness,
    structural_config,
    write_structural_settings,
    write_structural_topology,
)


class StructuralDispatchRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sprint, self.master, self.leaf = write_structural_topology(self.root)
        write_structural_settings(self.root)
        self.config = structural_config(self.root)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.root))
        self.host = FakeHost()
        self.runtime = StructuralAgentRuntime(
            host=cast(TerminalHost, self.host),
            spawn_overrides=SpawnOverrides(
                host=cast(TerminalHost, self.host),
                which=detected_harness,
            ),
            environ={},
        )

    def _dispatch(self) -> dict[str, object]:
        return dispatch_agent_tool(
            self.config,
            DispatchAgentRequest(
                task_document_ref=self.sprint,
                role="architect",
                brief="Design the sprint.",
            ),
            self.runtime,
        )

    def _architect(self) -> TerminalCatalogEntry:
        occupants = [
            row
            for row in self.catalog.list(include_terminated=True)
            if row.binding_task_document_ref == self.sprint
            and row.binding_role == "architect"
            and row.status == "running"
        ]
        self.assertEqual(len(occupants), 1)
        return occupants[0]

    def _briefs(self) -> list[OperatorInboxEntry]:
        return list(OperatorInboxStore(observer_root(self.config)).current().values())

    def test_pre_append_failure_retires_only_the_proven_unbriefed_generation(self) -> None:
        with mock.patch.object(
            OperatorInboxStore,
            "append",
            side_effect=OSError("append refused"),
        ):
            refused = self._dispatch()

        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "dispatch-persistence-refused")
        self.assertEqual(self._briefs(), [])
        rows = self.catalog.list(include_terminated=True)
        self.assertEqual([row.status for row in rows], ["terminated"])
        self.assertEqual(self.host.terminated, [rows[0].id])

    def test_post_append_compaction_failure_keeps_and_reuses_the_briefed_generation(self) -> None:
        with mock.patch.object(
            OperatorInboxStore,
            "compact",
            side_effect=OSError("compaction refused"),
        ):
            recovered = self._dispatch()

        original = self._architect()
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "dispatch-queued")
        self.assertEqual(len(self._briefs()), 1)
        self.assertEqual(original.dispatch_brief_entry_id, self._briefs()[0].id)
        self.assertEqual(self.host.terminated, [])

        repeated = self._dispatch()
        self.assertTrue(repeated["ok"])
        self.assertEqual(self._architect().id, original.id)
        self.assertEqual(len(self.host.ensured), 1)
        self.assertEqual(len(self._briefs()), 1)

    def test_receipt_bind_failure_is_unknown_not_rollback_and_retry_repairs_it(self) -> None:
        with mock.patch.object(
            DispatchBriefReceiptStore,
            "bind",
            side_effect=ValueError("receipt write refused"),
        ):
            refused = self._dispatch()

        original = self._architect()
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "dispatch-reconciliation-refused")
        self.assertIsNone(original.dispatch_brief_entry_id)
        self.assertEqual(len(self._briefs()), 1)
        self.assertEqual(self.host.terminated, [])

        repeated = self._dispatch()
        self.assertTrue(repeated["ok"])
        self.assertEqual(self._architect().id, original.id)
        self.assertIsNotNone(self._architect().dispatch_brief_entry_id)
        self.assertEqual(len(self.host.ensured), 1)
        self.assertEqual(len(self._briefs()), 1)

    def test_receipt_and_unbriefed_rollback_edges_are_explicit(self) -> None:
        context = mock.Mock(catalog=mock.Mock())
        target = mock.Mock(exact_dispatch_target="child")
        ordinary = mock.Mock(message_kind="message")
        brief = mock.Mock(message_kind="dispatch-brief")

        agent_tools._bind_dispatch_brief_receipt(context, target, ordinary, {"ok": True})
        agent_tools._bind_dispatch_brief_receipt(context, target, brief, {"ok": False})
        with self.assertRaisesRegex(ValueError, "did not return its entry id"):
            agent_tools._bind_dispatch_brief_receipt(context, target, brief, {"ok": True})

        receipts = mock.Mock()
        receipts.bind.return_value = None
        with (
            mock.patch.object(
                agent_tools,
                "DispatchBriefReceiptStore",
                return_value=receipts,
            ),
            self.assertRaisesRegex(ValueError, "dispatch target disappeared"),
        ):
            agent_tools._bind_dispatch_brief_receipt(
                context,
                target,
                brief,
                {"ok": True, "entryId": "brief-1"},
            )

        catalog = mock.Mock()
        catalog.get.side_effect = [None, mock.Mock(status="terminated")]
        with mock.patch.object(agent_tools, "TerminalCatalog", return_value=catalog):
            missing = agent_tools._retire_unbriefed_child(
                self.config,
                caller_id=None,
                child_id="missing",
                host=None,
            )
            terminated = agent_tools._retire_unbriefed_child(
                self.config,
                caller_id=None,
                child_id="terminated",
                host=None,
            )
        self.assertFalse(missing.retired)
        self.assertTrue(terminated.retired)

    def test_two_changed_generations_refuse_without_publishing_a_third(self) -> None:
        catalog = mock.Mock()
        catalog.get.return_value = None
        transaction = dispatch_transaction.DispatchTransaction(
            document=self.sprint,
            role="architect",
            catalog=catalog,
            inbox_store=mock.Mock(),
            admitted_spawn=lambda: {"status": "seat-taken", "ownerSession": "first"},
            retry_spawn=lambda: {"status": "seat-taken", "ownerSession": "second"},
            brief_spawned=mock.Mock(),
            retire_generation=mock.Mock(),
        )

        result = dispatch_transaction.execute_dispatch_transaction(transaction)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "dispatch-reconciliation-refused")
        self.assertIn("seat changed twice", result["detail"])
        self.assertEqual(catalog.get.call_count, 2)

    def test_failed_generation_that_cannot_retire_is_a_reconciliation_refusal(self) -> None:
        catalog = mock.Mock()
        catalog.get.return_value = mock.Mock(status="running")
        retire = mock.Mock(return_value=False)
        transaction = dispatch_transaction.DispatchTransaction(
            document=self.sprint,
            role="architect",
            catalog=catalog,
            inbox_store=mock.Mock(),
            admitted_spawn=mock.Mock(),
            retry_spawn=mock.Mock(),
            brief_spawned=mock.Mock(),
            retire_generation=retire,
        )
        with mock.patch.object(
            dispatch_transaction,
            "reconcile_dispatch_evidence",
            return_value=None,
        ):
            result = dispatch_transaction._reconcile_existing_dispatch(
                transaction,
                owner_id="failed",
            )

        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "dispatch-reconciliation-refused")
        self.assertIn("could not be retired", result["detail"])
        retire.assert_called_once_with("failed")

    def test_live_reviewer_from_another_parent_is_not_reused_or_retired(self) -> None:
        occupant = mock.Mock(
            status="running",
            structural_parent_task_document_ref=self.sprint,
            structural_parent_role="architect",
        )
        catalog = mock.Mock()
        catalog.get.return_value = occupant
        retire = mock.Mock()
        transaction = dispatch_transaction.DispatchTransaction(
            document=self.sprint,
            role="reviewer",
            catalog=catalog,
            inbox_store=mock.Mock(),
            admitted_spawn=mock.Mock(),
            retry_spawn=mock.Mock(),
            brief_spawned=mock.Mock(),
            retire_generation=retire,
            expected_structural_parent=(self.sprint, "orchestrator"),
        )

        with mock.patch.object(dispatch_transaction, "reconcile_dispatch_evidence") as reconcile:
            result = dispatch_transaction._reconcile_existing_dispatch(
                transaction,
                owner_id="plan-reviewer",
            )

        assert result is not None
        self.assertEqual(result["status"], "structural-parent-conflict")
        self.assertIn("retire it before dispatching", result["detail"])
        reconcile.assert_not_called()
        retire.assert_not_called()

    def test_unstamped_legacy_leaf_reviewer_keeps_its_only_possible_manager_parent(self) -> None:
        occupant = mock.Mock(
            structural_parent_task_document_ref=None,
            structural_parent_role=None,
        )
        transaction = dispatch_transaction.DispatchTransaction(
            document=self.leaf,
            role="reviewer",
            catalog=mock.Mock(),
            inbox_store=mock.Mock(),
            admitted_spawn=mock.Mock(),
            retry_spawn=mock.Mock(),
            brief_spawned=mock.Mock(),
            retire_generation=mock.Mock(),
            expected_structural_parent=(self.master, "manager"),
        )

        self.assertIsNone(dispatch_transaction._structural_parent_conflict(transaction, occupant))

    def test_receipt_repair_disappearance_and_legacy_occupancy_refuse(self) -> None:
        runtime = dispatch_transaction.DispatchEvidenceRuntime(
            document=self.sprint,
            role="architect",
            catalog=mock.Mock(),
            inbox_store=mock.Mock(),
        )
        occupant = mock.Mock(dispatch_brief_entry_id=None)
        brief = mock.Mock(id="brief-1")
        with (
            mock.patch.object(
                dispatch_transaction,
                "pinned_dispatch_brief",
                return_value=brief,
            ),
            mock.patch.object(
                DispatchBriefReceiptStore,
                "bind",
                return_value=None,
            ),
            self.assertRaisesRegex(
                dispatch_transaction.StructuralDispatchError,
                "disappeared while its pinned-brief receipt was repaired",
            ),
        ):
            dispatch_transaction._load_dispatch_evidence(
                runtime,
                occupant,
                "occupant",
            )

        legacy = mock.Mock(
            dispatch_brief_entry_id=None,
            spawned_by_kind=None,
        )
        result = dispatch_transaction._dispatch_evidence_outcome(
            runtime,
            legacy,
            None,
        )
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "seat-taken")

    def test_terminal_failed_brief_retires_and_replaces_that_generation(self) -> None:
        first = self._dispatch()
        self.assertTrue(first["ok"])
        original = self._architect()
        store = OperatorInboxStore(observer_root(self.config))
        brief = next(iter(store.current().values()))
        operator_inbox_transitions.mark_unresolved(
            store,
            brief.id,
            now="2026-08-25T01:00:00+00:00",
            reason="forced terminal failure",
        )

        recovered = self._dispatch()

        self.assertTrue(recovered["ok"])
        replacement = self._architect()
        self.assertNotEqual(replacement.id, original.id)
        retired = self.catalog.get(original.id)
        assert retired is not None
        self.assertEqual(retired.status, "terminated")
        self.assertEqual(self.host.terminated, [original.id])
        self.assertEqual(len(self.host.ensured), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
