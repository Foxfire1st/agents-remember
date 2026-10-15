"""After a host start the tree's agents load in the background, before a click (MIK-R145).

Fail-first: every test below fails without ``cli/paseo_warmup.py``. The bridge is
never touched: resume and listing calls are injected fakes, so no test starts, stops
or restarts a host and none sends it a request.
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import agents_remember.cli.paseo_warmup as warmup
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_warmup import check_host_once
from agents_remember.cli.paseo_warmup_state import (
    WarmupTracker,
    read_warmup_record,
)
from agents_remember_test_support.testing.paseo_warmup import (
    _down,
    _FakeHome,
    _live,
    _mem_plenty,
    _runtime_home,
)


class StartIdentityTests(unittest.TestCase):
    """F25: the start identity discriminates restarts; failures never consume a start."""

    def _check(self, config: Any, *, tracker: WarmupTracker):
        result = check_host_once(config, tracker=tracker)
        if result is not None:
            self.addCleanup(result[1].close)
        return result

    def test_a_failed_intake_does_not_consume_the_start(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            env = _FakeHome(Path(tmp))
            tracker = WarmupTracker()
            flaky = {"fail": True}

            def intake(config: Any, live: Any = None) -> list[str]:
                if flaky["fail"]:
                    raise PaseoBridgeFailure("daemon_unreachable", "host flaked mid-intake")
                return ["a1"]

            with (
                patch.object(warmup, "read_live_agents", return_value=_live("srv-1", ["a1"])),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", side_effect=intake),
            ):
                self.assertIsNone(self._check(env.config(), tracker=tracker))
            # Not consumed: no id marked, no record left behind, failure stated.
            self.assertIsNone(tracker.last_server_id)
            self.assertIsNone(tracker.run)
            self.assertIn("intake failed", tracker.intake_detail or "")
            self.assertEqual(list(env.home.glob("ar-warmup-*.json")), [])
            # The next poll retries the same start and takes it.
            flaky["fail"] = False
            with (
                patch.object(warmup, "read_live_agents", return_value=_live("srv-1", ["a1"])),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", side_effect=intake),
            ):
                claimed = self._check(env.config(), tracker=tracker)
            self.assertIsNotNone(claimed)
            self.assertEqual(tracker.last_server_id, "srv-1")
            assert claimed is not None
            self.assertEqual(claimed[0].server_id, "srv-1")

    def test_a_restart_then_poll_reruns_and_a_repeat_poll_does_not(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            env = _FakeHome(Path(tmp))
            tracker = WarmupTracker()
            listings: list[Any] = [_live("srv-1", [])]

            def listing(config: Any) -> Any:
                return listings[0]

            with (
                patch.object(warmup, "read_live_agents", side_effect=listing),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", return_value=[]),
            ):
                first = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(first)
                # The same start seen again starts nothing.
                self.assertIsNone(self._check(env.config(), tracker=tracker))
                # The same server id answers with a different OS process birth.
                listings[0] = _down()
                self.assertIsNone(self._check(env.config(), tracker=tracker))
                listings[0] = _live("srv-1", [], start=2)
                if tracker.run is not None:
                    tracker.note_run_ended(
                        tracker.run, tracker.claim, "older process start retired"
                    )
                second = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(second)
                assert first is not None and second is not None
                self.assertIsNot(first[0], second[0])

    def test_a_new_server_id_starts_without_any_outage(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            env = _FakeHome(Path(tmp))
            tracker = WarmupTracker()
            listings: list[Any] = [_live("srv-1", [])]

            def listing(config: Any) -> Any:
                return listings[0]

            with (
                patch.object(warmup, "read_live_agents", side_effect=listing),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", return_value=[]),
            ):
                first = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(first)
                assert first is not None
                tracker.note_run_ended(first[0], first[1], "older process start retired")
                listings[0] = _live("srv-2", [], start=2)
                rerun = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(rerun)
                assert rerun is not None
                self.assertEqual(rerun[0].server_id, "srv-2")

    def test_a_restart_after_completion_requalifies_and_a_reread_does_not(self) -> None:
        """F25: a new process birth requalifies after the previous pass completed.

        The server id stays the same; its authoritative birth changes. A reread
        of the same birth starts nothing, independently of any observed outage.
        """

        async def instant(agent_id: str) -> None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            env = _FakeHome(Path(tmp))
            tracker = WarmupTracker()
            listings: list[Any] = [_live("srv-1", [])]

            def listing(config: Any) -> Any:
                return listings[0]

            with (
                patch.object(warmup, "read_live_agents", side_effect=listing),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", return_value=[]),
            ):
                first = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(first)
                assert first is not None
                asyncio.run(first[0].run(resume_fn=instant, mem_fn=_mem_plenty))
                tracker.note_run_finished(first[0], first[1])
                assert tracker.finished_runs == 1
                filed = read_warmup_record(first[1].path)
                assert filed is not None
                self.assertEqual(filed.get("state"), "finished")
                # A reread of the same start, with no outage, starts nothing.
                self.assertIsNone(self._check(env.config(), tracker=tracker))
                # An outage alone proves nothing; the next reply has a new birth.
                listings[0] = _down()
                self.assertIsNone(self._check(env.config(), tracker=tracker))
                listings[0] = _live("srv-1", [], start=2)
                if tracker.run is not None:
                    tracker.note_run_ended(
                        tracker.run, tracker.claim, "older process start retired"
                    )
                second = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(second)
                assert second is not None
                self.assertIsNot(first[0], second[0])
                self.assertEqual(second[0].server_id, "srv-1")
                record = read_warmup_record(second[1].path)
                assert record is not None
                self.assertEqual(record.get("state"), "running")

    def test_a_different_process_birth_requalifies_without_an_outage(self) -> None:
        """F25: the answering process's birth requalifies without an outage edge.

        A fresh product server can distinguish a new host process from the
        settled record alone. A reread of the same exact birth stays quiet.
        """

        async def instant(agent_id: str) -> None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            env = _FakeHome(Path(tmp))
            tracker = WarmupTracker()
            listing = [_live("srv-1", [])]
            with (
                patch.object(warmup, "read_live_agents", side_effect=lambda _: listing[0]),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", return_value=[]),
            ):
                first = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(first)
                assert first is not None
                asyncio.run(first[0].run(resume_fn=instant, mem_fn=_mem_plenty))
                tracker.note_run_finished(first[0], first[1])
                ended = read_warmup_record(first[1].path)
                assert ended is not None
                ended_at = ended.get("endedAt")
                assert isinstance(ended_at, float)
                # Same birth stays quiet; a different birth requalifies.
                listing[0] = _live("srv-1", [], start=1)
                self.assertIsNone(self._check(env.config(), tracker=tracker))
                listing[0] = _live("srv-1", [], start=2)
                second = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(second)
                assert second is not None
                self.assertIsNot(first[0], second[0])
                self.assertEqual(second[0].server_id, "srv-1")
                # Exactly one run per real start: the reread stays quiet.
                self.assertIsNone(self._check(env.config(), tracker=tracker))

        with tempfile.TemporaryDirectory() as tmp:
            env = _FakeHome(Path(tmp))
            tracker = WarmupTracker()
            listing = [_live("srv-1", [])]
            with (
                patch.object(warmup, "read_live_agents", side_effect=lambda _: listing[0]),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", return_value=[]),
            ):
                first = self._check(env.config(), tracker=tracker)
                self.assertIsNotNone(first)
                assert first is not None
                asyncio.run(first[0].run(resume_fn=instant, mem_fn=_mem_plenty))
                tracker.note_run_finished(first[0], first[1])
                # The repeated listing still names the same authoritative birth.
                self.assertIsNone(self._check(env.config(), tracker=tracker))

    def test_one_successor_per_restart_across_two_servers(self) -> None:
        """F25: two servers see one post-outage start; exactly one loads."""

        with tempfile.TemporaryDirectory() as tmp:
            env = _FakeHome(Path(tmp))
            first_tracker = WarmupTracker()
            second_tracker = WarmupTracker()
            listings: list[Any] = [_live("srv-1", [])]

            def listing(config: Any) -> Any:
                return listings[0]

            with (
                patch.object(warmup, "read_live_agents", side_effect=listing),
                patch.object(
                    warmup, "require_bridge_runtime", return_value=_runtime_home(env.home)
                ),
                patch.object(warmup, "tree_agent_ids", return_value=[]),
            ):
                first = self._check(env.config(), tracker=first_tracker)
                self.assertIsNotNone(first)
                assert first is not None
                first_tracker.note_run_finished(first[0], first[1])
                # The other server consumes the settled outcome without running.
                self.assertIsNone(self._check(env.config(), tracker=second_tracker))
                assert second_tracker.last_record is not None
                self.assertEqual(second_tracker.last_record.get("state"), "finished")
                # An outage and return with the same id requalifies the start,
                # but the fresh claim still admits exactly one loader.
                listings[0] = _down()
                self.assertIsNone(self._check(env.config(), tracker=first_tracker))
                self.assertIsNone(self._check(env.config(), tracker=second_tracker))
                listings[0] = _live("srv-1", [], start=2)
                winner = self._check(env.config(), tracker=first_tracker)
                loser = self._check(env.config(), tracker=second_tracker)
                self.assertIsNotNone(winner)
                self.assertIsNone(loser)
                self.assertIsNotNone(second_tracker.observed_path)


class IntakeTests(unittest.TestCase):
    """F27: unknown memory starts nothing, says so distinctly, and gives up in bounds."""

    def _check(self, config: Any, *, tracker: WarmupTracker):
        result = check_host_once(config, tracker=tracker)
        if result is not None:
            self.addCleanup(result[1].close)
        return result

    def test_unknown_memory_starts_nothing_and_says_so(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "daemon-home"
            home.mkdir()
            coordination = root / "coordination"
            coordination.mkdir()  # no tasks/ : unknown, not empty

            config = SimpleNamespace(coordination_root=coordination)
            tracker = WarmupTracker()
            with (
                patch.object(warmup, "read_live_agents", return_value=_live("srv-1", [])),
                patch.object(warmup, "require_bridge_runtime", return_value=_runtime_home(home)),
            ):
                self.assertIsNone(self._check(config, tracker=tracker))
            self.assertIsNone(tracker.last_server_id)
            self.assertIsNone(tracker.run)
            self.assertIsNone(tracker.host_detail)
            assert tracker.intake_detail is not None
            self.assertIn("memory", tracker.intake_detail)
            status = tracker.status()
            self.assertEqual(status["intakeDetail"], tracker.intake_detail)
            self.assertFalse(status["running"])

    def test_intake_gives_up_after_the_window_and_keeps_retrying_before_it(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "daemon-home"
            home.mkdir()
            coordination = root / "coordination"
            coordination.mkdir()

            config = SimpleNamespace(coordination_root=coordination)
            tracker = WarmupTracker()
            with (
                patch.object(warmup, "read_live_agents", return_value=_live("srv-1", [])),
                patch.object(warmup, "require_bridge_runtime", return_value=_runtime_home(home)),
            ):
                self.assertIsNone(self._check(config, tracker=tracker))
                self.assertIsNone(tracker.last_server_id)
                # Past the window the same failure abandons the start with its reason.
                tracker.intake_first_failure_at = 0.0
                self.assertIsNone(self._check(config, tracker=tracker))
            self.assertEqual(tracker.last_server_id, "srv-1")
            assert isinstance(tracker.last_record, dict)
            self.assertEqual(tracker.last_record.get("state"), "given-up")
            self.assertIn("memory", str(tracker.last_record.get("reason", "")))


class BridgeCommandTests(unittest.TestCase):
    def test_resume_uses_agent_resume_only(self) -> None:
        # The resume path reaches the host through one bridge command only.
        text = inspect.getsource(warmup)
        self.assertIn('"agent-resume"', text)
        self.assertNotIn('"refresh-agent"', text)
        self.assertNotIn('"refresh_agent"', text)
