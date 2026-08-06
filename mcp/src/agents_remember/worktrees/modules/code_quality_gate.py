"""Strict source-quality enforcement for Agents Remember code commits."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.code_quality import memory_cap
from agents_remember.kernel.git_command import git_environment, run_git

QUALITY_WRAPPER = Path("mcp/src/agents_remember/code_quality/check.py")
QUALITY_MODULE = "agents_remember.code_quality.check"
FAILURE_OUTPUT_LINES = 40

GATE_ENFORCED = "enforced"
GATE_NO_CODE_COMMIT = "no-code-commit"
GATE_WRAPPER_UNAVAILABLE = "wrapper-unavailable"
GATE_TARGETED = "targeted"
GATE_FULL = "full"

QualityRunner = Callable[[list[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class QualityGatePlan:
    """What one gate run is: which contract, and (for full runs) the memory cap."""

    mode: str = GATE_TARGETED
    memory_cap_bytes: int | None = None
    systemd_run_available: bool | None = None


def quality_wrapper_path(code_worktree: Path) -> Path:
    """Where a checkout carries the project-owned quality wrapper."""
    return code_worktree / QUALITY_WRAPPER


def _gate_command(
    diff_base: str,
    *,
    mode: str = GATE_TARGETED,
    memory_cap_bytes: int | None = None,
    systemd_run_available: bool | None = None,
) -> str:
    """The command as reported, so a payload reader can rerun exactly what ran."""
    if mode not in {GATE_TARGETED, GATE_FULL}:
        raise ValueError(f"unknown quality gate mode: {mode}")
    base = f" --diff-base {diff_base}" if diff_base else ""
    if mode == GATE_TARGETED:
        return f"python -m {QUALITY_MODULE} --targeted{base}"
    if memory_cap_bytes is None:
        raise ValueError("full quality gate command requires memory_cap_bytes")
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
    memory_cap_payload: dict[str, object] = {}
    if plan.mode == GATE_FULL:
        cap_bytes = plan.memory_cap_bytes
        if cap_bytes is None:
            raise ValueError("full quality gate preview requires memory_cap_bytes")
        memory_cap_payload = {
            "memoryCap": {
                "capBytes": cap_bytes,
                "policy": memory_cap.QUALITY_MEMORY_CAP_POLICY,
                "mechanism": memory_cap.plan_capped_command(
                    "python",
                    ["-m", QUALITY_MODULE],
                    cap_bytes,
                    systemd_run_available=plan.systemd_run_available,
                ).mechanism,
            }
        }
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "command": _gate_command(
            diff_base,
            mode=plan.mode,
            memory_cap_bytes=plan.memory_cap_bytes,
            systemd_run_available=plan.systemd_run_available,
        ),
        "diffBase": diff_base,
        "mode": plan.mode,
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
        command,
        cwd=cwd,
        env=dict(env),
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def run_strict_code_quality_gate(
    code_worktree: Path,
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
    wrapper = quality_wrapper_path(code_worktree)
    if not wrapper.is_file():
        raise RuntimeError(
            "strict code-quality gate cannot run before code commit: "
            f"project-owned wrapper is missing at {wrapper}"
        )
    plan = plan or QualityGatePlan()
    if plan.mode not in {GATE_TARGETED, GATE_FULL}:
        raise ValueError(f"unknown quality gate mode: {plan.mode}")
    command, invocation, cap_plan = _gate_command_parts(code_worktree, plan, diff_base, invocation)
    result = runner(
        command,
        code_worktree,
        quality_environment(code_worktree, invocation=invocation),
    )
    if result.returncode != 0:
        raise RuntimeError(_gate_failure_message(result, cap_plan))
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "passed": True,
        "command": _gate_command(
            diff_base,
            mode=plan.mode,
            memory_cap_bytes=plan.memory_cap_bytes,
            systemd_run_available=plan.systemd_run_available,
        ),
        "diffBase": diff_base,
        "mode": plan.mode,
        **(
            {
                "memoryCap": {
                    "capBytes": cap_plan.cap_bytes,
                    "policy": cap_plan.policy,
                    "mechanism": cap_plan.mechanism,
                }
            }
            if cap_plan is not None
            else {}
        ),
    }


def _gate_command_parts(
    code_worktree: Path,
    plan: QualityGatePlan,
    diff_base: str,
    invocation: str,
) -> tuple[list[str], str, memory_cap.MemoryCapPlan | None]:
    """The concrete command, its invocation label, and (full runs) the cap plan."""
    python = quality_python(code_worktree)
    module_args = ["-m", QUALITY_MODULE]
    if plan.mode == GATE_TARGETED:
        module_args.append("--targeted")
    if diff_base:
        module_args += ["--diff-base", diff_base]
    if plan.mode != GATE_FULL:
        return [python.as_posix(), *module_args], invocation, None
    if plan.memory_cap_bytes is None:
        raise RuntimeError(
            "full quality gate requires a settings-owned memory cap "
            f"({memory_cap.QUALITY_MEMORY_CAP_POLICY})"
        )
    cap_plan = memory_cap.plan_capped_command(
        python,
        module_args,
        plan.memory_cap_bytes,
        systemd_run_available=plan.systemd_run_available,
    )
    return cap_plan.command, "master-integration", cap_plan


def _gate_failure_message(
    result: subprocess.CompletedProcess[str],
    cap_plan: memory_cap.MemoryCapPlan | None,
) -> str:
    """One refusal message: nothing committed, plus cap policy when a cap ran."""
    details = _failure_output(result.stdout)
    if cap_plan is not None:
        killed = (
            " The scope was killed by the memory cap (exit 137) inside its own scope."
            if result.returncode == 137
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
    code_worktree: Path, *, invocation: str = "closeout-staged"
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
    return env


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
