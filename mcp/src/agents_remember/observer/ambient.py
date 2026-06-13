"""The ambient lifecycle: the process-scoped owner of the current lifecycle.

One stdio MCP server process is approximately one harness session, so the
current lifecycle is a process singleton (design §1.6). ``lifecycle_start`` /
``switch_lifecycle`` set it; every subsequent tool response is auto-tagged at
the ``_tool_payload`` choke point via :func:`ambient`. The singleton owns:

* the signal state machine (start/block/resume/end/switch/phase),
* event emission (signals ``declared``; the tool choke point ``observed``),
* a heartbeat ticker that generalizes the ``setup_progress`` daemon-thread idiom
  so a dead session is detectable by a stale last heartbeat, and
* an opportunistic project-and-prune TTL sweep for dormant fleeting lifecycles.

The state *types* live in :mod:`agents_remember.observer.lifecycle_state`; this
module is the behavior + threading + the process-global registry.
"""

from __future__ import annotations

import contextlib
import shutil
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from agents_remember.observer.events import Actor, Event, Trust
from agents_remember.observer.lifecycle_state import (
    INITIAL_PHASE,
    GuardedStartError,
    LifecycleError,
    LifecycleState,
    Phase,
    State,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid

# Configuration defaults (design §8 assigns these to the lifecycle-tools slice).
# 15s beat mirrors the proven setup-progress cadence; 180s stale is the
# projection's paused-by-dormancy threshold (consumed by the slice-3 reducer);
# 3600s is the fleeting-only TTL after which a dormant, never-promoted lifecycle
# is pruned.
HEARTBEAT_SECONDS = 15.0
STALE_AFTER_SECONDS = 180.0
TTL_SECONDS = 3600.0

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


def _default_clock() -> datetime:
    return datetime.now(UTC)


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
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        ttl_seconds: float = TTL_SECONDS,
        clock: Clock = _default_clock,
        id_factory: IdFactory = new_ulid,
    ) -> None:
        self._store = store
        self._heartbeat_seconds = heartbeat_seconds
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._mint = id_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None
        self.current: LifecycleState | None = None

    # --- signals -----------------------------------------------------------

    def start(self, *, fleeting: bool = True, phase: Phase = INITIAL_PHASE) -> LifecycleState:
        """Guarded start: mint a lifecycle and become ``running`` (§1.3)."""
        with self._lock:
            if self.current is not None:
                raise GuardedStartError(self.current.id)
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
                raise LifecycleError(f"cannot block from state {current.state!r}; only running blocks")
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

    def end(self, outcome: str) -> LifecycleState:
        """Terminal ``lifecycle.ended``; clears the ambient (§1.2).

        Emitted before the ambient is cleared, so the choke point sees no
        current afterward and the end call itself produces no ``tool.completed``
        -- the terminal signal is the record, not a redundant tool event. The
        returned snapshot is the ended lifecycle's terminal state.
        """
        if outcome not in ("completed", "abandoned"):
            raise LifecycleError(f"end outcome must be completed|abandoned, got {outcome!r}")
        terminal: State = "completed" if outcome == "completed" else "abandoned"
        with self._lock:
            current = self._require_active()
            self._emit_locked("lifecycle.ended", "declared", "model", outcome=outcome)
            self._stop_ticker_locked()
            self.current = None
            return replace(current, state=terminal)

    def phase(self, phase: Phase) -> LifecycleState:
        """Move along the orthogonal phase axis (§1.4); any non-terminal state."""
        with self._lock:
            current = self._require_active()
            self.current = replace(current, phase=phase)
            self._emit_locked("lifecycle.phase-changed", "declared", "model", phase=phase)
            return self.current

    def switch(self, *, fleeting: bool = True, phase: Phase = INITIAL_PHASE) -> LifecycleState:
        """Transition the current lifecycle away, then mint a fresh one.

        Slice scope: this implements the *creates-new* half of ``switch`` plus
        the current-lifecycle transition -- a persistent ``running`` lifecycle is
        paused (``switched-away``); a fleeting one is discarded (ended
        ``abandoned``). Resuming an *existing* target (state replay) and the
        save-gate *promote* path arrive with the projection reducer and the
        worktree-contract wiring; the model never handles the target id.
        """
        with self._lock:
            self._transition_away_locked()
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

    def shutdown(self) -> None:
        """Stop the heartbeat ticker (process teardown / test isolation)."""
        with self._lock:
            self._stop_ticker_locked()

    # --- internals ---------------------------------------------------------

    def _require_active(self) -> LifecycleState:
        if self.current is None:
            raise LifecycleError("no active lifecycle; start or switch into one first")
        return self.current

    def _transition_away_locked(self) -> None:
        """Pause a persistent current, discard a fleeting one (lock held)."""
        current = self.current
        if current is None:
            return
        if current.fleeting:
            self._emit_locked("lifecycle.ended", "declared", "model", outcome="abandoned")
        else:
            self._emit_locked("lifecycle.paused", "observed", "system", cause="switched-away")
        self._stop_ticker_locked()
        self.current = None

    def _emit_locked(self, kind: str, trust: Trust, actor: Actor, **data: Any) -> None:
        current = self.current
        if current is None:  # pragma: no cover - guarded by callers
            return
        self._store.append(
            Event(
                id=self._mint(),
                ts=self._now(),
                kind=kind,
                trust=trust,
                actor=actor,
                lifecycleId=current.id,
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
        while not stop.wait(interval):
            with self._lock:
                current = self.current
                if current is None or current.is_terminal:
                    return
                self._emit_locked(
                    "lifecycle.heartbeat",
                    "observed",
                    "system",
                    state=current.state,
                    phase=current.phase,
                )

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
        age = _age_seconds(events[-1].ts, now)
        return age is not None and age > self._ttl_seconds


def build_ask(
    kind: str | None, prompt: str | None, options: list[str] | None
) -> dict[str, Any] | None:
    """The structured ask carried on a block, or ``None`` when none was given.

    Single source for the ask shape: ``lifecycle_block`` records it on the
    event and echoes it on the response from this one builder.
    """
    ask = {"kind": kind, "prompt": prompt, "options": options}
    pruned = {key: value for key, value in ask.items() if value is not None}
    return pruned or None


def _age_seconds(stamp: str, now: datetime) -> float | None:
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds()


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
