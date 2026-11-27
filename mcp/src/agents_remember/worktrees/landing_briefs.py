"""Post-landing file-brief refreshes for integration, checkpoint, and PR-recorded routes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.kernel.memory_ledger import LEDGER_RELATIVE_PATH
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.integration.integration_branch_authority import (
    branch_worktree_owners,
)
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.modules.git import head_commit, require_git
from agents_remember.worktrees.services import refresh_written_memory_briefs
from agents_remember.worktrees.worktree_contract import WorktreeContract

_GIT_ERRORS = (OSError, RuntimeError, subprocess.SubprocessError)


def refresh_integrated_memory_briefs(
    contract: WorktreeContract,
    *,
    code_commit: str,
    memory_commit: str,
    memory_branch: str,
    previous_memory_commit: str | None,
) -> str | None:
    """Refresh external-memory ``briefs/`` checkouts after integration/checkpoint publication.

    Runs only after durable landing finalization; failures are strictly advisory.
    """

    if contract.memory_mode != "external" or contract.memory_repo_path is None or not memory_branch:
        return None
    changed_or_problem = _memory_content_changed(
        contract.memory_repo_path, previous_memory_commit, memory_commit
    )
    if isinstance(changed_or_problem, str):
        return changed_or_problem
    if not changed_or_problem:
        return None
    owners_or_problem = _branch_owners(contract.memory_repo_path, memory_branch)
    if isinstance(owners_or_problem, str):
        return owners_or_problem
    paired = PairedCode(contract.code_repo_path, code_commit)
    problems: list[str] = []
    for checkout in owners_or_problem:
        if not (checkout / LAYOUT_MARKER_PATH).is_file():
            continue
        problem = refresh_written_memory_briefs(checkout, paired, contract.coordination_root)
        if problem is not None:
            problems.append(problem)
    return "; ".join(problems) if problems else None


def refresh_recorded_landing_memory_briefs(
    contract: WorktreeContract,
    *,
    code_commit: str,
    memory_commit: str,
) -> str | None:
    """Refresh matching checkouts on the target memory branch after ``worktree_record_landing``.

    Compares each checkout's actual ``HEAD`` against ``memory_commit`` so an unpulled local
    checkout is never refreshed against a newer landed code commit. Reports explicitly when no
    checked-out worktree has reached ``memory_commit`` yet.
    """

    memory_branch = contract.memory_source_branch
    if contract.memory_mode != "external" or contract.memory_repo_path is None or not memory_branch:
        return None
    if not memory_commit:
        return None
    target_commit = _resolve_commit(contract.memory_repo_path, memory_commit)
    owners_or_problem = _branch_owners(contract.memory_repo_path, memory_branch)
    if isinstance(owners_or_problem, str):
        return owners_or_problem
    paired = PairedCode(contract.code_repo_path, code_commit)
    matched = 0
    problems: list[str] = []
    for checkout in owners_or_problem:
        try:
            checkout_head = head_commit(checkout)
        except _GIT_ERRORS as error:
            problems.append(f"File briefs could not read HEAD of {checkout}: {error}")
            continue
        if checkout_head != target_commit:
            continue
        matched += 1
        if not (checkout / LAYOUT_MARKER_PATH).is_file():
            continue
        problem = refresh_written_memory_briefs(checkout, paired, contract.coordination_root)
        if problem is not None:
            problems.append(problem)
    if matched == 0 and not problems:
        return (
            f"No checked-out worktree on memory branch {memory_branch!r} is at landed memory "
            f"commit {target_commit[:12]} yet, so briefs/ was not refreshed; pull "
            f"{memory_branch!r} and run agents-remember memory-briefs --repo {contract.repo_name}."
        )
    return "; ".join(problems) if problems else None


def _memory_content_changed(
    memory_repo: Path, previous_commit: str | None, current_commit: str
) -> bool | str:
    if not current_commit:
        return False
    if previous_commit == current_commit:
        return False
    if not previous_commit:
        return True
    try:
        changed = require_git(
            memory_repo,
            [
                "diff",
                "--name-only",
                "--no-renames",
                previous_commit,
                current_commit,
                "--",
                ".",
                f":!{LEDGER_RELATIVE_PATH}",
            ],
        )
    except _GIT_ERRORS as error:
        return f"File briefs could not identify the landed memory diff: {error}"
    return bool(changed.strip())


def _branch_owners(memory_repo: Path, branch: str) -> tuple[Path, ...] | str:
    try:
        return branch_worktree_owners(memory_repo, branch)
    except _GIT_ERRORS as error:
        return f"File briefs could not list worktrees for memory branch {branch!r}: {error}"


def _resolve_commit(memory_repo: Path, ref: str) -> str:
    try:
        resolved = require_git(memory_repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"]).strip()
    except _GIT_ERRORS:
        return ref
    return resolved or ref


__all__ = [
    "refresh_integrated_memory_briefs",
    "refresh_recorded_landing_memory_briefs",
]
