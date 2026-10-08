"""How a master and the sprint that commands it find each other, and which sprint row is the master's.

A sprint commands a master through ``orchestrates``. Its row for that master is either typed
(``masterRef``) or, on sprints older than typed linkage, a seat row: a row whose ``file`` is the
seat document that coordinates the master and names it in its references. A sprint may also hold
no row for a commanded master at all. The linkage report states these shapes as facts; the
operations that complete or retire a master's row read them through this one correlation.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.models.task_document_ref import TaskDocumentRef

from .document import SubTaskRef, TaskDocument
from .store import read_task_doc
from .task_paths import ARCHIVE_DIR, ENCLOSURES_DIR

SEAT_DOC_FILE = re.compile(r"^(\d+_manage-|00_.*-seat)")
_SEAT_MASTER_REFERENCE = re.compile(r"\.\./([^/]+)/task\.json$")


def correlate_seat_row(
    sprint_folder: Path, repository: str, row: SubTaskRef
) -> TaskDocumentRef | None:
    """The master a legacy seat-doc row coordinates, via the seat doc's references."""

    seat_json = (sprint_folder / (row.file or "")).with_suffix(".json")
    try:
        seat = read_task_doc(seat_json)
    except (OSError, ValueError):
        return None
    for reference in seat.references:
        match = _SEAT_MASTER_REFERENCE.search(reference.strip())
        if match:
            return TaskDocumentRef(repository=repository, path=f"{match.group(1)}/task.json")
    return None


@dataclass(frozen=True)
class MasterRows:
    """The rows of one sprint that stand for one master it commands."""

    master_ref: TaskDocumentRef
    typed: tuple[SubTaskRef, ...]
    legacy: tuple[SubTaskRef, ...]

    @property
    def row(self) -> SubTaskRef | None:
        """The one row that stands for the master: its typed row, else the one correlated seat row."""

        if self.typed:
            return self.typed[0] if len(self.typed) == 1 else None
        return self.legacy[0] if len(self.legacy) == 1 else None

    @property
    def fact(self) -> dict[str, Any] | None:
        """The linkage fact that says why no typed row stands for the master; ``None`` when one does."""

        if self.typed:
            return None
        master = self.master_ref.key
        if len(self.legacy) == 1:
            return {"kind": "slug-only-membership", "master": master, "row": self.legacy[0].number}
        if self.legacy:
            return {
                "kind": "seat-doc-row-ambiguous",
                "master": master,
                "rows": [row.number for row in self.legacy],
            }
        return {"kind": "membership-without-row", "master": master}


def master_rows(
    sprint: TaskDocument, sprint_folder: Path, master_ref: TaskDocumentRef
) -> MasterRows:
    """Find the typed row and the correlated legacy seat rows of one commanded master."""

    typed = tuple(row for row in sprint.subTasks if row.masterRef == master_ref)
    legacy = tuple(
        row
        for row in sprint.subTasks
        if row.masterRef is None
        and row.retirement is None
        and SEAT_DOC_FILE.match(row.file or "")
        and correlate_seat_row(sprint_folder, master_ref.repository, row) == master_ref
    )
    return MasterRows(master_ref, typed, legacy)


@dataclass(frozen=True)
class SprintCensus:
    """Which task documents of a repository name one master, read as raw JSON and nothing more.

    ``commanding`` holds the documents whose ``orchestrates`` names the master and ``recording``
    the ones with a row that carries its retirement proof; a caller reads those in full and
    refuses by name when it cannot. A document that could not be opened, decoded or parsed is in
    ``unreadable_naming`` when its text still names the master, and in ``unreadable_other`` when
    it does not or when its text could not be read at all.
    """

    commanding: tuple[Path, ...]
    recording: tuple[Path, ...]
    unreadable_naming: tuple[Path, ...]
    unreadable_other: tuple[Path, ...]

    @property
    def unreadable(self) -> tuple[Path, ...]:
        """Every document of which it is not known whether it commands or records the master."""

        return (*self.unreadable_naming, *self.unreadable_other)


