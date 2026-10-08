"""Where a master's retirement stands on disk: its record, its folder, the sprints that name it.

Read-only. The retire operation decides every step from this observation and never from the
document a request happened to be addressed to.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_retirement import MasterRetirementProof
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocSourceSnapshot,
    read_task_doc_with_source,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.retired_rows import records_retirements
from agents_remember.tasks.sprint_rows import sprint_census
from agents_remember.tasks.task_paths import ARCHIVE_DIR

from .task_retirement_shared import require_root_master_folder
from .task_sprint_candidates import SprintLinkageError
from .task_sprint_context import SprintLinkageRequest

PROOF_NAME = "master-retirement.json"


def proof_path(folder: Path) -> Path:
    """The record of a master that no sprint commanded: it lies in, and moves with, its folder."""

    return folder / "notes" / "reports" / PROOF_NAME


@dataclass(frozen=True)
class RecordingRow:
    """A sprint row that records the retirement of the observed master."""

    sprint: ResolvedTaskDocument
    row: SubTaskRef


@dataclass(frozen=True)
class Observed:
    """One master as the task tree shows it now."""

    topology: TaskDocumentTopology
    master: ResolvedTaskDocument
    master_source: TaskDocSourceSnapshot
    live: Path
    archive: Path
    live_exists: bool
    archive_exists: bool
    commanding: tuple[ResolvedTaskDocument, ...]
    recording: tuple[RecordingRow, ...]
    observed_sources: list[TaskDocSourceSnapshot]

    @property
    def key(self) -> str:
        return self.master.ref.key

    @property
    def sources(self) -> tuple[TaskDocSourceSnapshot, ...]:
        """Every document read so far through :attr:`topology`, as the bytes it was read from."""

        return tuple(self.observed_sources)

    @property
    def folder(self) -> Path:
        """Where the master's folder is; the live place wins while both exist."""

        return self.live if self.live_exists else self.archive

    @property
    def archive_ref(self) -> TaskDocumentRef:
        return TaskDocumentRef(
            repository=self.master.ref.repository,
            path=f"{ARCHIVE_DIR}/{self.live.name}/task.json",
        )

    @property
    def proof_file(self) -> Path | None:
        stored = proof_path(self.folder)
        try:
            stored.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            # The required proof reader must refuse an object it cannot inspect/read.
            return stored
        return stored

    def source_of(self, document: ResolvedTaskDocument) -> TaskDocSourceSnapshot:
        """The exact bytes this observation read for one of the documents it resolved."""

        wanted = document.path.resolve(strict=False)
        return next(
            source for source in self.sources if source.json_path.resolve(strict=False) == wanted
        )


def observe(request: SprintLinkageRequest, master_ref: TaskDocumentRef) -> Observed:
    """Read the master, where its folder is, and every sprint that commands it or records it."""

    key = master_ref.key
    if not master_ref.path.endswith("/task.json") or ARCHIVE_DIR in Path(master_ref.path).parts:
        raise SprintLinkageError(
            f"{key} is not the task.json of a live master folder; pass the master's own "
            f"reference, {request.repo_id}/<master folder>/task.json"
        )
    locator = TaskDocumentTopology(request.coordination_root)
    live = locator.path_for_ref(master_ref).parent
    require_root_master_folder(request, key, live)
    archive = locator.path_for_ref(
        TaskDocumentRef(repository=request.repo_id, path=f"{ARCHIVE_DIR}/{live.name}/task.json")
    ).parent
    live_exists, archive_exists = live.exists(), archive.exists()
    if not live_exists and not archive_exists:
        raise SprintLinkageError(
            f"master {key} has no task folder: neither {live} nor {archive} exists. Pass the "
            "reference of an existing master, or restore its folder, before task_doc.retire_master"
        )
    document_path = (live if live_exists else archive) / "task.json"
    try:
        master, master_source = read_task_doc_with_source(document_path)
    except (OSError, ValueError) as exc:
        raise SprintLinkageError(
            f"master {key}: its task document {document_path} is missing or cannot be read "
            f"({type(exc).__name__}: {exc}); restore or repair that file before "
            "task_doc.retire_master"
        ) from exc
    if master.kind != "master" or master.repo != request.repo_id:
        raise SprintLinkageError(
            f"{key} is not one individual master of this repository; pass a master's own "
            "task.json reference to task_doc.retire_master"
        )
    if master.orchestrates or records_retirements(master):
        raise SprintLinkageError(
            f"{key} is a sprint, not a master: "
            + (
                f"it commands {len(master.orchestrates)} master(s)"
                if master.orchestrates
                else "it records the retirement of the masters it commanded"
            )
            + ". task_doc.retire_master retires one master and never a sprint. A sprint is "
            "archived as a whole by task_doc.archive_sprint, called on the sprint with a reason; "
            "where this build does not offer that operation yet, leave the sprint in place"
        )
    sources: list[TaskDocSourceSnapshot] = [master_source] if live_exists else []
    topology = TaskDocumentTopology(
        request.coordination_root, accepted_sources=sources, source_observer=sources.append
    )
    resolved = ResolvedTaskDocument(master_ref, live / "task.json", master)
    commanding, recording = sprints_naming(request, topology, resolved)
    return Observed(
        topology=topology,
        master=resolved,
        master_source=master_source,
        live=live,
        archive=archive,
        live_exists=live_exists,
        archive_exists=archive_exists,
        commanding=commanding,
        recording=tuple(
            RecordingRow(document, row)
            for document in recording
            for row in document.document.subTasks
            if row.retirement is not None and row.retirement.masterRef == master_ref
        ),
        observed_sources=sources,
    )


