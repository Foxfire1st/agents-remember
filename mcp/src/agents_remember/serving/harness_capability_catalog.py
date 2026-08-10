"""Token-free pre-session capability discovery with bounded install-aware caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from agents_remember.errors import HarnessControlError
from agents_remember.kernel.harnesses import Harness
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    LaunchSpec,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    capability_snapshot_json,
)
from agents_remember.serving.harness_control_adapter import (
    BUILTIN_PROTOCOL_HARNESSES,
    HarnessCapabilityDiscoverer,
)
from agents_remember.serving.harness_control_factories import create_harness_protocol_adapter
from agents_remember.serving.harness_control_runner import adapter_argv
from agents_remember.serving.harnesses import Which, find_harness

CAPABILITY_SCHEMA = "ar-harness-capabilities/v1"
CacheStatus = Literal["hit", "miss", "refreshed"]
EnvironmentReader = Callable[[], Mapping[str, str]]


class AdapterFactory(Protocol):
    def __call__(
        self, harness_id: str, *, env: Mapping[str, str]
    ) -> HarnessCapabilityDiscoverer: ...


class HarnessCapabilityLookupError(HarnessControlError):
    """A pre-session catalog request cannot address an installed native adapter."""

    def __init__(self, detail: str, *, status_code: int) -> None:
        super().__init__(detail)
        self.status_code = status_code


@dataclass(frozen=True)
class CapabilityCatalogResult:
    harness_id: str
    cache_status: CacheStatus
    install_fingerprint: str
    snapshot: CapabilitySnapshot

    def to_json(self) -> dict[str, object]:
        """Freeze the daemon envelope while nesting the unchanged normalized L1 snapshot."""

        return {
            "schema": CAPABILITY_SCHEMA,
            "harness": self.harness_id,
            "cacheStatus": self.cache_status,
            "installFingerprint": self.install_fingerprint,
            "capabilities": capability_snapshot_json(self.snapshot),
        }


@dataclass(frozen=True)
class _CacheEntry:
    fingerprint: str
    snapshot: CapabilitySnapshot


@dataclass(frozen=True)
class _InstalledHarness:
    harness: Harness
    executable: Path
    fingerprint: str


class HarnessCapabilityCatalog:
    """Discover native catalogs once per installed executable fingerprint.

    The cache has at most one successful entry and one lock for each of AR's three native
    harnesses. An explicit refresh replaces the entry for auth/account changes; failed discovery
    never overwrites or masquerades as a stale success.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        which: Which | None = None,
        adapter_factory: AdapterFactory = create_harness_protocol_adapter,
        environment: EnvironmentReader = lambda: os.environ,
    ) -> None:
        self._workspace_root = workspace_root
        self._which = which
        self._adapter_factory = adapter_factory
        self._environment = environment
        self._cache: dict[str, _CacheEntry] = {}
        self._locks = {harness_id: asyncio.Lock() for harness_id in BUILTIN_PROTOCOL_HARNESSES}

    @property
    def retained_entry_count(self) -> int:
        """Expose the fixed cache bound for scaling tests and diagnostics."""

        return len(self._cache)

    async def get(
        self,
        harness_id: str,
        *,
        registry: Sequence[Harness],
        refresh: bool = False,
    ) -> CapabilityCatalogResult:
        installed = self._installed_harness(harness_id, registry)
        observed = self._cache.get(harness_id)
        if not refresh and _matches(observed, installed.fingerprint):
            assert observed is not None
            return _result(installed, "hit", observed.snapshot)

        async with self._locks[harness_id]:
            current = self._cache.get(harness_id)
            if _matches(current, installed.fingerprint) and (
                not refresh or current is not observed
            ):
                assert current is not None
                return _result(installed, "hit", current.snapshot)
            return await self._refresh_entry(
                harness_id,
                installed,
                current,
                refresh=refresh,
            )

    async def _refresh_entry(
        self,
        harness_id: str,
        installed: _InstalledHarness,
        current: _CacheEntry | None,
        *,
        refresh: bool,
    ) -> CapabilityCatalogResult:
        try:
            snapshot = await self._discover(installed)
        except Exception:
            # An explicit refresh is the auth/account invalidation boundary. Remove only the exact
            # entry this refresh evaluated: a later successful refresh must never be erased by an
            # older failure.
            if refresh and self._cache.get(harness_id) is current:
                self._cache.pop(harness_id, None)
            raise
        self._cache[harness_id] = _CacheEntry(installed.fingerprint, snapshot)
        return _result(installed, "refreshed" if refresh else "miss", snapshot)

    def _installed_harness(self, harness_id: str, registry: Sequence[Harness]) -> _InstalledHarness:
        harness = find_harness(harness_id, registry=registry)
        if harness is None:
            raise HarnessCapabilityLookupError(f"unknown harness: {harness_id!r}", status_code=404)
        if harness_id not in BUILTIN_PROTOCOL_HARNESSES:
            raise HarnessCapabilityLookupError(
                f"harness {harness_id!r} has no normalized native capability adapter",
                status_code=409,
            )
        resolver = self._which if self._which is not None else shutil.which
        resolved = resolver(harness.command)
        if resolved is None:
            raise HarnessCapabilityLookupError(
                f"harness not installed: {harness_id!r}", status_code=404
            )
        try:
            executable = Path(resolved).resolve(strict=True)
            fingerprint = _install_fingerprint(harness, executable)
        except OSError as exc:
            raise HarnessControlError(
                f"could not fingerprint installed harness {harness_id!r}: {exc}"
            ) from exc
        return _InstalledHarness(harness, executable, fingerprint)

    async def _discover(self, installed: _InstalledHarness) -> CapabilitySnapshot:
        env = dict(self._environment())
        argv = (str(installed.executable), *installed.harness.argv[1:])
        launch = LaunchSpec(
            identity=ControlIdentity(
                ar_session_id=f"capability-discovery:{installed.harness.id}",
                tmux_name=f"capability-discovery-{installed.harness.id}",
                created_at=installed.fingerprint,
            ),
            harness_id=installed.harness.id,
            cwd=self._workspace_root,
            argv=adapter_argv(installed.harness.id, argv),
            env=env,
        )
        adapter = self._adapter_factory(installed.harness.id, env=env)
        return await adapter.discover(launch)


def _matches(entry: _CacheEntry | None, fingerprint: str) -> bool:
    return entry is not None and entry.fingerprint == fingerprint


def _result(
    installed: _InstalledHarness,
    status: CacheStatus,
    snapshot: CapabilitySnapshot,
) -> CapabilityCatalogResult:
    return CapabilityCatalogResult(
        harness_id=installed.harness.id,
        cache_status=status,
        install_fingerprint=installed.fingerprint,
        snapshot=snapshot,
    )


def _install_fingerprint(harness: Harness, executable: Path) -> str:
    stat_result = executable.stat()
    payload = {
        "harness": harness.id,
        "executable": str(executable),
        "argv": [str(executable), *harness.argv[1:]],
        "stat": {
            "device": stat_result.st_dev,
            "inode": stat_result.st_ino,
            "mode": stat_result.st_mode,
            "size": stat_result.st_size,
            "modifiedNs": stat_result.st_mtime_ns,
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
