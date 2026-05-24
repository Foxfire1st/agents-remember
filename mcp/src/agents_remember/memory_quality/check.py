"""Run memory-layer quality checks."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.memory_quality.style.update_history import history_order

CheckRunner = Callable[[Path], dict[str, Any]]
DRIFT_CHECK_NAME = "integrity.onboarding_drift_check.summary"

STYLE_CHECKS: dict[str, CheckRunner] = {
    history_order.CHECK_NAME: history_order.check_onboarding_root,
}
INTEGRITY_CHECKS = (DRIFT_CHECK_NAME,)
AVAILABLE_CHECKS = (*INTEGRITY_CHECKS, *STYLE_CHECKS)
DEFAULT_STYLE_CHECKS = tuple(STYLE_CHECKS)
DEFAULT_CONTEXT_CHECKS = (*INTEGRITY_CHECKS, *DEFAULT_STYLE_CHECKS)


@dataclass(frozen=True)
class DriftCheckContext:
    code_repository_root: Path
    context: Any
    detail_limit: int = 50


def run_memory_quality_check(
    onboarding_root: Path,
    *,
    checks: Sequence[str] | None = None,
    drift_context: DriftCheckContext | None = None,
) -> dict[str, Any]:
    selected = normalize_checks(checks, include_integrity=drift_context is not None)
    check_results: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    finding_count = 0
    for check in selected:
        result = run_check(check, onboarding_root, drift_context)
        check_results[check] = {key: value for key, value in result.items() if key != "findings"}
        finding_count += int(result.get("findingCount", 0))
        findings.extend(result["findings"])
    return {
        "ok": all(result.get("ok", False) for result in check_results.values()),
        "checks": check_results,
        "findingCount": finding_count,
        "findings": findings,
    }


def run_check(
    check: str,
    onboarding_root: Path,
    drift_context: DriftCheckContext | None,
) -> dict[str, Any]:
    if check in STYLE_CHECKS:
        return STYLE_CHECKS[check](onboarding_root)
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
    return {
        "ok": packet.get("actionableCount", 0) == 0,
        "check": DRIFT_CHECK_NAME,
        "status": packet["status"],
        "checkedCount": packet["count"],
        "reportPath": packet["reportPath"],
        "findingCount": packet["actionableCount"],
        "sampleCount": len(findings),
        "findings": findings,
    }


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
