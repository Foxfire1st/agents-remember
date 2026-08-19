from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.application import closeout_queue as public_queue
from agents_remember.application.task_doc_tools import TaskDocEdit, TaskDocTarget, task_doc_tool
from agents_remember.controlplane.closeout_queue_store import (
    QUEUE_OWNERSHIP,
    CloseoutQueueStore,
    CloseoutQueueStoreError,
    queue_store_paths,
)
from agents_remember.controlplane.durable_store import CompactionOwnerError
from agents_remember.models.closeout_queue import CloseoutQueueState
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
)
from agents_remember.models.task_document_ref import (
    MAX_TASK_DOCUMENT_PATH_LENGTH,
    MAX_TASK_REPOSITORY_LENGTH,
    TaskDocumentRef,
)
from agents_remember.tasks import (
    SprintExecutionGraph,
    SprintExecutionNode,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_queue import (
    CloseoutQueueError,
    CloseoutQueueRequest,
    QueueActor,
    _graph_context,
)
from agents_remember.worktrees.closeout_queue_graph import incomplete_predecessor_map
from agents_remember.worktrees.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
    claim_queue_candidate_for_integration,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.lifecycle_operations import start_or_observe_operation
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.worktree_contract import write_contract
from pydantic import ValidationError
from test_closeout_queue import (
    LEAF_A,
    LEAF_B,
    MASTER_A,
    MASTER_B,
    SPRINT,
    QueueFixture,
    _curator_report,
    _write_curator_evidence,
)
from test_worktree_support import git


class CloseoutQueueEvidenceForcingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_task_document_identity_is_runtime_bounded_without_unsupported_schema(self) -> None:
        with self.assertRaisesRegex(ValidationError, "repository exceeds"):
            TaskDocumentRef(
                repository="r" * (MAX_TASK_REPOSITORY_LENGTH + 1),
                path="leaf.json",
            )
        with self.assertRaisesRegex(ValidationError, "task document path exceeds"):
            TaskDocumentRef(
                repository="repo",
                path="p" * MAX_TASK_DOCUMENT_PATH_LENGTH + ".json",
            )

        schema = TaskDocumentRef.model_json_schema()["properties"]
        self.assertNotIn("maxLength", schema["repository"])
        self.assertNotIn("maxLength", schema["path"])

    def test_full_route_record_and_in_place_evidence_bytes_are_bound(self) -> None:
        record_fixture = QueueFixture(self.root / "record")
        record_fixture.declare(MASTER_A)
        task_path = record_fixture.contracts[MASTER_A].task_root / "leaf-a.json"
        leaf = read_task_doc(task_path)
        payload = leaf.model_dump(mode="json")
        payload["routeReview"]["reviewedAt"] = "2026-08-15T00:00:01+00:00"
        write_task_doc(task_path.parent, TaskDocument.model_validate(payload))
        record_blocked = record_fixture.status()["blocked"][0]
        self.assertIn("route-review-stale", record_blocked["reasons"])

        evidence_fixture = QueueFixture(self.root / "evidence")
        evidence_fixture.declare(MASTER_A)
        report = (
            evidence_fixture.contracts[MASTER_A].task_root
            / "notes"
            / "reports"
            / "leaf-a-review.md"
        )
        report.write_text(report.read_text(encoding="utf-8") + "Changed bytes.\n", encoding="utf-8")
        evidence_blocked = evidence_fixture.status()["blocked"][0]
        self.assertIn("route-review-stale", evidence_blocked["reasons"])

    def test_graph_revision_and_transitive_source_lineage_recompute_before_closeout(self) -> None:
        graph_fixture = QueueFixture(self.root / "graph")
        declared = graph_fixture.declare(MASTER_A)
        self.assertEqual(declared["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())
        sprint_path = graph_fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        payload = sprint.model_dump(mode="json")
        payload["executionGraph"]["edges"] = [
            {
                "predecessor": MASTER_A.model_dump(mode="json"),
                "successor": MASTER_B.model_dump(mode="json"),
                "reason": "replacement graph revision",
            }
        ]
        write_task_doc(sprint_path.parent, TaskDocument.model_validate(payload))
        self.assertIn("graph-revision-stale", graph_fixture.status()["blocked"][0]["reasons"])

        lineage_fixture = QueueFixture(self.root / "lineage")
        lineage_fixture.declare(MASTER_A)
        git(lineage_fixture.code, "checkout", "super")
        git(lineage_fixture.code, "commit", "--allow-empty", "-m", "advance super")
        reasons = lineage_fixture.status()["blocked"][0]["reasons"]
        self.assertIn("source-lineage-stale", reasons)

    def test_claim_recomputes_graph_under_lock_and_lane_ownership_freezes_task_tree_writes(
        self,
    ) -> None:
        fixture = QueueFixture(self.root / "graph-lock")
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        target = TaskDocTarget(repo_id="repo-a", task_name="sprint")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "facts are frozen"):
            task_doc_tool(
                fixture.cfg,
                target,
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "racing graph publication"}),
            )
        for governed_target in (
            TaskDocTarget(repo_id="repo-a", task_name="master-a"),
            TaskDocTarget(repo_id="repo-a", task_name="master-a", slug="leaf-a"),
        ):
            with (
                self.subTest(target=governed_target),
                self.assertRaisesRegex(CloseoutQueueStoreError, "facts are frozen"),
            ):
                task_doc_tool(
                    fixture.cfg,
                    governed_target,
                    operation="set_field",
                    edit=TaskDocEdit(fields={"objective": "racing governed task publication"}),
                )
        with self.assertRaisesRegex(CloseoutQueueStoreError, "facts are frozen"):
            task_doc_tool(
                fixture.cfg,
                TaskDocTarget(repo_id="repo-a", task_name="master-b", slug="leaf-b"),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "race through master sync"}),
            )

        original_transact = CloseoutQueueStore.transact
        sprint_path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        payload = sprint.model_dump(mode="json")
        payload["executionGraph"]["edges"] = [
            {
                "predecessor": MASTER_A.model_dump(mode="json"),
                "successor": MASTER_B.model_dump(mode="json"),
                "reason": "concurrent replacement graph",
            }
        ]

        def publish_graph_before_lock(store, **kwargs):
            write_task_doc(sprint_path.parent, TaskDocument.model_validate(payload))
            return original_transact(store, **kwargs)

        with (
            mock.patch.object(
                CloseoutQueueStore,
                "transact",
                new=publish_graph_before_lock,
            ),
            self.assertRaisesRegex(CloseoutQueueError, "graph-revision-stale"),
        ):
            claim_queue_candidate_for_closeout(fixture.contracts[MASTER_A], "a" * 64)

    def test_predecessor_index_is_linear_and_node_edge_admission_is_bounded(self) -> None:
        for size in (16, 64):
            nodes = [
                TaskDocumentRef(repository="repo-a", path=f"master-{number:03d}/task.json")
                for number in range(size)
            ]
            graph = SprintExecutionGraph.model_validate(
                {
                    "nodes": nodes,
                    "edges": [
                        {
                            "predecessor": nodes[number],
                            "successor": nodes[number + 1],
                            "reason": "linear dependency",
                        }
                        for number in range(size - 1)
                    ],
                }
            )
            incomplete = incomplete_predecessor_map(
                graph,
                completed=set(),
            )
            self.assertEqual(sum(map(len, incomplete.values())), size - 1)

        fixture = QueueFixture(self.root / "graph-limits")
        sprint_path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        too_many_nodes = [
            TaskDocumentRef(repository="repo-a", path=f"node-{number:03d}/task.json")
            for number in range(257)
        ]
        write_task_doc(
            sprint_path.parent,
            sprint.model_copy(
                update={
                    "orchestrates": [f"node-{number:03d}" for number in range(257)],
                    "executionGraph": SprintExecutionGraph(
                        nodes=[SprintExecutionNode(ref=ref) for ref in too_many_nodes]
                    ),
                }
            ),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "more than 256 graph masters"):
            _graph_context(TaskDocumentTopology(fixture.coord), SPRINT)

        edge_nodes = [
            TaskDocumentRef(repository="repo-a", path=f"edge-{number:03d}/task.json")
            for number in range(100)
        ]
        edges = [
            {
                "predecessor": edge_nodes[left],
                "successor": edge_nodes[right],
                "reason": "bounded dense dependency",
            }
            for left in range(100)
            for right in range(left + 1, 100)
        ][:4097]
        write_task_doc(
            sprint_path.parent,
            sprint.model_copy(
                update={
                    "orchestrates": [f"edge-{number:03d}" for number in range(100)],
                    "executionGraph": SprintExecutionGraph.model_validate(
                        {"nodes": edge_nodes, "edges": edges}
                    ),
                }
            ),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "more than 4096 dependency edges"):
            _graph_context(TaskDocumentTopology(fixture.coord), SPRINT)

    def test_atomic_blocker_allows_only_topology_stable_work_inside_its_master(self) -> None:
        fixture = QueueFixture(self.root / "blocker-task-scope", atomic_b=True)
        fixture.mutate(
            "acquire-blocker",
            blocker=MASTER_B,
            rationale="Isolate the atomic master sequence.",
        )
        task_doc_tool(
            fixture.cfg,
            TaskDocTarget(repo_id="repo-a", task_name="master-b"),
            operation="set_field",
            edit=TaskDocEdit(fields={"objective": "continue the isolated atomic block"}),
        )
        self.assertEqual(
            read_task_doc(fixture.tasks / "master-b" / "task.json").objective,
            "continue the isolated atomic block",
        )
        for blocked_target in (
            TaskDocTarget(repo_id="repo-a", task_name="master-a"),
            TaskDocTarget(repo_id="repo-a", task_name="sprint"),
        ):
            with (
                self.subTest(target=blocked_target),
                self.assertRaisesRegex(CloseoutQueueStoreError, "facts are frozen"),
            ):
                task_doc_tool(
                    fixture.cfg,
                    blocked_target,
                    operation="set_field",
                    edit=TaskDocEdit(fields={"objective": "cross the atomic blocker"}),
                )

    def test_atomic_blocker_allows_own_master_reopen_and_refuses_another_master(self) -> None:
        fixture = QueueFixture(self.root / "blocker-reopen-scope", atomic_b=True)

        def prepare_completed_leaf(master_ref: TaskDocumentRef, leaf_ref: TaskDocumentRef):
            closed = fixture.close_contract(master_ref)
            assert closed.memory_worktree is not None
            assert closed.ledger_commit
            git(fixture.code, "branch", "-f", closed.code_source_branch, closed.code_commit)
            git(
                fixture.memory,
                "branch",
                "-f",
                closed.memory_source_branch,
                closed.ledger_commit,
            )
            git(fixture.code, "worktree", "remove", "--force", str(closed.code_worktree))
            git(
                fixture.memory,
                "worktree",
                "remove",
                "--force",
                str(closed.memory_worktree),
            )
            completed = replace(
                closed,
                integration_status="completed",
                integrated_code_commit=closed.code_commit,
                integrated_memory_content_commit=closed.memory_content_commit,
                integrated_ledger_commit=closed.ledger_commit,
                cleanup="completed",
            )
            write_contract(completed.contract_path, completed)
            fixture.contracts[master_ref] = completed
            leaf_path = fixture.tasks / leaf_ref.path
            leaf = read_task_doc(leaf_path)
            write_task_doc(leaf_path.parent, leaf.model_copy(update={"status": "Completed"}))
            master = fixture.master_docs[master_ref]
            completed_master = master.model_copy(
                update={
                    "status": "Completed",
                    "subTasks": [
                        row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                    ],
                }
            )
            write_task_doc(fixture.tasks / Path(master_ref.path).parent, completed_master)
            return completed

        other = prepare_completed_leaf(MASTER_A, LEAF_A)
        stale_atomic = fixture.contracts[MASTER_B]
        assert stale_atomic.memory_worktree is not None
        git(
            fixture.code,
            "worktree",
            "remove",
            "--force",
            str(stale_atomic.code_worktree),
        )
        git(
            fixture.memory,
            "worktree",
            "remove",
            "--force",
            str(stale_atomic.memory_worktree),
        )
        for repo, branch in (
            (fixture.code, stale_atomic.code_work_branch),
            (fixture.code, stale_atomic.code_source_branch),
            (fixture.memory, stale_atomic.memory_work_branch),
            (fixture.memory, stale_atomic.memory_source_branch),
        ):
            git(repo, "branch", "-D", branch)
        fixture.contracts[MASTER_B] = fixture._contract(
            "master-b",
            "LEAF-B",
            git(fixture.code, "rev-parse", "super"),
            git(fixture.memory, "rev-parse", "super"),
        )
        own = prepare_completed_leaf(MASTER_B, LEAF_B)
        fixture.mutate(
            "acquire-blocker",
            blocker=MASTER_B,
            rationale="Keep the atomic recovery sequence isolated.",
        )
        own_result = reopen_task(own.contract_path)
        self.assertEqual((own_result.returncode, own_result.payload["state"]), (0, "reopened"))
        other_result = reopen_task(other.contract_path)
        self.assertEqual((other_result.returncode, other_result.payload["state"]), (2, "blocked"))
        self.assertIn("facts are frozen", str(other_result.payload["blockers"]))

    def test_malformed_durable_candidate_states_fail_before_projection(self) -> None:
        mutations = (
            {"state": "closeout-in-flight", "inFlightOwnerFingerprint": None},
            {"state": "certified", "closeoutCodeCommit": None},
            {"state": "declared", "inFlightOwnerFingerprint": "a" * 64},
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                fixture = QueueFixture(self.root / f"state-{index}")
                fixture.declare(MASTER_A)
                state_path, _pending_path = queue_store_paths(fixture.coord, SPRINT)
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["candidates"][LEAF_A.key].update(mutation)
                state_path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaisesRegex(
                    CloseoutQueueStoreError, "invalid closeout queue state"
                ):
                    fixture.status()

    def test_projection_names_only_operations_the_candidate_can_take(self) -> None:
        fixture = QueueFixture(self.root / "operations")
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        orchestrator_selected = fixture.status()["inFlight"][0]
        manager_selected = fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))[
            "inFlight"
        ][0]
        self.assertEqual(orchestrator_selected["legalNextOperations"], ["release-selection"])
        self.assertEqual(manager_selected["legalNextOperations"], ["worktree_closeout_apply"])
        contract = fixture.contracts[MASTER_A]
        start_or_observe_operation(
            CloseoutOperationInput(
                configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
                contractPath=contract.contract_path.as_posix(),
                codeCommitMessage="close candidate",
                approvalNote="approved",
            ),
            launcher=lambda *_: None,
        )
        closeout_store = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        )
        closeout_record = closeout_store.read()
        assert closeout_record is not None
        claim_queue_candidate_for_closeout(contract, closeout_record.operationKey)
        closeout_store.update(
            lambda current: current.model_copy(update={"irreversibleBoundaryEntered": True})
        )
        active_closeout = fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))[
            "inFlight"
        ][0]
        self.assertEqual(
            active_closeout["legalNextOperations"],
            ["worktree_closeout_apply"],
        )
        closed = fixture.close_contract(MASTER_A)
        certify_queue_candidate_closeout(closed, closeout_record.operationKey)
        closeout_store.update(
            lambda current: current.model_copy(
                update={"status": "running", "phase": "contract-finalization"}
            )
        )
        closeout_store.update(
            lambda current: current.model_copy(
                update={
                    "status": "completed",
                    "phase": "completed",
                    "finishedAt": "2026-08-15T00:01:00+00:00",
                }
            )
        )
        certified = fixture.status()["inFlight"][0]
        self.assertEqual(certified["legalNextOperations"], [])
        manager_certified = fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))[
            "inFlight"
        ][0]
        self.assertEqual(manager_certified["legalNextOperations"], ["worktree_integrate"])
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
        claim_queue_candidate_for_integration(closed, integration_record.operationKey)
        integrating = fixture.status(QueueActor(role="manager", task_document_ref=MASTER_A))[
            "inFlight"
        ][0]
        self.assertEqual(
            integrating["legalNextOperations"],
            ["worktree_integrate", "worktree_operation_cancel"],
        )

        blocked_fixture = QueueFixture(self.root / "blocked")
        blocked_fixture.declare(MASTER_A)
        (blocked_fixture.tasks / "sprint" / "grade.md").write_text("changed\n", encoding="utf-8")
        blocked = blocked_fixture.status()["blocked"][0]
        self.assertEqual(
            blocked["legalNextOperations"],
            ["set-grade", "withdraw"],
        )
        manager_blocked = blocked_fixture.status(
            QueueActor(role="manager", task_document_ref=MASTER_A)
        )["blocked"][0]
        self.assertEqual(manager_blocked["legalNextOperations"], ["set-admission", "withdraw"])

    def test_certification_rebinds_the_valid_post_refresh_curator_evidence(self) -> None:
        fixture = QueueFixture(self.root / "post-refresh")
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        closeout_key = "e" * 64
        contract = fixture.contracts[MASTER_A]
        claim_queue_candidate_for_closeout(contract, closeout_key)
        _write_curator_evidence(contract, _curator_report() + "\nPost-refresh proof.\n")
        closed = fixture.close_contract(MASTER_A)
        certify_queue_candidate_closeout(closed, closeout_key)
        certified = fixture.status()["inFlight"][0]
        self.assertEqual(certified["candidateState"], "certified")

        report = closed.worktree_group / "reports" / "curator-memory-quality.md"
        report.write_text(report.read_text(encoding="utf-8") + "stale\n", encoding="utf-8")
        self.assertIn(
            "memory-readiness-evidence-stale",
            fixture.status()["blocked"][0]["reasons"],
        )

    def test_wal_recovery_after_publish_is_idempotent_and_private_keys_never_persist(self) -> None:
        fixture = QueueFixture(self.root / "wal")
        _state_path, pending_path = queue_store_paths(fixture.coord, SPRINT)
        original_unlink = Path.unlink

        def preserve_pending(path: Path, *args, **kwargs) -> None:
            if path == pending_path:
                raise OSError("preserve pending transaction")
            original_unlink(path, *args, **kwargs)

        with mock.patch("pathlib.Path.unlink", new=preserve_pending):
            first = fixture.declare(MASTER_A, request_id="published-declare")
        self.assertTrue(pending_path.is_file())
        retried = fixture.declare(MASTER_A, request_id="published-declare")
        self.assertEqual(retried["revision"], first["revision"])
        self.assertFalse(pending_path.exists())

        fixture.mutate("select", candidate=LEAF_A)
        operation_key = "f" * 64
        with mock.patch("pathlib.Path.unlink", new=preserve_pending):
            claim_queue_candidate_for_closeout(fixture.contracts[MASTER_A], operation_key)
        state_path, pending_path = queue_store_paths(fixture.coord, SPRINT)
        self.assertNotIn(operation_key, state_path.read_text(encoding="utf-8"))
        self.assertNotIn(operation_key, pending_path.read_text(encoding="utf-8"))
        self.assertNotIn("inFlightOperationKey", state_path.read_text(encoding="utf-8"))
        fixture.status()
        self.assertFalse(pending_path.exists())

    def test_sprint_status_wal_recovers_before_after_and_reopen_crash_cuts(self) -> None:
        fixture = QueueFixture(self.root / "sprint-status-wal")
        fixture.declare(MASTER_A)
        fixture.mutate("withdraw", candidate=LEAF_A)
        store = CloseoutQueueStore(fixture.coord, SPRINT)
        state_path, pending_path = queue_store_paths(fixture.coord, SPRINT)

        with self.assertRaisesRegex(RuntimeError, "before task publication"):
            store.publish_sprint_update(
                lambda: (_ for _ in ()).throw(RuntimeError("before task publication")),
                completed=True,
                recorded_at="2026-08-15T00:00:00+00:00",
                validate_completion=lambda: None,
            )
        self.assertTrue(pending_path.is_file())
        fixture.status()
        self.assertFalse(pending_path.exists())
        self.assertFalse(
            CloseoutQueueState.model_validate_json(state_path.read_text(encoding="utf-8")).closed
        )

        for ref, master in fixture.master_docs.items():
            write_task_doc(
                fixture.tasks / Path(ref.path).parent,
                master.model_copy(
                    update={
                        "status": "Completed",
                        "subTasks": [
                            row.model_copy(update={"status": "Completed"})
                            for row in master.subTasks
                        ],
                    }
                ),
            )
        target = TaskDocTarget(repo_id="repo-a", task_name="sprint")
        original_publish = CloseoutQueueStore._publish
        failed = False

        def fail_after_task_publication(
            queue: CloseoutQueueStore, state: CloseoutQueueState
        ) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("after task publication")
            original_publish(queue, state)

        with (
            mock.patch.object(
                CloseoutQueueStore,
                "_publish",
                new=fail_after_task_publication,
            ),
            self.assertRaisesRegex(OSError, "after task publication"),
        ):
            task_doc_tool(
                fixture.cfg,
                target,
                operation="set_status",
                edit=TaskDocEdit(fields={"status": "Completed"}),
            )
        self.assertEqual(read_task_doc(fixture.tasks / "sprint" / "task.json").status, "Completed")
        self.assertTrue(pending_path.is_file())
        fixture.status()
        self.assertTrue(
            CloseoutQueueState.model_validate_json(state_path.read_text(encoding="utf-8")).closed
        )

        failed = False
        with (
            mock.patch.object(
                CloseoutQueueStore,
                "_publish",
                new=fail_after_task_publication,
            ),
            self.assertRaisesRegex(OSError, "after task publication"),
        ):
            task_doc_tool(
                fixture.cfg,
                target,
                operation="set_status",
                edit=TaskDocEdit(fields={"status": "inProgress"}),
            )
        self.assertEqual(read_task_doc(fixture.tasks / "sprint" / "task.json").status, "inProgress")
        self.assertTrue(pending_path.is_file())
        fixture.status()
        self.assertFalse(
            CloseoutQueueState.model_validate_json(state_path.read_text(encoding="utf-8")).closed
        )

    def test_plane_owned_actor_is_not_request_data_and_writer_census_is_enforced(self) -> None:
        fixture = QueueFixture(self.root / "actor")
        with self.assertRaisesRegex(ValidationError, "Extra inputs"):
            CloseoutQueueRequest.model_validate(
                {
                    "action": "status",
                    "sprint_task_document_ref": SPRINT.model_dump(mode="json"),
                    "actor": "orchestrator",
                }
            )
        request = CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT)
        with (
            mock.patch.object(
                public_queue,
                "resolve_ambient_seat",
                side_effect=public_queue.AmbientSeatError(
                    "ambient-seat-unavailable", "no hosted identity"
                ),
            ),
            self.assertRaisesRegex(public_queue.CloseoutQueueError, "no hosted identity"),
        ):
            public_queue.closeout_queue_tool(fixture.cfg, request)
        with (
            mock.patch.object(
                public_queue,
                "resolve_ambient_seat",
                return_value=SimpleNamespace(
                    binding_role="orchestrator",
                    binding_task_document_ref=None,
                ),
            ),
            self.assertRaisesRegex(public_queue.CloseoutQueueError, "canonical task document"),
        ):
            public_queue.closeout_queue_tool(fixture.cfg, request)
        with mock.patch.object(
            public_queue,
            "resolve_ambient_seat",
            return_value=SimpleNamespace(
                binding_role="orchestrator",
                binding_task_document_ref=SPRINT,
            ),
        ):
            response = public_queue.closeout_queue_tool(fixture.cfg, request)
        self.assertEqual(response["sprintTaskDocumentRef"], SPRINT.model_dump())
        with (
            mock.patch.object(
                public_queue,
                "resolve_ambient_seat",
                return_value=SimpleNamespace(
                    binding_role="worker",
                    binding_task_document_ref=LEAF_A,
                ),
            ),
            self.assertRaisesRegex(public_queue.CloseoutQueueError, "requires the sprint"),
        ):
            public_queue.closeout_queue_tool(fixture.cfg, request)

        fixture.declare(MASTER_A)
        with (
            mock.patch.object(
                public_queue,
                "resolve_ambient_seat",
                return_value=SimpleNamespace(
                    binding_role="manager",
                    binding_task_document_ref=MASTER_A,
                ),
            ),
            self.assertRaisesRegex(public_queue.CloseoutQueueError, "orchestrator authority"),
        ):
            public_queue.closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(
                    action="select",
                    sprint_task_document_ref=SPRINT,
                    request_id="manager-select",
                    expected_revision=fixture.status()["revision"],
                    candidate_task_document_ref=LEAF_A,
                ),
            )
        with (
            mock.patch.object(
                public_queue,
                "resolve_ambient_seat",
                return_value=SimpleNamespace(
                    binding_role="orchestrator",
                    binding_task_document_ref=SPRINT,
                ),
            ),
            self.assertRaisesRegex(public_queue.CloseoutQueueError, "owning master manager"),
        ):
            public_queue.closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(
                    action="declare",
                    sprint_task_document_ref=SPRINT,
                    request_id="orchestrator-declare",
                    expected_revision=fixture.status()["revision"],
                    contract_path=fixture.contracts[MASTER_A].contract_path.as_posix(),
                ),
            )

        self.assertEqual(QUEUE_OWNERSHIP.writers, ("mcp", "lifecycle-operation"))
        with (
            mock.patch(
                "agents_remember.controlplane.durable_store.declared_process_role",
                return_value="dashboard",
            ),
            self.assertRaises(CompactionOwnerError),
        ):
            fixture.status()


if __name__ == "__main__":
    unittest.main()
