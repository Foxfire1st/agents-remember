"""Exact-session hosted harness readiness without terminal input side effects."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from agents_remember.serving.terminal import pane_in_mode
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import capture_pane
from agents_remember.serving.turn_state import boot_ready

HostedReadinessStatus = Literal["ready", "not-ready", "unknown-session", "terminated"]
PaneCapturer = Callable[[str], str]
PaneModeProbe = Callable[[str], bool | None]
MAX_HOSTED_READINESS_WAIT_SECONDS = 60.0
"""Public bounded-wait ceiling; readiness never installs a watcher or sleeps without a bound."""


class HostedReadinessHost(Protocol):
    def has_session(self, tmux_name: str) -> bool: ...


@dataclass(frozen=True)
class HostedReadinessResult:
    """One truthful readiness observation for exactly one catalog session id."""

    status: HostedReadinessStatus
    session_id: str
    entry: TerminalCatalogEntry | None = None
    detail: str | None = None


def hosted_session_readiness(
    catalog: TerminalCatalog,
    host: HostedReadinessHost,
    *,
    session_id: str,
    wait_seconds: float = 0.0,
    pane_capturer: PaneCapturer = capture_pane,
    pane_mode_probe: PaneModeProbe = pane_in_mode,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = 0.1,
) -> HostedReadinessResult:
    """Observe the exact row/pane until ready or the caller's wait bound expires.

    This function never sends input. Readiness requires the same catalog id to remain running,
    its tmux session to remain addressable, its own captured pane to satisfy the harness marker
    table, and that exact pane to be outside tmux copy mode.
    """

    started = monotonic()
    bound = min(MAX_HOSTED_READINESS_WAIT_SECONDS, max(0.0, wait_seconds))
    while True:
        observed = _observe_exact_session(
            catalog,
            host,
            session_id=session_id,
            pane_capturer=pane_capturer,
            pane_mode_probe=pane_mode_probe,
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
    pane_capturer: PaneCapturer,
    pane_mode_probe: PaneModeProbe,
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

    pane_text = pane_capturer(entry.tmux_name)
    input_mode = pane_mode_probe(entry.tmux_name)
    current = catalog.get(session_id)
    if current is None:
        return HostedReadinessResult(
            "unknown-session", session_id, detail="catalog row disappeared during readiness check"
        )
    if hosted_session_identity(current) != hosted_session_identity(entry):
        return HostedReadinessResult(
            "unknown-session", session_id, detail="catalog identity changed during readiness check"
        )
    if current.status == "terminated":
        return HostedReadinessResult(
            "terminated", session_id, entry=current, detail="catalog status is terminated"
        )
    if current.status != "running":
        return HostedReadinessResult(
            "not-ready", session_id, entry=current, detail=f"catalog status is {current.status}"
        )
    if not host.has_session(current.tmux_name):
        return HostedReadinessResult(
            "not-ready",
            session_id,
            entry=current,
            detail="tmux session disappeared during readiness check",
        )
    if input_mode is not False:
        detail = "pane is in copy mode" if input_mode else "pane input mode could not be verified"
        return HostedReadinessResult("not-ready", session_id, entry=current, detail=detail)
    if not boot_ready(pane_text, harness=current.harness):
        return HostedReadinessResult(
            "not-ready", session_id, entry=current, detail="harness composer is not ready"
        )
    return HostedReadinessResult("ready", session_id, entry=current)


def hosted_session_identity(entry: TerminalCatalogEntry) -> tuple[str, str, str]:
    """Fields that prove a re-read still describes the exact spawned hosted session."""

    return entry.id, entry.tmux_name, entry.created_at
