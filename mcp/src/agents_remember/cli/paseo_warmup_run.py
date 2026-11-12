"""One bounded warmup pass: candidates, two resume lanes and memory pacing."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# At most two agents load at once (rule 2): one lane still answers a click's own
# resume while the other keeps warming, and two provider app servers starting side by
# side stay under the peak one click causes on its own.
WARMUP_CONCURRENCY = 2
# Before each agent the machine's available memory is read; under this floor the run
# stops and waits (rule 3). The floor is the product's start threshold: 6 GiB, the
# value the packet names for the developer's machine, whose agents have different
# memory footprints.
WARMUP_MEMORY_FLOOR_BYTES = 6 * 1024**3
# A stopped run looks again this often; looking is one read of /proc/meminfo.
WARMUP_MEMORY_RECHECK_SECONDS = 15.0
# A run that never finds memory again gives up for this start of the host after this
# long (rule 3): a full pass of 35 agents at ~25 s each over two lanes is about seven
# minutes worst measured, so thirty minutes is four times headroom before the agents
# stay click-loaded with their pane saying why.
WARMUP_GIVE_UP_SECONDS = 1800.0


# How often the watch loop asks the host for its live listing: host starts are rare
# and a pass takes minutes, so one light listing a minute is prompt enough and costs
# the background account one subprocess spawn a minute.
def collect_warmup_candidates(
    tree_agent_ids: list[str], live_agent_ids: frozenset[str]
) -> tuple[list[str], dict[str, str]]:
    """The agents to load, in tree order, each once, plus who is skipped and why.

    The host listing names unarchived agents only, so intersecting with it leaves
    archived agents out without ever naming them (rule 5): anything the host does
    not list is skipped, never loaded.
    """
    candidates: list[str] = []
    skipped: dict[str, str] = {}
    for agent_id in tree_agent_ids:
        if agent_id in candidates:
            continue
        if agent_id in live_agent_ids:
            candidates.append(agent_id)
        else:
            skipped.setdefault(agent_id, "not listed by the host as unarchived")
    return candidates, skipped


def read_available_memory() -> int | None:
    """The machine's available memory in bytes, or None where it cannot be read."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        name, _, value = line.partition(":")
        if name.strip() == "MemAvailable":
            parts = value.split()
            if len(parts) >= 2 and parts[1] == "kB" and parts[0].isdigit():
                return int(parts[0]) * 1024
    return None


