from __future__ import annotations

import inspect
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from agents_remember.application import lifecycle_operation_worker
from agents_remember.application.closeout_queue import CloseoutQueueError
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore, queue_store_paths
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.queue.closeout_queue import CloseoutQueueState
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.integration import integration_operation_authority
from agents_remember.worktrees.integration.integration_ref_transaction import IntegrationSources
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle_operations import (
    cancel_operation,
    start_or_observe_operation,
)
from agents_remember.worktrees.modules import closeout as closeout_mod
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.queue import closeout_queue as queue_module
from agents_remember.worktrees.queue import closeout_queue_lifecycle as queue_lifecycle
from agents_remember.worktrees.queue.closeout_queue import QueueActor
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
    claim_queue_candidate_for_integration,
    complete_queue_candidate_integration,
    require_queue_candidate_for_integration,
)
from agents_remember.worktrees.route_review import code_candidate_tree
from agents_remember.worktrees.worktree_contract import (
    load_contract,
)
from closeout_input_test_support import (
    MutationEvidenceRecorder,
    closeout_operation_input,
    closeout_worktree_args,
    start_closeout_operation,
)
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
        claim_queue_candidate_for_closeout(candidate_contract, closeout_key)
        contract = self.fixture.close_contract(MASTER_A)
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
                }
            )
        )
        git(contract.code_repo_path, "checkout", contract.code_source_branch)
        assert contract.memory_repo_path is not None
        git(contract.memory_repo_path, "checkout", contract.memory_source_branch)
        return contract

    def _integration_key(self, contract) -> str:
        start_or_observe_operation(
            IntegrateOperationInput(
                configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
                contractPath=contract.contract_path.as_posix(),
            ),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        return lifecycle_operation_worker.OperationRuntime(store).start().operationKey

    def test_production_integrate_claims_revalidates_and_consumes_exact_candidate(self) -> None:
        contract = self._certified_contract()
        integration_key = self._integration_key(contract)
        result = integrate_mod.integrate_result(
            WorktreeArgs(
                contract_path=contract.contract_path,
                approved=True,
                strategy="ff-only",
                operation_key=integration_key,
            )
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
        integration_key = self._integration_key(closed)
        claim_queue_candidate_for_integration(closed, integration_key)
        require_queue_candidate_for_integration(
            closed,
            operation_key=integration_key,
            code_commit=closed.code_commit,
            memory_content_commit=closed.memory_content_commit,
            ledger_commit=closed.ledger_commit,
        )
        complete_queue_candidate_integration(
            closed,
            operation_key=integration_key,
            code_commit=closed.code_commit,
            memory_content_commit=closed.memory_content_commit,
            ledger_commit=closed.ledger_commit,
        )
        self.assertEqual(self.fixture.status()["inFlight"], [])

    def test_governed_integration_acquires_queue_before_repository_authority(self) -> None:
        contract = self._certified_contract()
        integration_key = self._integration_key(contract)
        claim_queue_candidate_for_integration(contract, integration_key)
        original_inspect = CloseoutQueueStore.inspect
        queue_active = False
        order: list[str] = []

        def inspect_under_marker(store, initial, reader):
            def marked(state):
                nonlocal queue_active
                queue_active = True
                order.append("queue")
                try:
                    return reader(state)
                finally:
                    queue_active = False

            return original_inspect(store, initial, marked)

        @contextmanager
        def repository_authority(*_args):
            self.assertTrue(queue_active)
            order.append("repository")
            yield

        with (
            mock.patch.object(CloseoutQueueStore, "inspect", new=inspect_under_marker),
            mock.patch.object(
                queue_lifecycle,
                "integration_authority_lock",
                new=repository_authority,
            ),
        ):
            result = queue_lifecycle.publish_queue_candidate_integration_under_authority(
                contract,
                lambda: order.append("publication") or "published",
                operation_key=integration_key,
                commits=(
                    contract.code_commit,
                    contract.memory_content_commit,
                    contract.ledger_commit,
                ),
            )

        self.assertEqual(result, "published")
        self.assertEqual(order, ["queue", "repository", "publication"])

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

    def test_safe_ref_race_does_not_consume_the_in_flight_candidate(self) -> None:
        contract = self._certified_contract()
        integration_key = self._integration_key(contract)
        claim_queue_candidate_for_integration(contract, integration_key)
        commits = integrate_mod.IntegratedCommits(
            contract.code_commit,
            contract.memory_content_commit,
            contract.ledger_commit,
        )
        with (
            mock.patch.object(
                integrate_mod,
                "_prepare_integration_commits",
                return_value=(commits, {"passed": True}, None, False),
            ),
            mock.patch.object(
                integrate_mod,
                "_integration_source_state_block",
                return_value=None,
            ),
            mock.patch.object(
                integrate_mod,
                "prepare_integration_ref_move",
                return_value=mock.sentinel.snapshot,
            ),
            mock.patch.object(
                integrate_mod,
                "merge_integrated_commits",
                side_effect=integrate_mod.IntegrationRefRace(
                    "expected-old changed before first CAS",
                    safe_to_replace=True,
                ),
            ),
        ):
            result = integrate_mod._apply_integration(
                contract,
                WorktreeArgs(operation_key=integration_key),
                IntegrationSources(
                    contract.code_base_commit,
                    contract.memory_base_commit,
                    False,
                    False,
                ),
                handover_warning=None,
            )

        self.assertEqual(result.payload["state"], "integration-ref-raced")
        projected = self.fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))
        self.assertEqual(
            projected["inFlight"][0]["candidateState"],
            "integration-in-flight",
        )

    def test_boundary_rechecks_evidence_after_claim_before_source_merge(self) -> None:
        contract = self._certified_contract()
        integration_key = self._integration_key(contract)
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
                    operation_key=integration_key,
                )
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
                )
            )
        commit.assert_not_called()

    def test_production_integration_refuses_when_bound_sprint_graph_disappears(self) -> None:
        contract = self._certified_contract()
        integration_key = self._integration_key(contract)
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
                    operation_key=integration_key,
                )
            )
        merge.assert_not_called()

    def test_task_addressed_cancellation_releases_internal_queue_ownership(self) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(contract, code="close candidate", approval_note="approved"),
            launcher=lambda *_: None,
        )
        closeout_record = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        ).read()
        assert closeout_record is not None
        claim_queue_candidate_for_closeout(contract, closeout_record.operationKey)
        self.assertEqual(
            self.fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))["inFlight"][
                0
            ]["legalNextOperations"],
            ["worktree_closeout_apply", "worktree_operation_cancel"],
        )
        cancelled = cancel_operation(contract.contract_path, "closeout")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self.fixture.status()["ready"][0]["candidateState"], "declared")

        self.fixture.mutate("select", candidate=LEAF_A)
        claim_queue_candidate_for_closeout(contract, "2" * 64)
        certified_contract = self.fixture.close_contract(MASTER_A)
        certify_queue_candidate_closeout(certified_contract, "2" * 64)
        start_or_observe_operation(
            IntegrateOperationInput(
                configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
                contractPath=contract.contract_path.as_posix(),
            ),
            launcher=lambda *_: None,
        )
        integration_record = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "integrate")
        ).read()
        assert integration_record is not None
        claim_queue_candidate_for_integration(certified_contract, integration_record.operationKey)
        self.assertEqual(
            self.fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))["inFlight"][
                0
            ]["legalNextOperations"],
            ["worktree_integrate", "worktree_operation_cancel"],
        )
        cancelled = cancel_operation(contract.contract_path, "integrate")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self.fixture.status()["inFlight"][0]["candidateState"], "certified")

    def test_reversible_worker_failure_releases_internal_queue_ownership(self) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(contract, code="close candidate", approval_note="approved"),
            launcher=lambda *_: None,
        )
        record = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        ).read()
        assert record is not None
        claim_queue_candidate_for_closeout(contract, record.operationKey)
        with mock.patch.object(
            lifecycle_operation_worker,
            "execute_operation",
            side_effect=RuntimeError("reversible failure"),
        ):
            result = lifecycle_operation_worker.run_worker(contract.contract_path, "closeout")
        self.assertEqual(result, 1)
        self.assertEqual(self.fixture.status()["ready"][0]["candidateState"], "declared")

    def test_cancel_release_failure_still_terminates_worker_and_requeues_same_operation(
        self,
    ) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        operation_input = closeout_operation_input(
            contract, code="close candidate", approval_note="approved"
        )
        start_closeout_operation(operation_input, launcher=lambda *_: None)
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
        store.update(lambda record: record.model_copy(update={"workerPid": 4321}))
        record = store.read()
        assert record is not None
        claim_queue_candidate_for_closeout(contract, record.operationKey)
        with (
            mock.patch(
                "agents_remember.worktrees.integration.lifecycle_operations."
                "release_queue_candidate_after_reversible_operation",
                side_effect=CloseoutQueueError("queue-release-blocked", "topology invalid"),
            ),
            mock.patch(
                "agents_remember.worktrees.integration.lifecycle_operations._terminate_worker_group"
            ) as terminate,
            self.assertRaisesRegex(CloseoutQueueError, "queue-release-blocked"),
        ):
            cancel_operation(contract.contract_path, "closeout")
        terminate.assert_called_once_with(4321)
        cancelled = store.read()
        assert cancelled is not None
        self.assertEqual((cancelled.status, cancelled.workerPid), ("cancelled", None))
        blocked = self.fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))[
            "blocked"
        ][0]
        self.assertEqual(blocked["legalNextOperations"], ["worktree_closeout_apply"])
        retried = start_closeout_operation(operation_input, launcher=lambda *_: None)
        self.assertEqual(retried.status, "queued")

    def test_reversible_worker_release_failure_remains_visible_in_operation_record(self) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(contract, code="close candidate", approval_note="approved"),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
        queued = store.read()
        assert queued is not None
        claim_queue_candidate_for_closeout(contract, queued.operationKey)
        with (
            mock.patch.object(
                lifecycle_operation_worker,
                "load_config",
                return_value=self.fixture.cfg,
            ),
            mock.patch.object(
                lifecycle_operation_worker,
                "closeout_result",
                return_value=WorktreeCommandResult(1, {"reason": "reversible failure"}),
            ),
            mock.patch.object(
                lifecycle_operation_worker,
                "release_queue_candidate_after_reversible_operation",
                side_effect=CloseoutQueueError("queue-release-blocked", "topology invalid"),
            ) as release,
        ):
            result = lifecycle_operation_worker.run_worker(contract.contract_path, "closeout")
        release.assert_called_once()
        self.assertEqual(result, 0)
        record = store.read()
        assert record is not None
        self.assertEqual(record.status, "failed")
        assert record.result is not None
        self.assertIn("queue-release-blocked", str(record.result.get("queueReleaseFailure")))
        blocked = self.fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))[
            "blocked"
        ][0]
        self.assertEqual(blocked["legalNextOperations"], ["worktree_closeout_apply"])
        retried = start_closeout_operation(
            closeout_operation_input(contract, code="close candidate", approval_note="approved"),
            launcher=lambda *_: None,
        )
        self.assertEqual(retried.status, "queued")

    def test_worker_exception_combines_reversible_queue_release_failure(self) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(contract, code="close candidate", approval_note="approved"),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
        queued = store.read()
        assert queued is not None
        claim_queue_candidate_for_closeout(contract, queued.operationKey)
        with (
            mock.patch.object(
                lifecycle_operation_worker,
                "execute_operation",
                side_effect=RuntimeError("worker exploded"),
            ),
            mock.patch.object(
                lifecycle_operation_worker,
                "release_queue_candidate_after_reversible_operation",
                side_effect=CloseoutQueueError("queue-release-blocked", "topology invalid"),
            ) as release,
        ):
            result = lifecycle_operation_worker.run_worker(contract.contract_path, "closeout")
        release.assert_called_once()
        self.assertEqual(result, 1)
        record = store.read()
        assert record is not None
        self.assertEqual(record.status, "failed")
        assert record.result is not None
        self.assertIn("worker exploded", str(record.result.get("reason")))
        self.assertIn("queue-release-blocked", str(record.result.get("reason")))

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
                )
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
        operation_key = operation.operationKey
        first = WorktreeArgs(
            contract_path=contract.contract_path,
            closeout_input=operation.input.effectiveInput,
            approved=True,
            approval_note="developer approved exact closeout",
            operation_key=operation_key,
            candidate_tree=code_candidate_tree(contract),
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
            closeout_mod.closeout_result(first)
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
            )
        )
        self.assertEqual(recovered.payload["state"], "already-closed")
        self.assertEqual(fixture.status()["inFlight"][0]["candidateState"], "certified")
