"""Cleanup runs itself after a successful integration, and never after a refusal.

The developer ruling is that a completed leaf is reclaimed by an automatic procedure rather
than a prompt: integration no longer ends at ``cleanup-pending`` with a question. These cases
drive the real application tool over real Git repositories and prove both halves of the same
rule -- a landed integration removes the worktrees, the merged local branches, the reports
directory and the enclosure root and says so, while a refused integration leaves every one of
them exactly where it was. A refused cleanup must never turn a completed landing into a
reported failure, so the first case also pins the payload shape an operator reads.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_source_lineage import _git
from test_transaction_only_worktree_delivery import _public_config

pytestmark = pytest.mark.integration


class _NoManagedCitationCache:
    """The citation guard port answering with production's own no-authority guard.

    The fixture leaf has no lifecycle-bound managed cache namespace, which is exactly the
    case ``terminal_namespace_guard`` resolves by yielding an authority-free guard. Going
    through the port keeps that decision in the procedure under test; only the cache
    reservation is a double, and it is the same value production yields.
    """

    def guard(self, contract: WorktreeContract, *, requested_contract_path: Path):
        del contract, requested_contract_path
        return nullcontext(TerminalNamespaceGuard(None, None, None))


@pytest.fixture
def bound_worktree_services():
    bind_worktree_services(
        replace(build_default_worktree_services(), citation_guard=_NoManagedCitationCache())
    )
    try:
        yield
    finally:
        reset_worktree_services()


def _landed_leaf(tmp_path: Path) -> WorktreeContract:
    fixture = _authority_fixture(tmp_path, external_memory=True)
    return _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=True)


def _local_branch_exists(repo: Path, branch: str) -> bool:
    return (
        run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0
    )


def _work_branch_sides(contract: WorktreeContract) -> list[tuple[str, Path, str]]:
    sides = [("code", contract.code_repo_path, contract.code_work_branch)]
    if contract.memory_repo_path is not None:
        sides.append(("memory", contract.memory_repo_path, contract.memory_work_branch))
    return sides


def test_successful_integration_reclaims_the_enclosure_and_reports_it(
    tmp_path: Path, bound_worktree_services
) -> None:
    """The landed leaf loses its worktrees, branches, reports and root, and says what went."""

    closed = _landed_leaf(tmp_path)
    config = _public_config(tmp_path, closed)
    assert closed.memory_worktree is not None

    applied = worktree_tools.worktree_integrate_tool(
        replace(config, retirement=replace(config.retirement, auto_land_on_integration=True)),
        contract_path=closed.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=False,
    )

    assert applied["ok"] is True, applied
    assert applied["state"] == "integrated"
    cleanup = applied["cleanup"]
    assert cleanup["automatic"] is True
    assert cleanup["state"] == "cleanup-completed"
    assert "cleanup_question" not in applied
    assert cleanup["summary"].startswith("Automatic cleanup removed ")
    assert cleanup["summary"].endswith("nothing was left in place.")

    # What was removed is named: both worktrees, both merged local branches, reports, root.
    assert {item["side"] for item in cleanup["removed"]["worktrees"]} == {"code", "memory"}
    assert {item["side"] for item in cleanup["removed"]["localBranches"]} == {"code", "memory"}
    assert cleanup["removed"]["reports"]
    assert cleanup["removed"]["enclosureRoot"]
    # ...and what was not is empty, so a silent success is impossible.
    assert cleanup["notRemoved"] == {
        "worktrees": [],
        "localBranches": [],
        "reports": [],
        "enclosureRoot": [],
    }

    # The filesystem agrees with the report.
    assert not closed.code_worktree.exists()
    assert not closed.memory_worktree.exists()
    assert not closed.worktree_group.exists()
    for _side, repo, branch in _work_branch_sides(closed):
        assert not _local_branch_exists(repo, branch), (repo, branch)

    # The contract moved to its terminal cell, and the published projection says done.
    assert load_contract(closed.contract_path).cleanup == "completed"
    assert applied["phase"] == "cleanup-completed"
    assert applied["nextOperation"] == "done"


def test_refused_integration_leaves_every_worktree_and_branch_in_place(
    tmp_path: Path, bound_worktree_services
) -> None:
    """A moved source refuses before any ref moves, so nothing is reclaimed."""

    closed = _landed_leaf(tmp_path)
    config = _public_config(tmp_path, closed)
    assert closed.memory_worktree is not None
    assert closed.memory_repo_path is not None
    memory_source = closed.memory_source_branch

    preview = worktree_tools.worktree_integrate_tool(
        config,
        contract_path=closed.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=True,
    )
    assert preview["ok"] is True, preview

    # Move the code source past the verified candidate: the landing must refuse.
    source_before = _git(closed.code_repo_path, "rev-parse", closed.code_source_branch)
    _git(closed.code_repo_path, "branch", "race", source_before)
    _git(closed.code_repo_path, "switch", "race")
    (closed.code_repo_path / "parallel.txt").write_text("parallel\n", encoding="utf-8")
    _git(closed.code_repo_path, "add", "parallel.txt")
    _git(closed.code_repo_path, "commit", "-m", "Parallel source change")
    raced = _git(closed.code_repo_path, "rev-parse", "HEAD")
    _git(
        closed.code_repo_path,
        "update-ref",
        f"refs/heads/{closed.code_source_branch}",
        raced,
        source_before,
    )
    memory_before = _git(closed.memory_repo_path, "rev-parse", memory_source)

    refused = worktree_tools.worktree_integrate_tool(
        replace(config, retirement=replace(config.retirement, auto_land_on_integration=True)),
        contract_path=closed.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=False,
    )

    assert refused["ok"] is False, refused
    assert refused["state"] == "blocked-non-ff"
    # The contract cell is still "pending"; no automatic-cleanup report was produced.
    assert refused["cleanup"] == "pending"
    assert _git(closed.code_repo_path, "rev-parse", closed.code_source_branch) == raced
    assert _git(closed.memory_repo_path, "rev-parse", memory_source) == memory_before

    # Everything the cleanup would have removed is still there.
    assert closed.code_worktree.exists()
    assert closed.memory_worktree.exists()
    assert closed.worktree_group.exists()
    for _side, repo, branch in _work_branch_sides(closed):
        assert _local_branch_exists(repo, branch), (repo, branch)

    current = load_contract(closed.contract_path)
    assert current.integration_status != "completed"
    assert current.cleanup == "pending"
