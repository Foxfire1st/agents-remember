"""Exact-session hosted readiness derived only from the protocol bridge handshake."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_client import read_control_snapshot
from agents_remember.serving.harness_control_models import AdapterSnapshot
from agents_remember.serving.hosted_control_projection import (
    control_snapshot_entry,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

HostedReadinessStatus = Literal["ready", "not-ready", "unknown-session", "terminated"]
SnapshotReader = Callable[[TerminalCatalogEntry], AdapterSnapshot]
MAX_HOSTED_READINESS_WAIT_SECONDS = 60.0
"""Public bounded-wait ceiling; readiness never installs a watcher or sleeps without a bound."""


class HostedReadinessHost(Protocol):
    def has_session(self, tmux_name: str) -> bool: ...


@dataclass(frozen=True)
class HostedReadinessResult:
    """One truthful protocol observation for exactly one catalog session id."""

    status: HostedReadinessStatus
    session_id: str
    entry: TerminalCatalogEntry | None = None
    detail: str | None = None
    snapshot: AdapterSnapshot | None = None


def hosted_session_readiness(
    catalog: TerminalCatalog,
    host: HostedReadinessHost,
    *,
    session_id: str,
    wait_seconds: float = 0.0,
    snapshot_reader: SnapshotReader = read_control_snapshot,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = 0.1,
    **_diagnostic_only: object,
) -> HostedReadinessResult:
    """Observe one bridge until it is ready or the caller's wait bound expires.

    Pane text, copy mode, log markers, and timing heuristics are intentionally absent from the
    readiness decision. Extra legacy pane-probe keyword arguments remain accepted only so older
    callers fail by result instead of crashing while they migrate; they are never invoked.
    """

    started = monotonic()
    bound = min(MAX_HOSTED_READINESS_WAIT_SECONDS, max(0.0, wait_seconds))
    while True:
        observed = _observe_exact_session(
            catalog,
            host,
            session_id=session_id,
            snapshot_reader=snapshot_reader,
        )
        if observed.status != "not-ready":
            return observed
        remaining = bound - (monotonic() - started)
        if remaining <= 0.0:
            return observed
        with contextlib.suppress(OSError):
            sleep(min(max(0.01, poll_interval), remaining))


def _observe_exact_session(
    catalog: TerminalCatalog,
    host: HostedReadinessHost,
    *,
    session_id: str,
    snapshot_reader: SnapshotReader,
) -> HostedReadinessResult:
    entry = catalog.get(session_id)
    if entry is None:
        return HostedReadinessResult("unknown-session", session_id, detail="no catalog row")
    if entry.status == "terminated":
        return HostedReadinessResult(
            "terminated", session_id, entry=entry, detail=f"catalog status is {entry.status}"
        )
    if entry.status != "running":
        return HostedReadinessResult(
            "not-ready", session_id, entry=entry, detail=f"catalog status is {entry.status}"
        )
    if not host.has_session(entry.tmux_name):
        return HostedReadinessResult(
            "not-ready", session_id, entry=entry, detail="tmux session is not addressable"
        )
    if entry.kind != "harness":
        return HostedReadinessResult(
            "not-ready", session_id, entry=entry, detail="ordinary terminals have no harness bridge"
        )
    if entry.control_endpoint is None:
        legacy = replace(
            entry,
            control_state="unsupported",
            control_activity="unknown",
            control_acceptance="unsupported",
        )
        return HostedReadinessResult(
            "not-ready",
            session_id,
            entry=legacy,
            detail="legacy raw-TUI session is unsupported until restarted through the bridge",
        )
    try:
        snapshot = snapshot_reader(entry)
    except HarnessControlError as exc:
        return HostedReadinessResult(
            "not-ready", session_id, entry=entry, detail=str(exc)
        )

    current = catalog.get(session_id)
    if current is None or hosted_session_identity(current) != hosted_session_identity(entry):
        return HostedReadinessResult(
            "unknown-session", session_id, detail="catalog identity changed during readiness check"
        )
    if current.status == "terminated":
        return HostedReadinessResult(
            "terminated", session_id, entry=current, detail="catalog status is terminated"
        )
    current = control_snapshot_entry(current, snapshot)
    ready = snapshot.control == "ready" and snapshot.acceptance in {"immediate", "queued"}
    if ready:
        return HostedReadinessResult("ready", session_id, entry=current, snapshot=snapshot)
    detail = _snapshot_detail(snapshot)
    return HostedReadinessResult(
        "not-ready", session_id, entry=current, detail=detail, snapshot=snapshot
    )


def hosted_session_identity(entry: TerminalCatalogEntry) -> tuple[str, str, str]:
    """Fields that prove a re-read still describes the exact spawned hosted session."""

    return entry.id, entry.tmux_name, entry.created_at


def _snapshot_detail(snapshot: AdapterSnapshot) -> str:
    raw_detail = snapshot.raw.get("detail") or snapshot.raw.get("bridgeError")
    suffix = f": {raw_detail}" if isinstance(raw_detail, str) and raw_detail else ""
    return (
        f"adapter control={snapshot.control} acceptance={snapshot.acceptance}"
        f" activity={snapshot.activity}{suffix}"
    )
