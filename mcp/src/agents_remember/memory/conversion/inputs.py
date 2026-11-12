"""Read a memory tree to convert, from a working directory or from a Git tree (MIK-R24 rule 6).

Both readers return the same :class:`MemoryInput`: the exact bytes of every file under
``knowledge/`` and ``onboarding/`` (the route-index cache excluded, as the validator reads them) and a
readable path to legacy ``knowledge.sqlite`` when conversion needs it. An already-converted Git
tree returns its text files without resolving or materializing a historical database blob. A legacy
database read from Git is copied to scratch because SQLite opens files, not blobs; it is opened
read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.memory.conversion.convert import MemoryInput
from agents_remember.memory_quality.knowledge_validator.trees import (
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)

DATABASE_NAME: Final = "knowledge.sqlite"


def memory_from_directory(root: Path, *, label: str | None = None) -> MemoryInput:
    """The memory working tree at ``root``."""

    tree = knowledge_tree_from_directory(root, label=label)
    database = root / DATABASE_NAME
    return MemoryInput(
        label=tree.label, files=tree.files, database=database if database.is_file() else None
    )


def memory_from_git(
    repository: Path, treeish: str, scratch: Path, *, label: str | None = None
) -> MemoryInput:
    """The memory tree ``treeish``; only an unconverted tree's database is copied to ``scratch``."""

    tree = knowledge_tree_from_git(repository, treeish, label=label)
    if tree.converted:
        return MemoryInput(label=tree.label, files=tree.files, database=None)
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{treeish}:{DATABASE_NAME}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    blob = result.stdout.strip()
    database: Path | None = None
    if result.returncode == 0 and blob:
        scratch.mkdir(parents=True, exist_ok=True)
        database = scratch / f"{blob}.sqlite"
        if not database.is_file():
            database.write_bytes(read_git_blobs_bytes(repository, [blob])[blob])
    return MemoryInput(label=tree.label, files=tree.files, database=database)


def write_changed(root: Path, changed: dict[str, bytes]) -> None:
    """Write a finished conversion's changed files under the memory working tree ``root``.

    Called only after the whole converted tree validated, so a refused conversion writes nothing.
    Each file is written through a sibling temporary file and a rename.
    """

    for relative, data in sorted(changed.items()):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.converting")
        temporary.write_bytes(data)
        temporary.replace(target)
