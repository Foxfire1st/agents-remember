"""L13-R1: the atomic-sequential default mode and its master series lane.

A sprint without an executionGraph processes every commanded master
atomic-sequentially — regardless of declared nature — and at most one master is
in flight: the lane owner is the stored fact of a live (non-terminal) series
contract, released by the existing terminal series flow.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.modules import start_contract
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.start_contract import (
    MasterSeriesContractSpec,
    _existing_master_series_contract,
    ensure_master_series_contract,
)
from agents_remember.worktrees.scheduling_mode import (
    effective_execution_nature,
    resolve_scheduling_mode,
    sequential_lane_owner,
    series_lane_holders,
)
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_worktree_support import git, init_repo

REPO = "repo-a"
NOW = "2026-08-19T00:00:00+00:00"
SPRINT = TaskDocumentRef(repository=REPO, path="sprint/task.json")
MASTER_A = TaskDocumentRef(repository=REPO, path="master-a/task.json")
MASTER_B = TaskDocumentRef(repository=REPO, path="master-b/task.json")


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


class DefaultModeFixture:
    def __init__(self, root: Path, *, graph: bool = False) -> None:
        self.coord = root / "coordination"
        self.tasks = self.coord / "tasks" / REPO
        self.tasks.mkdir(parents=True)
        self.code = root / "code"
        init_repo(self.code, "main")
        git(self.code, "branch", "super", "main")
        self.topology = TaskDocumentTopology(self.coord)
        # Under an authored graph every commanded master needs an explicit nature;
        # the default-mode fixture leaves them nature-less (legacy shape, L13-R7).
        nature = "atomic" if graph else None
        write_task_doc(self.tasks / "master-a", _master(MASTER_A, nature))
        write_task_doc(self.tasks / "master-b", _master(MASTER_B, nature))
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
                    "orchestrates": ["master-a", "master-b"],
                    "integrationBranch": "super",
                    "executionGraph": (
                        {"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []}
                        if graph
                        else None
                    ),
                }
            ),
        )

    def spec(self, master: str) -> MasterSeriesContractSpec:
        return MasterSeriesContractSpec(
            coordination_root=self.coord,
            repo_name=REPO,
            code_repo=self.code,
            memory_root=None,
            task_root=self.tasks / master,
            task_name=master,
            parent_task_name="sprint",
            protected_branch="super",
        )


class SchedulingModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = DefaultModeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_graph_less_sprint_resolves_the_atomic_sequential_default(self) -> None:
        mode = resolve_scheduling_mode(self.fixture.topology, SPRINT)
        self.assertEqual(mode.mode, "atomic-sequential")
        self.assertEqual([master.ref for master in mode.masters], [MASTER_A, MASTER_B])
        self.assertTrue(any("atomic-sequential" in fact for fact in mode.facts))

    def test_graph_sprint_resolves_dag(self) -> None:
        fixture = DefaultModeFixture(Path(self.temp.name) / "dag", graph=True)
        self.assertEqual(
            resolve_scheduling_mode(fixture.topology, SPRINT).mode,
            "dag",
        )

    def test_non_sprint_is_refused(self) -> None:
        with self.assertRaises(TaskDocumentRefError) as raised:
            resolve_scheduling_mode(self.fixture.topology, MASTER_A)
        self.assertEqual(raised.exception.status, "task-execution-graph-sprint-required")

    def test_effective_execution_nature_matrix(self) -> None:
        topology = self.fixture.topology
        master = topology.resolve(MASTER_A).document
        sprintless = None
        graphless_sprint = topology.resolve(SPRINT).document
        # Nature-less standalone master: atomic by default (L13-R5e).
        self.assertEqual(effective_execution_nature(master, sprintless), "atomic")
        # Under the default mode every declared nature flattens to atomic (L13-R1).
        self.assertEqual(effective_execution_nature(master, graphless_sprint), "atomic")
        dag_fixture = DefaultModeFixture(Path(self.temp.name) / "dag", graph=True)
        dag_sprint = dag_fixture.topology.resolve(SPRINT).document
        # A nature-less master under an authored graph stays invalid.
        with self.assertRaises(TaskDocumentRefError):
            effective_execution_nature(master, dag_sprint)
        organizational = _master(MASTER_A, "organizational")
        # An explicit organizational standalone master keeps its nature (and its
        # leaf-start refusal), while under the default mode it runs atomically.
        self.assertEqual(effective_execution_nature(organizational, None), "organizational")
        self.assertEqual(effective_execution_nature(organizational, graphless_sprint), "atomic")

    def test_lane_owner_is_the_live_series_contract_stored_fact(self) -> None:
        fixture = self.fixture
        mode = resolve_scheduling_mode(fixture.topology, SPRINT)
        self.assertIsNone(sequential_lane_owner(fixture.topology, mode))

        created = ensure_master_series_contract(fixture.spec("master-a"))
        self.assertNotIsInstance(created, WorktreeCommandResult)
        owner = sequential_lane_owner(fixture.topology, mode)
        assert owner is not None
        self.assertEqual(owner.ref, MASTER_A)
        self.assertEqual(
            [master.ref for master in series_lane_holders(mode)],
            [MASTER_A],
        )
        # The existing terminal series flow releases the lane.
        contract = load_contract(series_contract_path(fixture.tasks / "master-a"))
        write_contract(contract.contract_path, replace(contract, cleanup="completed"))
        self.assertIsNone(sequential_lane_owner(fixture.topology, mode))

    def test_lane_owner_is_none_under_the_dag_mode(self) -> None:
        fixture = DefaultModeFixture(Path(self.temp.name) / "dag", graph=True)
        created = ensure_master_series_contract(fixture.spec("master-a"))
        self.assertNotIsInstance(created, WorktreeCommandResult)
        mode = resolve_scheduling_mode(fixture.topology, SPRINT)
        self.assertIsNone(sequential_lane_owner(fixture.topology, mode))


class SequentialLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = DefaultModeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_second_master_series_bootstrap_is_blocked_not_stranded(self) -> None:
        created = ensure_master_series_contract(self.fixture.spec("master-a"))
        self.assertNotIsInstance(created, WorktreeCommandResult)

        blocked = ensure_master_series_contract(self.fixture.spec("master-b"))
        assert isinstance(blocked, WorktreeCommandResult)
        self.assertEqual(blocked.returncode, 2)
        self.assertEqual(blocked.payload["state"], "sequential-lane-owned")
        self.assertEqual(blocked.payload["laneOwner"], MASTER_A.key)
        self.assertEqual(
            blocked.payload["laneOwnerContractPath"],
            series_contract_path(self.fixture.tasks / "master-a").as_posix(),
        )
        self.assertTrue(blocked.payload["legalNextOperations"])

        # The lane owner itself adopts its existing contract, even in dry run.
        adopted = ensure_master_series_contract(self.fixture.spec("master-a"))
        self.assertNotIsInstance(adopted, WorktreeCommandResult)
        preview_blocked = ensure_master_series_contract(self.fixture.spec("master-b"), dry_run=True)
        assert isinstance(preview_blocked, WorktreeCommandResult)
        self.assertEqual(preview_blocked.payload["state"], "sequential-lane-owned")

        # After the owner lands and its series goes terminal, the next master starts.
        contract = load_contract(series_contract_path(self.fixture.tasks / "master-a"))
        write_contract(contract.contract_path, replace(contract, cleanup="completed"))
        started = ensure_master_series_contract(self.fixture.spec("master-b"))
        self.assertNotIsInstance(started, WorktreeCommandResult)

    def test_terminal_stale_series_artifact_is_replaced_not_refused(self) -> None:
        # L13-R5b/R7: a terminal artifact left by an older lifecycle is swept by the
        # fresh bootstrap instead of dead-ending it.
        created = ensure_master_series_contract(self.fixture.spec("master-a"))
        assert not isinstance(created, WorktreeCommandResult)
        stale = replace(created, cleanup="abandoned")
        write_contract(stale.contract_path, stale)
        self.assertIsNone(_existing_master_series_contract(self.fixture.spec("master-a")))

    def test_nature_less_commanded_master_bootstraps_without_migration(
        self,
    ) -> None:  # L13-R7: legacy masters carry no executionNature; the default mode flows.
        created = ensure_master_series_contract(self.fixture.spec("master-a"))
        assert not isinstance(created, WorktreeCommandResult)
        self.assertEqual(created.kind, "series")
        self.assertEqual(created.code_work_branch, "ar/master-a")

    def test_lane_resolution_failure_fails_closed(self) -> None:
        # L13 review: a scheduling-mode resolution failure in the TOCTOU window
        # propagates as the typed refusal instead of silently skipping the lane.
        with (
            mock.patch.object(
                start_contract,
                "resolve_scheduling_mode",
                side_effect=TaskDocumentRefError("task-document-not-found", "sprint moved"),
            ),
            self.assertRaises(TaskDocumentRefError),
        ):
            ensure_master_series_contract(self.fixture.spec("master-a"))


if __name__ == "__main__":
    unittest.main()
