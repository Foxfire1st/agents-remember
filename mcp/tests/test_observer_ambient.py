"""Tests for the ambient lifecycle (slices 2b-2c).

Covers the signal state machine, guarded start, switch transitions, the
choke-point emission (tagging an active lifecycle; dropping a lifecycle-less
call), the TTL project-and-prune sweep, and the heartbeat ticker (2b); plus
promotion, adoption/resume via attach, and the save gate on leaving a fleeting
lifecycle (2c).
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer.ambient import (
    AmbientLifecycle,
    AmbientTiming,
    _default_ticker_wait,
    build_ask,
)
from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import (
    TERMINAL_STATES,
    GuardedStartError,
    LifecycleError,
    coerce_end_outcome,
    coerce_phase,
)
from agents_remember.observer.save_gate import (
    CROSS_REPO_SCOPE,
    UNSCOPED_SCOPE,
    SaveGateRequired,
    coerce_save_decision,
    compute_scope,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid


class _AmbientCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.store = EventStore(self.root)
        # A long heartbeat keeps these tests deterministic (no ticker noise).
        self.amb = AmbientLifecycle(self.store, timing=AmbientTiming(heartbeat_seconds=3600))

    def tearDown(self) -> None:
        self.amb.shutdown()
        self._dir.cleanup()

    def kinds(self, lifecycle_id: str) -> list[str]:
        return [event.kind for event in self.store.read(lifecycle_id)]


class StateMachineTests(_AmbientCase):
    def test_start_is_running_fleeting_and_guarded(self) -> None:
        lc = self.amb.start()
        self.assertEqual((lc.state, lc.phase, lc.fleeting), ("running", "request", True))
        with self.assertRaises(GuardedStartError):
            self.amb.start()

    def test_signal_sequence_is_recorded_in_order(self) -> None:
        lc = self.amb.start()
        self.amb.phase("build")
        self.amb.block(kind="question", prompt="?")
        self.amb.resume()
        self.amb.end("completed")
        self.assertEqual(
            self.kinds(lc.id),
            [
                "lifecycle.started",
                "lifecycle.phase-changed",
                "lifecycle.blocked",
                "lifecycle.resumed",
                "lifecycle.ended",
            ],
        )

    def test_block_only_from_running(self) -> None:
        self.amb.start()
        self.amb.block()
        with self.assertRaises(LifecycleError):
            self.amb.block()

    def test_resume_only_from_blocked(self) -> None:
        self.amb.start()
        with self.assertRaises(LifecycleError):
            self.amb.resume()

    def test_await_developer_only_from_running(self) -> None:
        # NOTIFY-AND-CONTINUE turn end (leaf-28): running -> awaiting-developer,
        # emitting lifecycle.awaiting-developer with the summary on its data.
        lc = self.amb.start()
        awaiting = self.amb.await_developer(summary="Turn complete; your move.")
        self.assertEqual(awaiting.state, "awaiting-developer")
        events = self.store.read(lc.id)
        self.assertEqual(events[-1].kind, "lifecycle.awaiting-developer")
        self.assertEqual(events[-1].data["summary"], "Turn complete; your move.")
        # Only running awaits: a second await (now awaiting) raises.
        with self.assertRaises(LifecycleError):
            self.amb.await_developer(summary="again")

    def test_resume_from_await_only_from_awaiting(self) -> None:
        lc = self.amb.start()
        # The strict resume() guard stays blocked-only -- it never resumes an await.
        self.amb.await_developer(summary="s")
        with self.assertRaises(LifecycleError):
            self.amb.resume()
        resumed = self.amb.resume_from_await()
        self.assertEqual(resumed.state, "running")
        self.assertEqual(self.kinds(lc.id)[-1], "lifecycle.resumed")
        # Only awaiting resumes this way: from running it raises.
        with self.assertRaises(LifecycleError):
            self.amb.resume_from_await()

    def test_end_clears_current_and_lifts_the_guard(self) -> None:
        self.amb.start()
        ended = self.amb.end("abandoned")
        self.assertEqual(ended.state, "abandoned")
        self.assertIsNone(self.amb.current)
        self.assertIsNotNone(self.amb.start())

    def test_end_rejects_a_non_terminal_outcome(self) -> None:
        self.amb.start()
        with self.assertRaises(LifecycleError):
            self.amb.end("paused")

    def test_phase_moves(self) -> None:
        self.amb.start()
        self.assertEqual(self.amb.phase("close").phase, "close")

    def test_signal_without_active_lifecycle_raises(self) -> None:
        with self.assertRaises(LifecycleError):
            self.amb.phase("build")


def _string_constants(function: Any) -> set[str]:
    """Every string literal compiled into a function's body, tuples and sets flattened.

    Reads the code object rather than the source text, so comments and the docstring cannot
    trip it: a name only counts here if the function actually *uses* it as a value.
    """
    found: set[str] = set()
    pending: list[Any] = list(function.__code__.co_consts)
    while pending:
        const = pending.pop()
        if isinstance(const, str):
            found.add(const)
        elif isinstance(const, tuple | frozenset | set | list):
            pending.extend(const)
    return found


class EndSignalVocabularyTests(unittest.TestCase):
    """``end`` READS the terminal vocabulary; it no longer keeps a second copy of it.

    Both halves of the classification used to live in this method: a literal
    ``("completed", "abandoned")`` accept-tuple and a hand-written outcome -> state
    conditional. That is a copy, and the failure a copy has is silent -- a third terminal
    state would be one the reducer projects and no session can write, and a renamed one
    would be accepted by the guard and then mapped to the wrong state by the conditional.

    ``test_the_ambient_end_signal_accepts_exactly_the_terminal_states`` (in
    ``test_observer_projection.py``) pins the BEHAVIOUR against today's vocabulary; a copy
    that happens to agree passes it, which is precisely what the removed copy did for as
    long as it existed. These pin the structure instead.
    """

    def test_the_end_signal_names_no_terminal_state_of_its_own(self) -> None:
        named = sorted(_string_constants(AmbientLifecycle.end) & TERMINAL_STATES)
        self.assertEqual(
            named,
            [],
            f"AmbientLifecycle.end hard-codes terminal state(s) {named}; the accept-set is "
            "TERMINAL_STATES and the outcome -> state conversion is coerce_end_outcome, so "
            "the write side has no state name to spell for itself",
        )

    def test_the_end_signal_converts_through_the_vocabulary(self) -> None:
        """The guard alone is not enough: a bare cast would also name no state."""
        self.assertIn("coerce_end_outcome", AmbientLifecycle.end.__code__.co_names)
        self.assertIn("TERMINAL_STATES", AmbientLifecycle.end.__code__.co_names)


class SwitchTests(_AmbientCase):
    def test_switch_from_fleeting_needs_a_save_decision(self) -> None:
        # The save gate is foundational and blocking: leaving an unsaved fleeting
        # lifecycle without a decision raises rather than silently discarding.
        first = self.amb.start()
        with self.assertRaises(SaveGateRequired):
            self.amb.switch()
        self.assertEqual(self.amb.current and self.amb.current.id, first.id)

    def test_switch_discard_ends_then_starts_fresh(self) -> None:
        first = self.amb.start()
        second = self.amb.switch(on_unsaved="discard")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(self.amb.current and self.amb.current.id, second.id)
        self.assertIn("lifecycle.ended", self.kinds(first.id))
        self.assertEqual(second.state, "running")

    def test_switch_save_promotes_then_pauses_the_old(self) -> None:
        first = self.amb.start()
        self.amb.switch(on_unsaved="save")
        kinds = self.kinds(first.id)
        self.assertIn("lifecycle.promoted", kinds)
        self.assertIn("lifecycle.paused", kinds)
        self.assertNotIn("lifecycle.ended", kinds)

    def test_switch_from_persistent_pauses_the_old(self) -> None:
        first = self.amb.start(fleeting=False)
        self.amb.switch()
        self.assertIn("lifecycle.paused", self.kinds(first.id))


class EmissionTests(_AmbientCase):
    def test_emit_tool_tags_the_active_lifecycle_once(self) -> None:
        lc = self.amb.start()
        self.amb.emit_tool("ping", {"tokens": 5, "ok": True})
        tool_events = [e for e in self.store.read(lc.id) if e.kind == "tool.completed"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].trust, "observed")
        self.assertEqual(tool_events[0].actor, "model")
        self.assertEqual(tool_events[0].data["tool"], "ping")

    def test_lifecycle_less_call_is_dropped_not_misattributed(self) -> None:
        self.amb.emit_tool("ping", {"tokens": 5, "ok": True})
        self.assertEqual(self.store.read(None), [])
        self.assertFalse((self.root / "lifecycles").exists())

    def test_end_signal_produces_no_trailing_tool_completed(self) -> None:
        # _tool_payload calls emit_tool after the tool body; lifecycle_end clears
        # the ambient first, so its own tool.completed is dropped by construction.
        lc = self.amb.start()
        self.amb.end("completed")
        self.amb.emit_tool("lifecycle_end", {"tokens": 3, "ok": True})
        self.assertNotIn("tool.completed", self.kinds(lc.id))


class TtlSweepTests(_AmbientCase):
    def _seed(
        self, lifecycle_id: str, *, fleeting: bool, age: timedelta, promoted: bool = False
    ) -> None:
        ts = (datetime.now(UTC) - age).isoformat()
        self.store.append(
            Event(
                id=new_ulid(),
                ts=ts,
                kind="lifecycle.started",
                trust="declared",
                actor="model",
                lifecycleId=lifecycle_id,
                data={"fleeting": fleeting},
            )
        )
        if promoted:
            self.store.append(
                Event(
                    id=new_ulid(),
                    ts=ts,
                    kind="lifecycle.promoted",
                    trust="observed",
                    actor="system",
                    lifecycleId=lifecycle_id,
                )
            )

    def _exists(self, lifecycle_id: str) -> bool:
        return (self.root / "lifecycles" / lifecycle_id).exists()

    def test_dormant_fleeting_is_pruned(self) -> None:
        self._seed("OLD", fleeting=True, age=timedelta(hours=2))
        self.assertEqual(self.amb._reap_stale_fleeting(), ["OLD"])
        self.assertFalse(self._exists("OLD"))

    def test_persistent_is_never_pruned(self) -> None:
        self._seed("KEEP", fleeting=False, age=timedelta(hours=2))
        self.assertEqual(self.amb._reap_stale_fleeting(), [])
        self.assertTrue(self._exists("KEEP"))

    def test_fresh_fleeting_is_kept(self) -> None:
        self._seed("FRESH", fleeting=True, age=timedelta(seconds=5))
        self.amb._reap_stale_fleeting()
        self.assertTrue(self._exists("FRESH"))

    def test_promoted_fleeting_is_kept(self) -> None:
        self._seed("PROMO", fleeting=True, age=timedelta(hours=2), promoted=True)
        self.amb._reap_stale_fleeting()
        self.assertTrue(self._exists("PROMO"))

    def test_start_keeps_the_fresh_current_while_sweeping(self) -> None:
        self._seed("OLD", fleeting=True, age=timedelta(hours=2))
        lc = self.amb.start()  # start triggers the opportunistic sweep
        self.assertFalse(self._exists("OLD"))
        self.assertTrue(self._exists(lc.id))


class _GatedTickerWait:
    """Test-only ticker seam: each call is one tick step the test grants.

    The fake never parks on a Condition/Event handoff; it polls its grant
    counter with tiny bounded sleeps, so the ticker thread's progress is fully
    under the test's control and a set stop flag is observed within
    milliseconds.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.stop_seen = 0
        self.allowed = 0
        self._lock = threading.Lock()

    def __call__(self, stop: threading.Event, _interval: float) -> bool:
        with self._lock:
            self.calls += 1
            call_no = self.calls
            if stop.is_set():
                self.stop_seen += 1
                return True
        while True:
            with self._lock:
                if stop.is_set():
                    self.stop_seen += 1
                    return True
                if call_no <= self.allowed:
                    return False
            time.sleep(0.001)

    def allow(self, count: int) -> None:
        """Grant ``count`` more tick steps to the ticker thread."""
        with self._lock:
            self.allowed += count


