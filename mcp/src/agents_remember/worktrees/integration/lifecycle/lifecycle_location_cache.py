"""Existing bounded lifecycle-location outcomes and their same-open input identities."""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_location as location_owner,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    location_error as _location_error,
)

if TYPE_CHECKING:
    from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
        LifecycleOperationLocation,
    )

_LOCATION_CACHE_MAX = 4096


_READ_CONSISTENCY_ATTEMPTS = 2


_StatIdentity = tuple[int, int, int]


def _stat_identity(path: Path) -> _StatIdentity | None:
    """The (mtime_ns, size, ctime_ns) identity of ``path``, or None when unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns)


@dataclass
class _LocationCacheEntry:
    """One cached resolution: derived paths plus the stat identities they were read at.

    The locator/manifest identities are the stat of the bytes parsed, sampled from the same
    opens during the fill -- never re-stated after the read. The derived locator/manifest
    paths are stored so a hit costs a confinement check, three stats and a deep copy, with
    no path derivation, no parsing and no error rebuild. A parent-directory rename without
    a file write is the one change this cannot see; any write to the contract, locator or
    manifest re-resolves, and enclosure moves always accompany contract writes.
    """

    contract_stat: _StatIdentity
    locator_path: str
    locator_stat: _StatIdentity | None
    manifest_path: str | None
    manifest_stat: _StatIdentity | None
    location: LifecycleOperationLocation | None
    error_spec: tuple[str, str, dict[str, object], dict[str, object]] | None


_location_cache: dict[str, _LocationCacheEntry] = {}


_location_cache_lock = threading.Lock()


_location_cache_stats: dict[str, int] = {"drops": 0}


def _location_cache_get(key: str) -> _LocationCacheEntry | None:
    with _location_cache_lock:
        return _location_cache.get(key)


def _location_cache_check(
    entry: _LocationCacheEntry, root: Path, contract_path: Path
) -> LifecycleOperationLocation | None:
    """The cached location (deep-copied), a fresh equivalent error, or None on any change.

    The hit re-derives confinement first (F14): a cached outcome is only usable while the
    contract still confines under this root and the cached locator/manifest paths still
    confine under it. Callers receive a deep copy -- the cached models are never shared.
    """
    location_owner._confined_contract_path(root, contract_path)
    if not Path(entry.locator_path).resolve(strict=False).is_relative_to(root):
        return None
    if entry.manifest_path is not None and not Path(entry.manifest_path).resolve(
        strict=False
    ).is_relative_to(root):
        return None
    if _stat_identity(contract_path) != entry.contract_stat:
        return None
    if _stat_identity(Path(entry.locator_path)) != entry.locator_stat:
        return None
    if entry.manifest_path is not None and (
        _stat_identity(Path(entry.manifest_path)) != entry.manifest_stat
    ):
        return None
    if entry.location is not None:
        return copy.deepcopy(entry.location)
    assert entry.error_spec is not None
    status, detail, expected, observed = entry.error_spec
    raise LifecycleOperationLocationError(
        status,
        detail,
        expected=copy.deepcopy(expected),
        observed=copy.deepcopy(observed),
    )


@dataclass(frozen=True)
class _ObservedIdentity:
    """The same-open locator/manifest identities one resolution's content was read at."""

    locator_stat: _StatIdentity | None = None
    manifest_stat: _StatIdentity | None = None
    manifest_path: str | None = None


_NO_OBSERVED_IDENTITY = _ObservedIdentity()


def _location_cache_put(
    key: str,
    contract_stat: _StatIdentity | None,
    *,
    location: LifecycleOperationLocation | None = None,
    error: LifecycleOperationLocationError | None = None,
    identities: _ObservedIdentity = _NO_OBSERVED_IDENTITY,
) -> None:
    """Remember one resolution outcome with the identities its content was read at.

    The locator/manifest identities are the stat of the bytes parsed, sampled from the same
    opens (F14) -- never re-stated after the read, so a replacement or deletion during the
    fill cannot bind old content to new stats. ``None`` means the file was absent when read
    (a stable fact: the check compares against the live stat, so a later creation misses).
    """
    locator_stat = identities.locator_stat
    manifest_stat = identities.manifest_stat
    manifest_path = identities.manifest_path
    if contract_stat is None:
        return
    if location is not None:
        if locator_stat is None or manifest_stat is None:
            # A positive entry without the identities its bytes were read at can never
            # be validated on a hit (F14): refuse the entry, serve this resolution
            # uncached, and let the next read fill a fully-identified entry.
            return
        locator_path = location.locator_path.as_posix()
        if manifest_path is None:
            manifest_path = location.manifest_path.as_posix()
        entry = _LocationCacheEntry(
            contract_stat=contract_stat,
            locator_path=locator_path,
            locator_stat=locator_stat,
            manifest_path=manifest_path,
            manifest_stat=manifest_stat,
            location=location,
            error_spec=None,
        )
    elif error is not None and error.status in {
        "operation-location-adoption-required",
        "operation-location-publication-interrupted",
        "operation-location-terminal-archived",
    }:
        # Locator-determined negatives: the outcome depends on the contract and
        # the locator only (missing locator, or its terminal-archived state), so
        # those two identities are the whole check. Every other failure
        # re-resolves fresh; those enclosures are few. The locator identity is the
        # stat of the bytes the negative was read from (absent for a missing locator),
        # so a locator created or replaced after the fill misses the cache.
        observed = error.observed
        locator_path = str(observed.get("locatorPath", ""))
        entry = _LocationCacheEntry(
            contract_stat=contract_stat,
            locator_path=locator_path,
            locator_stat=locator_stat,
            manifest_path=None,
            manifest_stat=None,
            location=None,
            error_spec=(
                error.status,
                error.detail,
                copy.deepcopy(dict(error.expected)),
                copy.deepcopy(dict(error.observed)),
            ),
        )
    else:
        return
    with _location_cache_lock:
        if len(_location_cache) >= _LOCATION_CACHE_MAX and key not in _location_cache:
            # Insertion-ordered: the first key is the oldest entry.
            oldest = next(iter(_location_cache))
            del _location_cache[oldest]
            _location_cache_stats["drops"] += 1
        _location_cache[key] = entry


