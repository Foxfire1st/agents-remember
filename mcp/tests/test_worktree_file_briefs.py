"""File briefs follow durable memory checkout creation and successful sync publication."""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.file_briefs.folder import tree_named
from agents_remember.application.worktree_tool_requests import (
    StartExecution,
    TaskBases,
    TaskIdentity,
)
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_ignore import BRIEFS_IGNORE_LINE
from agents_remember.mcp.tools.worktree import worktree_start_payload
from agents_remember.memory.knowledge_index.tree import directory_key
from agents_remember.worktrees.sync_transaction_git import _merge as sync_merge
from knowledge_conversion_test_support import APP
from knowledge_index_test_support import REVIEW_PATH, git, write_review_tree
from test_worktree_start_preview import LEAF, MASTER, REPO, PreviewFixture
from test_worktree_sync_knowledge_merge import _converted_lines


def _assert_brief(memory: Path, path: str, code_commit: str) -> None:
    file = memory / "briefs" / f"{path}.md"
    key = directory_key(memory)
    assert (memory / "briefs/TREE").read_text(encoding="utf-8").strip() == key
    assert tree_named(file) == key
    assert f"code commit `{code_commit[:12]}`" in file.read_text(encoding="utf-8")


def _start(world: PreviewFixture, *, dry_run: bool) -> dict[str, object]:
    return worktree_start_payload(
        world.config,
        TaskIdentity(
            repo_id=REPO, task_name=MASTER, worktree_name="fresh-preview-l1", leaf_id=LEAF
        ),
        bases=TaskBases(memory_mode="external"),
        execution=StartExecution(dry_run=dry_run),
    )


@pytest.mark.parametrize(
    ("ignore_lines", "ignored"),
    [
        pytest.param(f"{BRIEFS_IGNORE_LINE}\n".encode(), True, id="effective"),
        pytest.param(b"", False, id="missing"),
        pytest.param(
            f"{BRIEFS_IGNORE_LINE}\n!{BRIEFS_IGNORE_LINE}\n".encode(), False, id="negated"
        ),
    ],
)
def test_start_refreshes_the_new_memory_checkout_after_preview(
    tmp_path: Path, ignore_lines: bytes, ignored: bool
) -> None:
    world = PreviewFixture(tmp_path / "workspace")
    write_review_tree(world.memory)
    ignore = world.memory / ".gitignore"
    if ignore_lines:
        ignore.write_bytes(ignore.read_bytes() + ignore_lines)
    git(world.memory, "add", "knowledge", "onboarding", ".gitignore")
    git(
        world.memory,
        "commit",
        "-m",
        render_memory_content_message("Record knowledge", world.base),
    )
    plan = world.plan()
    memory = plan.contract.memory_worktree
    assert memory is not None
    source_ignore = (world.memory / ".gitignore").read_bytes()
    preview = _start(world, dry_run=True)
    assert preview["ok"] and preview["state"] == "would-start", preview
    assert not memory.exists()
    assert (world.memory / ".gitignore").read_bytes() == source_ignore

    applied = _start(world, dry_run=False)
    assert applied["ok"] and applied["state"] == "started", applied
    marker = memory / "briefs/TREE"
    if ignored:
        code_commit = git(plan.contract.code_worktree, "rev-parse", "HEAD")
        _assert_brief(memory, REVIEW_PATH, code_commit)
        assert git(memory, "check-ignore", "--no-index", "--", "briefs/TREE") == "briefs/TREE"
        assert "briefsProblem" not in applied
        marker.unlink()
    else:
        assert applied["briefsProblem"]
        assert not (memory / "briefs").exists()
    assert (memory / ".gitignore").read_bytes() == source_ignore
    assert git(memory, "status", "--porcelain=v1") == ""
    assert not (world.memory / "briefs").exists()
    assert (world.memory / ".gitignore").read_bytes() == source_ignore

    # Reaching the existing enclosure again only attaches. A missing brief stays missing.
    replay = _start(world, dry_run=False)
    assert replay["ok"], replay
    assert not marker.exists()


@pytest.mark.usefixtures("worktree_services")
def test_fast_forward_adopts_existing_ignore_and_refreshes_the_settled_pair(tmp_path: Path) -> None:
    lines = _converted_lines(tmp_path)
    incoming = lines.on_line({".gitignore": f"/memory.md\n{BRIEFS_IGNORE_LINE}\n".encode()})
    code = lines.fixture.contract.code_worktree
    source_commit = git(lines.fixture.code_repo, "rev-parse", lines.fixture.source_branch)
    (code / "local.py").write_text("LOCAL = True\n", encoding="utf-8")
    dirty_memory = lines.leaf / "local.md"
    dirty_memory.write_text("Local memory work.\n", encoding="utf-8")

    preview = lines.sync(dry_run=True)
    assert preview["state"] == "would-sync", preview
    assert not (lines.leaf / "briefs").exists()

    result = lines.sync()
    assert result["state"] == "synced", result
    assert git(lines.leaf, "rev-parse", "HEAD") == incoming
    _assert_brief(lines.leaf, APP, source_commit)
    assert dirty_memory.read_text(encoding="utf-8") == "Local memory work.\n"
    assert (code / "local.py").is_file()
    assert not (lines.line / "briefs").exists()

    marker = lines.leaf / "briefs/TREE"
    marker.unlink()
    replay = lines.sync(resolution_action="continue")
    assert replay["state"] == "synced", replay
    assert not marker.exists()
    current = lines.sync()
    assert current["state"] == "already-current", current
    assert not marker.exists()


