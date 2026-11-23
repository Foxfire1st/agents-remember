"""The writer's operation bases and the recorded leaf base for card comparison.

The writer compares the candidate (the memory working tree) with its base for three things: an
existing record's revision before this operation, an entry's ``before`` anchor, and the validator's
carried anchors and frozen history files. Its base is the memory worktree's ``HEAD``. When ``HEAD``
is unconverted and the candidate is converted -- the converting leaf before its closeout commits the
conversion, or a crossing sync whose own side is unconverted -- the operation uses
``HEAD``'s conversion. The onboarding card comparison takes the recorded leaf base and uses
the same conversion machinery. Each conversion is cached by memory commit, format version and
paired code commit.

A base without a readable ``Code-Commit`` trailer is converted at the code commit the request
names (B for a leaf), or at the code tree's ``HEAD`` when it names none.

On a converted ``HEAD`` nothing changes: it is the base as it was. A conversion that cannot be built,
or a ``HEAD`` whose tree Git cannot read, is a named problem; the writer then refuses rather than
compare against nothing.

**During a merge** (``MERGE_HEAD`` resolves in the memory repository) the incoming parent is a
second validation base, converted by the same rule through the same cache, so an anchor the
incoming side carries is judged as carried, exactly as the merge commit's own validation judges it
against both parents. Revisions and ``before`` anchors still come from ``HEAD`` alone. An incoming
parent Git cannot read, or whose conversion cannot be built, is a named problem like ``HEAD``'s:
that base is never silently omitted or replaced by an empty tree. Outside a merge nothing changes.
The memory-quality check reads its bases through :func:`writer_bases` too, so it sees the same two.
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

__all__ = ["BaseCode", "BaseSides", "recorded_card_base", "writer_bases"]


@dataclass(frozen=True)
class BaseSides:
    """``HEAD``, converted when the candidate needs it, and during a merge the incoming parent
    ``MERGE_HEAD`` converted by the same rule; or why one of them could not be read or built."""

    base: KnowledgeTree | None
    problem: str | None = None
    incoming: KnowledgeTree | None = None

    @property
    def validation_bases(self) -> tuple[KnowledgeTree, ...]:
        """The converted sides a tree is validated against: ``HEAD``, then ``MERGE_HEAD``."""

        return tuple(
            side for side in (self.base, self.incoming) if side is not None and side.converted
        )


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
    """The side resolves, but Git cannot read it as a commit tree."""


def _merge_head(root: Path) -> str | None:
    """The object ``MERGE_HEAD`` names while the memory repository is in a merge, else ``None``."""

    result = run_git(
        root,
        ["rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else None


def _side(
    root: Path, label: str, commit: str, candidate: KnowledgeTree, code: BaseCode
) -> KnowledgeTree:
    """``label``'s tree; its conversion when it is unconverted and the candidate is converted."""

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


_BASE_ERRORS = (ValueError, KeyError, OSError, subprocess.SubprocessError)


def recorded_card_base(
    root: Path, commit: str, candidate: KnowledgeTree, code: BaseCode
) -> KnowledgeTree | None:
    """Resolve the recorded side in the candidate's format for the onboarding card comparison."""

    try:
        return _side(root, "recorded leaf base", commit, candidate, code)
    except _BASE_ERRORS:
        return None


def _head(root: Path, candidate: KnowledgeTree, code: BaseCode) -> BaseSides:
    try:
        commit = _commit(root, "HEAD")
        if commit is None:
            return BaseSides(None)  # an unborn branch: there is no base, and every record is new
        return BaseSides(_side(root, "HEAD", commit, candidate, code))
    except _BaseUnreadable as error:
        return BaseSides(
            None,
            "the base of this operation, the memory worktree's HEAD, cannot be read, so it has "
            f"nothing to compare against: {error}",
        )
    except _BASE_ERRORS as error:
        return BaseSides(
            None,
            "the converted base (MIK-R24 rule 7) of the memory worktree's HEAD cannot be built, "
            f"so this operation has nothing to compare against: {error}",
        )


def _with_incoming(
    root: Path, head: BaseSides, candidate: KnowledgeTree, code: BaseCode
) -> BaseSides:
    """``head``, with the incoming parent ``MERGE_HEAD`` beside it while a merge is in progress."""

    try:
        name = _merge_head(root)
        if name is None:
            return head  # outside a merge: the one base, exactly as before
        commit = _commit(root, "MERGE_HEAD")
        if commit is None:
            raise _BaseUnreadable(f"MERGE_HEAD ({name[:12]}) names no commit Git can read")
        return BaseSides(head.base, incoming=_side(root, "MERGE_HEAD", commit, candidate, code))
    except _BaseUnreadable as error:
        return BaseSides(
            None,
            "the incoming merge parent of this operation, the memory worktree's MERGE_HEAD, "
            f"cannot be read, so it cannot be validated against both merge parents: {error}. "
            "Conclude the merge or abort it (git merge --abort) in the memory worktree, then retry",
        )
    except _BASE_ERRORS as error:
        return BaseSides(
            None,
            "the converted base of the memory worktree's MERGE_HEAD, the text-format side a "
            "converted tree is compared with, cannot be built, so this operation cannot be "
            f"validated against both merge parents: {error}. Conclude the merge or abort it "
            "(git merge --abort) in the memory worktree, then retry",
        )


def writer_bases(root: Path, candidate: KnowledgeTree, code: BaseCode) -> BaseSides:
    """The writer's bases for the memory worktree ``root`` (none when it is not a Git work tree):
    ``HEAD``, and during a merge the incoming parent ``MERGE_HEAD`` beside it."""

    if not (root / ".git").exists():
        return BaseSides(None)
    head = _head(root, candidate, code)
    return head if head.problem is not None else _with_incoming(root, head, candidate, code)
