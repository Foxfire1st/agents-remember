"""The validator as a commit route calls it: Git trees in, a refusal text out (MIK-R22 rule 8).

:class:`GitKnowledgeValidation` implements the worktree layer's ``KnowledgeValidationPort``
(:mod:`agents_remember.worktrees.services`); the composition layer binds it. A route hands it the
exact candidate tree it is about to commit, the commits of its comparison bases (K_B, or every
parent of a merge) and its paired code commit, and gets back ``None`` or the refusal naming every
violation.
"""

from __future__ import annotations

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
            require_valid_commit(candidate, bases=base_trees, code=code)
        except KnowledgeValidationError as error:
            return str(error)
        except ValueError as error:  # an unreadable tree is refused, never committed unchecked
            return f"the knowledge validator (MIK-R22) cannot read this commit's trees: {error}"
        return None
