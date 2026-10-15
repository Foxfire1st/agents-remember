"""After a host start the tree's agents load in the background, before a click (MIK-R145).

Fail-first: every test below fails without ``cli/paseo_warmup.py``. The bridge is
never touched: resume and listing calls are injected fakes, so no test starts, stops
or restarts a host and none sends it a request.
"""

from __future__ import annotations

import asyncio
import unittest

from agents_remember.cli.paseo_warmup_run import (
    WARMUP_CONCURRENCY,
    WarmupRun,
    collect_warmup_candidates,
)
from agents_remember_test_support.testing.paseo_warmup import (
    _mem_plenty,
    _never_resume,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


class CandidateTests(unittest.TestCase):
    def test_archived_agents_are_left_out(self) -> None:
        # The host listing names unarchived agents only; a tree agent the host
        # does not list (archived, missing, or beyond an incomplete listing) is
        # skipped with its reason, never loaded.
        candidates, skipped = collect_warmup_candidates(
            ["a1", "a2", "archived-9"], frozenset({"a1", "a2"})
        )
        self.assertEqual(candidates, ["a1", "a2"])
        self.assertEqual(skipped, {"archived-9": "not listed by the host as unarchived"})

    def test_tree_order_is_kept_duplicates_run_once(self) -> None:
        candidates, skipped = collect_warmup_candidates(
            ["a1", "a2", "a1", "a3"], frozenset({"a1", "a2", "a3"})
        )
        self.assertEqual(candidates, ["a1", "a2", "a3"])
        self.assertEqual(skipped, {})


class ConcurrencyTests(unittest.TestCase):
    def test_at_most_two_agents_load_at_once(self) -> None:
        self.assertEqual(WARMUP_CONCURRENCY, 2)
        in_flight = 0
        peak = 0
        loaded: list[str] = []

        async def resume(agent_id: str) -> None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)  # Yield one scheduler turn while both slots are occupied.
            in_flight -= 1
            loaded.append(agent_id)

        run = WarmupRun(server_id="srv-1", agent_ids=[f"a{i}" for i in range(6)])
        asyncio.run(run.run(resume_fn=resume, mem_fn=_mem_plenty))
        self.assertEqual(peak, 2)
        self.assertEqual(sorted(loaded), [f"a{i}" for i in range(6)])
        self.assertEqual(run.loaded, 6)

    def test_a_clicked_agent_goes_first(self) -> None:
        started: list[str] = []
        gate = asyncio.Event()
        release = asyncio.Event()

        async def resume(agent_id: str) -> None:
            started.append(agent_id)
            if len(started) == WARMUP_CONCURRENCY:
                gate.set()
            await release.wait()

        run = WarmupRun(server_id="srv-1", agent_ids=["a1", "a2", "a3", "a4"])

        async def main() -> None:
            task = asyncio.create_task(run.run(resume_fn=resume, mem_fn=_mem_plenty))
            await asyncio.wait_for(gate.wait(), timeout=HANG_GUARD_SECONDS)
            run.promote("a4")
            release.set()
            await asyncio.wait_for(task, timeout=HANG_GUARD_SECONDS)

        asyncio.run(main())
        # a4 loads third: the two in flight keep their slot, the clicked agent
        # is next before the rest of the list.
        self.assertEqual(started[:2], ["a1", "a2"])
        self.assertEqual(started[2], "a4")

    def test_only_timeline_requests_reach_the_host(self) -> None:
        # The run resumes through one injected call per agent; the module's own
        # resume path must call nothing but agent-resume (covered against the
        # real bridge wiring by BridgeCommandTests).
        calls: list[str] = []

        async def resume(agent_id: str) -> None:
            calls.append(agent_id)

        run = WarmupRun(server_id="srv-1", agent_ids=["a1", "a2"])
        asyncio.run(run.run(resume_fn=resume, mem_fn=_mem_plenty))
        self.assertEqual(calls, ["a1", "a2"])


class FailureTests(unittest.TestCase):
    def test_a_failed_agent_is_noted_with_the_hosts_words_and_the_next_follows(self) -> None:
        async def resume(agent_id: str) -> None:
            if agent_id == "a2":
                raise RuntimeError("the host refused: harness gone")

        run = WarmupRun(server_id="srv-1", agent_ids=["a1", "a2", "a3"])
        asyncio.run(run.run(resume_fn=resume, mem_fn=_mem_plenty))
        self.assertEqual(run.loaded, 2)
        self.assertEqual(run.failed, {"a2": "the host refused: harness gone"})
        self.assertEqual(run.record()["failed"], {"a2": "the host refused: harness gone"})


