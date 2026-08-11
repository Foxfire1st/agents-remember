"""Run the registered memory-layer quality checks.

Every ``STYLE_CHECKS`` entry receives ``StyleCheckInputs`` so tree-only, code-aware, and
history-aware checks share one gate path. A required input that is unavailable must appear
in that check's result rather than produce an empty success.

``reportOnlyFindings`` are surfaced and counted but do not affect ``ok``. Use them only
for ranked review output, never to soften an enforcing invariant. Detail samples may be
bounded; total counts remain exact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    DriftSummaryOutput,
    run_drift_summary,
)
from agents_remember.memory_quality.style.citations import claim_reopen, range_resolution
from agents_remember.memory_quality.style.document_shape import (
    diff_markers,
    entity_catalog_alignment,
    tables,
)
from agents_remember.memory_quality.style.update_history import history_order

DRIFT_CHECK_NAME = "integrity.onboarding_drift_check.summary"
FINDING_KEYS = ("findings", "reportOnlyFindings")
# Matches the drift check's own `detail_limit` default, for the same reason.
REPORT_ONLY_SAMPLE_LIMIT = 50


@dataclass(frozen=True)
class StyleCheckInputs:
    """What a style check may read: the memory tree, and the code tree when there is one.

    ``code_repository_root`` is ``None`` only for a caller that has no code checkout to
    resolve against -- the two callers in the product (``memory_quality_check`` and the
    closeout gate) both take it from the repository they are closing out. A check that needs
    it and does not get it says so in its own result rather than passing quietly.
    """

    onboarding_root: Path
    code_repository_root: Path | None = None
    unstamped_code_commit: str | None = None


CheckRunner = Callable[[StyleCheckInputs], dict[str, Any]]


def onboarding_only(runner: Callable[[Path], dict[str, Any]]) -> CheckRunner:
    """Adapt a check that reads only the onboarding tree to the one runner signature."""
    return lambda inputs: runner(inputs.onboarding_root)


STYLE_CHECKS: dict[str, CheckRunner] = {
    claim_reopen.CHECK_NAME: lambda inputs: claim_reopen.check_onboarding_root(
        inputs.onboarding_root,
        inputs.code_repository_root,
        unstamped_code_commit=inputs.unstamped_code_commit,
    ),
    diff_markers.CHECK_NAME: onboarding_only(diff_markers.check_onboarding_root),
    entity_catalog_alignment.CHECK_NAME: onboarding_only(
        entity_catalog_alignment.check_onboarding_root
    ),
    history_order.CHECK_NAME: onboarding_only(history_order.check_onboarding_root),
    range_resolution.CHECK_NAME: lambda inputs: range_resolution.check_onboarding_root(
        inputs.onboarding_root, inputs.code_repository_root
    ),
    tables.CHECK_NAME: onboarding_only(tables.check_onboarding_root),
}
INTEGRITY_CHECKS = (DRIFT_CHECK_NAME,)
AVAILABLE_CHECKS = (*INTEGRITY_CHECKS, *STYLE_CHECKS)
DEFAULT_STYLE_CHECKS = tuple(STYLE_CHECKS)
DEFAULT_CONTEXT_CHECKS = (*INTEGRITY_CHECKS, *DEFAULT_STYLE_CHECKS)
# Closeout first checks entity-catalog structure, then citations, before the code commit and the
# strict test wrapper. Those are working-tree semantics, so they need no commit to clear and a
# failure rejects in seconds instead of after the expensive suite. The curator runs the same
# `memory_quality_check` during the leaf; the gate is its fallback, so findings here are the
# exception, not the rule. The post-commit phase repeats citations without temporary provenance
# and runs drift, document shape, and history order over the single ordinary metadata refresh.
# Repeating citations proves every temporarily based, unstamped card received a real code-commit
# stamp before memory commits.
BEFORE_METADATA_REFRESH_CHECKS = (
    entity_catalog_alignment.CHECK_NAME,
    range_resolution.CHECK_NAME,
    claim_reopen.CHECK_NAME,
)
AFTER_METADATA_REFRESH_CHECKS = DEFAULT_CONTEXT_CHECKS


@dataclass(frozen=True)
class DriftCheckContext:
    code_repository_root: Path
    context: Any
    detail_limit: int = 50
    unstamped_code_commit: str | None = None
    report_path: Path | None = None
    include_rows: bool = False
    write_report: bool = True


def run_memory_quality_check(
    onboarding_root: Path,
    *,
    checks: Sequence[str] | None = None,
    drift_context: DriftCheckContext | None = None,
    include_report_only_findings: bool = False,
) -> dict[str, Any]:
    selected = normalize_checks(checks, include_integrity=drift_context is not None)
    check_results: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    report_only: list[dict[str, Any]] = []
    finding_count = 0
    for check in selected:
        result = run_check(check, onboarding_root, drift_context)
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


def run_check(
    check: str,
    onboarding_root: Path,
    drift_context: DriftCheckContext | None,
) -> dict[str, Any]:
    if check in STYLE_CHECKS:
        return STYLE_CHECKS[check](
            StyleCheckInputs(
                onboarding_root=onboarding_root,
                code_repository_root=(
                    None if drift_context is None else drift_context.code_repository_root
                ),
                unstamped_code_commit=(
                    None if drift_context is None else drift_context.unstamped_code_commit
                ),
            )
        )
    if check == DRIFT_CHECK_NAME:
        if drift_context is None:
            raise ValueError(f"{DRIFT_CHECK_NAME} requires drift context")
        return run_drift_quality_check(drift_context)
    raise ValueError(f"unknown memory quality check: {check}")


def run_drift_quality_check(drift_context: DriftCheckContext) -> dict[str, Any]:
    packet = run_drift_summary(
        code_repository_root=drift_context.code_repository_root,
        context=drift_context.context,
        detail_limit=drift_context.detail_limit,
        output=DriftSummaryOutput(
            report_path=drift_context.report_path,
            include_rows=drift_context.include_rows,
            write_report=drift_context.write_report,
        ),
    )
    if packet.get("status") != "checked":
        return {
            "ok": False,
            "check": DRIFT_CHECK_NAME,
            "status": packet.get("status", "error"),
            "findingCount": 1,
            "findings": [
                {
                    "check": DRIFT_CHECK_NAME,
                    "severity": "error",
                    "code": "onboarding_drift_check_failed",
                    "message": str(packet.get("error", "drift check failed")),
                }
            ],
        }
    findings = [drift_row_to_finding(row) for row in packet.get("actionableSample", [])]
    # `.get` throughout: `count`/`reportPath`/`actionableCount` only accompany a `checked`
    # status, which the guard above has established but the type cannot carry across it.
    result = {
        "ok": packet.get("actionableCount", 0) == 0,
        "check": DRIFT_CHECK_NAME,
        "status": packet["status"],
        "checkedCount": packet.get("count"),
        "reportPath": packet.get("reportPath"),
        "findingCount": packet.get("actionableCount"),
        "sampleCount": len(findings),
        "findings": findings,
    }
    if drift_context.include_rows:
        result["rows"] = packet.get("rows", [])
    return result


def drift_row_to_finding(row: dict[str, Any]) -> dict[str, Any]:
    classification = str(row.get("classification", "unknown"))
    code = "onboarding_drift_" + classification.replace(" ", "_").replace("-", "_")
    return {
        "check": DRIFT_CHECK_NAME,
        "path": row.get("onboarding_file", ""),
        "sourceFile": row.get("source_file", ""),
        "severity": "warning",
        "code": code,
        "message": row.get("note", ""),
        "classification": classification,
        "trust": row.get("trust", ""),
        "affectedSections": row.get("affected_sections", ""),
    }


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