def require_addressed_readable(request: SprintLinkageRequest) -> None:
    """Refuse a request addressed to a document that exists and cannot be read, naming the file.

    A request is addressed to the owner of the record. While that document cannot be opened,
    decoded or parsed, nothing it says about the master is known, so nothing is decided from it.
    """

    addressed = request.task_root / f"{request.slug or 'task'}.json"
    if not addressed.exists():
        return
    try:
        loaded = json.loads(addressed.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problem = f"{type(exc).__name__}: {exc}"
    else:
        if isinstance(loaded, dict):
            return
        problem = "it does not hold a JSON object"
    raise SprintLinkageError(
        f"task_doc.retire_master was called on {addressed.as_posix()}, which cannot be opened, "
        f"decoded or parsed ({problem}). Repair that file, then repeat task_doc.retire_master"
    )


def sprints_naming(
    request: SprintLinkageRequest, topology: TaskDocumentTopology, master: ResolvedTaskDocument
) -> tuple[tuple[ResolvedTaskDocument, ...], tuple[ResolvedTaskDocument, ...]]:
    """The sprints that command the master now, and the ones that hold a row for its retirement.

    The request is refused while any task document of the repository cannot be read, whether its
    text names the master or not: an unreadable sprint may be the one that commands the master.
    """

    tasks = topology.path_for_ref(master.ref).parents[1]
    census = sprint_census(tasks, master.path, master.ref, master.document)
    if census.unreadable:
        # An archive cannot be undone: while any task document cannot be read, no master is taken
        # for one that no sprint commands and no retirement for one that is not recorded.
        files = ", ".join(path.as_posix() for path in census.unreadable)
        raise SprintLinkageError(
            f"master {master.ref.key}: task document {files} cannot be opened, decoded or "
            "parsed, so it is not known whether a sprint there commands this master or records "
            "its retirement. Repair or remove that file, then repeat task_doc.retire_master"
        )

    def resolve(paths: tuple[Path, ...]) -> tuple[ResolvedTaskDocument, ...]:
        try:
            return tuple(
                topology.resolve(
                    TaskDocumentRef(
                        repository=request.repo_id, path=path.relative_to(tasks).as_posix()
                    )
                )
                for path in paths
            )
        except TaskDocumentRefError as exc:
            raise SprintLinkageError(
                f"master {master.ref.key}: {exc.status}: {exc}; repair that task document, which "
                "names this master, before task_doc.retire_master"
            ) from exc

    return resolve(census.commanding), resolve(census.recording)


def read_proof(observed: Observed) -> MasterRetirementProof | None:
    """The master's own retirement record, when its folder holds one."""

    stored = observed.proof_file
    if stored is None:
        return None
    try:
        return MasterRetirementProof.model_validate_json(stored.read_bytes())
    except (OSError, ValueError) as exc:
        raise SprintLinkageError(
            f"master {observed.key} retirement proof {stored} is unreadable: {exc}; "
            "restore the admitted proof before retrying task_doc.retire_master"
        ) from exc


def retirement_recorded(request: SprintLinkageRequest) -> bool:
    """Whether a retirement record already concerns this request.

    A record counts when it is the named master's (a row on any sprint, or its own proof file) or
    stands on the document the request addresses, for whichever master. It is read leniently from
    the raw files, because it is asked on the refusal path too, where the payload or a document
    may be the very thing that could not be parsed.
    """

    addressed = request.task_root / f"{request.slug or 'task'}.json"
    if _retired_rows(addressed):
        return True
    try:
        master_ref = TaskDocumentRef.model_validate(request.fields.get("masterRef"))
    except ValidationError:
        return False
    name = Path(master_ref.path).parent.name
    if name in {"", ".", ".."}:
        return False
    tasks = request.coordination_root / "tasks" / master_ref.repository
    if any(proof_path(folder).is_file() for folder in (tasks / name, tasks / ARCHIVE_DIR / name)):
        return True
    wanted = master_ref.model_dump(mode="json")
    return any(
        row.get("masterRef") == wanted
        for path in sorted(tasks.glob("*/task.json"))
        for row in _retired_rows(path)
    )


def _retired_rows(path: Path) -> list[dict[str, Any]]:
    """The retirement proofs a task document's rows carry, read without validating the document."""

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = loaded.get("subTasks") if isinstance(loaded, dict) else None
    return [
        row["retirement"]
        for row in rows or []
        if isinstance(row, dict) and isinstance(row.get("retirement"), dict)
    ]
