"""Exercise reset delivery using real admission, receipt discovery and held messages."""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

from agents_remember.cli import paseo_role_tools as tools
from agents_remember.cli.held_reset_delivery import hold_at_receipt, reset_delivery_ports
from agents_remember.cli.role_held_messages import HeldMessage, held_queue_receipts, hold_message
from agents_remember.controlplane.held_reset_index import HeldResetIndex, ResetTarget
from agents_remember.controlplane.held_store_layout import HELD_QUEUE_MAX_AGE_SECONDS
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import held_reset_scheduler as scheduler
from agents_remember.serving.held_reset_scheduler import ResetAttempt
from test_paseo_role_tools import RoleToolsTestCase, binding

RESET = datetime(2026, 11, 25, 1, tzinfo=UTC)


class _Done(Exception):
    """Stop the scheduler after its pass reaches its next wait."""


class HeldResetDeliveryTests(RoleToolsTestCase):
    def _recipient(self) -> tuple[str, Path, Path]:
        worker = self.start(binding("manager"), "worker")["agentId"]
        config = self.config
        result = tools._locate_recipient_store(
            config, tools.RoleMessageCall(text="lookup", agent_id=worker), worker
        )
        assert result is not None
        self.runtime.agents[worker]["status"] = "idle"
        return worker, result[0], Path(result[2])

    def _messages(self):
        for text in ("first waiting marker", "second waiting marker"):
            yield HeldMessage(
                sender_line="manager",
                sender_agent_id="sender",
                text=text,
                reason="provider-limit",
                limited_until=RESET.isoformat(),
            )

    def test_startup_adopts_an_aged_legacy_wait_and_delivers_in_order_at_reset(self) -> None:
        agent, path, workspace = self._recipient()
        old = datetime.now(UTC) - timedelta(seconds=HELD_QUEUE_MAX_AGE_SECONDS + 1)
        for message in self._messages():
            hold_message(workspace, agent, message)
        from agents_remember.controlplane import held_store_layout as layout  # noqa: PLC0415

        with patch.object(layout, "_now_iso", return_value=old.isoformat()):
            layout.note_held_receipt(self.config, path)
        self.assertEqual(held_queue_receipts(self.config), [])

        index = HeldResetIndex(self.config.coordination_root)
        self.assertIsNone(index.next_attempt())
        ports = reset_delivery_ports(self.config)
        moment = RESET - timedelta(seconds=1)
        sleeps: list[float] = []

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

        async def sleep(delay: float):
            nonlocal moment
            if sleeps:
                raise _Done
            self.assertFalse(any("waiting marker" in text for text in self.received(agent)))
            sleeps.append(delay)
            moment += timedelta(seconds=delay)

        with (
            patch.object(scheduler, "datetime", Clock),
            patch.object(scheduler.asyncio, "sleep", sleep),
            self.assertRaises(_Done),
        ):
            asyncio.run(scheduler.run_held_reset_scheduler(self.config, ports, None))

        self.assertEqual(sleeps, [1.0])
        delivery = [text for text in self.received(agent) if "waiting marker" in text]
        self.assertEqual(len(delivery), 1)
        self.assertLess(delivery[0].index("first waiting"), delivery[0].index("second waiting"))
        self.assertLessEqual((moment - RESET).total_seconds(), 30)
        self.assertIsNone(index.next_attempt())

    def test_an_indexed_wait_survives_a_new_scheduler_and_is_retired_after_ack(self) -> None:
        agent, path, workspace = self._recipient()
        for message in self._messages():
            hold_at_receipt(self.config, path, workspace, agent, message)

        index = HeldResetIndex(self.config.coordination_root)
        self.assertEqual(index.next_attempt(), RESET)
        self.assertEqual(index.due_at(RESET - timedelta(microseconds=1)), [])
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = scheduler.deliver_due_pass(
                index, pool, 2, partial(reset_delivery_ports(self.config).attempt, index), RESET
            )
        self.assertEqual(result, scheduler.ResetPassResult(1, 1, 1))
        self.assertEqual(index.due_at(RESET), [])
        self.assertTrue(any("second waiting" in text for text in self.received(agent)))


def test_live_reset_wins_enrollment_and_a_prior_generation_cannot_retire_it(tmp_path: Path) -> None:
    index = HeldResetIndex(tmp_path)
    early = ResetTarget("agent", tmp_path / "receipt", tmp_path, RESET)
    extended = ResetTarget("agent", early.receipt_path, tmp_path, RESET + timedelta(minutes=1))
    index.schedule(early)
    (old,) = index.due_at(RESET)

    index.schedule(extended)
    index.enroll([early])
    index.finish(old, None)

    assert index.next_attempt() == extended.reset_at
    assert index.due_at(early.reset_at) == []
    assert index.due_at(extended.reset_at)[0].target == extended


def test_pass_overlaps_distinct_recipients_and_stays_at_the_pool_bound(tmp_path: Path) -> None:
    config = McpRuntimeConfig(
        config_path=tmp_path / "mcp.json",
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    index = HeldResetIndex(config.coordination_root)
    targets = {str(uuid.uuid4()) for _ in range(8)}
    for agent in targets:
        index.schedule(ResetTarget(agent, tmp_path / agent, tmp_path, RESET))

    lock = Lock()
    barrier = Barrier(2, timeout=10)
    active: set[str] = set()
    visited: set[str] = set()

    def attempt(entry, _retry_at):
        agent = entry.target.agent_id
        with lock:
            assert agent not in visited
            visited.add(agent)
            active.add(agent)
            assert len(active) <= 2
        barrier.wait()
        with lock:
            active.remove(agent)
        return ResetAttempt(attempted=True, delivered=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = scheduler.deliver_due_pass(index, pool, 2, attempt, RESET)

    assert result == scheduler.ResetPassResult(8, 8, 8)
    assert visited == targets
