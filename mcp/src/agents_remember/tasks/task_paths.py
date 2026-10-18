"""The on-disk paths a task root owns, and the predicates over them.

These are task-domain rules, not worktree rules: a series contract, an enclosure
directory, a leaf's enclosure contract, and the archive segment are decided by
the task root alone. They live here so the task package can derive its own paths
without reaching up into ``worktrees`` (``layers.toml`` ranks ``tasks`` below
``worktrees``, and ``worktrees`` already imports ``tasks``).

``agents_remember.worktrees.task_resolver`` remains the published import site --
it re-exports every name here -- so this module is the single definition and no
caller has to change.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

SERIES_CONTRACT_FILENAME = "series-contract.md"
TASK_DOCUMENT_FILENAME = "task.json"
ARCHIVE_DIR = "0_archive"
ENCLOSURES_DIR = "enclosures"
NOTES_DIR = "notes"


def iter_task_folders(tasks_root: Path) -> Iterator[Path]:
    """Every live task folder of one repository's tasks root (MIK-R83 rule 1).

    A task folder is a direct child directory of ``tasks/<repository>/``; the
    archive segment is not one. This is the single walk: the five lookups that
    used to walk the whole task tree each enumerate the canonical places of the
    folders this yields, so a copy under ``notes/``, ``requirements/`` or any
    deeper folder is never met by a lookup at all (MIK-R83 rule 2).
    """

    if not tasks_root.is_dir():
        return
    for child in sorted(tasks_root.iterdir()):
        if child.name == ARCHIVE_DIR:
            continue
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        yield child


def iter_task_documents(tasks_root: Path) -> Iterator[Path]:
    """The canonical task documents: ``task.json`` at the root of each task folder.

    A path is yielded when it exists, including one that is unreadable or is a
    directory: a canonical place is never skipped silently, so the reader that
    opens it names it in its refusal (MIK-R83 Failure).
    """

    for folder in iter_task_folders(tasks_root):
        candidate = folder / TASK_DOCUMENT_FILENAME
        if os.path.lexists(candidate):
            yield candidate


def iter_task_folder_contracts(tasks_root: Path) -> Iterator[Path]:
    """The canonical series contracts: ``series-contract.md`` in a task folder."""

    for folder in iter_task_folders(tasks_root):
        candidate = folder / SERIES_CONTRACT_FILENAME
        if os.path.lexists(candidate):
            yield candidate


def iter_task_contracts(tasks_root: Path) -> Iterator[Path]:
    """Every canonical contract: a task's own, then its leaves' enclosure contracts."""

    for folder in iter_task_folders(tasks_root):
        own = folder / SERIES_CONTRACT_FILENAME
        if os.path.lexists(own):
            yield own
        yield from _enclosure_contracts(folder)


def _enclosure_contracts(task_folder: Path) -> Iterator[Path]:
    enclosures = task_folder / ENCLOSURES_DIR
    try:
        if not enclosures.is_dir():
            return
        leaves = sorted(enclosures.iterdir())
    except OSError:
        return
    for leaf in leaves:
        candidate = leaf / SERIES_CONTRACT_FILENAME
        try:
            if not leaf.is_dir() or not os.path.lexists(candidate):
                continue
        except OSError:
            continue
        yield candidate


def canonical_contract_place(path: Path) -> bool:
    """Whether ``path`` lies at one of the three places a contract is authority (rule 2).

    ``<tasks>/<repo>/<task>/series-contract.md`` and
    ``<tasks>/<repo>/<task>/enclosures/<leaf>/series-contract.md`` -- exactly
    those depths. A copy under ``notes/``, ``requirements/``, a dot-directory or
    any deeper folder is not one, however far above the ``tasks`` segment is.
    """

    if path.name != SERIES_CONTRACT_FILENAME:
        return False
    parts = path.parts
    for index, part in enumerate(parts):
        if part != "tasks":
            continue
        below = parts[index + 1 :]
        if len(below) == 3 and below[2] == SERIES_CONTRACT_FILENAME:
            return True
        if len(below) == 5 and below[2] == ENCLOSURES_DIR and below[4] == SERIES_CONTRACT_FILENAME:
            return True
    return False


def canonical_task_document_place(path: Path) -> bool:
    """Whether ``path`` is a leaf/master document at the root of a task folder (rule 2)."""

    if path.suffix != ".json":
        return False
    parts = path.parts
    for index, part in enumerate(parts):
        if part != "tasks":
            continue
        below = parts[index + 1 :]
        return len(below) == 3
    return False


def iter_stray_task_files(tasks_root: Path) -> Iterator[Path]:
    """Files named like a task document or a contract anywhere but a canonical place.

    The scan is for the linkage report only (MIK-R83 rule 4): it walks every
    live task folder, skipping the archive segment, ``notes/`` and
    ``requirements/`` whose copies are already outside every lookup, and yields
    each ``task.json`` or ``series-contract.md`` that is not at one of the
    canonical places of rule 2 with the folder it could not scan reported by
    :func:`scan_stray_task_files`.
    """

    yield from scan_stray_task_files(tasks_root)[0]


def scan_stray_task_files(tasks_root: Path) -> tuple[list[Path], list[Path]]:
    """The stray files, and the folders the scan could not read (rule 4, Failure).

    A folder that cannot be listed is named instead of failing the report: the
    rest of the scan still runs and lists what it found, so one unreadable
    folder never hides the strays of another.
    """

    strays: list[Path] = []
    unreadable: list[Path] = []

    def note_unreadable(error: OSError) -> None:
        if error.filename:
            unreadable.append(Path(error.filename))

    for folder in iter_task_folders(tasks_root):
        for dirpath, _dirnames, filenames in os.walk(folder, onerror=note_unreadable):
            strays.extend(_strays_in(Path(dirpath), filenames))
    return sorted(strays), sorted(unreadable)


def _strays_in(folder: Path, filenames: Sequence[str]) -> list[Path]:
    """The names in one folder that look like a task document or a contract but are not one."""

    found: list[Path] = []
    for name in (TASK_DOCUMENT_FILENAME, SERIES_CONTRACT_FILENAME):
        if name not in filenames:
            continue
        candidate = folder / name
        if name == SERIES_CONTRACT_FILENAME:
            if canonical_contract_place(candidate):
                continue
        elif canonical_task_document_place(candidate):
            continue
        if not is_archived_path(candidate):
            found.append(candidate)
    return found


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip(".-_")
    return slug or "task"


def series_contract_path(task_root: Path) -> Path:
    """The series contract at the root of one task folder."""

    return task_root / SERIES_CONTRACT_FILENAME


def leaf_enclosure_dir(task_root: Path, leaf_id: str) -> Path:
    """The leaf's enclosure directory, whose name is the slugified leaf id."""

    return task_root / ENCLOSURES_DIR / slugify(leaf_id)


