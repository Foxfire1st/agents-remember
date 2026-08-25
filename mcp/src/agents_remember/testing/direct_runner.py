"""Canonical explicit, bounded, serial Python diagnostic command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.models.test_evidence import DiagnosticTestEvidence, evidence_payload
from agents_remember.testing.diagnostic_bootstrap import (
    DiagnosticBootstrapError,
    diagnostic_pytest_environment,
    prepare_diagnostic_pytest_bootstrap,
)
from agents_remember.testing.eligibility import (
    classify_direct_selection,
    direct_selection_is_current,
)
from agents_remember.testing.pytest_phase_reporter import (
    PYTEST_PHASE_REPORT_OPTION,
    PYTEST_PHASE_REPORT_SCHEMA,
)
from agents_remember.testing.selection_contract import (
    EligibleDirectSelection,
    RefusedDirectSelection,
)

DIRECT_DIAGNOSTIC_SCHEMA = "python-direct-diagnostic/v1"
CANONICAL_COMMAND = "./scripts/test-python"
REFUSAL_EXIT_CODE = 2
INTERNAL_ERROR_EXIT_CODE = 3
PYTEST_OUTPUT_MAX_BYTES = 32 * 1024
_PARALLEL_PREFIXES = ("-n", "--numprocesses", "--dist", "--maxprocesses")


@dataclass(frozen=True)
class DirectNodeOutcome:
    node_id: str
    outcome: str


@dataclass(frozen=True)
class PytestPhaseEvidence:
    timestamps: dict[str, str | None]
    phase_seconds: dict[str, float | None]


@dataclass(frozen=True)
class DirectDiagnosticTiming:
    route_started_at: str
    admission_finished_at: str
    bootstrap_prepared_at: str
    route_finished_at: str
    route_seconds: float
    admission_seconds: float
    bootstrap_preparation_seconds: float
    time_to_first_node_start_seconds: float | None
    pytest: PytestPhaseEvidence

    def payload(self) -> dict[str, object]:
        return {
            "routeStartedAt": self.route_started_at,
            "admissionFinishedAt": self.admission_finished_at,
            "bootstrapPreparedAt": self.bootstrap_prepared_at,
            "routeFinishedAt": self.route_finished_at,
            "routeSeconds": round(self.route_seconds, 6),
            "admissionSeconds": round(self.admission_seconds, 6),
            "bootstrapPreparationSeconds": round(
                self.bootstrap_preparation_seconds,
                6,
            ),
            "timeToFirstNodeStartSeconds": (
                None
                if self.time_to_first_node_start_seconds is None
                else round(self.time_to_first_node_start_seconds, 6)
            ),
            "pytest": {
                "timestamps": self.pytest.timestamps,
                "phaseSeconds": self.pytest.phase_seconds,
            },
        }


@dataclass(frozen=True)
class DirectDiagnosticCompleted:
    evidence: DiagnosticTestEvidence
    outcomes: tuple[DirectNodeOutcome, ...]
    timing: DirectDiagnosticTiming
    pytest_stdout: str
    pytest_stderr: str

    @property
    def exit_code(self) -> int:
        return self.evidence.exit_code

    def payload(self) -> dict[str, object]:
        return {
            "schemaVersion": DIRECT_DIAGNOSTIC_SCHEMA,
            "status": "passed" if self.exit_code == 0 else "failed",
            "altitude": "diagnostic",
            "certifying": False,
            "executed": True,
            "message": "NON-CERTIFYING local feedback; Dagger acceptance is still required",
            "pytestExitCode": self.exit_code,
            "elapsedSeconds": round(self.timing.route_seconds, 6),
            "timing": self.timing.payload(),
            "pytestStdout": self.pytest_stdout,
            "pytestStderr": self.pytest_stderr,
            "nodes": [
                {"nodeId": outcome.node_id, "outcome": outcome.outcome} for outcome in self.outcomes
            ],
            "evidence": evidence_payload(self.evidence),
        }


@dataclass(frozen=True)
class DirectDiagnosticRefused:
    code: str
    message: str
    target: str | None = None
    dependency: str | None = None

    @property
    def exit_code(self) -> int:
        return REFUSAL_EXIT_CODE

    def payload(self) -> dict[str, object]:
        return {
            "schemaVersion": DIRECT_DIAGNOSTIC_SCHEMA,
            "status": "refused",
            "altitude": "diagnostic",
            "certifying": False,
            "executed": False,
            "executedNodeCount": 0,
            "refusal": {
                "code": self.code,
                "message": self.message,
                "target": self.target,
                "dependency": self.dependency,
                "nextAction": (
                    "correct the explicit selection or run the documented Dagger quality gate; "
                    "no subset or alternative route was executed"
                ),
            },
        }


DirectDiagnosticResult = DirectDiagnosticCompleted | DirectDiagnosticRefused
CommandExecutor = Callable[[list[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]
Printer = Callable[[str], None]


class DiagnosticExecutionError(RuntimeError):
    """The eligible diagnostic process failed outside an ordinary test outcome."""


def run_direct_diagnostic(
    candidate_root: Path,
    targets: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    executor: CommandExecutor | None = None,
) -> DirectDiagnosticResult:
    """Classify the whole request, then run exactly that selection once and serially."""

    route_started = time.monotonic()
    route_started_at = _utc_now()
    argument_refusal = _argument_refusal(targets)
    if argument_refusal is not None:
        return argument_refusal
    decision = classify_direct_selection(candidate_root, targets)
    if isinstance(decision, RefusedDirectSelection):
        return _classification_refusal(decision)
    admission_finished = time.monotonic()
    admission_finished_at = _utc_now()
    bootstrap = prepare_diagnostic_pytest_bootstrap(decision)
    bootstrap_prepared = time.monotonic()
    bootstrap_prepared_at = _utc_now()
    execute = executor or _execute
    with tempfile.TemporaryDirectory(prefix="ar-python-diagnostic-", dir="/tmp") as temporary:
        runtime_root = Path(temporary)
        report_path = runtime_root / "pytest-phases.json"
        child_environment = diagnostic_pytest_environment(
            bootstrap,
            os.environ if environ is None else environ,
            cache_root=runtime_root / "cache",
        )
        command = _pytest_command(decision, runtime_root)
        completed = execute(command, decision.candidate_root, child_environment)
        outcomes, phase_evidence = _load_phase_report(
            report_path,
            decision,
            completed.returncode,
        )
    if not direct_selection_is_current(decision):
        raise DiagnosticExecutionError(
            "candidate changed during diagnostic execution; discard the result and rerun"
        )
    route_finished = time.monotonic()
    route_finished_at = _utc_now()
    first_node_started_at = phase_evidence.timestamps["firstNodeStartedAt"]
    timing = DirectDiagnosticTiming(
        route_started_at=route_started_at,
        admission_finished_at=admission_finished_at,
        bootstrap_prepared_at=bootstrap_prepared_at,
        route_finished_at=route_finished_at,
        route_seconds=route_finished - route_started,
        admission_seconds=admission_finished - route_started,
        bootstrap_preparation_seconds=bootstrap_prepared - admission_finished,
        time_to_first_node_start_seconds=(
            None
            if first_node_started_at is None
            else max(
                0.0,
                (
                    _parsed_stamp(first_node_started_at) - _parsed_stamp(route_started_at)
                ).total_seconds(),
            )
        ),
        pytest=phase_evidence,
    )
    return DirectDiagnosticCompleted(
        DiagnosticTestEvidence(decision.binding, decision.nodes, completed.returncode),
        outcomes,
        timing,
        _bounded_output(completed.stdout),
        _bounded_output(completed.stderr),
    )


def _argument_refusal(targets: Sequence[str]) -> DirectDiagnosticRefused | None:
    for target in targets:
        if target.startswith(_PARALLEL_PREFIXES):
            return DirectDiagnosticRefused(
                "unsupported-parallel",
                "direct Python diagnostics are serial and refuse pytest worker/parallel flags",
                target=target,
            )
        if target.startswith("-"):
            return DirectDiagnosticRefused(
                "unsupported-argument",
                "direct Python diagnostics accept exact pytest node IDs only",
                target=target,
            )
    return None


def _classification_refusal(decision: RefusedDirectSelection) -> DirectDiagnosticRefused:
    dependency = None
    if decision.dependency is not None:
        dependency = (
            f"{decision.dependency.path}:{decision.dependency.line}:"
            f"{decision.dependency.symbol} ({decision.dependency.detail})"
        )
    return DirectDiagnosticRefused(
        decision.code.value,
        decision.message,
        target=decision.target,
        dependency=dependency,
    )


def _pytest_command(
    selection: EligibleDirectSelection,
    runtime_root: Path,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        f"--rootdir={selection.candidate_root.as_posix()}",
        "-c",
        (selection.candidate_root / "pyproject.toml").as_posix(),
        "--noconftest",
        "-p",
        "agents_remember.testing.pytest_bootstrap",
        "-p",
        "agents_remember.testing.evidence_lanes",
        "-p",
        "agents_remember.testing.pytest_phase_reporter",
        PYTEST_PHASE_REPORT_OPTION,
        (runtime_root / "pytest-phases.json").as_posix(),
        "-n=0",
        "--basetemp",
        (runtime_root / "pytest").as_posix(),
        *selection.nodes,
    ]


def _load_phase_report(
    report_path: Path,
    selection: EligibleDirectSelection,
    process_exit_code: int,
) -> tuple[tuple[DirectNodeOutcome, ...], PytestPhaseEvidence]:
    if process_exit_code < 0:
        raise DiagnosticExecutionError(
            f"diagnostic pytest terminated by signal {-process_exit_code}; no result published"
        )
    raw = _read_phase_payload(report_path)
    if raw.get("pytestExitCode") != process_exit_code:
        raise DiagnosticExecutionError("diagnostic pytest report contradicts the process exit code")
    outcomes = _node_outcomes(raw.get("nodes"), selection)
    timestamps = _timestamps(raw.get("timestamps"))
    phases = _phase_seconds(raw.get("phaseSeconds"))
    return outcomes, PytestPhaseEvidence(timestamps=timestamps, phase_seconds=phases)


def _read_phase_payload(report_path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DiagnosticExecutionError(
            "diagnostic pytest produced no readable local report"
        ) from error
    if not isinstance(raw, dict) or raw.get("schemaVersion") != PYTEST_PHASE_REPORT_SCHEMA:
        raise DiagnosticExecutionError("diagnostic pytest report schema is invalid")
    if set(raw) != {"schemaVersion", "pytestExitCode", "timestamps", "phaseSeconds", "nodes"}:
        raise DiagnosticExecutionError("diagnostic pytest report fields are invalid")
    return raw


def _node_outcomes(
    raw_nodes: object,
    selection: EligibleDirectSelection,
) -> tuple[DirectNodeOutcome, ...]:
    if not isinstance(raw_nodes, list):
        raise DiagnosticExecutionError("diagnostic pytest report node outcomes are invalid")
    outcomes: list[DirectNodeOutcome] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict) or set(raw_node) != {"nodeId", "outcome"}:
            raise DiagnosticExecutionError("diagnostic pytest report node outcomes are invalid")
        node_id = raw_node.get("nodeId")
        outcome = raw_node.get("outcome")
        if not isinstance(node_id, str) or outcome not in {"passed", "failed", "skipped"}:
            raise DiagnosticExecutionError("diagnostic pytest report node outcomes are invalid")
        outcomes.append(DirectNodeOutcome(node_id, outcome))
    if tuple(outcome.node_id for outcome in outcomes) != selection.nodes:
        raise DiagnosticExecutionError(
            "diagnostic pytest did not report the exact classified node sequence"
        )
    return tuple(outcomes)


def _timestamps(raw: object) -> dict[str, str | None]:
    expected = {
        "reporterImportedAt",
        "sessionStartedAt",
        "collectionFinishedAt",
        "firstNodeStartedAt",
        "reportingStartedAt",
        "reportingFinishedAt",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise DiagnosticExecutionError("diagnostic pytest timestamps are invalid")
    result: dict[str, str | None] = {}
    for name in sorted(expected):
        value = raw.get(name)
        if value is not None and not isinstance(value, str):
            raise DiagnosticExecutionError("diagnostic pytest timestamps are invalid")
        if isinstance(value, str):
            _parsed_stamp(value)
        result[name] = value
    return result


def _phase_seconds(raw: object) -> dict[str, float | None]:
    expected = {
        "bootstrap",
        "collection",
        "collectionToFirstNodeStart",
        "execution",
        "reporting",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise DiagnosticExecutionError("diagnostic pytest phase durations are invalid")
    result: dict[str, float | None] = {}
    for name in sorted(expected):
        value = raw.get(name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int | float) or value < 0
        ):
            raise DiagnosticExecutionError("diagnostic pytest phase durations are invalid")
        result[name] = None if value is None else float(value)
    return result


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parsed_stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DiagnosticExecutionError("diagnostic pytest timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise DiagnosticExecutionError("diagnostic pytest timestamp has no timezone")
    return parsed


def _execute(
    command: list[str],
    cwd: Path,
    environ: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=dict(environ),
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _bounded_output(value: str | None) -> str:
    if value is None:
        return ""
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= PYTEST_OUTPUT_MAX_BYTES:
        return value
    marker = "[older pytest output truncated]\n"
    budget = PYTEST_OUTPUT_MAX_BYTES - len(marker.encode("utf-8"))
    tail = encoded[-budget:].decode("utf-8", errors="ignore")
    return marker + tail


def help_text() -> str:
    return (
        "usage: ./scripts/test-python EXACT_NODE [EXACT_NODE ...]\n\n"
        "Run at most eight exact nodes from the content-sealed cohort manifest serially as "
        "NON-CERTIFYING local diagnostics. Current exact form: "
        "mcp/tests/test_file.py::test_name. Non-members, changed audited content, and all "
        "other refusals run zero tests and never fall back to Dagger. Final acceptance still "
        "requires the documented Dagger gate."
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    candidate_root: Path | None = None,
    executor: CommandExecutor | None = None,
    printer: Printer = print,
) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments in {("-h",), ("--help",)}:
        printer(help_text())
        return 0
    try:
        result = run_direct_diagnostic(
            (candidate_root or Path.cwd()).resolve(),
            arguments,
            executor=executor,
        )
    except (DiagnosticBootstrapError, DiagnosticExecutionError, OSError) as error:
        printer(
            json.dumps(
                {
                    "schemaVersion": DIRECT_DIAGNOSTIC_SCHEMA,
                    "status": "error",
                    "altitude": "diagnostic",
                    "certifying": False,
                    "executed": False,
                    "message": f"diagnostic infrastructure error: {error}",
                },
                sort_keys=True,
            )
        )
        return INTERNAL_ERROR_EXIT_CODE
    printer(json.dumps(result.payload(), sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
