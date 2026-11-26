"""Live MCP server binding and process-ancestry records (L51-R1).

Each MCP server writes a small per-PID record under
``<coordination-root>/runtime/server-bindings/<pid>.json`` at startup and
removes it on exit.  Keeping these records separate from
``runtime/servers/<pid>.json`` preserves older server-inventory readers and
single-file glob assertions on ``runtime/servers/``.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

logger = logging.getLogger(__name__)

BINDING_ANCESTOR_LIMIT = 8
PROC_ROOT = Path("/proc")
_STAT_PPID_INDEX = 1
_STAT_STARTTIME_INDEX = 19
_MCP_SERVER_CMDLINE_MARKER = b"agents_remember.mcp"


class ProcessAncestor(BaseModel):
    """One ancestor PID and its Linux ``/proc/<pid>/stat`` start time when readable."""

    model_config = ConfigDict(extra="forbid")

    pid: int
    processStart: str | None = None


class BoundIdentityPayload(BaseModel):
    """The agent-binding identity fields recorded for a bound MCP server."""

    model_config = ConfigDict(extra="forbid")

    agentId: str
    requestId: str
    role: str
    sprintDocumentRef: dict[str, str] | None = None
    masterDocumentRef: dict[str, str] | None = None
    taskDocumentRef: dict[str, str] | None = None


class ServerBindingRecord(BaseModel):
    """Per-PID runtime record describing a live MCP server's binding and ancestry."""

    model_config = ConfigDict(extra="forbid")

    pid: int
    processStart: str | None
    startedAt: str
    configPath: str
    parentPid: int
    parentProcessStart: str | None
    ancestors: list[ProcessAncestor]
    bindingState: Literal["bound", "unbound", "unreadable"]
    binding: BoundIdentityPayload | None = None
    unreadableReason: str | None = None


class _BindingWarnings:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reported = False

    def report(self, error: OSError) -> None:
        with self._lock:
            if self._reported:
                return
            self._reported = True
        logger.warning("The server binding record could not be written: %s", error)


_WARNINGS = _BindingWarnings()


def bindings_directory(config: McpRuntimeConfig) -> Path:
    """Return the runtime directory holding per-PID server binding records."""
    return config.coordination_root / "runtime" / "server-bindings"


def proc_available() -> bool:
    """Return whether ``PROC_ROOT`` is readable as a procfs directory."""
    return PROC_ROOT.is_dir()


def _read_stat_fields(pid: int) -> list[str] | None:
    try:
        raw = (PROC_ROOT / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # The comm field may contain spaces or parentheses; fields after the final ')'
    # start at process state (index 0), with ppid at index 1 and starttime at index 19.
    parts = raw.rsplit(")", 1)
    if len(parts) != 2:
        return None
    return parts[1].split()


def process_start(pid: int) -> str | None:
    """Read ``starttime`` from ``PROC_ROOT/<pid>/stat``, or ``None`` if unavailable."""
    fields = _read_stat_fields(pid)
    if fields is None or len(fields) <= _STAT_STARTTIME_INDEX:
        return None
    return fields[_STAT_STARTTIME_INDEX]


def _parent_pid(pid: int) -> int | None:
    fields = _read_stat_fields(pid)
    if fields is None or len(fields) <= _STAT_PPID_INDEX:
        return None
    try:
        return int(fields[_STAT_PPID_INDEX])
    except ValueError:
        return None


def process_ancestors(pid: int, *, limit: int = BINDING_ANCESTOR_LIMIT) -> list[ProcessAncestor]:
    """Walk upward from ``pid``'s parent through procfs, capped at ``limit`` entries."""
    if limit <= 0:
        return []
    has_proc = proc_available()
    current = _parent_pid(pid) if has_proc else None
    if current is None and pid == os.getpid():
        current = os.getppid()
    ancestors: list[ProcessAncestor] = []
    seen: set[int] = {pid}
    while current is not None and current > 1 and current not in seen and len(ancestors) < limit:
        seen.add(current)
        ancestors.append(ProcessAncestor(pid=current, processStart=process_start(current)))
        current = _parent_pid(current) if has_proc else None
    return ancestors


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def register_server_binding(
    config: McpRuntimeConfig,
    *,
    binding_state: Literal["bound", "unbound", "unreadable"],
    binding: BoundIdentityPayload | None = None,
    unreadable_reason: str | None = None,
) -> Callable[[], None]:
    """Write this server's binding record and return an idempotent exit cleanup callback."""
    pid = os.getpid()
    parent_pid = os.getppid()
    record = ServerBindingRecord(
        pid=pid,
        processStart=process_start(pid),
        startedAt=_now_iso(),
        configPath=str(config.config_path),
        parentPid=parent_pid,
        parentProcessStart=process_start(parent_pid),
        ancestors=process_ancestors(pid),
        bindingState=binding_state,
        binding=binding,
        unreadableReason=unreadable_reason,
    )
    path = bindings_directory(config) / f"{record.pid}.json"
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
            logger.warning("The server binding record could not be removed: %s", path)

    atexit.register(cleanup)
    return cleanup


def _alive(record: ServerBindingRecord) -> bool:
    if record.pid <= 0:
        return False
    try:
        os.kill(record.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    actual_start = process_start(record.pid)
    return (
        record.processStart is None or actual_start is None or record.processStart == actual_start
    )


def _read_binding_record(path: Path) -> ServerBindingRecord | None:
    try:
        record = ServerBindingRecord.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None
    if not _alive(record):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("A dead server binding record could not be removed: %s", path)
        return None
    return record


def live_server_bindings(config: McpRuntimeConfig) -> list[ServerBindingRecord]:
    """Return all live server binding records, reclaiming dead or recycled-PID files."""
    try:
        paths = sorted(bindings_directory(config).glob("*.json"))
    except OSError as error:
        logger.warning("The runtime server binding records could not be read: %s", error)
        return []
    records: list[ServerBindingRecord] = []
    for path in paths:
        record = _read_binding_record(path)
        if record is not None:
            records.append(record)
    return records


def _has_matching_harness_env(entry: Path, target_env: bytes) -> bool:
    try:
        raw_env = (entry / "environ").read_bytes()
    except OSError:
        return False
    return target_env in raw_env.split(b"\x00")


def _is_mcp_server_process(entry: Path) -> bool | None:
    try:
        raw_cmdline = (entry / "cmdline").read_bytes()
    except OSError:
        return None
    return _MCP_SERVER_CMDLINE_MARKER in raw_cmdline


def harness_root_pids(agent_id: str) -> list[ProcessAncestor] | None:
    """Return root harness process IDs carrying ``PASEO_AGENT_ID=<agent_id>``, or ``None`` without procfs."""
    if not proc_available():
        return None
    if not agent_id:
        return []
    try:
        entries = list(PROC_ROOT.iterdir())
    except OSError:
        return None
    target_env = f"PASEO_AGENT_ID={agent_id}".encode()
    matching: dict[int, int | None] = {}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        if not _has_matching_harness_env(entry, target_env):
            continue
        is_mcp = _is_mcp_server_process(entry)
        if is_mcp is None or is_mcp:
            continue
        pid = int(entry.name)
        matching[pid] = _parent_pid(pid)
    roots = sorted(pid for pid, ppid in matching.items() if ppid not in matching)
    return [ProcessAncestor(pid=pid, processStart=process_start(pid)) for pid in roots]
