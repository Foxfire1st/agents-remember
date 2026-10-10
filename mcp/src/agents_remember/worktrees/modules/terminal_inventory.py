"""No-remains terminal inventory for worktree cleanup and abandon.

MIK-R82 rule 5: a leaf enclosure with no locator, no worktree folder, and no
work branches finishes through ``worktree_abandon`` (never integrated) or
``worktree_cleanup`` (integrated) with nothing to archive and nothing to
delete. This module owns that probe and its already-absent inventory as one
authoritative implementation shared by both verbs: the reason the exception
applied, the worktree/branch entries the probe just proved, the response
annotation, and the surviving-contract readback that proves a later reader
can recover the why.

The exception is deliberately narrow -- folder-present, branch-present,
wrong-integration-state, and external-memory-remains cases all stay outside
it -- and it never adopts or archives: there is nothing left to adopt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class LocalBranchPresence:
    state: Literal["present", "absent", "error"]
    reason: str = ""


def local_branch_presence(repo: Path, branch: str) -> LocalBranchPresence:
    if not branch:
        return LocalBranchPresence("error", "branch is empty")
    result = run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    if result.returncode == 0:
        return LocalBranchPresence("present")
    if result.returncode == 1 and not result.stderr.strip():
        return LocalBranchPresence("absent")
    return LocalBranchPresence("error", result.stderr.strip() or "git ref query failed")


_TERMINAL_WITHOUT_REMAINS_INTEGRATION: dict[str, str] = {
    "worktree_abandon": "not-started",
    "worktree_cleanup": "completed",
}


def _memory_work_branch_absent(contract: WorktreeContract) -> bool:
    """The memory work branch is absent, or there is no external memory side."""

    if (
        contract.memory_mode != "external"
        or contract.memory_repo_path is None
        or not contract.memory_work_branch
    ):
        return True
    return (
        local_branch_presence(contract.memory_repo_path, contract.memory_work_branch).state
        == "absent"
    )


def terminal_without_remains_reason(contract: WorktreeContract, *, operation: str) -> str | None:
    """MIK-R82 rule 5: why this enclosure may finish with nothing to archive.

    Returns the recorded reason when the enclosure has no locator while its
    worktree folder and its work branches do not exist; otherwise ``None`` so
    the caller keeps the normal terminal path (including the adoption refusal
    for an enclosure with a folder still on disk). The integration pairing is
    exact: abandon only for a never-integrated leaf, cleanup only for an
    integrated one. Adoption stays explicit: this never adopts, it only
    recognizes that there is nothing left to adopt.
    """

    if (
        contract.integration_status == _TERMINAL_WITHOUT_REMAINS_INTEGRATION.get(operation)
        and contract.kind == "leaf"
        and inspect_lifecycle_operation_locator(
            contract.coordination_root, contract.contract_path
        ).state
        == "missing"
        and not contract.worktree_group.exists()
        and all(
            worktree is None or not worktree.exists()
            for worktree in (contract.code_worktree, contract.memory_worktree)
        )
        and local_branch_presence(contract.code_repo_path, contract.code_work_branch).state
        == "absent"
        and _memory_work_branch_absent(contract)
    ):
        return (
            "MIK-R82 rule 5: no locator is published for this enclosure and its "
            "worktree folder and work branches do not exist; there was nothing to "
            "archive and nothing to delete."
        )
    return None


def _absent_worktree_entries(
    contract: WorktreeContract,
) -> dict[str, dict[str, object]]:
    """The already-absent worktree entries the probe above just proved."""

    entries = {
        "code": {
            "path": contract.code_worktree.as_posix(),
            "removed": False,
            "reason": "already-absent",
        }
    }
    if contract.memory_mode == "external" and contract.memory_worktree is not None:
        entries["memory"] = {
            "path": contract.memory_worktree.as_posix(),
            "removed": False,
            "reason": "already-absent",
        }
    return entries


def _absent_branch_entries(
    contract: WorktreeContract,
) -> dict[str, dict[str, object]]:
    """The already-absent branch entries the probe above just proved."""

    entries = {
        "code": {
            "branch": contract.code_work_branch,
            "deleted": False,
            "reason": "already-absent",
        }
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_work_branch
    ):
        entries["memory"] = {
            "branch": contract.memory_work_branch,
            "deleted": False,
            "reason": "already-absent",
        }
    return entries


def _mark_without_remains(
    result: WorktreeCommandResult, reason: str, *, summary: str
) -> WorktreeCommandResult:
    """Record why the contract finished with nothing to archive or delete."""

    return WorktreeCommandResult(
        result.returncode,
        {
            **result.payload,
            "summary": summary,
            "reason": reason,
            "nothingToArchive": True,
            "nothingToDelete": True,
            "locatorState": "missing",
        },
    )


def _require_terminal_note_readback(contract_path: Path, terminal_note: str) -> str | None:
    """Confirm the surviving contract still carries the no-remains why.

    The contract-owned terminal publication just wrote the note alongside the
    terminal cleanup state; reading the exact file bytes back proves a later
    reader can recover why the pre-locator exception was valid.
    """

    try:
        text = contract_path.read_text(encoding="utf-8")
    except OSError as error:
        return f"terminal note readback failed: {error}"
    if terminal_note not in text:
        return "terminal note missing from surviving contract after publication"
    return None
