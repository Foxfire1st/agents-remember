"""Page-interest landing probe due times, reclaimed to the current contract set."""

from __future__ import annotations

import json
from collections.abc import Hashable, Set
from dataclasses import dataclass
from datetime import datetime, timedelta

from agents_remember.worktrees.worktree_contract import WorktreeContract

_ANSWER_FIELDS = frozenset({"kind", "label", "state", "factState", "detail", "at"})


@dataclass(frozen=True)
class _ScheduledProbe:
    contract: WorktreeContract
    answer: str
    changed_at: datetime
    due_at: datetime


class LandingProbeSchedule:
    """One entry per current identity, replaced on change and evicted on departure.

    This runtime schedule is not durable truth. Every discovery replaces its key
    population, so removals and reopened/new arcs cannot inherit an old deadline.
    """

    def __init__(self) -> None:
        self._entries: dict[Hashable, _ScheduledProbe] = {}

    def retain(self, current: Set[Hashable]) -> None:
        self._entries = {key: value for key, value in self._entries.items() if key in current}

    def due(self, key: Hashable, contract: WorktreeContract, now: datetime) -> bool:
        previous = self._entries.get(key)
        return (
            previous is None
            or previous.contract != contract
            or now < previous.changed_at
            or now >= previous.due_at
        )

    def observed(
        self,
        key: Hashable,
        contract: WorktreeContract,
        rows: list[dict[str, object]],
        now: datetime,
    ) -> None:
        # Observation clocks/ages are not changed answers. The semantic producer
        # fields (including the fact's own `at`) are; preserve row order as well.
        answer = json.dumps(
            [{k: v for k, v in row.items() if k in _ANSWER_FIELDS} for row in rows],
            sort_keys=True,
        )
        previous = self._entries.get(key)
        changed_at = now
        if (
            previous is not None
            and previous.contract == contract
            and previous.answer == answer
            and now >= previous.changed_at
        ):
            changed_at = previous.changed_at
        age = (now - changed_at).total_seconds()
        period = 30 if age < 120 else 120 if age < 600 else 600 if age < 3600 else 3600
        self._entries[key] = _ScheduledProbe(
            contract, answer, changed_at, now + timedelta(seconds=period)
        )
