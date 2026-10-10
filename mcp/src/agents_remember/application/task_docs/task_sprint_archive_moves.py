"""Sprint-archive first phase: record write, ordered moves, and rollback (MIK-R82 rule 1).

What moves: the sprint's own folder moves first, then every moving commanded
master's folder in a deterministic recorded order, all under
``tasks/<repository>/0_archive/``.

A failure before the moves end restores the moved masters first and the sprint
last, then removes the record this request wrote, so a request leaves the state
it found or a later one, never something in between. A resumed retry restores
from the record's own move order rather than the current invocation's moves.

This module sustains the single authoritative implementation of the
move/rollback sequence; the archive operation imports it rather than carrying
a copy.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.sprint_archive import RECORD_NAME, SprintArchiveRecord
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.sprint_rows import sprint_census
from agents_remember.tasks.task_paths import ARCHIVE_DIR

from .task_sprint_archive_records import _MasterSlot
from .task_sprint_candidates import SprintLinkageError

if TYPE_CHECKING:
    from .task_sprint_archive import ArchivePlan
    from .task_sprint_context import SprintLinkageRequest


@dataclass(frozen=True)
class _RecordJournal:
    """What the first phase must undo when a move fails: the record it wrote, if any."""

    stored: Path
    created: list[Path]
    archive_root_existed: bool
    new_record: bool


def _write_record_file(stored: Path, record: SprintArchiveRecord) -> None:
    stored.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(stored, record.model_dump_json(indent=2) + "\n")


def _other_live_sprints(
    request: SprintLinkageRequest, sprint_ref: TaskDocumentRef, master: ResolvedTaskDocument
) -> list[ResolvedTaskDocument]:
    """The live sprints, besides the archiving one, that command the master."""

    tasks_root = request.coordination_root / "tasks" / request.repo_id
    try:
        commanding, _recording = _census(request, tasks_root, master)
    except SprintLinkageError as exc:
        raise SprintLinkageError(
            str(exc).replace("task_doc.retire_master", "task_doc.archive_sprint")
        ) from exc
    others = [sprint for sprint in commanding if sprint.ref != sprint_ref]
    try:
        return [sprint for sprint in others if sprint.document.is_sprint]
    except ValueError:
        return others


def _census(
    request: SprintLinkageRequest, tasks_root: Path, master: ResolvedTaskDocument
) -> tuple[tuple[ResolvedTaskDocument, ...], tuple[ResolvedTaskDocument, ...]]:
    census = sprint_census(tasks_root, master.path, master.ref, master.document)
    if census.unreadable:
        files = ", ".join(path.as_posix() for path in census.unreadable)
        raise SprintLinkageError(
            f"master {master.ref.key}: task document {files} cannot be opened, decoded or "
            "parsed, so it is not known whether a sprint there commands this master. Repair "
            "or remove that file, then repeat task_doc.archive_sprint"
        )
    topology = TaskDocumentTopology(request.coordination_root)

    def resolve(paths: tuple[Path, ...]) -> tuple[ResolvedTaskDocument, ...]:
        try:
            return tuple(
                topology.resolve(
                    TaskDocumentRef(
                        repository=request.repo_id, path=path.relative_to(tasks_root).as_posix()
                    )
                )
                for path in paths
            )
        except TaskDocumentRefError as exc:
            raise SprintLinkageError(
                f"master {master.ref.key}: {exc.status}: {exc}; repair that task document, "
                "which names this master, before task_doc.archive_sprint"
            ) from exc

    return resolve(census.commanding), resolve(census.recording)


def _move_sprint(plan: ArchivePlan, moved: list[tuple[Path, Path]]) -> None:
    plan.archive_dir.parent.mkdir(parents=True, exist_ok=True)
    if plan.live_dir.exists():
        plan.live_dir.rename(plan.archive_dir)
        moved.append((plan.live_dir, plan.archive_dir))


def _move_one(
    request: SprintLinkageRequest,
    plan: ArchivePlan,
    slot: _MasterSlot,
    moved: list[tuple[Path, Path]],
) -> None:
    """Move one commanded master's folder, re-checking at the last moment that no sprint names it."""

    if not slot.live.exists():
        if slot.archive.exists():
            return
        raise OSError(f"task folder {slot.live.as_posix()} is gone")
    others = _other_live_sprints(request, plan.sprint_ref, slot.master)
    if others:
        names = ", ".join(other.ref.key for other in others)
        raise SprintLinkageError(
            f"sprint {names} commands master {slot.master.ref.key}, so its folder stays "
            "where that sprint resolves it"
        )
    slot.archive.parent.mkdir(parents=True, exist_ok=True)
    slot.live.rename(slot.archive)
    moved.append((slot.live, slot.archive))


