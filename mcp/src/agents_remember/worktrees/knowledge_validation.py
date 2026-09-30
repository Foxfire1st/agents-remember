"""The knowledge validator at the worktree layer's memory commit routes (MIK-R22 rule 8).

A route calls :func:`memory_commit_refusal` with the exact tree it is about to commit, its comparison
bases (K_B, or every parent of a merge) and its paired code commit, before it commits. The route
validates whenever the candidate or any base holds the layout marker ``knowledge/layout.json``;
otherwise the commit is unconverted memory and nothing here runs, so no route commits differently
before the cutover (MIK-R37). The validator itself ranks above this layer and is reached through
:class:`~agents_remember.worktrees.services.KnowledgeValidationPort`; a converted commit with no bound
validator, or with no paired code commit, is refused rather than committed unchecked.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.services import worktree_services


@dataclass(frozen=True)
class PairedCode:
    """The code commit a memory commit is paired with, and the repository that holds it."""

    repository: Path
    commit: str


class LayoutProbeError(RuntimeError):
    """Git could not answer whether a tree holds the layout marker."""


def has_layout_marker(repository: Path, treeish: str) -> bool:
    """Answer whether ``treeish`` holds the layout marker, without reading the tree.

    An absent path is ``False``; a tree Git cannot read raises :class:`LayoutProbeError`, so an
    unreadable side is never taken for unconverted memory.
    """

    try:
        result = run_git(
            repository,
            ["ls-tree", "--name-only", treeish, "--", LAYOUT_MARKER_PATH],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except subprocess.SubprocessError as error:  # a timed-out probe is named, never unconverted
        raise LayoutProbeError(
            f"cannot read {treeish!r} in {repository}: the Git probe failed or timed out "
            f"({type(error).__name__})"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise LayoutProbeError(f"cannot read {treeish!r} in {repository}: {detail}")
    return result.stdout.strip() == LAYOUT_MARKER_PATH


def memory_commit_refusal(
    *,
    memory_repository: Path,
    candidate_tree: str,
    bases: Sequence[str],
    paired_code: PairedCode | None,
    leaf_publication: bool = False,
) -> str | None:
    """Return why this memory commit is refused, or ``None`` when it may be committed.

    ``leaf_publication`` marks a commit that publishes a leaf (closeout, direct landing, a leaf's
    recorded landing): the leaf's own history file is then checked even once it is closed (MIK-R09).
    """

    try:
        converted = [
            has_layout_marker(memory_repository, tree) for tree in (candidate_tree, *bases)
        ]
    except LayoutProbeError as error:
        return f"the knowledge validator (MIK-R22) refuses this memory commit: {error}"
    if not any(converted):
        return None
    if paired_code is None or not paired_code.commit:
        return (
            "the knowledge validator (MIK-R22) refuses this converted memory commit: "
            "its paired code commit is unknown"
        )
    validator = worktree_services().knowledge_validation
    if validator is None:
        return (
            "the knowledge validator (MIK-R22) is not bound in this process; a converted memory "
            "commit is never committed unvalidated"
        )
    route = validator.leaf_refusal if leaf_publication else validator.refusal
    return route(
        memory_repository=memory_repository,
        candidate_tree=candidate_tree,
        bases=tuple(bases),
        code_repository=paired_code.repository,
        code_commit=paired_code.commit,
    )
