"""Non-accepting Dagger route for scheduled, provider-bump, and migration evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.testing.dagger_admission import require_dagger_admission
from agents_remember.testing.evidence_lanes import EvidenceTrigger, expression_for
from agents_remember.testing.evidence_lifecycle import (
    EvidenceCategory,
    load_evidence_inventory,
)
from agents_remember.testing.pytest_phase_reporter import PYTEST_PHASE_REPORT_OPTION

CADENCE_EVIDENCE_SCHEMA = "python-cadence-evidence/v1"
EXECUTABLE_TRIGGERS = frozenset(
    {
        EvidenceTrigger.PROVIDER_BUMP,
        EvidenceTrigger.SCHEDULED,
        EvidenceTrigger.MIGRATION_WINDOW,
    }
)


@dataclass(frozen=True)
class _CadenceResult:
    trigger: EvidenceTrigger
    status: str
    exit_code: int
    elapsed_seconds: float
    marker_expression: str
    executed: bool
    reason: str


def run_cadence_evidence(
    project_root: Path,
    *,
    trigger: EvidenceTrigger,
    json_output: Path,
    pytest_report_log: Path,
    pytest_phase_report: Path,
) -> int:
    """Run one explicit cadence population without producing acceptance evidence."""

    require_dagger_admission(subject=f"{trigger.value} test evidence")
    if trigger not in EXECUTABLE_TRIGGERS:
        allowed = ", ".join(sorted(item.value for item in EXECUTABLE_TRIGGERS))
        raise ValueError(f"cadence runner accepts only {allowed}; received {trigger.value}")
    marker_expression = expression_for(trigger)
    if marker_expression is None:
        raise RuntimeError(f"{trigger.value} unexpectedly has no marker expression")
    root = project_root.resolve()
    inventory = load_evidence_inventory(root)
    for path in (json_output, pytest_report_log, pytest_phase_report):
        path.parent.mkdir(parents=True, exist_ok=True)
    if trigger is EvidenceTrigger.MIGRATION_WINDOW and not any(
        item.category is EvidenceCategory.MIGRATION for item in inventory.artifacts
    ):
        _write_result(
            json_output,
            _CadenceResult(
                trigger=trigger,
                status="not-applicable",
                exit_code=0,
                elapsed_seconds=0.0,
                marker_expression=marker_expression,
                executed=False,
                reason="the lifecycle catalog contains no current migration evidence",
            ),
        )
        return 0

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-n=0",
        "-m",
        marker_expression,
        "-p",
        "agents_remember.testing.evidence_lanes",
        "-p",
        "agents_remember.testing.pytest_phase_reporter",
        f"--report-log={pytest_report_log.as_posix()}",
        PYTEST_PHASE_REPORT_OPTION,
        pytest_phase_report.as_posix(),
    ]
    started = time.monotonic()
    completed = subprocess.run(command, cwd=root, check=False, stdin=subprocess.DEVNULL)
    elapsed = time.monotonic() - started
    _write_result(
        json_output,
        _CadenceResult(
            trigger=trigger,
            status="passed" if completed.returncode == 0 else "failed",
            exit_code=completed.returncode,
            elapsed_seconds=elapsed,
            marker_expression=marker_expression,
            executed=True,
            reason="non-accepting cadence evidence; the full release gate remains authoritative",
        ),
    )
    return completed.returncode


def _write_result(
    path: Path,
    result: _CadenceResult,
) -> None:
    payload = {
        "schemaVersion": CADENCE_EVIDENCE_SCHEMA,
        "status": result.status,
        "trigger": result.trigger.value,
        "markerExpression": result.marker_expression,
        "executed": result.executed,
        "pytestExitCode": result.exit_code,
        "elapsedSeconds": round(result.elapsed_seconds, 6),
        "acceptanceEligible": False,
        "certifying": False,
        "message": result.reason,
        "finishedAt": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--trigger",
        choices=tuple(sorted(item.value for item in EXECUTABLE_TRIGGERS)),
        required=True,
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--pytest-report-log", type=Path, required=True)
    parser.add_argument("--pytest-phase-report", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_cadence_evidence(
        args.project_root,
        trigger=EvidenceTrigger(args.trigger),
        json_output=args.json_output,
        pytest_report_log=args.pytest_report_log,
        pytest_phase_report=args.pytest_phase_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
