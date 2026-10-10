"""MIK-R129 rules 1-2: a cleanup repeated after its archive was written finishes.

With a terminal archive present whose operation and arguments match and whose
state is not ``cleanup-completed``, ``cleanup_result`` takes the archived
contract. That branch must hold the same carryover-or-kept-line judgement as
the first run, made on the archived contract (rule 1); the repeat then runs to
its end (rule 2). Before the fix the branch reached the shared tail with
``kept_line`` unbound and Python raised ``UnboundLocalError``.
"""

import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import worktree_tools
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.models.lifecycles.enclosure import (
    TerminalWorktreeCleanupArguments,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    terminal_cleanup_contract_authority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from lifecycle_enclosure_test_support import publish_test_enclosure
from test_transaction_only_worktree_delivery import _public_config
from test_worktree_support import git, init_repo

CODE_BRANCH = "ar/fixture-repeat"
MEMORY_BRANCH = "ar/fixture-repeat-memory"
SOURCE_BRANCH = "main"


def _commit(repo: Path, name: str) -> None:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-m", name)


def _repo_with_leaf(repo: Path, branch: str) -> None:
    init_repo(repo, branch=SOURCE_BRANCH)
    git(repo, "checkout", "-b", branch)
    _commit(repo, "leaf-work")
    git(repo, "checkout", SOURCE_BRANCH)
    git(repo, "merge", "--no-ff", branch, "-m", "merge leaf")


def _contract(root: Path, code_repo: Path, memory_repo: Path) -> Path:
    contract_path = root / "tasks" / "repo-a" / "leaf" / "enclosures" / "a" / "series-contract.md"
    contract = WorktreeContract(
        task_id="task-1",
        task_name="task-one",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="external",  # type: ignore[arg-type]
        coordination_root=root,
        task_root=root / "tasks" / "repo-a" / "leaf",
        contract_path=contract_path,
        task_artifact=root / "tasks" / "repo-a" / "leaf" / "task.md",
        worktree_group=root / "worktrees" / "repo-a" / "leaf",
        code_repo_path=code_repo,
        code_source_branch=SOURCE_BRANCH,
        code_work_branch=CODE_BRANCH,
        code_base_commit="a" * 40,
        code_worktree=root / "worktrees" / "repo-a" / "leaf" / "code",
        memory_repo_path=memory_repo,
        memory_source_branch=SOURCE_BRANCH,
        memory_work_branch=MEMORY_BRANCH,
        memory_worktree=root / "worktrees" / "repo-a" / "leaf" / "memory",
        ledger_path=root / "worktrees" / "repo-a" / "leaf" / "memory-ledger.md",
        integration_status="completed",  # type: ignore[arg-type]
        kind="leaf",
        leaf_id="L129",
        lifecycle_id="01TESTREPEAT00000000000000",
    )
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract_path, contract)
    return contract_path


