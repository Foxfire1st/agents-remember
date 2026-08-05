"""Bounded background observation and immutable publication of landing facts."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from agents_remember.kernel.atomic_write import atomic_write_text
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
# Landing freeze: a finished contract's landing arc is FROZEN after one fully-observed
# probe — the facts are persisted beside the contract and the contract leaves the refresh sweep
# forever. Without this every closed-out task in the coordination root stays "landing-active"
# and the 30 s sweep probes origin (git ls-remote + gh) for ALL of history, indefinitely.
LANDING_FINAL_BASENAME = "landing-final.json"

# The exact shape of a landing row as it must reach the reducer's ``LandingRefNode`` (which is
# ``extra="forbid"``). Frozen rows are projected onto these keys on the way both into and out of
# landing-final.json so no stray key (historically a ``"frozen": true`` marker written by earlier
# builds — 85 such files already exist on the live root) can raise ValidationError inside
# ``project_and_write`` and silently stall every projection tick.
_LANDING_ROW_KEYS = frozenset(
    {
        "kind",
        "label",
        "state",
        "factState",
        "detail",
        "at",
        "observedAt",
        "lastAttemptAt",
        "staleSeconds",
    }
)

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
        # path -> (final-file mtime_ns, frozenAt, rows). frozenAt rides along so the freshness
        # check below never has to re-read the file, and so a contract rewritten *after* a cache
        # fill is still re-judged on every call (the cache keys on the final file, not the contract).
        self._frozen_cache: dict[str, tuple[int, datetime, list[dict[str, object]]]] = {}

    def current(self, contract: WorktreeContract, *, now: datetime) -> LandingRows:
        pending = unobserved_landing_refs(contract)
        if pending is None:
            return None
        # Serve the frozen final observation ONLY while the contract is still terminally
        # completed. A reopened task (cleanup != "completed") re-enters the sweep, and its
        # fresh live observations must NOT be shadowed by the stale first-finish file that
        # still sits beside the contract until reopen deletes it / the next finish rewrites it.
        if contract.cleanup == "completed":
            frozen = self._frozen_rows(contract)
            if frozen is not None:
                return frozen
        observation = self._observations.get(LandingContractKey.from_contract(contract))
        if observation is None:
            return pending
        return [fact.payload(now=now) for fact in observation.facts]

    def _frozen_rows(self, contract: WorktreeContract) -> LandingRows:
        """The persisted final observation, mtime-cached; ``None`` while the arc is live.

        Also ``None`` for a file that cannot be shown to describe the contract's *current* arc
        (see :func:`_frozen_before_contract`) — a stale-but-loadable file must not shadow the
        live probe.
        """
        path = _final_path(contract)
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        key = path.as_posix()
        cached = self._frozen_cache.get(key)
        if cached is None or cached[0] != mtime:
            loaded = _load_final(path)
            if loaded is None:
                return None
            cached = (mtime, loaded[0], loaded[1])
            self._frozen_cache[key] = cached
        if _frozen_before_contract(cached[1], contract):
            return None
        return cached[2]

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
        by_key = {LandingContractKey.from_contract(c): c for c in contracts}
        for key, rows in results:
            if rows is not None:
                self._publish(key, rows, attempted_at=attempted_at)
                contract = by_key.get(key)
                if contract is not None:
                    self._maybe_freeze(contract, rows, attempted_at=attempted_at)
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
            if unobserved_landing_refs(contract) is None:
                continue
            # Landing freeze: a frozen finished contract left the sweep for good — its facts are served
            # from the persisted final observation, never re-probed. A reopened task (cleanup
            # no longer "completed") re-enters the sweep and re-freezes on its next finish.
            # Gate on a *trustworthy* final file (mtime-cached), not mere existence: a corrupt,
            # unparseable, or contract-predating landing-final.json is treated as absent so the
            # contract stays in the sweep and self-heals (its next full observation atomically
            # rewrites the file) instead of leaving the sweep forever while serving stale facts.
            if contract.cleanup == "completed" and self._frozen_rows(contract) is not None:
                continue
            contracts.append(contract)
        return contracts

    def _maybe_freeze(
        self,
        contract: WorktreeContract,
        rows: list[dict[str, object]],
        *,
        attempted_at: datetime,
    ) -> None:
        """Persist the final observation once the finished contract is fully observed.

        Freeze requires cleanup "completed" (the lifecycle is over — nothing left to land)
        and every fact ``observed`` in THIS probe (a missing fact means the remote answer is
        still outstanding; the ordinary cadence retries and freezes on the next full answer).

        No ``path.exists()`` early-return: a contract that already carries a *trusted* final
        file has left the sweep and never reaches here, so the only contracts that do are ones
        whose file is absent, corrupt, or stale — all of which we WANT to (re)write atomically.
        This is what lets a corrupt file self-heal and a reopened→re-finished contract re-freeze
        with fresh facts (reopen having deleted the stale file first).

        The write is still decided on a seconds-old contract snapshot, so it can resurrect a
        file ``task_reopen`` just deleted; ``frozenAt`` is stamped with the sweep's start, which
        is what lets :func:`_frozen_before_contract` recognise and discard such a file on read.
        Re-checking the contract here instead would only narrow the window, not close it.
        """
        if contract.cleanup != "completed":
            return
        if not rows or any(row.get("factState") != "observed" for row in rows):
            return
        path = _final_path(contract)
        frozen_rows = [
            {
                **{key: value for key, value in row.items() if key in _LANDING_ROW_KEYS},
                "observedAt": attempted_at.isoformat(),
                "lastAttemptAt": attempted_at.isoformat(),
                "staleSeconds": None,
            }
            for row in rows
        ]
        payload = {"frozenAt": attempted_at.isoformat(), "facts": frozen_rows}
        try:
            atomic_write_text(path, json.dumps(payload, indent=1))
        except OSError:
            logger.warning("could not persist landing freeze for %s", path, exc_info=True)

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


def _final_path(contract: WorktreeContract) -> Path:
    """The frozen-facts file lives beside the contract, so it travels with the task."""
    return contract.contract_path.parent / LANDING_FINAL_BASENAME


def _load_final(path: Path) -> tuple[datetime, list[dict[str, object]]] | None:
    """``(frozenAt, reducer-shaped rows)``, or ``None`` for anything we cannot trust as final.

    A file with no usable ``frozenAt`` is treated exactly like a corrupt one: its provenance
    cannot be checked against the contract, so it is "absent" and the leaf self-heals by
    re-probing and re-freezing (the freeze has always written ``frozenAt``).
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    frozen_at = _parse_stamp(payload.get("frozenAt"))
    facts = payload.get("facts")
    if frozen_at is None or not isinstance(facts, list):
        return None
    if not all(isinstance(row, dict) for row in facts):
        return None
    # Project every row onto the reducer-known keys: files written by earlier builds carry a
    # ``"frozen": true`` marker that ``LandingRefNode`` (extra="forbid") would reject. Stripping
    # here keeps the projection tick alive against files already on disk, not just new writes.
    rows = [{key: value for key, value in row.items() if key in _LANDING_ROW_KEYS} for row in facts]
    return frozen_at, rows


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    # The freeze always writes an aware UTC stamp; a naive one (hand-edited file) is read as UTC
    # rather than local time so the comparison below cannot silently shift by the host offset.
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _frozen_before_contract(frozen_at: datetime, contract: WorktreeContract) -> bool:
    """Whether a frozen file predates its contract's last write — i.e. describes a past arc.

    ``_maybe_freeze`` decides against the contract snapshot the sweep
    loaded seconds earlier (the probes are cross-process, unlocked, ``_PROBE_TIMEOUT_SECONDS``
    each), so a ``task_reopen`` landing mid-sweep has its deletion of landing-final.json undone
    by the in-flight freeze — and the reopened leaf would then serve run 1's SHA/PR as
    ``observed`` forever. The stamp comparison closes it without weakening the freeze: freezing
    stamps the *sweep start*, which necessarily precedes the reopen's ``write_contract`` (the
    sweep had already read the contract as "completed"), while an honest freeze always lands
    after the write that finished the leaf. The same check retires a file whose reopen-time
    deletion failed (``frozenLanding: "delete-failed"``).

    Deliberately asymmetric: a false "stale" costs one probe cycle and heals itself on the next
    freeze; a false "current" is a permanent fabricated observation.
    """
    try:
        contract_written_at = contract.contract_path.stat().st_mtime
    except OSError:
        return True
    return frozen_at.timestamp() < contract_written_at


def _optional_text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None
