"""Validate converted knowledge and report reference currentness and landing changes.

Refusing validator violations are findings; stale references and landing changes
are report-only. The comparison base is supplied by the converted-base cache."""

from __future__ import annotations

from collections.abc import Callable
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
from agents_remember.memory_quality.landing_report import (
    CheckLandingScope,
    LandingBasePort,
    landing_report_for_check,
)
from agents_remember.memory_quality.reference_state import check_references

CONVERTED_KNOWLEDGE_CHECK: Final = "knowledge.converted"
CONVERTED_BASE_RULE: Final = "R24.7-converted-base"
#: The validator rule whose refusals classify anchors against the comparison
#: base (L44-C1-09): withheld, never refused, when the base cannot be built.
_BASE_DEPENDENT_RULE: Final = "R22.6-anchor-path"
KnowledgeBasePort = Callable[[Path, KnowledgeTree], tuple[KnowledgeTree | None, str | None]]
"""``(memory root, candidate) -> (K_B as the candidate is compared with it, or why it cannot be
built)``. This layer cannot reach the converted-base cache; the application binds it."""
# The checks that read the legacy card format; the names are the checks' own CHECK_NAME values.


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


def _comparison_bases(
    memory_root: Path, tree: KnowledgeTree, base: KnowledgeBasePort | None
) -> tuple[tuple[KnowledgeTree, ...], str | None]:
    """The validator's bases and, when the converted base cannot be built, why."""

    if base is None:
        return _committed_base(memory_root), None
    found, problem = base(memory_root, tree)
    return ((found,) if found is not None and found.converted else ()), problem


def _unbuildable_base(problem: str) -> dict[str, Any]:
    """A base that cannot be built is a finding by name, never a comparison against nothing."""

    return {
        "check": CONVERTED_KNOWLEDGE_CHECK,
        "path": "knowledge/layout.json",
        "field": "",
        "rule": CONVERTED_BASE_RULE,
        "message": problem,
        "reportOnly": False,
    }


def converted_knowledge_check(
    memory_root: Path,
    code_root: Path,
    *,
    base: KnowledgeBasePort | None = None,
    landing_base: LandingBasePort | None = None,
    include_landing: bool = True,
) -> dict[str, Any]:
    """Validate the converted tree and report its stale references (report-only).

    When the comparison base cannot be built, carried-vs-new classification is
    unknown, so the base-dependent anchor-path refusals are withheld instead
    of being decided against an invented empty base (L44-C1-09): the named
    unbuildable-base refusal is recorded, every independently measurable
    reference state keeps being reported, and no new/carried refusal fires.

    The landing report compares against the bound leaf base when one is
    supplied (MIK-R120 rule 1), else against the same comparison base.
    ``include_landing=False`` skips the landing report for callers that
    surface only validator and stale-reference state (the drift summary).
    """

    tree = knowledge_tree_from_directory(memory_root)
    bases, problem = _comparison_bases(memory_root, tree, base)
    report = validate_tree(
        tree,
        bases=bases,
        code=CodeDirectory(label=code_root.as_posix(), root=code_root),
    )
    findings = [_unbuildable_base(problem)] if problem is not None else []
    refused = [violation.to_document() for violation in report.refusals]
    if problem is not None:
        refused = [document for document in refused if document.get("rule") != _BASE_DEPENDENT_RULE]
    findings += [{"check": CONVERTED_KNOWLEDGE_CHECK, **document} for document in refused]
    report_only = [
        {"check": CONVERTED_KNOWLEDGE_CHECK, **violation.to_document()}
        for violation in report.reports
    ]
    references = check_references(memory_root, code_root)
    report_only.extend(references["reportOnlyFindings"])
    landing_counts: dict[str, Any] = {
        "cutCardCount": 0,
        "cutCards": [],
        "addedFileCount": 0,
        "addedFileBytes": 0,
        "addedFiles": [],
    }
    if include_landing:
        resolved_leaf_base = landing_base(tree) if landing_base is not None else None
        landing = landing_report_for_check(
            CheckLandingScope(
                memory_root=memory_root,
                candidate=tree,
                bases=bases,
                base_problem=problem,
                code_root=code_root,
                leaf_base=resolved_leaf_base,
            )
        )
        report_only.extend(landing["reportOnlyFindings"])
        landing_counts = {
            "cutCardCount": landing["cutCardCount"],
            "cutCards": landing["cutCards"],
            "addedFileCount": landing["addedFileCount"],
            "addedFileBytes": landing["addedFileBytes"],
            "addedFiles": landing["addedFiles"],
        }
    return {
        "ok": not findings,
        "check": CONVERTED_KNOWLEDGE_CHECK,
        "status": "converted",
        "findingCount": len(findings),
        "findings": findings,
        "reportOnlyFindings": report_only,
        "referenceStates": references["states"],
        "sidecarCount": references["sidecarCount"],
        "unreadableSidecars": references["unreadableSidecars"],
        **landing_counts,
    }
