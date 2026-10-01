"""The writer's base: ``HEAD``, or its conversion when the candidate is converted (MIK-R24 rule 7).

The writer compares the candidate (the memory working tree) with its base for three things: an
existing record's revision before this operation, an entry's ``before`` anchor, and the validator's
carried anchors and frozen history files. Its base is the memory worktree's ``HEAD``. When ``HEAD``
is unconverted and the candidate is converted -- the converting leaf before its closeout commits the
conversion, or a crossing sync whose own side is unconverted -- the base is ``HEAD``'s conversion,
exactly as the worklist, the onboarding gate and the reviewer see it: the same
:func:`~agents_remember.application.knowledge_worklist.base_cache.converted_base_files`, cached
under the same key (memory commit, conversion-format version, paired code commit), so one base is
converted once and every reader sees it.

A base without a ``Code-Commit`` trailer is converted at the code commit the request names (a
leaf's code base B, exactly the gate's and the worklist's fallback, so the cache key is theirs), or
at the code tree's ``HEAD`` when it names none.

On a converted ``HEAD`` nothing changes: it is the base as it was. A conversion that cannot be built,
or a ``HEAD`` whose tree Git cannot read, is a named problem; the writer then refuses rather than
compare against nothing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_worklist.base_cache import converted_base_files
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitPreparationError,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.conversion.base import pinned_version
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTree,
    KnowledgeTreeReadError,
    knowledge_tree_from_git,
)

__all__ = ["BaseCode", "BaseSides", "writer_bases"]


@dataclass(frozen=True)
class BaseSides:
    """``HEAD``, converted when the candidate needs it, or why its conversion could not be built."""

    base: KnowledgeTree | None
    problem: str | None = None


@dataclass(frozen=True)
class BaseCode:
    """What converting an unconverted base reads, and where its conversion is cached.

    ``fallback`` is the commit-ish a base without a ``Code-Commit`` trailer is converted at: the
    leaf's code base B (the gate's and the worklist's fallback), or the code tree's ``HEAD`` when
    it is ``None``.
    """

    root: Path | None = None
    fallback: str | None = None
    cache_directory: Path | None = None


def _commit(root: Path, treeish: str) -> str | None:
    result = run_git(
        root,
        ["rev-parse", "--verify", "--quiet", f"{treeish}^{{commit}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


class _BaseUnreadable(ValueError):
    """``HEAD`` resolves, but Git cannot read its tree."""


def _side(root: Path, label: str, candidate: KnowledgeTree, code: BaseCode) -> KnowledgeTree | None:
    """``label``'s tree; its conversion when it is unconverted and the candidate is converted."""

    commit = _commit(root, label)
    if commit is None:
        return None  # an unborn branch: there is no base, and every record is new
    try:
        tree = knowledge_tree_from_git(root, commit, label=f"{root.name}@{label}")
    except (
        KnowledgeTreeReadError,
        GitPreparationError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        raise _BaseUnreadable(f"{label} ({commit[:12]}) cannot be read: {error!r}") from error
    if tree.converted or not candidate.converted or code.root is None:
        return tree
    version = pinned_version(candidate)
    fallback = code.fallback or "HEAD"
    code_commit = _commit(code.root, fallback)
    if version is None or code_commit is None:
        raise ValueError(
            f"the candidate's conversion version or the code commit {fallback!r} is unknown"
        )
    files = converted_base_files(
        root,
        commit,
        code=(code.root, code_commit),
        version=version,
        cache_directory=code.cache_directory,
    )
    return KnowledgeTree(label=f"converted base {label} {commit[:12]}", files=files)


def writer_bases(root: Path, candidate: KnowledgeTree, code: BaseCode) -> BaseSides:
    """The writer's base for the memory worktree ``root`` (none when it is not a Git work tree)."""

    if not (root / ".git").exists():
        return BaseSides(None)
    try:
        return BaseSides(_side(root, "HEAD", candidate, code))
    except _BaseUnreadable as error:
        return BaseSides(
            None,
            "the base of this operation, the memory worktree's HEAD, cannot be read, so it has "
            f"nothing to compare against: {error}",
        )
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        return BaseSides(
            None,
            "the converted base (MIK-R24 rule 7) of the memory worktree's HEAD cannot be built, "
            f"so this operation has nothing to compare against: {error}",
        )
