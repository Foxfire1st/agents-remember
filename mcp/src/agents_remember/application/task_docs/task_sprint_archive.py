"""Archiving a sprint: one operation, one record, two phases (MIK-R82 rule 1).

``archive_sprint`` is called on the sprint document with a reason; a dry run reports exactly what
would change. Preconditions are checked before anything changes, in a dry run as in a real one,
and each refusal names the row or file and the clearing action:

* **(a)** the sprint status is Completed;
* **(b)** every row commanding a master is Completed, abandoned, or an MIK-R41v4 retirement row;
* **(c)** every commanded master passes the retire-operation readiness check (MIK-R41v4 rule 4
  Open work: no open work in its leaves, via :func:`require_master_retirement_ready`).

What moves: the sprint's own folder moves first, then every moving commanded master's folder
in a deterministic recorded order, all under ``tasks/<repository>/0_archive/``. A master also
commanded by another live sprint stays at its canonical live path, and the result names that
keeping sprint.

Record: before the first move the operation writes ``notes/reports/sprint-archive.json`` into
the sprint folder (request identity, every source/destination path, the original live
membership bytes, every staying master with its keepers, the ordered move progress and the
readiness facts, plus the same-request comparison basis). The record moves with the sprint
folder under ``0_archive/`` and is protected like a retirement record: a repeat is
compared with it by reason and selection, and a differing request is refused.

Two phases, mirroring the retire operation:

* *first phase* (record, then the sprint's folder first, then the masters): a failure before
  the moves end restores the moved masters first and the sprint last, then removes the record
  this request wrote, so a request leaves the state it found or a later one, never something
  in between. At every move boundary the archiving sprint has ceased to command live masters
  while the remaining live sprints still resolve all of theirs; ordinary live readers stay
  blind to the archive (no archive-lookup fallback is added);
* *second phase* (cleanup): after the moves the archive hook runs for every archived folder
  (the hook the retire operation uses). A hook failure keeps the archive with ``ok=false``,
  ``state=archived-with-hook-failures`` plus per-folder deleted/failed lists; repeating the same
  request runs only the remaining cleanup, with receipts per attempt, while a different reason is
  refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agents_remember.models.sprint_archive import (
    RECORD_NAME,
    SprintArchiveMasterEntry,
    SprintArchiveRecord,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    read_task_doc,
    read_task_doc_with_source,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.sprint_rows import (
    SEAT_DOC_FILE,
    correlate_seat_row,
    master_rows,
)
from agents_remember.tasks.task_paths import ARCHIVE_DIR
from agents_remember.worktrees.task_retirement import (
    MasterRetirementScope,
    require_master_retirement_ready,
)
from agents_remember.worktrees.worktree_contract import ContractError

from ..review_artifact_receipts import DELETION_KEYS
from .task_retirement_shared import (
    archive_hook,
    digest,
    hook_failed,
    hook_remainder,
    optional_digest,
)
from .task_sprint_archive_moves import _other_live_sprints, _phase_one
from .task_sprint_archive_records import (
    _Located,
    _MasterSlot,
    _new_record,
    _read_record,
    _read_sprint,
    _Reading,
    _record_location,
    _recorded_slots,
    _summarized_cleanup_changes,
    _summarized_cleanup_writes,
)
from .task_sprint_candidates import SprintLinkageError
from .task_sprint_context import (
    SprintLinkageRequest,
    _require_serving_retirement_schema,
    _require_serving_topology_schema,
)

TERMINAL_ROW_STATUS = frozenset({"Completed", "abandoned"})

ArchiveState = Literal["not-recorded", "recorded", "folders-archived"]


class ArchiveSprintPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=4096)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("archive_sprint requires a nonblank reason")
        return value.strip()


@dataclass(frozen=True)
class ArchivePlan:
    """One admitted request: the state it found and everything its phases need."""

    state: ArchiveState
    sprint_ref: TaskDocumentRef
    sprint: ResolvedTaskDocument
    live_dir: Path
    archive_dir: Path
    record: SprintArchiveRecord
    slots: tuple[_MasterSlot, ...]
    readiness_facts: tuple[str, ...]

    @property
    def resumed(self) -> bool:
        return self.state != "not-recorded"

    @property
    def moving(self) -> tuple[_MasterSlot, ...]:
        return tuple(slot for slot in self.slots if not slot.staying)


def archive_sprint(request: SprintLinkageRequest) -> dict[str, Any]:
    """Archive one orchestration sprint as a whole, with its commanded masters."""

    try:
        payload = ArchiveSprintPayload.model_validate(request.fields)
    except ValidationError as exc:
        raise SprintLinkageError(f"invalid archive_sprint payload: {exc}") from exc
    try:
        _require_serving_topology_schema()
        _require_serving_retirement_schema()
        plan = _admit(request, payload.reason)
    except SprintLinkageError:
        raise
    except (OSError, ValueError) as exc:
        action = (
            ""
            if "task_doc.archive_sprint" in str(exc)
            else "; repair the named source and retry task_doc.archive_sprint"
        )
        raise SprintLinkageError(f"sprint archive refused: {exc}{action}") from exc
    result = _first_answer(request, plan)
    if request.dry_run:
        return _preview(request, plan, result)
    return _execute(request, plan, result)


# -- admission: the state the request finds, and whether it may act on it -------------------------


def _admit(request: SprintLinkageRequest, reason: str) -> ArchivePlan:
    located = _locate(request)
    record = _read_record(located.live_dir, located.archive_dir)
    reading = _assemble(request, located)
    if record is None:
        return _admit_fresh(request, reason, reading)
    return _admit_recorded(request, reason, reading, record)


def _locate(request: SprintLinkageRequest) -> _Located:
    tasks_root = request.coordination_root / "tasks" / request.repo_id
    name = request.task_root.name
    try:
        relative = request.task_root.resolve(strict=False).relative_to(
            tasks_root.resolve(strict=False)
        )
    except ValueError:
        raise SprintLinkageError(
            f"task_doc.archive_sprint was called on {request.task_root.as_posix()}, which is "
            f"outside tasks/{request.repo_id}; call it on the sprint document with a reason"
        ) from None
    if ARCHIVE_DIR in relative.parts or name == ARCHIVE_DIR:
        raise SprintLinkageError(
            f"task_doc.archive_sprint is called on the live sprint, not inside {ARCHIVE_DIR}; "
            "address the sprint by its task name, then repeat task_doc.archive_sprint"
        )
    live_dir = tasks_root / name
    _require_live_address(request, name, tasks_root, live_dir)
    stem = request.slug or "task"
    live_json, archive_dir = live_dir / f"{stem}.json", tasks_root / ARCHIVE_DIR / name
    archive_json = archive_dir / f"{stem}.json"
    live_exists, archive_exists = live_json.is_file(), archive_json.is_file()
    if live_exists and archive_exists:
        raise SprintLinkageError(
            f"sprint {name!r} exists at both {live_json.as_posix()} and "
            f"{archive_json.as_posix()}; reconcile that directory before task_doc.archive_sprint"
        )
    if not live_exists and not archive_exists:
        raise SprintLinkageError(
            f"sprint {name!r} has no task folder: neither {live_json.as_posix()} nor "
            f"{archive_json.as_posix()} exists. Pass the name of an existing sprint, or "
            "restore its folder, before task_doc.archive_sprint"
        )
    return _Located(
        name=name,
        live_dir=live_dir,
        archive_dir=archive_dir,
        live_json=live_json,
        archive_json=archive_json,
        live_exists=live_exists,
    )


def _require_live_address(
    request: SprintLinkageRequest, name: str, tasks_root: Path, live_dir: Path
) -> None:
    if live_dir.resolve(strict=False) != request.task_root.resolve(strict=False):
        raise SprintLinkageError(
            f"task_doc.archive_sprint was called on {request.task_root.as_posix()}, which is "
            f"not the live sprint folder {live_dir.as_posix()}; address the sprint by its task "
            "name, then repeat task_doc.archive_sprint"
        )
    if live_dir.parent.resolve(strict=False) != tasks_root.resolve(strict=False):
        raise SprintLinkageError(
            f"sprint {name!r} sits in {live_dir.parent.as_posix()}, nested inside another "
            f"task's folder; the archive location 0_archive/{name} is defined only for root "
            f"sprint folders under {tasks_root.as_posix()}, so task_doc.archive_sprint will "
            "not move it"
        )


def _assemble(request: SprintLinkageRequest, located: _Located) -> _Reading:
    sprint, source = _read_sprint(
        located.name, located.live_json if located.live_exists else located.archive_json
    )
    stem = request.slug or "task"
    return _Reading(
        located=located,
        sprint_ref=TaskDocumentRef(repository=request.repo_id, path=f"{located.name}/{stem}.json"),
        sprint=sprint,
        source=source,
    )


def _admit_fresh(request: SprintLinkageRequest, reason: str, reading: _Reading) -> ArchivePlan:
    located = reading.located
    slots, facts = _commanded_slots(request, reading.sprint, located.live_dir, check_readiness=True)
    _require_archive_targets(request, located, slots)
    return ArchivePlan(
        state="not-recorded",
        sprint_ref=reading.sprint_ref,
        sprint=reading.sprint,
        live_dir=located.live_dir,
        archive_dir=located.archive_dir,
        record=_new_record(reading, reason, slots, facts),
        slots=slots,
        readiness_facts=facts,
    )


def _admit_recorded(
    request: SprintLinkageRequest, reason: str, reading: _Reading, record: SprintArchiveRecord
) -> ArchivePlan:
    located = reading.located
    if record.reason != reason:
        stored = _record_location(located.live_dir, located.archive_dir)
        raise SprintLinkageError(
            f"sprint {reading.sprint_ref.key} archive request differs from its retained "
            f"record {stored}; repeat the original reason"
        )
    _require_record_binding(request, reading, record, located)
    if located.live_exists:
        slots, facts = _admit_live_selection(request, reading, record)
    else:
        slots = _recorded_slots(request, record)
        facts = tuple(record.readinessFacts)
    state = _require_record_custody(request, record)
    if not located.live_exists:
        _require_archived_sprint_bytes(reading, record, located)
    return ArchivePlan(
        state=state,
        sprint_ref=reading.sprint_ref,
        sprint=reading.sprint,
        live_dir=located.live_dir,
        archive_dir=located.archive_dir,
        record=record,
        slots=slots,
        readiness_facts=facts,
    )


def _require_record_binding(
    request: SprintLinkageRequest,
    reading: _Reading,
    record: SprintArchiveRecord,
    located: _Located,
) -> None:
    """A recorded retry repeats the admitted request: same sprint, paths, and selection shape.

    Anything that names a different sprint, folder, archive destination, or master
    population is refused before any move or hook runs.
    """

    key = reading.sprint_ref.key
    if record.sprintRef != reading.sprint_ref:
        raise SprintLinkageError(
            f"sprint {key} archive record names {record.sprintRef.key}, not the requested "
            f"sprint {reading.sprint_ref.key}; restore the admitted record before "
            "retrying task_doc.archive_sprint"
        )
    if record.sprintFolder != located.name or (
        record.sprintArchivePath != located.archive_dir.as_posix()
    ):
        raise SprintLinkageError(
            f"sprint {key} archive record binds folders {record.sprintFolder!r} and "
            f"{record.sprintArchivePath}, not the requested {located.live_dir.as_posix()} "
            f"and {located.archive_dir.as_posix()}; restore the admitted folders before "
            "retrying task_doc.archive_sprint"
        )
    tasks_root = request.coordination_root / "tasks" / request.repo_id
    archive_root = tasks_root / ARCHIVE_DIR
    seen: set[str] = set()
    for entry in record.masters:
        entry_key = entry.masterRef.key
        if Path(entry.masterRef.path).parent.name != entry.folder:
            raise SprintLinkageError(
                f"sprint {key} archive record binds master {entry_key} to folder "
                f"{entry.folder!r}, outside its task document {entry.masterRef.path}; "
                "restore the admitted record before retrying task_doc.archive_sprint"
            )
        expected = (
            tasks_root / entry.folder if entry.staying else archive_root / entry.folder
        ).as_posix()
        if entry.archivePath != expected:
            raise SprintLinkageError(
                f"sprint {key} archive record binds master {entry_key} to {entry.archivePath}, "
                f"not the admitted destination {expected}; restore the admitted record "
                "before retrying task_doc.archive_sprint"
            )
        if entry.folder in seen:
            raise SprintLinkageError(
                f"sprint {key} archive record names folder {entry.folder!r} twice; restore "
                "the admitted record before retrying task_doc.archive_sprint"
            )
        seen.add(entry.folder)
    moving = sorted(entry.folder for entry in record.masters if not entry.staying)
    if (
        not record.moveOrder
        or record.moveOrder[0] != record.sprintFolder
        or sorted(record.moveOrder[1:]) != moving
        or len(set(record.moveOrder)) != len(record.moveOrder)
    ):
        raise SprintLinkageError(
            f"sprint {key} archive record move order "
            f"({', '.join(record.moveOrder) or 'empty'}) does not match its recorded masters "
            f"({', '.join(moving) or 'no masters'}); restore the admitted record before "
            "retrying task_doc.archive_sprint"
        )


def _require_record_custody(
    request: SprintLinkageRequest, record: SprintArchiveRecord
) -> ArchiveState:
    """Every recorded folder sits at exactly one side, in recorded move order.

    Moves run in ``moveOrder`` sequence, so the only admissible states are prefixes:
    a leading run of archived folders with the rest still live. A folder on both
    sides, on neither side, out of order, or a missing staying keeper refuses with
    its exact path and clearing action, before any move or hook.
    """

    tasks_root = request.coordination_root / "tasks" / request.repo_id
    archive_root = tasks_root / ARCHIVE_DIR
    sides: list[str] = []
    for folder in record.moveOrder:
        live, archive = tasks_root / folder, archive_root / folder
        live_here, archived = live.is_dir(), archive.is_dir()
        if live_here and archived:
            raise SprintLinkageError(
                f"sprint archive cannot resume: {folder!r} exists at both "
                f"{live.as_posix()} and {archive.as_posix()}; reconcile that directory "
                "before task_doc.archive_sprint"
            )
        if not live_here and not archived:
            raise SprintLinkageError(
                f"sprint archive cannot resume: {folder!r} is missing at both "
                f"{live.as_posix()} and {archive.as_posix()}; restore that folder before "
                "retrying task_doc.archive_sprint"
            )
        sides.append("archived" if archived else "live")
    for position, (folder, side) in enumerate(zip(record.moveOrder, sides, strict=True)):
        if side == "live" and "archived" in sides[position + 1 :]:
            raise SprintLinkageError(
                f"sprint archive cannot resume: {folder!r} is still live while a later "
                "recorded folder is already archived; restore the recorded move order "
                f"({', '.join(record.moveOrder)}) before retrying task_doc.archive_sprint"
            )
    for entry in record.masters:
        if entry.staying and not (tasks_root / entry.folder).is_dir():
            raise SprintLinkageError(
                f"sprint archive cannot resume: staying master folder "
                f"{(tasks_root / entry.folder).as_posix()} is missing; restore that folder "
                "before retrying task_doc.archive_sprint"
            )
    return "folders-archived" if all(side == "archived" for side in sides) else "recorded"


def _require_archived_sprint_bytes(
    reading: _Reading, record: SprintArchiveRecord, located: _Located
) -> None:
    """The archived sprint document still carries the exact admitted bytes."""

    try:
        _document, source = read_task_doc_with_source(located.archive_json)
    except (OSError, ValueError) as exc:
        raise SprintLinkageError(
            f"sprint archive cannot resume: its archived task document "
            f"{located.archive_json.as_posix()} is missing or cannot be read "
            f"({type(exc).__name__}: {exc}); restore that file before retrying "
            "task_doc.archive_sprint"
        ) from exc
    if digest(source.json_bytes or b"") != record.sprintJsonSha256 or (
        optional_digest(source.markdown_bytes) != record.sprintMarkdownSha256
    ):
        raise SprintLinkageError(
            f"sprint {reading.sprint_ref.key} archived task document "
            f"{located.archive_json.as_posix()} changed since archive admission; restore "
            "the admitted pair before retrying task_doc.archive_sprint"
        )


def _admit_live_selection(
    request: SprintLinkageRequest, reading: _Reading, record: SprintArchiveRecord
) -> tuple[tuple[_MasterSlot, ...], tuple[str, ...]]:
    located = reading.located
    source = reading.source
    if (
        digest(source.json_bytes or b"") != record.sprintJsonSha256
        or optional_digest(source.markdown_bytes) != record.sprintMarkdownSha256
    ):
        raise SprintLinkageError(
            f"sprint {reading.sprint_ref.key} source pair changed since archive admission: "
            f"{located.live_dir.as_posix()}; restore the admitted pair before retrying "
            "task_doc.archive_sprint"
        )
    slots, facts = _commanded_slots(
        request, reading.sprint, located.live_dir, check_readiness=True, record=record
    )
    current = sorted(
        f"{slot.master.ref.key}:{'staying' if slot.staying else 'moving'}" for slot in slots
    )
    if current != record.selection():
        raise SprintLinkageError(
            f"sprint {reading.sprint_ref.key} archive request commands a different selection "
            f"({', '.join(current) or 'no masters'}) than its retained record "
            f"({', '.join(record.selection()) or 'no masters'}); restore the admitted "
            "selection before retrying task_doc.archive_sprint"
        )
    _require_archive_targets(request, located, slots)
    return slots, facts


# -- preconditions: (a) sprint Completed, (b) rows terminal, (c) masters ready ----------------------


def _commanded_slots(
    request: SprintLinkageRequest,
    sprint: ResolvedTaskDocument,
    live_dir: Path,
    *,
    check_readiness: bool,
    record: SprintArchiveRecord | None = None,
) -> tuple[tuple[_MasterSlot, ...], tuple[str, ...]]:
    key, document, json_path = sprint.ref.key, sprint.document, sprint.path
    if document.status != "Completed":
        raise SprintLinkageError(
            f"sprint {key} status is {document.status!r} in {json_path.as_posix()}; set it "
            "to Completed with task_doc.set_status on the sprint before task_doc.archive_sprint"
        )
    masters, premoved = _resolve_commanded(request, sprint, record)
    # Rows of masters this archive already moved read as admitted (the source pair is
    # unchanged), so the terminal-row check below covers only masters still live.
    skipped: set[str] = set()
    for entry in premoved:
        standing_row = master_rows(document, live_dir, entry.masterRef).row
        if standing_row is not None:
            skipped.add(standing_row.number)
    commanded = tuple(slot.ref for slot in masters.values()) + tuple(
        entry.masterRef for entry in premoved
    )
    _require_rows_terminal(
        _RowAdmission(
            key=key,
            document=document,
            live_dir=live_dir,
            json_path=json_path,
            skipped=skipped,
            repo_id=request.repo_id,
            commanded=commanded,
        )
    )
    tasks_root = request.coordination_root / "tasks" / request.repo_id
    slots = _build_slots(request, sprint.ref, masters, premoved, tasks_root)
    facts = _collect_readiness(request, slots) if check_readiness else ()
    return tuple(slots), tuple(facts)


@dataclass(frozen=True)
class _RowAdmission:
    """The sprint state one terminal-row check admits commanded masters against."""

    key: str
    document: TaskDocument
    live_dir: Path
    json_path: Path
    skipped: set[str]
    repo_id: str
    commanded: tuple[TaskDocumentRef, ...]


def _require_rows_terminal(admission: _RowAdmission) -> None:
    """Precondition (b): every row commanding a master is terminal or a retirement row."""

    key, document, live_dir, json_path, skipped = (
        admission.key,
        admission.document,
        admission.live_dir,
        admission.json_path,
        admission.skipped,
    )

    for row in document.subTasks:
        if (
            row.masterRef is not None
            and row.number not in skipped
            and (row.status not in TERMINAL_ROW_STATUS and row.retirement is None)
        ):
            raise SprintLinkageError(
                f"row {row.number!r} of sprint {key} commands master "
                f"{row.masterRef.key} with status {row.status!r} in "
                f"{json_path.as_posix()}; complete that master's work and mark its row "
                "Completed or abandoned (or retire the master with task_doc.retire_master), "
                "then repeat task_doc.archive_sprint"
            )
    for row in document.subTasks:
        if row.masterRef is None or row.number in skipped:
            continue
        standing = master_rows(document, live_dir, row.masterRef).row
        if (
            standing is not None
            and standing.number == row.number
            and standing.status not in TERMINAL_ROW_STATUS
            and standing.retirement is None
        ):
            raise SprintLinkageError(
                f"row {standing.number!r} of sprint {key} commands master "
                f"{row.masterRef.key} with status {standing.status!r} in "
                f"{json_path.as_posix()}; complete that master's work and mark its row "
                "Completed or abandoned (or retire the master with task_doc.retire_master), "
                "then repeat task_doc.archive_sprint"
            )
    _require_legacy_rows_terminal(admission)


def _require_legacy_rows_terminal(admission: _RowAdmission) -> None:
    """Precondition (b) for legacy seat rows: a seat document that coordinates a master
    commands it, so its row is admitted exactly like a typed row.

    An unreadable seat document is an unknown command correlation and refuses; two
    seat rows standing for one commanded master are ambiguous and refuse. Either
    names the exact row and file with its clearing action.
    """

    key, document, live_dir, json_path, repo_id, commanded = (
        admission.key,
        admission.document,
        admission.live_dir,
        admission.json_path,
        admission.repo_id,
        admission.commanded,
    )
    commanded_keys = {ref.key for ref in commanded}
    correlated: dict[str, list[SubTaskRef]] = {}
    for row in document.subTasks:
        if row.masterRef is not None or row.retirement is not None:
            continue
        if not SEAT_DOC_FILE.match(row.file or ""):
            continue
        ref = _correlated_legacy_master(key, live_dir, json_path, repo_id, row)
        if ref is None:
            continue
        if ref.repository != repo_id:
            raise SprintLinkageError(
                f"row {row.number!r} of sprint {key} commands master {ref.key} "
                f"outside the sprint repository in {json_path.as_posix()}; move that master "
                "into this repository, or remove the row, before task_doc.archive_sprint"
            )
        if row.status not in TERMINAL_ROW_STATUS:
            raise SprintLinkageError(
                f"row {row.number!r} of sprint {key} commands master "
                f"{ref.key} with status {row.status!r} in "
                f"{json_path.as_posix()}; complete that master's work and mark its row "
                "Completed or abandoned (or retire the master with task_doc.retire_master), "
                "then repeat task_doc.archive_sprint"
            )
        correlated.setdefault(ref.key, []).append(row)
    for ref_key, rows in correlated.items():
        if len(rows) > 1 and ref_key in commanded_keys:
            numbers = ", ".join(sorted(row.number for row in rows))
            raise SprintLinkageError(
                f"sprint {key} has {len(rows)} seat rows ({numbers}) commanding master "
                f"{ref_key} in {json_path.as_posix()}; keep exactly one row for that "
                "master, then repeat task_doc.archive_sprint"
            )


def _correlated_legacy_master(
    key: str, live_dir: Path, json_path: Path, repo_id: str, row: SubTaskRef
) -> TaskDocumentRef | None:
    """The master one legacy seat row coordinates, refusing an unknown correlation."""

    seat_json = (live_dir / (row.file or "")).with_suffix(".json")
    try:
        read_task_doc(seat_json)
    except (OSError, ValueError) as exc:
        raise SprintLinkageError(
            f"row {row.number!r} of sprint {key} names seat file {row.file or ''!r} whose "
            f"task document {seat_json.as_posix()} cannot be read "
            f"({type(exc).__name__}: {exc}) in {json_path.as_posix()}, so it is not known "
            "whether the row commands a master; restore or repair that file, or remove "
            "the row, before task_doc.archive_sprint"
        ) from exc
    return correlate_seat_row(live_dir, repo_id, row)


def _build_slots(
    request: SprintLinkageRequest,
    sprint_ref: TaskDocumentRef,
    masters: dict[str, ResolvedTaskDocument],
    premoved: list[SprintArchiveMasterEntry],
    tasks_root: Path,
) -> list[_MasterSlot]:
    """One slot per commanded master: moving, or staying with the live sprint named."""

    slots: list[_MasterSlot] = []
    for master in sorted(masters.values(), key=lambda item: item.ref.key):
        _require_root_master_folder(request, master)
        others = _other_live_sprints(request, sprint_ref, master)
        if others:
            names = ", ".join(other.ref.key for other in others)
            slots.append(
                _MasterSlot(
                    master=master,
                    staying=True,
                    stay_reason=(
                        f"commanded by live sprint {names}, so its folder stays where that "
                        "sprint resolves it; detach it there with task_doc.detach_master, "
                        "then repeat task_doc.archive_sprint to archive it as well"
                    ),
                    live=master.path.parent,
                    archive=master.path.parent,
                )
            )
        else:
            slots.append(
                _MasterSlot(
                    master=master,
                    staying=False,
                    stay_reason=None,
                    live=master.path.parent,
                    archive=tasks_root / ARCHIVE_DIR / master.path.parent.name,
                )
            )
    for entry in premoved:
        # Already moved by this archive: the record owns the slot, and the archived
        # document is read back only at the record's own archive path (R83: no
        # ordinary archive lookup). An unreadable record-bound document refuses.
        archived_doc = tasks_root / ARCHIVE_DIR / entry.masterRef.path
        try:
            premoved_document, _source = read_task_doc_with_source(archived_doc)
        except (OSError, ValueError) as exc:
            raise SprintLinkageError(
                f"sprint archive cannot resume: its moved master {entry.masterRef.key} "
                f"task document {archived_doc.as_posix()} cannot be read "
                f"({type(exc).__name__}: {exc}); restore that file before retrying "
                "task_doc.archive_sprint"
            ) from exc
        slots.append(
            _MasterSlot(
                master=ResolvedTaskDocument(
                    entry.masterRef,
                    archived_doc,
                    premoved_document,
                ),
                staying=False,
                stay_reason=None,
                live=tasks_root / entry.folder,
                archive=tasks_root / ARCHIVE_DIR / entry.folder,
            )
        )
    return slots


def _collect_readiness(request: SprintLinkageRequest, slots: list[_MasterSlot]) -> tuple[str, ...]:
    """Precondition (c): every still-live commanded master passes the retire readiness check."""

    facts: list[str] = []
    for slot in slots:
        if slot.live.is_dir():
            facts.extend(_require_ready(request, slot.master))
    return tuple(facts)


def _resolve_commanded(
    request: SprintLinkageRequest,
    sprint: ResolvedTaskDocument,
    record: SprintArchiveRecord | None,
) -> tuple[dict[str, ResolvedTaskDocument], list[SprintArchiveMasterEntry]]:
    """Every master the sprint commands, by membership entry and by typed row.

    On a resume (``record`` given) a master this archive already moved is returned as a
    record entry instead of resolved: its live document is gone, and the archived one is
    never read back.
    """

    tasks_root = request.coordination_root / "tasks" / request.repo_id
    topology = TaskDocumentTopology(request.coordination_root)
    recorded = (
        {entry.masterRef.key: entry for entry in record.masters if not entry.staying}
        if record is not None
        else {}
    )
    masters: dict[str, ResolvedTaskDocument] = {}
    premoved: dict[str, SprintArchiveMasterEntry] = {}
    for entry in sprint.document.orchestrates:
        matches = _match_live_masters(request, tasks_root, entry)
        if not matches:
            if (tasks_root / ARCHIVE_DIR / entry / "task.json").is_file():
                moved = next(
                    (candidate for candidate in recorded.values() if candidate.folder == entry),
                    None,
                )
                if moved is not None:
                    premoved[moved.masterRef.key] = moved
                    continue
            raise SprintLinkageError(
                f"orchestrates entry {entry!r} of sprint {sprint.ref.key} names no live "
                f"master in {sprint.path.as_posix()}; remove the entry with "
                "task_doc.set_field on the sprint, or restore that master's folder, before "
                "task_doc.archive_sprint"
            )
        if len(matches) > 1:
            found = ", ".join(sorted(match.ref.key for match in matches))
            raise SprintLinkageError(
                f"orchestrates entry {entry!r} of sprint {sprint.ref.key} resolves to "
                f"{len(matches)} masters ({found}) in {sprint.path.as_posix()}; an entry "
                "names exactly one. Give the master that is not meant an id and a title of "
                "its own, then repeat task_doc.archive_sprint"
            )
        masters[matches[0].ref.key] = matches[0]
    for row in sprint.document.subTasks:
        ref = row.masterRef
        if ref is None or ref.key in masters or ref.key in premoved:
            continue
        try:
            masters[ref.key] = _resolve_typed_row(request, topology, sprint, row)
        except SprintLinkageError:
            moved = recorded.get(ref.key)
            if (
                moved is not None
                and (tasks_root / ARCHIVE_DIR / moved.folder / "task.json").is_file()
            ):
                premoved[ref.key] = moved
                continue
            raise
    return masters, list(premoved.values())


def _match_live_masters(
    request: SprintLinkageRequest, tasks_root: Path, entry: str
) -> list[ResolvedTaskDocument]:
    """The live masters one membership entry names, by folder name, id, or title."""

    matches: list[ResolvedTaskDocument] = []
    try:
        folders = sorted(tasks_root.iterdir())
    except OSError as exc:
        raise SprintLinkageError(
            f"sprint archive cannot list the task tree {tasks_root.as_posix()} "
            f"({type(exc).__name__}: {exc}); repair that directory before task_doc.archive_sprint"
        ) from exc
    for folder in folders:
        if folder.name == ARCHIVE_DIR or folder.is_symlink() or not folder.is_dir():
            continue
        json_path = folder / "task.json"
        try:
            exists = json_path.is_file()
        except OSError:
            exists = True
        if not exists:
            continue
        try:
            document, _source = read_task_doc_with_source(json_path)
        except (OSError, ValueError) as exc:
            raise SprintLinkageError(
                f"sprint archive cannot read task document {json_path.as_posix()} "
                f"({type(exc).__name__}: {exc}), so it is not known whether the sprint "
                "commands its master; repair or remove that file, then repeat "
                "task_doc.archive_sprint"
            ) from exc
        if document.kind != "master" or document.is_sprint:
            continue
        if entry in {folder.name, document.id, document.title}:
            matches.append(
                ResolvedTaskDocument(
                    TaskDocumentRef(repository=request.repo_id, path=f"{folder.name}/task.json"),
                    json_path,
                    document,
                )
            )
    return matches


def _resolve_typed_row(
    request: SprintLinkageRequest,
    topology: TaskDocumentTopology,
    sprint: ResolvedTaskDocument,
    row: SubTaskRef,
) -> ResolvedTaskDocument:
    ref = row.masterRef
    assert ref is not None
    if ref.repository != request.repo_id:
        raise SprintLinkageError(
            f"row {row.number!r} of sprint {sprint.ref.key} commands master {ref.key} "
            f"outside the sprint repository in {sprint.path.as_posix()}; move that master "
            "into this repository, or remove the row, before task_doc.archive_sprint"
        )
    try:
        return topology.resolve(ref)
    except (TaskDocumentRefError, OSError, ValueError) as exc:
        raise SprintLinkageError(
            f"row {row.number!r} of sprint {sprint.ref.key} commands master {ref.key}, "
            f"whose task document cannot be read ({exc}) in {sprint.path.as_posix()}; "
            "restore that master's folder, or detach the row with task_doc.detach_master, "
            "before task_doc.archive_sprint"
        ) from exc


def _require_root_master_folder(
    request: SprintLinkageRequest, master: ResolvedTaskDocument
) -> None:
    tasks = (request.coordination_root / "tasks" / request.repo_id).resolve()
    if master.path.parent.resolve().parent != tasks:
        raise SprintLinkageError(
            f"master {master.ref.key} sits in {master.path.parent.resolve().parent.as_posix()}, "
            "nested inside another task's folder; the archive location 0_archive/<name> is "
            f"defined only for root task folders under {tasks.as_posix()}, so "
            "task_doc.archive_sprint will not move it. Keep it in place, or move it to a "
            "root task folder first"
        )


def _require_archive_targets(
    request: SprintLinkageRequest,
    located: _Located,
    slots: tuple[_MasterSlot, ...],
) -> None:
    tasks_root = request.coordination_root / "tasks" / request.repo_id
    archive_root = tasks_root / ARCHIVE_DIR
    if archive_root.is_symlink() or (
        archive_root.exists()
        and archive_root.resolve(strict=False) != (tasks_root.resolve() / ARCHIVE_DIR)
    ):
        raise SprintLinkageError(
            f"sprint archive target {archive_root.as_posix()} escapes tasks/"
            f"{request.repo_id}; remove that link or directory before task_doc.archive_sprint"
        )
    if located.archive_dir.exists():
        raise SprintLinkageError(
            f"sprint {located.name!r} archive target already exists: "
            f"{located.archive_dir.as_posix()}; reconcile that directory before task_doc.archive_sprint"
        )
    for slot in slots:
        if slot.staying:
            continue
        if not slot.live.exists() and slot.archive.exists():
            continue  # moved already by this archive; a resume completes what is left
        if slot.archive.exists():
            raise SprintLinkageError(
                f"master {slot.master.ref.key} archive target already exists: "
                f"{slot.archive.as_posix()}; reconcile that directory before "
                "task_doc.archive_sprint"
            )


def _require_ready(request: SprintLinkageRequest, master: ResolvedTaskDocument) -> list[str]:
    """The retire-operation readiness of one commanded master, in this operation's words."""

    scope = MasterRetirementScope(
        request.coordination_root,
        request.repo_id,
        request.code_repository,
        request.memory_repository,
        master,
    )
    try:
        readiness = require_master_retirement_ready(scope)
    except ContractError as refusal:
        detail = str(refusal).replace("task_doc.retire_master", "task_doc.archive_sprint")
        raise SprintLinkageError(
            f"master {master.ref.key} is not ready to archive: {detail}"
        ) from refusal
    return [f"{master.path.parent.name}: {fact}" for fact in readiness.facts]


