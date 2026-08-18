"""Strict source-quality enforcement for Agents Remember code commits."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.worktrees.modules.clean_quality_executor import (
    CleanQualityRequest,
    published_quality_attestation,
    published_report_path,
    run_clean_quality,
)

QUALITY_WRAPPER = Path("mcp/src/agents_remember/code_quality/check.py")
FAILURE_OUTPUT_LINES = 40
REPORT_DIRECTORY_NAME = "reports"
TEST_RESULTS_REPORT_NAME = "test-results.md"
CLEAN_QUALITY_RESULTS_NAME = "clean-quality-results.json"

GATE_ENFORCED = "enforced"
GATE_NO_CODE_COMMIT = "no-code-commit"
GATE_WRAPPER_UNAVAILABLE = "wrapper-unavailable"
GATE_TARGETED = "targeted"
GATE_FULL = "full"
SELF_ACCEPTANCE_REPOSITORY = "agents-remember"


@dataclass(frozen=True)
class QualityGatePlan:
    """What one gate run is, plus an optional explicit full-run memory cap."""

    mode: str = GATE_TARGETED
    memory_cap_bytes: int | None = None
    executor: str = "dagger"


@dataclass(frozen=True)
class QualityGateTarget:
    """The checkout being certified and its owning enclosure."""

    code_worktree: Path
    worktree_group: Path


@dataclass(frozen=True)
class _QualityGateReport:
    path: Path
    result: subprocess.CompletedProcess[str]
    command: list[str]
    invocation: str
    mode: str
    executor: str
    diff_base: str
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float
    requested_memory_cap_bytes: int | None


def quality_wrapper_path(code_worktree: Path) -> Path:
    """Where a checkout carries the project-owned quality wrapper."""
    return code_worktree / QUALITY_WRAPPER


def test_results_report_path(worktree_group: Path) -> Path:
    """Return the enclosure-owned report for the latest completed quality run."""
    return worktree_group / REPORT_DIRECTORY_NAME / TEST_RESULTS_REPORT_NAME


def _gate_command(
    diff_base: str,
    *,
    mode: str = GATE_TARGETED,
    memory_cap_bytes: int | None = None,
    executor: str = "dagger",
) -> str:
    """The command as reported, so a payload reader can rerun exactly what ran."""
    if mode not in {GATE_TARGETED, GATE_FULL}:
        raise ValueError(f"unknown quality gate mode: {mode}")
    if executor != "dagger":
        raise ValueError("quality gate executor must be the pinned Dagger graph")
    return shlex.join(
        _dagger_report_command(
            QualityGatePlan(
                mode=mode,
                memory_cap_bytes=memory_cap_bytes,
                executor=executor,
            ),
            diff_base,
        )
    )


def requires_integrated_acceptance(repo_name: str) -> bool:
    """Whether repository policy makes the integrated wrapper mandatory."""
    return repo_name == SELF_ACCEPTANCE_REPOSITORY


def requires_strict_code_quality(
    code_worktree: Path,
    *,
    code_would_commit: bool,
    required_when_missing: bool = False,
) -> bool:
    """Whether this boundary must run the integrated acceptance adapter.

    Consumer repositories opt in by carrying the adapter. A repository whose own
    policy requires the adapter can additionally make its absence fail closed.
    """
    return code_would_commit and (
        required_when_missing or quality_wrapper_path(code_worktree).is_file()
    )


def code_quality_gate_preview(
    code_worktree: Path,
    *,
    code_would_commit: bool,
    diff_base: str = "",
    plan: QualityGatePlan | None = None,
    required_when_missing: bool = False,
) -> dict[str, object]:
    """Report which of the three gate states this closeout is in.

    A consuming repository that carries no wrapper reaches ``wrapper-unavailable``,
    which is a reported state rather than a silent skip: the closeout still runs,
    and the payload says the code commit was not quality-checked and why.
    """
    if not code_would_commit:
        return {
            "required": False,
            "status": GATE_NO_CODE_COMMIT,
            "command": "",
            "reason": "no code commit would be created",
        }
    if not quality_wrapper_path(code_worktree).is_file():
        if required_when_missing:
            raise RuntimeError(
                "repository policy requires integrated acceptance, but the candidate is missing "
                f"its self-owned wrapper at {QUALITY_WRAPPER.as_posix()}"
            )
        return {
            "required": False,
            "status": GATE_WRAPPER_UNAVAILABLE,
            "command": "",
            "reason": (
                "code would commit but this checkout carries no quality wrapper at "
                f"{QUALITY_WRAPPER.as_posix()}; the strict gate cannot run and this "
                "code commit is not quality-checked"
            ),
        }
    plan = plan or QualityGatePlan()
    if plan.executor != "dagger":
        raise ValueError(
            "lifecycle quality acceptance requires the pinned Dagger executor; "
            f"received {plan.executor!r}"
        )
    memory_cap_payload: dict[str, object] = {}
    if plan.mode == GATE_FULL:
        memory_cap_payload = _memory_policy_payload(
            executor=plan.executor,
            requested_cap_bytes=plan.memory_cap_bytes,
        )
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "command": _gate_command(
            diff_base,
            mode=plan.mode,
            memory_cap_bytes=plan.memory_cap_bytes,
            executor=plan.executor,
        ),
        "diffBase": diff_base,
        "mode": plan.mode,
        "executor": plan.executor,
        **memory_cap_payload,
        "reason": (
            "closeout stages the whole task worktree so the gate's scope is the commit's "
            "content, then runs the leaf change-set-scoped quality contract (--targeted) "
            "over exactly that before the code commit. The full wrapper runs once per "
            "master, at the master integration gate, not at leaf closeout."
        ),
    }


def _validated_quality_gate_plan(plan: QualityGatePlan | None) -> QualityGatePlan:
    resolved = plan or QualityGatePlan()
    if resolved.mode not in {GATE_TARGETED, GATE_FULL}:
        raise ValueError(f"unknown quality gate mode: {resolved.mode}")
    if resolved.executor != "dagger":
        raise ValueError(
            "lifecycle quality acceptance requires the pinned Dagger executor; "
            f"received {resolved.executor!r}"
        )
    return resolved


def run_strict_code_quality_gate(
    target: QualityGateTarget,
    *,
    diff_base: str = "",
    plan: QualityGatePlan | None = None,
    invocation: str = "closeout-staged",
    attestation: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Run the altitude-routed quality contract or refuse before the commit.

    ``diff_base`` must be the recorded base commit so the coverage floor measures
    this change set, not the whole integration branch. The wrapper certifies the
    index/working tree it is handed; on failure nothing is committed and closeout
    deliberately leaves its staging in place.
    """
    code_worktree = target.code_worktree
    wrapper = quality_wrapper_path(code_worktree)
    if not wrapper.is_file():
        raise RuntimeError(
            "strict code-quality gate cannot run before code commit: "
            f"self-owned wrapper is missing at {wrapper}"
        )
    plan = _validated_quality_gate_plan(plan)
    command, invocation = _gate_command_parts(plan, diff_base, invocation)
    started_at = datetime.now(UTC)
    started = time.monotonic()
    result = run_clean_quality(
        CleanQualityRequest(
            code_worktree=code_worktree,
            worktree_group=target.worktree_group,
            mode=plan.mode,
            diff_base=diff_base,
            memory_cap_bytes=plan.memory_cap_bytes,
            attestation=attestation,
        )
    )
    finished_at = datetime.now(UTC)
    report_path = test_results_report_path(target.worktree_group)
    _write_test_results_report(
        _QualityGateReport(
            path=report_path,
            result=result,
            command=command,
            invocation=invocation,
            mode=plan.mode,
            executor=plan.executor,
            diff_base=diff_base,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=time.monotonic() - started,
            requested_memory_cap_bytes=plan.memory_cap_bytes,
        )
    )
    if result.returncode != 0:
        raise RuntimeError(
            _gate_failure_message(
                result,
                report_path,
                requested_memory_cap_bytes=plan.memory_cap_bytes,
            )
        )
    return _strict_quality_success_payload(
        target,
        diff_base=diff_base,
        plan=plan,
    )