def leaf_enclosure_path(task_root: Path, leaf_id: str) -> Path:
    """The leaf's own enclosure contract."""

    return leaf_enclosure_dir(task_root, leaf_id) / SERIES_CONTRACT_FILENAME


def is_archived_path(path: Path) -> bool:
    return ARCHIVE_DIR in path.parts


def is_enclosure_contract(path: Path) -> bool:
    return (
        path.name == SERIES_CONTRACT_FILENAME
        and len(path.parts) >= 3
        and path.parent.parent.name == ENCLOSURES_DIR
    )


def is_below_notes(path: Path, tasks_root: Path) -> bool:
    """Whether the contract lies below a task's ``notes`` folder.

    Real enclosures live at ``<task>/enclosures/<leaf>/series-contract.md``;
    anything with a ``notes`` segment above the trailing
    ``enclosures/<leaf>/series-contract.md`` is an evidence copy kept as review
    input, never an enclosure (MIK-R134 rule 3). The trailing three parts are
    excluded so an enclosure whose leaf directory happens to be named ``notes``
    still counts as an enclosure.
    """

    relative = path.relative_to(tasks_root)
    return NOTES_DIR in relative.parts[:-3]


def enclosure_repository_tasks(contract_path: Path) -> Path:
    """The repository tasks folder owning an enclosure contract, by position.

    ``contract_path`` must lie in enclosure position
    (``<repo-tasks>/<task>/enclosures/<leaf>/series-contract.md``, the only
    live geometry on this installation): the repository tasks folder is the
    parent of the task folder holding the ``enclosures`` directory.
    """

    return contract_path.parent.parent.parent.parent


def is_foreign_enclosure_contract(contract_path: Path, task_artifact: Path) -> bool:
    """Whether the contract's task document lies outside its repository's tasks.

    A contract file below the tasks folder whose task document is not inside
    the repository's tasks folder is not an enclosure of the repository
    (MIK-R134 rule 2): it is skipped wherever a walk meets it, and the caller
    names it in its own detail channel. Membership is positional (see
    :func:`enclosure_repository_tasks`), never taken from the contract's
    recorded roots, so a self-consistent foreign copy still counts as foreign.
    Both sides are filesystem-resolved first, so a recorded path that starts
    under the repository tasks yet escapes through ``..`` segments (or a
    symlink) still counts as foreign.
    """

    try:
        task_artifact.resolve().relative_to(enclosure_repository_tasks(contract_path).resolve())
    except (OSError, RuntimeError, ValueError):
        return True
    return False


def iter_leaf_enclosure_contracts(tasks_root: Path) -> Iterator[Path]:
    """Every leaf enclosure contract at a canonical place below ``tasks_root``.

    ``tasks_root`` is any of the three levels its callers pass: the
    coordination tasks root, one repository's tasks root, or one task folder.
    Whichever it is, the contracts come from the canonical place of each task
    folder's ``enclosures/<leaf>/`` (MIK-R83 rule 2), never from a copy under
    ``notes/``, ``requirements/`` or a deeper folder. Sorted by path.
    """

    found = {
        path
        for folder in _task_folders_below(tasks_root)
        for path in _enclosure_contracts(folder)
        if not is_archived_path(path)
    }
    yield from sorted(found)


def _task_folders_below(root: Path) -> Iterator[Path]:
    """The task folders below a tasks root, a repository's tasks root, or one task folder."""

    try:
        if not root.is_dir():
            return
    except OSError:
        return
    if _is_task_folder(root):
        yield root
        return
    for child in _child_directories(root):
        if _is_task_folder(child):
            yield child
            continue
        # A repository's tasks root: its direct children are the task folders.
        for folder in iter_task_folders(child):
            if _is_task_folder(folder):
                yield folder


def _child_directories(root: Path) -> list[Path]:
    """The child directories of ``root``, sorted, the archive segment left out."""

    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    children: list[Path] = []
    for entry in entries:
        if entry.name == ARCHIVE_DIR:
            continue
        try:
            if entry.is_dir():
                children.append(entry)
        except OSError:
            continue
    return children


def _is_task_folder(path: Path) -> bool:
    """Whether ``path`` holds a task's own document or contract, or enclosures."""

    try:
        return (
            os.path.lexists(path / TASK_DOCUMENT_FILENAME)
            or os.path.lexists(path / SERIES_CONTRACT_FILENAME)
            or (path / ENCLOSURES_DIR).is_dir()
        )
    except OSError:
        return False
