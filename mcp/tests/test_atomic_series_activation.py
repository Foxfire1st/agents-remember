"""Forcing tests for source-pair-scoped atomic-series activation authority."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
    activation_path,
    activation_waiting_reason,
    atomic_series_source_pair,
    observe_atomic_series,
    publish_atomic_series_selection,
    require_atomic_series_cancellation_owner,
)
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_atomic_series_selection,
)
from agents_remember.worktrees.activation.atomic_series_activation_terminal import (
    with_terminal_atomic_series_release,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.startup.start_contract import (
    MasterSeriesContractSpec,
    ensure_master_series_contract,
)
from agents_remember.worktrees.queue.closeout_projection_activation import (
    project_series_activation,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_worktree_support import git, init_repo

REPO = "repo-a"
NOW = "2026-08-26T00:00:00+00:00"
MASTER_A = TaskDocumentRef(repository=REPO, path="master-a/task.json")
MASTER_B = TaskDocumentRef(repository=REPO, path="master-b/task.json")


def _master(ref: TaskDocumentRef) -> TaskDocument:
    name = Path(ref.path).parent.name
    return TaskDocument.model_validate(
        {
            "id": name.upper(),
            "slug": name,
            "title": name,
            "kind": "master",
            "status": "inProgress",
            "repo": REPO,
            "createdAt": NOW,
            "subTasks": [],
        }
    )


class ActivationFixture:
    def __init__(self, root: Path) -> None:
        self.coord = root / "coordination"
        self.tasks = self.coord / "tasks" / REPO
        self.code = root / "code"
        init_repo(self.code, "main")
        git(self.code, "branch", "super", "main")
        for ref in (MASTER_A, MASTER_B):
            write_task_doc(self.tasks / Path(ref.path).parent, _master(ref))
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
                }
            ),
        )

    def contract(self, name: str) -> WorktreeContract:
        result = ensure_master_series_contract(
            MasterSeriesContractSpec(
                coordination_root=self.coord,
                repo_name=REPO,
                code_repo=self.code,
                memory_root=None,
                task_root=self.tasks / name,
                task_name=name,
                parent_task_name="sprint",
                protected_branch="super",
            )
        )
        assert isinstance(result, WorktreeContract)
        release_atomic_series_selection(result, timestamp=NOW)
        return result


class AtomicSeriesActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = ActivationFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_absence_is_vacant_even_when_multiple_live_contracts_exist(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")

        observed_a = observe_atomic_series(contract_a)
        observed_b = observe_atomic_series(contract_b)

        self.assertEqual(observed_a.state, "vacant")
        self.assertEqual(observed_b.state, "vacant")
        self.assertEqual(
            activation_waiting_reason(observed_a, MASTER_A), "atomic-series-not-selected"
        )
        self.assertEqual(
            activation_waiting_reason(observed_b, MASTER_B), "atomic-series-not-selected"
        )

    def test_selection_switches_logical_active_owner_without_retiring_work(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")

        reconciling_a = publish_atomic_series_selection(contract_a, "reconciling", timestamp=NOW)
        active_a = publish_atomic_series_selection(contract_a, "active", timestamp=NOW)
        selected_b = publish_atomic_series_selection(contract_b, "reconciling", timestamp=NOW)

        self.assertEqual(reconciling_a.state, "reconciling")
        self.assertEqual(active_a.state, "active")
        self.assertEqual(selected_b.selected_master, MASTER_B)
        self.assertEqual(
            activation_waiting_reason(selected_b, MASTER_A),
            f"atomic-series-paused-by: {MASTER_B.key}",
        )
        self.assertEqual(
            activation_waiting_reason(selected_b, MASTER_B), "atomic-series-reconciling"
        )
        self.assertTrue(contract_a.contract_path.is_file())
        self.assertTrue(contract_b.contract_path.is_file())

    def test_same_state_selection_is_idempotent(self) -> None:
        contract = self.fixture.contract("master-a")
        vacant = observe_atomic_series(contract)
        assert vacant.record is not None
        first = publish_atomic_series_selection(contract, "reconciling", timestamp=NOW)
        second = publish_atomic_series_selection(contract, "reconciling", timestamp="later")

        self.assertEqual(first.record, second.record)
        self.assertEqual(
            second.record.revision if second.record is not None else None,
            vacant.record.revision + 1,
        )

    def test_source_pairs_are_isolated(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")
        git(self.fixture.code, "branch", "other-super", "main")
        isolated_b = replace(contract_b, code_source_branch="other-super")
        write_contract(isolated_b.contract_path, isolated_b)

        selected_a = publish_atomic_series_selection(contract_a, "active", timestamp=NOW)
        observed_b = observe_atomic_series(isolated_b)

        self.assertEqual(selected_a.state, "active")
        self.assertEqual(observed_b.state, "vacant")
        self.assertNotEqual(selected_a.activation_path, observed_b.activation_path)

    def test_terminal_selected_contract_makes_the_pair_vacant_without_resurrection(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")
        publish_atomic_series_selection(contract_a, "active", timestamp=NOW)
        publish_atomic_series_selection(contract_b, "active", timestamp=NOW)
        terminal_b = replace(contract_b, integration_status="completed")
        write_contract(terminal_b.contract_path, terminal_b)

        observed = observe_atomic_series(contract_a)

        self.assertEqual(observed.state, "vacant")
        self.assertIsNone(observed.selected_master)
        self.assertEqual(observed.last_selected_master, MASTER_B)
        self.assertEqual(
            activation_waiting_reason(observed, MASTER_A), "atomic-series-not-selected"
        )

    def test_strict_observation_refuses_malformed_bytes_and_selection_archives_them(self) -> None:
        contract = self.fixture.contract("master-a")
        pair = atomic_series_source_pair(contract)
        path = activation_path(self.fixture.coord, pair)
        path.parent.mkdir(parents=True, exist_ok=True)
        malformed = b'{"schemaVersion":"1.0","selectedMaster":'
        path.write_bytes(malformed)

        before = observe_atomic_series(contract)
        repaired = publish_atomic_series_selection(contract, "reconciling", timestamp=NOW)

        self.assertEqual(before.state, "unreadable")
        self.assertEqual(repaired.state, "reconciling")
        archive = path.parent / "archive" / repaired.source_pair_fingerprint
        snapshots = list(archive.glob("*.snapshot"))
        evidence = list(archive.glob("*.json"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].read_bytes(), malformed)
        self.assertEqual(len(evidence), 1)

    def test_nonregular_selection_is_quarantined_without_following_it(self) -> None:
        contract = self.fixture.contract("master-a")
        pair = atomic_series_source_pair(contract)
        path = activation_path(self.fixture.coord, pair)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = Path(self.temporary.name) / "outside-authority.json"
        target.write_text("outside-must-not-change", encoding="utf-8")
        path.unlink()
        path.symlink_to(target)

        repaired = publish_atomic_series_selection(contract, "reconciling", timestamp=NOW)

        self.assertEqual(repaired.state, "reconciling")
        self.assertEqual(target.read_text(encoding="utf-8"), "outside-must-not-change")
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        archive = path.parent / "archive" / repaired.source_pair_fingerprint
        entries = list(archive.glob("*.entry"))
        evidence_paths = list(archive.glob("*.json"))
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].is_symlink())
        self.assertEqual(entries[0].readlink(), target)
        self.assertEqual(len(evidence_paths), 1)
        evidence = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
        self.assertEqual(evidence["archiveKind"], "opaque-entry")

    def test_cleanup_terminal_is_effective_vacancy(self) -> None:
        contract = self.fixture.contract("master-a")
        publish_atomic_series_selection(contract, "active", timestamp=NOW)
        terminal = replace(load_contract(contract.contract_path), cleanup="abandoned")
        write_contract(terminal.contract_path, terminal)

        self.assertEqual(observe_atomic_series(terminal).state, "vacant")

    def test_explicit_release_is_durable_vacancy_and_does_not_load_retired_contract(self) -> None:
        contract = self.fixture.contract("master-a")
        pair = atomic_series_source_pair(contract)
        publish_atomic_series_selection(contract, "reconciling", timestamp=NOW)
        released = release_atomic_series_selection(contract, timestamp=NOW)
        replay_owner = require_atomic_series_cancellation_owner(contract)
        contract.contract_path.unlink()

        observed = observe_atomic_series(pair, coordination_root=self.fixture.coord)

        self.assertEqual(released.state, "vacant")
        self.assertEqual(replay_owner.state, "vacant")
        self.assertEqual(observed.state, "vacant")
        self.assertEqual(observed.last_selected_master, MASTER_A)

    def test_release_refuses_to_clear_another_selected_master(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")
        publish_atomic_series_selection(contract_a, "reconciling", timestamp=NOW)

        with self.assertRaises(AtomicSeriesActivationError) as raised:
            release_atomic_series_selection(contract_b, timestamp=NOW)

        self.assertEqual(
            raised.exception.status,
            "atomic-series-activation-selected-contract-mismatch",
        )

    def test_terminal_cleanup_publishes_vacancy_before_contract_deletion(self) -> None:
        contract = self.fixture.contract("master-a")
        pair = atomic_series_source_pair(contract)
        publish_atomic_series_selection(contract, "active", timestamp=NOW)
        terminal = replace(contract, cleanup="completed")
        write_contract(terminal.contract_path, terminal)

        result = with_terminal_atomic_series_release(
            terminal,
            WorktreeCommandResult(
                0,
                {"state": "cleanup-completed", "summary": "Cleanup completed."},
            ),
            dry_run=False,
        )
        terminal.contract_path.unlink()
        observed = observe_atomic_series(pair, coordination_root=self.fixture.coord)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.payload["atomicSeriesActivationRelease"],
            {"state": "vacant"},
        )
        self.assertEqual(observed.state, "vacant")
        self.assertEqual(observed.last_selected_master, MASTER_A)

    def test_terminal_cleanup_of_paused_series_preserves_newer_selection(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")
        publish_atomic_series_selection(contract_b, "active", timestamp=NOW)
        terminal_a = replace(contract_a, cleanup="abandoned")
        write_contract(terminal_a.contract_path, terminal_a)

        result = with_terminal_atomic_series_release(
            terminal_a,
            WorktreeCommandResult(0, {"state": "abandoned", "summary": "Abandoned."}),
            dry_run=False,
        )

        self.assertEqual(
            result.payload["atomicSeriesActivationRelease"],
            {"state": "different-selection-preserved"},
        )
        self.assertEqual(observe_atomic_series(contract_b).selected_master, MASTER_B)

    def test_malformed_selection_does_not_block_terminal_cleanup(self) -> None:
        contract = self.fixture.contract("master-a")
        path = activation_path(self.fixture.coord, atomic_series_source_pair(contract))
        malformed = b"{malformed"
        path.write_bytes(malformed)
        terminal = replace(contract, cleanup="abandoned")
        write_contract(terminal.contract_path, terminal)

        result = with_terminal_atomic_series_release(
            terminal,
            WorktreeCommandResult(0, {"state": "abandoned", "summary": "Abandoned."}),
            dry_run=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.payload["atomicSeriesActivationRelease"],
            {"state": "unreadable-preserved"},
        )
        self.assertEqual(path.read_bytes(), malformed)

    def test_source_alias_read_failure_becomes_projection_problem(self) -> None:
        contract = self.fixture.contract("master-a")

        with mock.patch(
            "agents_remember.worktrees.activation.atomic_series_activation.canonical_local_branch",
            side_effect=RuntimeError("symbolic alias cycle"),
        ):
            projected = project_series_activation(contract, MASTER_A)

        self.assertEqual(projected.source_fact["state"], "unreadable")
        self.assertIsNotNone(projected.problem)
        assert projected.problem is not None
        self.assertEqual(
            projected.problem.errorType,
            "atomic-series-code-source-identity-unreadable",
        )
