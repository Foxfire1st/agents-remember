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

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.closeout_queue_lifecycle import (
    require_atomic_series_terminal_release,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.start_contract import (
    _parent_series_contract,
)
from agents_remember.worktrees.scheduling_mode import stale_series_artifact_fact
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
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


if __name__ == "__main__":
    unittest.main()