def _restore_targets(plan: ArchivePlan, moved: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Every folder this archive put archive-side, masters first and sprint last."""

    if plan.resumed:
        tasks_root = plan.live_dir.parent
        archive_root = tasks_root / ARCHIVE_DIR
        return [
            (tasks_root / folder, archive_root / folder)
            for folder in reversed(plan.record.moveOrder)
        ]
    return list(reversed(moved))


def _restore_pair(live: Path, archive: Path) -> list[str]:
    """Move one archive-side folder back, reporting whatever is left behind."""

    live_here, archived = live.exists(), archive.exists()
    if live_here and archived:
        return [
            f"{live.as_posix()} exists alongside its archive "
            f"{archive.as_posix()}; reconcile that directory"
        ]
    if not live_here and not archived:
        return [
            f"{live.as_posix()} is missing and its archive "
            f"{archive.as_posix()} is missing too; restore that folder"
        ]
    if archived:
        try:
            archive.rename(live)
        except OSError as exc:
            return [
                f"{archive.as_posix()} could not be restored to "
                f"{live.as_posix()} ({exc}); repair that directory"
            ]
    return []


def _remove_restored_record(plan: ArchivePlan, journal: _RecordJournal) -> list[str]:
    """Remove the record after complete restoration, reporting leftovers."""

    problems: list[str] = []
    for stored in (
        journal.stored,
        plan.archive_dir / "notes" / "reports" / RECORD_NAME,
    ):
        try:
            stored.unlink(missing_ok=True)
        except OSError as exc:
            problems.append(f"{stored.as_posix()} could not be removed ({exc}); remove it by hand")
    return problems


def _tidy_restored_dirs(plan: ArchivePlan, journal: _RecordJournal) -> None:
    for folder in journal.created:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    if not journal.archive_root_existed and plan.archive_dir.parent.is_dir():
        with suppress(OSError):
            plan.archive_dir.parent.rmdir()


def _restore_phase_one(
    plan: ArchivePlan, journal: _RecordJournal, moved: list[tuple[Path, Path]]
) -> list[str]:
    """A failure before the moves end: every archive-side record folder moves back.

    Masters restore first and the sprint last. A resumed retry restores from the
    record's own move order rather than the current invocation's moves, so an
    earlier prefix is restored too. The record is removed only after complete
    restoration; leftovers are returned as observed problems instead of being
    claimed as restored.
    """

    problems: list[str] = []
    for live, archive in _restore_targets(plan, moved):
        problems.extend(_restore_pair(live, archive))
    if problems:
        return problems
    if journal.new_record or plan.resumed:
        problems.extend(_remove_restored_record(plan, journal))
    if not problems:
        _tidy_restored_dirs(plan, journal)
    return problems


def _phase_one(request: SprintLinkageRequest, plan: ArchivePlan) -> None:
    """Write the record (unless recorded), then move the sprint first, then the masters."""

    new_record = plan.state == "not-recorded"
    stored = plan.live_dir / "notes" / "reports" / RECORD_NAME
    journal = _RecordJournal(
        stored=stored,
        created=[folder for folder in (stored.parent, stored.parent.parent) if not folder.exists()],
        archive_root_existed=plan.archive_dir.parent.exists(),
        new_record=new_record,
    )
    moved: list[tuple[Path, Path]] = []
    try:
        if new_record:
            _write_record_file(stored, plan.record)
        _move_sprint(plan, moved)
        for slot in plan.moving:
            _move_one(request, plan, slot, moved)
    except Exception as exc:
        problems = _restore_phase_one(plan, journal, moved)
        if problems:
            raise SprintLinkageError(
                f"sprint {plan.sprint_ref.key} archive failed before archival ({exc}); "
                f"restoration incomplete: {'; '.join(problems)}; address the named "
                "folders, then retry task_doc.archive_sprint"
            ) from exc
        raise SprintLinkageError(
            f"sprint {plan.sprint_ref.key} archive failed before archival; sprint sources "
            f"and task folders restored: {exc}; retry task_doc.archive_sprint"
        ) from exc
