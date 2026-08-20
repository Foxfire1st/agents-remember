"""L13-R5/R7: legacy tolerance — nature-less masters flow through the default mode.

Forcing proofs that the old dead-ends are gone: a nature-less standalone master
resolves altitude ``master`` with no parent edge (no migration), its series
contract retires through the normal terminal authority (abandon/cleanup stay
reachable), and a terminal stale series artifact under an organizational master
is ignored and reported instead of refusing the start. An explicit
organizational standalone master keeps its refusal.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.integration import integration_branch_authority as authority
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.start_contract import (
    _commanding_sprint_document,
    _declared_integration_source_branch,
    _parent_series_contract,
)
from agents_remember.worktrees.modules.start_result import started_result
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    require_atomic_series_terminal_release,
)
from agents_remember.worktrees.scheduling_mode import (
    resolve_scheduling_mode,
    sequential_lane_owner,
    series_lane_holders,
    stale_series_artifact_fact,
)
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
    write_contract,
)
from test_worktree_support import init_repo

REPO = "repo-a"
NOW = "2026-08-19T00:00:00+00:00"


def _master(ref: TaskDocumentRef, nature: str | None) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": ref.path.split("/")[0].upper(),
            "slug": ref.path.split("/")[0],
            "title": ref.path.split("/")[0],
            "kind": "master",
            "status": "inProgress",
            "repo": REPO,
            "createdAt": NOW,
            "executionNature": nature,
            "subTasks": [],
        }
    )


class LegacyNatureToleranceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.coord = self.root / "coordination"
        self.tasks = self.coord / "tasks" / REPO
        self.tasks.mkdir(parents=True)
        self.code = self.root / "code"
        self.code_base = init_repo(self.code, "main")
        self.topology = TaskDocumentTopology(self.coord)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _series_contract(self, task_root: Path, task_name: str, *, cleanup: str = "pending"):
        contract = default_series_contract(
            ContractTask(
                name=task_name,
                repo_name=REPO,
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            code=RepoBranchPlan(
                repo_path=self.code,
                source_branch="main",
                work_branch=f"ar/{task_name}",
                base_commit=self.code_base,
            ),
            memory=None,
            task_root=task_root,
        )
        if cleanup != "pending":
            contract = replace(contract, cleanup=cleanup)
        write_contract(contract.contract_path, contract)
        return contract

    def test_nature_less_standalone_master_resolves_master_altitude(self) -> None:
        master_ref = TaskDocumentRef(repository=REPO, path="legacy/task.json")
        write_task_doc(self.tasks / "legacy", _master(master_ref, None))
        # The uncommanded-implies-atomic default: altitude resolves, no parent edge.
        self.assertEqual(self.topology.altitude(master_ref), "master")
        self.assertIsNone(self.topology.parent(master_ref))

    def test_explicit_organizational_standalone_still_refuses(self) -> None:
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        with self.assertRaises(TaskDocumentRefError) as raised:
            self.topology.altitude(master_ref)
        self.assertEqual(raised.exception.status, "task-document-parent-missing")

    def test_nature_less_master_series_retires_through_terminal_authority(self) -> None:
        # The forcing proof for the old abandon/cleanup dead-end (L13-R5a): a
        # nature-less legacy master's series passes the terminal release gate.
        master_ref = TaskDocumentRef(repository=REPO, path="legacy/task.json")
        write_task_doc(self.tasks / "legacy", _master(master_ref, None))
        contract = self._series_contract(self.tasks / "legacy", "legacy")
        require_atomic_series_terminal_release(contract)

    def test_terminal_stale_series_artifact_is_ignored_and_reported(self) -> None:
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        stale = self._series_contract(self.tasks / "org", "org", cleanup="completed")
        fact = stale_series_artifact_fact(self.tasks / "org")
        self.assertEqual(
            fact,
            {
                "fact": "staleSeriesArtifact",
                "contractPath": stale.contract_path.as_posix(),
                "cleanup": "completed",
            },
        )
        self.assertIsNone(stale_series_artifact_fact(self.tasks / "legacy"))

    def test_organizational_master_with_live_series_artifact_reports_no_fact(self) -> None:
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        self._series_contract(self.tasks / "org", "org")
        self.assertIsNone(stale_series_artifact_fact(self.tasks / "org"))

    def test_organizational_master_start_ignores_terminal_artifact(self) -> None:
        # L13-R5b: the leaf start's stale series-contract refusal applies only to
        # non-terminal artifacts; a terminal artifact is ignored.
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": REPO,
                    "createdAt": NOW,
                    "orchestrates": ["org"],
                    "integrationBranch": "main",
                    "executionGraph": {"nodes": [master_ref.model_dump()], "edges": []},
                }
            ),
        )
        self._series_contract(self.tasks / "org", "org", cleanup="completed")
        context = SimpleNamespace(
            coordination_root=self.coord,
            code_repository_name=REPO,
            code_repository_root=self.code,
        )
        args = WorktreeArgs(
            task_name="org",
            worktree_name="leaf-1",
            dry_run=True,
        )
        result = _parent_series_contract(context, args, "disabled")
        # Organizational under a graph: no series is adopted, and the terminal
        # artifact no longer refuses the start.
        self.assertIsNone(result)

    def _graph_sprint(self, master_ref: TaskDocumentRef) -> None:
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": REPO,
                    "createdAt": NOW,
                    "orchestrates": [master_ref.path.split("/")[0]],
                    "integrationBranch": "main",
                    "executionGraph": {"nodes": [master_ref.model_dump()], "edges": []},
                }
            ),
        )

    def _context(self) -> SimpleNamespace:
        return SimpleNamespace(
            coordination_root=self.coord,
            code_repository_name=REPO,
            code_repository_root=self.code,
        )

    def test_organizational_master_with_corrupt_series_artifact_still_refuses(self) -> None:
        # A non-terminal artifact refuses; an unreadable artifact is not terminal
        # evidence, so it refuses too (fail closed).
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        self._graph_sprint(master_ref)
        series_contract_path(self.tasks / "org").write_text("not a contract\n", encoding="utf-8")
        args = WorktreeArgs(task_name="org", worktree_name="leaf-1", dry_run=True)
        with self.assertRaisesRegex(RuntimeError, "must not carry"):
            _parent_series_contract(self._context(), args, "disabled")

    def test_commanding_sprint_document_absent_and_error_paths(self) -> None:
        # Nature-less standalone master: no parent edge resolves.
        master_ref = TaskDocumentRef(repository=REPO, path="legacy/task.json")
        write_task_doc(self.tasks / "legacy", _master(master_ref, None))
        self.assertIsNone(_commanding_sprint_document(self._context(), self.tasks / "legacy"))
        # Explicit organizational standalone: the parent lookup refuses, so no
        # commanding sprint document resolves.
        org_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(org_ref, "organizational"))
        self.assertIsNone(_commanding_sprint_document(self._context(), self.tasks / "org"))

    def test_stale_series_fact_survives_corrupt_documents(self) -> None:
        # Corrupt master document: no fact.
        master_root = self.tasks / "org"
        master_root.mkdir()
        (master_root / "task.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(stale_series_artifact_fact(master_root))
        # Organizational master with an unreadable series artifact: no fact.
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(master_root, _master(master_ref, "organizational"))
        series_contract_path(master_root).write_text("not a contract\n", encoding="utf-8")
        self.assertIsNone(stale_series_artifact_fact(master_root))

    def test_lane_holders_skip_unreadable_and_vanished_masters(self) -> None:
        master_ref = TaskDocumentRef(repository=REPO, path="legacy/task.json")
        write_task_doc(self.tasks / "legacy", _master(master_ref, None))
        sprint_ref = TaskDocumentRef(repository=REPO, path="sprint/task.json")
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": REPO,
                    "createdAt": NOW,
                    "orchestrates": ["legacy"],
                    "integrationBranch": "main",
                }
            ),
        )
        mode = resolve_scheduling_mode(self.topology, sprint_ref)
        # An unreadable series artifact is skipped, not mistaken for lane ownership.
        series_contract_path(self.tasks / "legacy").write_text("garbage\n", encoding="utf-8")
        self.assertEqual(series_lane_holders(mode), ())
        self.assertIsNone(sequential_lane_owner(self.topology, mode))
        # A live series whose master document vanished cannot own the lane.
        contract = self._series_contract(self.tasks / "legacy", "legacy")
        (self.tasks / "legacy" / "task.json").unlink()
        with mock.patch.object(
            TaskDocumentTopology,
            "resolve",
            side_effect=TaskDocumentRefError("task-document-not-found", "gone"),
        ):
            self.assertIsNone(sequential_lane_owner(self.topology, mode))
        self.assertTrue(contract.contract_path.is_file())

    def test_start_result_reports_the_stale_series_artifact_fact(self) -> None:
        # L13-R5b: a start under an organizational master with a terminal stale
        # series artifact reports the fact in the result payload.
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        self._series_contract(self.tasks / "org", "org", cleanup="completed")
        contract = default_contract(
            ContractTask(
                name="org",
                repo_name=REPO,
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            leaf=LeafIdentity(worktree_name="leaf-1", leaf_id="LEAF-1"),
            code=RepoBranchPlan(
                repo_path=self.code,
                source_branch="main",
                work_branch="ar/leaf-1",
                base_commit=self.code_base,
            ),
            memory=None,
        )
        result = started_result(contract, WorktreeArgs(dry_run=True), "created", {}, {})
        fact = result.payload["staleSeriesArtifact"]
        assert isinstance(fact, dict)
        self.assertEqual(fact["fact"], "staleSeriesArtifact")

    def test_nature_less_master_series_retires_through_terminal_authority_under_default_mode(
        self,
    ) -> None:
        # The graph-less sprint branch of the terminal publication: no queue graph
        # exists, so the series retires through the repository authority alone.
        master_ref = TaskDocumentRef(repository=REPO, path="legacy/task.json")
        write_task_doc(self.tasks / "legacy", _master(master_ref, None))
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": REPO,
                    "createdAt": NOW,
                    "orchestrates": ["legacy"],
                    "integrationBranch": "main",
                }
            ),
        )
        contract = self._series_contract(self.tasks / "legacy", "legacy")
        require_atomic_series_terminal_release(contract)

    def test_master_authority_skips_graph_validation_under_the_default_mode(self) -> None:
        # A graph-less sprint: _master_authority reads the super branch and the
        # effective atomic nature without a graph validation pass.
        master_ref = TaskDocumentRef(repository=REPO, path="legacy/task.json")
        write_task_doc(self.tasks / "legacy", _master(master_ref, None))
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": REPO,
                    "createdAt": NOW,
                    "orchestrates": ["legacy"],
                    "integrationBranch": "main",
                }
            ),
        )
        scope = authority._BranchScope(self.coord, REPO, self.tasks / "legacy", ())
        resolved = authority._master_authority(scope)
        self.assertEqual(resolved.sprint_branch, "main")
        self.assertEqual(resolved.execution_nature, "atomic")

    def test_publication_authority_skips_a_standalone_organizational_master(self) -> None:
        # The non-atomic arm of the standalone census: an explicit organizational
        # standalone master claims no atomic integration surface.
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        masters = authority._publication_master_authority(self.topology, REPO, {})
        self.assertNotIn(master_ref, masters)

    def test_declared_source_branch_refuses_a_standalone_organizational_master(self) -> None:
        # L13-R5e: only an effective atomic master may exist outside a sprint graph;
        # the explicit organizational standalone refuses at the topology parent edge.
        master_ref = TaskDocumentRef(repository=REPO, path="org/task.json")
        write_task_doc(self.tasks / "org", _master(master_ref, "organizational"))
        with self.assertRaisesRegex(RuntimeError, "cannot resolve one parent"):
            _declared_integration_source_branch(self._context(), self.tasks / "org")
