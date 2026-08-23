"""Post-claim recovery forcing for journal-owned integration publication."""

from __future__ import annotations

import hashlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import test_organizational_completion_integration as fixture_mod
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.integration import integration_claim_transfer as claim_transfer_mod
from agents_remember.worktrees.integration import integration_ref_transaction
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules import integration_publication as publication_mod
from agents_remember.worktrees.queue import closeout_queue as queue_mod
from agents_remember.worktrees.queue.closeout_queue_state import initial_queue_state
from agents_remember.worktrees.worktree_contract import load_contract
from test_closeout_queue import NOW, SPRINT
from test_worktree_support import git


class IntegrationPublicationRecoveryL2Tests(unittest.TestCase):
    """The queue ceases to be authority after the exact journal claim transfer."""

    def setUp(self) -> None:
        self.owner = fixture_mod.OrganizationalCompletionIntegrationTests(
            "test_nonfinal_leaf_reuses_targeted_closeout_without_full_gate"
        )
        self.owner.setUp()
        self.fixture = self.owner.fixture

    def tearDown(self) -> None:
        try:
            self.owner.doCleanups()
        finally:
            self.owner.tearDown()

    def _delete_queue(self) -> None:
        store = CloseoutQueueStore(self.fixture.coord, SPRINT)
        store.state_path.unlink(missing_ok=True)
        store.pending_path.unlink(missing_ok=True)

    def test_queue_deleted_after_claim_before_refs_recovers_same_generation(self) -> None:
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        real_transfer = claim_transfer_mod.transfer_integration_claim

        def transfer_then_delete(*args, **kwargs):
            proven = real_transfer(*args, **kwargs)
            self._delete_queue()
            return proven

        with (
            mock.patch.object(
                fixture_mod.quality_mod,
                "run_strict_code_quality_gate",
                return_value=fixture_mod._full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                claim_transfer_mod,
                "transfer_integration_claim",
                side_effect=transfer_then_delete,
            ),
            mock.patch.object(
                integrate_mod,
                "prepare_integration_ref_move",
                side_effect=SystemExit("cut after claim before protected refs"),
            ),
            self.assertRaisesRegex(SystemExit, "after claim"),
        ):
            integrate_mod.integrate_result(self.owner._args(contract, runtime, record), contract)

        accepted = store.read()
        assert accepted is not None and accepted.integrationPublication is not None
        self.assertEqual(accepted.integrationPublication.claimState, "proven")
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            contract.code_base_commit,
        )
        assert contract.memory_repo_path is not None
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            contract.memory_base_commit,
        )
        current = load_contract(contract.contract_path)
        recovered = integrate_mod.integrate_result(
            self.owner._args(current, runtime, accepted), current
        )
        self.assertEqual(recovered.returncode, 0)
        self.assertEqual(recovered.payload["state"], "integrated")
        full_gate.assert_called_once()

    def test_surviving_queue_receipt_contains_projection_identity_only(self) -> None:
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        with (
            mock.patch.object(
                fixture_mod.quality_mod,
                "run_strict_code_quality_gate",
                return_value=fixture_mod._full_gate(contract),
            ),
            mock.patch.object(
                integrate_mod,
                "prepare_integration_ref_move",
                side_effect=SystemExit("cut after projection consumption"),
            ),
            self.assertRaisesRegex(SystemExit, "projection consumption"),
        ):
            integrate_mod.integrate_result(self.owner._args(contract, runtime, record), contract)

        accepted = store.read()
        assert accepted is not None and accepted.integrationPublication is not None
        intent = accepted.integrationPublication
        self.assertEqual(intent.claimState, "proven")
        queue = CloseoutQueueStore(self.fixture.coord, SPRINT)
        graph = queue_mod._graph_context(TaskDocumentTopology(self.fixture.coord), SPRINT)
        state = queue.read(initial_queue_state(SPRINT, graph.revision, NOW))
        receipts = [
            item
            for item in state.appliedRequests
            if item.requestId.startswith("integration-projection-transfer:")
        ]
        self.assertEqual(len(receipts), 1)
        payload = {
            "action": "complete-integration",
            "sprint": intent.queueSprintTaskDocument,
            "candidate": intent.queueCandidateTaskDocument,
            "closeoutDoorGenerationId": intent.closeoutDoorGenerationId,
        }
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        receipt = receipts[0]
        self.assertEqual(receipt.requestId, f"integration-projection-transfer:{expected}")
        self.assertEqual(receipt.fingerprint, expected)
        public_receipt = receipt.model_dump_json()
        private_values = {
            accepted.operationKey,
            intent.operationKey,
            intent.queueCandidateSha256,
            contract.code_commit,
            contract.memory_content_commit,
            contract.ledger_commit,
        }
        for private in private_values:
            self.assertIsInstance(private, str)
            assert isinstance(private, str)
            self.assertNotIn(private, public_receipt)

    def test_journal_claim_intent_survives_queue_invalidation_and_governed_task_edit(
        self,
    ) -> None:
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        real_transfer = claim_transfer_mod.transfer_integration_claim

        def edit_governed_task() -> dict[str, object]:
            return task_doc_tool(
                self.fixture.cfg,
                TaskDocTarget(repo_id="repo-a", task_name="master-a"),
                operation="append_decision",
                edit=TaskDocEdit(
                    decision={
                        "at": NOW,
                        "decision": "task authoring won after journal claim intent",
                        "rationale": "Queue invalidation is not lifecycle authority.",
                    }
                ),
            )

        def invalidate_before_transfer(*args, **kwargs):
            current = store.read()
            assert current is not None and current.integrationPublication is not None
            self.assertEqual(current.integrationPublication.claimState, "intent")
            self._delete_queue()
            with ThreadPoolExecutor(max_workers=1) as pool:
                edited = pool.submit(edit_governed_task).result(timeout=10)
            self.assertEqual(edited["state"], "updated")
            return real_transfer(*args, **kwargs)

        with (
            mock.patch.object(
                fixture_mod.quality_mod,
                "run_strict_code_quality_gate",
                return_value=fixture_mod._full_gate(contract),
            ),
            mock.patch.object(
                claim_transfer_mod,
                "transfer_integration_claim",
                side_effect=invalidate_before_transfer,
            ),
            mock.patch.object(
                CloseoutQueueStore,
                "transact",
                side_effect=AssertionError(
                    "a missing disposable queue projection must not require claim receipt"
                ),
            ),
            mock.patch.object(
                integrate_mod,
                "prepare_integration_ref_move",
                side_effect=SystemExit("cut after journal-owned claim transfer"),
            ),
            self.assertRaisesRegex(SystemExit, "journal-owned claim"),
        ):
            integrate_mod.integrate_result(self.owner._args(contract, runtime, record), contract)

        accepted = store.read()
        assert accepted is not None and accepted.integrationPublication is not None
        self.assertEqual(accepted.integrationPublication.claimState, "proven")
        queue = CloseoutQueueStore(self.fixture.coord, SPRINT)
        graph = queue_mod._graph_context(
            TaskDocumentTopology(self.fixture.coord),
            SPRINT,
        )
        state = queue.read(initial_queue_state(SPRINT, graph.revision, NOW))
        self.assertFalse(
            any(
                receipt.requestId.startswith("integration-projection-transfer:")
                for receipt in state.appliedRequests
            )
        )
        master = read_task_doc(self.fixture.tasks / "master-a" / "task.json")
        self.assertTrue(
            any(
                decision.decision == "task authoring won after journal claim intent"
                for decision in master.decisions
            )
        )

    def test_queue_deleted_between_code_and_memory_refs_retains_torn_pair_and_recovers(
        self,
    ) -> None:
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        original_cas = integration_ref_transaction._compare_and_swap_ref
        calls = 0

        def cut_second_cas(repo, branch, expected, target, *, authority=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                self._delete_queue()
                raise SystemExit("cut after code ref before memory ref")
            return original_cas(repo, branch, expected, target, authority=authority)

        with (
            mock.patch.object(
                fixture_mod.quality_mod,
                "run_strict_code_quality_gate",
                return_value=fixture_mod._full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                integration_ref_transaction,
                "_compare_and_swap_ref",
                side_effect=cut_second_cas,
            ),
            self.assertRaisesRegex(SystemExit, "before memory ref"),
        ):
            integrate_mod.integrate_result(self.owner._args(contract, runtime, record), contract)

        accepted = store.read()
        assert accepted is not None and accepted.integrationPublication is not None
        self.assertEqual(accepted.integrationPublication.claimState, "proven")
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            contract.code_commit,
        )
        assert contract.memory_repo_path is not None
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            contract.memory_base_commit,
        )
        current = load_contract(contract.contract_path)
        recovered = integrate_mod.integrate_result(
            self.owner._args(current, runtime, accepted), current
        )
        self.assertEqual(recovered.returncode, 0)
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            contract.ledger_commit,
        )
        full_gate.assert_called_once()

    def test_queue_deleted_after_contract_before_task_publication_recovers(self) -> None:
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        master_path = self.fixture.tasks / "master-a" / "task.json"
        master_before = master_path.read_bytes()

        def cut_before_task_publication(_intent) -> None:
            self._delete_queue()
            raise SystemExit("cut after contract before organizational task publication")

        with (
            mock.patch.object(
                fixture_mod.quality_mod,
                "run_strict_code_quality_gate",
                return_value=fixture_mod._full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                publication_mod,
                "publish_organizational_master_completion",
                side_effect=cut_before_task_publication,
            ),
            self.assertRaisesRegex(SystemExit, "before organizational"),
        ):
            integrate_mod.integrate_result(self.owner._args(contract, runtime, record), contract)

        accepted = store.read()
        assert accepted is not None and accepted.integrationPublication is not None
        self.assertEqual(load_contract(contract.contract_path).integration_status, "completed")
        self.assertEqual(master_path.read_bytes(), master_before)
        current = load_contract(contract.contract_path)
        recovered = integrate_mod.integrate_result(
            self.owner._args(current, runtime, accepted), current
        )
        self.assertEqual(recovered.returncode, 0)
        self.assertEqual(read_task_doc(master_path).status, "Completed")
        full_gate.assert_called_once()
