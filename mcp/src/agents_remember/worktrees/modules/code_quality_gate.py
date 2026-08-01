"""Strict source-quality enforcement for Agents Remember code commits."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from agents_remember.kernel.git_command import git_environment, run_git

QUALITY_WRAPPER = Path("mcp/src/agents_remember/code_quality/check.py")
QUALITY_MODULE = "agents_remember.code_quality.check"
FAILURE_OUTPUT_LINES = 40

GATE_ENFORCED = "enforced"
GATE_NO_CODE_COMMIT = "no-code-commit"
GATE_WRAPPER_UNAVAILABLE = "wrapper-unavailable"

QualityRunner = Callable[[list[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]


def quality_wrapper_path(code_worktree: Path) -> Path:
    """Where a checkout carries the project-owned quality wrapper."""
    return code_worktree / QUALITY_WRAPPER


def _gate_command(diff_base: str) -> str:
    """The command as reported, so a payload reader can rerun exactly what ran."""
    base = f" --diff-base {diff_base}" if diff_base else ""
    return f"python -m {QUALITY_MODULE}{base}"


def requires_strict_code_quality(code_worktree: Path, *, code_would_commit: bool) -> bool:
    """Whether this closeout must run the wrapper the checkout carries.

    Availability of the wrapper decides this, not the repository's name: the gate
    is documented as mandatory for every repository, so it applies wherever it can
    run at all.
    """
    return code_would_commit and quality_wrapper_path(code_worktree).is_file()


def code_quality_gate_preview(
    code_worktree: Path, *, code_would_commit: bool, diff_base: str = ""
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
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "command": _gate_command(diff_base),
        "diffBase": diff_base,
        "reason": (
            "closeout stages the whole task worktree so the gate's scope is the commit's "
            "content, then runs the strict project-owned quality wrapper over exactly that "
            "before the code commit"
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
    runner: QualityRunner = run_subprocess,
) -> dict[str, object]:
    """Run this worktree's mandatory quality wrapper or refuse the commit.

    ``diff_base`` must be the leaf's recorded base commit. Without it the wrapper
    falls back to ``origin/HEAD``/``main``, and the per-diff coverage floor then
    demands 100% branch coverage of every change on the whole integration branch
    rather than of this leaf's own diff -- which no leaf can pass, so the gate
    would be as useless as one that cannot fail. CI keeps the ``main`` default
    because a pull request genuinely is measured against ``main``; a leaf closeout
    is measured against the leaf.

    The wrapper derives its scope from the index, so what is staged when this runs is what
    gets certified. This function certifies the index it is handed and says nothing about
    how it came to look that way: ``runner`` is a public parameter and closeout is not the
    only caller this signature admits, so the failure message below states only what is
    true of every caller -- that nothing was committed. It deliberately does not say the
    staging was undone, because closeout does not undo it.
    """
    wrapper = quality_wrapper_path(code_worktree)
    if not wrapper.is_file():
        raise RuntimeError(
            "strict code-quality gate cannot run before code commit: "
            f"project-owned wrapper is missing at {wrapper}"
        )
    python = quality_python(code_worktree)
    command = [python.as_posix(), "-m", QUALITY_MODULE]
    if diff_base:
        command += ["--diff-base", diff_base]
    result = runner(command, code_worktree, quality_environment(code_worktree))
    if result.returncode != 0:
        details = _failure_output(result.stdout)
        raise RuntimeError(
            "strict code-quality gate failed before code commit"
            f" with exit code {result.returncode}; code, memory, and ledger remain uncommitted."
            f"{details}"
        )
    return {
        "required": True,
        "status": GATE_ENFORCED,
        "passed": True,
        "command": _gate_command(diff_base),
        "diffBase": diff_base,
    }


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


def quality_environment(code_worktree: Path) -> dict[str, str]:
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
