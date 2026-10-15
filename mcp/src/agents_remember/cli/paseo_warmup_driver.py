"""The serving watch's existing warmup driver and drained retirement."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from agents_remember.cli.paseo_warmup_run import WarmupRun, drain_warmup_tasks
from agents_remember.cli.paseo_warmup_state import TRACKER, WarmupClaim, WarmupTracker


@dataclass(frozen=True)
class _DrivenPass:
    run: WarmupRun
    claim: WarmupClaim
    end_note: dict[str, str | None]


class WarmupDriver:
    """The watch loop's driving tasks: retire old on new starts, end on host-away (F26).

    One driver per watch loop. A new start retires whatever still drives an older one; an
    unreachable poll ends every driving run with its ended record; lifespan exit cancels and
    drains. The per-run concurrency cap stays two (rule 2): this set only bounds how many
    passes drive at once, never how many agents load at once.
    """

    def __init__(self, tracker: WarmupTracker | None = None) -> None:
        self._tracker = tracker if tracker is not None else TRACKER
        self._driving: set[asyncio.Task[None]] = set()
        self._notes: dict[int, _DrivenPass] = {}

    def _reap(self) -> None:
        for task in [one for one in self._driving if one.done()]:
            self._driving.discard(task)
            self._settle_unclosed(
                self._notes.pop(id(task), None), "the driver ended before completing its pass"
            )

    def _settle_unclosed(self, passing: _DrivenPass | None, reason: str) -> None:
        # A task cancelled before its first instruction never entered the producer's
        # try/finally. Its claim belongs to this driver and closes only after drain.
        if passing is not None and not passing.claim.lease.closed:
            self._tracker.note_run_ended(passing.run, passing.claim, reason)

    async def _retire_all(self, reason: str) -> None:
        """Cancel every driving pass as retired for ``reason`` and wait for it to stop."""

        self._reap()
        if not self._driving:
            return
        for task in self._driving:
            note = self._notes.get(id(task))
            if note is not None:
                note.end_note["end_reason"] = reason
            task.cancel()
        retired = set(self._driving)
        self._driving.clear()
        await drain_warmup_tasks(retired)
        for task in retired:
            self._settle_unclosed(self._notes.pop(id(task), None), reason)

    async def noticed(
        self,
        claimed: tuple[WarmupRun, WarmupClaim] | None,
        drive_fn: Callable[[WarmupRun, WarmupClaim, dict[str, str | None]], asyncio.Task[None]],
    ) -> bool:
        """Fold one watch step into the driving set: retire, end, or start driving."""

        self._reap()
        retired = bool(self._driving) and (
            self._tracker.host_away or self._tracker.retire_reason is not None
        )
        if self._tracker.host_away or self._tracker.retire_reason is not None:
            await self._retire_all(self._tracker.retire_reason or "the host went away mid-pass")
            self._tracker.retire_reason = None
        if claimed is not None:
            await self._retire_all("a newer start of the host retired it")
            run, path = claimed
            self._tracker.run = run
            self._tracker.claim = path
            note: dict[str, str | None] = {"end_reason": None}
            task = drive_fn(run, path, note)
            self._driving.add(task)
            self._notes[id(task)] = _DrivenPass(run, path, note)
        return retired

    async def aclose(self) -> None:
        """Cancel every driving pass for lifespan exit and wait for it to stop."""

        await self._retire_all(
            "the product serving lifetime ended before the loading pass finished"
        )
        # Intake runs in a drained worker. Cancellation can discard its return before
        # noticed owns a driver task, while the tracker already owns the admitted claim.
        run, claim = self._tracker.run, self._tracker.claim
        if run is not None and claim is not None and not claim.lease.closed:
            self._tracker.note_run_ended(
                run, claim, "the product serving lifetime ended before the loading pass started"
            )