def test_repeated_cleanup_with_archive_ready_finishes(tmp_path: Path) -> None:
    """An archive-ready repeat runs to its end without re-judging the gate."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_leaf(code_repo, CODE_BRANCH)
    _repo_with_leaf(memory_repo, MEMORY_BRANCH)
    contract_path = _contract(tmp_path, code_repo, memory_repo)

    # Publish the enclosure, then the terminal archive exactly as the first
    # cleanup does before its destructive outputs: the repeat under test is a
    # cleanup interrupted after the archive was written.
    contract = load_contract(contract_path)
    publish_test_enclosure(contract, contract_path.read_text(encoding="utf-8"))
    archive = terminal_archive_required_result(
        load_contract(contract_path),
        operation="worktree_cleanup",
        arguments=TerminalWorktreeCleanupArguments(teardown_providers=True),
        dry_run=False,
    )
    assert archive.returncode == 0, archive.payload

    # Before the fix this raised UnboundLocalError: `kept_line`. Per
    # L129-THE-GATE-IS-NOT-JUDGED-AGAIN-RULING the gate is not judged again: the
    # archive holds no kept-line record, so the repeat carries none and invents none.
    result = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))

    assert result.returncode == 0, result.payload
    assert "landedTips" not in result.payload


class _NoManagedCitationCache:
    """Production's own no-authority guard: the fixture leaf owns no lifecycle-bound
    managed cache namespace, so the guard port yields an authority-free guard."""

    def guard(
        self,
        contract: WorktreeContract,
        *,
        requested_contract_path: Path,
        completed_contract_with_remains: bool = False,
    ):
        del contract, requested_contract_path, completed_contract_with_remains
        return nullcontext(TerminalNamespaceGuard(None, None, None))


@pytest.fixture
def bound_services():
    bind_worktree_services(
        replace(build_default_worktree_services(), citation_guard=_NoManagedCitationCache())
    )
    try:
        yield
    finally:
        reset_worktree_services()


def _integrated_archive_ready_leaf(tmp_path: Path) -> WorktreeContract:
    """Landed integration interrupted right after its terminal archive write."""
    fixture = _authority_fixture(tmp_path, external_memory=True)
    closed = _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=True)
    config = _public_config(tmp_path, closed)
    assert closed.memory_worktree is not None

    applied = worktree_tools.worktree_integrate_tool(
        replace(config, retirement=replace(config.retirement, auto_land_on_integration=True)),
        contract_path=closed.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=False,
    )
    assert applied["state"] == "integrated", applied

    # The interruption: the first cleanup published its terminal archive, then
    # stopped before any destructive output. Same helper, same accepted request.
    contract_path = closed.contract_path
    contract = load_contract(contract_path)
    observation = inspect_lifecycle_operation_locator(
        contract.coordination_root, contract.contract_path
    )
    if observation.locator is None and observation.state != "terminal-archived":
        publish_test_enclosure(contract, contract_path.read_text(encoding="utf-8"))
    archive = terminal_archive_required_result(
        load_contract(contract_path),
        operation="worktree_cleanup",
        arguments=TerminalWorktreeCleanupArguments(teardown_providers=True),
        dry_run=False,
    )
    assert archive.returncode == 0, archive.payload
    contract = load_contract(contract_path)
    locator = inspect_lifecycle_operation_locator(
        contract.coordination_root, contract.contract_path
    ).locator
    assert locator is not None
    authority = terminal_cleanup_contract_authority(
        contract.coordination_root,
        contract,
        locator,
    )
    assert authority.state == "archive-ready", authority.state
    return load_contract(contract_path)


def _assert_cleanup_completed(contract_path: Path) -> None:
    finished = load_contract(contract_path)
    assert finished.cleanup == "completed"
    finished_locator = inspect_lifecycle_operation_locator(
        finished.coordination_root, finished.contract_path
    ).locator
    assert finished_locator is not None
    completed_authority = terminal_cleanup_contract_authority(
        finished.coordination_root,
        finished,
        finished_locator,
    )
    assert completed_authority.state == "cleanup-completed", completed_authority.state


def test_repeated_cleanup_real_run_reaches_cleanup_completed(
    tmp_path: Path, bound_services
) -> None:
    """A genuine archive-ready repeat runs to its end and completes the contract.

    A landed integration is interrupted right after its terminal archive is
    published; the repeat (``approved=True``) must finish the cleanup and the
    terminal authority must read ``cleanup-completed`` afterward — the packet's
    conforming example, which a dry run cannot demonstrate.
    """
    contract_path = _integrated_archive_ready_leaf(tmp_path).contract_path

    # The repeat. Before the fix this raised UnboundLocalError: `kept_line`.
    result = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, approved=True))

    assert result.returncode == 0, result.payload
    _assert_cleanup_completed(contract_path)


def test_repeated_cleanup_with_branches_already_gone_finishes(
    tmp_path: Path, bound_services
) -> None:
    """R1-01 case: branches retired before the interruption still finish on repeat.

    The first cleanup published its archive, then retired the work branches exactly
    as its own destructive tail does, and died before the completed stamp. The gate
    is not judged again: the archive holds no kept-line record, so the repeat
    carries none, retires what remains, and completes.
    """
    closed = _integrated_archive_ready_leaf(tmp_path)
    contract_path = closed.contract_path
    # The tail's own order: worktree removals precede branch deletions. Retire both
    # exactly as the destructive tail does, then die before the completed stamp.
    git(closed.code_repo_path, "worktree", "remove", "--force", closed.code_worktree.as_posix())
    git(closed.code_repo_path, "branch", "-D", closed.code_work_branch)
    assert closed.memory_repo_path is not None
    assert closed.memory_worktree is not None
    git(
        closed.memory_repo_path,
        "worktree",
        "remove",
        "--force",
        closed.memory_worktree.as_posix(),
    )
    git(closed.memory_repo_path, "branch", "-D", closed.memory_work_branch)

    result = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, approved=True))

    assert result.returncode == 0, result.payload
    _assert_cleanup_completed(contract_path)
