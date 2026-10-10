"""Sprint-archive record I/O and slot reconstruction (MIK-R82 rule 1).

The record ``notes/reports/sprint-archive.json`` is written into the sprint folder
before the first folder moves, moves with the sprint folder under ``0_archive/``,
and protects the archive the way a retirement record protects a retirement: a
repeated request is compared with it by reason and selection, and a request that
differs is refused and changes nothing.

This module sustains the single authoritative implementation of record location,
record reads, record construction, and recorded-slot reconstruction; the archive
operation imports these rather than carrying copies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.models.sprint_archive import (
    RECORD_NAME,
    SprintArchiveMasterEntry,
    SprintArchiveRecord,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc_with_source
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import ARCHIVE_DIR

from .task_retirement_shared import digest, optional_digest
from .task_sprint_candidates import SprintLinkageError

if TYPE_CHECKING:
    from .task_sprint_context import SprintLinkageRequest


@dataclass(frozen=True)
class _MasterSlot:
    """One commanded master: where its folder is, and whether it moves or stays."""

    master: ResolvedTaskDocument
    staying: bool
    stay_reason: str | None
    live: Path
    archive: Path


@dataclass(frozen=True)
class _Located:
    """The sprint's live and archive folders, and which task document exists."""

    name: str
    live_dir: Path
    archive_dir: Path
    live_json: Path
    archive_json: Path
    live_exists: bool


@dataclass(frozen=True)
class _Reading:
    """The sprint document as read now, with where it was read from."""

    located: _Located
    sprint_ref: TaskDocumentRef
    sprint: ResolvedTaskDocument
    source: Any


def _record_location(live_dir: Path, archive_dir: Path) -> Path | None:
    for folder in (live_dir, archive_dir):
        stored = folder / "notes" / "reports" / RECORD_NAME
        try:
            if stored.is_file():
                return stored
        except OSError:
            return stored
    return None


def _read_record(live_dir: Path, archive_dir: Path) -> SprintArchiveRecord | None:
    found: SprintArchiveRecord | None = None
    for folder in (live_dir, archive_dir):
        stored = folder / "notes" / "reports" / RECORD_NAME
        try:
            exists = stored.is_file()
        except OSError:
            exists = True
        if not exists:
            continue
        try:
            record = SprintArchiveRecord.model_validate_json(stored.read_bytes())
        except (OSError, ValueError) as exc:
            raise SprintLinkageError(
                f"sprint archive record {stored.as_posix()} is unreadable: {exc}; restore "
                "the admitted record before retrying task_doc.archive_sprint"
            ) from exc
        if found is not None:
            raise SprintLinkageError(
                f"sprint archive has more than one record: {live_dir.as_posix()} and "
                f"{archive_dir.as_posix()} both hold {RECORD_NAME}. A sprint archive has "
                "exactly one; remove the surplus record by hand, keeping the one whose "
                "request is to be repeated, before task_doc.archive_sprint"
            )
        found = record
    return found


def _read_sprint(name: str, json_path: Path) -> tuple[ResolvedTaskDocument, Any]:
    try:
        document, source = read_task_doc_with_source(json_path)
    except (OSError, ValueError) as exc:
        raise SprintLinkageError(
            f"sprint {name!r}: its task document {json_path.as_posix()} is missing or "
            f"cannot be read ({type(exc).__name__}: {exc}); restore or repair that file "
            "before task_doc.archive_sprint"
        ) from exc
    if not document.is_sprint:
        if document.kind == "master":
            raise SprintLinkageError(
                f"{name!r} is a master, not a sprint: task_doc.archive_sprint archives an "
                "orchestration sprint as a whole. Retire that master with "
                "task_doc.retire_master instead"
            )
        raise SprintLinkageError(
            "task_doc.archive_sprint requires an orchestration sprint document"
        )
    return ResolvedTaskDocument(
        TaskDocumentRef(repository=document.repo, path=f"{name}/{json_path.name}"),
        json_path,
        document,
    ), source


