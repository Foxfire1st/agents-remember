"""Safe continuation policy for causal-preflight evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest

from agents_remember_test_support.testing.causal_failures import load_causal_report


class CausalReportState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CausalReportObservation:
    state: CausalReportState
    detail: str


@dataclass(frozen=True)
class CausalPreflightDecision:
    passed: bool
    causal_failure: bool
    report_unavailable: bool


def inspect_causal_report(path: Path) -> CausalReportObservation:
    """Return whether an exact report proves pass, failure, or no causality."""

    try:
        payload = load_causal_report(path)
    except pytest.UsageError as error:
        return CausalReportObservation(
            CausalReportState.UNAVAILABLE,
            " ".join(str(error).split()),
        )
    state = CausalReportState(str(payload["status"]))
    return CausalReportObservation(state, f"validated {state.value} causal report")


def evaluate_preflight_result(
    return_code: int,
    report_path: Path,
    *,
    printer: Callable[[str], None],
) -> CausalPreflightDecision:
    """Reconcile process exit with evidence and publish the safe-mode decision."""

    observation = inspect_causal_report(report_path)
    consistent_pass = return_code == 0 and observation.state is CausalReportState.PASSED
    consistent_failure = return_code != 0 and observation.state is CausalReportState.FAILED
    if consistent_pass:
        printer("result: causal-preflight PASS")
        return CausalPreflightDecision(True, False, False)
    if not consistent_failure:
        printer(f"causal-preflight evidence unavailable: {observation.detail}")
        printer(
            "causal-preflight safe mode: run the full selected pytest population "
            "without suppression"
        )
    printer(f"result: causal-preflight FAIL (exit {return_code})")
    return CausalPreflightDecision(
        False,
        causal_failure=consistent_failure,
        report_unavailable=not consistent_failure,
    )