class MemoryTests(unittest.TestCase):
    def test_loading_stops_under_the_threshold_and_goes_on_above_it(self) -> None:
        mem = iter([16 * 1024**3, 1 * 1024**3, 1 * 1024**3, 16 * 1024**3])
        loaded: list[str] = []

        async def resume(agent_id: str) -> None:
            loaded.append(agent_id)

        run = WarmupRun(
            server_id="srv-1",
            agent_ids=["a1", "a2"],
            memory_recheck_seconds=0,
            give_up_seconds=60,
        )
        asyncio.run(run.run(resume_fn=resume, mem_fn=lambda: next(mem, 16 * 1024**3)))
        self.assertEqual(loaded, ["a1", "a2"])
        self.assertTrue(run.memory_stops >= 1)

    def test_loading_gives_up_after_the_named_time(self) -> None:
        run = WarmupRun(
            server_id="srv-1",
            agent_ids=["a1", "a2"],
            memory_recheck_seconds=0,
            give_up_seconds=0,
        )
        asyncio.run(run.run(resume_fn=_never_resume, mem_fn=lambda: 1 * 1024**3))
        self.assertEqual(run.loaded, 0)
        self.assertIn("a1", run.skipped)
        self.assertIn("memory", run.skipped["a1"])

    def test_unknown_memory_starts_nothing_and_says_unavailable(self) -> None:
        """MIK-R145 (F27): unreadable memory never reaches the resume callback.

        Unknown readings wait out the named timeout exactly like below-floor ones, then
        skip with a note that names unavailability -- distinctly from a low reading.
        """
        loaded: list[str] = []

        async def resume(agent_id: str) -> None:
            loaded.append(agent_id)

        run = WarmupRun(
            server_id="srv-1",
            agent_ids=["a1", "a2"],
            memory_recheck_seconds=0,
            give_up_seconds=0,
        )
        asyncio.run(run.run(resume_fn=resume, mem_fn=lambda: None))
        self.assertEqual(loaded, [])
        self.assertEqual(run.loaded, 0)
        self.assertIn("unavailable", run.skipped["a1"])
        self.assertNotIn("under the start threshold", run.skipped["a1"])
        self.assertIsNone(run.lowest_available_bytes)

    def test_unknown_memory_then_readable_loads_again(self) -> None:
        """Unknown, then a healthy reading: the pass recovers and loads (F27)."""
        mem = iter([None, None, 16 * 1024**3])
        loaded: list[str] = []

        async def resume(agent_id: str) -> None:
            loaded.append(agent_id)

        run = WarmupRun(
            server_id="srv-1",
            agent_ids=["a1"],
            memory_recheck_seconds=0,
            give_up_seconds=60,
        )
        asyncio.run(run.run(resume_fn=resume, mem_fn=lambda: next(mem, 16 * 1024**3)))
        self.assertEqual(loaded, ["a1"])
        self.assertEqual(run.skipped, {})

    def test_unknown_memory_then_low_reports_low(self) -> None:
        """Unknown, then a below-floor reading: the give-up note names the low floor (F27)."""
        mem = iter([None, 1 * 1024**3])
        run = WarmupRun(
            server_id="srv-1",
            agent_ids=["a1"],
            memory_recheck_seconds=0,
            give_up_seconds=0.2,
        )
        asyncio.run(run.run(resume_fn=_never_resume, mem_fn=lambda: next(mem, 1 * 1024**3)))
        self.assertEqual(run.loaded, 0)
        self.assertIn("under the start threshold", run.skipped["a1"])

    def test_lowest_memory_is_recorded(self) -> None:
        mem = iter([10 * 1024**3, 8 * 1024**3])

        async def resume(agent_id: str) -> None:
            pass

        run = WarmupRun(server_id="srv-1", agent_ids=["a1"])
        asyncio.run(run.run(resume_fn=resume, mem_fn=lambda: next(mem, 12 * 1024**3)))
        record = run.record()
        self.assertEqual(record["lowestAvailableBytes"], 8 * 1024**3)
