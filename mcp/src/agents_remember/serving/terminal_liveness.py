"""Liveness probing for durable dashboard terminal catalog rows."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from agents_remember.serving.conversation.models import HarnessId

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
)
from agents_remember.models.terminal_catalog import (
    CatalogTurnEvidence,
    SeatTurnState,
    TerminalCatalogEntry,
    TerminalCatalogLivenessConfig,
    TerminalLivenessEvidence,
)
from agents_remember.serving.harness_control_client import read_control_snapshot
from agents_remember.serving.hosted_control_projection import (
    mark_legacy_control_unsupported,
    project_control_snapshot,
    snapshot_turn_state,
)
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.seat_turn_truth import (
    record_terminal_cursors,
    record_turn_projection,
)
from agents_remember.serving.terminal_catalog import (
    DEFAULT_LIVENESS_HYSTERESIS,
)
from agents_remember.serving.terminal_evidence import (
    TerminalEvidenceProjection,
    TerminalEvidenceRead,
    interrupted_origin,
    read_entry_terminal_evidence,
)
from agents_remember.serving.terminal_paste import capture_pane as _default_capture_pane
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.serving.turn_state import TurnStateClassification, classify_turn_state

logger = logging.getLogger(__name__)

PaneCapturer = Callable[[str], str]
SnapshotReader = Callable[[TerminalCatalogEntry], AdapterSnapshot]
TerminalEvidenceReader = Callable[[TerminalCatalogEntry], TerminalEvidenceRead]
ControlSnapshotObserver = Callable[[TerminalCatalogEntry, AdapterSnapshot], None]

DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS = 1.0
"""Minimum spacing between targeted control_state="starting" probes inside the full-sweep gap."""

DEFAULT_CONTROL_READ_FAILURE_THRESHOLD = 3
"""Consecutive bridge snapshot-read failures before a LIVE row may be marked disconnected.

A read failure on a row whose tmux session just probed alive on this same sweep is ambiguous
evidence -- a busy bridge losing the 2s read race during an active turn is indistinguishable from
a dead one -- so, like the tmux command-failure threshold above, one failure never flips the row.
This does NOT delay process-gone marking: a dead tmux session never reaches the snapshot read; it
exit-marks through ``with_liveness_failure``'s pane-gone threshold on the same sweep."""


@runtime_checkable
class _TerminalSessionLike(Protocol):
    @property
    def is_alive(self) -> bool: ...


class TerminalLivenessHost(Protocol):
    def has_session(self, tmux_name: str) -> bool: ...


@dataclass(frozen=True)
class TerminalLivenessObservation:
    entry: TerminalCatalogEntry
    alive: bool
    # Whether THIS observation transitioned ``entry.turn_state``: the caller emits
    # the observer state-change event only when this is true, never on every sweep tick.
    turn_state_changed: bool = False


@dataclass(frozen=True)
class LivenessProbe:
    """How one catalog row is observed: the instruments that read it, and the rule that judges it.

    Reading and judging are one decision. The pane capturer and the bridge snapshot reader produce
    the evidence; ``hysteresis`` decides how much of it is required before a row is marked exited,
    and ``on_control_snapshot`` is who else gets to see that same evidence. Substituting one without
    the others -- a fake reader against production thresholds, say -- observes a session that does
    not exist, which is exactly the mistake four separate parameters made easy.
    """

    hysteresis: TerminalCatalogLivenessConfig = DEFAULT_LIVENESS_HYSTERESIS
    pane_capturer: PaneCapturer | None = None
    snapshot_reader: SnapshotReader = read_control_snapshot
    terminal_reader: TerminalEvidenceReader = read_entry_terminal_evidence
    on_control_snapshot: ControlSnapshotObserver | None = None
    # When set (the sweeps' deferred drain), the synchronizer's evidence is APPENDED here
    # inside the catalog batch and the side effect runs after the commit; None (direct
    # callers outside a batch -- WS attach, paste) keeps the legacy inline call. Bundled
    # with the observer it defers, so the call signature keeps the bundled shape the
    # four-parameter mistake taught -- rather than growing a sixth argument.
    sync_collector: list[_PendingInteractionSync] | None = None


