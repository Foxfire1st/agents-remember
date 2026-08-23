from __future__ import annotations

import inspect
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from agents_remember.application.closeout_queue import CloseoutQueueError
from agents_remember.application.lifecycle import lifecycle_operation_worker
from agents_remember.application.worktree_tools import (
    OperationControlRequest,
    worktree_operation_control_tool,
)
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore, queue_store_paths
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.queue.closeout_queue import CloseoutQueueState
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.integration import integration_operation_authority
from agents_remember.worktrees.integration.integration_publication_fence import (
    classify_integration_door_authority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    LifecycleControlProjectionContext,
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    start_or_observe_operation,
)
from agents_remember.worktrees.integration.organizational_completion_integration import (
    IntegrationBoundaryFacts,
    prepare_integration_publication_intent,
    preview_integration_boundary,
    transfer_integration_claim,
)
from agents_remember.worktrees.modules import closeout as closeout_mod
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.queue import closeout_queue as queue_module
from agents_remember.worktrees.queue.closeout_queue import QueueActor
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
)
from agents_remember.worktrees.route_review import code_candidate_tree
from agents_remember.worktrees.worktree_contract import (
    load_contract,
)
from closeout_input_test_support import (
    MutationEvidenceRecorder,
    closeout_operation_input,
    closeout_worktree_args,
    publish_closeout_finalization,
    start_closeout_operation,
)
from lifecycle_control_test_support import cancel_current_generation
from test_closeout_queue import LEAF_A, MASTER_A, SPRINT, QueueFixture
from test_worktree_support import git


class CloseoutQueueIntegrationBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _certified_contract(self):
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        candidate_contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(
                candidate_contract,
                code="close candidate",
                approval_note="approved",
            ),
            launcher=lambda *_: None,
        )
        closeout_store = LifecycleOperationStore(
            operation_record_path(candidate_contract.worktree_group, "closeout")
        )
        closeout_record = closeout_store.read()
        assert closeout_record is not None
        closeout_key = closeout_record.operationKey
        candidate_contract = load_contract(candidate_contract.contract_path)
        claim_queue_candidate_for_closeout(candidate_contract, closeout_key)
        contract = self.fixture.close_contract(MASTER_A)
        publish_closeout_finalization(
            lifecycle_operation_worker.OperationRuntime(closeout_store), contract
        )
        certify_queue_candidate_closeout(contract, closeout_key)
        closeout_store.update(
            lambda record: record.model_copy(
                update={"status": "running", "phase": "contract-finalization"}
            )
        )
        closeout_store.update(
            lambda record: record.model_copy(
                update={
                    "status": "completed",
                    "phase": "completed",
                    "finishedAt": "2026-08-15T00:01:00+00:00",
                    "result": {"state": "closed"},
                }
            )
        )
        git(contract.code_repo_path, "checkout", contract.code_source_branch)
        assert contract.memory_repo_path is not None
        git(contract.memory_repo_path, "checkout", contract.memory_source_branch)
        return contract

    def _integration_record(self, contract):
        start_or_observe_operation(
            IntegrateOperationInput(
                configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
                contractPath=contract.contract_path.as_posix(),
            ),
            contract,
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        return lifecycle_operation_worker.OperationRuntime(store).start()

    def _queue_projection_bytes(self) -> dict[Path, bytes | None]:
        return {
            path: path.read_bytes() if path.is_file() else None
            for path in queue_store_paths(self.fixture.coord, SPRINT)
        }

    def _task_and_ref_truth(self, contract) -> dict[str, object]:
        assert contract.memory_repo_path is not None
        resolved = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
        assert resolved is not None
        task_json, _document = resolved
        task_markdown = task_json.with_suffix(".md")
        return {
            "taskDocument": {
                "json": task_json.read_bytes(),
                "markdown": task_markdown.read_bytes(),
            },
            "refs": {
                "code": git(
                    contract.code_repo_path,
                    "rev-parse",
                    contract.code_source_branch,
                ),
                "memory": git(
                    contract.memory_repo_path,
                    "rev-parse",
                    contract.memory_source_branch,
                ),
            },
        }

    def _assert_cancelled_door(self, claimed_door, cancelled_door) -> None:
        self.assertEqual(
            cancelled_door,
            claimed_door.model_copy(
                update={
                    "disposition": "cancelled",
                    "operationKind": None,
                    "operationFingerprint": "",
                    "claimedOperationKey": "",
                }
            ),
        )

    def _assert_cancelled_operation(self, accepted, durable, cancelled_door) -> None:
        self.assertEqual(durable.status, "cancelled")
        self.assertEqual(durable.phase, "cancelled")
        self.assertEqual(durable.generationDisposition, "cancelled")
        self.assertTrue(durable.cancelRequested)
        self.assertIsNotNone(durable.finishedAt)
        self.assertEqual(durable.operationKey, accepted.operationKey)
        self.assertEqual(durable.generation, accepted.generation)
        self.assertEqual(durable.input, accepted.input)
        self.assertEqual(durable.fingerprint, accepted.fingerprint)
        self.assertEqual(
            durable.doorPublicationHistory,
            [*accepted.doorPublicationHistory, accepted.doorPublication],
        )
        assert durable.doorPublication is not None
        self.assertEqual(durable.doorPublication.state, "proven")
        self.assertEqual(durable.doorPublication.generation, cancelled_door)
        evidence = durable.cancellationEvidence
        assert evidence is not None
        self.assertEqual(evidence.state, "proven-unchanged")
        self.assertEqual(evidence.operationKind, "closeout")
        self.assertEqual(evidence.generation, accepted.generation)
        self.assertTrue(evidence.workerExitProven)
        self.assertTrue(evidence.expected)
        self.assertEqual(evidence.observed, evidence.expected)

    def test_post_intent_exact_claimed_door_cells_match(self) -> None:
        contract = self._certified_contract()
        integration = self._integration_record(contract)
        facts = preview_integration_boundary(contract)
        intent = prepare_integration_publication_intent(
            contract,
            operation_key=integration.operationKey,
            generation=integration.generation,
            facts=IntegrationBoundaryFacts(facts.candidate, None),
            certification=None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        lifecycle_operation_worker.OperationRuntime(store).progress(
            "source-merge",
            {
                "irreversible_boundary": True,
                "recovery_commits": {
                    "codeCommit": contract.code_commit,
                    "memoryContentCommit": contract.memory_content_commit,
                    "ledgerCommit": contract.ledger_commit,
                },
                "integration_publication": intent.model_dump(mode="json"),
            },
        )
        durable = store.read()
        assert durable is not None
        publication = durable.integrationPublication
        self.assertIsNotNone(publication)
        assert publication is not None

        boundary = classify_integration_door_authority(contract, publication)

        self.assertTrue(boundary.valid)
        self.assertEqual(boundary.state, "claimed")
        for cell in (
            "disposition",
            "generationId",
            "operationFingerprint",
            "claimedOperationKey",
        ):
            self.assertEqual(boundary.observed[cell], boundary.expected[cell])
        self.assertEqual(boundary.expected["contractPath"], contract.contract_path.as_posix())
        self.assertEqual(boundary.expected["operationKind"], "integrate")
        self.assertEqual(boundary.expected["generation"], durable.generation)

    def test_production_integrate_claims_revalidates_and_consumes_exact_candidate(self) -> None:
        contract = self._certified_contract()
        integration = self._integration_record(contract)
        result = integrate_mod.integrate_result(
            WorktreeArgs(
                contract_path=contract.contract_path,
                approved=True,
                strategy="ff-only",
                operation_key=integration.operationKey,
                operation_generation=integration.generation,
            ),
            contract,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["state"], "integrated")
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            contract.code_commit,
        )
        self.assertEqual(self.fixture.status()["inFlight"], [])

    def test_closeout_certification_and_integration_claim_bind_exact_commits(self) -> None:
        closed = self._certified_contract()
        integration = self._integration_record(closed)
        facts = preview_integration_boundary(closed)
        intent = prepare_integration_publication_intent(
            closed,
            operation_key=integration.operationKey,
            generation=integration.generation,
            facts=IntegrationBoundaryFacts(facts.candidate, None),
            certification=None,
        )
        self.assertEqual(intent.claimState, "intent")
        proven = transfer_integration_claim(
            closed,
            intent,
            commits=(
                closed.code_commit,
                closed.memory_content_commit,
                closed.ledger_commit,
            ),
        )
        self.assertEqual(proven.claimState, "proven")
        self.assertEqual(transfer_integration_claim(closed, proven, commits=("", "", "")), proven)
        self.assertEqual(self.fixture.status()["inFlight"], [])

    def test_journal_claim_transfer_consumes_queue_once_then_never_reads_it(self) -> None:
        contract = self._certified_contract()
        integration = self._integration_record(contract)
        facts = preview_integration_boundary(contract)
        intent = prepare_integration_publication_intent(
            contract,
            operation_key=integration.operationKey,
            generation=integration.generation,
            facts=IntegrationBoundaryFacts(facts.candidate, None),
            certification=None,
        )
        with mock.patch.object(
            CloseoutQueueStore,
            "transact",
            autospec=True,
            side_effect=CloseoutQueueStore.transact,
        ) as tx:
            proven = transfer_integration_claim(
                contract,
                intent,
                commits=(
                    contract.code_commit,
                    contract.memory_content_commit,
                    contract.ledger_commit,
                ),
            )
        self.assertEqual(tx.call_count, 1)
        for path in queue_store_paths(self.fixture.coord, SPRINT):
            path.unlink(missing_ok=True)
        absent_projection = self._queue_projection_bytes()
        self.assertTrue(all(value is None for value in absent_projection.values()))
        with (
            mock.patch.object(CloseoutQueueStore, "inspect", side_effect=AssertionError("read")),
            mock.patch.object(CloseoutQueueStore, "transact", side_effect=AssertionError("write")),
            mock.patch.object(
                CloseoutQueueStore,
                "transact_with_publication",
                side_effect=AssertionError("publication write"),
            ),
        ):
            self.assertEqual(
                transfer_integration_claim(contract, proven, commits=("", "", "")),
                proven,
            )
        self.assertEqual(self._queue_projection_bytes(), absent_projection)

    def test_retired_door_fences_preserved_stale_candidate_at_every_boundary(
        self,
    ) -> None:
        self._assert_retired_door_fences_stale_candidate("retire")

    def test_superseded_door_fences_preserved_stale_candidate_at_every_boundary(
        self,
    ) -> None:
        self._assert_retired_door_fences_stale_candidate("supersede")

    def _assert_retired_door_fences_stale_candidate(self, action: str) -> None:
        contract = self._certified_contract()
        closeout_store = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        )
        closeout = closeout_store.read()
        assert closeout is not None
        facts = preview_integration_boundary(contract)
        intent = prepare_integration_publication_intent(
            contract,
            operation_key="9" * 64,
            generation=1,
            facts=IntegrationBoundaryFacts(facts.candidate, None),
            certification=None,
        )
        queue_paths = queue_store_paths(self.fixture.coord, SPRINT)
        queue_before = {path: path.read_bytes() for path in queue_paths if path.is_file()}
        owner = DeclaredCaller(
            role="orchestrator",
            task_document_ref=TaskDocumentRef(
                repository=contract.repo_name,
                path=SPRINT.path,
            ),
        )
        row = next(
            item
            for item in legal_operation_controls(
                contract,
                closeout,
                context=LifecycleControlProjectionContext(
                    allow_completed_disposition=True,
                    caller=owner,
                ),
            )
            if item["action"] == action
        )
        result = worktree_operation_control_tool(
            load_config(Path(closeout.input.configPath)),
            OperationControlRequest(**row["arguments"]),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            {path: path.read_bytes() for path in queue_paths if path.is_file()},
            queue_before,
        )
        projected = self.fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))
        stale = next(
            item
            for bucket in ("ready", "blocked", "inFlight")
            for item in projected[bucket]
            if item["taskDocumentRef"] == LEAF_A.model_dump()
        )
        self.assertNotIn("worktree_integrate", stale["legalNextOperations"])
        self.assertTrue(
            any(reason.startswith("closeout-door-not-claimed:") for reason in stale["reasons"])
        )
        retired = load_contract(contract.contract_path)
        with self.assertRaisesRegex(CloseoutQueueError, "integration-blocked"):
            preview_integration_boundary(retired)
        boundary = classify_integration_door_authority(retired, intent)
        self.assertFalse(boundary.valid)
        self.assertEqual(boundary.status, "integration-closeout-door-not-claimed")
        self.assertEqual(boundary.expected["generationId"], intent.closeoutDoorGenerationId)

    def test_declaration_binds_contract_under_queue_then_repository_authority(self) -> None:
        original_transact = CloseoutQueueStore.transact
        queue_active = False
        order: list[str] = []

        def transact_under_marker(store, **kwargs):
            nonlocal queue_active
            queue_active = True
            order.append("queue")
            try:
                return original_transact(store, **kwargs)
            finally:
                queue_active = False

        @contextmanager
        def repository_authority(*_args):
            self.assertTrue(queue_active)
            order.append("repository")
            yield

        with (
            mock.patch.object(
                CloseoutQueueStore,
                "transact",
                new=transact_under_marker,
            ),
            mock.patch.object(
                queue_module,
                "integration_authority_lock",
                new=repository_authority,
            ),
        ):
            self.fixture.declare(MASTER_A, priority=None)

        rebound = load_contract(self.fixture.contracts[MASTER_A].contract_path)
        self.assertEqual(order, ["queue", "repository"])
        self.assertEqual(rebound.queue_sprint_task_document, SPRINT.key)
        self.assertEqual(rebound.queue_candidate_task_document, LEAF_A.key)

    def test_boundary_rechecks_evidence_after_claim_before_source_merge(self) -> None:
        contract = self._certified_contract()
        integration = self._integration_record(contract)
        original = integrate_mod._integrated_memory_commits

        def mutate_after_memory(*args, **kwargs):
            result = original(*args, **kwargs)
            report = contract.worktree_group / "reports" / "curator-memory-quality.md"
            report.write_text(report.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
            return result

        with (
            mock.patch.object(
                integrate_mod,
                "_integrated_memory_commits",
                side_effect=mutate_after_memory,
            ),
            mock.patch.object(integrate_mod, "merge_integrated_commits") as merge,
            self.assertRaises(CloseoutQueueError) as raised,
        ):
            integrate_mod.integrate_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    strategy="ff-only",
                    operation_key=integration.operationKey,
                    operation_generation=integration.generation,
                ),
                contract,
            )
        self.assertEqual(raised.exception.status, "closeout-candidate-integration-blocked")
        self.assertIn("memory-readiness-evidence-stale", str(raised.exception))
        merge.assert_not_called()
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            contract.code_base_commit,
        )

    def test_production_closeout_refuses_when_bound_sprint_graph_disappears(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "missing-graph", memory_mode="internal")
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        contract = fixture.contracts[MASTER_A]
        sprint_path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        write_task_doc(sprint_path.parent, sprint.model_copy(update={"executionGraph": None}))
        with (
            mock.patch.object(closeout_mod, "require_ordinary_worktree"),
            mock.patch.object(closeout_mod, "require_current_source_lineage"),
            mock.patch.object(closeout_mod, "_refuse_unsatisfied_closeout_gate"),
            mock.patch.object(
                closeout_mod,
                "_closeout_quality_preflight",
                return_value=({"status": "passed", "passed": True}, {}, False),
            ),
            mock.patch.object(closeout_mod, "_closeout_commit_phase") as commit,
            self.assertRaisesRegex(CloseoutQueueError, "lost its executionGraph"),
        ):
            closeout_mod.closeout_result(
                closeout_worktree_args(
                    contract,
                    approved=True,
                    approval_note="approved",
                    operation_key="f" * 64,
                    candidate_tree=code_candidate_tree(contract),
                    operation_progress=MutationEvidenceRecorder(),
                ),
                contract,
            )
        commit.assert_not_called()

    def test_production_integration_refuses_when_bound_sprint_graph_disappears(self) -> None:
        contract = self._certified_contract()
        integration = self._integration_record(contract)
        targets = integrate_mod.integration_targets(contract)
        sprint_path = self.fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        write_task_doc(sprint_path.parent, sprint.model_copy(update={"executionGraph": None}))
        with (
            mock.patch.object(integrate_mod, "require_ordinary_worktree"),
            mock.patch.object(integrate_mod, "integration_targets", return_value=targets),
            mock.patch.object(
                integration_operation_authority,
                "integration_targets",
                return_value=targets,
            ),
            mock.patch.object(
                integrate_mod,
                "_integration_source_state_block",
                return_value=None,
            ),
            mock.patch.object(
                integrate_mod,
                "_integration_lineage_block",
                return_value=None,
            ),
            mock.patch.object(integrate_mod, "merge_integrated_commits") as merge,
            self.assertRaisesRegex(CloseoutQueueError, "lost its executionGraph"),
        ):
            integrate_mod.integrate_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    strategy="ff-only",
                    operation_key=integration.operationKey,
                    operation_generation=integration.generation,
                ),
                contract,
            )
        merge.assert_not_called()

    def test_task_addressed_cancellation_does_not_require_or_recreate_queue_projection(
        self,
    ) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(contract, code="close candidate", approval_note="approved"),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
        closeout_record = store.read()
        assert closeout_record is not None
        contract = load_contract(contract.contract_path)
        claim_queue_candidate_for_closeout(contract, closeout_record.operationKey)
        accepted = store.read()
        assert accepted is not None
        contract = load_contract(contract.contract_path)
        claimed_door = contract.closeout_door
        assert claimed_door is not None
        self.assertEqual(claimed_door.disposition, "claimed")
        assert accepted.doorPublication is not None
        self.assertEqual(accepted.doorPublication.state, "proven")
        self.assertEqual(accepted.doorPublication.generation, claimed_door)
        self.assertIsNone(accepted.cancellationEvidence)
        queue_after_claim = self._queue_projection_bytes()
        truth_before = self._task_and_ref_truth(contract)
        with (
            mock.patch.object(
                CloseoutQueueStore,
                "inspect",
                side_effect=AssertionError("read"),
            ) as queue_read,
            mock.patch.object(
                CloseoutQueueStore,
                "transact",
                side_effect=AssertionError("write"),
            ) as queue_write,
            mock.patch.object(
                CloseoutQueueStore,
                "transact_with_publication",
                side_effect=AssertionError("publication write"),
            ) as queue_recreate,
        ):
            cancelled = cancel_current_generation(contract.contract_path, "closeout")
        queue_read.assert_not_called()
        queue_write.assert_not_called()
        queue_recreate.assert_not_called()
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self._queue_projection_bytes(), queue_after_claim)
        self.assertEqual(self._task_and_ref_truth(contract), truth_before)
        cancelled_contract = load_contract(contract.contract_path)
        assert cancelled_contract.closeout_door is not None
        cancelled_door = cancelled_contract.closeout_door
        self._assert_cancelled_door(claimed_door, cancelled_door)
        durable = store.read()
        assert durable is not None
        self._assert_cancelled_operation(accepted, durable, cancelled_door)

    def test_reversible_worker_failure_leaves_queue_projection_unchanged(self) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(contract, code="close candidate", approval_note="approved"),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
        record = store.read()
        assert record is not None
        contract = load_contract(contract.contract_path)
        claim_queue_candidate_for_closeout(contract, record.operationKey)
        queue_before = self._queue_projection_bytes()
        contract_before = contract.contract_path.read_bytes()
        truth_before = self._task_and_ref_truth(contract)
        runtime = lifecycle_operation_worker.OperationRuntime(store)
        with (
            mock.patch.object(CloseoutQueueStore, "inspect", side_effect=AssertionError("read")),
            mock.patch.object(CloseoutQueueStore, "transact", side_effect=AssertionError("write")),
            mock.patch.object(
                CloseoutQueueStore,
                "transact_with_publication",
                side_effect=AssertionError("publication write"),
            ),
        ):
            runtime.start()
            runtime.fail(RuntimeError("reversible failure"))
        failed = store.read()
        assert failed is not None
        self.assertEqual(failed.status, "failed")
        self.assertEqual(self._queue_projection_bytes(), queue_before)
        self.assertEqual(contract.contract_path.read_bytes(), contract_before)
        self.assertEqual(self._task_and_ref_truth(contract), truth_before)

    def test_closeout_transition_order_is_forced_around_irreversible_commit(self) -> None:
        source = inspect.getsource(closeout_mod.closeout_result)
        claim = source.index("claim_queue_candidate_for_closeout")
        commit = source.index("_closeout_commit_phase")
        contract_write = source.index("write_contract")
        certify = source.index("certify_queue_candidate_closeout", claim + 1)
        self.assertLess(claim, commit)
        self.assertLess(commit, contract_write)
        self.assertLess(contract_write, certify)

    def test_production_closeout_commits_exact_tree_and_certifies_queue_candidate(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "production-closeout", memory_mode="internal")
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        contract = fixture.contracts[MASTER_A]
        candidate_tree = code_candidate_tree(contract)
        start_closeout_operation(
            closeout_operation_input(
                contract,
                code="close exact queue candidate",
                approval_note="developer approved exact closeout",
            ),
            launcher=lambda *_: None,
        )
        operation_store = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        )
        runtime = lifecycle_operation_worker.OperationRuntime(operation_store)
        operation = runtime.start()
        assert isinstance(operation.input, CloseoutOperationInput)
        current_contract = load_contract(contract.contract_path)
        with mock.patch.object(
            closeout_mod,
            "_closeout_quality_preflight",
            return_value=({"status": "passed", "passed": True}, {}, False),
        ):
            result = closeout_mod.closeout_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    closeout_input=operation.input.effectiveInput,
                    approved=True,
                    approval_note="developer approved exact closeout",
                    operation_key=operation.operationKey,
                    candidate_tree=candidate_tree,
                    operation_progress=runtime.progress,
                ),
                current_contract,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["state"], "closed")
        closed = load_contract(contract.contract_path)
        self.assertEqual(closed.closeout_status, "completed")
        self.assertEqual(
            git(closed.code_worktree, "rev-parse", f"{closed.code_commit}^{{tree}}"),
            candidate_tree,
        )
        queued = fixture.status()["inFlight"][0]
        self.assertEqual(queued["candidateState"], "certified")
        self.assertEqual(queued["candidateTree"], candidate_tree)

    def test_post_contract_write_pre_certification_crash_recovers_idempotently(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "certify-recovery", memory_mode="internal")
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        contract = fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(
                contract,
                code="close exact queue candidate",
                approval_note="developer approved exact closeout",
            ),
            launcher=lambda *_: None,
        )
        operation_store = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        )
        runtime = lifecycle_operation_worker.OperationRuntime(operation_store)
        runtime.start()
        operation = operation_store.read()
        assert operation is not None
        assert isinstance(operation.input, CloseoutOperationInput)
        current_contract = load_contract(contract.contract_path)
        operation_key = operation.operationKey
        first = WorktreeArgs(
            contract_path=contract.contract_path,
            closeout_input=operation.input.effectiveInput,
            approved=True,
            approval_note="developer approved exact closeout",
            operation_key=operation_key,
            candidate_tree=code_candidate_tree(current_contract),
            operation_progress=runtime.progress,
        )
        with (
            mock.patch.object(
                closeout_mod,
                "_closeout_quality_preflight",
                return_value=({"status": "passed", "passed": True}, {}, False),
            ),
            mock.patch.object(
                closeout_mod,
                "certify_queue_candidate_closeout",
                side_effect=RuntimeError("certification publish interrupted"),
            ),
            self.assertRaisesRegex(RuntimeError, "certification publish interrupted"),
        ):
            closeout_mod.closeout_result(first, current_contract)
        closed = load_contract(contract.contract_path)
        self.assertEqual(closed.closeout_status, "completed")
        state_path, _pending_path = queue_store_paths(fixture.coord, SPRINT)
        durable = CloseoutQueueState.model_validate_json(state_path.read_text(encoding="utf-8"))
        self.assertEqual(durable.candidates[LEAF_A.key].state, "closeout-in-flight")
        projected = fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))
        self.assertEqual(projected["inFlight"][0]["candidateState"], "closeout-in-flight")
        self.assertEqual(
            projected["inFlight"][0]["legalNextOperations"],
            ["worktree_closeout_apply"],
        )
        self.assertEqual(projected["blocked"], [])

        recovered = closeout_mod.closeout_result(
            WorktreeArgs(
                contract_path=closed.contract_path,
                closeout_input=operation.input.effectiveInput,
                operation_key=operation_key,
                recovery_commits=LifecycleOperationRecoveryCommits(
                    codeCommit=closed.code_commit,
                ),
                operation_progress=runtime.progress,
            ),
            closed,
        )
        self.assertEqual(recovered.payload["state"], "already-closed")
        self.assertEqual(fixture.status()["inFlight"][0]["candidateState"], "certified")
