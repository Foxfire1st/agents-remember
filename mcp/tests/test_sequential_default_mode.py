"""L13-R1: atomic-sequential mode with independent source-pair activation.

A sprint without an executionGraph processes every commanded master
atomic-sequentially regardless of declared nature. Multiple non-terminal series
contracts may preserve work; one source-pair activation snapshot selects which
master may expose implementation work.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.application.structural.agent_tools import (
    _implementation_series_admission_refusal,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.activation.atomic_series_activation import (
    activation_waiting_reason,
    observe_atomic_series,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.startup import start_contract
from agents_remember.worktrees.modules.startup.start_contract import (
    MasterSeriesContractSpec,
    _existing_master_series_contract,
    ensure_master_series_contract,
)
from agents_remember.worktrees.scheduling_mode import (
    effective_execution_nature,
    resolve_scheduling_mode,
)
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
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

    def test_activation_is_independent_of_scheduling_mode_shape(self) -> None:
        fixture = DefaultModeFixture(Path(self.temp.name) / "dag", graph=True)
        created_a = ensure_master_series_contract(fixture.spec("master-a"))
        created_b = ensure_master_series_contract(fixture.spec("master-b"))
        assert isinstance(created_a, WorktreeContract)
        assert isinstance(created_b, WorktreeContract)
        mode = resolve_scheduling_mode(fixture.topology, SPRINT)
        self.assertEqual(mode.mode, "dag")
        observed = observe_atomic_series(created_a)
        self.assertEqual(observed.selected_master, MASTER_B)
        self.assertEqual(
            activation_waiting_reason(observed, MASTER_A),
            f"atomic-series-paused-by: {MASTER_B.key}",
        )


class AtomicSeriesSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = DefaultModeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_second_master_selects_and_logically_pauses_first_without_retiring_it(self) -> None:
        created_a = ensure_master_series_contract(self.fixture.spec("master-a"))
        created_b = ensure_master_series_contract(self.fixture.spec("master-b"))
        assert isinstance(created_a, WorktreeContract)
        assert isinstance(created_b, WorktreeContract)

        selected_b = observe_atomic_series(created_a)
        self.assertEqual(selected_b.state, "active")
        self.assertEqual(selected_b.selected_master, MASTER_B)
        self.assertEqual(
            activation_waiting_reason(selected_b, MASTER_A),
            f"atomic-series-paused-by: {MASTER_B.key}",
        )
        self.assertTrue(created_a.contract_path.is_file())
        self.assertTrue(created_b.contract_path.is_file())

        # Previewing another selection is byte-for-byte read-only and cannot switch it.
        before = selected_b.activation_path.read_bytes()
        preview = ensure_master_series_contract(self.fixture.spec("master-a"), dry_run=True)
        self.assertIsInstance(preview, WorktreeContract)
        self.assertEqual(selected_b.activation_path.read_bytes(), before)

        selected_a = ensure_master_series_contract(self.fixture.spec("master-a"))
        assert isinstance(selected_a, WorktreeContract)
        self.assertEqual(observe_atomic_series(selected_a).selected_master, MASTER_A)

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

    def test_bootstrap_failure_before_contract_publication_keeps_former_selection(self) -> None:
        selected_a = ensure_master_series_contract(self.fixture.spec("master-a"))
        assert isinstance(selected_a, WorktreeContract)
        with (
            mock.patch.object(
                start_contract,
                "_publish_master_series_contract",
                side_effect=RuntimeError("bootstrap failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "bootstrap failed"),
        ):
            ensure_master_series_contract(self.fixture.spec("master-b"))

        observed = observe_atomic_series(selected_a)
        self.assertEqual(observed.state, "active")
        self.assertEqual(observed.selected_master, MASTER_A)

    def test_sync_refusal_after_contract_publication_keeps_new_selection_reconciling(self) -> None:
        selected_a = ensure_master_series_contract(self.fixture.spec("master-a"))
        assert isinstance(selected_a, WorktreeContract)
        blocked = WorktreeCommandResult(
            2,
            {"state": "sync-resolution-required", "summary": "resolve conflict"},
        )
        with mock.patch(
            "agents_remember.worktrees.activation.atomic_series_activation_transaction."
            "sync_contract_under_authority",
            return_value=blocked,
        ):
            result = ensure_master_series_contract(self.fixture.spec("master-b"))

        assert isinstance(result, WorktreeCommandResult)
        observed = observe_atomic_series(selected_a)
        self.assertEqual(observed.state, "reconciling")
        self.assertEqual(observed.selected_master, MASTER_B)

    def test_standalone_master_bootstrap_selects_its_default_branch_pair(self) -> None:
        master_ref = TaskDocumentRef(repository=REPO, path="solo/task.json")
        write_task_doc(self.fixture.tasks / "solo", _master(master_ref, None))
        created = ensure_master_series_contract(
            MasterSeriesContractSpec(
                coordination_root=self.fixture.coord,
                repo_name=REPO,
                code_repo=self.fixture.code,
                memory_root=None,
                task_root=self.fixture.tasks / "solo",
                task_name="solo",
                parent_task_name="",
                protected_branch="main",
            )
        )
        self.assertNotIsInstance(created, WorktreeCommandResult)
        assert isinstance(created, WorktreeContract)
        self.assertEqual(created.code_work_branch, "ar/solo")

    def test_parent_series_contract_selects_new_master_before_leaf_contract_build(
        self,
    ) -> None:
        ensure_master_series_contract(self.fixture.spec("master-a"))
        write_task_doc(
            self.fixture.tasks / "master-b",
            _master(MASTER_B, None).model_copy(
                update={"subTasks": [SubTaskRef(number="LEAF-1", name="leaf", file="leaf-1.md")]}
            ),
        )
        write_task_doc(
            self.fixture.tasks / "master-b",
            TaskDocument.model_validate(
                {
                    "id": "LEAF-1",
                    "slug": "leaf-1",
                    "title": "leaf",
                    "kind": "subTask",
                    "status": "planning",
                    "repo": REPO,
                    "createdAt": NOW,
                    "steps": [{"id": "S1", "title": "work", "status": "done"}],
                }
            ),
        )
        context = SimpleNamespace(
            coordination_root=self.fixture.coord,
            code_repository_name=REPO,
            code_repository_root=self.fixture.code,
            memory_mode="disabled",
        )
        args = WorktreeArgs(task_name="master-b", worktree_name="leaf-1")
        result = start_contract._parent_series_contract(context, args, "disabled")
        assert isinstance(result, WorktreeContract)
        self.assertEqual(observe_atomic_series(result).selected_master, MASTER_B)
        built = start_contract._build_start_contract(
            context,
            WorktreeArgs(task_name="master-b", worktree_name="leaf-1", dry_run=True),
        )
        assert isinstance(built, WorktreeContract)

    def test_manager_dispatch_selects_the_requested_master(self) -> None:
        ensure_master_series_contract(self.fixture.spec("master-a"))
        topology = TaskDocumentTopology(self.fixture.coord)
        resolved = topology.resolve(MASTER_B)
        config = SimpleNamespace(
            coordination_root=self.fixture.coord,
            repositories={
                REPO: SimpleNamespace(repo_id=REPO, path=self.fixture.code, memory_root=None)
            },
        )
        outcome = _implementation_series_admission_refusal(
            cast(McpRuntimeConfig, config),
            resolved,
            "manager",
        )
        self.assertIsNone(outcome)
        selected = load_contract(series_contract_path(self.fixture.tasks / "master-b"))
        self.assertEqual(observe_atomic_series(selected).selected_master, MASTER_B)