DEFAULT_LIVENESS_PROBE = LivenessProbe()


@dataclass(frozen=True)
class _PendingInteractionSync:
    """One hosted-interaction sync deferred to after the catalog batch commit.

    The synchronizer takes the operator-inbox and gate locks, and the catalog batch (RLock +
    flock held for the whole sweep) must never be held across another store's lock -- the
    agent-notifier's reconcile takes the same inbox lock and then reads the catalog, the
    mirror-image nesting whose ABBA deadlocked the serving daemon in production. The
    evidence (projected entry + snapshot + prior quarantine marker) is captured inside the
    sweep exactly as before; only the side effect moves, to just past the batch commit, still
    on the one sweep tick ("no second hot loop").
    """

    entry: TerminalCatalogEntry
    snapshot: AdapterSnapshot
    previous_sync_error: str | None


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class TerminalCatalogLivenessSweeper:
    """Rate-limited, non-overlapping liveness sweep for terminal catalog rows."""

    def __init__(
        self,
        catalog: TerminalCatalogPort,
        host: TerminalLivenessHost,
        *,
        now: Clock | None = None,
        probe: LivenessProbe = DEFAULT_LIVENESS_PROBE,
        on_turn_state_change: Callable[[TerminalLivenessObservation], None] | None = None,
    ) -> None:
        self._catalog = catalog
        self._host = host
        self._now = now or utc_now
        self._probe = probe
        self._on_turn_state_change = on_turn_state_change
        self._lock = threading.Lock()
        self._last_sweep_at: datetime | None = None
        self._last_starting_sweep_at: datetime | None = None

    def refresh(self) -> list[TerminalCatalogEntry]:
        moment = self._now()
        if self._rate_limited(moment):
            return self._refresh_starting_rows(moment)
        if not self._lock.acquire(blocking=False):
            return self._catalog.list()
        try:
            moment = self._now()
            if self._rate_limited(moment):
                return self._catalog.list()
            self._last_sweep_at = moment
            # One disk read + one disk write for the whole sweep. The
            # per-entry probes' read-modify-writes and the terminated-row reclamation all hit the batch's
            # in-memory buffer; the single atomic commit lands on ``batch()`` exit. Without this each of
            # the n probes re-read and rewrote the full catalog file -- O(n^2) disk work per sweep.
            # The hosted-interaction synchronizer is NOT run inside the batch: its inbox/gate locks
            # must never be taken under the catalog lock, so its evidence is
            # collected here and drained by ``_run_deferred_interaction_syncs`` after the commit.
            pending_syncs: list[_PendingInteractionSync] = []
            with self._catalog.batch():
                observations = [
                    self._observe_catalog_entry(
                        entry, checked_at=moment, sync_collector=pending_syncs
                    )
                    for entry in self._catalog.list()
                ]
                self._catalog.compact(now=moment)
            self._run_deferred_interaction_syncs(pending_syncs)
            if self._on_turn_state_change is not None:
                for observation in observations:
                    if observation.turn_state_changed:
                        self._on_turn_state_change(observation)
            return [observation.entry for observation in observations]
        finally:
            self._lock.release()

    def _refresh_starting_rows(self, moment: datetime) -> list[TerminalCatalogEntry]:
        """Targeted re-probe of starting harness rows while the full sweep is rate-limited.

        A fresh chat's catalog row sits at control_state="starting" until a sweep
        projects its first bridge snapshot, so the 10s full-sweep rate limit quantized the
        starting -> ready flip to ~10.4s measured while the bridge was ready in ~4.6s -- and the
        composer gate waits on catalog readiness. Inside the full-sweep blackout, re-probe only
        running harness rows still marked "starting" (capped at 4) at most once per
        DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS, under the same non-overlapping lock and one batch
        commit. Full-sweep cadence, compaction, and failure hysteresis are unchanged: the fast
        path runs the same observe_terminal_liveness, so with_liveness_failure's minimum window
        still gates exit marking.
        """
        if self._starting_rate_limited(moment):
            return self._catalog.list()
        entries = self._catalog.list()
        starting = [
            entry
            for entry in entries
            if entry.kind == "harness"
            and entry.status == "running"
            and entry.control_state == "starting"
        ][:4]  # cap the fast-path batch so a starting-row burst stays bounded
        if not starting:
            return entries
        if not self._lock.acquire(blocking=False):
            return entries
        try:
            moment = self._now()
            if self._starting_rate_limited(moment):
                return self._catalog.list()
            self._last_starting_sweep_at = moment
            pending_syncs: list[_PendingInteractionSync] = []
            with self._catalog.batch():
                observations = [
                    self._observe_catalog_entry(
                        entry, checked_at=moment, sync_collector=pending_syncs
                    )
                    for entry in starting
                ]
            self._run_deferred_interaction_syncs(pending_syncs)
            if self._on_turn_state_change is not None:
                for observation in observations:
                    if observation.turn_state_changed:
                        self._on_turn_state_change(observation)
            return self._catalog.list()
        finally:
            self._lock.release()

    def _starting_rate_limited(self, moment: datetime) -> bool:
        return (
            self._last_starting_sweep_at is not None
            and (moment - self._last_starting_sweep_at).total_seconds()
            < DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS
        )

    def _rate_limited(self, moment: datetime) -> bool:
        return (
            self._last_sweep_at is not None
            and (moment - self._last_sweep_at).total_seconds()
            < self._probe.hysteresis.sweep_interval_seconds
        )

    def _observe_catalog_entry(
        self,
        entry: TerminalCatalogEntry,
        *,
        checked_at: datetime,
        sync_collector: list[_PendingInteractionSync] | None = None,
    ) -> TerminalLivenessObservation:
        if entry.status == "landed":
            return TerminalLivenessObservation(entry=entry, alive=True)
        probe = self._probe
        if sync_collector is not None:
            probe = replace(probe, sync_collector=sync_collector)
        return observe_terminal_liveness(
            self._catalog, self._host, entry, checked_at=checked_at, probe=probe
        )

    def _run_deferred_interaction_syncs(self, pending: list[_PendingInteractionSync]) -> None:
        """Drain the hosted-interaction syncs collected during the sweep, batch already committed.

        The synchronizer folds the operator-inbox and gate stores, and the
        catalog batch lock must never be held across another store's lock (the ABBA with the
        agent-notifier's lock-held reconcile that deadlocked the serving daemon twice on 2026-08-05).
        The row is re-read before each quarantine upsert so the marker composes with the turn
        state the batch just committed instead of clobbering it with the pre-commit snapshot;
        the only visible cost is that a freshly-quarantined row shows its marker from the next
        read of the catalog rather than inside this sweep's return value.
        """
        observer = self._probe.on_control_snapshot
        if observer is None:
            return
        for item in pending:
            current = self._catalog.get(item.entry.id) or item.entry
            _observe_control_snapshot(
                self._catalog,
                current,
                item.snapshot,
                observer,
                previous_sync_error=item.previous_sync_error,
            )


