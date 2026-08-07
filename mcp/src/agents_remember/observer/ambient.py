"""The ambient lifecycle: the process-scoped owner of the current lifecycle.

One stdio MCP server process is approximately one harness session, so the
current lifecycle is a process singleton (design §1.6). ``lifecycle_start`` /
``switch_lifecycle`` set it; every subsequent tool response is auto-tagged at
the ``_tool_payload`` choke point via :func:`ambient`. The singleton owns:

* the signal state machine (start/block/resume/end/switch/phase),
* event emission (signals ``declared``; the tool choke point ``observed``),
* a heartbeat ticker that generalizes the ``setup_progress`` daemon-thread idiom
  so a dead session is detectable by a stale last heartbeat -- and which goes
  quiet after a span of no real activity, so an idle/parked lifecycle's log ages
  out and becomes cleanable instead of being kept alive by its own keepalive, and
* an opportunistic project-and-prune TTL sweep for dormant fleeting lifecycles.

The state *types* live in :mod:`agents_remember.observer.lifecycle_state`; this
module is the behavior + threading + the process-global registry.
"""

from __future__ import annotations

import contextlib
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.controlplane.stamps import age_seconds
from agents_remember.observer.events import Actor, Event, Trust
from agents_remember.observer.lifecycle_state import (
    INITIAL_PHASE,
    TERMINAL_STATES,
    GuardedStartError,
    LifecycleError,
    LifecycleState,
    Phase,
    coerce_end_outcome,
)
from agents_remember.observer.save_gate import (
    SaveDecision,
    SaveGateRequired,
    compute_scope,
)
from agents_remember.observer.served_store import ServedLedger, ServedStore
from agents_remember.observer.store import EventStore
from agents_remember.observer.timeutil import (
    HEARTBEAT_SECONDS,
    TTL_SECONDS,
    Clock,
)
from agents_remember.observer.ulid import new_ulid

IdFactory = Callable[[], str]
TickerWait = Callable[[threading.Event, float], bool]

# The fixed facts-only allowlist for a ``read.packet`` per-file entry. The
# read-packet emitter projects every caller entry to exactly these keys, so no
# source/onboarding/overview content can reach ``Event.data`` regardless of the
# caller (the privacy invariant is this projection, not Event's ``extra``).
_READ_PACKET_FACTS = ("path", "lines", "status", "bytes")

# A heartbeat reflects *activity*, not mere process liveness: after this long with no
# real (non-heartbeat) event the ticker goes quiet so a parked/idle lifecycle's log ages
# out and becomes cleanable instead of being held alive by its own keepalive. It resumes
# emitting the moment real activity returns.
INACTIVITY_CUTOFF_SECONDS = 10 * 60
_HEARTBEAT_KIND = "lifecycle.heartbeat"


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _default_ticker_wait(stop: threading.Event, interval: float) -> bool:
    """Wait up to ``interval`` for the stop flag, rechecking on every wake.

    CPython's ``Event.wait``/``Condition.wait`` parks the caller on a waiter
    lock, and the lock-handoff there can overrun the timeout and leave the
    thread parked with no recheck or escape. The ticker must never silently
    stop, so the production wait chunks ``time.sleep`` against a monotonic
    deadline instead: every sleep returns, the stop flag is re-read, and the
    interval expires deterministically. Returns True when stop was observed,
    False when the interval elapsed.
    """
    deadline = time.monotonic() + interval
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(remaining, 0.01))
    return True


@dataclass(frozen=True)
class AmbientTiming:
    """The three durations that govern one ambient lifecycle's clock: how often it beats, how
    long a log lives, and how long inactivity is tolerated before the ticker stops beating.
    They are read against each other -- a heartbeat longer than the TTL keeps nothing alive --
    so they are one timing policy rather than three independent knobs."""

    heartbeat_seconds: float = HEARTBEAT_SECONDS
    ttl_seconds: float = TTL_SECONDS
    inactivity_cutoff_seconds: float = INACTIVITY_CUTOFF_SECONDS


