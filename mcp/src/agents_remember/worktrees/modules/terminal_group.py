"""Checked move-out and removal of a terminal task's working folder."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import AuthorityError
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.group_messages import (
    GroupMessageCollectionError,
    collect_group_messages,
)
from agents_remember.worktrees.modules.filesystem import remove_tree
from agents_remember.worktrees.modules.kept_tool_calls import keep_tool_calls_for_group
from agents_remember.worktrees.modules.terminal_agents import (
    archive_terminal_agents,
    terminal_agent_blockers,
)
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalResult,
    legacy_series_reports_is_child_enclosure,
    terminal_result_blockers,
)
from agents_remember.worktrees.role_reports import (
    RoleReportCollectionError,
    collect_group_reports,
    remove_group_report_locators,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

type TerminalItems = dict[str, dict[str, object]]

ENCLOSURE_REPORTS_DIRECTORY = "reports"
ENCLOSURE_LOGS_DIRECTORY = "logs"


def legacy_series_group(contract: WorktreeContract) -> bool:
    return (
        contract.kind == "series" and contract.worktree_group == contract.task_root / "enclosures"
    )


def preview_group_removal(contract: WorktreeContract) -> TerminalItems:
    if legacy_series_group(contract):
        return legacy_series_directories(contract, dry_run=True)
    group = _group_observation(contract)
    if not group.get("would_remove"):
        return {
            "worktree_group": group,
            **outside_scratch.removal_entry_for(contract, dry_run=True),
        }
    blocker = _directory_blocker(contract)
    if blocker is not None:
        return {"worktree_group": blocker}
    return {
        "worktree_group": group,
        **_working_child_rows(contract, "would_remove"),
        **outside_scratch.removal_entry_for(contract, dry_run=True),
        **_move_out(contract, dry_run=True),
    }


def prepare_group_removal(contract: WorktreeContract) -> TerminalItems:
    return _move_out(contract, dry_run=False)


def _collect_before_removal(
    contract: WorktreeContract,
) -> tuple[dict[str, object], TerminalItems]:
    try:
        collect_group_reports(contract, dry_run=False)
    except (OSError, RoleReportCollectionError) as error:
        return {}, {"role_reports": _refused(contract.worktree_group, str(error))}
    return keep_tool_calls_for_group(contract, dry_run=False)


def remove_terminal_group(contract: WorktreeContract) -> TerminalItems:
    if legacy_series_group(contract):
        return legacy_series_directories(contract, dry_run=False)
    group = _group_observation(contract)
    if not group.get("would_remove"):
        extra = (
            outside_scratch.removal_entry_for(contract, dry_run=False)
            if group.get("reason") == "already-absent"
            else {}
        )
        return {"worktree_group": group, **extra}
    kept, move_out_blocker = _collect_before_removal(contract)
    if move_out_blocker:
        return move_out_blocker
    outside = outside_scratch.removal_entry_for(contract, dry_run=False)
    if group_directory_blockers(outside, dry_run=False):
        return outside
    try:
        remove_group_report_locators(contract)
    except OSError as error:
        return {**outside, "role_reports": _refused(contract.worktree_group, str(error))}
    reports = _working_child_rows(contract, "removed")
    if kept and ENCLOSURE_LOGS_DIRECTORY in reports:
        reports[ENCLOSURE_LOGS_DIRECTORY] = {**reports[ENCLOSURE_LOGS_DIRECTORY], **kept}
    try:
        removed = remove_tree(contract.worktree_group, dry_run=False)
    except OSError as error:
        removed = _refused(contract.worktree_group, str(error))
    return {
        **outside,
        **(reports if removed.get("removed") else {}),
        "worktree_group": removed,
    }


def group_directory_blockers(
    directories: TerminalItems, *, dry_run: bool
) -> list[dict[str, object]]:
    return terminal_result_blockers(
        TerminalResult(worktrees={}, branches={}, directories=directories, preview=dry_run)
    )


def _group_observation(contract: WorktreeContract) -> dict[str, object]:
    group = contract.worktree_group
    try:
        if any(path.is_symlink() for path in reversed(group.parents)):
            raise AuthorityError(f"Registered task folder {group} has a linked parent directory")
        mode = group.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise AuthorityError(f"Registered task folder {group} is not a real directory")
        if group == contract.coordination_root / "worktrees" / contract.repo_name:
            raise AuthorityError(f"{group} is the repository's shared worktree folder")
        if not os.access(group.parent, os.W_OK | os.X_OK):
            raise AuthorityError(f"The task folder's parent {group.parent} cannot be written")
    except FileNotFoundError:
        return {"path": group.as_posix(), "removed": False, "reason": "already-absent"}
    except (OSError, AuthorityError) as error:
        return _refused(group, str(error))
    return {"path": group.as_posix(), "would_remove": True, "removed": False}


def _directory_blocker(contract: WorktreeContract) -> dict[str, object] | None:
    """Inspect directories owned by the final recursive removal."""

    separate_removals: set[Path] = set()
    if contract.kind == "leaf":
        separate_removals.add(contract.code_worktree)
        if contract.memory_worktree is not None:
            separate_removals.add(contract.memory_worktree)
    pending = [contract.worktree_group]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                can_remove_children = os.access(directory, os.W_OK | os.X_OK)
                for entry in entries:
                    if not can_remove_children:
                        return _refused(
                            directory, f"Entries in task directory {directory} cannot be removed"
                        )
                    path = Path(entry.path)
                    if path not in separate_removals and entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except OSError as error:
            return _refused(directory, str(error))
    return None


def _working_child_rows(contract: WorktreeContract, key: str) -> TerminalItems:
    # These rows describe the result of whole-folder removal to existing report consumers.
    paths = (
        contract.worktree_group / ENCLOSURE_REPORTS_DIRECTORY,
        contract.worktree_group / ENCLOSURE_LOGS_DIRECTORY,
    )
    return {
        path.name: {"path": path.as_posix(), key: True}
        for path in paths
        if path.is_symlink() or path.exists()
    }


def _move_out(contract: WorktreeContract, *, dry_run: bool) -> TerminalItems:
    try:
        collect_group_reports(contract, dry_run=dry_run)
    except RoleReportCollectionError as error:
        return {"role_reports": _refused(contract.worktree_group, str(error))}
    if legacy_series_group(contract):
        return {}
    try:
        collect_group_messages(contract, dry_run=dry_run)
    except (OSError, GroupMessageCollectionError) as error:
        return {"messages": _refused(contract.worktree_group / "messages", str(error))}
    _kept, log_blocker = keep_tool_calls_for_group(contract, dry_run=dry_run)
    return log_blocker


def _refused(path: Path, reason: str) -> dict[str, object]:
    return {"path": path.as_posix(), "removed": False, "reason": reason}


def legacy_series_directories(contract: WorktreeContract, *, dry_run: bool) -> TerminalItems:
    group = contract.worktree_group
    reports = group / ENCLOSURE_REPORTS_DIRECTORY
    row = (
        {
            "path": reports.as_posix(),
            "removed": False,
            "reason": "child-enclosure",
            "preserved": True,
        }
        if legacy_series_reports_is_child_enclosure(contract)
        else remove_tree(reports, dry_run=dry_run)
    )
    remove_tree(group / ENCLOSURE_LOGS_DIRECTORY, dry_run=dry_run)
    provider_runtime = remove_tree(group / "provider-runtime", dry_run=dry_run)
    series_dirs: TerminalItems = {
        "reports": row,
        "lifecycle": remove_tree(group / ".lifecycle", dry_run=dry_run),
        **outside_scratch.removal_entry_for(contract, dry_run=dry_run),
    }
    if provider_runtime.get("reason") != "already-absent":
        series_dirs["provider_runtime"] = provider_runtime
    return series_dirs


@dataclass(frozen=True)
class GroupPreparation:
    directories: TerminalItems
    agent_archive: dict[str, object]
    blockers: list[dict[str, object]]


def prepare_terminal_group(contract: WorktreeContract, *, dry_run: bool) -> GroupPreparation:
    directories = preview_group_removal(contract)
    blockers = group_directory_blockers(directories, dry_run=True)
    if blockers:
        return GroupPreparation(directories, {}, blockers)
    archive = (
        {} if legacy_series_group(contract) else archive_terminal_agents(contract, dry_run=dry_run)
    )
    if directories.get("worktree_group", {}).get("would_remove"):
        blockers = terminal_agent_blockers(archive)
        if blockers:
            return GroupPreparation(directories, archive, blockers)
    if not dry_run:
        move_out = prepare_group_removal(contract)
        blockers = group_directory_blockers(move_out, dry_run=False)
        if blockers:
            return GroupPreparation(move_out, archive, blockers)
    return GroupPreparation(directories, archive, [])