def _new_record(
    reading: _Reading,
    reason: str,
    slots: tuple[_MasterSlot, ...],
    facts: tuple[str, ...],
) -> SprintArchiveRecord:
    located, source = reading.located, reading.source
    moving = [slot for slot in sorted(slots, key=lambda slot: slot.master.ref.key)]
    return SprintArchiveRecord(
        sprintRef=reading.sprint_ref,
        reason=reason,
        archivedAt=datetime.now(UTC).isoformat(timespec="seconds"),
        sprintFolder=located.live_dir.name,
        sprintArchivePath=located.archive_dir.as_posix(),
        sprintJsonSha256=digest(source.json_bytes or b""),
        sprintMarkdownSha256=optional_digest(source.markdown_bytes),
        masters=[
            SprintArchiveMasterEntry(
                masterRef=slot.master.ref,
                folder=slot.live.name,
                archivePath=slot.archive.as_posix(),
                staying=slot.staying,
                stayReason=slot.stay_reason,
            )
            for slot in moving
        ],
        moveOrder=[located.live_dir.name] + [slot.live.name for slot in moving if not slot.staying],
        readinessFacts=list(facts),
    )


def _recorded_slots(
    request: SprintLinkageRequest, record: SprintArchiveRecord
) -> tuple[_MasterSlot, ...]:
    """The slots of a retained record: exact live reads, else the record-bound archive copy.

    The archived side is read only at the record's own archive paths (R83 ordinary
    invisibility holds: no archive lookup is consulted). A master with no readable
    task document on either side, or one on both, is refused with its exact paths
    and clearing action; no stand-in document is ever invented.
    """

    tasks_root = request.coordination_root / "tasks" / request.repo_id
    archive_root = tasks_root / ARCHIVE_DIR
    slots: list[_MasterSlot] = []
    for entry in record.masters:
        live = tasks_root / entry.folder
        archive = tasks_root / ARCHIVE_DIR / entry.folder
        live_doc = tasks_root / entry.masterRef.path
        archive_doc = archive_root / entry.masterRef.path
        live_has, archive_has = live_doc.is_file(), archive_doc.is_file()
        if live_has and archive_has:
            raise SprintLinkageError(
                f"sprint archive cannot resume: master {entry.masterRef.key} has a task "
                f"document at both {live_doc.as_posix()} and {archive_doc.as_posix()}; "
                "reconcile that duplication before retrying task_doc.archive_sprint"
            )
        if not live_has and not archive_has:
            raise SprintLinkageError(
                f"sprint archive cannot resume: master {entry.masterRef.key} has no task "
                f"document at either {live_doc.as_posix()} or {archive_doc.as_posix()}; "
                "restore that master's folder before retrying task_doc.archive_sprint"
            )
        found = live_doc if live_has else archive_doc
        try:
            document, _source = read_task_doc_with_source(found)
        except (OSError, ValueError) as exc:
            raise SprintLinkageError(
                f"sprint archive cannot resume: master {entry.masterRef.key} task document "
                f"{found.as_posix()} cannot be read ({type(exc).__name__}: {exc}); restore "
                "or repair that file before retrying task_doc.archive_sprint"
            ) from exc
        slots.append(
            _MasterSlot(
                master=ResolvedTaskDocument(entry.masterRef, found, document),
                staying=entry.staying,
                stay_reason=entry.stayReason,
                live=live,
                archive=archive,
            )
        )
    return tuple(slots)


def _summarized_cleanup_changes(cleanups: list[dict[str, Any]], failed: bool) -> list[str]:
    """Bounded cleanup preview: exact counts with pointers at the per-folder detail.

    The machine-exact per-folder lists stay under ``taskArchive.cleanups``; these
    lines summarize them without silent truncation so whole-sprint populations fit
    the public response bounds.
    """

    remainder_folders = sum(1 for item in cleanups if item["remainder"])
    receipt_folders = sum(1 for item in cleanups if item["receipt_line"] is not None)
    changes: list[str] = []
    if remainder_folders:
        changes.append(
            f"review artifacts: delete pending artifacts in {remainder_folders} of "
            f"{len(cleanups)} archived folders (exact per-folder lists under "
            "taskArchive.cleanups)"
        )
    if receipt_folders:
        changes.append(
            f"cleanup receipts: write {receipt_folders} of {len(cleanups)} folder "
            "receipts (exact paths under taskArchive.cleanups)"
        )
    if failed:
        changes.append(
            "review artifacts: retry what the hook cannot reach (listed under "
            "taskArchive.cleanups[].reviewArtifacts.failures)"
        )
    return changes


def _summarized_cleanup_writes(cleanups: list[dict[str, Any]]) -> list[str]:
    total = sum(len(item["receipts"]) for item in cleanups)
    if not total:
        return []
    return [
        f"{total} cleanup receipt files across {len(cleanups)} archived folders "
        "(exact paths under taskArchive.cleanups)"
    ]
