from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents_remember.memory_quality.style.citations import source_index_cache
from agents_remember.worktrees.modules import terminal_validation
from agents_remember.worktrees.modules.abandon import abandon_result
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract
from test_cleanup_carryover import _contract
from test_worktree_support import git, init_repo


class TerminalPreflightFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)

    def contract(self, name: str, *, missing_source: bool = False) -> WorktreeContract:
        code = self.tmp / f"{name}-code"
        memory = self.tmp / f"{name}-memory"
        init_repo(code, "main")
        init_repo(memory, "main")
        git(code, "checkout", "-b", "ar/task")
        (code / "change.txt").write_text("unmerged\n", encoding="utf-8")
        git(code, "add", "change.txt")
        git(code, "commit", "-m", "unmerged")
        git(memory, "branch", "ar/task")
        contract = _contract(
            self.tmp,
            task_name=name,
            contract_path=self.tmp / "tasks" / name / "series-contract.md",
            code_repo_path=code,
            code_worktree=code,
            code_source_branch="missing" if missing_source else "main",
            code_work_branch="ar/task",
            memory_repo_path=memory,
            memory_worktree=memory,
            memory_source_branch="main",
            memory_work_branch="ar/task",
        )
        write_contract(contract.contract_path, contract)
        return contract

    def cache(self, contract: WorktreeContract) -> tuple[Path, bytes]:
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        authority.namespace.mkdir(parents=True)
        payload = b"preflight-must-preserve"
        (authority.namespace / "ready.json").write_bytes(payload)
        return authority.namespace, payload

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done", return_value=(True, "now"))
    def test_cleanup_unmerged_preflight_preserves_exact_cache(self, _carryover: MagicMock) -> None:
        contract = self.contract("cleanup-unmerged")
        cache, payload = self.cache(contract)
        with patch(
            "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
            return_value=False,
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["blockers"][0]["reason"], "not-merged-into-source")  # type: ignore[index]
        self.assertEqual((cache / "ready.json").read_bytes(), payload)

    def test_abandon_missing_source_is_not_safe_empty_history(self) -> None:
        contract = self.contract("abandon-missing-source", missing_source=True)
        cache, payload = self.cache(contract)
        with patch(
            "agents_remember.worktrees.modules.abandon.provider_async.provider_setup_running",
            return_value=False,
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["blockers"][0]["reason"], "source-branch-missing")  # type: ignore[index]
        self.assertEqual((cache / "ready.json").read_bytes(), payload)

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done", return_value=(True, "now"))
    def test_cleanup_ancestry_query_failure_is_explicit(self, _carryover: MagicMock) -> None:
        contract = self.contract("cleanup-query-failure")
        cache, payload = self.cache(contract)
        original = terminal_validation.run_git

        def failed(repo: Path, args: list[str], **kwargs: object):
            if args[0] == "merge-base":
                return SimpleNamespace(returncode=128, stdout="", stderr="ancestry exploded")
            return original(repo, args, **kwargs)  # type: ignore[arg-type]

        with (
            patch.object(terminal_validation, "run_git", new=failed),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ancestry exploded", result.payload["blockers"][0]["reason"])  # type: ignore[index]
        self.assertEqual((cache / "ready.json").read_bytes(), payload)
