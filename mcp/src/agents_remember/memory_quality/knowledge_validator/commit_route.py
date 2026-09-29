"""The validator as a commit route calls it: Git trees in, a refusal text out (MIK-R22 rule 8).

:class:`GitKnowledgeValidation` implements the worktree layer's ``KnowledgeValidationPort``
(:mod:`agents_remember.worktrees.services`); the composition layer binds it. A route hands it the
exact candidate tree it is about to commit, the commits of its comparison bases (K_B, or every
parent of a merge) and its paired code commit, and gets back ``None`` or the refusal naming every
violation.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agents_remember.memory_quality.knowledge_validator.report import KnowledgeValidationError
from agents_remember.memory_quality.knowledge_validator.trees import (
    code_tree_from_git,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.validator import (
    require_valid_commit,
    validation_applies,
)


class GitKnowledgeValidation:
    """Validate a memory commit's candidate Git tree against its bases and paired code commit."""

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
            code = code_tree_from_git(code_repository, code_commit, label=f"code {code_commit}")
            require_valid_commit(candidate, bases=base_trees, code=code)
        except KnowledgeValidationError as error:
            return str(error)
        except ValueError as error:  # an unreadable tree is refused, never committed unchecked
            return f"the knowledge validator (MIK-R22) cannot read this commit's trees: {error}"
        return None
