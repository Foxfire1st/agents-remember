"""After a host start the tree's agents load in the background, before a click (MIK-R145).

Fail-first: every test below fails without ``cli/paseo_warmup.py``. The bridge is
never touched: resume and listing calls are injected fakes, so no test starts, stops
or restarts a host and none sends it a request.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents_remember.cli.paseo_status import HostStartIdentity, LiveAgents
from agents_remember.cli.paseo_warmup_state import (
    WarmupTracker,
    find_warmup_record,
)
from agents_remember.serving.background_accounting import (
    OWNER_WARMUP,
    BackgroundLedger,
    offload,
)

from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until


def _mem_plenty() -> int | None:
    return 16 * 1024**3


class _BlockedWorkerResumes:
    """Production-style resumes blocking real worker threads through ``offload``.

    ``block`` is the sync bridge call shape: it occupies a worker until
    ``release`` fires. ``resume`` is the production ``_measured_resume`` shape.
    ``active``/``peak`` count occupied workers across handoffs.
    """

    def __init__(self) -> None:
        self.ledger = BackgroundLedger()
        self.active = 0
        self.peak = 0
        self.started: list[str] = []
        self.release = threading.Event()

    def block(self, agent_id: str) -> None:
        self.started.append(agent_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            assert self.release.wait(timeout=HANG_GUARD_SECONDS)
        finally:
            self.active -= 1

    async def resume(self, agent_id: str) -> None:
        await offload(OWNER_WARMUP, self.ledger, self.block, agent_id)


class _FakeHome:
    """A daemon home plus a coordination root with a readable (empty) task root."""

    def __init__(self, root: Path) -> None:
        self.home = root / "daemon-home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.coordination = root / "coordination"
        (self.coordination / "tasks").mkdir(parents=True, exist_ok=True)

    def config(self) -> Any:

        return SimpleNamespace(coordination_root=self.coordination)


def _live(server_id: str, agents: list[str] | None = None, *, start: int = 1) -> Any:

    return LiveAgents(
        reachable=True,
        agent_ids=frozenset(agents or []),
        server_id=server_id,
        start_identity=HostStartIdentity(start, str(start * 100), "sandbox-boot"),
    )


def _down(reason: str = "no route") -> Any:

    return LiveAgents(reachable=False, unreachable_reason=reason)


def _runtime_home(home: Path) -> Any:

    return SimpleNamespace(home=home)


async def _never_resume(_agent_id: str) -> None:
    raise AssertionError("must not be called")


async def _release_then_hang(
    agent_id: str, started: list[str], releases: dict[str, asyncio.Event]
) -> None:
    started.append(agent_id)
    await releases.setdefault(agent_id, asyncio.Event()).wait()


def _staged_resume(
    gate: asyncio.Event, release: asyncio.Event, order: list[str]
) -> Callable[[str], Awaitable[None]]:
    """A resume fake with one slow, one failing and two fast agents."""

    async def staged(agent_id: str) -> None:
        order.append(agent_id)
        if agent_id == "a1":
            gate.set()
            await release.wait()
        elif agent_id == "a2":
            await release.wait()
            raise RuntimeError("host is tired")
        else:
            await asyncio.sleep(0)

    return staged


async def _watch_until_inflight(home: Path, server_id: str, agent_id: str) -> None:
    """Poll the shared record until the agent shows in-flight (or give up loudly)."""

    def is_inflight() -> bool:
        found = find_warmup_record(home, server_id)
        if found is not None:
            watching_probe = WarmupTracker()
            watching_probe.observed_path = found
            probe = watching_probe.status()
            if agent_id in probe.get("inFlight", []):
                return True
        return False

    await async_wait_until(is_inflight, f"{agent_id} in-flight in the shared record")
