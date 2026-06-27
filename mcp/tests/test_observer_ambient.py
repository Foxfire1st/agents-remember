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
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer.ambient import AmbientLifecycle, build_ask
from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import (
    GuardedStartError,
    LifecycleError,
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
        self.amb = AmbientLifecycle(self.store, heartbeat_seconds=3600)

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


class HeartbeatTests(unittest.TestCase):
    def test_ticker_emits_heartbeats_until_stopped(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EventStore(Path(tmp.name))
        amb = AmbientLifecycle(store, heartbeat_seconds=0.02)
        self.addCleanup(amb.shutdown)
        lc = amb.start()
        time.sleep(0.2)
        amb.shutdown()
        time.sleep(0.05)  # let the ticker observe the stop and exit before reading
        kinds = [event.kind for event in store.read(lc.id)]
        self.assertIn("lifecycle.heartbeat", kinds)


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
