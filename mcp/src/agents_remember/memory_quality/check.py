"""Run the surviving document-shape checks and converted knowledge validator.

Report-only findings are surfaced and counted but do not affect ok."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.converted_check import (
    CONVERTED_KNOWLEDGE_CHECK,
    KnowledgeBasePort,
    converted_knowledge_check,
)
from agents_remember.memory_quality.landing_report import LandingBasePort
from agents_remember.memory_quality.reference_state import is_converted_memory
from agents_remember.memory_quality.source_files import QualitySourceFiles
from agents_remember.memory_quality.style.document_shape import (
    diff_markers,
    entity_catalog_alignment,
    tables,
)

DRIFT_CHECK_NAME = "integrity.onboarding_drift_check.summary"
FINDING_KEYS = ("findings", "reportOnlyFindings")
# Matches the drift check's own `detail_limit` default, for the same reason.
REPORT_ONLY_SAMPLE_LIMIT = 50


@dataclass(frozen=True)
class StyleCheckInputs:
    """The onboarding tree the surviving document-shape checks read."""

    onboarding_root: Path


CheckRunner = Callable[[StyleCheckInputs], dict[str, Any]]


def onboarding_only(runner: Callable[[Path], dict[str, Any]]) -> CheckRunner:
    """Adapt a check that reads only the onboarding tree to the one runner signature."""
    return lambda inputs: runner(inputs.onboarding_root)


STYLE_CHECKS: dict[str, CheckRunner] = {
    diff_markers.CHECK_NAME: onboarding_only(diff_markers.check_onboarding_root),
    entity_catalog_alignment.CHECK_NAME: onboarding_only(
        entity_catalog_alignment.check_onboarding_root
    ),
    tables.CHECK_NAME: onboarding_only(tables.check_onboarding_root),
}
INTEGRITY_CHECKS = (DRIFT_CHECK_NAME,)
AVAILABLE_CHECKS = (*INTEGRITY_CHECKS, *STYLE_CHECKS)
DEFAULT_STYLE_CHECKS = tuple(STYLE_CHECKS)
DEFAULT_CONTEXT_CHECKS = (*INTEGRITY_CHECKS, *DEFAULT_STYLE_CHECKS)
# The pre-commit phase checks the entity catalogue; the post-commit phase checks
# converted knowledge and the surviving document-shape rules.
BEFORE_METADATA_REFRESH_CHECKS = (entity_catalog_alignment.CHECK_NAME,)
AFTER_METADATA_REFRESH_CHECKS = DEFAULT_CONTEXT_CHECKS


@dataclass(frozen=True)
class DriftCheckContext:
    code_repository_root: Path
    context: Any
    detail_limit: int = 50
    unstamped_code_commit: str | None = None
    retained_code_history_commits: tuple[str, ...] = ()
    report_path: Path | None = None
    include_rows: bool = False
    write_report: bool = True
    knowledge_base: KnowledgeBasePort | None = None
    """The converted tree's comparison base (MIK-R24 rule 7); without it, a converted ``HEAD``."""
    landing_base: LandingBasePort | None = None
    """The leaf's admitted memory base for the landing report (MIK-R120 rule 1)."""


def run_memory_quality_check(
    onboarding_root: Path,
    *,
    checks: Sequence[str] | None = None,
    drift_context: DriftCheckContext | None = None,
    include_report_only_findings: bool = False,
) -> dict[str, Any]:
    selected = normalize_checks(checks, include_integrity=drift_context is not None)
    converted = is_converted_memory(onboarding_root.parent)
    check_results: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    report_only: list[dict[str, Any]] = []
    finding_count = 0
    for selected_check in selected:
        check, result = (
            _converted_check(selected_check, onboarding_root, drift_context)
            if converted
            else (selected_check, run_check(selected_check, onboarding_root))
        )
        check_results[check] = {
            key: value for key, value in result.items() if key not in FINDING_KEYS
        }
        finding_count += int(result.get("findingCount", 0))
        findings.extend(result["findings"])
        report_only.extend(result.get("reportOnlyFindings", []))
    payload = {
        "ok": all(result.get("ok", False) for result in check_results.values()),
        "checks": check_results,
        "findingCount": finding_count,
        "findings": findings,
        "reportOnlyFindingCount": len(report_only),
        "reportOnlySample": report_only[:REPORT_ONLY_SAMPLE_LIMIT],
        "reportOnlySampleCount": min(len(report_only), REPORT_ONLY_SAMPLE_LIMIT),
    }
    if include_report_only_findings:
        payload["reportOnlyFindings"] = report_only
    return payload


def _converted_check(
    check: str, onboarding_root: Path, drift_context: DriftCheckContext | None
) -> tuple[str, dict[str, Any]]:
    """The drift slot runs the converted validator and stale-reference report."""

    if check == DRIFT_CHECK_NAME:
        if drift_context is None:
            raise ValueError(f"{DRIFT_CHECK_NAME} requires drift context")
        return CONVERTED_KNOWLEDGE_CHECK, converted_knowledge_check(
            onboarding_root.parent,
            QualitySourceFiles(drift_context.code_repository_root),
            base=drift_context.knowledge_base,
            landing_base=drift_context.landing_base,
        )
    return check, run_check(check, onboarding_root)


def run_check(
    check: str,
    onboarding_root: Path,
) -> dict[str, Any]:
    """Run one surviving document-shape check."""
    if check in STYLE_CHECKS:
        return STYLE_CHECKS[check](StyleCheckInputs(onboarding_root=onboarding_root))
    raise ValueError(f"unknown memory quality check: {check}")


def normalize_checks(
    checks: Sequence[str] | None,
    *,
    include_integrity: bool = False,
) -> tuple[str, ...]:
    if checks is None:
        if include_integrity:
            return DEFAULT_CONTEXT_CHECKS
        return DEFAULT_STYLE_CHECKS
    selected = tuple(checks)
    if not selected:
        if include_integrity:
            return DEFAULT_CONTEXT_CHECKS
        return DEFAULT_STYLE_CHECKS
    unknown = sorted(set(selected) - set(AVAILABLE_CHECKS))
    if unknown:
        allowed = ", ".join(sorted(AVAILABLE_CHECKS))
        raise ValueError(
            f"unknown memory quality check(s): {', '.join(unknown)}; allowed: {allowed}"
        )
    return selected
