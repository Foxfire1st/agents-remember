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
from collections.abc import Iterator
from pathlib import Path

SERIES_CONTRACT_FILENAME = "series-contract.md"
ARCHIVE_DIR = "0_archive"
ENCLOSURES_DIR = "enclosures"
NOTES_DIR = "notes"


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
    if not tasks_root.is_dir():
        return
    for path in sorted(_iter_enclosure_contract_candidates(tasks_root)):
        if is_archived_path(path):
            continue
        if is_below_notes(path, tasks_root):
            continue
        yield path


def _iter_enclosure_contract_candidates(tasks_root: Path) -> Iterator[Path]:
    """Yield ``enclosures/*/series-contract.md`` without entering dead folders.

    The traversal never descends into a ``notes`` or ``0_archive`` directory,
    so evidence copies and archived tasks cost no directory walk (MIK-R134
    rules 2 and 3, D149). Children of an ``enclosures`` directory itself are
    still entered, so an enclosure leaf directory that happens to be named
    ``notes`` is found exactly as before. Yielded order is unsorted; the caller
    sorts.
    """

    for dirpath, dirnames, _filenames in os.walk(tasks_root):
        if Path(dirpath).name != ENCLOSURES_DIR:
            dirnames[:] = [name for name in dirnames if name not in (NOTES_DIR, ARCHIVE_DIR)]
        else:
            for leaf in dirnames:
                candidate = Path(dirpath, leaf, SERIES_CONTRACT_FILENAME)
                if os.path.lexists(candidate):
                    yield candidate