def observe_terminal_liveness(
    catalog: TerminalCatalogPort,
    host: TerminalLivenessHost,
    entry: TerminalCatalogEntry,
    *,
    checked_at: datetime,
    probe: LivenessProbe = DEFAULT_LIVENESS_PROBE,
) -> TerminalLivenessObservation:
    """Probe one catalog row and persist the matching hysteresis transition.

    An ALIVE harness row is queried through its exact bridge on this SAME sweep call -- no second
    hot loop. Pane classification remains visible as diagnostic detail but cannot set turn state.
    ``probe.sync_collector`` defers the hosted-interaction synchronizer to the caller's post-batch
    drain; ``None`` runs it inline, as direct callers outside a batch do.
    """
    session = _host_session(host, entry.id)
    if session is not None and session.is_alive:
        updated = catalog.record_liveness_probe(entry.id, alive=True, checked_at=checked_at)
        return _observe_alive(
            catalog,
            updated or entry.with_liveness_success(),
            checked_at=checked_at,
            probe=probe,
        )

    tmux = _probe_tmux(host, entry.tmux_name)
    if tmux.exists:
        updated = catalog.record_liveness_probe(entry.id, alive=True, checked_at=checked_at)
        return _observe_alive(
            catalog,
            updated or entry.with_liveness_success(),
            checked_at=checked_at,
            probe=probe,
        )

    updated = catalog.record_liveness_probe(
        entry.id,
        alive=False,
        checked_at=checked_at,
        evidence=_failure_evidence(tmux),
        hysteresis=probe.hysteresis,
    )
    return TerminalLivenessObservation(entry=updated or entry, alive=False)


