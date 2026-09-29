"""Run every registered rule over one candidate tree (MIK-R22).

* :func:`validate_tree` is the validator itself: every rule of the registry, over K_C, against its
  comparison bases and its paired code tree. The writer (MIK-R12) calls it over the tree it produces,
  and the curator's command calls it over a working tree.
* :func:`validation_applies` is rule 8's applicability: a commit route validates whenever K_B or K_C
  -- every parent, at a merge -- has the layout marker. Before the cutover no production tree has
  one, so no production route changes behaviour.
* :func:`require_valid_commit` is what a commit route calls before it commits memory: nothing when
  the commit is out of scope, the report when it passes, and :class:`KnowledgeValidationError` --
  listing every refusing violation -- when it does not. It has no parameter that skips a rule.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents_remember.memory_quality.knowledge_validator import (
    rules_census as _rules_census,  # noqa: F401  # registers MIK-R20's census rules (rule 9)
)
from agents_remember.memory_quality.knowledge_validator import (
    rules_references as _rules_references,  # noqa: F401  # registers MIK-R22 rules 3, 5, 6
)
from agents_remember.memory_quality.knowledge_validator import (
    rules_routes as _rules_routes,  # noqa: F401  # registers MIK-R04's family route rules
)
from agents_remember.memory_quality.knowledge_validator import (
    rules_structure as _rules_structure,  # noqa: F401  # registers MIK-R22 rules 1, 2, 4, 6, 7
)
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.registry import (
    ValidationContext,
    registered_rules,
)
from agents_remember.memory_quality.knowledge_validator.report import (
    KnowledgeValidationError,
    ValidationReport,
    Violation,
)
from agents_remember.memory_quality.knowledge_validator.trees import CodeTree, KnowledgeTree


def validate_tree(
    candidate: KnowledgeTree,
    *,
    bases: Sequence[KnowledgeTree] = (),
    code: CodeTree | None = None,
    conversion: bool = False,
) -> ValidationReport:
    """Validate ``candidate`` with every registered rule.

    ``bases`` are the comparison bases of rule 6: K_B at a commit route, every parent at a merge,
    none for a writer or a curator run with no base (then every anchor is checked for path
    existence). ``code`` is the paired code tree; it is required unless ``conversion`` is set, which
    is a standalone conversion's run: it carries anchors and authors none, so none is checked for
    path existence.
    """

    if code is None and not conversion:
        raise ValueError("anchor path existence needs the paired code tree")
    context = ValidationContext(
        candidate=candidate,
        parsed=parse_tree(candidate),
        bases=tuple(bases),
        code=code,
        conversion=conversion,
    )
    violations = [
        Violation(
            path=finding.path,
            field=finding.field,
            rule=rule.id,
            message=finding.message,
            report_only=rule.report_only,
        )
        for rule in registered_rules()
        for finding in rule.check(context)
    ]
    return ValidationReport(candidate=candidate.label, violations=tuple(sorted(violations)))


def validation_applies(candidate: KnowledgeTree, bases: Sequence[KnowledgeTree]) -> bool:
    """Rule 8: a commit is validated when its candidate or any base has the layout marker."""

    return candidate.converted or any(base.converted for base in bases)


def require_valid_commit(
    candidate: KnowledgeTree, *, bases: Sequence[KnowledgeTree], code: CodeTree
) -> ValidationReport | None:
    """Validate a memory commit's candidate at a commit route, refusing on any violation.

    Returns ``None`` when neither the candidate nor any base is converted (rule 8), and the report
    when the candidate passes (report-only findings included). Raises
    :class:`KnowledgeValidationError` naming every refusing violation otherwise.
    """

    if not validation_applies(candidate, bases):
        return None
    report = validate_tree(candidate, bases=bases, code=code)
    if not report.ok:
        raise KnowledgeValidationError(report)
    return report
