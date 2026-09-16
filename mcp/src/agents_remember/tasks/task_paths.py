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

import re
from collections.abc import Iterator
from pathlib import Path

SERIES_CONTRACT_FILENAME = "series-contract.md"
ARCHIVE_DIR = "0_archive"
ENCLOSURES_DIR = "enclosures"


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


def iter_leaf_enclosure_contracts(tasks_root: Path) -> Iterator[Path]:
    if not tasks_root.is_dir():
        return
    for path in sorted(tasks_root.rglob(f"{ENCLOSURES_DIR}/*/{SERIES_CONTRACT_FILENAME}")):
        if not is_archived_path(path):
            yield path