# -- answers: the result of a request, and what a dry run would change -----------------------------


def _first_answer(request: SprintLinkageRequest, plan: ArchivePlan) -> dict[str, Any]:
    archived = [(slot.master.ref, slot.archive) for slot in plan.moving]
    stayed = [
        {
            "masterRef": slot.master.ref.model_dump(mode="json"),
            "reason": slot.stay_reason,
        }
        for slot in plan.slots
        if slot.staying
    ]
    return {
        "ok": True,
        "operation": "task_doc.archive_sprint",
        "state": "would-archive" if request.dry_run else "archived",
        "dryRun": request.dry_run,
        "sprintTaskDocumentRef": plan.sprint_ref.model_dump(mode="json"),
        "archiveState": plan.state,
        "archiveResumed": plan.resumed,
        "sprintArchive": plan.record.model_dump(mode="json"),
        "archivedMasters": [
            {"masterRef": ref.model_dump(mode="json"), "archivePath": path.as_posix()}
            for ref, path in archived
        ],
        "stayedMasters": stayed,
        "readinessFacts": list(plan.readiness_facts),
        "documents": [],
        "projectionEffects": [],
        "taskArchive": {
            "state": "would-archive" if request.dry_run else "archived",
            "taskRoot": plan.live_dir.as_posix(),
            "archivePath": plan.archive_dir.as_posix(),
            "cleanups": [],
        },
    }


