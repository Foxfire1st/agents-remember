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


@dataclass(frozen=True)
class ReadinessWait:
    """How long to keep observing a bridge, how often, and by whose clock.

    A bound without a poll interval never re-observes and a poll interval without a bound never
    stops, so the four are one decision -- and the clock/sleep pair must be the same one the bound
    is measured in, which separate parameters cannot guarantee.
    """

    seconds: float = 0.0
    poll_interval: float = 0.1
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


NO_READINESS_WAIT = ReadinessWait()
"""Observe exactly once and answer with what is true right now."""


def hosted_session_readiness(
    catalog: TerminalCatalog,
    host: HostedReadinessHost,
    *,
    session_id: str,
    snapshot_reader: SnapshotReader = read_control_snapshot,
    wait: ReadinessWait = NO_READINESS_WAIT,
    **_diagnostic_only: object,
) -> HostedReadinessResult:
    """Observe one bridge until it is ready or the caller's wait bound expires.

    Pane text, copy mode, log markers, and timing heuristics are intentionally absent from the
    readiness decision. Extra legacy pane-probe keyword arguments remain accepted only so older
    callers fail by result instead of crashing while they migrate; they are never invoked.
    """

    started = wait.monotonic()
    bound = min(MAX_HOSTED_READINESS_WAIT_SECONDS, max(0.0, wait.seconds))
    while True:
        observed = _observe_exact_session(
            catalog,
            host,
            session_id=session_id,
            snapshot_reader=snapshot_reader,
        )
        if observed.status != "not-ready":
            return observed
        remaining = bound - (wait.monotonic() - started)
        if remaining <= 0.0:
            return observed
        with contextlib.suppress(OSError):
            wait.sleep(min(max(0.01, wait.poll_interval), remaining))


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
    unreachable = _bridge_unreachable(entry, host, session_id=session_id)
    if unreachable is not None:
        return unreachable
    try:
        snapshot = snapshot_reader(entry)
    except HarnessControlError as exc:
        return HostedReadinessResult("not-ready", session_id, entry=entry, detail=str(exc))
    return _readiness_from_snapshot(
        catalog, session_id=session_id, observed=entry, snapshot=snapshot
    )


def _bridge_unreachable(
    entry: TerminalCatalogEntry,
    host: HostedReadinessHost,
    *,
    session_id: str,
) -> HostedReadinessResult | None:
    """The result to report when this row has no bridge to read, or ``None`` to go read it.

    Every branch here is settled from the catalog row and the tmux host alone -- no bridge call is
    made until all of them pass, so an unaddressable, non-harness or legacy raw-TUI session never
    reaches the protocol read.
    """

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
    return None


def _readiness_from_snapshot(
    catalog: TerminalCatalog,
    *,
    session_id: str,
    observed: TerminalCatalogEntry,
    snapshot: AdapterSnapshot,
) -> HostedReadinessResult:
    """Re-read the catalog after the bridge call and project the snapshot onto the current row.

    The snapshot read is not instantaneous, so the row it described may have been replaced or
    terminated meanwhile. A changed identity is reported as ``unknown-session`` rather than being
    silently attributed to whatever now holds that id.
    """

    current = catalog.get(session_id)
    if current is None or hosted_session_identity(current) != hosted_session_identity(observed):
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
    return HostedReadinessResult(
        "not-ready",
        session_id,
        entry=current,
        detail=_snapshot_detail(snapshot),
        snapshot=snapshot,
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