def _observe_alive(
    catalog: TerminalCatalogPort,
    entry: TerminalCatalogEntry,
    *,
    checked_at: datetime,
    probe: LivenessProbe,
) -> TerminalLivenessObservation:
    """Project bridge state for one live harness; pane classification is diagnostic only."""
    if entry.kind != "harness":
        return TerminalLivenessObservation(entry=entry, alive=True)
    capture = probe.pane_capturer or _default_capture_pane
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
        snapshot = probe.snapshot_reader(entry)
    except HarnessControlError as exc:
        return _observe_control_read_failure(
            catalog,
            entry,
            exc,
            pane_diagnostic=pane_diagnostic,
            checked_at=checked_at,
        )
    # Read the terminal evidence BEFORE persisting the advanced snapshot pointer: the
    # terminal cursors advance only on a successful read, so a failed read leaves the
    # row at the pre-window position and the next sweep re-reads the same evidence.
    terminal_read = _terminal_evidence(probe, entry)
    projected = project_control_snapshot(catalog, entry, snapshot)
    projected = replace(
        projected,
        control_raw={**(projected.control_raw or {}), "paneDiagnostic": pane_diagnostic.state},
    )
    previous_sync_error = (entry.control_raw or {}).get("interactionSyncError")
    catalog.upsert(projected)
    if terminal_read is not None:
        record_terminal_cursors(
            catalog,
            entry.id,
            evidence_sequence=terminal_read.evidence_sequence,
            native_cursor=terminal_read.native_cursor,
        )
    terminal_projection = terminal_read.projection if terminal_read is not None else None
    if probe.on_control_snapshot is not None:
        prior_error = previous_sync_error if isinstance(previous_sync_error, str) else None
        if probe.sync_collector is not None:
            # Defer the synchronizer to the caller's post-batch drain -- its
            # inbox/gate locks must never be taken while the catalog batch lock is held.
            probe.sync_collector.append(
                _PendingInteractionSync(
                    entry=projected, snapshot=snapshot, previous_sync_error=prior_error
                )
            )
        else:
            projected = _observe_control_snapshot(
                catalog,
                projected,
                snapshot,
                probe.on_control_snapshot,
                previous_sync_error=prior_error,
            )
    return _record_adapter_turn_state(
        catalog,
        projected,
        snapshot_turn_state(
            snapshot,
            cast("HarnessId | None", entry.harness),
            previous=projected.turn_state,
            terminal=terminal_projection.evidence if terminal_projection is not None else None,
        ),
        checked_at,
        terminal=terminal_projection,
    )


