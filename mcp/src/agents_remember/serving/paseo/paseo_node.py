"""Fetch, verify and atomically place the release-owned Node; never use the shell's Node."""

from __future__ import annotations

import hashlib
import http.client
import shutil
import tarfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from agents_remember.errors import LockCapabilityError, PaseoRuntimeFailure
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, exclusive_file_lock
from agents_remember.kernel.primitives.paseo_node_paths import NodeRuntime, product_node
from agents_remember.serving.paseo.paseo_command import (
    CommandRunner,
    run_command,
)

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
FETCH_SECONDS = 300
ACQUIRE_SECONDS = 30
Fetch = Callable[[str, Path], None]


def node_executable_valid(runtime: NodeRuntime, runner: CommandRunner = run_command) -> bool:
    if not runtime.node.is_file():
        return False
    version = runner([runtime.node.as_posix(), "--version"], 10)
    return version.returncode == 0 and version.stdout.strip() == f"v{runtime.version}"


def node_status(executable: str | None) -> dict:
    """Compare an observed host executable with the build's Node without acquiring it."""
    runtime = product_node()
    return {
        "node": runtime.payload(),
        "nodeExecutable": executable,
        "restartRequired": ["node"]
        if executable is not None and executable != runtime.node.as_posix()
        else [],
    }


def node_valid(runtime: NodeRuntime, runner: CommandRunner = run_command) -> bool:
    if not runtime.npm.is_file() or not node_executable_valid(runtime, runner):
        return False
    npm = runner([runtime.node.as_posix(), runtime.npm.as_posix(), "--version"], 10)
    return npm.returncode == 0 and bool(npm.stdout.strip())


def fetch_archive(url: str, target: Path) -> None:
    """One bounded download; the caller removes the partial archive on every failure."""
    deadline = time.monotonic() + FETCH_SECONDS
    received = 0
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            received += len(chunk)
            if received > MAX_ARCHIVE_BYTES or time.monotonic() > deadline:
                raise OSError("Node archive exceeded the 128 MiB / 300 second download limit")
            output.write(chunk)


def ensure_node(runner: CommandRunner = run_command, fetch: Fetch = fetch_archive) -> dict:
    runtime = product_node()
    try:
        if runtime.root.exists():
            existing = _existing(runtime, runner)
            if not any(path.exists() for path in _temporaries(runtime)):
                return existing
        with exclusive_file_lock(
            runtime.root, "product Node", deadline=time.monotonic() + ACQUIRE_SECONDS
        ):
            if runtime.root.exists():
                existing = _existing(runtime, runner)
                return {**existing, "removed": _reclaim_temporaries(runtime)}
            return _place(runtime, runner, fetch)
    except LockAcquisitionTimeout as error:
        raise _failure(runtime, "node_operation_busy", str(error)) from error
    except (OSError, LockCapabilityError) as error:
        raise _failure(runtime, "node_install_failed", str(error)) from error


def _existing(runtime: NodeRuntime, runner: CommandRunner) -> dict:
    if not node_valid(runtime, runner):
        raise _failure(
            runtime,
            "node_target_invalid",
            "the existing folder is not the pinned Node and npm; "
            "it was not overwritten; move it aside before retrying runtime_install",
        )
    return {**runtime.payload(), "changed": False}


def _place(runtime: NodeRuntime, runner: CommandRunner, fetch: Fetch) -> dict:
    runtime.cache.mkdir(parents=True, exist_ok=True)
    archive, staging = _temporaries(runtime)
    _reclaim_temporaries(runtime)
    try:
        fetch(runtime.url, archive)
        with archive.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != runtime.sha256:
            raise _failure(
                runtime,
                "node_digest_mismatch",
                f"expected SHA-256 {runtime.sha256}, got {actual}; archive removed",
            )
        staging.mkdir()
        with tarfile.open(archive, "r:xz") as source:
            source.extractall(staging, filter="data")
        extracted = staging / runtime.root.name
        if not node_valid(replace(runtime, root=extracted), runner):
            raise _failure(
                runtime,
                "node_archive_invalid",
                "the verified archive did not provide the pinned Node and npm",
            )
        extracted.rename(runtime.root)
    except (OSError, tarfile.TarError, http.client.IncompleteRead) as error:
        raise _failure(runtime, "node_install_failed", str(error)) from error
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    return {**runtime.payload(), "changed": True}


def _temporaries(runtime: NodeRuntime) -> tuple[Path, Path]:
    return (
        runtime.cache / f"{runtime.root.name}.tar.xz.partial",
        runtime.root.with_name(f".{runtime.root.name}.unpack"),
    )


def _reclaim_temporaries(runtime: NodeRuntime) -> list[str]:
    archive, staging = _temporaries(runtime)
    removed = []
    if archive.exists() or archive.is_symlink():
        archive.unlink()
        removed.append(archive.as_posix())
    if staging.exists() or staging.is_symlink():
        staging.unlink() if staging.is_symlink() else shutil.rmtree(staging)
        removed.append(staging.as_posix())
    return removed


def _failure(runtime: NodeRuntime, code: str, cause: str) -> PaseoRuntimeFailure:
    return PaseoRuntimeFailure(
        code,
        "node",
        f"Node {runtime.version} ({runtime.platform}) at "
        f"{runtime.root}, from {runtime.url}: {cause}",
    )