def _preview(
    request: SprintLinkageRequest, plan: ArchivePlan, result: dict[str, Any]
) -> dict[str, Any]:
    folders = _pending_folders(plan)
    if plan.state == "folders-archived":
        return _preview_archived(request, result, folders)
    return _preview_pending(request, plan, result, folders)


def _preview_archived(
    request: SprintLinkageRequest,
    result: dict[str, Any],
    folders: list[tuple[Path, Path]],
) -> dict[str, Any]:
    cleanups = [_dry_cleanup(request, live, archive) for live, archive in folders]
    result["taskArchive"]["cleanups"] = cleanups
    failed = any(item["failed"] for item in cleanups)
    exact_changes = [
        f"review artifacts of {archive.as_posix()}: delete "
        f"{item['remainder']} (listed under taskArchive.cleanups)"
        for item, (_, archive) in zip(cleanups, folders, strict=True)
        if item["remainder"]
    ]
    exact_written: list[Path] = []
    for item in cleanups:
        exact_written.extend(item["receipts"])
        if item["receipt_line"] is not None:
            exact_changes.append(item["receipt_line"])
    if failed:
        exact_changes.append(
            "review artifacts: retry what the hook cannot reach (listed under "
            "taskArchive.cleanups[].reviewArtifacts.failures)"
        )
    if len(exact_changes) <= 16 and len(exact_written) <= 16:
        result["wouldChange"] = exact_changes
        result["wouldWrite"] = [path.as_posix() for path in exact_written]
    else:
        result["wouldChange"] = _summarized_cleanup_changes(cleanups, failed)
        result["wouldWrite"] = _summarized_cleanup_writes(cleanups)
    if exact_changes:
        result["state"] = "would-clean-up"
        result["archiveState"] = "hook-failed" if failed else "folders-archived"
    else:
        result["state"] = "archived"
        result["archiveState"] = "hook-finished"
        result["detail"] = (
            "Nothing would change: the sprint is archived and the cleanup is complete."
        )
    return result