def recover_strict_code_quality_gate(
    target: QualityGateTarget,
    *,
    diff_base: str,
    plan: QualityGatePlan,
    attestation: Mapping[str, str],
) -> dict[str, object] | None:
    """Recover one exact passed Dagger generation after its caller crashed."""

    reports = target.worktree_group / REPORT_DIRECTORY_NAME
    try:
        published = published_quality_attestation(reports)
    except RuntimeError:
        return None
    if published != dict(attestation):
        return None
    report_path = published_report_path(reports, CLEAN_QUALITY_RESULTS_NAME)
    try:
        result = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise RuntimeError("published Dagger result is unreadable") from error
    if result.get("status") != "passed" or result.get("exitCode") != 0:
        return None
    return _strict_quality_success_payload(
        target,
        diff_base=diff_base,
        plan=plan,
        report_path=report_path,
    )


def _strict_quality_success_payload(
    target: QualityGateTarget,
    *,
    diff_base: str,
    plan: QualityGatePlan,
    report_path: Path | None = None,
) -> dict[str, object]:
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "passed": True,
        "command": _gate_command(
            diff_base,
            mode=plan.mode,
            memory_cap_bytes=plan.memory_cap_bytes,
            executor=plan.executor,
        ),
        "diffBase": diff_base,
        "mode": plan.mode,
        "executor": plan.executor,
        "reportPath": (report_path or test_results_report_path(target.worktree_group)).as_posix(),
        **(
            _memory_policy_payload(
                executor=plan.executor,
                requested_cap_bytes=plan.memory_cap_bytes,
            )
            if plan.mode == GATE_FULL
            else {}
        ),
    }


