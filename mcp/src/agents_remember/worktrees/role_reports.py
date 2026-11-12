"""Collect agent-owned reports before their working folders can be reclaimed.

Changed copies are refreshed atomically; sources remain available for recovery. Collection is bounded
to 10,000 files/directories and 2 GiB per call. Oversize evidence must be archived in canonical notes first.
No failed or incomplete collection admits folder removal. Legacy aliases are only unlinked.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agents_remember.cli.role_message_files import MESSAGES_LINK
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.atomic_write import atomic_replace
from agents_remember.worktrees.role_handover_locator import (
    locator_directory,
    locator_root,
    remove_locator,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

ROLE_REPORT_DIRECTORY = "role-reports"
LEGACY_ROLE_REPORT_ALIAS = "task-reports"
# The locators the launches of the folders being removed published; only the aliases go.
HANDOVER_LOCATOR_DIRECTORY = "handover-locators"
MAX_COLLECTION_FILES = 10_000
MAX_COLLECTION_BYTES = 2 * 1024**3


class RoleReportCollectionError(AgentsRememberError):
    """Collection did not prove durable report copies; preserve the source folders."""


def collect_report_folder(
    source: Path,
    destination: Path,
    *,
    dry_run: bool = False,
    file_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source.is_symlink():
        if source.resolve() != destination.resolve():
            raise RoleReportCollectionError(
                f"Recorded report alias {source} no longer leads to {destination}; restore its recorded target before collection"
            )
        return {"state": "legacy-canonical", "source": str(source), "canonical": str(destination)}
    if not source.exists():
        return {"state": "unwritten", "source": str(source), "canonical": str(destination)}
    if source.resolve() == destination.resolve():
        return {"state": "canonical", "source": str(source), "canonical": str(destination)}
    copied = 0
    try:
        paths, size = _collection_inventory(source)
        _prune_file_state(source, paths, file_state, dry_run=dry_run)
        for path in paths:
            if dry_run:
                continue
            relative = path.relative_to(source).as_posix()
            target = destination / relative
            _check_target(target)
            stamps = {"source": _file_stamp(path), "canonical": _file_stamp(target)}
            if file_state is not None and file_state.get(relative) == stamps:
                continue
            stamps = _copy_report(path, target)
            copied += 1
            if file_state is not None:
                file_state[relative] = stamps
    except OSError as error:
        raise RoleReportCollectionError(
            f"Collect {source} into {destination} before removal: {error}; source folders were preserved"
        ) from error
    return {
        "state": "would-collect" if dry_run else "collected",
        "source": str(source),
        "canonical": str(destination),
        "files": len(paths),
        "bytes": size,
        "copiedFiles": copied,
    }


def _prune_file_state(
    source: Path, paths: list[Path], file_state: dict[str, Any] | None, *, dry_run: bool
) -> None:
    if file_state is None or dry_run:
        return
    present = {path.relative_to(source).as_posix() for path in paths}
    for relative in file_state.keys() - present:
        del file_state[relative]


def _collection_inventory(source: Path) -> tuple[list[Path], int]:
    # Validate the bounded inventory before publishing any part of it.
    paths: list[Path] = []
    size = 0
    for path in _report_files(source):
        paths.append(path)
        size += path.stat().st_size
        if len(paths) > MAX_COLLECTION_FILES or size > MAX_COLLECTION_BYTES:
            raise RoleReportCollectionError(
                f"Report folder {source} exceeds the collection budget; archive oversized evidence in canonical notes before retrying collection"
            )
    return paths, size


def _report_files(source: Path) -> Iterator[Path]:
    # scandir streams each directory: no list of an unbounded fanout is allocated.
    pending = [source]
    entries_seen = 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > MAX_COLLECTION_FILES:
                    raise RoleReportCollectionError(
                        f"Report folder {source} exceeds the file/directory collection budget; archive its evidence in canonical notes before retrying"
                    )
                path = Path(entry.path)
                if entry.is_symlink():
                    raise RoleReportCollectionError(
                        f"Report evidence {path} is a link; preserve it and archive its intended evidence in canonical notes"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path
                else:
                    raise RoleReportCollectionError(
                        f"Report evidence {path} is not a regular file; preserve it and reconcile before collection"
                    )


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_target(target: Path) -> None:
    if any(ancestor.is_symlink() for ancestor in (target, *target.parents)):
        raise RoleReportCollectionError(
            f"Canonical report target {target} contains a link; reconcile it before collection"
        )


def _file_stamp(path: Path) -> list[int] | None:
    if not path.exists():
        return None
    value = path.stat()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def _copy_report(source: Path, target: Path) -> dict[str, Any]:
    _check_target(target)
    before = _file_stamp(source)
    digest = _file_digest(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".role-report-", delete=False
    ) as temporary:
        staged = Path(temporary.name)
    try:
        shutil.copyfile(source, staged)
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
        if _file_digest(staged) != digest or _file_stamp(source) != before:
            raise RoleReportCollectionError(
                f"Report {source} changed during collection; preserve it and retry after its writer finishes"
            )
        atomic_replace(staged, target)
        canonical_stamp = _file_stamp(target)
        if _file_digest(target) != digest or _file_stamp(target) != canonical_stamp:
            raise RoleReportCollectionError(
                f"Report {target} failed durable readback; preserve {source} and retry collection"
            )
        return {"source": before, "canonical": canonical_stamp}
    finally:
        staged.unlink(missing_ok=True)


def collect_execution_reports(receipt: dict[str, Any]) -> dict[str, Any]:
    workspace = receipt.get("workspace", {})
    local = workspace.get("taskReportAccessRoot")
    canonical = workspace.get("taskReportRoot")
    if not local or not canonical:
        receipt.pop("reportCollectionFiles", None)
        return {"state": "legacy-direct", "report": receipt.get("report")}
    if (
        receipt.get("reportCollection", {}).get("state") == "collected-closed"
        and not Path(local).exists()
        and not Path(local).is_symlink()
    ):
        receipt.pop("reportCollectionFiles", None)
        return receipt["reportCollection"]
    result = collect_report_folder(
        Path(local), Path(canonical), file_state=receipt.setdefault("reportCollectionFiles", {})
    )
    if result["state"] != "collected":
        receipt.pop("reportCollectionFiles", None)
    receipt["reportCollection"] = result
    return result


def collect_group_reports(contract: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
    canonical = contract.task_root / "notes" / "reports"
    result = collect_report_folder(
        contract.worktree_group / ROLE_REPORT_DIRECTORY, canonical, dry_run=dry_run
    )
    alias = contract.worktree_group / LEGACY_ROLE_REPORT_ALIAS
    if alias.is_symlink():
        collect_report_folder(alias, canonical, dry_run=dry_run)
    elif alias.exists():
        raise RoleReportCollectionError(
            f"Legacy access {alias} is no longer an alias; preserve it and reconcile its recorded layout before removal"
        )
    return result


def _remove_group_locators(contract: WorktreeContract, *, dry_run: bool) -> dict[str, object]:
    """Remove the launches' locators of this group's role folders, and only those aliases.

    Each request folder under ``role-reports/<role>/`` names the launch whose handover it holds,
    and that launch's locator is the alias of its report folder. The report folders themselves
    are collected and removed by this call's own steps; a locator that leads anywhere else is
    left standing and reported.
    """

    reports = contract.worktree_group / ROLE_REPORT_DIRECTORY
    entries: list[dict[str, Any]] = []
    if reports.is_dir() and not reports.is_symlink():
        for role_folder in sorted(reports.iterdir()):
            if not role_folder.is_dir() or role_folder.is_symlink():
                continue
            for request_folder in sorted(role_folder.iterdir()):
                if request_folder.is_dir():
                    entries.append(
                        remove_locator(
                            locator_directory(request_folder.name),
                            within=contract.worktree_group,
                            dry_run=dry_run,
                        )
                    )
    removed = [entry for entry in entries if entry["reason"] is None]
    return {
        "path": locator_root().as_posix(),
        "would_remove" if dry_run else "removed": bool(removed),
        "reason": None if removed else "already-absent",
    }


def remove_group_role_folders(
    contract: WorktreeContract, *, dry_run: bool
) -> dict[str, dict[str, object]]:
    collect_group_reports(contract, dry_run=dry_run)
    result: dict[str, dict[str, object]] = {}
    result[HANDOVER_LOCATOR_DIRECTORY] = _remove_group_locators(contract, dry_run=dry_run)
    for name in (ROLE_REPORT_DIRECTORY, "scratch", LEGACY_ROLE_REPORT_ALIAS, "task-documents"):
        path = contract.worktree_group / name
        present = path.exists() or path.is_symlink()
        if present and not dry_run:
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path)
        result[name] = {
            "path": str(path),
            "would_remove" if dry_run else "removed": present,
            "reason": None if present else "already-absent",
        }
    # MIK-R117: cleanup of a workspace removes the messages link. No cleanup
    # deletes a message file: a real directory or file at the link's name is
    # left alone and reported.
    link = contract.worktree_group / MESSAGES_LINK
    if link.is_symlink():
        if not dry_run:
            link.unlink()
        result[MESSAGES_LINK] = {
            "path": str(link),
            "would_remove" if dry_run else "removed": True,
            "reason": None,
        }
    elif link.exists():
        result[MESSAGES_LINK] = {
            "path": str(link),
            "would_remove" if dry_run else "removed": False,
            "reason": "not-a-link-preserved",
        }
    else:
        result[MESSAGES_LINK] = {
            "path": str(link),
            "would_remove" if dry_run else "removed": False,
            "reason": "already-absent",
        }
    return result


def close_execution_role_folders(receipt: dict[str, Any]) -> None:
    """Only a confirmed archived/replaced non-leaf execution admits this action."""
    workspace = receipt.get("workspace", {})
    if "contractPath" in workspace or "scratchRoot" not in workspace:
        return
    execution = (
        Path(workspace["path"]) / ".agents-remember" / "role-executions" / receipt["requestId"]
    )
    if (
        Path(workspace["scratchRoot"]) != execution / "scratch"
        or Path(workspace["taskReportAccessRoot"]) != execution / ROLE_REPORT_DIRECTORY
    ):
        raise RoleReportCollectionError(
            "Execution folder differs from its recorded layout; preserve it and reconcile before removal"
        )
    collection = collect_execution_reports(receipt)
    if execution.is_symlink():
        raise RoleReportCollectionError(
            f"Execution folder {execution} became a link; preserve it before removal"
        )
    # The launch's locator goes with the folder it leads into, and only the alias does: the
    # report folder inside `execution` is collected above and removed below.
    remove_locator(locator_directory(str(receipt["requestId"])), within=execution)
    if execution.exists():
        shutil.rmtree(execution)
    receipt["reportCollection"] = {**collection, "state": "collected-closed"}
    receipt.pop("reportCollectionFiles", None)
