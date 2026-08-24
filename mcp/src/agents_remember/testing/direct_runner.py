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
from pathlib import Path

from agents_remember.models.test_evidence import DiagnosticTestEvidence, test_evidence_payload
from agents_remember.testing.diagnostic_bootstrap import (
    DiagnosticBootstrapError,
    diagnostic_pytest_environment,
    prepare_diagnostic_pytest_bootstrap,
)
from agents_remember.testing.eligibility import (
    classify_direct_selection,
    direct_selection_is_current,
)
from agents_remember.testing.pytest_diagnostic_reporter import (
    DIAGNOSTIC_REPORT_ENV,
    DIAGNOSTIC_REPORT_SCHEMA,
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
class DirectDiagnosticCompleted:
    evidence: DiagnosticTestEvidence
    outcomes: tuple[DirectNodeOutcome, ...]
    elapsed_seconds: float
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
            "elapsedSeconds": round(self.elapsed_seconds, 6),
            "pytestStdout": self.pytest_stdout,
            "pytestStderr": self.pytest_stderr,
            "nodes": [
                {"nodeId": outcome.node_id, "outcome": outcome.outcome} for outcome in self.outcomes
            ],
            "evidence": test_evidence_payload(self.evidence),
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

    argument_refusal = _argument_refusal(targets)
    if argument_refusal is not None:
        return argument_refusal
    decision = classify_direct_selection(candidate_root, targets)
    if isinstance(decision, RefusedDirectSelection):
        return _classification_refusal(decision)
    bootstrap = prepare_diagnostic_pytest_bootstrap(decision)
    execute = executor or _execute
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ar-python-diagnostic-", dir="/tmp") as temporary:
        runtime_root = Path(temporary)
        report_path = runtime_root / "pytest-diagnostic.json"
        child_environment = diagnostic_pytest_environment(
            bootstrap,
            os.environ if environ is None else environ,
            cache_root=runtime_root / "cache",
        )
        child_environment[DIAGNOSTIC_REPORT_ENV] = report_path.as_posix()
        command = _pytest_command(decision, runtime_root)
        completed = execute(command, decision.candidate_root, child_environment)
        outcomes = _load_outcomes(report_path, decision, completed.returncode)
    if not direct_selection_is_current(decision):
        raise DiagnosticExecutionError(
            "candidate changed during diagnostic execution; discard the result and rerun"
        )
    return DirectDiagnosticCompleted(
        DiagnosticTestEvidence(decision.binding, decision.nodes, completed.returncode),
        outcomes,
        time.monotonic() - started,
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
        "agents_remember.testing.pytest_diagnostic_reporter",
        "-n=0",
        "--basetemp",
        (runtime_root / "pytest").as_posix(),
        *selection.nodes,
    ]


def _load_outcomes(
    report_path: Path,
    selection: EligibleDirectSelection,
    process_exit_code: int,
) -> tuple[DirectNodeOutcome, ...]:
    if process_exit_code < 0:
        raise DiagnosticExecutionError(
            f"diagnostic pytest terminated by signal {-process_exit_code}; no result published"
        )
    try:
        raw: object = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DiagnosticExecutionError(
            "diagnostic pytest produced no readable local report"
        ) from error
    if not isinstance(raw, dict) or raw.get("schemaVersion") != DIAGNOSTIC_REPORT_SCHEMA:
        raise DiagnosticExecutionError("diagnostic pytest report schema is invalid")
    if raw.get("pytestExitCode") != process_exit_code:
        raise DiagnosticExecutionError("diagnostic pytest report contradicts the process exit code")
    raw_nodes = raw.get("nodes")
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
        "Run at most eight structurally eligible pytest nodes serially as NON-CERTIFYING "
        "local diagnostics. Exact form: mcp/tests/test_file.py::test_name or "
        "mcp/tests/test_file.py::Class::test_method. Refusals run zero tests and never "
        "fall back to Dagger. Final acceptance still requires the documented Dagger gate."
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
