"""The validator as a commit route calls it: Git trees in, a refusal text out (MIK-R22 rule 8).

:class:`GitKnowledgeValidation` implements the worktree layer's ``KnowledgeValidationPort``
(:mod:`agents_remember.worktrees.services`); the composition layer binds it. A route hands it the
exact candidate tree it is about to commit, the commits of its comparison bases (K_B, or every
parent of a merge) and its paired code commit, and gets back ``None`` or the refusal naming every
violation.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.memory_quality.knowledge_validator.report import KnowledgeValidationError
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTree,
    code_tree_from_git,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.validator import (
    require_valid_commit,
    validation_applies,
)

BaseConverter = Callable[..., KnowledgeTree]
"""``(memory_repository, base, *, after, code_repository, code_commit) -> KnowledgeTree``."""


@dataclass(frozen=True)
class _Commit:
    memory_repository: Path
    candidate_tree: str
    bases: tuple[str, ...]
    code_repository: Path
    code_commit: str


@dataclass(frozen=True)
class GitKnowledgeValidation:
    """Validate a memory commit's candidate Git tree against its bases and paired code commit.

    ``base_converter`` is MIK-R24 rule 7, bound by the composition layer: a converted candidate's
    unconverted base (the far side of a crossing sync) is replaced by its conversion before the rules
    run. Without one, such a base is refused by rule 6 (``R22.6-base-converted``).
    """

    base_converter: BaseConverter | None = None

    def refusal(
        self,
        *,
        memory_repository: Path,
        candidate_tree: str,
        bases: Sequence[str],
        code_repository: Path,
        code_commit: str,
    ) -> str | None:
        return self._refusal(
            _Commit(memory_repository, candidate_tree, tuple(bases), code_repository, code_commit)
        )

    def leaf_refusal(
        self,
        *,
        memory_repository: Path,
        candidate_tree: str,
        bases: Sequence[str],
        code_repository: Path,
        code_commit: str,
    ) -> str | None:
        """A commit that publishes a leaf: its own history file is read whatever its flag."""

        return self._refusal(
            _Commit(memory_repository, candidate_tree, tuple(bases), code_repository, code_commit),
            leaf_publication=True,
        )

    def _refusal(self, commit: _Commit, *, leaf_publication: bool = False) -> str | None:
        memory_repository, candidate_tree, bases = (
            commit.memory_repository,
            commit.candidate_tree,
            commit.bases,
        )
        code_repository, code_commit = commit.code_repository, commit.code_commit
        try:
            candidate = knowledge_tree_from_git(
                memory_repository, candidate_tree, label=f"memory candidate {candidate_tree}"
            )
            base_trees = [
                knowledge_tree_from_git(memory_repository, base, label=f"memory base {base}")
                for base in bases
            ]
            if not validation_applies(candidate, base_trees):
                return None
            if candidate.converted and self.base_converter is not None:
                base_trees = [
                    tree
                    if tree.converted
                    else self.base_converter(
                        memory_repository,
                        base,
                        after=candidate,
                        code_repository=code_repository,
                        code_commit=code_commit,
                    )
                    for base, tree in zip(bases, base_trees, strict=True)
                ]
            code = code_tree_from_git(code_repository, code_commit, label=f"code {code_commit}")
            require_valid_commit(
                candidate, bases=base_trees, code=code, leaf_publication=leaf_publication
            )
        except KnowledgeValidationError as error:
            return str(error)
        except ValueError as error:  # an unreadable tree is refused, never committed unchecked
            return f"the knowledge validator (MIK-R22) cannot read this commit's trees: {error}"
        except subprocess.SubprocessError as error:  # a failed or timed-out Git read, named
            return (
                "the knowledge validator (MIK-R22) cannot read this commit's trees: a Git call "
                f"failed or timed out ({type(error).__name__}: {error})"
            )
        return None
