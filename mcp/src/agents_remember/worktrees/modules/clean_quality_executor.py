"""Exact staged-candidate execution through the pinned Dagger quality module."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from agents_remember.kernel.atomic_write import (
    atomic_write_bytes,
    atomic_write_text,
)
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
DAGGER_PROGRESS_MAX_BYTES = 512 * 1024
DAGGER_RESULT_MAX_BYTES = 128 * 1024
DAGGER_STREAM_CHUNK_BYTES = 64 * 1024
DAGGER_PROGRESS_TRUNCATION = "[older Dagger output truncated]\n"
REPORT_GENERATIONS_DIRECTORY = ".quality-report-generations"
REPORT_SET_MANIFEST = "quality-report-set.json"
EXPORTED_REPORT_NAMES = frozenset(
    {
        "clean-quality-results.json",
        "codex-probe.json",
        "coverage.data",
        "coverage.json",
        "pytest-events.jsonl",
        "quality-progress.json",
    }
)

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
        source,
        env,
    )
    if exported.returncode != 0:
        _write_current(
            request.worktree_group, "failed", "Dagger pipeline/export failed", status="failed"
        )
        return exported
    pipeline_exit = _exported_pipeline_exit(export_root)
    _publish_reports(export_root, request.worktree_group / "reports")
    outcome = "passed" if pipeline_exit == 0 else "failed"
    _write_current(
        request.worktree_group,
        "complete" if pipeline_exit == 0 else "failed",
        f"Dagger Ubuntu quality {outcome}",
        status="completed" if pipeline_exit == 0 else "failed",
    )
    return subprocess.CompletedProcess(
        exported.args,
        pipeline_exit,
        stdout=_bounded_text_tail(exported.stdout, max_bytes=DAGGER_RESULT_MAX_BYTES),
        stderr=exported.stderr,
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


def _exported_pipeline_exit(export_root: Path) -> int:
    report = export_root / "clean-quality-results.json"
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
        exit_code = payload["exitCode"]
        status = payload["status"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(f"Dagger exported no valid authoritative result: {report}") from error
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
        raise RuntimeError(f"Dagger exported an invalid pipeline exit code: {exit_code!r}")
    expected_status = "passed" if exit_code == 0 else "failed"
    if status != expected_status:
        raise RuntimeError(
            f"Dagger exported a contradictory result: status={status!r}, exitCode={exit_code}"
        )
    return exit_code


def _publish_reports(source: Path, destination: Path) -> dict[str, object]:
    """Publish one immutable evidence generation, then atomically point readers at it.

    Individual report renames cannot make a multi-file result atomic. The generation
    directory is complete and hash-validated before the one authoritative pointer moves;
    a crash therefore leaves readers on either the previous complete generation or this
    complete one, never a mixture of both.
    """
    if not source.is_dir():
        raise RuntimeError(f"Dagger did not export its reports directory: {source}")
    exported_names = {report.name for report in source.iterdir() if report.is_file()}
    unexpected = exported_names - EXPORTED_REPORT_NAMES
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise RuntimeError(f"Dagger exported unexpected report files: {names}")
    destination.mkdir(parents=True, exist_ok=True)
    files = {
        name: {
            "sha256": hashlib.sha256((source / name).read_bytes()).hexdigest(),
            "size": (source / name).stat().st_size,
        }
        for name in sorted(exported_names)
    }
    generation = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    generations = destination / REPORT_GENERATIONS_DIRECTORY
    generations.mkdir(parents=True, exist_ok=True)
    generation_root = generations / generation
    if not generation_root.is_dir():
        staged = Path(tempfile.mkdtemp(prefix=f".{generation}.", dir=generations))
        try:
            for name in sorted(exported_names):
                shutil.copyfile(source / name, staged / name)
            _validate_generation(staged, files)
            try:
                staged.rename(generation_root)
            except FileExistsError:
                shutil.rmtree(staged)
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            raise
    else:
        _validate_generation(generation_root, files)
    manifest: dict[str, object] = {
        "schemaVersion": "1.0",
        "generation": generation,
        "files": files,
    }
    atomic_write_text(
        destination / REPORT_SET_MANIFEST,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    # The former top-level copies were not a coherent publication surface. Remove
    # them only after the pointer commits; live quality-progress.json may be created
    # again by _write_current, but durable result readers use the manifest helper.
    for legacy_name in EXPORTED_REPORT_NAMES:
        (destination / legacy_name).unlink(missing_ok=True)
    _prune_report_generations(generations, generation)
    return manifest


def published_report_path(destination: Path, name: str) -> Path:
    """Resolve and verify one report from the currently published generation."""
    try:
        manifest = json.loads((destination / REPORT_SET_MANIFEST).read_text(encoding="utf-8"))
        generation = manifest["generation"]
        file_record = manifest["files"][name]
        expected_hash = file_record["sha256"]
        expected_size = file_record["size"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("no complete Dagger report generation is published") from error
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{64}", generation):
        raise RuntimeError("published Dagger report generation id is invalid")
    report = destination / REPORT_GENERATIONS_DIRECTORY / generation / name
    try:
        payload = report.read_bytes()
    except OSError as error:
        raise RuntimeError(f"published Dagger report is incomplete: {name}") from error
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or len(payload) != expected_size
        or not isinstance(expected_hash, str)
        or hashlib.sha256(payload).hexdigest() != expected_hash
    ):
        raise RuntimeError(f"published Dagger report failed generation verification: {name}")
    return report


def _validate_generation(root: Path, files: dict[str, dict[str, object]]) -> None:
    for name, record in files.items():
        payload = (root / name).read_bytes()
        if (
            len(payload) != record["size"]
            or hashlib.sha256(payload).hexdigest() != record["sha256"]
        ):
            raise RuntimeError(f"Dagger report generation copy failed verification: {name}")


def _prune_report_generations(generations: Path, current: str) -> None:
    completed = sorted(
        (
            path
            for path in generations.iterdir()
            if path.is_dir() and re.fullmatch(r"[0-9a-f]{64}", path.name)
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    keep = {current, *(path.name for path in completed[:2])}
    for path in completed:
        if path.name not in keep:
            shutil.rmtree(path)


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
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    prior = _bounded_file_tail(progress_path, max_bytes=DAGGER_PROGRESS_MAX_BYTES)
    progress_tail = bytearray(prior)
    result_tail = bytearray()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(env),
        text=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    live = progress_path.open("ab")
    live_size = len(prior)
    truncated = False
    try:
        while chunk := process.stdout.read(DAGGER_STREAM_CHUNK_BYTES):
            if isinstance(chunk, str):  # test doubles must still obey the same byte cap
                chunk = chunk.encode("utf-8", errors="replace")
            _append_bounded_bytes(result_tail, chunk, max_bytes=DAGGER_RESULT_MAX_BYTES)
            before = len(progress_tail)
            _append_bounded_bytes(progress_tail, chunk, max_bytes=DAGGER_PROGRESS_MAX_BYTES)
            truncated = truncated or before + len(chunk) > DAGGER_PROGRESS_MAX_BYTES
            if live_size < DAGGER_PROGRESS_MAX_BYTES:
                writable = chunk[: DAGGER_PROGRESS_MAX_BYTES - live_size]
                _write_live_progress(live, writable)
                live_size += len(writable)
            detail = _chunk_detail(chunk)
            if detail:
                atomic_write_text(
                    progress_path.parent / "quality-progress.json",
                    json.dumps(
                        {"status": "running", "step": "dagger", "detail": detail[:500]},
                        indent=2,
                    )
                    + "\n",
                )
    finally:
        live.close()
    marker = DAGGER_PROGRESS_TRUNCATION.encode("utf-8") if truncated else b""
    retained = _utf8_tail_bytes(
        bytes(progress_tail),
        max_bytes=max(DAGGER_PROGRESS_MAX_BYTES - len(marker), 0),
    )
    atomic_write_bytes(progress_path, marker + retained)
    stdout = _utf8_tail_bytes(bytes(result_tail), max_bytes=DAGGER_RESULT_MAX_BYTES).decode(
        "utf-8", errors="replace"
    )
    return subprocess.CompletedProcess(command, process.wait(), stdout=stdout, stderr="")


def _write_live_progress(live: BinaryIO, chunk: bytes) -> None:
    """Append one already-bounded live chunk without any retained-tail rewrite."""
    live.write(chunk)
    live.flush()


def _append_bounded_bytes(buffer: bytearray, chunk: bytes, *, max_bytes: int) -> None:
    buffer.extend(chunk)
    if len(buffer) > max_bytes:
        del buffer[:-max_bytes]


def _bounded_text_tail(value: str, *, max_bytes: int) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value
    return _utf8_tail_bytes(encoded, max_bytes=max_bytes).decode("utf-8")


def _bounded_file_tail(path: Path, *, max_bytes: int) -> bytes:
    try:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            source.seek(max(size - max_bytes, 0))
            return _utf8_tail_bytes(source.read(max_bytes), max_bytes=max_bytes)
    except OSError:
        return b""


def _chunk_detail(chunk: bytes) -> str:
    bounded = chunk[-4096:].decode("utf-8", errors="replace")
    lines = [line for line in bounded.splitlines() if line.strip()]
    if not lines:
        return ""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", lines[-1]).strip()[:500]


def _utf8_tail_bytes(value: bytes, *, max_bytes: int) -> bytes:
    if len(value) <= max_bytes:
        return value
    return value[-max_bytes:].decode("utf-8", errors="ignore").encode("utf-8")


def _resolve_dagger(env: Mapping[str, str]) -> str:
    return native_command(["dagger"], env)[0]