def _preview_pending(
    request: SprintLinkageRequest,
    plan: ArchivePlan,
    result: dict[str, Any],
    folders: list[tuple[Path, Path]],
) -> dict[str, Any]:
    record_change = (
        [
            f"archive record: write {RECORD_NAME} in {plan.live_dir.as_posix()}/notes/reports "
            f"(sprint {plan.sprint_ref.key})"
        ]
        if plan.state == "not-recorded"
        else []
    )
    record_write = (
        [(plan.live_dir / "notes" / "reports" / RECORD_NAME).as_posix()]
        if plan.state == "not-recorded"
        else []
    )
    pending = [pair for pair in folders if pair[0].exists()]
    move_changes = [
        f"task folder: move {live.as_posix()} to {archive.as_posix()}" for live, archive in pending
    ]
    cleanups = [_dry_cleanup(request, live, archive) for live, archive in pending]
    result["taskArchive"]["cleanups"] = [
        {
            "taskRoot": live.as_posix(),
            "archivePath": archive.as_posix(),
            "reviewArtifacts": item["report"],
        }
        for item, (live, archive) in zip(cleanups, pending, strict=True)
    ]
    cleanup_changes: list[str] = []
    cleanup_written: list[Path] = []
    for item, (live, archive) in zip(cleanups, pending, strict=True):
        _preview_cleanup_changes(item, live, archive, cleanup_changes, cleanup_written)
    exact_changes = [*record_change, *move_changes, *cleanup_changes]
    exact_writes = [*record_write, *(path.as_posix() for path in cleanup_written)]
    if len(exact_changes) <= 16 and len(exact_writes) <= 16:
        result["wouldChange"] = exact_changes
        result["wouldWrite"] = exact_writes
        return result
    staying = sum(1 for slot in plan.slots if slot.staying)
    changes = [*record_change]
    if move_changes:
        changes.append(
            f"task folders: move {len(pending)} folders in recorded order (exact order "
            "under sprintArchive.moveOrder, exact destinations under archivedMasters)"
            + (f"; {staying} staying masters under stayedMasters" if staying else "")
        )
    changes.extend(_summarized_cleanup_changes(cleanups, any(item["failed"] for item in cleanups)))
    writes = [*record_write]
    if cleanup_written:
        writes.append(
            f"{len(cleanup_written)} cleanup receipt files across {len(pending)} "
            "folders (exact paths under taskArchive.cleanups)"
        )
    result["wouldChange"] = changes
    result["wouldWrite"] = writes
    return result


