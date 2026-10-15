"""Immutable explicit leaf comparisons, shared by capture and completion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_worklist.planned_effects import Declaration


@dataclass(frozen=True)
class CandidateTrees:
    """C and K_C given as Git trees, the exact candidate a gate evaluates (MIK-R09).

    ``code`` is a tree of the leaf's code repository and ``memory`` a tree of its memory repository,
    both already written to their object stores (the closeout's own candidate captures). Given them,
    the worklist reads exactly those trees instead of capturing the two worktrees again.
    """

    code: str
    memory: str


@dataclass(frozen=True)
class CapturedBase:
    """The review's already paired B/K_B, including the immutable tree K_B is read as."""

    code: str
    memory: str
    read_tree: str


@dataclass(frozen=True)
class ExplicitSides:
    """The four sides named directly (the command line, and evidence runs on scratch copies).

    ``memory_candidate`` is a memory working tree (a directory) or a Git revision of
    ``memory_repository``; ``code_candidate`` is a Git revision of ``code_repository`` or ``None`` to
    capture ``code_worktree``.
    """

    code_repository: Path
    base: str
    memory_repository: Path
    memory_base: str
    memory_candidate: str | Path
    code_candidate: str | None = None
    code_worktree: Path | None = None
    maintenance_scope: bool = False
    owner: str | None = None
    cache_directory: Path | None = None
    """Where converted bases are cached (:mod:`.base_cache`); ``None`` converts on every run."""
    expected_effects: tuple[Declaration, ...] | None = None
    """The leaf's declared ``expectedKnowledgeEffects`` (MIK-R11); ``None`` declares none."""
    coordination_root: Path | None = None
    """Where requirement endpoints' owning tasks live (MIK-R14); ``None`` resolves none."""
    held_base_tree: str | None = None
    """The captured comparison's converted K_B tree; read it without converting again."""
