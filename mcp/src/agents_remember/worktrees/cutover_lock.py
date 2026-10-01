"""The cutover lock: unconverted memory is read, never written, checked, synced or landed.

MIK-R09 rule 6 (second bullet) and MIK-R24 rule 9: a route whose memory holds the layout marker
``knowledge/layout.json`` on neither side is unconverted, and the new runtime refuses it, naming the
crossing sync. Reads still work (``read_ar_files`` marks the card ``legacy-format``), and code may
still change; every write, memory-quality run, managed memory sync other than a crossing sync,
closeout and landing refuses (MIK-R37 rule 6).

**When the lock holds (L37 ruling).** The lock holds for a memory repository once that repository
holds converted memory anywhere: a local branch whose tip holds the marker, or a registered worktree
whose working tree does. That is the observable fact of the cutover window -- the cutover leaf's
memory worktree holds the converted tree before its build is installed (MIK-R37 rule 2), and every
converted line descends from it -- so the installed build meets a locked repository from its
installation on, and every other agents-remember master's unconverted line is refused at once. A
repository that holds no converted memory at all is not locked: every route behaves exactly as it
did before this master, which is what keeps the lock inert on unconverted lines until the installed
build meets them (the build's own unconverted fixtures, the base-against-worktree comparison, and
any repository that has not crossed). A converting candidate is never refused: it holds the marker
on its own side, so the gate applies to it instead, against the converted base (MIK-R24 rule 7).

**Fail closed.** A probe Git cannot answer refuses by name; it is never read as "no converted
memory". The caller decides that the route's sides are unconverted; this module only answers
whether the repository is locked and words the refusal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH

__all__ = [
    "CUTOVER_LOCK_CODE",
    "CutoverProbeError",
    "converted_memory_location",
    "cutover_lock_refusal",
]

CUTOVER_LOCK_CODE: Final = "unconverted-memory-locked"
_WORKTREE_PREFIX: Final = "worktree "


class CutoverProbeError(RuntimeError):
    """Git could not say whether the memory repository holds converted memory."""


def _git(repository: Path, args: list[str], *, input_text: str | None = None) -> str:
    try:
        result = run_git(
            repository,
            args,
            GitRunnerOptions(input_text=input_text, timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError) as error:  # named, never "unlocked"
        raise CutoverProbeError(
            f"git {args[0]} failed or timed out in {repository} ({type(error).__name__})"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise CutoverProbeError(f"git {args[0]} failed in {repository}: {detail}")
    return result.stdout


def _converted_branch(repository: Path) -> str | None:
    listed = _git(repository, ["for-each-ref", "--format=%(objectname) %(refname)", "refs/heads"])
    branches = [line.split(" ", 1) for line in listed.splitlines() if " " in line]
    if not branches:
        return None
    query = "".join(f"{commit}:{LAYOUT_MARKER_PATH}\n" for commit, _ref in branches)
    answers = _git(repository, ["cat-file", "--batch-check=%(objecttype)"], input_text=query)
    for (_commit, ref), answer in zip(branches, answers.splitlines(), strict=False):
        if answer.strip() == "blob":
            return f"branch {ref.removeprefix('refs/heads/')}"
    return None


def _converted_worktree(repository: Path) -> str | None:
    listed = _git(repository, ["worktree", "list", "--porcelain"])
    for line in listed.splitlines():
        if line.startswith(_WORKTREE_PREFIX):
            worktree = Path(line[len(_WORKTREE_PREFIX) :])
            if (worktree / LAYOUT_MARKER_PATH).is_file():
                return f"worktree {worktree.as_posix()}"
    return None


def _holds_git_entry(directory: Path) -> bool:
    """Whether ``directory`` or a parent holds a ``.git`` entry, or it is itself a bare repository."""

    if (directory / "HEAD").is_file() and (directory / "objects").is_dir():
        return True
    return any(
        (one / ".git").exists() or (one / ".git").is_symlink()
        for one in (directory, *directory.parents)
    )


def _is_repository(repository: Path) -> bool:
    """Whether ``repository`` is in a Git repository at all (a plain directory has no lines).

    Only a definite non-repository answers ``False``: Git finds no repository, and nothing on disk
    says there is one (no ``.git`` entry in the directory or a parent, and no bare repository).
    Every other failure -- a linked worktree whose gitdir was pruned, ``safe.directory`` ownership,
    a permission error, a broken repository -- raises :class:`CutoverProbeError` (MIK-R09 rule 6).
    """

    try:
        result = run_git(
            repository,
            ["rev-parse", "--git-dir"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CutoverProbeError(
            f"git rev-parse failed or timed out in {repository} ({type(error).__name__})"
        ) from error
    if result.returncode == 0:
        return True
    if not _holds_git_entry(repository):
        return False
    detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
    raise CutoverProbeError(f"git rev-parse failed in {repository}: {detail}")


def converted_memory_location(repository: Path) -> str | None:
    """Where this memory repository holds converted memory (a branch or a worktree), or ``None``.

    A directory that is not in a Git repository has no lines, so it holds none. Raises
    :class:`CutoverProbeError` when Git cannot answer.
    """

    if not _is_repository(repository):
        return None
    return _converted_branch(repository) or _converted_worktree(repository)


def cutover_lock_refusal(repository: Path | None, *, operation: str, line: str) -> str | None:
    """Refuse ``operation`` on the unconverted memory of ``line`` once the repository is locked.

    The caller has established that the route's memory is unconverted on every side. ``None`` means
    the repository holds no converted memory, so the route behaves exactly as before this master.
    """

    if repository is None or not repository.is_dir():
        return None
    try:
        where = converted_memory_location(repository)
    except CutoverProbeError as error:
        return (
            f"{operation} refuses the unconverted memory of {line}: the cutover lock cannot tell "
            f"whether this memory repository already holds converted memory ({error}), and an "
            "unanswered probe is never read as unlocked (MIK-R09 rule 6)"
        )
    if where is None:
        return None
    return (
        f"{operation} refuses the unconverted memory of {line}: this memory repository already "
        f"holds converted memory ({where}), so unconverted memory is only read (legacy-format) and "
        "is never written, checked, synced or landed (MIK-R24 rule 9, MIK-R09 rule 6). The line "
        "converts through the crossing sync: once the line it syncs from holds converted memory, "
        "run worktree_sync, which crosses the boundary (MIK-R24 rule 8)"
    )