def _terminal_evidence(
    probe: LivenessProbe,
    entry: TerminalCatalogEntry,
) -> TerminalEvidenceRead | None:
    """Lift the newest per-vendor terminal outcome for one catalog row.

    The daemon surfaces are bounded working surfaces: a failed read means no new terminal
    claim AND no cursor advance this sweep, so the next sweep re-reads the same window
    (no evidence is ever skipped). A failed snapshot read is handled by the caller's
    hysteresis; a failed terminal read must not fail the sweep.
    """
    try:
        return probe.terminal_reader(entry)
    except HarnessControlError:
        return None


def _observe_control_read_failure(
    catalog: TerminalCatalogPort,
    entry: TerminalCatalogEntry,
    exc: HarnessControlError,
    *,
    pane_diagnostic: TurnStateClassification,
    checked_at: datetime,
) -> TerminalLivenessObservation:
    """Hysteresis for one failed bridge snapshot read on a session that just probed ALIVE.

    This row's tmux session probed alive on THIS sweep, so the failed read is
    ambiguous -- a busy bridge losing the 2s snapshot race during an active working turn looks
    identical to a dead one. Flipping ``control_state`` to "disconnected" (+ ``turn_state``
    "stale") on the FIRST failure lied about provably-working seats, measured live twice, and the
    frontend had to rank around the phantom. Mirror the tmux failure hysteresis instead: persist
    a consecutive-failure count on the row (a daemon restart cannot erase it, same rationale as
    ``liveness_failures``) and require DEFAULT_CONTROL_READ_FAILURE_THRESHOLD consecutive failed
    reads before marking disconnected. Below the threshold the row keeps its last projected
    control/turn state -- a failed read is the ABSENCE of evidence, not evidence of loss -- and no
    turn-state observer event fires. The next successful read wipes the counter with the rest of
    ``control_raw`` (``control_snapshot_entry`` reprojects it from the snapshot), so recovery
    needs no special case. Exit marking is untouched: a dead tmux session never reaches this
    path -- ``observe_terminal_liveness`` exit-marks it through ``with_liveness_failure``'s
    pane-gone threshold on the same sweep.
    """
    raw = dict(entry.control_raw or {})
    previous = raw.get("controlReadFailures")
    failures = (previous if isinstance(previous, int) and not isinstance(previous, bool) else 0) + 1
    # Only a row that has ALREADY projected a live bridge may be flipped to
    # "disconnected" by the strike counter. A row still at control_state="starting" has never
    # connected -- its failed reads are a bridge still booting (the control socket is not listening
    # yet), the ABSENCE of a connection rather than the loss of one. The starting-row fast path selects on
    # control_state=="starting", so rewriting a booting row to "disconnected" on the 3rd strike both
    # lied ("disconnected" asserts a connection existed and dropped) AND evicted the row from the
    # fast path mid-boot, stranding it on the 10s full-sweep cadence (measured: bind@12s served ready
    # at 20.0s instead of 12.5s). Keep a booting row "starting" through however many read failures
    # its boot takes so it rides the fast path until the bridge opens. The connected-row flip is untouched: a row
    # that reached "ready"/"working" still flips after the threshold, and a dead tmux session never
    # reaches this read -- it exit-marks on with_liveness_failure's pane-gone path on the same sweep.
    disconnected = (
        failures >= DEFAULT_CONTROL_READ_FAILURE_THRESHOLD and entry.control_state != "starting"
    )
    projected = replace(
        entry,
        control_state="disconnected" if disconnected else entry.control_state,
        control_activity="unknown" if disconnected else entry.control_activity,
        control_acceptance="unknown" if disconnected else entry.control_acceptance,
        control_raw={
            **raw,
            "bridgeError": str(exc),
            "controlReadFailures": failures,
            "paneDiagnostic": pane_diagnostic.state,
        },
    )
    catalog.upsert(projected)
    if not disconnected:
        return TerminalLivenessObservation(entry=projected, alive=True)
    return _record_adapter_turn_state(catalog, projected, "stale", checked_at)