class AmbientLifecycle:
    """Process-scoped current lifecycle: state machine + emission + heartbeat.

    All state mutation and event emission happens under ``_lock`` because the
    heartbeat ticker thread and the request thread both write to the same
    per-lifecycle log, and the store is single-writer-per-file by contract.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        timing: AmbientTiming | None = None,
        clock: Clock = _default_clock,
        id_factory: IdFactory = new_ulid,
        served_store: ServedStore | None = None,
    ) -> None:
        timing = timing or AmbientTiming()
        self._store = store
        self._heartbeat_seconds = timing.heartbeat_seconds
        self._ttl_seconds = timing.ttl_seconds
        self._inactivity_cutoff_seconds = timing.inactivity_cutoff_seconds
        # ISO ts of the last real (non-heartbeat) event; gates the heartbeat ticker.
        self._last_activity_iso: str | None = None
        self._clock = clock
        self._mint = id_factory
        self._ticker_wait = _default_ticker_wait
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None
        self.current: LifecycleState | None = None
        # The served-onboarding dedup ledger lives beside the event logs (one
        # served.jsonl per lifecycle dir). It is a COLLABORATOR rather than four more
        # methods here: the dedup state is a second substrate with its own file and its
        # own lock, and it was never part of this state machine -- only co-located with
        # it. This class keeps exactly one duty over it, forgetting a lifecycle's set
        # when that lifecycle ends or is left.
        self._served = ServedLedger(served_store or ServedStore(store.root))

    @property
    def served(self) -> ServedLedger:
        """The served-onboarding dedup ledger for the lifecycles in this process."""
        return self._served

    @property
    def root(self) -> Path:
        """The observer store root (``logs/observer``) this ambient lifecycle writes under.

        260707-HFX2-L2 R5: the MCP tool choke point (``_tool_payload``) reads this to check the
        supervisor heartbeat opportunistically, without needing its own ``McpRuntimeConfig``.
        """
        return self._store.root

    # --- signals -----------------------------------------------------------

    def start(
        self,
        *,
        fleeting: bool = True,
        phase: Phase = INITIAL_PHASE,
        ticker_wait: TickerWait | None = None,
    ) -> LifecycleState:
        """Guarded start: mint a lifecycle and become ``running`` (§1.3)."""
        with self._lock:
            if self.current is not None:
                raise GuardedStartError(self.current.id)
            if ticker_wait is not None:
                self._ticker_wait = ticker_wait
            current = LifecycleState(
                id=self._mint(),
                state="running",
                phase=phase,
                fleeting=fleeting,
                started_at=self._now(),
            )
            self.current = current
            self._emit_locked(
                "lifecycle.started", "declared", "model", phase=phase, fleeting=fleeting
            )
            self._start_ticker_locked()
        self._reap_stale_fleeting()  # opportunistic; never touches the fresh current
        return current

    def block(
        self,
        *,
        kind: str | None = None,
        prompt: str | None = None,
        options: list[str] | None = None,
    ) -> LifecycleState:
        """``running`` -> ``blocked``; the optional ask rides on the event data.

        The durable gate record and its enforcement are the gate-control-plane
        slice; here the ask is carried as facts on ``lifecycle.blocked`` so that
        later slice can materialize the record without re-instrumentation.
        """
        with self._lock:
            current = self._require_active()
            if current.state != "running":
                raise LifecycleError(
                    f"cannot block from state {current.state!r}; only running blocks"
                )
            self.current = replace(current, state="blocked")
            ask = build_ask(kind, prompt, options)
            self._emit_locked(
                "lifecycle.blocked",
                "declared",
                "model",
                **({"ask": ask} if ask else {}),
            )
            return self.current

    def resume(self) -> LifecycleState:
        """``blocked`` -> ``running`` once the gate is resolved (§1.3)."""
        with self._lock:
            current = self._require_active()
            if current.state != "blocked":
                raise LifecycleError(
                    f"cannot resume from state {current.state!r}; only blocked resumes"
                )
            self.current = replace(current, state="running")
            self._emit_locked("lifecycle.resumed", "declared", "model")
            return self.current

    def await_developer(self, *, summary: str) -> LifecycleState:
        """``running`` -> ``awaiting-developer``: the NOTIFY-AND-CONTINUE turn end.

        Modeled on :meth:`block`, but there is no gate and no wait: the model
        declares the turn complete and stops. The state is non-terminal -- the
        next AR tool call auto-resumes it (``resume_from_await``) at the
        ``_tool_payload`` choke point -- so this is a notification, not a barrier.
        """
        with self._lock:
            current = self._require_active()
            if current.state != "running":
                raise LifecycleError(
                    f"cannot await-developer from state {current.state!r}; only running awaits"
                )
            self.current = replace(current, state="awaiting-developer")
            self._emit_locked("lifecycle.awaiting-developer", "declared", "model", summary=summary)
            return self.current

    def resume_from_await(self) -> LifecycleState:
        """``awaiting-developer`` -> ``running`` on the next AR tool call.

        A separate method from :meth:`resume` (which only resumes ``blocked``) so
        the parked gate stack keeps its strict "only blocked resumes" guard. The
        choke point calls this automatically when any tool other than the turn-end
        notification fires while awaiting -- the auto-dismiss that makes the
        notification a stop, not a stall.
        """
        with self._lock:
            current = self._require_active()
            if current.state != "awaiting-developer":
                raise LifecycleError(
                    f"cannot resume-from-await from state {current.state!r}; "
                    "only awaiting-developer resumes this way"
                )
            self.current = replace(current, state="running")
            self._emit_locked("lifecycle.resumed", "declared", "model")
            return self.current

    def end(self, outcome: str) -> LifecycleState:
        """Terminal ``lifecycle.ended``; clears the ambient (§1.2).

        Emitted before the ambient is cleared, so the choke point sees no
        current afterward and the end call itself produces no ``tool.completed``
        -- the terminal signal is the record, not a redundant tool event. The
        returned snapshot is the ended lifecycle's terminal state.

        Which outcomes are accepted, and which state each one names, are both read
        from :mod:`~agents_remember.observer.lifecycle_state` rather than restated
        here: the terminal half of the vocabulary IS the ``lifecycle.ended`` outcome
        vocabulary, so the write side has nothing of its own to keep in step. The
        WRITE side still refuses an unknown outcome rather than defaulting it --
        :func:`coerce_end_outcome`'s leniency is for the reducer, which reads logs
        it did not write; a session ending itself must not have a typo silently
        recorded as an abandonment.
        """
        if outcome not in TERMINAL_STATES:
            raise LifecycleError(
                f"end outcome must be {'|'.join(sorted(TERMINAL_STATES))}, got {outcome!r}"
            )
        # Membership is already checked, so this is the identity conversion -- called
        # anyway, rather than cast, so the outcome -> state rule has exactly one owner
        # and the write side reads it from the same function the reducer does.
        terminal = coerce_end_outcome(outcome)
        with self._lock:
            current = self._require_active()
            self._emit_locked("lifecycle.ended", "declared", "model", outcome=outcome)
            self._stop_ticker_locked()
            self._served.forget(current.id)
            self.current = None
            return replace(current, state=terminal)

    def phase(self, phase: Phase) -> LifecycleState:
        """Move along the orthogonal phase axis (§1.4); any non-terminal state."""
        with self._lock:
            current = self._require_active()
            self.current = replace(current, phase=phase)
            self._emit_locked("lifecycle.phase-changed", "declared", "model", phase=phase)
            return self.current

    def switch(
        self,
        *,
        on_unsaved: SaveDecision | None = None,
        fleeting: bool = True,
        phase: Phase = INITIAL_PHASE,
    ) -> LifecycleState:
        """Leave the current lifecycle, then mint a fresh one (no target, §1.3).

        Leaving routes through the save gate: a persistent ``running`` lifecycle
        is paused (``switched-away``); a *fleeting* one needs an explicit
        ``on_unsaved`` decision -- ``save`` promotes it (then pauses), ``discard``
        ends it ``abandoned``, and an absent decision raises ``SaveGateRequired``
        so unsaved work is never dropped silently. Adopting an *existing* target
        id is :meth:`attach` (contract-resolved); the model never handles ids.
        """
        with self._lock:
            self._leave_current_locked(on_unsaved)
            current = LifecycleState(
                id=self._mint(),
                state="running",
                phase=phase,
                fleeting=fleeting,
                started_at=self._now(),
            )
            self.current = current
            self._emit_locked(
                "lifecycle.started", "declared", "model", phase=phase, fleeting=fleeting
            )
            self._start_ticker_locked()
        self._reap_stale_fleeting()
        return current

    def promote(self, *, enclosure: str, repo_id: str | None, scope: str) -> LifecycleState:
        """Fleeting -> persistent on ``worktree_start`` (§1.3).

        The current lifecycle gains its durable contract anchor (``enclosure``)
        and scope; ``lifecycle.promoted`` is a first-class v1 event so a reader
        tells a saved lifecycle from a dormant fleeting one. Not a switch -- the
        same lifecycle keeps running.
        """
        with self._lock:
            current = self._require_active()
            self.current = replace(
                current, fleeting=False, enclosure=enclosure, repo_id=repo_id, scope=scope
            )
            self._emit_locked("lifecycle.promoted", "observed", "system", scope=scope)
            return self.current

    def attach(
        self,
        lifecycle_id: str,
        *,
        enclosure: str,
        repo_id: str | None = None,
        on_unsaved: SaveDecision | None = None,
        phase: Phase = "build",
    ) -> LifecycleState:
        """``worktree_attach`` as ``switch_lifecycle`` with a resolved target (§1.3).

        none active -> adopt; same id -> no-op; otherwise leave the current
        (persistent: auto-pause; fleeting: save gate) then adopt the target and
        emit ``lifecycle.resumed`` (``adopted``). Adoption is attribution only --
        the resolved id becomes ambient so later calls tag correctly; full state
        replay is the slice-3 reducer, so it adopts at phase ``build`` (attach is
        mid-work).
        """
        with self._lock:
            current = self.current
            if current is not None and current.id == lifecycle_id:
                return current
            self._leave_current_locked(on_unsaved)
            adopted = LifecycleState(
                id=lifecycle_id,
                state="running",
                phase=phase,
                fleeting=False,
                started_at=self._now(),
                enclosure=enclosure,
                repo_id=repo_id,
                scope=compute_scope(repo_id),
            )
            self.current = adopted
            self._emit_locked("lifecycle.resumed", "observed", "system", cause="adopted")
            self._start_ticker_locked()
        self._reap_stale_fleeting()
        return adopted

    # --- ambient attribution ----------------------------------------------

    def emit_tool(self, tool_name: str, payload: dict[str, Any]) -> None:
        """Emit one ``observed`` ``tool.completed`` for the active lifecycle.

        A lifecycle-less call is *dropped*, never attributed to a lifecycle it
        does not belong to (the same honesty rule as "never pretend declared is
        observed"). Emission must never break the tool it observes, so a
        validation or disk error is contained here.
        """
        with self._lock:
            if self.current is None:
                return
            with contextlib.suppress(ValidationError, OSError):
                self._emit_locked(
                    "tool.completed",
                    "observed",
                    "model",
                    tool=tool_name,
                    tokens=payload.get("tokens"),
                    ok=payload.get("ok"),
                )

    def emit_read_packet(self, repo_id: str, files: list[dict[str, Any]]) -> None:
        """Emit one facts-only ``read.packet`` for the active lifecycle.

        Modeled on :meth:`emit_tool`: a lifecycle-less call is *dropped* (never
        misattributed), and emission must never break the read it records, so a
        validation or disk error is contained here. The facts-only guarantee is
        **enforced structurally**: each caller entry is projected to the fixed
        allowlist ``{path, lines, status, bytes}`` by construction (a new dict
        picking only those keys), so any other key -- including file content --
        is dropped here regardless of what the caller passes. (Event's
        ``extra="forbid"`` only governs top-level Event fields, not the contents
        of ``data``, so the projection -- not the envelope -- is the privacy
        invariant.) The packet ``data`` also carries the read's ``repoId`` (the
        repo the files belong to -- a fact, distinct from the lifecycle's
        top-level Event ``repoId``). This rides the same ambient choke as
        ``tool.completed`` so it inherits the chat's fleeting identity (or the
        adopted worktree lifecycle) and is session-traceable by construction.
        """
        projected = [
            {key: entry[key] for key in _READ_PACKET_FACTS if key in entry} for entry in files
        ]
        with self._lock:
            if self.current is None:
                return
            with contextlib.suppress(ValidationError, OSError):
                self._emit_locked(
                    "read.packet", "observed", "model", repoId=repo_id, files=projected
                )

    def shutdown(self) -> None:
        """Stop the heartbeat ticker (process teardown / test isolation)."""
        with self._lock:
            self._stop_ticker_locked()

    # --- internals ---------------------------------------------------------

    def _require_active(self) -> LifecycleState:
        if self.current is None:
            raise LifecycleError("no active lifecycle; start or switch into one first")
        return self.current

    def _leave_current_locked(self, decision: SaveDecision | None) -> None:
        """Leave the current lifecycle (lock held): pause persistent, save-gate fleeting.

        Persistent -> ``paused`` (``switched-away``). Fleeting -> the save gate
        (§1.2/§1.5): ``save`` promotes to a landing zone then pauses, ``discard``
        ends ``abandoned``, and ``None`` raises ``SaveGateRequired`` -- the gate
        blocks rather than dropping unsaved work, and slice 06 supplies the
        decision through the interactive return channel.
        """
        current = self.current
        if current is None:
            return
        if not current.fleeting:
            self._pause_locked("switched-away")
            return
        if decision is None:
            raise SaveGateRequired(current.id)
        if decision == "save":
            self._promote_to_landing_zone_locked()
            self._pause_locked("switched-away")
        else:
            # Naming ONE outcome, not classifying: discarding unsaved work is a decision
            # this branch makes, so the literal is the decision. (It is deliberately not
            # ``DEFAULT_END_OUTCOME``, which is the separate policy for coercing a free-form
            # outcome at the tool boundary -- discard would follow that constant anywhere it
            # moved, and there is no reason it should.)
            self._emit_locked("lifecycle.ended", "declared", "model", outcome="abandoned")
            self._stop_ticker_locked()
            self._served.forget(current.id)
            self.current = None

    def _promote_to_landing_zone_locked(self) -> None:
        """Save a fleeting lifecycle being left behind (it has no worktree).

        Promotion without a tangible repo lands in a scope zone (§1.5):
        ``0_unscoped`` by default. The dashboard (slice 4) groups by the scope
        tag; here we only record it on ``lifecycle.promoted``.
        """
        current = self.current
        if current is None:  # pragma: no cover - guarded by callers
            return
        scope = compute_scope(current.repo_id)
        self.current = replace(current, fleeting=False, scope=scope)
        self._emit_locked("lifecycle.promoted", "observed", "system", scope=scope)

    def _pause_locked(self, cause: str) -> None:
        """Leaving -> ``paused``: emit, stop the ticker, release the current."""
        self._emit_locked("lifecycle.paused", "observed", "system", cause=cause)
        self._stop_ticker_locked()
        if self.current is not None:
            self._served.forget(self.current.id)
        self.current = None

    def _emit_locked(self, kind: str, trust: Trust, actor: Actor, **data: Any) -> None:
        current = self.current
        if current is None:  # pragma: no cover - guarded by callers
            return
        ts = self._now()
        if kind != _HEARTBEAT_KIND:
            # Heartbeats are liveness theater, not activity; only real events keep the
            # ticker alive (and reset the inactivity clock retention reads).
            self._last_activity_iso = ts
        self._store.append(
            Event(
                id=self._mint(),
                ts=ts,
                kind=kind,
                trust=trust,
                actor=actor,
                lifecycleId=current.id,
                enclosure=current.enclosure,
                repoId=current.repo_id,
                data=dict(data),
            )
        )

    def _now(self) -> str:
        return self._clock().isoformat()

    # --- heartbeat ticker (generalizes setup_progress) ---------------------

    def _start_ticker_locked(self) -> None:
        self._stop_ticker_locked()
        self._stop = threading.Event()
        ticker = threading.Thread(
            target=self._heartbeat_loop,
            args=(self._stop, self._heartbeat_seconds),
            name="lifecycle-heartbeat",
            daemon=True,
        )
        self._ticker = ticker
        ticker.start()

    def _stop_ticker_locked(self) -> None:
        self._stop.set()

    def _heartbeat_loop(self, stop: threading.Event, interval: float) -> None:
        while not self._ticker_wait(stop, interval):
            if not self._heartbeat_tick():
                return

    def _heartbeat_tick(self) -> bool:
        """Run one beat: emit unless idle past the cutoff or the lifecycle is gone.

        Returns False when the loop should exit (no active lifecycle or a
        terminal one), True otherwise.
        """
        with self._lock:
            current = self.current
            if current is None or current.is_terminal:
                return False
            if self._inactive_seconds_locked() > self._inactivity_cutoff_seconds:
                # Idle past the cutoff: stop the heartbeat theater so the log ages out
                # and becomes cleanable. The ticker keeps looping and resumes emitting
                # the moment a real event resets the activity clock.
                return True
            self._emit_locked(
                "lifecycle.heartbeat",
                "observed",
                "system",
                state=current.state,
                phase=current.phase,
            )
            return True

    def _inactive_seconds_locked(self) -> float:
        """Seconds since the last real (non-heartbeat) event for the current lifecycle."""
        last = self._last_activity_iso
        if last is None:
            return 0.0
        age = age_seconds(last, self._clock())
        return age if age is not None else 0.0

    # --- TTL project-and-prune sweep ---------------------------------------

    def _reap_stale_fleeting(self) -> list[str]:
        """Delete dormant, never-promoted fleeting logs; return pruned ids.

        Project-and-prune (design §1.5): a fleeting lifecycle past its TTL has no
        live owner to write a terminal event, so readers derive ``abandoned``
        and this sweep deletes the log directory -- never a non-owner append, so
        the single-writer invariant holds. Runs opportunistically on
        start/switch (a dead stdio process cannot reap itself).
        """
        lifecycles_root = self._store.root / "lifecycles"
        if not lifecycles_root.is_dir():
            return []
        keep = self.current.id if self.current else None
        now = self._clock()
        pruned: list[str] = []
        for entry in lifecycles_root.iterdir():
            if not entry.is_dir() or entry.name == keep:
                continue
            if self._is_dormant_fleeting(entry.name, now):
                shutil.rmtree(entry, ignore_errors=True)
                pruned.append(entry.name)
        return pruned

    def _is_dormant_fleeting(self, lifecycle_id: str, now: datetime) -> bool:
        events = self._store.read(lifecycle_id)
        if not events:
            return False
        started = events[0]
        if started.kind != "lifecycle.started" or not started.data.get("fleeting"):
            return False
        if any(event.kind == "lifecycle.promoted" for event in events):
            return False
        age = age_seconds(events[-1].ts, now)
        return age is not None and age > self._ttl_seconds


def build_ask(
    kind: str | None, prompt: str | None, options: list[str] | None
) -> dict[str, Any] | None:
    """The structured ask carried on a block, or ``None`` when none was given.

    Single source for the ask shape used by ``lifecycle_gate`` and the retained
    lower-level lifecycle-block compatibility builder.
    """
    ask = {"kind": kind, "prompt": prompt, "options": options}
    pruned = {key: value for key, value in ask.items() if value is not None}
    return pruned or None


# --- process-global registry ----------------------------------------------


class _AmbientRegistry:
    """Holds the one ambient lifecycle for this server process.

    A class attribute rather than a module ``global`` so the choke point reads
    it without a ``global`` statement and tests can swap it cleanly.
    """

    instance: AmbientLifecycle | None = None


def ambient() -> AmbientLifecycle | None:
    """The installed ambient lifecycle, or ``None`` outside a server process."""
    return _AmbientRegistry.instance


def install_ambient(instance: AmbientLifecycle) -> None:
    """Install the process singleton (once per ``create_server``)."""
    _AmbientRegistry.instance = instance


def require_ambient() -> AmbientLifecycle:
    """Return the installed ambient or raise -- used by the signal tools."""
    current = _AmbientRegistry.instance
    if current is None:
        raise LifecycleError("no ambient lifecycle installed in this process")
    return current


def reset_ambient() -> None:
    """Stop any ticker and clear the singleton (test isolation)."""
    existing = _AmbientRegistry.instance
    if existing is not None:
        existing.shutdown()
    _AmbientRegistry.instance = None
