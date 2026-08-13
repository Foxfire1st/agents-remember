"""Strict source-quality enforcement for Agents Remember code commits."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import git_environment, run_git
from agents_remember.kernel.platform_subprocess import (
    native_command,
    native_subprocess_environment,
)
from agents_remember.kernel.primitives import memory_cap
from agents_remember.worktrees.modules.clean_quality_executor import (
    CleanQualityRequest,
    run_clean_quality,
)

QUALITY_WRAPPER = Path("mcp/src/agents_remember/code_quality/check.py")
QUALITY_MODULE = "agents_remember.code_quality.check"
FAILURE_OUTPUT_LINES = 40
REPORT_DIRECTORY_NAME = "reports"
TEST_RESULTS_REPORT_NAME = "test-results.md"
PYTEST_EVENTS_REPORT_NAME = "pytest-events.jsonl"
COVERAGE_DATA_REPORT_NAME = "coverage.data"
QUALITY_PROGRESS_REPORT_NAME = "quality-progress.json"
CODEX_PROBE_REPORT_NAME = "codex-probe.json"
QUALITY_TEMP_ROOT = Path("/tmp/arq")

GATE_ENFORCED = "enforced"
GATE_NO_CODE_COMMIT = "no-code-commit"
GATE_WRAPPER_UNAVAILABLE = "wrapper-unavailable"
GATE_TARGETED = "targeted"
GATE_FULL = "full"

QualityRunner = Callable[[list[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class QualityGatePlan:
    """What one gate run is, plus an optional explicit full-run memory cap."""

    mode: str = GATE_TARGETED
    memory_cap_bytes: int | None = None
    systemd_run_available: bool | None = None
    executor: str = "local"


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
    cap_plan: memory_cap.MemoryCapPlan | None
    requested_memory_cap_bytes: int | None


def quality_wrapper_path(code_worktree: Path) -> Path:
    """Where a checkout carries the project-owned quality wrapper."""
    return code_worktree / QUALITY_WRAPPER


def test_results_report_path(worktree_group: Path) -> Path:
    """Return the enclosure-owned report for the latest completed quality run."""
    return worktree_group / REPORT_DIRECTORY_NAME / TEST_RESULTS_REPORT_NAME


def pytest_events_report_path(worktree_group: Path) -> Path:
    """Return the enclosure-owned, self-overwriting live pytest event report."""
    return worktree_group / REPORT_DIRECTORY_NAME / PYTEST_EVENTS_REPORT_NAME


def coverage_data_report_path(worktree_group: Path) -> Path:
    """Return the enclosure-owned Coverage.py state path."""
    return worktree_group / REPORT_DIRECTORY_NAME / COVERAGE_DATA_REPORT_NAME


def quality_progress_report_path(worktree_group: Path) -> Path:
    """Return the one atomic current-rail projection for this enclosure."""
    return worktree_group / REPORT_DIRECTORY_NAME / QUALITY_PROGRESS_REPORT_NAME


def codex_probe_report_path(worktree_group: Path) -> Path:
    """Return the real-versus-fake Codex integration evidence path."""
    return worktree_group / REPORT_DIRECTORY_NAME / CODEX_PROBE_REPORT_NAME


def _gate_command(
    diff_base: str,
    *,
    mode: str = GATE_TARGETED,
    memory_cap_bytes: int | None = None,
    systemd_run_available: bool | None = None,
    executor: str = "local",
) -> str:
    """The command as reported, so a payload reader can rerun exactly what ran."""
    if mode not in {GATE_TARGETED, GATE_FULL}:
        raise ValueError(f"unknown quality gate mode: {mode}")
    if executor not in {"local", "dagger"}:
        raise ValueError(f"unknown quality gate executor: {executor}")
    if executor == "dagger":
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
    base = f" --diff-base {diff_base}" if diff_base else ""
    if mode == GATE_TARGETED:
        return f"python -m {QUALITY_MODULE} --targeted{base}"
    if memory_cap_bytes is None:
        return f"python -m {QUALITY_MODULE}{base}"
    plan = memory_cap.plan_capped_command(
        "python",
        ["-m", QUALITY_MODULE],
        memory_cap_bytes,
        systemd_run_available=systemd_run_available,
    )
    rendered = " ".join(plan.command)
    return f"{rendered}{base}"


def requires_strict_code_quality(code_worktree: Path, *, code_would_commit: bool) -> bool:
    """Whether this closeout must run the wrapper the checkout carries.

    Availability of the wrapper decides this, not the repository's name: the gate
    is documented as mandatory for every repository, so it applies wherever it can
    run at all.
    """
    return code_would_commit and quality_wrapper_path(code_worktree).is_file()


def code_quality_gate_preview(
    code_worktree: Path,
    *,
    code_would_commit: bool,
    diff_base: str = "",
    plan: QualityGatePlan | None = None,
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
    if plan.executor not in {"local", "dagger"}:
        raise ValueError(f"unknown quality gate executor: {plan.executor}")
    memory_cap_payload: dict[str, object] = {}
    if plan.mode == GATE_FULL:
        cap_bytes = plan.memory_cap_bytes
        cap_plan = (
            None
            if cap_bytes is None or plan.executor == "dagger"
            else memory_cap.plan_capped_command(
                "python",
                ["-m", QUALITY_MODULE],
                cap_bytes,
                systemd_run_available=plan.systemd_run_available,
            )
        )
        memory_cap_payload = _memory_policy_payload(
            cap_plan,
            executor=plan.executor,
            requested_cap_bytes=cap_bytes,
        )
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "command": _gate_command(
            diff_base,
            mode=plan.mode,
            memory_cap_bytes=plan.memory_cap_bytes,
            systemd_run_available=plan.systemd_run_available,
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


def run_subprocess(
    command: list[str], cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        native_command(command, env),
        cwd=cwd,
        env=dict(env),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _validated_quality_gate_plan(plan: QualityGatePlan | None) -> QualityGatePlan:
    resolved = plan or QualityGatePlan()
    if resolved.mode not in {GATE_TARGETED, GATE_FULL}:
        raise ValueError(f"unknown quality gate mode: {resolved.mode}")
    if resolved.executor not in {"local", "dagger"}:
        raise ValueError(f"unknown quality gate executor: {resolved.executor}")
    return resolved


def run_strict_code_quality_gate(
    target: QualityGateTarget,
    *,
    diff_base: str = "",
    plan: QualityGatePlan | None = None,
    invocation: str = "closeout-staged",
    runner: QualityRunner = run_subprocess,
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
            f"project-owned wrapper is missing at {wrapper}"
        )
    plan = _validated_quality_gate_plan(plan)
    pytest_report = pytest_events_report_path(target.worktree_group)
    command, invocation, cap_plan = _gate_command_parts(
        code_worktree,
        plan,
        diff_base,
        invocation,
        pytest_report=pytest_report,
    )
    started_at = datetime.now(UTC)
    started = time.monotonic()
    if plan.executor == "dagger":
        result = run_clean_quality(
            CleanQualityRequest(
                code_worktree=code_worktree,
                worktree_group=target.worktree_group,
                mode=plan.mode,
                diff_base=diff_base,
                memory_cap_bytes=plan.memory_cap_bytes,
            )
        )
    else:
        result = runner(
            command,
            code_worktree,
            quality_environment(
                code_worktree,
                reports_root=target.worktree_group / REPORT_DIRECTORY_NAME,
                invocation=invocation,
            ),
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
            cap_plan=cap_plan,
            requested_memory_cap_bytes=plan.memory_cap_bytes,
        )
    )
    if result.returncode != 0:
        raise RuntimeError(_gate_failure_message(result, cap_plan, report_path))
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "passed": True,
        "command": _gate_command(
            diff_base,
            mode=plan.mode,
            memory_cap_bytes=plan.memory_cap_bytes,
            systemd_run_available=plan.systemd_run_available,
            executor=plan.executor,
        ),
        "diffBase": diff_base,
        "mode": plan.mode,
        "executor": plan.executor,
        "reportPath": report_path.as_posix(),
        **(
            _memory_policy_payload(
                cap_plan,
                executor=plan.executor,
                requested_cap_bytes=plan.memory_cap_bytes,
            )
            if plan.mode == GATE_FULL
            else {}
        ),
    }


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
    if report.cap_plan is not None:
        lines.extend(
            [
                f"- Memory-cap policy: `{report.cap_plan.policy}`",
                f"- Memory-cap mechanism: `{report.cap_plan.mechanism}`",
                f"- Memory-cap bytes: `{report.cap_plan.cap_bytes}`",
                "- Swap policy: `host-managed`",
            ]
        )
    elif report.requested_memory_cap_bytes is not None:
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
                f"- Memory policy: `{'container-host-managed' if report.executor == 'dagger' else 'host-managed'}`",
                f"- Swap policy: `{'container-host-managed' if report.executor == 'dagger' else 'host-managed'}`",
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
    code_worktree: Path,
    plan: QualityGatePlan,
    diff_base: str,
    invocation: str,
    *,
    pytest_report: Path | None = None,
) -> tuple[list[str], str, memory_cap.MemoryCapPlan | None]:
    """The concrete command, its invocation label, and (full runs) the cap plan."""
    if plan.executor == "dagger":
        return (
            _dagger_report_command(plan, diff_base),
            "master-integration" if plan.mode == GATE_FULL else invocation,
            None,
        )
    python = quality_python(code_worktree)
    module_args = ["-m", QUALITY_MODULE]
    if pytest_report is not None:
        module_args += ["--pytest-report-log", pytest_report.as_posix()]
    if plan.mode == GATE_TARGETED:
        module_args.append("--targeted")
    if diff_base:
        module_args += ["--diff-base", diff_base]
    if plan.mode != GATE_FULL:
        return [python.as_posix(), *module_args], invocation, None
    if plan.memory_cap_bytes is None:
        return [python.as_posix(), *module_args], "master-integration", None
    cap_plan = memory_cap.plan_capped_command(
        python,
        module_args,
        plan.memory_cap_bytes,
        systemd_run_available=plan.systemd_run_available,
    )
    return cap_plan.command, "master-integration", cap_plan


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
    cap_plan: memory_cap.MemoryCapPlan | None,
    *,
    executor: str = "local",
    requested_cap_bytes: int | None = None,
) -> dict[str, object]:
    if executor == "dagger":
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
    policy: dict[str, object] = {
        "mode": "host-managed" if cap_plan is None else "explicit-cap",
        "pytestProcesses": "auto",
        "swap": "host-managed",
    }
    if cap_plan is None:
        return {"memoryPolicy": policy}
    return {
        "memoryPolicy": policy,
        "memoryCap": {
            "capBytes": cap_plan.cap_bytes,
            "policy": cap_plan.policy,
            "mechanism": cap_plan.mechanism,
        },
    }


def _gate_failure_message(
    result: subprocess.CompletedProcess[str],
    cap_plan: memory_cap.MemoryCapPlan | None,
    report_path: Path,
) -> str:
    """One refusal message: nothing committed, plus cap policy when a cap ran."""
    details = _failure_output(result.stdout)
    if cap_plan is not None:
        killed = (
            " The scope was killed by the memory cap (returncode -9, shell exit 137) "
            "inside its own scope."
            if result.returncode in (137, -9)
            else ""
        )
        details += (
            f"\nfull gate memory policy: {cap_plan.policy}; "
            f"mechanism={cap_plan.mechanism}; cap={cap_plan.cap_bytes} bytes; "
            f"exit code {result.returncode}.{killed}"
            " Raise orchestration.qualityGate.memoryCapBytes only after the run itself "
            "is proven healthy."
        )
    return (
        "strict code-quality gate failed before code commit"
        f" with exit code {result.returncode}; code, memory, and ledger remain uncommitted."
        f" Full output: {report_path.as_posix()}."
        f"{details}"
    )


def quality_python(code_worktree: Path) -> Path:
    """Use the worktree or shared-clone dev interpreter, then the active server Python."""
    local_python = code_worktree / ".venv" / "bin" / "python"
    if local_python.is_file():
        return local_python
    common_dir = _git_common_dir(code_worktree)
    if common_dir is not None:
        shared_python = common_dir.parent / ".venv" / "bin" / "python"
        if shared_python.is_file():
            return shared_python
    active_python = Path(sys.executable)
    if active_python.is_file():
        return active_python
    raise RuntimeError(
        "strict code-quality gate cannot run before code commit: no Python interpreter found"
    )


def quality_environment(
    code_worktree: Path,
    *,
    reports_root: Path,
    invocation: str = "closeout-staged",
) -> dict[str, str]:
    """Put the current worktree package first even when Python comes from another checkout.

    Built from :func:`git_environment` rather than ``os.environ``: the wrapper this hands the
    environment to derives its own scope from ``git ls-files`` and its diff base from
    ``merge-base``, and closeout spawns it from paths where ``GIT_DIR`` can be exported. Every
    git call inside that subprocess strips the selectors itself today, so this is defence in
    depth -- but the gate decides which repository gets certified, and that must not rest on
    the good behaviour of a child process this one cannot see.
    """
    env = git_environment()
    entries = [(code_worktree / "mcp" / "src").as_posix()]
    existing = env.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    # Naming the invoking altitude lets the shared wrapper describe what it certifies:
    # closeout has already reset and staged the whole task worktree; integration runs
    # on a clean checkout at the commit about to land.
    env["AR_QUALITY_INVOCATION"] = invocation
    env["COVERAGE_FILE"] = coverage_data_report_path(reports_root.parent).as_posix()
    env["AR_QUALITY_PROGRESS_REPORT"] = quality_progress_report_path(reports_root.parent).as_posix()
    env["AR_CODEX_PROBE_REPORT"] = codex_probe_report_path(reports_root.parent).as_posix()
    # WSL imports Windows PATH/TEMP by design. Keep durable output enclosure-owned, but use a
    # deliberately short native scratch root: pytest-created coordination roots may contain
    # Unix sockets, whose 103-byte address limit cannot tolerate a reports/enclosure prefix.
    return native_subprocess_environment(env, temp_root=QUALITY_TEMP_ROOT)


def _git_common_dir(code_worktree: Path) -> Path | None:
    # On the one runner: an inherited GIT_DIR would answer with *its* common dir, and
    # this value decides which repository the closeout quality gate then certifies.
    result = run_git(code_worktree, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def _failure_output(output: str) -> str:
    lines = output.strip().splitlines()
    if not lines:
        return ""
    return "\nQuality output tail:\n" + "\n".join(lines[-FAILURE_OUTPUT_LINES:])