def _preview_cleanup_changes(
    item: dict[str, Any], live: Path, archive: Path, changes: list[str], written: list[Path]
) -> None:
    if item["remainder"]:
        changes.append(
            f"review artifacts of {live.name}: delete {item['remainder']} (listed "
            "under taskArchive.cleanups)"
        )
    if item["failed"]:
        changes.append(
            "review artifacts: retry what the hook cannot reach (listed under "
            "taskArchive.cleanups[].reviewArtifacts.failures)"
        )
    if item["receipt_line"] is not None:
        changes.append(item["receipt_line"].replace(str(live), str(archive)))
    written.extend(
        archive / Path(name).relative_to(live) for name in item["report"].get("receiptFiles", [])
    )


def _pending_folders(plan: ArchivePlan) -> list[tuple[Path, Path]]:
    """Every folder the cleanup still owns: the sprint first, then each moved master."""

    folders = [(slot.live, slot.archive) for slot in plan.moving]
    return [(plan.live_dir, plan.archive_dir), *folders]


def _dry_cleanup(request: SprintLinkageRequest, live: Path, archive: Path) -> dict[str, Any]:
    folder = archive if plan_folder_archived(live, archive) else live
    report = archive_hook(request, folder)
    return {
        "report": report,
        "remainder": hook_remainder(report),
        "failed": hook_failed(report),
        "receipts": [
            folder / Path(name).relative_to(folder) for name in report.get("receiptFiles", [])
        ],
        "receipt_line": (
            f"cleanup receipt: write receipt {report.get('attempt')} at "
            f"{next((folder / Path(name).relative_to(folder) for name in report.get('receiptFiles', [])), folder).as_posix()}"
            if report.get("receipt") == "would-write"
            else None
        ),
    }


