"""L13-R2: the leaf-altitude closeout lane — sync-first admission, stale-by-evidence.

Closeout and integration serialize per super branch: admission requires the
candidate's recorded base pair to equal the current target tip and names
``worktree_sync`` as the recovery when it does not; when a landing releases the
lane, in-flight siblings whose recorded base pair no longer matches the new
source tips are reported stale-by-evidence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.application import lifecycle_operation_worker
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
)
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees import series_closeout
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle_operations import start_or_observe_operation
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.queue import closeout_queue as queue
from agents_remember.worktrees.queue import closeout_queue_lifecycle as lifecycle
from agents_remember.worktrees.queue.closeout_queue_blocker import _stale_sibling_facts
from agents_remember.worktrees.queue.closeout_queue_candidate_evidence import (
    require_source_bases_current,
)
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    _boundary_recovery,
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
)
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from closeout_input_test_support import closeout_operation_input, start_closeout_operation
from test_closeout_queue import LEAF_A, LEAF_B, MASTER_A, MASTER_B, NOW, SPRINT, QueueFixture
from test_worktree_support import git


def _close_and_certify(fixture: QueueFixture, master) -> WorktreeContract:
    """Drive one declared leaf through the exact closeout lane to certified."""

    fixture.declare(master)
    leaf = fixture.leaf_refs[master]
    fixture.mutate("select", candidate=leaf)
    contract = fixture.contracts[master]
    start_closeout_operation(
        closeout_operation_input(
            contract,
            config_path=fixture.config_path,
            code="close leaf",
            memory="close memory",
            ledger="map pair",
            approval_note="approved",
        ),
        launcher=lambda *_: None,
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    record = runtime.start()
    claim_queue_candidate_for_closeout(contract, record.operationKey)
    closed = fixture.close_contract(master)
    certify_queue_candidate_closeout(closed, record.operationKey)
    runtime.finish({"ok": True}, ok=True)
    git(closed.code_repo_path, "checkout", closed.code_source_branch)
    assert closed.memory_repo_path is not None
    git(closed.memory_repo_path, "checkout", closed.memory_source_branch)
    return closed


def _integrate(fixture: QueueFixture, contract):
    """Land one certified leaf through the plane-owned integration operation."""

    start_or_observe_operation(
        IntegrateOperationInput(
            configPath=fixture.config_path.as_posix(),
            contractPath=contract.contract_path.as_posix(),
            autoCompleteSeats=False,
        ),
        launcher=lambda *_: None,
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    record = runtime.start()
    args = WorktreeArgs(
        contract_path=contract.contract_path,
        approved=True,
        strategy="ff-only",
        operation_key=record.operationKey,
        recovery_commits=record.recoveryCommits,
        quality_certification=record.qualityCertification,
        operation_progress=runtime.progress,
    )
    return runtime, integrate_mod.integrate_result(args)


class CloseoutLaneSyncFirstTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_moved_source_names_worktree_sync_as_the_recovery(self) -> None:
        self.fixture.declare(MASTER_A)
        contract = self.fixture.contracts[MASTER_A]
        # Super moved after the leaf started: admission requires sync first.
        git(contract.code_repo_path, "checkout", contract.code_source_branch)
        (contract.code_repo_path / "moved.txt").write_text("moved\n", encoding="utf-8")
        git(contract.code_repo_path, "add", "moved.txt")
        git(contract.code_repo_path, "commit", "-m", "move super")
        with self.assertRaises(CloseoutQueueError) as raised:
            require_source_bases_current(load_contract(contract.contract_path))
        self.assertIn("worktree_sync", str(raised.exception))

    def test_boundary_recovery_suffix_names_worktree_sync(self) -> None:
        self.assertEqual(
            _boundary_recovery(["code-source-moved: run worktree_sync, then retry"]),
            "; recovery: worktree_sync",
        )
        self.assertEqual(
            _boundary_recovery(["memory-source-moved: run worktree_sync, then retry"]),
            "; recovery: worktree_sync",
        )
        self.assertEqual(_boundary_recovery(["leaf-task-incomplete"]), "")

    def test_lane_release_reports_stale_siblings_by_evidence(self) -> None:
        # Declare the sibling leaf, then land the first leaf: the sibling's
        # recorded base pair no longer matches the moved super tips.
        self.fixture.declare(MASTER_B)
        closed = _close_and_certify(self.fixture, MASTER_A)
        runtime, result = _integrate(self.fixture, closed)
        self.assertEqual(result.returncode, 0, result.payload)
        stale = result.payload.get("staleByEvidence")
        assert isinstance(stale, list)
        self.assertEqual([row["candidate"] for row in stale], [LEAF_B.key])
        self.assertEqual(stale[0]["recovery"], "worktree_sync")
        self.assertNotEqual(stale[0]["recordedCodeBase"], stale[0]["currentCodeSourceTip"])
        runtime.finish(result.payload, ok=True)

    def test_unreadable_sibling_contract_is_reported_not_swallowed(self) -> None:
        # L13 review: the lane-release fact sweep reports an unreadable sibling
        # contract as a fact row instead of silently skipping it.
        self.fixture.declare(MASTER_B)
        topology = TaskDocumentTopology(self.fixture.coord)
        graph = queue._graph_context(topology, SPRINT)
        state = CloseoutQueueStore(self.fixture.coord, SPRINT).read(
            queue._initial_state(SPRINT, graph.revision, NOW)
        )
        self.fixture.contracts[MASTER_B].contract_path.unlink()
        facts = _stale_sibling_facts(state)
        self.assertEqual(
            facts,
            [
                {
                    "candidate": LEAF_B.key,
                    "owningMaster": MASTER_B.key,
                    "fact": "contract-unreadable",
                }
            ],
        )

    def test_sibling_with_current_bases_reports_no_stale_fact(self) -> None:
        # The not-stale arm: a sibling whose recorded base pair still matches the
        # source tips produces no stale-by-evidence row.
        self.fixture.declare(MASTER_B)
        topology = TaskDocumentTopology(self.fixture.coord)
        graph = queue._graph_context(topology, SPRINT)
        state = CloseoutQueueStore(self.fixture.coord, SPRINT).read(
            queue._initial_state(SPRINT, graph.revision, NOW)
        )
        self.assertEqual(_stale_sibling_facts(state), [])


class CloseoutLaneSerializationTests(unittest.TestCase):
    """L13-R2/R3: the landing lane serializes; refusal branches report, never swallow."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_integration_claim_refuses_when_another_candidate_owns_the_lane(self) -> None:
        self.fixture.declare(MASTER_A)
        self.fixture.declare(MASTER_B)
        topology = TaskDocumentTopology(self.fixture.coord)
        graph = queue._graph_context(topology, SPRINT)
        state = CloseoutQueueStore(self.fixture.coord, SPRINT).read(
            queue._initial_state(SPRINT, graph.revision, NOW)
        )
        lane_holder = state.candidates[LEAF_A.key].model_copy(
            update={
                "state": "integration-in-flight",
                "inFlightOwnerFingerprint": "a" * 64,
                "closeoutCodeCommit": "a" * 40,
                "closeoutMemoryContentCommit": "b" * 40,
                "closeoutLedgerCommit": "c" * 40,
            }
        )
        waiting = state.candidates[LEAF_B.key].model_copy(
            update={
                "state": "certified",
                "closeoutCodeCommit": "d" * 40,
                "closeoutMemoryContentCommit": "e" * 40,
                "closeoutLedgerCommit": "f" * 40,
            }
        )
        state = state.model_copy(
            update={"candidates": {LEAF_A.key: lane_holder, LEAF_B.key: waiting}}
        )
        context = lifecycle._LifecycleCandidateContext(
            topology, graph, state, self.fixture.contracts[MASTER_B], "0" * 64
        )
        with (
            mock.patch.object(lifecycle, "_post_closeout_blockers", return_value=[]),
            self.assertRaises(CloseoutQueueError) as raised,
        ):
            lifecycle._claim_integration(context, waiting)
        self.assertIn("integration-lane-owned-by", str(raised.exception))
        self.assertIn(LEAF_A.key, str(raised.exception))

    def test_series_edge_publication_skips_the_queue_under_the_default_mode(self) -> None:
        # L13-R1: under a graph-less sprint the series publishes through its live
        # series contract without any queue graph.
        fixture = QueueFixture(Path(self.temp.name) / "atomic", atomic_b=True)
        path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(path)
        write_task_doc(path.parent, sprint.model_copy(update={"executionGraph": None}))
        contract = load_contract(series_contract_path(fixture.tasks / "master-b"))
        with (
            mock.patch.object(series_closeout, "_require_atomic_master_complete"),
            mock.patch.object(series_closeout, "_require_every_atomic_leaf_landed"),
        ):
            self.assertEqual(
                series_closeout._publish_atomic_series_edge(
                    contract, lambda: "published", edge="closeout"
                ),
                "published",
            )

    def test_series_closeout_reports_the_effective_nature_refusal(self) -> None:
        # A nature-less master under an authored graph: the effective-nature
        # resolution raises and the closeout surfaces it as a typed invalid-task.
        topology = TaskDocumentTopology(self.fixture.coord)
        with (
            mock.patch.object(
                series_closeout,
                "effective_execution_nature",
                side_effect=TaskDocumentRefError(
                    "task-execution-topology-migration-required", "no nature"
                ),
            ),
            self.assertRaises(CloseoutQueueError) as raised,
        ):
            series_closeout._require_atomic_master_complete(topology, MASTER_A)
        self.assertIn("atomic-series-closeout-task-invalid", str(raised.exception))

    def test_terminal_authority_reports_the_effective_nature_refusal(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "atomic", atomic_b=True)
        contract = load_contract(series_contract_path(fixture.tasks / "master-b"))
        with (
            mock.patch.object(
                lifecycle,
                "effective_execution_nature",
                side_effect=TaskDocumentRefError(
                    "task-execution-topology-migration-required", "no nature"
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "cannot resolve atomic terminal authority"),
        ):
            lifecycle.require_atomic_series_terminal_release(contract)