@pytest.mark.usefixtures("worktree_services")
def test_ref_only_memory_sync_does_not_regenerate_briefs(tmp_path: Path) -> None:
    lines = _converted_lines(tmp_path)
    ignore = f"/memory.md\n{BRIEFS_IGNORE_LINE}\n".encode()
    lines.on_line({".gitignore": ignore})
    assert lines.sync()["state"] == "synced"
    marker = lines.leaf / "briefs/TREE"
    marker.unlink()
    old = git(lines.line, "rev-parse", "HEAD")
    code = git(lines.fixture.code_repo, "rev-parse", lines.fixture.source_branch)
    git(
        lines.line,
        "commit",
        "--allow-empty",
        "-m",
        render_memory_content_message("Advance the memory ref", code),
    )
    incoming = git(lines.line, "rev-parse", "HEAD")
    assert incoming != old
    assert git(lines.line, "rev-parse", f"{incoming}^{{tree}}") == git(
        lines.line, "rev-parse", f"{old}^{{tree}}"
    )

    result = lines.sync()

    assert result["state"] == "synced", result
    assert git(lines.leaf, "rev-parse", "HEAD") == incoming
    assert "briefsProblem" not in result
    assert not marker.exists()


@pytest.mark.usefixtures("worktree_services")
def test_fast_forward_with_no_ignore_line_keeps_incoming_bytes(tmp_path: Path) -> None:
    lines = _converted_lines(tmp_path)
    incoming = lines.on_line({"incoming.md": b"New memory.\n"})
    ignore = lines.leaf / ".gitignore"
    previous = ignore.read_bytes() if ignore.exists() else None

    result = lines.sync()

    assert result["state"] == "synced", result
    assert str(result["briefsProblem"])
    assert git(lines.leaf, "rev-parse", "HEAD") == incoming
    assert (ignore.read_bytes() if ignore.exists() else None) == previous
    assert not (lines.leaf / "briefs").exists()


@pytest.mark.usefixtures("worktree_services")
def test_memory_merge_prepares_ignore_before_committing_and_refreshes(tmp_path: Path) -> None:
    lines = _converted_lines(tmp_path)
    lines.on_leaf({"own.md": b"Leaf memory.\n"})
    lines.on_line({"incoming.md": b"Other memory.\n"})

    skipped = lines.sync(memory_sync_choice="skip-memory")
    assert skipped["state"] == "sync-pass-completed-memory-skipped", skipped
    assert not (lines.leaf / "briefs").exists()

    result = lines.merge()

    assert result["state"] == "synced", result
    assert BRIEFS_IGNORE_LINE in git(lines.leaf, "show", "HEAD:.gitignore").splitlines()
    code_commit = git(lines.fixture.contract.code_worktree, "rev-parse", "HEAD")
    _assert_brief(lines.leaf, APP, code_commit)


@pytest.mark.usefixtures("worktree_services")
def test_refused_memory_merge_preserves_existing_ignore_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = _converted_lines(tmp_path)
    start = lines.on_leaf({"own.md": b"Leaf memory.\n", ".gitignore": b"/memory.md\n/custom/\n"})
    lines.on_line({"incoming.md": b"Other memory.\n"})

    def refuse(*, candidate_tree: str, **_fields: object) -> str:
        staged_ignore = git(lines.leaf, "show", f"{candidate_tree}:.gitignore")
        assert BRIEFS_IGNORE_LINE in staged_ignore.splitlines()
        return "fixture gate refusal"

    monkeypatch.setattr(sync_merge, "memory_commit_refusal", refuse)

    result = lines.merge()

    assert result["state"] == "sync-knowledge-validation-refused", result
    assert git(lines.leaf, "rev-parse", "HEAD") == start
    assert (lines.leaf / ".gitignore").read_bytes() == b"/memory.md\n/custom/\n"
    assert git(lines.leaf, "diff", "--cached", "--", ".gitignore") == ""
    assert not (lines.leaf / "briefs").exists()


@pytest.mark.usefixtures("worktree_services")
def test_parked_memory_conflict_and_cancel_do_not_generate_briefs(tmp_path: Path) -> None:
    lines = _converted_lines(tmp_path)
    lines.on_leaf({"conflict.md": b"Leaf memory.\n"})
    lines.on_line({"conflict.md": b"Other memory.\n"})
    ignore = lines.leaf / ".gitignore"
    previous = ignore.read_bytes() if ignore.exists() else None

    parked = lines.merge()
    assert parked["state"] == "sync-resolution-required", parked
    assert not (lines.leaf / "briefs").exists()
    assert (ignore.read_bytes() if ignore.exists() else None) == previous

    cancelled = lines.sync(resolution_action="cancel")
    assert cancelled["state"] == "sync-cancelled", cancelled
    assert not (lines.leaf / "briefs").exists()
    assert (ignore.read_bytes() if ignore.exists() else None) == previous