def _deleted_names(report: dict[str, Any]) -> list[str]:
    """The per-folder deleted artifacts, by ref or by path, whatever entry shape they use."""

    names: list[str] = []
    for key in DELETION_KEYS:
        for entry in report.get(key, []):
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                ref = entry.get("ref") or entry.get("path")
                if isinstance(ref, str) and ref:
                    names.append(ref)
    return [name for name in names if isinstance(name, str) and name]


def plan_folder_archived(live: Path, archive: Path) -> bool:
    return not live.exists() and archive.is_dir()


# -- execution: first the record and the moves, then the hook for every archived folder -------------


def _execute(
    request: SprintLinkageRequest, plan: ArchivePlan, result: dict[str, Any]
) -> dict[str, Any]:
    if plan.state != "folders-archived":
        _phase_one(request, plan)
    return _phase_two(request, plan, result)


def _phase_two(
    request: SprintLinkageRequest, plan: ArchivePlan, result: dict[str, Any]
) -> dict[str, Any]:
    """Run the archive hook for every archived folder; a failed hook keeps the archive."""

    cleanups = []
    for live, archive in _pending_folders(plan):
        if not archive.is_dir():
            continue
        report = archive_hook(request, archive)
        cleanups.append(
            {
                "taskRoot": live.as_posix(),
                "archivePath": archive.as_posix(),
                "reviewArtifacts": report,
                "deleted": _deleted_names(report),
                "failed": report.get("failures", []),
            }
        )
    result["taskArchive"]["cleanups"] = cleanups
    result["archiveState"] = "hook-finished"
    if any(hook_failed(entry["reviewArtifacts"]) for entry in cleanups):
        result["ok"] = False
        result["state"] = "archived-with-hook-failures"
        result["archiveState"] = "hook-failed"
        result["nextAction"] = (
            "Repeat task_doc.archive_sprint with the same reason to retry the archive hook."
        )
    return result