def sprint_census(
    repository_tasks: Path, master_path: Path, master_ref: TaskDocumentRef, master: TaskDocument
) -> SprintCensus:
    """Find every ``task.json`` that commands the master or records its retirement.

    The two callers read the unreadable ones differently, and both on purpose. Finalization
    cannot archive anything, so a broken document elsewhere in the tree stops it only when its
    text names the master. A retirement archives a folder and cannot be undone, so it is stopped
    by every document it cannot read.
    """

    names = {master_path.parent.name, master.id, master.title}
    own = master_path.resolve(strict=False)
    wanted = master_ref.model_dump(mode="json")
    commanding: list[Path] = []
    recording: list[Path] = []
    naming: list[Path] = []
    other: list[Path] = []
    for path in sorted(repository_tasks.rglob("task.json")):
        parts = path.relative_to(repository_tasks).parts
        if ARCHIVE_DIR in parts or ENCLOSURES_DIR in parts or path.resolve(strict=False) == own:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            other.append(path)
            continue
        membership = _raw_membership(text)
        if membership is None:
            named = any(json.dumps(name) in text for name in names)
            (naming if named else other).append(path)
            continue
        commanded, retired = membership
        if names.intersection(commanded):
            commanding.append(path)
        if wanted in retired:
            recording.append(path)
    other.extend(_closed_task_folders(repository_tasks))
    return SprintCensus(tuple(commanding), tuple(recording), tuple(naming), tuple(other))


def _closed_task_folders(repository_tasks: Path) -> list[Path]:
    """The ``task.json`` of every task folder that cannot be looked into.

    A search of the tree passes over a folder it may not list, and with it over the sprint
    document that may lie in it. Such a folder's document is one that cannot be opened.
    """

    closed: list[Path] = []
    try:
        folders = sorted(repository_tasks.iterdir())
    except OSError:
        return [repository_tasks / "task.json"]
    for folder in folders:
        if folder.name == ARCHIVE_DIR or folder.is_symlink() or not folder.is_dir():
            continue
        try:
            os.stat(folder / "task.json")
        except FileNotFoundError:
            continue
        except OSError:
            closed.append(folder / "task.json")
    return closed


def _raw_membership(text: str) -> tuple[list[str], list[object]] | None:
    """A task document's membership entries and the masters its rows record as retired.

    ``None`` when the text is not a JSON object whose ``orchestrates`` and ``subTasks`` are lists.
    """

    try:
        loaded = json.loads(text)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    commanded, rows = loaded.get("orchestrates", []), loaded.get("subTasks", [])
    if not isinstance(commanded, list) or not isinstance(rows, list):
        return None
    return (
        [entry for entry in commanded if isinstance(entry, str)],
        [
            row["retirement"].get("masterRef")
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("retirement"), dict)
        ],
    )


def membership_removal_action(
    sprint_key: str, sprint: TaskDocument, master_ref: TaskDocumentRef, names: set[str]
) -> str:
    """The edit that takes one master out of one sprint's membership, and works on that sprint.

    ``task_doc.detach_master`` acts on a typed row and refuses without one. A sprint that
    commands the master without a typed row loses it by an edit of ``orchestrates`` itself, in
    one call with the graph when the graph places the master. No edit takes away the last master
    of a sprint that records no retirement, and the text says so instead of naming one.
    """

    where = f"on sprint {sprint_key}"
    if any(row.masterRef == master_ref for row in sprint.subTasks):
        return f"{where}: task_doc.detach_master with fields={{masterRef:{master_ref.key}}}"
    remaining = [entry for entry in sprint.orchestrates if entry not in names]
    if not remaining and not any(row.retirement is not None for row in sprint.subTasks):
        return (
            f"{where}, which holds no typed row for it and commands no other master: this build "
            "has no edit that takes a sprint's last master away, so keep this sprint as the one "
            "that commands it"
        )
    fields = f"orchestrates:{json.dumps(remaining)}"
    graph = sprint.executionGraph
    if graph is not None and master_ref in graph.master_refs():
        fields += (
            f", executionGraph:<the graph as it is, without the node of {master_ref.key} and "
            "without the edges that touch it>"
        )
    if sprint.integrationBranch is None:
        fields += (
            ", integrationBranch:<the sprint's integration branch, which this edit requires and "
            "the sprint does not declare yet>"
        )
    return f"{where}, which holds no typed row for it: task_doc.set_field with fields={{{fields}}}"
