"""Bounded background observation and immutable publication of landing facts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from agents_remember.worktrees.modules.landing import landing_refs, unobserved_landing_refs
from agents_remember.worktrees.task_resolver import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

LANDING_REFRESH_INTERVAL_SECONDS = 30.0
LANDING_REFRESH_CONCURRENCY = 4
LANDING_STALE_AFTER_SECONDS = 120.0

logger = logging.getLogger(__name__)

LandingRows = list[dict[str, object]] | None
LandingObserver = Callable[[WorktreeContract], LandingRows]


@dataclass(frozen=True)
class LandingContractKey:
    """Identity fields that prevent observations bleeding across rewritten contracts."""

    contract_path: str
    repo_name: str
    worktree_group: str
    code_repo_path: str
    code_source_branch: str
    memory_repo_path: str
    memory_source_branch: str

    @classmethod
    def from_contract(cls, contract: WorktreeContract) -> LandingContractKey:
        return cls(
            contract_path=contract.contract_path.as_posix(),
            repo_name=contract.repo_name,
            worktree_group=contract.worktree_group.as_posix(),
            code_repo_path=contract.code_repo_path.as_posix(),
            code_source_branch=contract.code_source_branch,
            memory_repo_path=(
                contract.memory_repo_path.as_posix() if contract.memory_repo_path else ""
            ),
            memory_source_branch=contract.memory_source_branch,
        )


@dataclass(frozen=True)
class LandingFact:
    kind: str
    label: str
    state: str
    fact_state: str
    detail: str | None
    at: str | None
    observed_at: datetime | None
    last_attempt_at: datetime
    stale: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, object], *, attempted_at: datetime) -> LandingFact:
        fact_state = str(payload.get("factState") or "missing")
        return cls(
            kind=str(payload.get("kind") or ""),
            label=str(payload.get("label") or ""),
            state=str(payload.get("state") or "unknown"),
            fact_state=fact_state,
            detail=_optional_text(payload.get("detail")),
            at=_optional_text(payload.get("at")),
            observed_at=attempted_at if fact_state != "missing" else None,
            last_attempt_at=attempted_at,
        )

    def payload(self, *, now: datetime) -> dict[str, object]:
        stale_seconds = None
        if self.observed_at is not None:
            stale_seconds = max(0.0, (now - self.observed_at).total_seconds())
        stale = self.stale or (
            stale_seconds is not None and stale_seconds >= LANDING_STALE_AFTER_SECONDS
        )
        return {
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "factState": "stale" if stale else self.fact_state,
            "detail": self.detail,
            "at": self.at,
            "observedAt": self.observed_at.isoformat() if self.observed_at else None,
            "lastAttemptAt": self.last_attempt_at.isoformat(),
            "staleSeconds": stale_seconds,
        }


@dataclass(frozen=True)
class LandingObservation:
    facts: tuple[LandingFact, ...]


class LandingStateReader(Protocol):
    def current(self, contract: WorktreeContract, *, now: datetime) -> LandingRows: ...


class LandingStateRefresh(LandingStateReader, Protocol):
    async def run(self) -> None: ...


class LandingStateRefresher:
    """Refresh landing-active contracts off the projection tick with capped concurrency."""

    def __init__(
        self,
        config: McpRuntimeConfig,
        *,
        interval_seconds: float = LANDING_REFRESH_INTERVAL_SECONDS,
        max_concurrency: int = LANDING_REFRESH_CONCURRENCY,
        observe: LandingObserver = landing_refs,
    ) -> None:
        self._coordination_root = config.coordination_root
        self._interval_seconds = interval_seconds
        self._max_concurrency = max_concurrency
        self._observe = observe
        self._observations: dict[LandingContractKey, LandingObservation] = {}

    def current(self, contract: WorktreeContract, *, now: datetime) -> LandingRows:
        pending = unobserved_landing_refs(contract)
        if pending is None:
            return None
        observation = self._observations.get(LandingContractKey.from_contract(contract))
        if observation is None:
            return pending
        return [fact.payload(now=now) for fact in observation.facts]

    async def run(self) -> None:
        while True:
            try:
                await self.refresh_once()
            except Exception:
                # Cycle containment only: an unexpected sweep/publication failure is logged, then
                # the ordinary cadence makes the next attempt. Cancellation remains a BaseException
                # and propagates; there is no retry/backoff loop inside the failed cycle.
                logger.exception("landing refresh cycle failed; continuing next interval")
            await asyncio.sleep(self._interval_seconds)

    async def refresh_once(self, *, now: datetime | None = None) -> None:
        attempted_at = now or datetime.now(UTC)
        contracts = self._landing_contracts()
        active_keys = {LandingContractKey.from_contract(contract) for contract in contracts}
        semaphore = asyncio.Semaphore(self._max_concurrency)
        results = await asyncio.gather(
            *(self._observe_contract(contract, semaphore) for contract in contracts)
        )
        for key, rows in results:
            if rows is not None:
                self._publish(key, rows, attempted_at=attempted_at)
        # Copy-on-write publication keeps projection reads immutable and bounds retention to the
        # contracts that are landing-active in the latest enclosure sweep.
        self._observations = {
            key: observation
            for key, observation in self._observations.items()
            if key in active_keys
        }

    async def _observe_contract(
        self, contract: WorktreeContract, semaphore: asyncio.Semaphore
    ) -> tuple[LandingContractKey, LandingRows]:
        async with semaphore:
            try:
                rows = await asyncio.to_thread(self._observe, contract)
            except Exception:
                # Required boundary containment, not a retry layer: one malformed remote-command
                # result must become explicit missing/stale truth instead of killing the shared
                # lifecycle-managed refresher for every other exact contract.
                logger.warning(
                    "landing observation failed for %s", contract.contract_path, exc_info=True
                )
                rows = unobserved_landing_refs(contract)
                if rows is not None:
                    rows = [{**row, "detail": "observation failed"} for row in rows]
        return LandingContractKey.from_contract(contract), rows

    def _landing_contracts(self) -> list[WorktreeContract]:
        tasks_root = self._coordination_root / "tasks"
        contracts: list[WorktreeContract] = []
        for path in iter_leaf_enclosure_contracts(tasks_root):
            try:
                contract = load_contract(path)
            except (ContractError, OSError):
                continue
            if unobserved_landing_refs(contract) is not None:
                contracts.append(contract)
        return contracts

    def _publish(
        self,
        key: LandingContractKey,
        rows: list[dict[str, object]],
        *,
        attempted_at: datetime,
    ) -> None:
        previous = self._observations.get(key)
        previous_by_kind = (
            {fact.kind: fact for fact in previous.facts} if previous is not None else {}
        )
        facts: list[LandingFact] = []
        for row in rows:
            fact = LandingFact.from_payload(row, attempted_at=attempted_at)
            previous_fact = previous_by_kind.get(fact.kind)
            if (
                fact.fact_state == "missing"
                and previous_fact is not None
                and previous_fact.observed_at is not None
            ):
                fact = replace(previous_fact, last_attempt_at=attempted_at, stale=True)
            facts.append(fact)
        published = dict(self._observations)
        published[key] = LandingObservation(facts=tuple(facts))
        self._observations = published


def _optional_text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None
