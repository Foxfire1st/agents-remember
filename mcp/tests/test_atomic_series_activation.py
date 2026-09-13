"""Forcing tests for contract-scoped atomic-series activation authority."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents_remember.models.structural.atomic_series_activation import (
    AtomicSeriesActivationRecord,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.activation.atomic_series_activation import (
    activation_path,
    activation_waiting_reason,
    contract_fingerprint,
    observe_atomic_series,
    publish_atomic_series_selection,
)
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_atomic_series_selection,
)
from agents_remember.worktrees.modules.startup.start_contract import (
    MasterSeriesContractSpec,
    ensure_master_series_contract,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
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
    """Two sprint-commanded atomic masters that share one protected source pair.

    The sprint's integration branch is the source of every commanded atomic master, so
    both series contracts carry the SAME ``(code repository, source branch)`` and the same
    ``(memory repository, source branch)``. Only the work branches differ.
    """

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

    def test_contracts_sharing_one_source_pair_hold_independent_selection(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")
        # The premise: one sprint commands both masters from the same protected source.
        self.assertEqual(contract_a.code_source_branch, contract_b.code_source_branch)
        self.assertNotEqual(
            activation_path(self.fixture.coord, contract_a),
            activation_path(self.fixture.coord, contract_b),
        )

        active_a = publish_atomic_series_selection(contract_a, "active", timestamp=NOW)
        selected_b = publish_atomic_series_selection(contract_b, "reconciling", timestamp=NOW)

        observed_a = observe_atomic_series(contract_a)
        observed_b = observe_atomic_series(contract_b)
        self.assertEqual((active_a.state, observed_a.state), ("active", "active"))
        self.assertEqual(observed_a.selected_master, MASTER_A)
        self.assertEqual((selected_b.state, observed_b.state), ("reconciling", "reconciling"))
        self.assertEqual(observed_b.selected_master, MASTER_B)
        # Neither master's state is a reason for the other to wait.
        self.assertIsNone(activation_waiting_reason(observed_a))
        self.assertEqual(activation_waiting_reason(observed_b), "atomic-series-reconciling")
        self.assertTrue(contract_a.contract_path.is_file())
        self.assertTrue(contract_b.contract_path.is_file())

    def test_vacant_and_active_are_never_waiting_states(self) -> None:
        contract = self.fixture.contract("master-a")

        vacant = observe_atomic_series(contract)

        self.assertEqual(vacant.state, "vacant")
        self.assertIsNone(activation_waiting_reason(vacant))
        active = publish_atomic_series_selection(contract, "active", timestamp=NOW)
        self.assertIsNone(activation_waiting_reason(active))

    def test_release_addresses_only_the_released_contract(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")
        publish_atomic_series_selection(contract_a, "active", timestamp=NOW)
        publish_atomic_series_selection(contract_b, "active", timestamp=NOW)

        released = release_atomic_series_selection(contract_a, timestamp=NOW)

        self.assertEqual(released.state, "vacant")
        self.assertEqual(observe_atomic_series(contract_a).state, "vacant")
        self.assertEqual(observe_atomic_series(contract_a).last_selected_master, MASTER_A)
        self.assertEqual(observe_atomic_series(contract_b).state, "active")
        self.assertEqual(observe_atomic_series(contract_b).selected_master, MASTER_B)
        # A release still requires the exact selection it addresses.
        activation_path(self.fixture.coord, contract_a).unlink()
        with self.assertRaises(RuntimeError) as raised:
            release_atomic_series_selection(contract_a, timestamp=NOW)
        self.assertEqual(
            getattr(raised.exception, "status", None),
            "atomic-series-activation-selection-missing",
        )

    def test_another_contracts_record_can_never_be_adopted(self) -> None:
        contract_a = self.fixture.contract("master-a")
        contract_b = self.fixture.contract("master-b")
        publish_atomic_series_selection(contract_b, "active", timestamp=NOW)
        path_a = activation_path(self.fixture.coord, contract_a)
        path_a.parent.mkdir(parents=True, exist_ok=True)
        # Both directions of the identity are refused: another contract's key, and this
        # contract's key naming another contract.
        foreign = (
            AtomicSeriesActivationRecord(
                contractFingerprint=contract_fingerprint(contract_b),
                selectedMaster=MASTER_B,
                contractPath=contract_b.contract_path.resolve().as_posix(),
                state="active",
                revision=7,
                selectedAt=NOW,
            ),
            AtomicSeriesActivationRecord(
                contractFingerprint=contract_fingerprint(contract_a),
                selectedMaster=MASTER_B,
                contractPath=contract_b.contract_path.resolve().as_posix(),
                state="active",
                revision=7,
                selectedAt=NOW,
            ),
        )
        for record in foreign:
            path_a.write_text(json.dumps(record.model_dump(mode="json")), encoding="utf-8")

            observed = observe_atomic_series(contract_a)

            self.assertEqual(observed.state, "unreadable")
            self.assertEqual(observed.error_type, "atomic-series-activation-contract-mismatch")
        recovered = publish_atomic_series_selection(contract_a, "active", timestamp=NOW)
        self.assertEqual(recovered.selected_master, MASTER_A)
        self.assertEqual(observe_atomic_series(contract_a).state, "active")

    def test_a_terminal_contract_cannot_be_selected(self) -> None:
        contract_b = self.fixture.contract("master-b")
        publish_atomic_series_selection(contract_b, "active", timestamp=NOW)
        terminal = replace(contract_b, integration_status="completed")
        write_contract(terminal.contract_path, terminal)

        with self.assertRaises(RuntimeError) as raised:
            publish_atomic_series_selection(terminal, "reconciling", timestamp=NOW)

        self.assertEqual(
            getattr(raised.exception, "status", None),
            "atomic-series-terminal",
        )


if __name__ == "__main__":
    unittest.main()
