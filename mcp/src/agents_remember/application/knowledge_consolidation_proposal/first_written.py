"""When each family record was first written: the commit that first added its file.

A family's writer stamp names the writer of its current revision only, and the one-time conversion
stamped every older record with the conversion's own time, so the stamps cannot order the families
by their first writing. The memory's Git history can: the first commit that added a family's file
is when its record was first written. One ``git log`` over the family folder answers every family
at once; the file name begins with the record id, which a slug change keeps, so the earliest added
file whose name starts with the id is the record's first writing.

A family whose file no commit added yet (written in the working tree only) has no time here; the
proposal orders it after every committed family.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final

from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, RECORD_DIRECTORIES

FAMILY_DIRECTORY: Final = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES['family']}"
# Git writes ``%x00`` as a NUL byte, which no path holds, ahead of each commit time.
_COMMIT_MARK: Final = "\x00"
# Every commit that added a family file, newest first, each as its commit time and the added paths.
_FIRST_ADDS: Final = [
    "log",
    "--no-renames",
    "--diff-filter=A",
    "--format=%x00%cI",
    "--name-only",
    "--",
    FAMILY_DIRECTORY,
]


def first_written(memory_root: Path, families: set[str]) -> dict[str, datetime]:
    """Each named family's first commit time; families no commit added are left out.

    A memory root that is no Git checkout, or whose log cannot be read, answers no time at all, so
    every family then orders after the committed ones, by id.
    """

    try:
        result = run_git(memory_root.resolve(), _FIRST_ADDS)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    earliest: dict[str, datetime] = {}
    moment: datetime | None = None
    for line in result.stdout.splitlines():
        if line.startswith(_COMMIT_MARK):
            moment = datetime.fromisoformat(line[1:].strip())
            continue
        family = _family_of(line.strip(), families)
        if family is None or moment is None:
            continue
        if family not in earliest or moment < earliest[family]:
            earliest[family] = moment
    return earliest


def _family_of(path: str, families: set[str]) -> str | None:
    """The family id a file name ``<ID>-<slug>.json`` begins with, when it is a known family."""

    name = PurePosixPath(path).name
    parts = name.split("-", 2)
    if len(parts) < 2:
        return None
    record_id = f"{parts[0]}-{parts[1].removesuffix('.json')}"
    return record_id if record_id in families else None
