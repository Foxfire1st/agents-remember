"""One immutable leaf-enclosure-contract snapshot per projection tick (260712-PTS-L2).

Each projection tick previously enumerated ``iter_leaf_enclosure_contracts`` and
re-parsed EVERY leaf enclosure contract three times: ``read_enclosures``,
``read_engine_process_facts``, and drift-snapshot pruning each ran their own
walk + ``load_contract`` pass. :class:`ContractSnapshotCache.build` performs that
pass ONCE at tick start and hands the result to all three readers as an immutable
:class:`ContractSnapshot`, backed by a stat-identity parse cache (path +
``mtime_ns`` + size + ``ctime_ns``) so an unchanged contract file is not re-read
or re-parsed on later ticks at all.

Concurrency discipline (mirrors ``projection_store._lifecycle_log_cache``): the
cache is mutated only inside ``build``, which runs on the projection worker
thread -- the projector serializes ticks by awaiting each ``asyncio.to_thread``
call, so builds never overlap. The published :class:`ContractSnapshot` is an
immutable value (read-only mapping of frozen contracts) that may be handed to any
consumer without locks. The landing refresher and agent-notifier sweep keep their own
independent passes; they never touch this cache.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from agents_remember.worktrees.task_resolver import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


@dataclass(frozen=True)
class ContractSnapshot:
    """Immutable per-tick view of every live leaf enclosure contract.

    ``contracts`` preserves the sorted enumeration order of
    ``iter_leaf_enclosure_contracts`` (insertion-ordered mapping), so consumers
    iterate exactly the paths, contracts, and order they previously walked
    themselves. ``skipped`` carries the paths whose parse failed this tick -- the
    same skip-never-fatal containment each reader applied inline before.
    """

    contracts: Mapping[Path, WorktreeContract]
    skipped: frozenset[Path]


@dataclass(frozen=True)
class _ParseCacheEntry:
    mtime_ns: int
    size: int
    ctime_ns: int
    contract: WorktreeContract


class ContractSnapshotCache:
    """Cross-tick contract parse cache keyed by stat identity (R2) with live-set pruning (R3).

    A cache entry is reused only while the contract file's ``(mtime_ns, size,
    ctime_ns)`` is unchanged; any stat change re-parses. ``ctime_ns`` is part of
    the identity (adversarial-review hardening): a ``chmod 000`` changes neither
    mtime nor size, so without it the cache would serve the old good parse
    forever where the pre-cache readers degraded to skip-every-tick, and a
    rewrite whose ``(mtime_ns, size)`` was pinned via ``os.utime`` would never be
    seen; ctime changes on both, while staying untouched for genuinely unchanged
    files -- so the hardening costs zero extra parses. Parse FAILURES are never
    cached: an unreadable or malformed contract is skipped this build and
    re-attempted on the next one, exactly the retry-every-tick containment the
    readers had when they parsed inline (a transient ``OSError`` therefore
    self-heals without waiting for a stat change). Entries whose paths left the
    enumeration are dropped on the next build, so retention is bounded by the
    live contract set.
    """

    def __init__(self) -> None:
        self._entries: dict[Path, _ParseCacheEntry] = {}

    def build(self, tasks_root: Path) -> ContractSnapshot:
        """Enumerate + parse the live leaf contracts once; publish an immutable snapshot."""
        contracts: dict[Path, WorktreeContract] = {}
        skipped: set[Path] = set()
        seen: set[Path] = set()
        for path in iter_leaf_enclosure_contracts(tasks_root):
            seen.add(path)
            stat = _safe_stat(path)
            cached = self._cached_contract(path, stat)
            if cached is not None:
                contracts[path] = cached
                continue
            try:
                contract = load_contract(path)
            except (ContractError, OSError):
                self._entries.pop(path, None)
                skipped.add(path)
                continue
            if stat is not None:
                self._entries[path] = _ParseCacheEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    ctime_ns=stat.st_ctime_ns,
                    contract=contract,
                )
            else:
                self._entries.pop(path, None)
            contracts[path] = contract
        for stale in [path for path in self._entries if path not in seen]:
            del self._entries[stale]
        return ContractSnapshot(contracts=MappingProxyType(contracts), skipped=frozenset(skipped))

    def _cached_contract(self, path: Path, stat: os.stat_result | None) -> WorktreeContract | None:
        """The cached parse, only while the stat identity (mtime_ns + size + ctime_ns) holds."""
        if stat is None:
            return None
        entry = self._entries.get(path)
        if (
            entry is None
            or entry.mtime_ns != stat.st_mtime_ns
            or entry.size != stat.st_size
            or entry.ctime_ns != stat.st_ctime_ns
        ):
            return None
        return entry.contract


def _safe_stat(path: Path) -> os.stat_result | None:
    """Stat is a cache concern only: the readers never stat'ed before this leaf, so a
    failed stat must not introduce a new skip path -- the caller falls through to the
    (uncached) parse attempt, which applies the original containment."""
    try:
        return path.stat()
    except OSError:
        return None


def build_contract_snapshot(tasks_root: Path) -> ContractSnapshot:
    """One-shot snapshot for standalone reader calls (no cross-tick cache).

    Readers keep their public signatures: a call without an injected snapshot
    builds its own local pass -- identical cost and behavior to the pre-L2 walk.
    """
    return ContractSnapshotCache().build(tasks_root)
