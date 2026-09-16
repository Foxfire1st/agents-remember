"""Protected integration refs and their plane-owned mutation capability."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
)
from agents_remember.worktrees.integration import (
    integration_ref_transaction,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_ordinary_worktree,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationRefRace,
    merge_integrated_commits,
    prepare_integration_ref_move,
    require_integrated_memory_ancestry,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.integrate import (
    IntegrationSources,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_source_lineage import _commit_on, _git


class IntegrationBranchAuthorityTests(unittest.TestCase):
    def test_branch_alias_nested_checkout_and_memory_name_cannot_bypass_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            code = fixture.code_repo
            memory = fixture.leaf_contract.memory_repo_path
            assert memory is not None
            _git(code, "symbolic-ref", "refs/heads/alias-master", "refs/heads/ar/master")
            nested = code / "nested"
            nested.mkdir()
            cases = (
                replace(fixture.leaf_contract, code_work_branch="refs/heads/super"),
                replace(fixture.leaf_contract, code_work_branch="alias-master"),
                replace(
                    fixture.leaf_contract,
                    code_repo_path=nested,
                    code_work_branch="ar/atomic-two",
                ),
                replace(fixture.leaf_contract, memory_work_branch="main"),
            )
            for candidate in cases:
                with (
                    self.subTest(candidate=candidate),
                    self.assertRaisesRegex(RuntimeError, "integration-branch-is-not-a-workbench"),
                ):
                    require_ordinary_worktree(candidate, operation="test")

    def test_external_pair_cas_retains_torn_pair_without_clobbering_memory_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            memory_repo = closed.memory_repo_path
            assert memory_repo is not None
            operation_input = IntegrateOperationInput(
                configPath=fixture.config_path.as_posix(),
                contractPath=closed.contract_path.as_posix(),
            )
            lifecycle_operations.start_or_observe_operation(
                operation_input,
                closed,
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(closed.worktree_group, "integrate")
            )
            running = store.read()
            assert running is not None
            authority = running.integrationAuthority
            assert authority is not None
            _git(memory_repo, "branch", "memory-race", "ar/master")
            _commit_on(memory_repo, "memory-race", "parallel-memory.txt")
            raced_memory = _git(memory_repo, "rev-parse", "memory-race")
            original_cas = integration_ref_transaction._compare_and_swap_ref

            def race_memory(
                repo: Path,
                branch: str,
                expected: str,
                target: str,
                *,
                authority: object | None = None,
            ) -> bool:
                if repo == memory_repo and branch == "ar/master":
                    _git(
                        memory_repo,
                        "update-ref",
                        "refs/heads/ar/master",
                        raced_memory,
                        expected,
                    )
                return original_cas(
                    repo,
                    branch,
                    expected,
                    target,
                    authority=authority,
                )

            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "_compare_and_swap_ref",
                    side_effect=race_memory,
                ),
                self.assertRaises(IntegrationRefRace) as raised,
            ):
                commits = IntegratedCommits(
                    code=closed.code_commit,
                    memory_content=closed.memory_content_commit,
                )
                snapshot = prepare_integration_ref_move(
                    closed,
                    commits,
                    WorktreeArgs(operation_key=running.operationKey),
                    IntegrationSources(
                        current_code_source=authority.codeSourceCommit,
                        current_memory_source=authority.memorySourceCommit,
                        code_replay_required=False,
                        memory_replay_required=False,
                    ),
                )
                merge_integrated_commits(closed, commits, snapshot)
            expected = raised.exception.expected
            self.assertEqual(
                expected["before"],
                {"codeRef": authority.codeSourceCommit, "memoryRef": authority.memorySourceCommit},
            )
            self.assertEqual(
                expected["intended"],
                {"codeRef": closed.code_commit, "memoryRef": closed.memory_content_commit},
            )
            self.assertEqual(raised.exception.observed, {})
            self.assertEqual(
                _git(fixture.code_repo, "rev-parse", "ar/master"),
                closed.code_commit,
            )
            self.assertEqual(_git(memory_repo, "rev-parse", "ar/master"), raced_memory)

    def test_cache_damage_cannot_change_the_accepted_pair_or_block_its_ref_move(self) -> None:
        for cache_text in (None, "<<<<<<< malformed cache\n", "stale cache row\n"):
            with self.subTest(cache_text=cache_text), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fixture = _authority_fixture(root, external_memory=True)
                closed = _closed_external_leaf_worktrees(
                    fixture, root, publish_closeout_evidence=False
                )
                assert closed.memory_repo_path is not None
                assert closed.memory_worktree is not None
                cache = closed.memory_worktree / "memory.md"
                if cache_text is None:
                    cache.unlink(missing_ok=True)
                else:
                    cache.write_text(cache_text, encoding="utf-8")
                commits = IntegratedCommits(closed.code_commit, closed.memory_content_commit)
                sources = IntegrationSources(
                    current_code_source=_git(
                        closed.code_repo_path, "rev-parse", closed.code_source_branch
                    ),
                    current_memory_source=_git(
                        closed.memory_repo_path, "rev-parse", closed.memory_source_branch
                    ),
                    code_replay_required=False,
                    memory_replay_required=False,
                )
                memory_history = _git(closed.memory_repo_path, "rev-list", "--all", "--count")

                snapshot = prepare_integration_ref_move(closed, commits, WorktreeArgs(), sources)
                merge_integrated_commits(closed, commits, snapshot)

                self.assertEqual(
                    _git(closed.code_repo_path, "rev-parse", closed.code_source_branch),
                    commits.code,
                )
                self.assertEqual(
                    _git(closed.memory_repo_path, "rev-parse", closed.memory_source_branch),
                    commits.memory_content,
                )
                self.assertEqual(
                    _git(closed.memory_repo_path, "rev-list", "--all", "--count"), memory_history
                )


def _init_repo(root: Path) -> Path:
    """One real repository, because every clause here is asked of Git rather than of a stub."""

    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "landing@example.invalid")
    _git(root, "config", "user.name", "Landing Clauses")
    return root


def _commit_text(repo: Path, name: str, body: str) -> str:
    """One real commit of this repository, so a cell names something git can resolve."""

    (repo / name).write_text(body + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _integration_contract(repo: Path) -> WorktreeContract:
    """A leaf over real repositories for the accepted-object and ancestry proof."""

    return WorktreeContract(
        task_id="TASK",
        task_name="task",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=repo,
        task_root=repo,
        contract_path=repo / "series-contract.md",
        task_artifact=repo / "task.md",
        worktree_group=repo,
        code_repo_path=repo,
        code_source_branch="main",
        code_work_branch="work",
        code_base_commit="",
        code_worktree=repo,
        memory_repo_path=repo,
        memory_source_branch="main",
        memory_work_branch="memory-work",
        memory_base_commit="",
        ledger_path=repo / "memory.md",
    )


def test_the_accepted_memory_commit_must_descend_from_the_exact_source(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "reachability")
    base = _commit_text(repo, "base.md", "base")
    _git(repo, "branch", "unlanded", base)
    source = _commit_text(repo, "source.md", "current source")
    _git(repo, "switch", "unlanded")
    unlanded = _commit_text(repo, "unlanded.md", "memory content outside the source line")
    contract = _integration_contract(repo)
    assert not is_ancestor(repo, source, unlanded)

    with pytest.raises(RuntimeError, match="not based on the exact memory source"):
        require_integrated_memory_ancestry(
            contract,
            IntegratedCommits(code=source, memory_content=unlanded),
            memory_source_commit=source,
        )


def test_the_accepted_code_commit_must_exist_even_when_memory_is_current(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "code-side")
    head = _commit_text(repo, "content.md", "memory content")
    contract = _integration_contract(repo)

    with pytest.raises(RuntimeError, match="code commit does not exist"):
        require_integrated_memory_ancestry(
            contract,
            IntegratedCommits(code="c" * 40, memory_content=head),
            memory_source_commit=head,
        )
