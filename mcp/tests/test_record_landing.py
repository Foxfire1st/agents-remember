"""The pull-request landing route records what the remote already did.

``worktree_integrate`` moves refs locally and then records what it moved. A pull request moves
nothing locally, so its route records the commit the caller names. These cases lock that recording
and the three ways it must refuse: an unapproved record, a missing commit, and a commit that landed
nowhere.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.record_landing import PR_STRATEGY, record_landing_result
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
    load_contract,
    write_contract,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=ar@example.invalid", "-c", "user.name=AR", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fixture(root: Path) -> WorktreeContract:
    """A series contract over a repository whose work branch has landed nowhere yet."""

    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    for branch in ("super", "ar/master"):
        _git(repo, "branch", branch)
    coordination = root / "coordination"
    task_root = coordination / "tasks" / "repo" / "master"
    task_root.mkdir(parents=True)
    contract = default_series_contract(
        ContractTask("master", "repo", coordination, "light-task", "disabled"),
        code=RepoBranchPlan(repo, "super", "ar/master", _git(repo, "rev-parse", "super")),
        memory=None,
        task_root=task_root,
    )
    write_contract(contract.contract_path, contract)
    return contract


def _commit_on(repo: Path, branch: str, name: str) -> str:
    _git(repo, "switch", branch)
    (repo / name).write_text(name + "\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)
    return _git(repo, "rev-parse", branch)


def _record(
    contract: WorktreeContract,
    *,
    landed_code_commit: str = "",
    dry_run: bool = False,
):
    return record_landing_result(
        WorktreeArgs(
            contract_path=contract.contract_path,
            approved=True,
            landed_code_commit=landed_code_commit,
            dry_run=dry_run,
        )
    )


class RecordLandingTests(unittest.TestCase):
    def test_landed_commit_sets_the_terminal_integration_cell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")

            result = _record(contract, landed_code_commit=landed)

            self.assertEqual((result.returncode, result.payload["state"]), (0, "recorded"))
            stored = load_contract(contract.contract_path)
            self.assertEqual(stored.integration_status, "completed")
            self.assertEqual(stored.integration_strategy, PR_STRATEGY)
            self.assertEqual(stored.integrated_code_commit, landed)

    def test_commit_that_landed_nowhere_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _commit_on(contract.code_repo_path, "super", "landed.txt")
            _git(contract.code_repo_path, "switch", "-c", "side", "super")
            (contract.code_repo_path / "unlanded.txt").write_text("x\n", encoding="utf-8")
            _git(contract.code_repo_path, "add", "-A")
            _git(contract.code_repo_path, "commit", "-m", "never merged")
            unlanded = _git(contract.code_repo_path, "rev-parse", "side")

            with self.assertRaises(RuntimeError) as raised:
                _record(contract, landed_code_commit=unlanded)

            self.assertIn("not reachable from any landing target", str(raised.exception))
            self.assertEqual(
                load_contract(contract.contract_path).integration_status, "not-started"
            )

    def test_recording_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")

            with self.assertRaises(RuntimeError) as raised:
                record_landing_result(
                    WorktreeArgs(contract_path=contract.contract_path, landed_code_commit=landed)
                )

            self.assertIn("explicit developer approval", str(raised.exception))

    def test_a_missing_commit_argument_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))

            with self.assertRaises(RuntimeError) as raised:
                _record(contract)

            self.assertIn("requires landed_code_commit", str(raised.exception))

    def test_dry_run_records_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")

            result = record_landing_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    landed_code_commit=landed,
                    dry_run=True,
                )
            )

            self.assertEqual(result.payload["state"], "would-record")
            self.assertEqual(
                load_contract(contract.contract_path).integration_status, "not-started"
            )

    def test_recording_twice_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")

            _record(contract, landed_code_commit=landed)
            again = _record(contract, landed_code_commit=landed)

            self.assertEqual(again.payload["state"], "already-recorded")

    def test_a_checkpointed_series_is_not_upgraded_into_a_reclaimable_integration(self) -> None:
        """A checkpoint landed a series that is still open, so this route must not close it.

        The full-record path writes ``completed`` plus ``cleanup="pending"``, and that pending
        cleanup is exactly what ``worktree_cleanup`` requires before it retires a branch. Taking
        it here would revoke the checkpoint's guarantee and make an open series reclaimable, so a
        checkpoint reads as already recorded and both cells keep the values the checkpoint wrote.
        """

        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")
            write_contract(
                contract.contract_path,
                replace(load_contract(contract.contract_path), integration_status="checkpointed"),
            )

            result = _record(contract, landed_code_commit=landed)

            self.assertEqual(result.payload["state"], "already-recorded")
            stored = load_contract(contract.contract_path)
            self.assertEqual(stored.integration_status, "checkpointed")
            self.assertEqual(stored.cleanup, "pending")
