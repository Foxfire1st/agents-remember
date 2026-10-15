"""After a host start the tree's agents load in the background, before a click (MIK-R145).

Fail-first: every test below fails without ``cli/paseo_warmup.py``. The bridge is
never touched: resume and listing calls are injected fakes, so no test starts, stops
or restarts a host and none sends it a request.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agents_remember.cli.paseo_status import HostStartIdentity
from agents_remember.cli.paseo_warmup_run import (
    WarmupRun,
)
from agents_remember.cli.paseo_warmup_state import (
    WarmupTracker,
    claim_warmup_record,
    find_warmup_record,
    read_warmup_priority,
    read_warmup_record,
)
from agents_remember_test_support.testing.paseo_warmup import (
    _mem_plenty,
    _staged_resume,
    _watch_until_inflight,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


class ClaimTests(unittest.TestCase):
    def test_two_servers_notice_one_start_only_one_loads(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            first = claim_warmup_record(
                Path(home), "srv-1", HostStartIdentity(1, "100", "test-boot")
            )
            second = claim_warmup_record(
                Path(home), "srv-1", HostStartIdentity(1, "100", "test-boot")
            )
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            assert first is not None
            self.addCleanup(first.close)
            payload = json.loads(first.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "running")
            self.assertEqual(payload["serverId"], "srv-1")

    def test_a_new_start_of_the_host_loads_again(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            first = claim_warmup_record(
                Path(home), "srv-1", HostStartIdentity(1, "100", "test-boot")
            )
            assert first is not None
            self.addCleanup(first.close)
            first.close()
            second = claim_warmup_record(
                Path(home), "srv-2", HostStartIdentity(2, "200", "test-boot")
            )
            self.assertIsNotNone(second)
            assert second is not None
            self.addCleanup(second.close)


class SharedProgressTests(unittest.TestCase):
    """F28: two servers share one pass through the record file, with reasons in every status."""

    def test_two_servers_share_progress_and_priority_with_no_host(self) -> None:
        async def main() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                owner = WarmupTracker()
                owner.home = home
                loser = WarmupTracker()
                loser.home = home
                path = claim_warmup_record(home, "srv-1", HostStartIdentity(1, "100", "test-boot"))
                assert path is not None
                run = WarmupRun(server_id="srv-1", agent_ids=["a1", "a2", "a3", "a4"])
                owner.run = run
                owner.claim = path
                owner.write_progress()

                gate = asyncio.Event()
                release = asyncio.Event()
                order: list[str] = []

                task = asyncio.create_task(
                    run.run(
                        resume_fn=_staged_resume(gate, release, order),
                        mem_fn=_mem_plenty,
                        priority_fn=lambda: read_warmup_priority(home),
                        progress_fn=owner.write_progress,
                    )
                )
                await asyncio.wait_for(gate.wait(), timeout=HANG_GUARD_SECONDS)
                # The loser watches the shared record mid-pass: running, in-flight shown.
                await _watch_until_inflight(home, "srv-1", "a1")
                found = find_warmup_record(home, "srv-1")
                self.assertIsNotNone(found)
                assert found is not None
                loser.observed_path = found
                watching = loser.status()
                self.assertTrue(watching["running"])
                self.assertIn("a1", watching["inFlight"])
                self.assertEqual(watching["agents"]["a1"], {"state": "loading", "reason": None})
                # The loser's click steers the owner's next dispatch across processes:
                # a4 pending jumps ahead of a3.
                loser.promote(["a4"])
                release.set()
                await asyncio.wait_for(task, timeout=HANG_GUARD_SECONDS)
                self.assertEqual(order[:2], ["a1", "a2"])
                self.assertEqual(order[2], "a4")
                # The finished record keeps every reason after the run is gone.
                owner.note_run_finished(run, path)
                finished = read_warmup_record(path.path)
                assert finished is not None
                self.assertEqual(finished["state"], "finished")
                idle = owner.status()
                self.assertFalse(idle["running"])
                self.assertEqual(
                    idle["agents"]["a2"], {"state": "failed", "reason": "host is tired"}
                )
                self.assertEqual(idle["agents"]["a1"], {"state": "loaded", "reason": None})
                self.assertEqual(set(idle["agents"]), {"a1", "a2", "a3", "a4"})

        asyncio.run(main())