def run_local_quality_diagnostic(
    target: QualityGateTarget,
    *,
    diff_base: str = "",
    plan: QualityGatePlan | None = None,
) -> NoReturn:
    """Refuse host quality execution; acceptance and test diagnostics are Dagger-only."""
    del target, diff_base, plan
    raise RuntimeError(
        "host quality execution is forbidden; run tests through the pinned Dagger graph"
    )


def _write_test_results_report(report: _QualityGateReport) -> None:
    """Atomically replace the one durable report for a completed strict gate run."""
    output = (report.result.stdout or "").rstrip()
    lines = [
        "# Strict Quality Test Results",
        "",
        f"- Status: **{'passed' if report.result.returncode == 0 else 'failed'}**",
        f"- Invocation: `{report.invocation}`",
        f"- Mode: `{report.mode}`",
        f"- Executor: `{report.executor}`",
        f"- Diff base: `{report.diff_base or '(none)'}`",
        f"- Exit code: `{report.result.returncode}`",
        f"- Started: `{report.started_at.replace(microsecond=0).isoformat()}`",
        f"- Finished: `{report.finished_at.replace(microsecond=0).isoformat()}`",
        f"- Elapsed seconds: `{report.elapsed_seconds:.3f}`",
        f"- Command: `{shlex.join(report.command)}`",
    ]
    if report.requested_memory_cap_bytes is not None:
        lines.extend(
            [
                "- Memory-cap policy: `dagger-inner-wrapper`",
                f"- Memory-cap bytes: `{report.requested_memory_cap_bytes}`",
                "- Swap policy: `container-host-managed`",
            ]
        )
    elif report.mode == GATE_FULL:
        lines.extend(
            [
                "- Memory policy: `container-host-managed`",
                "- Swap policy: `container-host-managed`",
            ]
        )
    lines.extend(
        [
            "",
            (
                "This file contains the latest completed strict quality run, including its "
                "pytest rail. The next completed run atomically replaces it; worktree cleanup "
                "removes it with the enclosure."
            ),
            "",
            "## Output",
            "",
        ]
    )
    if output:
        lines.extend(f"    {line}" for line in output.splitlines())
    else:
        lines.append("_No output was captured._")
    atomic_write_text(report.path, "\n".join(lines) + "\n")


def _gate_command_parts(
    plan: QualityGatePlan,
    diff_base: str,
    invocation: str,
) -> tuple[list[str], str]:
    """The symbolic Dagger command and its invocation label."""
    if plan.executor != "dagger":
        raise ValueError("quality gate executor must be the pinned Dagger graph")
    return (
        _dagger_report_command(plan, diff_base),
        "master-integration" if plan.mode == GATE_FULL else invocation,
    )


def _dagger_report_command(plan: QualityGatePlan, diff_base: str) -> list[str]:
    command = [
        "dagger",
        "call",
        "quality",
        "--source=<exact-staged-candidate>",
        "--repository-bundle=<exact-git-ancestry-bundle>",
        f"--mode={plan.mode}",
    ]
    if diff_base:
        command.append(f"--diff-base={diff_base}")
    if plan.memory_cap_bytes is not None:
        command.append(f"--memory-cap-bytes={plan.memory_cap_bytes}")
    return command


def _memory_policy_payload(
    *,
    executor: str = "dagger",
    requested_cap_bytes: int | None = None,
) -> dict[str, object]:
    if executor != "dagger":
        raise ValueError("quality gate executor must be the pinned Dagger graph")
    policy: dict[str, object] = {
        "mode": "container-host-managed" if requested_cap_bytes is None else "explicit-cap",
        "pytestProcesses": "auto",
        "swap": "container-host-managed",
    }
    if requested_cap_bytes is None:
        return {"memoryPolicy": policy}
    return {
        "memoryPolicy": policy,
        "memoryCap": {
            "capBytes": requested_cap_bytes,
            "policy": "dagger-inner-wrapper",
            "mechanism": "container-wrapper",
        },
    }


def _gate_failure_message(
    result: subprocess.CompletedProcess[str],
    report_path: Path,
    *,
    requested_memory_cap_bytes: int | None = None,
) -> str:
    """One refusal message: nothing committed, plus Dagger cap policy when requested."""
    details = _failure_output(result.stdout)
    if requested_memory_cap_bytes is not None:
        killed = (
            " The Dagger container scope was killed by the memory cap."
            if result.returncode in (137, -9)
            else ""
        )
        details += (
            "\nfull gate memory policy: dagger-inner-wrapper; "
            f"cap={requested_memory_cap_bytes} bytes; exit code {result.returncode}."
            f"{killed}"
        )
    return (
        "strict code-quality gate failed before code commit"
        f" with exit code {result.returncode}; code, memory, and ledger remain uncommitted."
        f" Full output: {report_path.as_posix()}."
        f"{details}"
    )


def _failure_output(output: str) -> str:
    lines = output.strip().splitlines()
    if not lines:
        return ""
    return "\nQuality output tail:\n" + "\n".join(lines[-FAILURE_OUTPUT_LINES:])
