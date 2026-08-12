"""Exact staged-candidate execution through the pinned Dagger quality module."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.platform_subprocess import (
    native_command,
    native_path_environment,
    windows_interop_reason,
)

DAGGER_VERSION = "v0.21.8"
CODEX_VERSION = "0.147.0"
PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright:v1.60.0-noble@"
    "sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9"
)
CLEAN_SANDBOX_NAME = "test-sandbox"

CommandRunner = Callable[[list[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]
DaggerResolver = Callable[[Mapping[str, str]], str]


@dataclass(frozen=True)
class CleanQualityRequest:
    code_worktree: Path
    worktree_group: Path
    mode: str
    diff_base: str
    memory_cap_bytes: int | None = None


def clean_sandbox_root(worktree_group: Path) -> Path:
    return worktree_group / "reports" / CLEAN_SANDBOX_NAME


def run_clean_quality(
    request: CleanQualityRequest,
    *,
    runner: CommandRunner | None = None,
    dagger_resolver: DaggerResolver | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the canonical Dagger pipeline; never fall back to host quality rails."""
    if request.mode not in {"targeted", "full"}:
        raise ValueError(f"unknown clean quality mode: {request.mode}")
    for path in (request.code_worktree, request.worktree_group):
        if reason := windows_interop_reason(path):
            raise RuntimeError(f"clean quality refuses {path}: {reason}")
    sandbox = _prepare_sandbox(request)
    source = sandbox / "source"
    bundle = sandbox / "candidate.bundle"
    export_root = sandbox / "export"
    env = native_path_environment(os.environ)
    dagger = (dagger_resolver or _resolve_dagger)(env)
    execute = runner or (
        lambda command, cwd, command_env: _stream_dagger(
            command,
            cwd,
            command_env,
            progress_path=request.worktree_group / "reports" / "dagger-progress.log",
        )
    )
    atomic_write_text(request.worktree_group / "reports" / "dagger-progress.log", "")
    common = [
        dagger,
        "--progress=plain",
        "call",
        "quality",
        f"--source={source.as_posix()}",
        f"--repository-bundle={bundle.as_posix()}",
        f"--mode={request.mode}",
    ]
    if request.diff_base:
        common.append(f"--diff-base={request.diff_base}")
    if request.memory_cap_bytes is not None:
        common.append(f"--memory-cap-bytes={request.memory_cap_bytes}")
    _write_current(request.worktree_group, "dagger", "start pinned Ubuntu quality pipeline")
    exported = execute(
        [*common, "reports", "export", f"--path={export_root.as_posix()}"],
        request.code_worktree,
        env,
    )
    if exported.returncode != 0:
        _write_current(
            request.worktree_group, "failed", "Dagger pipeline/export failed", status="failed"
        )
        return exported
    _publish_reports(export_root, request.worktree_group / "reports")
    status = execute([*common, "exit-code"], request.code_worktree, env)
    try:
        pipeline_exit = int(status.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as error:
        raise RuntimeError(
            f"Dagger quality returned no valid exit code: {status.stdout!r}"
        ) from error
    outcome = "passed" if pipeline_exit == 0 else "failed"
    _write_current(
        request.worktree_group,
        "complete" if pipeline_exit == 0 else "failed",
        f"Dagger Ubuntu quality {outcome}",
        status="completed" if pipeline_exit == 0 else "failed",
    )
    return subprocess.CompletedProcess(
        status.args,
        pipeline_exit,
        stdout=exported.stdout + status.stdout,
        stderr=status.stderr,
    )


def _prepare_sandbox(request: CleanQualityRequest) -> Path:
    sandbox = clean_sandbox_root(request.worktree_group)
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    source = sandbox / "source"
    _git_ok(
        run_git(
            source,
            [
                "clone",
                "--no-local",
                "--no-checkout",
                request.code_worktree.as_posix(),
                source.as_posix(),
            ],
            work_dir=sandbox,
        ),
        "clone exact candidate",
    )
    head = _git_ok(run_git(request.code_worktree, ["rev-parse", "HEAD"]), "resolve HEAD")
    _git_ok(run_git(source, ["checkout", "--detach", head]), "checkout exact HEAD")
    staged = _git_ok(
        run_git(request.code_worktree, ["diff", "--cached", "--binary", "HEAD"]),
        "read staged overlay",
        preserve_output=True,
    )
    if staged:
        _git_ok(run_git(source, ["apply", "--index", "-"], input_text=staged), "apply overlay")
    bundle = sandbox / "candidate.bundle"
    _git_ok(
        run_git(source, ["bundle", "create", bundle.as_posix(), "HEAD"]),
        "bundle candidate ancestry",
    )
    manifest = {
        "head": head,
        "stagedOverlaySha256": hashlib.sha256(staged.encode("utf-8")).hexdigest(),
        "source": request.code_worktree.as_posix(),
        "daggerVersion": DAGGER_VERSION,
        "image": PLAYWRIGHT_IMAGE,
        "codexVersion": CODEX_VERSION,
        "bundleSha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
    }
    atomic_write_text(sandbox / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return sandbox


def _publish_reports(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Dagger did not export its reports directory: {source}")
    for report in source.iterdir():
        if report.is_file():
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / report.name
            temporary = target.with_name(f".{target.name}.tmp")
            shutil.copyfile(report, temporary)
            os.replace(temporary, target)


def _git_ok(
    result: subprocess.CompletedProcess[str], action: str, *, preserve_output: bool = False
) -> str:
    if result.returncode != 0:
        raise RuntimeError(f"clean quality could not {action}: {result.stderr.strip()}")
    return result.stdout if preserve_output else result.stdout.strip()


def _write_current(
    worktree_group: Path, step: str, detail: str, *, status: str = "running"
) -> None:
    atomic_write_text(
        worktree_group / "reports" / "quality-progress.json",
        json.dumps({"status": status, "step": step, "detail": detail}, indent=2) + "\n",
    )


def _stream_dagger(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str],
    *,
    progress_path: Path,
) -> subprocess.CompletedProcess[str]:
    lines = progress_path.read_text(encoding="utf-8").splitlines(keepends=True)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(env),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    for line in process.stdout:
        lines.append(line)
        atomic_write_text(progress_path, "".join(lines))
        detail = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
        if detail:
            atomic_write_text(
                progress_path.parent / "quality-progress.json",
                json.dumps(
                    {"status": "running", "step": "dagger", "detail": detail[:500]},
                    indent=2,
                )
                + "\n",
            )
    return subprocess.CompletedProcess(command, process.wait(), stdout="".join(lines), stderr="")


def _resolve_dagger(env: Mapping[str, str]) -> str:
    return native_command(["dagger"], env)[0]
