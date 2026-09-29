"""The memory-quality run on a converted memory tree (MIK-R24 rule 5).

A converted card has no metadata table, no Update History and no citation tables, so the checks that
read those have nothing to check: each reports ``not-applicable-converted`` and passes. Currentness
comes from anchors instead, so the integrity slot runs :func:`converted_knowledge_check`:

* the knowledge validator (MIK-R22) over the whole tree against the code working tree -- its refusing
  violations are findings;
* its report-only findings and every stale reference (:mod:`.reference_state`) are report-only: a
  stale reference is refreshed through the onboarding gate (MIK-R30), never failed here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    KnowledgeTree,
    KnowledgeTreeReadError,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.validator import validate_tree
from agents_remember.memory_quality.reference_state import check_references

CONVERTED_KNOWLEDGE_CHECK: Final = "knowledge.converted"
NOT_APPLICABLE: Final = "not-applicable-converted"
# The checks that read the legacy card format; the names are the checks' own CHECK_NAME values.
LEGACY_FORMAT_CHECKS: Final = frozenset(
    {
        "style.update_history.history_order",
        "style.citations.range_resolution",
        "style.citations.claim_reopen",
    }
)


def not_applicable_on_converted(check: str) -> dict[str, Any]:
    """The result of a legacy-format check on a converted tree: nothing to read, nothing wrong."""

    return {
        "ok": True,
        "check": check,
        "status": NOT_APPLICABLE,
        "findingCount": 0,
        "findings": [],
    }


def _committed_base(memory_root: Path) -> tuple[KnowledgeTree, ...]:
    """K_B for a working-tree run: the memory repository's converted ``HEAD``, when it has one.

    Anchors carried from it unchanged are only reported when their path is gone (MIK-R22 rule 6);
    without it every anchor is checked as if newly added.
    """

    try:
        base = knowledge_tree_from_git(memory_root, "HEAD", label="memory HEAD")
    except (KnowledgeTreeReadError, ValueError, OSError):
        return ()
    return (base,) if base.converted else ()


def converted_knowledge_check(memory_root: Path, code_root: Path) -> dict[str, Any]:
    """Validate the converted tree and report its stale references (report-only)."""

    tree = knowledge_tree_from_directory(memory_root)
    report = validate_tree(
        tree,
        bases=_committed_base(memory_root),
        code=CodeDirectory(label=code_root.as_posix(), root=code_root),
    )
    findings = [
        {"check": CONVERTED_KNOWLEDGE_CHECK, **violation.to_document()}
        for violation in report.refusals
    ]
    report_only = [
        {"check": CONVERTED_KNOWLEDGE_CHECK, **violation.to_document()}
        for violation in report.reports
    ]
    references = check_references(memory_root, code_root)
    report_only.extend(references["reportOnlyFindings"])
    return {
        "ok": not findings,
        "check": CONVERTED_KNOWLEDGE_CHECK,
        "status": "converted",
        "findingCount": len(findings),
        "findings": findings,
        "reportOnlyFindings": report_only,
        "referenceStates": references["states"],
    }
