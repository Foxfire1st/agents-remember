"""The cutover lock: unconverted memory is read, never written, checked, synced or landed.

MIK-R46 rule 1 (one rule): a check, a write, a memory sync, a closeout or a landing whose
memory input is unconverted refuses. ``cutover_lock_refusal`` no longer returns "not locked"
for a repository without converted memory: the caller has established that the route's
memory is unconverted on every side, and the refusal is unconditional. The probe
:func:`converted_memory_location` stays and only words the message (where converted memory
already is); it decides nothing. Reads still work (``read_ar_files`` marks the card
``legacy-format`` with no knowledge section), and code may still change; every write,
memory-quality run, managed memory sync other than a crossing sync or an adopting
fast-forward, closeout and landing refuses.

MIK-R46 rule 2 (one message): every such refusal comes from
:func:`legacy_format_refusal` and carries the code ``legacy-format``. It names the
operation, the memory root or line, that the memory has no ``knowledge/layout.json``, and
how it converts: ``worktree_sync`` with ``merge-memory`` when the line it syncs from is
converted; otherwise the first conversion of rule 6. A route keeps the status name it
returns today; the detail text is this message. Answers that must fit a row
bound carry a short form of it once and keep the full text in the logged
result; a drift result is ``status: "error"`` without repeating it.

**Fail closed.** A probe Git cannot answer refuses by name; it is never read as "no converted
memory" and never as "legacy". The caller decides that the route's sides are unconverted;
this module only refuses and words the refusal. Missing, unreadable or corrupt memory keeps
its own error; it is never called "legacy" (MIK-R46 preservation).
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
    "FIRST_CONVERSION_COMMAND",
    "FIRST_CONVERSION_GUIDE",
    "FIRST_CONVERSION_SKILL",
    "LEGACY_FORMAT_CODE",
    "CutoverProbeError",
    "converted_memory_location",
    "cutover_lock_refusal",
    "first_conversion_procedure",
    "is_legacy_format_refusal",
    "legacy_format_refusal",
]

CUTOVER_LOCK_CODE: Final = "unconverted-memory-locked"
LEGACY_FORMAT_CODE: Final = "legacy-format"
_WORKTREE_PREFIX: Final = "worktree "

#: How a never-converted repository makes its first conversion (MIK-R46 rule 6).
FIRST_CONVERSION_COMMAND: Final = (
    "agents-remember knowledge-convert MEMORY_ROOT --code CODE_REPOSITORY"
)
#: The skill that describes the first-conversion procedure.
FIRST_CONVERSION_SKILL: Final = "c-13-install-and-onboard"
#: The guide page that describes the first-conversion procedure.
FIRST_CONVERSION_GUIDE: Final = "docs/guides/first-conversion.md"


def first_conversion_procedure() -> str:
    """How a never-converted repository makes its first conversion (MIK-R46 rule 6).

    Memory with no attributed baseline converts in place with the conversion command, then
    adopts the baseline; memory with an official line converts inside one new leaf's memory
    worktree as that leaf's first memory change, then closes out and lands like any leaf.
    The skill and the guide carry the full procedure.
    """

    return (
        f"make its first conversion with `{FIRST_CONVERSION_COMMAND}`, as the "
        f"{FIRST_CONVERSION_SKILL} skill and {FIRST_CONVERSION_GUIDE} describe (MIK-R46 rule 6)"
    )


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


def _converted_lines(repository: Path) -> str:
    """Every place this repository holds converted memory, as text: a branch, a worktree, or both.

    A reader needs the worktree (a directory to name as a memory root) and the branch (the line
    it belongs to), so both are named when both exist. Empty when the repository holds none.
    """

    if not _is_repository(repository):
        return ""
    found = (_converted_branch(repository), _converted_worktree(repository))
    return " and ".join(one for one in found if one is not None)


def is_legacy_format_refusal(text: str) -> bool:
    """Whether ``text`` is the one rule-2 message (it carries ``code legacy-format``).

    Named probe errors deliberately do not carry the code, so they never match.
    Routes use this to keep legacy refusals free of durable side effects without
    changing any other refusal's behavior.
    """

    return "code legacy-format" in text


def cutover_lock_refusal(repository: Path | None, *, operation: str, line: str) -> str | None:
    """Refuse ``operation`` on the unconverted memory of ``line`` (MIK-R46 rule 1).

    The caller has established that the route's memory is unconverted on every side, so the
    refusal is unconditional: a repository without converted memory is refused exactly as a
    repository with converted memory elsewhere is. The probe
    :func:`converted_memory_location` only words the message and decides nothing. ``None``
    only when there is no memory repository to judge: ``None``, not a directory, or
    definitely not a Git repository (a plain directory has no lines). A probe Git cannot
    answer refuses by name, never as unlocked and never as "legacy".
    """

    if repository is None or not repository.is_dir():
        return None
    try:
        if not _is_repository(repository):
            return None
        converted_memory_location(repository)
    except CutoverProbeError as error:
        return (
            f"{operation} refuses the unconverted memory of {line}: the cutover lock cannot tell "
            f"whether this memory repository already holds converted memory ({error}), and an "
            "unanswered probe is never read as unlocked (MIK-R09 rule 6)"
        )
    return legacy_format_refusal(
        repository, operation=operation, subject=f"the unconverted memory of {line}"
    )


def legacy_format_refusal(
    repository: Path | None, *, operation: str, subject: str, database_file: bool = False
) -> str:
    """Word the refusal of a route that was handed memory in the legacy format (MIK-R46 rule 2).

    The canonical knowledge database is retired: knowledge is read and written as files, so a
    route given an unconverted memory tree or a database file (``database_file``) has nothing to
    open. The refusal names the operation, the memory root or line, that the memory has no
    ``knowledge/layout.json``, and how it converts: ``worktree_sync`` with ``merge-memory``
    when the line it syncs from is converted; otherwise the first conversion of MIK-R46 rule 6
    (``agents-remember knowledge-convert MEMORY_ROOT --code CODE_REPOSITORY``, as the
    ``c-13-install-and-onboard`` skill describes). When Git can say, it names the line of
    ``repository`` that already holds converted memory. It decides nothing; the caller established
    the legacy input. It carries the code ``legacy-format``.
    """

    held = "This memory repository holds no converted memory yet."
    if repository is not None and repository.is_dir():
        try:
            where = _converted_lines(repository)
        except CutoverProbeError as error:
            held = f"Whether this memory repository holds converted memory is unknown ({error})."
        else:
            if where:
                held = f"Converted memory of this repository is on {where}; read it there."
    what = (
        "it is a knowledge database file, the legacy format, and no route opens one: name the "
        "root directory of a converted memory tree instead"
        if database_file
        else f"it is memory in the legacy format (code legacy-format; no {LAYOUT_MARKER_PATH})"
    )
    return (
        f"{operation} refuses {subject}: {what}. The canonical knowledge database is retired "
        "(MIK-R26), so knowledge is read and written only as files. A line that descends from a "
        "converted line converts through the crossing sync: run worktree_sync with merge-memory "
        "once the line it syncs from holds converted memory (MIK-R24 rule 8). A repository that "
        f"was never converted {first_conversion_procedure()}. {held}"
    )
