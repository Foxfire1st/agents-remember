"""Liveness probing for durable dashboard terminal catalog rows."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_client import read_control_snapshot
from agents_remember.serving.harness_control_models import AdapterSnapshot
from agents_remember.serving.hosted_control_projection import (
    mark_legacy_control_unsupported,
    project_control_snapshot,
    snapshot_turn_state,
)
from agents_remember.serving.terminal import TmuxProbeResult
from agents_remember.serving.terminal_catalog import (
    SeatTurnState,
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalLivenessEvidence,
)
from agents_remember.serving.terminal_paste import capture_pane as _default_capture_pane
from agents_remember.serving.turn_state import classify_turn_state

PaneCapturer = Callable[[str], str]
SnapshotReader = Callable[[TerminalCatalogEntry], AdapterSnapshot]
ControlSnapshotObserver = Callable[[TerminalCatalogEntry, AdapterSnapshot], None]

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
    # Whether THIS observation transitioned ``entry.turn_state`` (260707-HFX-L8): the caller emits
    # the observer state-change event only when this is true, never on every sweep tick.
    turn_state_changed: bool = False


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
        pane_capturer: PaneCapturer | None = None,
        on_turn_state_change: Callable[[TerminalLivenessObservation], None] | None = None,
        snapshot_reader: SnapshotReader = read_control_snapshot,
        on_control_snapshot: ControlSnapshotObserver | None = None,
    ) -> None:
        self._catalog = catalog
        self._host = host
        self._now = now or utc_now
        self._config = config or TerminalCatalogLivenessConfig()
        self._pane_capturer = pane_capturer
        self._on_turn_state_change = on_turn_state_change
        self._snapshot_reader = snapshot_reader
        self._on_control_snapshot = on_control_snapshot
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
            # 260707-HFX2-L12 F1/CS-6 D2+D3: one disk read + one disk write for the whole sweep. The
            # per-entry probes' read-modify-writes and the terminated-row reclamation all hit the batch's
            # in-memory buffer; the single atomic commit lands on ``batch()`` exit. Without this each of
            # the n probes re-read and rewrote the full catalog file -- O(n^2) disk work per sweep.
            with self._catalog.batch():
                observations = [
                    self._observe_catalog_entry(entry, checked_at=moment)
                    for entry in self._catalog.list()
                ]
                self._catalog.compact(now=moment)
            if self._on_turn_state_change is not None:
                for observation in observations:
                    if observation.turn_state_changed:
                        self._on_turn_state_change(observation)
            return [observation.entry for observation in observations]
        finally:
            self._lock.release()

    def _rate_limited(self, moment: datetime) -> bool:
        return (
            self._last_sweep_at is not None
            and (moment - self._last_sweep_at).total_seconds() < self._config.sweep_interval_seconds
        )

    def _observe_catalog_entry(
        self, entry: TerminalCatalogEntry, *, checked_at: datetime
    ) -> TerminalLivenessObservation:
        if entry.status == "landed":
            return TerminalLivenessObservation(entry=entry, alive=True)
        return observe_terminal_liveness(
            self._catalog,
            self._host,
            entry,
            checked_at=checked_at,
            config=self._config,
            pane_capturer=self._pane_capturer,
            snapshot_reader=self._snapshot_reader,
            on_control_snapshot=self._on_control_snapshot,
        )


def observe_terminal_liveness(
    catalog: TerminalCatalog,
    host: TerminalLivenessHost,
    entry: TerminalCatalogEntry,
    *,
    checked_at: datetime,
    config: TerminalCatalogLivenessConfig | None = None,
    pane_capturer: PaneCapturer | None = None,
    snapshot_reader: SnapshotReader = read_control_snapshot,
    on_control_snapshot: ControlSnapshotObserver | None = None,
) -> TerminalLivenessObservation:
    """Probe one catalog row and persist the matching hysteresis transition.

    An ALIVE harness row is queried through its exact bridge on this SAME sweep call -- no second
    hot loop. Pane classification remains visible as diagnostic detail but cannot set turn state.
    """
    liveness_config = config or TerminalCatalogLivenessConfig()
    session = _host_session(host, entry.id)
    if session is not None and session.is_alive:
        updated = catalog.record_liveness_probe(entry.id, alive=True, checked_at=checked_at)
        return _observe_alive(
            catalog,
            updated or entry.with_liveness_success(),
            checked_at=checked_at,
            pane_capturer=pane_capturer,
            snapshot_reader=snapshot_reader,
            on_control_snapshot=on_control_snapshot,
        )

    probe = _probe_tmux(host, entry.tmux_name)
    if probe.exists:
        updated = catalog.record_liveness_probe(entry.id, alive=True, checked_at=checked_at)
        return _observe_alive(
            catalog,
            updated or entry.with_liveness_success(),
            checked_at=checked_at,
            pane_capturer=pane_capturer,
            snapshot_reader=snapshot_reader,
            on_control_snapshot=on_control_snapshot,
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


def _observe_alive(
    catalog: TerminalCatalog,
    entry: TerminalCatalogEntry,
    *,
    checked_at: datetime,
    pane_capturer: PaneCapturer | None,
    snapshot_reader: SnapshotReader,
    on_control_snapshot: ControlSnapshotObserver | None,
) -> TerminalLivenessObservation:
    """Project bridge state for one live harness; pane classification is diagnostic only."""
    if entry.kind != "harness":
        return TerminalLivenessObservation(entry=entry, alive=True)
    capture = pane_capturer or _default_capture_pane
    pane_text = capture(entry.tmux_name)
    pane_diagnostic = classify_turn_state(pane_text, harness=entry.harness)
    if entry.control_endpoint is None:
        projected = mark_legacy_control_unsupported(catalog, entry)
        projected = replace(
            projected,
            control_raw={
                **(projected.control_raw or {}),
                "paneDiagnostic": pane_diagnostic.state,
            },
        )
        catalog.upsert(projected)
        return _record_adapter_turn_state(catalog, projected, "stale", checked_at)
    try:
        snapshot = snapshot_reader(entry)
    except HarnessControlError as exc:
        projected = replace(
            entry,
            control_state="disconnected",
            control_activity="unknown",
            control_acceptance="unknown",
            control_raw={
                **(entry.control_raw or {}),
                "bridgeError": str(exc),
                "paneDiagnostic": pane_diagnostic.state,
            },
        )
        catalog.upsert(projected)
        return _record_adapter_turn_state(catalog, projected, "stale", checked_at)
    projected = project_control_snapshot(catalog, entry, snapshot)
    projected = replace(
        projected,
        control_raw={**(projected.control_raw or {}), "paneDiagnostic": pane_diagnostic.state},
    )
    catalog.upsert(projected)
    if on_control_snapshot is not None:
        on_control_snapshot(projected, snapshot)
    return _record_adapter_turn_state(
        catalog, projected, snapshot_turn_state(snapshot), checked_at
    )


def _record_adapter_turn_state(
    catalog: TerminalCatalog,
    entry: TerminalCatalogEntry,
    state: SeatTurnState,
    checked_at: datetime,
) -> TerminalLivenessObservation:
    previous_state = entry.turn_state
    updated = catalog.record_turn_state(entry.id, state, changed_at=checked_at.isoformat())
    resolved = updated or entry
    changed = updated is not None and updated.turn_state != previous_state
    return TerminalLivenessObservation(entry=resolved, alive=True, turn_state_changed=changed)


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
