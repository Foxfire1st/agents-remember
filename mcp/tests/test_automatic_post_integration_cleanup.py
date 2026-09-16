"""Reclamation is automatic and unprompted, and finalization is what runs it.

The developer ruling is unchanged: a completed leaf is reclaimed by an automatic procedure
rather than a prompt. What this lane now pins is ownership. Integration used to reclaim inside
itself, which left the enclosure at ``cleanup: completed`` before the one guard that routes a
landed leaf to ``lifecycle_finalize_task`` -- keyed on that exact cell -- could ever look at it.
A real landing therefore reported ``done`` while the leaf's task document stayed ``planning``
and the master's sub-task row stayed ``inProgress``. The apply now stops at the landed refs and
the projection names the finalization move; finalization reclaims and reconciles the leaf
document and its master row, and its own procedure returns ``already-completed`` when the
cleanup cell already says so.

These cases drive the real application tools over real Git repositories and prove the whole
path: a landed integration retires nothing and routes to ``lifecycle_finalize_task``; that
finalization removes the worktrees, the merged local branches, the reports directory and the
enclosure root, publishes the operator-facing reclamation report naming all of them, and
completes the leaf document; a cleanup that cannot complete refuses before the task edge closes,
leaving the leaf document and its master row open over an enclosure that is still on disk; a
dry-run finalization reports cleanup's own plan and is never shaped with the completed
reclamation sentence; and a refused integration still leaves every one of them exactly where it
was.

The module keeps its historical name: reclamation is no longer "post integration" -- finalization
owns it -- and nine manifest locations declare this exact path. The real subjects are
``agents_remember.worktrees.modules.finalize::_run_or_verify_cleanup``, which runs reclamation and
is what these cases exercise through ``lifecycle_finalize_task``, and
``agents_remember.worktrees.modules.cleanup_report::cleanup_report``, which shapes the
operator-facing report the first case asserts.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.tasks import TaskDocument, read_task_doc
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.modules import finalize
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


def _leaf_document(contract: WorktreeContract) -> TaskDocument:
    """Read the leaf document the finalizer is contractually bound to complete."""

    resolved = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    assert resolved is not None, "the fixture leaf must own a task document"
    return read_task_doc(resolved[0])


def _master_row_status(contract: WorktreeContract) -> str:
    """The status of the master row that names this leaf."""

    master = read_task_doc(contract.task_root / "task.json")
    rows = [row for row in master.subTasks if row.number == contract.leaf_id]
    assert len(rows) == 1, f"expected one master row for {contract.leaf_id!r}, got {rows!r}"
    return rows[0].status


def _integrate_apply(config, contract: WorktreeContract):
    return worktree_tools.worktree_integrate_tool(
        replace(config, retirement=replace(config.retirement, auto_land_on_integration=True)),
        contract_path=contract.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=False,
    )


def test_a_landed_leaf_is_reclaimed_by_finalization_and_never_by_integration(
    tmp_path: Path, bound_worktree_services
) -> None:
    """The landing retires nothing, routes to finalization, and finalization completes the edge."""

    closed = _landed_leaf(tmp_path)
    config = _public_config(tmp_path, closed)
    assert closed.memory_worktree is not None

    applied = _integrate_apply(config, closed)

    assert applied["ok"] is True, applied
    assert applied["state"] == "integrated"

    # The result claims no reclamation: no removal inventory, and the ``cleanup`` key is the
    # untouched contract cell rather than a cleanup report.
    assert applied["cleanup"] == "pending"
    assert "removed" not in applied
    assert "notRemoved" not in applied
    assert "cleanup_question" not in applied
    assert "reclaimed when the task edge is finalized" in applied["summary"]

    # The route reaches finalization: the same projection every operator reads names the
    # terminal edge and the tool that owns it.
    assert applied["phase"] == "cleanup-pending"
    assert applied["nextOperation"] == "finalize"
    assert applied["nextTool"] == "lifecycle_finalize_task"
    assert applied["nextArgs"]["contract_path"] == closed.contract_path.as_posix()
    assert applied["nextRequiredArgs"] == ["contract_path"]

    # Nothing was retired: the cell is still pending and every target still exists.
    assert load_contract(closed.contract_path).cleanup == "pending"
    assert closed.code_worktree.exists()
    assert closed.memory_worktree.exists()
    assert closed.worktree_group.exists()
    assert (closed.worktree_group / "reports").is_dir()
    for _side, repo, branch in _work_branch_sides(closed):
        assert _local_branch_exists(repo, branch), (repo, branch)

    # ...and the leaf document is still open, which is the defect this ownership split fixes:
    # a leaf that has landed but not finalized has not completed its task edge.
    assert _leaf_document(closed).status != "Completed"

    # The move the projection named is the one that reclaims, and it is the one that closes
    # the task edge this whole lane exists for.
    finalized = worktree_tools.lifecycle_finalize_task_tool(
        config,
        contract_path=closed.contract_path.as_posix(),
    )

    assert finalized["ok"] is True, finalized
    assert finalized["state"] == "finalized"

    # The finalize payload carries the operator-facing reclamation report, not the raw cleanup
    # payload: the one sentence that names what was removed, and the inventory behind it.
    report = finalized["cleanup"]
    assert report["automatic"] is True
    assert report["state"] == "cleanup-completed"
    assert report["summary"] == (
        "Automatic cleanup removed 2 worktrees, 2 local branches, 1 reports directory, "
        "1 enclosure root; nothing was left in place."
    )
    removed = report["removed"]
    assert {item["side"] for item in removed["worktrees"]} == {"code", "memory"}
    assert {item["side"] for item in removed["localBranches"]} == {"code", "memory"}
    assert [item["path"] for item in removed["reports"]] == [
        (closed.worktree_group / "reports").as_posix()
    ]
    assert [item["path"] for item in removed["enclosureRoot"]] == [closed.worktree_group.as_posix()]
    # Nothing stayed behind, and the report says exactly that instead of listing the very
    # targets it just removed.
    assert report["notRemoved"] == {
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

    # The contract moved to its terminal cell...
    assert load_contract(closed.contract_path).cleanup == "completed"
    # ...and the leaf document plus the master row that named it both read Completed.
    assert _leaf_document(closed).status == "Completed"
    assert _master_row_status(closed) == "Completed"


def test_a_refused_cleanup_blocks_finalization_and_leaves_the_task_edge_open(
    tmp_path: Path, bound_worktree_services
) -> None:
    """A cleanup that cannot complete refuses rather than close an edge it never reclaimed.

    This is the load-bearing half of the ownership split. Reclamation now happens after the
    landing, so a refusal or a crash there must not be able to mark the leaf document or the
    master row ``Completed``: that would report a closed task edge over worktrees, merged
    branches and an enclosure root that are all still on disk.
    """

    closed = _landed_leaf(tmp_path)
    config = _public_config(tmp_path, closed)
    assert closed.memory_worktree is not None
    assert _integrate_apply(config, closed)["state"] == "integrated"

    with mock.patch.object(finalize, "cleanup_result", side_effect=RuntimeError("refused")):
        blocked = worktree_tools.lifecycle_finalize_task_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
        )

    assert blocked["state"] == "cleanup-blocked", blocked
    assert "task documents were not changed" in blocked["summary"]
    # Cleanup's own refusal payload is passed through whole -- its state and its reason, not a
    # reclamation report shaped over a cleanup that never ran.
    assert blocked["cleanup"] == {"state": "blocked", "summary": "refused"}

    # The task edge is still open, and every reclamation target is still exactly where it was.
    assert _leaf_document(closed).status != "Completed"
    assert _master_row_status(closed) != "Completed"
    assert load_contract(closed.contract_path).cleanup == "pending"
    assert closed.code_worktree.exists()
    assert closed.memory_worktree.exists()
    assert closed.worktree_group.exists()
    for _side, repo, branch in _work_branch_sides(closed):
        assert _local_branch_exists(repo, branch), (repo, branch)


def test_a_dry_run_finalization_reports_the_cleanup_plan_and_shapes_nothing(
    tmp_path: Path, bound_worktree_services
) -> None:
    """An unrun cleanup is never shaped with the completed reclamation sentence.

    A preview's payload lists what cleanup *would* remove, so shaping it with "removed ...
    nothing was left in place" would assert a reclamation that never happened. This is the case
    that pins the report shaper's dry-run gate.
    """

    closed = _landed_leaf(tmp_path)
    config = _public_config(tmp_path, closed)
    assert closed.memory_worktree is not None
    assert _integrate_apply(config, closed)["state"] == "integrated"

    preview = worktree_tools.lifecycle_finalize_task_tool(
        config,
        contract_path=closed.contract_path.as_posix(),
        dry_run=True,
    )

    assert preview["state"] == "would-finalize", preview
    # Cleanup's own plan, unshaped: no operator reclamation report and no completed sentence.
    assert preview["cleanup"]["state"] == "would-cleanup"
    assert "automatic" not in preview["cleanup"]
    assert preview["cleanup"]["summary"] == (
        "Cleanup would reclaim the worktree provider stack, remove worktrees, and delete "
        "merged local task branches where Git proves they are merged."
    )

    # A preview retires nothing.
    assert load_contract(closed.contract_path).cleanup == "pending"
    assert closed.code_worktree.exists()
    assert closed.memory_worktree.exists()
    assert closed.worktree_group.exists()
    assert (closed.worktree_group / "reports").is_dir()
    for _side, repo, branch in _work_branch_sides(closed):
        assert _local_branch_exists(repo, branch), (repo, branch)


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
    # The dry run promises only the landing; reclamation is the finalization's job now.
    assert "reclaimed when the task edge is finalized" in preview["cleanup_reminder"]

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

    refused = _integrate_apply(config, closed)

    assert refused["ok"] is False, refused
    assert refused["state"] == "blocked-non-ff"
    # The contract cell is still "pending"; no reclamation report was produced.
    assert refused["cleanup"] == "pending"
    assert "removed" not in refused
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
    assert _leaf_document(closed).status != "Completed"
