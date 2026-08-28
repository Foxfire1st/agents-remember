"""One command builder for every Dagger invocation of the Python quality wrapper."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpectedCommand:
    """One non-accepting evidence step and its deliberate exit contract."""

    name: str
    command: tuple[str, ...]
    expected_exit: int


def quality_wrapper_command(
    *,
    reports: str,
    diff_base: str,
    mode: str,
    memory_cap_bytes: int = 0,
    threshold: float | None = None,
) -> list[str]:
    """Build the canonical wrapper command without route-local argument drift."""

    command = [
        "/opt/ar-venv/bin/python",
        "-m",
        "agents_remember_test_support.code_quality.check",
        "--pytest-report-log",
        f"{reports}/pytest-events.jsonl",
        "--pytest-phase-report",
        f"{reports}/pytest-phases.json",
        "--causal-failure-report",
        f"{reports}/causal-failures.json",
        "--coverage-json",
        f"{reports}/coverage.json",
        "--coverage-data",
        f"{reports}/coverage.data",
        "--progress-report",
        f"{reports}/quality-progress.json",
    ]
    if mode == "targeted":
        command.append("--targeted")
    command += ["--diff-base", diff_base]
    if memory_cap_bytes > 0:
        command += ["--memory-cap-bytes", str(memory_cap_bytes)]
    if threshold is not None:
        command += ["--threshold", str(threshold)]
    return command


def retry_decision_lines(output: str) -> tuple[str, ...]:
    """Extract bounded retry evidence from the ordinary wrapper output."""

    return tuple(line for line in output.splitlines() if line.startswith("retry-proof:"))


def causal_evidence_steps(reports: str) -> tuple[ExpectedCommand, ...]:
    """Build the controlled baseline/localized/verification command sequence."""

    module = "agents_remember_test_support.testing.causal_route_evidence"
    python = "/opt/ar-venv/bin/python"
    common = (python, "-m", module)
    causal_report = f"{reports}/causal-failures.json"
    return (
        ExpectedCommand(
            "prepare",
            (
                *common,
                "prepare",
                "--project-root",
                "/workspace",
                "--causal-report",
                causal_report,
            ),
            0,
        ),
        ExpectedCommand(
            "baseline",
            (
                *common,
                "run",
                "--project-root",
                "/workspace",
                "--phase-report",
                f"{reports}/causal-baseline-phases.json",
            ),
            1,
        ),
        ExpectedCommand(
            "localized",
            (
                *common,
                "run",
                "--project-root",
                "/workspace",
                "--phase-report",
                f"{reports}/causal-localized-phases.json",
                "--causal-report",
                causal_report,
            ),
            0,
        ),
        ExpectedCommand(
            "verify",
            (
                *common,
                "verify",
                "--causal-report",
                causal_report,
                "--baseline-phase-report",
                f"{reports}/causal-baseline-phases.json",
                "--localized-phase-report",
                f"{reports}/causal-localized-phases.json",
                "--output",
                f"{reports}/causal-evidence.json",
            ),
            0,
        ),
    )