def _observe_control_snapshot(
    catalog: TerminalCatalogPort,
    entry: TerminalCatalogEntry,
    snapshot: AdapterSnapshot,
    observer: ControlSnapshotObserver,
    *,
    previous_sync_error: str | None,
) -> TerminalCatalogEntry:
    """Run the hosted-interaction synchronizer as a quarantined per-entry side effect.

    The synchronizer is a downstream durable projection (agent-question gates
    plus operator-inbox completion rows), NOT part of computing this row's liveness/control state --
    which is already committed by the ``catalog.upsert(projected)`` above. A single poisoned
    completion (e.g. an adapter terminal result whose ``vendorCorrelationId`` matches no accepted
    inbox row) raises ``HarnessControlError`` inside ``observe``; before this guard that exception
    propagated straight out of the sweep's per-entry list comprehension, aborting the whole
    ``TerminalCatalog.batch()`` and 500-ing ``/api/terminal/sessions`` for EVERY row. Contain the
    failure to this one row: record it loudly on the row's own ``control_raw`` and log it, and never
    let one row's side-effect failure break the catalog projection. This is availability hardening of
    a proven failure; it does not touch the completion-correlation contract that raised.

    An orphan completion is the NORMAL steady state of every cockpit-driven
    hosted chat (a cockpit turn's terminal result carries a ``vendorCorrelationId`` that matches no
    operator-inbox row because it never was an inbox delivery), so the failure re-fires on EVERY
    ~10 s sweep, indefinitely. Log only on STATE CHANGE -- first occurrence / a changed error / heal
    -- while still refreshing the per-sweep marker so the wire stays honestly degraded every sweep.
    """
    try:
        observer(entry, snapshot)
    except Exception as exc:  # one row's downstream side effect must never fail the whole sweep
        message = str(exc)
        if message != previous_sync_error:
            logger.warning(
                "hosted interaction synchronizer failed for terminal row %s; "
                "quarantining on its row: %s",
                entry.id,
                message,
            )
        quarantined = replace(
            entry,
            control_raw={**(entry.control_raw or {}), "interactionSyncError": message},
        )
        catalog.upsert(quarantined)
        return quarantined
    if previous_sync_error is not None:
        logger.info(
            "hosted interaction synchronizer recovered for terminal row %s; "
            "cleared the quarantine marker",
            entry.id,
        )
    return entry


def _record_adapter_turn_state(
    catalog: TerminalCatalogPort,
    entry: TerminalCatalogEntry,
    state: SeatTurnState | None,
    checked_at: datetime,
    *,
    terminal: TerminalEvidenceProjection | None = None,
) -> TerminalLivenessObservation:
    # A None projection makes NO seat claim this sweep (a healthy
    # boot or a fresh ready-idle chat) — the row keeps its last claim (or none) and no
    # turn-state event fires, rather than stamping an alarming "stale"/"turn-ended".
    if state is None:
        return TerminalLivenessObservation(entry=entry, alive=True)
    previous_state = entry.turn_state
    # A sweep without new terminal evidence preserves the lifted outcome: terminal truth,
    # once observed, is not un-written by a later non-terminal snapshot.
    stamp = CatalogTurnEvidence(
        state=state,
        changed_at=checked_at.isoformat(),
        terminal_outcome=(
            terminal.evidence.outcome if terminal is not None else entry.terminal_outcome
        ),
        terminal_outcome_at=(
            terminal.observed_at if terminal is not None else entry.terminal_outcome_at
        ),
        terminal_evidence_id=(
            terminal.evidence_id if terminal is not None else entry.terminal_evidence_id
        ),
        interrupted_by=(
            interrupted_origin(entry, terminal.evidence)
            if terminal is not None
            else entry.interrupted_by
        ),
    )
    updated = record_turn_projection(catalog, entry.id, stamp)
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
