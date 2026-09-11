"""Non-accepting Dagger route for scheduled, provider-bump, and migration evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text

from agents_remember_test_support.testing.dagger_admission import require_dagger_admission
from agents_remember_test_support.testing.evidence_lanes import EvidenceTrigger, expression_for
from agents_remember_test_support.testing.evidence_lifecycle import (
    EvidenceCategory,
    load_evidence_inventory,
)
from agents_remember_test_support.testing.evidence_provenance import capture_provenance
from agents_remember_test_support.testing.pytest_phase_reporter import (
    PYTEST_PHASE_REPORT_OPTION,
    PYTEST_PHASE_REPORT_SCHEMA,
)

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
    command: tuple[str, ...]
    provenance: dict[str, object]
    population: dict[str, object] | None
    artifacts: dict[str, object]


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
    provenance = capture_provenance(root)
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
                command=(),
                provenance=provenance,
                population=None,
                artifacts={},
            ),
        )
        return 0

    command = (
        sys.executable,
        "-m",
        "pytest",
        "-n=0",
        "-m",
        marker_expression,
        "-p",
        "agents_remember_test_support.testing.evidence_lanes",
        "-p",
        "agents_remember_test_support.testing.pytest_phase_reporter",
        f"--report-log={pytest_report_log.as_posix()}",
        PYTEST_PHASE_REPORT_OPTION,
        pytest_phase_report.as_posix(),
    )
    started = time.monotonic()
    completed = subprocess.run(list(command), cwd=root, check=False, stdin=subprocess.DEVNULL)
    elapsed = time.monotonic() - started
    try:
        phase_payload = _load_phase_report(pytest_phase_report, completed.returncode)
        population = _required_population(phase_payload)
        artifacts: dict[str, object] = {
            "pytestPhaseReport": _artifact_ref(pytest_phase_report),
            "pytestReportLog": _artifact_ref(pytest_report_log),
        }
    except RuntimeError as error:
        _write_result(
            json_output,
            _CadenceResult(
                trigger=trigger,
                status="failed",
                exit_code=completed.returncode,
                elapsed_seconds=elapsed,
                marker_expression=marker_expression,
                executed=True,
                reason=f"cadence evidence is incomplete: {error}",
                command=command,
                provenance=provenance,
                population=None,
                artifacts=_available_artifacts(
                    pytest_phase_report=pytest_phase_report,
                    pytest_report_log=pytest_report_log,
                ),
            ),
        )
        return completed.returncode or 1
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
            command=command,
            provenance=provenance,
            population=population,
            artifacts=artifacts,
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
        "candidateProvenance": result.provenance,
        "population": result.population,
        "topology": {
            "controllerProcesses": 1,
            "xdistWorkers": 0,
            "serial": True,
        },
        "phaseDefinitions": {
            "bootstrap": "pytest reporter import to session start",
            "collection": "session start to completed marker selection",
            "execution": "first selected node start to report publication",
            "reporting": "phase-report serialization",
        },
        "repetitions": {
            "requested": 1,
            "completed": int(result.executed),
        },
        "command": list(result.command),
        "artifacts": result.artifacts,
        "limitations": [
            "This is a single non-accepting cadence observation.",
            "It proves only the explicit marker population on this exact candidate and machine.",
            "The final full Dagger quality gate remains the acceptance authority.",
        ],
        "finishedAt": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_phase_report(path: Path, expected_exit: int) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cadence phase report is unavailable: {error}") from error
    if not isinstance(raw, dict) or raw.get("schemaVersion") != PYTEST_PHASE_REPORT_SCHEMA:
        raise RuntimeError("cadence phase report schema is invalid")
    if raw.get("pytestExitCode") != expected_exit:
        raise RuntimeError("cadence phase report exit code differs from pytest")
    return raw


def _required_population(payload: dict[str, object]) -> dict[str, object]:
    population = payload.get("population")
    if not isinstance(population, dict):
        raise RuntimeError("cadence phase report has no explicit population")
    for key in ("collected", "selected", "deselected", "reported"):
        value = population.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError(f"cadence population {key} is invalid")
    return population


def _artifact_ref(path: Path) -> dict[str, object]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"cadence artifact is unavailable: {path}: {error}") from error
    return {
        "path": path.name,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _available_artifacts(
    *,
    pytest_phase_report: Path,
    pytest_report_log: Path,
) -> dict[str, object]:
    """Content-address every readable failure artifact without inventing a substitute."""

    artifacts: dict[str, object] = {}
    for name, path in (
        ("pytestPhaseReport", pytest_phase_report),
        ("pytestReportLog", pytest_report_log),
    ):
        try:
            artifacts[name] = _artifact_ref(path)
        except RuntimeError:
            continue
    return artifacts


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