def resolve_lifecycle_operation_location(
    coordination_root: Path,
    contract_path: Path,
) -> LifecycleOperationLocation:
    """Resolve locator -> root manifest -> journals without scans or inference.

    Outcomes are cached by file stat identity (MIK-R81 rule 3): the tick reads
    hundreds of enclosures per refresh, and re-deriving every locator path,
    re-parsing every locator and manifest, and rebuilding every location error
    dominates that pass. A cached outcome is reused only while the contract,
    locator and manifest files are byte-identical to the resolution; any write
    to any of them re-resolves. The key carries the canonical coordination root
    (F14): two checkouts never share entries, and every hit re-validates
    confinement before touching the cache. Failures cache as re-raiseable specs,
    never as exception instances, so no traceback accumulates across refreshes.
    """
    root = coordination_root.resolve(strict=False)
    key = f"{root.as_posix()}\0{contract_path.as_posix()}"
    entry = _location_cache_get(key)
    if entry is not None:
        location = _location_cache_check(entry, root, contract_path)
        if location is not None:
            return location
    # The contract identity is sampled before the resolution, not after it (F14): a
    # write during the fill binds the older identity, so the next read misses instead
    # of serving a resolution the write may already have invalidated.
    contract_stat = _stat_identity(contract_path)
    try:
        location, locator_stat, manifest_stat = (
            location_owner._resolve_lifecycle_operation_location(coordination_root, contract_path)
        )
    except LifecycleOperationLocationError as error:
        _location_cache_put(
            key,
            contract_stat,
            error=error,
            identities=_ObservedIdentity(locator_stat=error.locator_stat_read),
        )
        raise
    _location_cache_put(
        key,
        contract_stat,
        location=location,
        identities=_ObservedIdentity(
            locator_stat=locator_stat,
            manifest_stat=manifest_stat,
            manifest_path=location.manifest_path.as_posix(),
        ),
    )
    return location


def _read_consistent_bytes(
    path: Path, contract_path: Path, kind: str
) -> tuple[bytes, _StatIdentity]:
    """Read ``path`` with a before/after descriptor-identity consistency check (F14).

    One open samples the descriptor identity, reads the bytes, then samples the
    descriptor again: only a triple where both samples agree is returned, so an
    in-place write landing between read completion and fstat cannot bind the old
    bytes to the new settled identity. A still-changing file retries up to
    ``_READ_CONSISTENCY_ATTEMPTS`` opens, then fails closed; an unavailable
    descriptor identity (fstat failure) is refused the same way -- a positive
    resolution is never built on bytes without an identity.
    """

    last_error: str = "changed during read"
    for _ in range(_READ_CONSISTENCY_ATTEMPTS):
        try:
            with open(path, "rb") as handle:
                try:
                    before = os.fstat(handle.fileno())
                except OSError:
                    last_error = "descriptor identity unavailable before read"
                    break
                raw = handle.read()
                try:
                    after = os.fstat(handle.fileno())
                except OSError:
                    last_error = "descriptor identity unavailable after read"
                    break
        except FileNotFoundError:
            raise
        except OSError:
            last_error = "unreadable during consistent read"
            break
        before_identity = (before.st_mtime_ns, before.st_size, before.st_ctime_ns)
        after_identity = (after.st_mtime_ns, after.st_size, after.st_ctime_ns)
        if before_identity == after_identity and after.st_size == len(raw):
            return raw, after_identity
        last_error = "changed during read"
    raise _location_error(
        "operation-location-invalid",
        f"the canonical enclosure {kind} is unreadable or invalid",
        contract_path=contract_path,
        observed={f"{kind}Path": path.as_posix(), "errorType": last_error},
    )
