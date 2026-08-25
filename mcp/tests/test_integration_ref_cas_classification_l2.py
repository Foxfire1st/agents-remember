"""Protected CAS failures share one live ref classifier across public recovery."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

import organizational_completion_test_support as fixture_mod
import pytest
from agents_remember.application.lifecycle import lifecycle_operation_worker
from agents_remember.application.lifecycle.lifecycle_operation_worker import OperationRuntime
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import (
    OperationControlRequest,
    worktree_operation_control_tool,
    worktree_status_tool,
)
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.integration import integration_quality as quality_mod
from agents_remember.worktrees.integration import integration_ref_state
from agents_remember.worktrees.integration.integration_ref_transaction import IntegrationRefRace
from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_controls as controls_mod,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_leaf_worktree,
)
from test_closeout_queue import SPRINT
from test_worktree_support import git


@dataclass
class _RecoveryCasCut:
    side: str
    outcome: str
    target_repo: Path
    target_branch: str
    target_before: str
    target_intended: str
    other_repo: Path
    other_branch: str
    other_before: str
    other_intended: str
    real_run_git: Callable[[Path, list[str]], CompletedProcess[str]]
    third: str = ""
    unreadable: bool = False

    def classify(self, repo: Path, arguments: list[str]) -> CompletedProcess[str]:
        if (
            self.unreadable
            and repo == self.target_repo
            and arguments[:2]
            == [
                "show-ref",
                "--verify",
            ]
        ):
            return CompletedProcess(
                arguments,
                2,
                stdout="",
                stderr="private backend detail",
            )
        return self.real_run_git(repo, arguments)

    def lose(
        self,
        _contract: object,
        _args: object,
        _commits: object,
        *,
        side: str,
    ) -> bool:
        assert side == self.side
        if self.outcome == "unchanged":
            git(
                self.other_repo,
                "update-ref",
                f"refs/heads/{self.other_branch}",
                self.other_before,
                self.other_intended,
            )
        elif self.outcome == "full-intended":
            git(
                self.target_repo,
                "update-ref",
                f"refs/heads/{self.target_branch}",
                self.target_intended,
                self.target_before,
            )
        elif self.outcome == "third":
            tree = git(self.target_repo, "rev-parse", f"{self.target_before}^{{tree}}")
            self.third = git(
                self.target_repo,
                "commit-tree",
                tree,
                "-p",
                self.target_before,
                "-m",
                "unrelated recovery CAS winner",
            )
            git(
                self.target_repo,
                "update-ref",
                f"refs/heads/{self.target_branch}",
                self.third,
                self.target_before,
            )
        elif self.outcome == "missing":
            git(
                self.target_repo,
                "update-ref",
                "-d",
                f"refs/heads/{self.target_branch}",
                self.target_before,
            )
        elif self.outcome == "unreadable":
            self.unreadable = True
        elif self.outcome != "partial-intended":
            raise AssertionError(f"unknown recovery CAS outcome: {self.outcome}")
        return False


def test_compare_and_swap_failure_requires_same_generation_recovery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture = _authority_fixture(root)
        closed = _closed_leaf_worktree(fixture, root, candidate_commit=True)
        write_contract(closed.contract_path, closed)
        operation_input = IntegrateOperationInput(
            configPath=fixture.config_path.as_posix(),
            contractPath=closed.contract_path.as_posix(),
        )
        lifecycle_operations.start_or_observe_operation(
            operation_input,
            closed,
            launcher=lambda *_: None,
        )
        store = located_lifecycle_operation_store(closed, "integrate")
        runtime = OperationRuntime(store)
        running = runtime.start()
        authority = running.integrationAuthority
        assert authority is not None
        runtime.progress(
            "source-merge",
            {
                "irreversible_boundary": True,
                "recovery_commits": {
                    "codeCommit": closed.code_commit,
                    "memoryContentCommit": "",
                    "ledgerCommit": "",
                },
            },
        )
        runtime.finish(
            {
                "reason": "compare-and-swap lost",
                "expected": {
                    "before": {"codeRef": authority.codeSourceCommit},
                    "intended": {"codeRef": closed.code_commit},
                },
                "observed": {"codeRef": authority.codeSourceCommit},
            },
            ok=False,
        )
        launch = mock.Mock()

        lifecycle_operations.start_or_observe_operation(
            operation_input,
            closed,
            launcher=launch,
            now=datetime.now(UTC) + timedelta(seconds=60),
        )

        retained = store.read()
        assert retained is not None
        assert retained.status == "input-required"
        assert retained.attempt == running.attempt
        assert retained.operationKey == running.operationKey
        assert retained.integrationAuthority == authority
        assert [row["action"] for row in legal_operation_controls(closed, retained)] == ["recover"]
        launch.assert_not_called()


class IntegrationRefCasClassificationL2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = fixture_mod.OrganizationalCompletionFixture()
        self.owner.setUp()
        self.fixture = self.owner.fixture

    def tearDown(self) -> None:
        try:
            self.owner.doCleanups()
        finally:
            self.owner.tearDown()

    def _status(self, contract):
        with mock.patch(
            "agents_remember.application.worktree_tools.git_worktree_manager.status_result",
            return_value=WorktreeCommandResult(
                0,
                {
                    "contract_path": contract.contract_path.as_posix(),
                    "task_name": contract.task_name,
                },
            ),
        ):
            status = worktree_status_tool(
                self.fixture.cfg,
                TaskRef(
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                ),
            )
        return next(row for row in status["lifecycleOperations"] if row["kind"] == "integrate")

    def _snapshots(self, contract, store):
        assert contract.memory_repo_path is not None
        queue = CloseoutQueueStore(self.fixture.coord, SPRINT)

        def ref_tip(repository, branch):
            ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
            result = integration_ref_state.run_git(
                repository,
                ["show-ref", "--verify", "--hash", ref],
            )
            return result.stdout.strip() if result.returncode == 0 else None

        return {
            "journal": {
                path.relative_to(store.path.parent).as_posix(): path.read_bytes()
                for path in store.path.parent.rglob("*")
                if path.is_file()
            },
            "tasks": {
                path.relative_to(self.fixture.tasks).as_posix(): path.read_bytes()
                for path in self.fixture.tasks.rglob("*")
                if path.is_file() and (path.name == "task.json" or path.suffix == ".md")
            },
            "contract": contract.contract_path.read_bytes(),
            "queue": {
                path.name: path.read_bytes() if path.exists() else None
                for path in (queue.state_path,)
            },
            "refs": {
                "code": ref_tip(
                    contract.code_repo_path,
                    contract.code_source_branch,
                ),
                "memory": ref_tip(
                    contract.memory_repo_path,
                    contract.memory_source_branch,
                ),
            },
        }

    def _cas_interruption(self, state: str):
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        authority = record.integrationAuthority
        assert authority is not None
        stale_recover = {}
        third_ref = ""
        classifier_unreadable = False
        real_run_git = integration_ref_state.run_git

        def unreadable_classifier(repo, args):
            if (
                classifier_unreadable
                and repo == contract.code_repo_path
                and args[:2] == ["show-ref", "--verify"]
            ):
                return CompletedProcess(
                    args,
                    2,
                    stdout="",
                    stderr="not public",
                )
            return real_run_git(repo, args)

        def fail_after_live_state(current, commits, _snapshot):
            nonlocal classifier_unreadable, third_ref
            durable = store.read()
            assert durable is not None
            rows = legal_operation_controls(load_contract(current.contract_path), durable)
            stale_recover.update(next(row for row in rows if row["action"] == "recover"))
            if state in {"partial", "full"}:
                git(
                    current.code_repo_path,
                    "update-ref",
                    f"refs/heads/{authority.codeSourceBranch}",
                    commits.code,
                    authority.codeSourceCommit,
                )
            if state == "full":
                assert current.memory_repo_path is not None
                git(
                    current.memory_repo_path,
                    "update-ref",
                    f"refs/heads/{authority.memorySourceBranch}",
                    commits.ledger,
                    authority.memorySourceCommit,
                )
            if state == "third":
                tree = git(
                    current.code_repo_path,
                    "rev-parse",
                    f"{authority.codeSourceCommit}^{{tree}}",
                )
                third_ref = git(
                    current.code_repo_path,
                    "commit-tree",
                    tree,
                    "-p",
                    authority.codeSourceCommit,
                    "-m",
                    "unrelated protected ref object",
                )
                git(
                    current.code_repo_path,
                    "update-ref",
                    f"refs/heads/{authority.codeSourceBranch}",
                    third_ref,
                    authority.codeSourceCommit,
                )
            if state == "missing":
                git(
                    current.code_repo_path,
                    "update-ref",
                    "-d",
                    f"refs/heads/{authority.codeSourceBranch}",
                    authority.codeSourceCommit,
                )
            if state == "unreadable":
                classifier_unreadable = True
            raise IntegrationRefRace(
                "transient raw compare-and-swap detail",
                expected={"internal": {"old": "not-public"}},
                observed={"internal": "not-public"},
            )

        with (
            mock.patch.object(
                quality_mod,
                "run_strict_code_quality_gate",
                side_effect=fixture_mod._full_gate(contract),
            ),
            mock.patch.object(
                integrate_mod,
                "merge_integrated_commits",
                side_effect=fail_after_live_state,
            ),
            mock.patch.object(
                integration_ref_state,
                "run_git",
                side_effect=unreadable_classifier,
            ),
        ):
            protected = integrate_mod.integrate_result(
                self.owner._args(contract, runtime, record), contract
            )
        self.assertEqual(protected.returncode, 2)
        runtime.finish(protected.payload, ok=False)
        retained = store.read()
        assert retained is not None
        return contract, store, retained, stale_recover, third_ref, protected.payload

    def _recover_in_two_phases(self, contract, store, row) -> tuple[dict, dict]:
        captured: list[tuple[WorktreeContract, LifecycleOperationRecord]] = []

        def capture_launch(admitted_contract, record) -> None:
            captured.append((admitted_contract, record))

        with mock.patch.object(
            controls_mod,
            "launch_detached_worker",
            side_effect=capture_launch,
        ) as launch:
            control = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**row["arguments"]),
            )
        launch.assert_called_once()
        self.assertEqual(len(captured), 1)
        admitted_contract, accepted = captured[0]
        self.assertEqual(admitted_contract.contract_path, contract.contract_path)
        self.assertEqual(control["lifecycleOperation"]["generation"], accepted.generation)
        self.assertEqual(control["lifecycleOperation"]["status"], "queued")

        runtime = lifecycle_operation_worker.OperationRuntime(store)
        current = runtime.start()
        self.assertEqual(current.operationKey, accepted.operationKey)
        self.assertEqual(current.generation, accepted.generation)
        lifecycle_operation_worker.execute_operation(current, runtime)
        return control, self._status(contract)

    def _assert_recovery_cas_loss(
        self,
        side: str,
        outcome: str,
    ) -> None:
        contract, store, retained, advertised, cut = self._prepare_recovery_cas_cut(
            side,
            outcome,
        )
        with (
            mock.patch.object(
                integrate_mod,
                "recover_integration_ref",
                side_effect=cut.lose,
            ) as recovery_cas,
            mock.patch.object(
                integration_ref_state,
                "run_git",
                side_effect=cut.classify,
            ),
        ):
            protected_control, protected = self._recover_in_two_phases(
                contract,
                store,
                advertised,
            )
            self.assertTrue(protected_control["ok"])
            fresh = self._status(contract)
            recovery_cas.assert_called_once()

            if outcome == "full-intended":
                self.assertEqual(protected["status"], "completed")
                self.assertEqual(protected["legalControls"], [])
                completed = store.read()
                assert completed is not None
                self.assertEqual(completed.generation, retained.generation)
                return

            if outcome in {"unchanged", "partial-intended"}:
                retry = self._assert_recoverable_cas_loss(outcome, protected, fresh)
            else:
                self._assert_conflicting_cas_loss(
                    (contract, store, advertised, protected, fresh, cut),
                )
                return

        recovered_control, recovered = self._recover_in_two_phases(contract, store, retry)
        self.assertTrue(recovered_control["ok"])
        self.assertEqual(recovered["status"], "completed")
        completed = store.read()
        assert completed is not None
        self.assertEqual(
            (completed.generation, completed.status), (retained.generation, "completed")
        )

    def _prepare_recovery_cas_cut(self, side: str, outcome: str):
        contract, store, retained, _stale, _third, _protected = self._cas_interruption("unchanged")
        authority = retained.integrationAuthority
        commits = retained.recoveryCommits
        assert authority is not None and commits is not None
        assert contract.memory_repo_path is not None
        refs = {
            "code": (
                contract.code_repo_path,
                authority.codeSourceBranch,
                authority.codeSourceCommit,
                commits.codeCommit,
            ),
            "memory": (
                contract.memory_repo_path,
                authority.memorySourceBranch,
                authority.memorySourceCommit,
                commits.ledgerCommit,
            ),
        }
        other = "memory" if side == "code" else "code"
        other_repo, other_branch, other_before, other_intended = refs[other]
        git(
            other_repo,
            "update-ref",
            f"refs/heads/{other_branch}",
            other_intended,
            other_before,
        )
        advertised = self._status(contract)["legalControls"][0]
        assert advertised["action"] == "recover"
        target_repo, target_branch, target_before, target_intended = refs[side]
        cut = _RecoveryCasCut(
            side=side,
            outcome=outcome,
            target_repo=target_repo,
            target_branch=target_branch,
            target_before=target_before,
            target_intended=target_intended,
            other_repo=other_repo,
            other_branch=other_branch,
            other_before=other_before,
            other_intended=other_intended,
            real_run_git=integration_ref_state.run_git,
        )
        return contract, store, retained, advertised, cut

    def _assert_recoverable_cas_loss(self, outcome, protected, fresh):
        self.assertEqual(
            protected["result"]["state"],
            "integration-ref-publication-interrupted",
        )
        self.assertEqual(
            protected["result"]["refState"],
            "unchanged" if outcome == "unchanged" else "intended",
        )
        self.assertEqual(protected["result"], fresh["result"])
        self.assertEqual(
            [row["action"] for row in fresh["legalControls"]],
            ["recover"],
        )
        return fresh["legalControls"][0]

    def _assert_conflicting_cas_loss(self, context) -> None:
        contract, store, advertised, protected, fresh, cut = context
        self.assertEqual(protected["result"]["state"], "integration-ref-conflict")
        self.assertEqual(protected["result"], fresh["result"])
        self.assertEqual(fresh["legalControls"], [])
        self.assertEqual(protected["result"]["nextAction"], "developer-decision")
        observed = protected["result"]["observed"][f"{cut.side}Ref"]
        if cut.outcome == "unreadable":
            self.assertEqual(observed["errorType"], "ref-unreadable")
            self.assertNotIn("private backend detail", repr(protected))
        elif cut.outcome == "missing":
            self.assertEqual(observed["errorType"], "ref-missing")
        else:
            self.assertEqual(observed["objectId"], cut.third)
        before_refusal = self._snapshots(contract, store)
        refused = worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**advertised["arguments"]),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], protected["result"]["state"])
        self.assertEqual(refused["expected"], protected["result"]["expected"])
        self.assertEqual(refused["observed"], protected["result"]["observed"])
        self.assertEqual(self._snapshots(contract, store), before_refusal)

    def _assert_recoverable_cas(self, state: str, projected_ref_state: str) -> None:
        contract, store, retained, _stale, _third, protected = self._cas_interruption(state)
        self.assertEqual(protected["state"], "integration-ref-publication-interrupted")
        self.assertEqual(protected["refState"], projected_ref_state)
        self.assertEqual(protected["nextAction"], "recover")
        self.assertNotIn("developerDecisionRequired", protected)
        self.assertNotIn("internal", repr(protected))

        projected = self._status(contract)
        self.assertEqual(projected["result"], protected)
        rows = projected["legalControls"]
        self.assertEqual([row["action"] for row in rows], ["recover"])
        control, result = self._recover_in_two_phases(contract, store, rows[0])
        self.assertTrue(control["ok"])
        self.assertEqual(result["status"], "completed")
        completed = store.read()
        assert completed is not None
        self.assertEqual(completed.generation, retained.generation)
        self.assertEqual(completed.status, "completed")
        authority = completed.integrationAuthority
        commits = completed.recoveryCommits
        assert authority is not None and commits is not None
        self.assertEqual(
            git(
                contract.code_repo_path,
                "rev-parse",
                authority.codeSourceBranch,
            ),
            commits.codeCommit,
        )
        assert contract.memory_repo_path is not None
        self.assertEqual(
            git(
                contract.memory_repo_path,
                "rev-parse",
                authority.memorySourceBranch,
            ),
            commits.ledgerCommit,
        )

    def test_unchanged_cas_interruption_recovers_same_generation(self) -> None:
        self._assert_recoverable_cas("unchanged", "unchanged")

    def test_partial_intended_cas_interruption_recovers_same_generation(self) -> None:
        self._assert_recoverable_cas("partial", "intended")

    def test_full_intended_cas_interruption_recovers_same_generation(self) -> None:
        self._assert_recoverable_cas("full", "intended")

    def test_third_ref_cas_result_status_and_stale_handler_are_one_decision(self) -> None:
        contract, store, retained, stale, third, protected = self._cas_interruption("third")
        self.assertEqual(protected["state"], "integration-ref-conflict")
        self.assertEqual(protected["nextAction"], "developer-decision")
        self.assertNotIn("internal", repr(protected))
        projected = self._status(contract)
        self.assertEqual(projected["legalControls"], [])
        self.assertEqual(projected["result"], protected)
        before = self._snapshots(contract, store)

        refused = worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**stale["arguments"]),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(
            {
                "status": protected["state"],
                "detail": protected["decisionSurface"],
                "developerDecisionRequired": protected["developerDecisionRequired"],
                "nextAction": protected["nextAction"],
                "expected": protected["expected"],
                "observed": protected["observed"],
            },
            {
                key: refused[key]
                for key in (
                    "status",
                    "detail",
                    "developerDecisionRequired",
                    "nextAction",
                    "expected",
                    "observed",
                )
            },
        )
        self.assertEqual(self._snapshots(contract, store), before)

        authority = retained.integrationAuthority
        assert authority is not None
        git(
            contract.code_repo_path,
            "update-ref",
            f"refs/heads/{authority.codeSourceBranch}",
            authority.codeSourceCommit,
            third,
        )
        restored = self._status(contract)
        self.assertEqual(
            restored["result"]["state"],
            "integration-ref-publication-interrupted",
        )
        rows = restored["legalControls"]
        self.assertEqual([row["action"] for row in rows], ["recover"])
        recovered_control, recovered = self._recover_in_two_phases(contract, store, rows[0])
        self.assertTrue(recovered_control["ok"])
        self.assertEqual(recovered["status"], "completed")
        completed = store.read()
        assert completed is not None
        self.assertEqual(
            (completed.generation, completed.status), (retained.generation, "completed")
        )

    def test_missing_ref_cas_result_status_and_stale_handler_are_one_decision(self) -> None:
        contract, store, retained, stale, _third, protected = self._cas_interruption("missing")
        self.assertEqual(protected["state"], "integration-ref-conflict")
        observed = protected["observed"]
        assert isinstance(observed, dict)
        code_ref = observed["codeRef"]
        assert isinstance(code_ref, dict)
        self.assertEqual(
            code_ref["errorType"],
            "ref-missing",
        )
        projected = self._status(contract)
        self.assertEqual(projected["legalControls"], [])
        self.assertEqual(projected["result"], protected)
        before = self._snapshots(contract, store)
        refused = worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**stale["arguments"]),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], protected["state"])
        self.assertEqual(refused["expected"], protected["expected"])
        self.assertEqual(refused["observed"], protected["observed"])
        self.assertEqual(self._snapshots(contract, store), before)

        authority = retained.integrationAuthority
        assert authority is not None
        git(
            contract.code_repo_path,
            "update-ref",
            f"refs/heads/{authority.codeSourceBranch}",
            authority.codeSourceCommit,
        )
        restored = self._status(contract)
        self.assertEqual([row["action"] for row in restored["legalControls"]], ["recover"])
        recovered_control, recovered = self._recover_in_two_phases(
            contract,
            store,
            restored["legalControls"][0],
        )
        self.assertTrue(recovered_control["ok"])
        self.assertEqual(recovered["status"], "completed")

    def test_unreadable_ref_cas_result_status_and_stale_handler_are_one_decision(self) -> None:
        contract, store, _retained, stale, _third, protected = self._cas_interruption("unreadable")
        current = store.read()
        assert current is not None
        authority = current.integrationAuthority
        assert authority is not None
        real_run_git = integration_ref_state.run_git

        def unreadable_code(repo, args):
            if repo == contract.code_repo_path and args[:2] == ["show-ref", "--verify"]:
                return CompletedProcess(
                    args,
                    2,
                    stdout="",
                    stderr="not public",
                )
            return real_run_git(repo, args)

        with mock.patch.object(
            integration_ref_state,
            "run_git",
            side_effect=unreadable_code,
        ):
            projected = self._status(contract)
            before = self._snapshots(contract, store)
            refused = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**stale["arguments"]),
            )
        self.assertEqual(projected["legalControls"], [])
        self.assertEqual(projected["result"], protected)
        observed = protected["observed"]
        assert isinstance(observed, dict)
        code_ref = observed["codeRef"]
        assert isinstance(code_ref, dict)
        self.assertEqual(code_ref["errorType"], "ref-unreadable")
        self.assertNotIn("not public", repr(protected))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["expected"], protected["expected"])
        self.assertEqual(refused["observed"], protected["observed"])
        self.assertEqual(self._snapshots(contract, store), before)
        restored = self._status(contract)
        self.assertEqual([row["action"] for row in restored["legalControls"]], ["recover"])
        recovered_control, recovered = self._recover_in_two_phases(
            contract,
            store,
            restored["legalControls"][0],
        )
        self.assertTrue(recovered_control["ok"])
        self.assertEqual(recovered["status"], "completed")


@pytest.mark.parametrize("side", ["code", "memory"])
@pytest.mark.parametrize(
    "outcome",
    [
        "unchanged",
        "partial-intended",
        "full-intended",
        "third",
        "missing",
        "unreadable",
    ],
)
def test_recovery_side_cas_loss_uses_the_shared_live_classifier(
    side: str,
    outcome: str,
) -> None:
    case = IntegrationRefCasClassificationL2Tests(
        "test_unchanged_cas_interruption_recovers_same_generation"
    )
    case.setUp()
    try:
        case._assert_recovery_cas_loss(side, outcome)
    finally:
        case.tearDown()


if __name__ == "__main__":
    unittest.main()
