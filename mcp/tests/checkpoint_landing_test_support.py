"""Real temporary Git world for the checkpoint-landing boundary proofs.

One authoritative builder for the master/leaf series world that
``test_checkpoint_landing_end_to_end.py`` drives through the public operations. These helpers
construct state on real repositories and read it back; they assert nothing about the routes,
so the cases keep the whole claim.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.application import worktree_tools
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_cache import prepare_memory_cache, refresh_memory_cache
from agents_remember.kernel.memory_ledger import LedgerRow
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    write_contract,
)
from test_closeout_queue import QueueFixture
from test_worktree_support import git


def rev(repository: Path, branch: str) -> str:
    return git(repository, "rev-parse", branch)


def memory_repository(series: WorktreeContract) -> Path:
    """The external-memory repository this series contract must have."""

    assert series.memory_repo_path is not None
    return series.memory_repo_path


def payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload[key]
    assert isinstance(value, str), (key, value)
    return value


@contextlib.contextmanager
def branch_checkout(repository: Path, branch: str, scratch: Path) -> Iterator[Path]:
    """A disposable checkout of one branch, so a commit can be authored on it directly."""

    scratch.mkdir(parents=True, exist_ok=True)
    git(repository, "worktree", "add", scratch.as_posix(), branch)
    try:
        yield scratch
    finally:
        git(repository, "worktree", "remove", "--force", scratch.as_posix())


def close_out_leaf(leaf: WorktreeContract) -> WorktreeContract:
    """Record the real code and attributed memory commits authored in the leaf's worktrees."""

    code_worktree = leaf.code_worktree
    memory_worktree = leaf.memory_worktree
    assert memory_worktree is not None
    git(code_worktree, "add", "-A")
    git(code_worktree, "commit", "-m", "Leaf code")
    code_commit = git(code_worktree, "rev-parse", "HEAD")
    prepare_memory_cache(memory_worktree)
    git(memory_worktree, "add", "-A")
    git(memory_worktree, "commit", "-m", render_memory_content_message("Leaf memory", code_commit))
    memory_content = git(memory_worktree, "rev-parse", "HEAD")
    refresh_memory_cache(memory_worktree, repo_name=leaf.repo_name)
    closed = replace(
        leaf,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_content,
    )
    write_contract(closed.contract_path, closed)
    return closed


def accumulate_master_line(
    fixture: QueueFixture, series: WorktreeContract, scratch: Path, *, label: str
) -> LedgerRow:
    """Commit one accumulated code/memory pair, with attribution inside the memory commit."""

    del fixture
    code_commit = commit_code(series, scratch, label=label)
    memory_content = commit_memory_content(series, scratch, label=label)
    return LedgerRow(code_commit, memory_content)


def commit_code(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    with branch_checkout(series.code_repo_path, series.code_work_branch, scratch / "code") as tree:
        (tree / f"{label}.txt").write_text(f"{label}\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "commit", "-m", f"Land {label} code")
        return git(tree, "rev-parse", "HEAD")


def commit_memory_content(series: WorktreeContract, scratch: Path, *, label: str) -> str:
    code_commit = rev(series.code_repo_path, series.code_work_branch)
    with branch_checkout(
        memory_repository(series), series.memory_work_branch, scratch / "memory-content"
    ) as tree:
        (tree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
        prepare_memory_cache(tree)
        git(tree, "add", "-A")
        git(
            tree, "commit", "-m", render_memory_content_message(f"Land {label} memory", code_commit)
        )
        return git(tree, "rev-parse", "HEAD")


def advance_source_line(series: WorktreeContract, scratch: Path, *, label: str) -> LedgerRow:
    """Advance the memory source independently of the master's memory work branch."""

    memory = memory_repository(series)
    code_commit = commit_code(series, scratch, label=label)
    with branch_checkout(memory, series.memory_source_branch, scratch / f"source-{label}") as tree:
        (tree / f"{label}.md").write_text(f"# {label}\n", encoding="utf-8")
        prepare_memory_cache(tree)
        git(tree, "add", "-A")
        git(
            tree, "commit", "-m", render_memory_content_message(f"Land {label} source", code_commit)
        )
        return LedgerRow(code_commit, git(tree, "rev-parse", "HEAD"))


def absorb_source_into_master_line(series: WorktreeContract, scratch: Path, *, label: str) -> None:
    """Merge the actual source content into the master's memory work branch."""

    with branch_checkout(
        memory_repository(series), series.memory_work_branch, scratch / f"absorb-{label}"
    ) as tree:
        git(tree, "merge", "--no-ff", "-m", f"Merge {label} source", series.memory_source_branch)


def closeout_messages() -> Any:
    """The raw commit messages one closeout call carries; resolution decides the enabled legs."""

    return worktree_tools.CloseoutCommitMessages(code="Code", memory="Memory")


def checkpoint(fixture: QueueFixture, series: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
    """Drive the PUBLIC operation, exactly as the registered tool does."""

    return worktree_tools.worktree_checkpoint_landing_tool(
        fixture.cfg,
        contract_path=series.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=dry_run,
    )


def master_status(series: WorktreeContract) -> str:
    """The master's own task status, read from its document rather than from the contract."""

    document = TaskDocument.model_validate_json(
        (series.task_root / "task.json").read_text(encoding="utf-8")
    )
    return document.status


def set_master_status(series: WorktreeContract, *, status: str, row_status: str) -> None:
    """Rewrite the master's own status and its sub-task row status."""

    master = TaskDocument.model_validate_json(
        (series.task_root / "task.json").read_text(encoding="utf-8")
    )
    write_task_doc(
        series.task_root,
        master.model_copy(
            update={
                "status": status,
                "subTasks": [
                    row.model_copy(update={"status": row_status}) for row in master.subTasks
                ],
            }
        ),
    )
