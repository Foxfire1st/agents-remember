"""Liveness probing for durable dashboard terminal catalog rows."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from agents_remember.serving.terminal import TmuxProbeResult
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalLivenessEvidence,
)

DEFAULT_LIVENESS_FAILURE_THRESHOLD = 3
"""Consecutive tmux-command failures needed before a catalog row is marked exited."""

DEFAULT_LIVENESS_FAILURE_WINDOW_SECONDS = 5.0
"""Minimum age of the first failed command probe before hysteresis can exit-mark a row."""

DEFAULT_PANE_GONE_FAILURE_THRESHOLD = 1
"""Pane-gone evidence is definitive, so it may mark faster than command failures."""

DEFAULT_LIVENESS_SWEEP_INTERVAL_SECONDS = 10.0
"""Minimum spacing between full catalog sweeps, independent of the dashboard projection tick."""


@runtime_checkable
class _TerminalSessionLike(Protocol):
    @property
    def is_alive(self) -> bool: ...


class TerminalLivenessHost(Protocol):
    def has_session(self, tmux_name: str) -> bool: ...


@dataclass(frozen=True)
class TerminalCatalogLivenessConfig:
    failure_threshold: int = DEFAULT_LIVENESS_FAILURE_THRESHOLD
    minimum_failure_window_seconds: float = DEFAULT_LIVENESS_FAILURE_WINDOW_SECONDS
    pane_gone_failure_threshold: int = DEFAULT_PANE_GONE_FAILURE_THRESHOLD
    sweep_interval_seconds: float = DEFAULT_LIVENESS_SWEEP_INTERVAL_SECONDS


@dataclass(frozen=True)
class TerminalLivenessObservation:
    entry: TerminalCatalogEntry
    alive: bool


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class TerminalCatalogLivenessSweeper:
    """Rate-limited, non-overlapping liveness sweep for terminal catalog rows."""

    def __init__(
        self,
        catalog: TerminalCatalog,
        host: TerminalLivenessHost,
        *,
        now: Clock | None = None,
        config: TerminalCatalogLivenessConfig | None = None,
    ) -> None:
        self._catalog = catalog
        self._host = host
        self._now = now or utc_now
        self._config = config or TerminalCatalogLivenessConfig()
        self._lock = threading.Lock()
        self._last_sweep_at: datetime | None = None

    def refresh(self) -> list[TerminalCatalogEntry]:
        moment = self._now()
        if self._rate_limited(moment):
            return self._catalog.list()
        if not self._lock.acquire(blocking=False):
            return self._catalog.list()
        try:
            moment = self._now()
            if self._rate_limited(moment):
                return self._catalog.list()
            self._last_sweep_at = moment
            return [
                observe_terminal_liveness(
                    self._catalog,
                    self._host,
                    entry,
                    checked_at=moment,
                    config=self._config,
                ).entry
                for entry in self._catalog.list()
            ]
        finally:
            self._lock.release()

    def _rate_limited(self, moment: datetime) -> bool:
        return (
            self._last_sweep_at is not None
            and (moment - self._last_sweep_at).total_seconds() < self._config.sweep_interval_seconds
        )


def observe_terminal_liveness(
    catalog: TerminalCatalog,
    host: TerminalLivenessHost,
    entry: TerminalCatalogEntry,
    *,
    checked_at: datetime,
    config: TerminalCatalogLivenessConfig | None = None,
) -> TerminalLivenessObservation:
    """Probe one catalog row and persist the matching hysteresis transition."""
    liveness_config = config or TerminalCatalogLivenessConfig()
    session = _host_session(host, entry.id)
    if session is not None and session.is_alive:
        updated = catalog.record_liveness_probe(
            entry.id,
            alive=True,
            checked_at=checked_at,
        )
        return TerminalLivenessObservation(
            entry=updated or entry.with_liveness_success(), alive=True
        )

    probe = _probe_tmux(host, entry.tmux_name)
    if probe.exists:
        updated = catalog.record_liveness_probe(
            entry.id,
            alive=True,
            checked_at=checked_at,
        )
        return TerminalLivenessObservation(
            entry=updated or entry.with_liveness_success(), alive=True
        )

    evidence = _failure_evidence(probe)
    updated = catalog.record_liveness_probe(
        entry.id,
        alive=False,
        checked_at=checked_at,
        evidence=evidence,
        failure_threshold=liveness_config.failure_threshold,
        minimum_failure_window_seconds=liveness_config.minimum_failure_window_seconds,
        pane_gone_failure_threshold=liveness_config.pane_gone_failure_threshold,
    )
    return TerminalLivenessObservation(entry=updated or entry, alive=False)


def _probe_tmux(host: TerminalLivenessHost, tmux_name: str) -> TmuxProbeResult:
    probe = getattr(host, "probe_session", None)
    if callable(probe):
        result = probe(tmux_name)
        if isinstance(result, TmuxProbeResult):
            return result
    exists = host.has_session(tmux_name)
    evidence = "alive" if exists else "pane-gone"
    return TmuxProbeResult(exists=exists, evidence=evidence)


def _host_session(host: TerminalLivenessHost, sid: str) -> _TerminalSessionLike | None:
    getter = getattr(host, "get", None)
    if not callable(getter):
        return None
    session = getter(sid)
    if isinstance(session, _TerminalSessionLike):
        return session
    return None


def _failure_evidence(probe: TmuxProbeResult) -> TerminalLivenessEvidence:
    return "tmux-command-failed" if probe.evidence == "tmux-command-failed" else "pane-gone"
