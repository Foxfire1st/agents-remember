"""The mandatory knowledge validator (MIK-R22): integrity at the commit boundary.

Text files are the source of truth for knowledge (D18), so integrity is checked where memory is
committed instead of by database constraints. One module, one rule registry:

* :mod:`.trees` -- the inputs: a memory tree's knowledge files and the paired code tree's paths;
* :mod:`.parsed` -- every file read once through the MIK-R21 / MIK-R07 models;
* :mod:`.markers` -- the ``[n]`` markers of a Markdown file;
* :mod:`.registry` -- the rule registry (rule 9) and the context every rule reads;
* :mod:`.rules_structure` and :mod:`.rules_references` -- MIK-R22's own rules 1 to 7;
* :mod:`.validator` -- :func:`validate_tree`, rule 8's applicability and
  :func:`require_valid_commit`;
* :mod:`.commit_route` -- the Git adapter a commit route calls through the worktree port.

The validator judges no meaning (Doc13): whether a statement is true, or a realization really
enforces its invariant, is the curator's and the reviewer's. Whether an anchor's content still
matches the code is currentness (MIK-R03), not validity.
"""

from __future__ import annotations

from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    register_rule,
    registered_rules,
)
from agents_remember.memory_quality.knowledge_validator.report import (
    KnowledgeValidationError,
    ValidationReport,
    Violation,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    CodePathSet,
    CodeTree,
    KnowledgeTree,
    code_tree_from_git,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.validator import (
    require_valid_commit,
    validate_tree,
    validation_applies,
)

__all__ = [
    "CodeDirectory",
    "CodePathSet",
    "CodeTree",
    "Finding",
    "KnowledgeTree",
    "KnowledgeValidationError",
    "ValidationContext",
    "ValidationReport",
    "ValidationRule",
    "Violation",
    "code_tree_from_git",
    "knowledge_tree_from_directory",
    "knowledge_tree_from_git",
    "register_rule",
    "registered_rules",
    "require_valid_commit",
    "validate_tree",
    "validation_applies",
]