class WarmupRun:
    """One pass over the tree's agents for one start of the host.

    The resume and memory functions are injected: the serving loop passes the
    bridge's ``agent-resume`` and the machine's memory, the tests pass fakes, and
    no test ever reaches a host.
    """

    def __init__(
        self,
        server_id: str,
        agent_ids: list[str],
        *,
        memory_recheck_seconds: float = WARMUP_MEMORY_RECHECK_SECONDS,
        give_up_seconds: float = WARMUP_GIVE_UP_SECONDS,
    ) -> None:
        self.server_id = server_id
        self.total = len(agent_ids)
        self._pending: deque[str] = deque(agent_ids)
        self.role_receipts: dict[str, dict[str, Any]] = {}
        self._in_flight: list[str] = []
        self._memory_recheck_seconds = memory_recheck_seconds
        self._give_up_seconds = give_up_seconds
        self._began_at = time.time()
        self._ended_at: float | None = None
        self.loaded = 0
        self.loaded_ids: list[str] = []
        self.failed: dict[str, str] = {}
        self.skipped: dict[str, str] = {}
        self.memory_stops = 0
        self.lowest_available_bytes: int | None = None
        self.stopped_for_memory = False
        self.memory_detail: str | None = None

    @property
    def finished(self) -> int:
        return self.loaded + len(self.failed) + len(self.skipped)

    @property
    def done(self) -> bool:
        return self._ended_at is not None

    @property
    def in_flight(self) -> list[str]:
        return list(self._in_flight)

    def agent_states(self) -> dict[str, dict[str, str | None]]:
        """Every agent's state and reason, for the pane and the shared record (F28).

        Pending agents are queued, in-flight ones are loading now, loaded ones carry no
        reason, and failed/skipped ones carry the host's or the run's own words. The map
        covers the run's whole candidate list, so a pane never shows a remainder without
        its state.
        """

        states: dict[str, dict[str, str | None]] = {}
        for agent_id in self._pending:
            states[agent_id] = {
                "state": "pending",
                "reason": self.memory_detail if self.stopped_for_memory else None,
            }
        for agent_id in self._in_flight:
            states[agent_id] = {"state": "loading", "reason": None}
        for agent_id in self.loaded_ids:
            states[agent_id] = {"state": "loaded", "reason": None}
        for agent_id, reason in self.failed.items():
            states[agent_id] = {"state": "failed", "reason": reason}
        for agent_id, reason in self.skipped.items():
            states[agent_id] = {"state": "skipped", "reason": reason}
        return states

    def retain_completed(self, record: dict[str, Any] | None) -> None:
        """An incomplete pass retries remaining agents without reloading settled ones."""
        if record is None or not isinstance(record.get("agents"), dict):
            return
        for agent_id, outcome in record["agents"].items():
            if agent_id not in self._pending or not isinstance(outcome, dict):
                continue
            state = outcome.get("state")
            if state == "loaded":
                self.loaded_ids.append(agent_id)
                self.loaded += 1
            elif state in ("failed", "skipped") and isinstance(outcome.get("reason"), str):
                (self.failed if state == "failed" else self.skipped)[agent_id] = outcome["reason"]
            else:
                continue
            self._pending.remove(agent_id)

    def promote(self, agent_id: str) -> None:
        """Load a clicked agent next, before the rest of the list (rule 4).

        Agents already loading keep their slot; the clicked agent moves to the
        front of what has not started. Anything else is a no-op.
        """
        try:
            self._pending.remove(agent_id)
        except ValueError:
            return
        self._pending.appendleft(agent_id)

    def _note_memory(self, available: int | None) -> None:
        if available is not None and (
            self.lowest_available_bytes is None or available < self.lowest_available_bytes
        ):
            self.lowest_available_bytes = available

    async def run(
        self,
        *,
        resume_fn: Callable[[str], Awaitable[None]],
        mem_fn: Callable[[], int | None] = read_available_memory,
        priority_fn: Callable[[], list[str]] | None = None,
        progress_fn: Callable[[], None] | None = None,
    ) -> None:
        tasks: set[asyncio.Task[None]] = set()
        try:
            while self._pending or tasks:
                self._apply_priority(priority_fn)
                while self._pending and len(tasks) < WARMUP_CONCURRENCY:
                    if not await self._memory_allows(mem_fn, progress_fn):
                        break
                    # A foreign click can arrive while memory keeps this admission waiting.
                    self._apply_priority(priority_fn)
                    agent_id = self._pending.popleft()
                    self._in_flight.append(agent_id)
                    tasks.add(asyncio.create_task(self._load_one(agent_id, resume_fn, mem_fn)))
                self._report_progress(progress_fn)
                if not tasks:
                    break
                _done, pending_tasks = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                tasks = set(pending_tasks)
                self._report_progress(progress_fn)
        finally:
            # Cancel what is still loading and wait for it to stop (F26): a cancelled run
            # leaves no resume behind it.
            for task in tasks:
                task.cancel()
            await drain_warmup_tasks(tasks)
            self._ended_at = time.time()
            self.stopped_for_memory = False
            self._report_progress(progress_fn)

    @staticmethod
    def _report_progress(progress_fn: Callable[[], None] | None) -> None:
        """Share progress, never letting the sharing break the pass (F28)."""

        if progress_fn is None:
            return
        with contextlib.suppress(Exception):
            progress_fn()

    def _apply_priority(self, priority_fn: Callable[[], list[str]] | None) -> None:
        """Front-load agents another server's clicks asked for (F28)."""

        if priority_fn is None or not self._pending:
            return
        try:
            ordered = priority_fn()
        except OSError:
            return
        pending = set(self._pending)
        for agent_id in reversed([one for one in ordered if one in pending]):
            self._pending.remove(agent_id)
            self._pending.appendleft(agent_id)

    async def _memory_allows(
        self, mem_fn: Callable[[], int | None], progress_fn: Callable[[], None] | None
    ) -> bool:
        """True when the next agent may start; waits out short memory dips.

        Unknown memory (F27) never starts a load: the start threshold was not established,
        so the pass waits and rechecks exactly like a below-floor reading, gives up on the
        same named timeout, and skips the pending agents with a note that says the reading
        was unavailable rather than low.
        """
        while True:
            available = mem_fn()
            self._note_memory(available)
            if available is not None and available >= WARMUP_MEMORY_FLOOR_BYTES:
                self.stopped_for_memory = False
                self.memory_detail = None
                return True
            self.memory_stops += 1
            self.stopped_for_memory = True
            self.memory_detail = (
                "memory reading unavailable; waiting without starting a load"
                if available is None
                else "available memory is below6 GiB; waiting without starting a load"
            )
            if time.time() - self._began_at >= self._give_up_seconds:
                if available is None:
                    note = (
                        "memory reading unavailable; the start threshold was never "
                        "established, so nothing started: "
                        "the agent loads at its first click"
                    )
                else:
                    note = (
                        "memory stayed under the start threshold; "
                        "the agent loads at its first click"
                    )
                for agent_id in list(self._pending):
                    self.skipped.setdefault(agent_id, note)
                self._pending.clear()
                return False
            self._report_progress(progress_fn)
            await asyncio.sleep(self._memory_recheck_seconds)

    async def _load_one(
        self,
        agent_id: str,
        resume_fn: Callable[[str], Awaitable[None]],
        mem_fn: Callable[[], int | None],
    ) -> None:
        try:
            await resume_fn(agent_id)
        except asyncio.CancelledError:
            self._pending.appendleft(agent_id)
            raise
        except Exception as error:
            # The host's own words are the note: a failure is recorded, never raised.
            # One unloadable agent never ends the pass (rule 5).
            self.failed[agent_id] = str(error) or type(error).__name__
        else:
            self.loaded += 1
            self.loaded_ids.append(agent_id)
        finally:
            self._note_memory(mem_fn())
            with contextlib.suppress(ValueError):
                self._in_flight.remove(agent_id)

    def record(self) -> dict[str, Any]:
        """The record of rule 7: when, how many, who was skipped and why."""
        return {
            "serverId": self.server_id,
            "beganAt": self._began_at,
            "endedAt": self._ended_at,
            "total": self.total,
            "finished": self.finished,
            "inFlight": self.in_flight,
            "pending": list(self._pending),
            "stoppedForMemory": self.stopped_for_memory,
            "loaded": self.loaded,
            "failed": dict(self.failed),
            "skipped": dict(self.skipped),
            "agents": self.agent_states(),
            "lowestAvailableBytes": self.lowest_available_bytes,
            "memoryStops": self.memory_stops,
        }

    def line(self) -> str:
        """The one line the dashboard shows while the loading runs (rule 6)."""

        if self.stopped_for_memory:
            return (
                "loading agents after the host's start: "
                f"paused for memory, {self.finished} of {self.total}"
            )
        return f"loading agents after the host's start: {self.finished} of {self.total}"


async def drain_warmup_tasks(tasks: set[asyncio.Task[None]]) -> None:
    """Wait through repeated cancellation until every real resume has retired."""
    while any(not task.done() for task in tasks):
        try:
            await asyncio.wait(tasks)
        except asyncio.CancelledError:
            continue
    for task in tasks:
        if not task.cancelled():
            task.exception()
