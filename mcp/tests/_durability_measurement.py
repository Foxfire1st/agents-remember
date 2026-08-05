"""Non-vacuity contract for one durability stress result."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Real runs across eight stores produced 22-39 successful compactions idle and 34-49 under heavy
# CPU load. Ten is above the one-success stopped-compactor failure and below half the lowest
# observed legitimate run.
MIN_SUCCESSFUL_RECLAIMS = 10


class VacuousRunError(RuntimeError):
    """The instrument did not complete enough requested work to report a durability number."""


def require_stress_measurement(result: dict[str, Any], work: Path) -> dict[str, Any]:
    """Return a complete stress result or name every fact that makes it vacuous."""
    requested = int(result["requested"])
    attempted = int(result["attempted"])
    completed = int(result["completed"])
    reclaim_attempts = int(result["reclaim_attempts"])
    successful_reclaims = int(result["successful_reclaims"])
    reclaim_error_count = int(result["reclaim_error_count"])
    reclaim_errors = sorted(str(error) for error in result["reclaim_errors"])
    stragglers = list(result["stragglers"])
    findings: list[str] = []
    if attempted == 0:
        findings.append(f"workers attempted 0 of {requested} required writes")
    elif attempted != requested:
        findings.append(f"workers attempted {attempted} of {requested} required writes")
    if completed != attempted:
        findings.append(f"workers completed {completed} of {attempted} attempted writes")
    if stragglers:
        findings.append(f"processes did not stop: {', '.join(sorted(stragglers))}")
    if reclaim_error_count:
        findings.append(
            f"reclaimer failures: {reclaim_error_count} of {reclaim_attempts} attempted "
            f"compactions raised (error types: {', '.join(reclaim_errors) or 'unrecorded'})"
        )
    if successful_reclaims < MIN_SUCCESSFUL_RECLAIMS:
        findings.append(
            f"the reclaimer completed {successful_reclaims} successful compaction(s), below the "
            f"required {MIN_SUCCESSFUL_RECLAIMS}"
        )
    if not findings:
        return result
    raise VacuousRunError(
        f"{result['case']} / stress refused a durability result: {'; '.join(findings)}. "
        f"The instrument will not report '{result['lost']} lost' over incomplete work. "
        f"Remediation: inspect {work / 'reclaimer.err'} and the run bookkeeping under {work}; "
        f"repair every reclaim failure or stale stop/budget condition, then rerun with zero "
        f"reclaim errors and at least {MIN_SUCCESSFUL_RECLAIMS} successful compactions."
    )
