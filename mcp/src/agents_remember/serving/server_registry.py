"""Small live-process records let installation report servers that need reconnecting."""

from __future__ import annotations

import atexit
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.core import RunningServerPayload
from agents_remember.serving.build_currentness import inspect_build, retain_live_builds
from agents_remember.serving.build_info import ServingBuild, process_serving_build

logger = logging.getLogger(__name__)


class _ServerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pid: int
    role: str
    loadedDigest: str | None
    startedAt: str
    configPath: str
    packageRoot: str | None
    processStart: str | None


class _RecordWarnings:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reported = False

    def report(self, error: OSError) -> None:
        with self._lock:
            if self._reported:
                return
            self._reported = True
        logger.warning("The server's runtime record could not be written: %s", error)


_WARNINGS = _RecordWarnings()


def _directory(config: McpRuntimeConfig) -> Path:
    return config.coordination_root / "runtime" / "servers"


def _process_start(pid: int) -> str | None:
    try:
        # The comm field may contain spaces or parentheses; fields after its final ')'
        # start at process state, and the twentieth of those fields is starttime.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _alive(record: _ServerRecord) -> bool:
    if record.pid <= 0:
        return False
    try:
        os.kill(record.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    actual_start = _process_start(record.pid)
    return (
        record.processStart is None or actual_start is None or record.processStart == actual_start
    )


def register_server(config: McpRuntimeConfig, role: str) -> Callable[[], None]:
    """Record this boot; cleanup is idempotent and also runs at normal process exit."""
    build = process_serving_build()
    record = _ServerRecord(
        pid=os.getpid(),
        role=role,
        loadedDigest=build.source_digest,
        startedAt=build.booted_at,
        configPath=str(config.config_path),
        packageRoot=build.package_root,
        processStart=_process_start(os.getpid()),
    )
    path = _directory(config) / f"{record.pid}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
    except OSError as error:
        _WARNINGS.report(error)

    def cleanup() -> None:
        atexit.unregister(cleanup)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("The server's runtime record could not be removed: %s", path)

    atexit.register(cleanup)
    return cleanup


def _read_record(path: Path) -> _ServerRecord | None:
    try:
        record = _ServerRecord.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None
    if not _alive(record):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("A dead server's runtime record could not be removed: %s", path)
        return None
    return record


def server_inventory(config: McpRuntimeConfig) -> list[RunningServerPayload]:
    """Inspect all live records, removing dead records without writing a new inventory."""
    try:
        paths = sorted(_directory(config).glob("*.json"))
    except OSError as error:
        logger.warning("The runtime server records could not be read: %s", error)
        return []
    servers = []
    live_builds = []
    for path in paths:
        record = _read_record(path)
        if record is None:
            continue
        build = ServingBuild(
            version="",
            commit=None,
            booted_at=record.startedAt,
            source_digest=record.loadedDigest,
            package_root=record.packageRoot,
        )
        live_builds.append(build)
        servers.append(
            RunningServerPayload(
                pid=record.pid,
                role=record.role,
                loadedDigest=record.loadedDigest,
                startedAt=record.startedAt,
                configPath=record.configPath,
                packageRoot=record.packageRoot,
                currentness=inspect_build(build, role=record.role),
            )
        )
    retain_live_builds(live_builds)
    return servers
