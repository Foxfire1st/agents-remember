"""The memory's ignore line for briefs.

Briefs are derived from the memory by the product and never edited, so they never enter Git. A
memory created by ``memory_init`` starts with the line. Other writers add it during their owned
content preparation, before the resulting tree is selected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)

BRIEFS_IGNORE_LINE: Final = "/briefs/"
BRIEFS_IGNORE_TEXT: Final = (
    "# Briefs of source files: derived by the product from this memory, never edited, never in Git.\n"
    f"{BRIEFS_IGNORE_LINE}\n"
)
_ALREADY: Final = frozenset({BRIEFS_IGNORE_LINE, "briefs/", "/briefs", "briefs"})


def _ignore_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="surrogateescape")
    except FileNotFoundError:
        return ""


def _names_briefs(text: str) -> bool:
    return any(line.strip() in _ALREADY for line in text.splitlines())


def briefs_ignored(memory_root: Path) -> bool:
    """Whether the memory declares an ignore rule and Git excludes the whole folder."""

    if not _names_briefs(_ignore_text(memory_root / ".gitignore")):
        return False
    if not (memory_root / ".git").exists():
        return False
    result = run_git(
        memory_root,
        ["check-ignore", "--quiet", "--no-index", "--", "briefs/"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    return result.returncode == 0


def with_briefs_ignore(text: str) -> str:
    """Include the briefs rule while preserving the existing ignore text."""

    if _names_briefs(text):
        return text
    separator = "" if not text or text.endswith("\n") else "\n"
    gap = "\n" if text else ""
    return f"{text}{separator}{gap}{BRIEFS_IGNORE_TEXT}"


def ignore_briefs(memory_root: Path) -> bool:
    """Add the line during a writer's owned preparation and say whether it was added."""

    ignore = memory_root / ".gitignore"
    text = _ignore_text(ignore)
    prepared = with_briefs_ignore(text)
    if prepared == text:
        return False
    atomic_write_bytes(ignore, prepared.encode("utf-8", errors="surrogateescape"))
    return True


__all__ = [
    "BRIEFS_IGNORE_LINE",
    "BRIEFS_IGNORE_TEXT",
    "briefs_ignored",
    "ignore_briefs",
    "with_briefs_ignore",
]
