"""External-memory quality phases owned by the closeout pipeline."""

from __future__ import annotations

from typing import Any

from agents_remember.worktrees.services import worktree_services


def _format_finding(finding: dict[str, Any]) -> str:
    path = str(finding.get("path") or finding.get("sourceFile") or "")
    code = str(finding.get("code") or finding.get("check") or "memory_quality")
    message = str(finding.get("message") or "")
    return f"{code}{f' at {path}' if path else ''}: {message}"


def _failure_message(result: dict[str, Any]) -> str:
    finding_count = int(result.get("findingCount", 0))
    findings = result.get("findings", [])
    sample: list[str] = []
    if isinstance(findings, list):
        sample = [_format_finding(finding) for finding in findings[:5] if isinstance(finding, dict)]
    details = "; ".join(sample)
    if details:
        details = f" Findings: {details}"
    return (
        "external-memory closeout requires a clean memory_quality_check before memory commit; "
        f"findingCount={finding_count}.{details} Fix memory/onboarding issues, rerun "
        "memory_quality_check, then rerun closeout."
    )


def run_memory_quality_phase(
    context,
    checks: tuple[str, ...],
    *,
    unstamped_code_commit: str | None = None,
) -> dict[str, Any]:
    """Run one declared closeout memory-quality phase or refuse with bounded evidence."""
    quality = worktree_services().memory_quality
    result = quality.run_check(
        context.onboarding_root,
        checks=checks,
        drift_context=quality.drift_context(
            code_repository_root=context.code_repository_root,
            context=context,
            detail_limit=50,
            unstamped_code_commit=unstamped_code_commit,
        ),
    )
    if not result.get("ok", False):
        raise RuntimeError(_failure_message(result))
    return result


def combine_memory_quality(
    before_refresh: dict[str, Any], after_refresh: dict[str, Any]
) -> dict[str, Any]:
    """Combine the pre-refresh and post-refresh results into one closeout gate."""
    before_checks, after_checks = worktree_services().memory_quality.check_groups()
    report_only_sample = [
        *before_refresh.get("reportOnlySample", []),
        *after_refresh["reportOnlySample"],
    ][:50]
    return {
        "ok": True,
        "checks": {**before_refresh.get("checks", {}), **after_refresh["checks"]},
        "findingCount": before_refresh.get("findingCount", 0) + after_refresh["findingCount"],
        "findings": [*before_refresh.get("findings", []), *after_refresh["findings"]],
        "reportOnlyFindingCount": (
            before_refresh.get("reportOnlyFindingCount", 0)
            + after_refresh["reportOnlyFindingCount"]
        ),
        "reportOnlySample": report_only_sample,
        "reportOnlySampleCount": len(report_only_sample),
        "closeoutPhases": {
            "beforeMetadataRefresh": list(before_checks),
            "afterMetadataRefresh": list(after_checks),
        },
    }
