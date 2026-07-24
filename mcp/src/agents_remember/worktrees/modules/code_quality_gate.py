"""Strict source-quality enforcement for Agents Remember code commits."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

AGENTS_REMEMBER_REPO = "agents-remember"
QUALITY_WRAPPER = Path("mcp/src/agents_remember/code_quality/check.py")
QUALITY_MODULE = "agents_remember.code_quality.check"
FAILURE_OUTPUT_LINES = 40

QualityRunner = Callable[
    [list[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]
]


def requires_strict_code_quality(repo_name: str, *, code_would_commit: bool) -> bool:
    """Whether this closeout would commit the repository that owns the wrapper."""
    return code_would_commit and repo_name == AGENTS_REMEMBER_REPO


def code_quality_gate_preview(repo_name: str, *, code_would_commit: bool) -> dict[str, object]:
    required = requires_strict_code_quality(repo_name, code_would_commit=code_would_commit)
    return {
        "required": required,
        "command": f"python -m {QUALITY_MODULE}" if required else "",
        "reason": (
            "strict project-owned quality wrapper runs before the code commit"
            if required
            else "no Agents Remember code commit would be created"
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
    runner: QualityRunner = run_subprocess,
) -> dict[str, object]:
    """Run this worktree's mandatory quality wrapper or refuse the commit."""
    wrapper = code_worktree / QUALITY_WRAPPER
    if not wrapper.is_file():
        raise RuntimeError(
            "strict code-quality gate cannot run before code commit: "
            f"project-owned wrapper is missing at {wrapper}"
        )
    python = quality_python(code_worktree)
    command = [python.as_posix(), "-m", QUALITY_MODULE]
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
        "passed": True,
        "command": f"python -m {QUALITY_MODULE}",
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
    """Put the current worktree package first even when Python comes from another checkout."""
    env = dict(os.environ)
    entries = [(code_worktree / "mcp" / "src").as_posix()]
    existing = env.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def _git_common_dir(code_worktree: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=code_worktree,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def _failure_output(output: str) -> str:
    lines = output.strip().splitlines()
    if not lines:
        return ""
    return "\nQuality output tail:\n" + "\n".join(lines[-FAILURE_OUTPUT_LINES:])