class HeartbeatTests(unittest.TestCase):
    def test_ticker_emits_heartbeats_until_stopped(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        waiter = _GatedTickerWait()
        amb = AmbientLifecycle(store, timing=AmbientTiming(heartbeat_seconds=0.02))
        self.addCleanup(amb.shutdown)
        lc = amb.start(ticker_wait=waiter)
        ticker = amb._ticker
        assert ticker is not None

        # Deterministic through the seam: granted ticks run, ungranted ones park
        # on the test gate, so no real-time Event wait decides the outcome.
        waiter.allow(3)
        deadline = time.monotonic() + 5.0
        while store.read_heartbeat(lc.id) is None and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertIsNotNone(store.read_heartbeat(lc.id), "ticker never emitted a heartbeat")

        amb.shutdown()
        deadline = time.monotonic() + 5.0
        while waiter.stop_seen == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertGreaterEqual(waiter.stop_seen, 1)
        ticker.join(timeout=5)
        self.assertFalse(ticker.is_alive(), "ticker thread wedged after stop was set")

    def test_inactive_seconds_tracks_real_activity_not_heartbeats(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        clock = [datetime(2026, 1, 1, tzinfo=UTC)]
        amb = AmbientLifecycle(
            store,
            timing=AmbientTiming(heartbeat_seconds=3600, inactivity_cutoff_seconds=600),
            clock=lambda: clock[0],
        )
        self.addCleanup(amb.shutdown)
        amb.start()  # lifecycle.started records the first real activity at T0
        with amb._lock:
            self.assertEqual(amb._inactive_seconds_locked(), 0.0)
        clock[0] = clock[0] + timedelta(seconds=120)
        with amb._lock:
            self.assertEqual(amb._inactive_seconds_locked(), 120.0)
        # A heartbeat is not activity: it must not reset the inactivity clock.
        with amb._lock:
            amb._emit_locked(
                "lifecycle.heartbeat", "observed", "system", state="running", phase="request"
            )
        clock[0] = clock[0] + timedelta(seconds=60)
        with amb._lock:
            self.assertEqual(amb._inactive_seconds_locked(), 180.0)
        # A real event resets it.
        amb.emit_tool("ping", {"tokens": 1, "ok": True})
        with amb._lock:
            self.assertEqual(amb._inactive_seconds_locked(), 0.0)

    def test_heartbeat_ticker_goes_quiet_when_idle_and_resumes_on_activity(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        clock = [datetime(2026, 1, 1, tzinfo=UTC)]
        waiter = _GatedTickerWait()
        amb = AmbientLifecycle(
            store,
            timing=AmbientTiming(heartbeat_seconds=0.02, inactivity_cutoff_seconds=5.0),
            clock=lambda: clock[0],
        )
        self.addCleanup(amb.shutdown)
        lc = amb.start(ticker_wait=waiter)
        ticker = amb._ticker
        assert ticker is not None

        def heartbeat_ts() -> str | None:
            heartbeat = store.read_heartbeat(lc.id)
            return heartbeat.ts if heartbeat is not None else None

        def wait_until(
            predicate: Callable[[], bool],
            *,
            what: str,
            timeout: float = 5.0,
            interval: float = 0.001,
        ) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(interval)
            self.fail(f"timed out after {timeout:.1f}s waiting for {what}")

        # Grant the first tick step: the first beat lands because activity age 0
        # is inside the cutoff. The seam gates every further step, so no tick
        # can race the clock jump below.
        waiter.allow(1)
        wait_until(lambda: heartbeat_ts() is not None, what="the first heartbeat")
        first = heartbeat_ts()
        assert first is not None

        clock[0] = clock[0] + timedelta(seconds=60)  # jump past the 5s inactivity cutoff
        # Let several ticks run after the jump: each sees the age past the
        # cutoff and must stay quiet, leaving the last heartbeat unchanged.
        calls_before = waiter.calls
        waiter.allow(5)
        wait_until(
            lambda: waiter.calls >= calls_before + 5,
            what="the post-jump idle ticks",
        )
        self.assertEqual(heartbeat_ts(), first)

        amb.emit_tool("ping", {"tokens": 1, "ok": True})  # real activity resets the clock
        waiter.allow(1)
        wait_until(
            lambda: (heartbeat_ts() or "") > (first or ""),
            what="the ticker to resume after activity",
        )
        resumed = heartbeat_ts()
        assert resumed is not None

        # Stop must be observed on the next wake; the loop then exits without a
        # wedged park, and no beat can land after the stop flag is set.
        amb.shutdown()
        wait_until(lambda: waiter.stop_seen >= 1, what="the ticker to observe the stop flag")
        wait_until(lambda: not ticker.is_alive(), what="the ticker thread to exit")
        self.assertEqual(heartbeat_ts(), resumed)

    def test_heartbeat_loop_cannot_wedge_and_honors_stop(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        waiter = _GatedTickerWait()
        amb = AmbientLifecycle(
            store,
            timing=AmbientTiming(heartbeat_seconds=0.02),
        )
        self.addCleanup(amb.shutdown)
        lc = amb.start(ticker_wait=waiter)
        ticker = amb._ticker
        assert ticker is not None

        waiter.allow(1)
        deadline = time.monotonic() + 5.0
        while store.read_heartbeat(lc.id) is None and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertIsNotNone(store.read_heartbeat(lc.id), "ticker never beat through the seam")

        amb.shutdown()
        deadline = time.monotonic() + 5.0
        while waiter.stop_seen == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertGreaterEqual(waiter.stop_seen, 1)
        ticker.join(timeout=5)
        self.assertFalse(ticker.is_alive(), "ticker thread wedged after stop was set")

    def test_default_ticker_wait_returns_after_the_interval(self) -> None:
        stop = threading.Event()
        started = time.monotonic()
        self.assertFalse(_default_ticker_wait(stop, 0.02))
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.01)
        self.assertLess(elapsed, 1.0)

    def test_default_ticker_wait_rechecks_stop_on_every_wake(self) -> None:
        stop = threading.Event()
        results: list[bool | None] = [None]

        def run() -> None:
            results[0] = _default_ticker_wait(stop, 60.0)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.03)  # park well inside the long interval
        stop.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive(), "default ticker wait wedged past its stop recheck")
        self.assertIs(results[0], True)

    def test_heartbeat_tick_returns_false_without_an_active_lifecycle(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        amb = AmbientLifecycle(store, timing=AmbientTiming(heartbeat_seconds=3600))
        self.addCleanup(amb.shutdown)
        self.assertFalse(amb._heartbeat_tick())  # never started -> current is None

    def test_heartbeat_tick_returns_false_for_a_terminal_lifecycle(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        amb = AmbientLifecycle(store, timing=AmbientTiming(heartbeat_seconds=3600))
        self.addCleanup(amb.shutdown)
        started = amb.start()
        terminal = replace(started, state=coerce_end_outcome("completed"))
        with amb._lock:
            amb.current = terminal
        self.assertFalse(amb._heartbeat_tick())

    def test_heartbeat_loop_exits_when_tick_reports_the_lifecycle_is_gone(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        waiter = _GatedTickerWait()
        amb = AmbientLifecycle(store, timing=AmbientTiming(heartbeat_seconds=0.02))
        self.addCleanup(amb.shutdown)
        lc = amb.start(ticker_wait=waiter)
        ticker = amb._ticker
        assert ticker is not None

        waiter.allow(1)
        deadline = time.monotonic() + 5.0
        while store.read_heartbeat(lc.id) is None and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertIsNotNone(store.read_heartbeat(lc.id), "ticker never beat through the seam")

        # Clear the ambient without setting the stop flag: the next granted tick
        # reports the lifecycle is gone and the loop must exit through its
        # explicit return rather than wedge in the wait.
        with amb._lock:
            amb.current = None
        waiter.allow(1)
        ticker.join(timeout=5)
        self.assertFalse(ticker.is_alive(), "loop did not exit when tick reported no lifecycle")


class AskTests(unittest.TestCase):
    def test_build_ask_prunes_and_returns_none_when_empty(self) -> None:
        self.assertIsNone(build_ask(None, None, None))
        self.assertEqual(
            build_ask("decision", "ok?", ["a", "b"]),
            {"kind": "decision", "prompt": "ok?", "options": ["a", "b"]},
        )
        self.assertEqual(build_ask(None, "just a prompt", None), {"prompt": "just a prompt"})

    def test_coerce_phase_validates_at_the_boundary(self) -> None:
        self.assertEqual(coerce_phase("build"), "build")
        with self.assertRaises(LifecycleError):
            coerce_phase("not-a-phase")


class PromoteTests(_AmbientCase):
    def test_promote_makes_persistent_and_records_anchor(self) -> None:
        lc = self.amb.start()
        promoted = self.amb.promote(
            enclosure="/c/series-contract.md", repo_id="agents-remember", scope="agents-remember"
        )
        self.assertFalse(promoted.fleeting)
        self.assertEqual(promoted.enclosure, "/c/series-contract.md")
        self.assertEqual(promoted.scope, "agents-remember")
        promoted_events = [e for e in self.store.read(lc.id) if e.kind == "lifecycle.promoted"]
        self.assertEqual(len(promoted_events), 1)
        self.assertEqual(promoted_events[0].data["scope"], "agents-remember")

    def test_events_after_promotion_carry_the_enclosure_and_repo(self) -> None:
        lc = self.amb.start()
        self.amb.promote(
            enclosure="/c/series-contract.md", repo_id="agents-remember", scope="agents-remember"
        )
        self.amb.emit_tool("ping", {"tokens": 1, "ok": True})
        tool_event = next(e for e in self.store.read(lc.id) if e.kind == "tool.completed")
        self.assertEqual(tool_event.enclosure, "/c/series-contract.md")
        self.assertEqual(tool_event.repoId, "agents-remember")

    def test_promote_requires_an_active_lifecycle(self) -> None:
        with self.assertRaises(LifecycleError):
            self.amb.promote(enclosure="/c/series-contract.md", repo_id="r", scope="r")


class AttachTests(_AmbientCase):
    def test_attach_with_none_active_adopts_and_resumes(self) -> None:
        adopted = self.amb.attach("LC-EXISTING", enclosure="/c/series-contract.md", repo_id="r")
        self.assertEqual(adopted.id, "LC-EXISTING")
        self.assertEqual((adopted.state, adopted.fleeting), ("running", False))
        resumed = next(e for e in self.store.read("LC-EXISTING") if e.kind == "lifecycle.resumed")
        self.assertEqual(resumed.data["cause"], "adopted")

    def test_attach_same_id_is_a_noop(self) -> None:
        self.amb.attach("LC", enclosure="/c/series-contract.md", repo_id="r")
        before = len(self.store.read("LC"))
        same = self.amb.attach("LC", enclosure="/c/series-contract.md", repo_id="r")
        self.assertEqual(same.id, "LC")
        self.assertEqual(len(self.store.read("LC")), before)

    def test_attach_pauses_a_persistent_current_then_adopts(self) -> None:
        first = self.amb.start(fleeting=False)
        self.amb.attach("OTHER", enclosure="/c/other.md", repo_id="r")
        self.assertIn("lifecycle.paused", self.kinds(first.id))
        self.assertEqual(self.amb.current and self.amb.current.id, "OTHER")

    def test_attach_over_unsaved_fleeting_needs_a_decision(self) -> None:
        first = self.amb.start()
        with self.assertRaises(SaveGateRequired):
            self.amb.attach("OTHER", enclosure="/c/other.md", repo_id="r")
        self.assertEqual(self.amb.current and self.amb.current.id, first.id)

    def test_attach_discard_then_adopts(self) -> None:
        first = self.amb.start()
        self.amb.attach("OTHER", enclosure="/c/other.md", repo_id="r", on_unsaved="discard")
        self.assertIn("lifecycle.ended", self.kinds(first.id))
        self.assertEqual(self.amb.current and self.amb.current.id, "OTHER")


class SaveGateUnitTests(unittest.TestCase):
    def test_compute_scope(self) -> None:
        self.assertEqual(compute_scope("agents-remember"), "agents-remember")
        self.assertEqual(compute_scope(None), UNSCOPED_SCOPE)
        self.assertEqual(compute_scope("r", cross_repo=True), CROSS_REPO_SCOPE)

    def test_coerce_save_decision(self) -> None:
        self.assertEqual(coerce_save_decision("save"), "save")
        self.assertEqual(coerce_save_decision("discard"), "discard")
        with self.assertRaises(LifecycleError):
            coerce_save_decision("maybe")


if __name__ == "__main__":
    unittest.main()
