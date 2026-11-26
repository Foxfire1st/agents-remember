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
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

from agents_remember.cli.role_handover_file import HANDOVER_FILE_SUFFIX
from agents_remember.cli.role_instruction_links import relative_destinations
from agents_remember.cli.role_message_files import (
    MESSAGES_DIRECTORY,
    MESSAGES_LINK,
    leaf_messages_folder,
)
from agents_remember.errors import AgentsRememberError, AuthorityError
from agents_remember.kernel.atomic_write import atomic_replace
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel
from agents_remember.memory_quality.style.citations.model import code_span_texts
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.role_handover_locator import (
    locator_directory,
    locator_root,
    remove_locator,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

ROLE_REPORT_DIRECTORY = "role-reports"
LEGACY_ROLE_REPORT_ALIAS = "task-reports"
COLLECTED_REPORT_DIRECTORY = "notes/reports"
COLLECTED_REPORT_PARTS = PurePosixPath(COLLECTED_REPORT_DIRECTORY).parts
# The locators the launches of the folders being removed published; only the aliases go.
HANDOVER_LOCATOR_DIRECTORY = "handover-locators"
MAX_COLLECTION_FILES = 10_000
MAX_COLLECTION_BYTES = 2 * 1024**3


def collected_report_ref(relative: str | PurePath) -> str:
    """Map a path relative to ``role-reports/`` to its collected task-relative form."""

    raw = relative.as_posix() if isinstance(relative, PurePath) else relative
    cleaned = raw.strip().lstrip("/")
    parts = PurePosixPath(cleaned).parts
    if not parts or ".." in parts:
        raise ValueError("collected_report_ref requires a relative path inside role-reports/")
    return f"{COLLECTED_REPORT_DIRECTORY}/{'/'.join(parts)}"


def is_collected_report_ref(reference: str) -> bool:
    """Whether ``reference`` is already a task-relative ``notes/reports/<rel>`` path."""

    raw = reference.strip()
    if not raw or Path(raw).is_absolute():
        return False
    parts = PurePosixPath(raw).parts
    return (
        len(parts) > len(COLLECTED_REPORT_PARTS)
        and parts[: len(COLLECTED_REPORT_PARTS)] == COLLECTED_REPORT_PARTS
        and ".." not in parts
    )


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
            relative = path.relative_to(source).as_posix()
            target = destination / relative
            _check_target(target)
            if dry_run:
                continue
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
    if any(ancestor.is_symlink() for ancestor in (*reversed(target.parents), target)):
        raise RoleReportCollectionError(
            f"Canonical report target {target} contains a link; reconcile it before collection"
        )
    if any(ancestor.exists() and not ancestor.is_dir() for ancestor in target.parents):
        raise RoleReportCollectionError(
            f"Canonical report target {target} has a parent that is not a directory"
        )
    if target.exists() and not target.is_file():
        raise RoleReportCollectionError(f"Canonical report target {target} is not a regular file")
    existing_parent = next((parent for parent in target.parents if parent.exists()), None)
    if existing_parent is not None and not os.access(existing_parent, os.W_OK | os.X_OK):
        raise RoleReportCollectionError(
            f"Canonical report directory {existing_parent} cannot be written"
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


def canonical_execution_reports(receipt: Mapping[str, Any], reports_root: Path) -> Iterator[Path]:
    """Read the role's canonical report collection after local folders and caches are gone."""

    workspace = receipt.get("workspace")
    canonical = workspace.get("taskReportRoot") if isinstance(workspace, dict) else None
    if not isinstance(canonical, str):
        return
    try:
        relative = Path(canonical).relative_to(reports_root).as_posix()
        root = reports_root / confine_non_symlink_rel(reports_root, relative)
    except (AuthorityError, OSError, ValueError):
        return

    report = receipt.get("report")
    primary = report.get("canonicalPath") if isinstance(report, dict) else None
    launch_report = Path(primary) if isinstance(primary, str) else None
    measurements: set[Path] = set()
    citations: set[Path] = set()
    for path in _report_candidates(root, launch_report):
        if path.suffix.lower() == ".json":
            measurements.add(path)
        else:
            yield path
            citations.update(_measurement_citations(path, root))
    yield from measurements & citations


def _report_candidates(root: Path, launch_report: Path | None) -> Iterator[Path]:
    """Select report text and possible structured measurements inside the collection."""

    for path in root.rglob("*", recurse_symlinks=False):
        if path.suffix.lower() not in {".md", ".txt", ".json"}:
            continue
        if path.is_symlink() or not path.is_file() or _launch_carrier(path, launch_report):
            continue
        yield path


def _launch_carrier(path: Path, launch_report: Path | None) -> bool:
    if launch_report is None or path.parent != launch_report.parent:
        return False
    return (
        path == launch_report.with_suffix(".handover.txt")
        or path == launch_report.with_suffix(HANDOVER_FILE_SUFFIX)
        or path.name.startswith(f"{launch_report.stem}.detail-")
    )


def _measurement_citations(report: Path, root: Path) -> Iterator[Path]:
    """Read explicit JSON references and let the selected collection supply their bytes."""

    try:
        with report.open(encoding="utf-8") as stream:
            for line in stream:
                if ".json" in line.lower():
                    yield from _line_measurement_citations(line, report.parent, root)
    except (OSError, UnicodeError):
        return


def _line_measurement_citations(line: str, directory: Path, root: Path) -> Iterator[Path]:
    for span in code_span_texts(line):
        if Path(span).suffix.lower() == ".json":
            # Worker notes use request-relative paths, and nested reports also cite neighboring files.
            yield Path(os.path.normpath(root / span))
            yield Path(os.path.normpath(directory / span))
    for start, end in relative_destinations(line):
        target = line[start:end]
        if Path(target).suffix.lower() == ".json":
            yield Path(os.path.normpath(directory / target))


def collect_group_reports(contract: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
    canonical = contract.task_root / COLLECTED_REPORT_DIRECTORY
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


def canonical_group_messages(contract: WorktreeContract) -> Path:
    if contract.kind == "series":
        return contract.task_root / "notes" / MESSAGES_DIRECTORY
    leaf = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    if leaf is None:
        raise AuthorityError(
            f"No leaf document for {contract.leaf_id!r} identifies the canonical messages folder"
        )
    path, _document = leaf
    return leaf_messages_folder(path.parent, path.name)


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


def remove_group_report_locators(contract: WorktreeContract) -> None:
    _remove_group_locators(contract, dry_run=False)


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
    # Directory-only sprint archive retirement unlinks the message alias.
    # Whole-folder terminal reclamation preserves real messages before removal.
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
