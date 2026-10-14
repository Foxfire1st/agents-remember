"""The ambient lifecycle: the process-scoped owner of the current lifecycle.

One stdio MCP server process is approximately one harness session, so the
current lifecycle is a process singleton (design §1.6). ``worktree_start`` and
``worktree_attach`` set it (:meth:`AmbientLifecycle.attach`); every subsequent tool
response is auto-tagged at the ``_tool_payload`` choke point via :func:`ambient`.
The singleton owns:

* the current lifecycle (attach/end),
* event emission (signals ``declared``; the tool choke point ``observed``),
* a heartbeat ticker that generalizes the ``setup_progress`` daemon-thread idiom
  so a dead session is detectable by a stale last heartbeat -- and which goes
  quiet after a span of no real activity, so an idle/parked lifecycle's log ages
  out and becomes cleanable instead of being kept alive by its own keepalive.

The state *types* live in :mod:`agents_remember.observer.lifecycle_state`; this
module is the behavior + threading + the process-global registry.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from agents_remember.controlplane.stamps import age_seconds
from agents_remember.observer.events import Actor, Event, Trust
from agents_remember.observer.lifecycle_state import (
    TERMINAL_STATES,
    LifecycleError,
    LifecycleState,
    Phase,
    coerce_end_outcome,
)
from agents_remember.observer.served_store import ServedLedger, ServedStore
from agents_remember.observer.store import EventStore
from agents_remember.observer.timeutil import (
    HEARTBEAT_SECONDS,
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
    """The two durations that govern one ambient lifecycle's clock: how often it beats and
    how long inactivity is tolerated before the ticker stops beating."""

    heartbeat_seconds: float = HEARTBEAT_SECONDS
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

    # --- signals -----------------------------------------------------------

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

    def attach(
        self,
        lifecycle_id: str,
        *,
        enclosure: str,
        repo_id: str | None = None,
        phase: Phase = "build",
    ) -> LifecycleState:
        """Adopt the lifecycle a worktree contract names (§1.3).

        none active -> adopt; same id -> no-op; otherwise pause the current
        (``switched-away``), then adopt the target and
        emit ``lifecycle.resumed`` (``adopted``). Adoption is attribution only --
        the resolved id becomes ambient so later calls tag correctly; full state
        replay is the slice-3 reducer, so it adopts at phase ``build`` (attach is
        mid-work).
        """
        with self._lock:
            current = self.current
            if current is not None and current.id == lifecycle_id:
                return current
            if current is not None:
                self._pause_locked("switched-away")
            adopted = LifecycleState(
                id=lifecycle_id,
                state="running",
                phase=phase,
                fleeting=False,
                started_at=self._now(),
                enclosure=enclosure,
                repo_id=repo_id,
            )
            self.current = adopted
            self._emit_locked("lifecycle.resumed", "observed", "system", cause="adopted")
            self._start_ticker_locked()
        return adopted

    # --- ambient attribution ----------------------------------------------

    def emit_tool(
        self, tool_name: str, payload: dict[str, Any], *, answer_tokens: int | None = None
    ) -> None:
        """Emit one ``observed`` ``tool.completed`` for the active lifecycle.

        A lifecycle-less call is *dropped*, never attributed to a lifecycle it
        does not belong to (the same honesty rule as "never pretend declared is
        observed"). Emission must never break the tool it observes, so a
        validation or disk error is contained here.

        ``answer_tokens`` is the token count of the answer the agent received
        (MIK-R108): answers no longer carry the envelope count, so the
        application boundary passes the count explicitly instead of this
        reader reading it off the answer. When absent, the whole result's
        stamped count is used as before.
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
                    tokens=(answer_tokens if answer_tokens is not None else payload.get("tokens")),
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
        ``tool.completed`` so it inherits the adopted worktree lifecycle and is
        session-traceable by construction.
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
            raise LifecycleError("no active lifecycle")
        return self.current

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


def reset_ambient() -> None:
    """Stop any ticker and clear the singleton (test isolation)."""
    existing = _AmbientRegistry.instance
    if existing is not None:
        existing.shutdown()
    _AmbientRegistry.instance = None
