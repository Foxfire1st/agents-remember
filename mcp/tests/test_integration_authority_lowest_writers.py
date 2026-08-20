"""Direct lowest-writer refusal for protected integration refs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.integration import integration_ref_transaction
from agents_remember.worktrees.modules import start_contract
from test_integration_branch_authority import _authority_fixture
from test_source_lineage import _commit_on, _git


class IntegrationAuthorityLowestWriterTests(unittest.TestCase):
    def test_public_worktree_facade_does_not_export_arbitrary_git_writers(self) -> None:
        for name in ("commit_if_dirty", "require_git", "run_git"):
            with self.subTest(name=name):
                self.assertNotIn(name, git_worktree_manager.__all__)
                self.assertFalse(hasattr(git_worktree_manager, name))

    def test_lowest_ref_and_checkout_writers_require_journaled_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            before = _git(fixture.code_repo, "rev-parse", "ar/master")
            _commit_on(fixture.code_repo, "leaf", "candidate.txt")
            target = _git(fixture.code_repo, "rev-parse", "leaf")

            with self.assertRaisesRegex(RuntimeError, "journaled authority"):
                integration_ref_transaction._compare_and_swap_ref(
                    fixture.code_repo,
                    "ar/master",
                    before,
                    target,
                )
            with self.assertRaisesRegex(RuntimeError, "journaled authority"):
                integration_ref_transaction.refresh_owned_checkout(
                    fixture.code_repo,
                    "ar/master",
                    before,
                    target,
                )

            self.assertEqual(_git(fixture.code_repo, "rev-parse", "ar/master"), before)

    def test_partial_bootstrap_rollback_requires_its_journal_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            before = _git(fixture.code_repo, "rev-parse", "ar/master")
            spec = start_contract.MasterSeriesContractSpec(
                coordination_root=fixture.coordination,
                repo_name="repo",
                code_repo=fixture.code_repo,
                memory_root=None,
                task_root=fixture.master_contract.task_root,
                task_name="master",
                parent_task_name="",
                protected_branch="main",
            )
            record = start_contract._SeriesBootstrapRecord(
                contractPath=fixture.master_contract.contract_path.as_posix(),
                codeRepository=fixture.code_repo.as_posix(),
                codeSourceBranch="main",
                codeWorkBranch="ar/master",
                codeBaseCommit=before,
            )

            with self.assertRaisesRegex(RuntimeError, "journaled bootstrap capability"):
                start_contract._rollback_partial_bootstrap_refs(spec, record)

            self.assertEqual(_git(fixture.code_repo, "rev-parse", "ar/master"), before)
