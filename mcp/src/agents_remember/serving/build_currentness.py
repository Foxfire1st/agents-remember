"""On-demand comparison of immutable loaded Python code with its current package files."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agents_remember.kernel.primitives.checkout_coordination import resolve_checkout_location
from agents_remember.models.core import BuildCurrentnessPayload
from agents_remember.observer.events import now_iso
from agents_remember.serving import build_info

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
    from agents_remember.serving.build_info import ServingBuild

DIGEST_CACHE_SECONDS = 30.0


@dataclass(frozen=True)
class _DigestReading:
    checked_at: float
    digest: str | None


class _BuildComparisons:
    """One process's shared cache and first-observed mismatch times."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._digests: dict[str, _DigestReading] = {}
        self._first_seen: dict[tuple[str, str | None, str], str] = {}

    def inspect(self, build: ServingBuild, role: str) -> BuildCurrentnessPayload:
        mode = build_mode(build)
        root = build.package_root
        if root is None:
            return BuildCurrentnessPayload(
                mode=mode,
                state="unknown",
                loadedDigest=build.source_digest,
                reason="The loaded package root is unknown; its Python digest cannot be compared.",
            )
        with self._lock:
            installed = self._digest(root)
            identity = (root, build.source_digest, build.booted_at)
            if build.source_digest is None or installed is None:
                return BuildCurrentnessPayload(
                    mode=mode,
                    state="unknown",
                    loadedDigest=build.source_digest,
                    installedDigest=installed,
                    reason=f"The loaded or installed Python digest could not be computed for {root}.",
                )
            if installed == build.source_digest:
                self._first_seen.pop(identity, None)
                return BuildCurrentnessPayload(
                    mode=mode,
                    state="current",
                    loadedDigest=build.source_digest,
                    installedDigest=installed,
                )
            first = self._first_seen.setdefault(identity, now_iso())
            remedy = (
                "Restart the dashboard."
                if role == "dashboard"
                else "Restart or reconnect this MCP server."
            )
            return BuildCurrentnessPayload(
                mode=mode,
                state="stale",
                loadedDigest=build.source_digest,
                installedDigest=installed,
                firstSeenAt=first,
                reason="Python source files changed since this process loaded its build.",
                remedy=remedy,
            )

    def _digest(self, root: str) -> str | None:
        now = time.monotonic()
        cached = self._digests.get(root)
        if cached is not None and now - cached.checked_at < DIGEST_CACHE_SECONDS:
            return cached.digest
        digest = build_info.runtime_source_digest(Path(root))
        self._digests[root] = _DigestReading(now, digest)
        return digest

    def retain(self, builds: Iterable[ServingBuild]) -> None:
        """Mismatch times follow live boots; unreferenced digest entries expire at their TTL."""
        live = list(builds)
        roots = {build.package_root for build in live}
        identities = {(build.package_root, build.source_digest, build.booted_at) for build in live}
        now = time.monotonic()
        with self._lock:
            self._first_seen = {
                identity: first
                for identity, first in self._first_seen.items()
                if identity in identities
            }
            self._digests = {
                root: reading
                for root, reading in self._digests.items()
                if root in roots or now - reading.checked_at < DIGEST_CACHE_SECONDS
            }


_COMPARISONS = _BuildComparisons()


def build_mode(build: ServingBuild) -> Literal["installed", "checkout"]:
    """Use repository shape, rather than cwd or a best-effort Git subprocess result."""
    if build.package_root is not None:
        location = resolve_checkout_location(Path(build.package_root) / "__init__.py")
        if (
            location is not None
            and Path(build.package_root).resolve()
            == location.checkout_root / "mcp" / "src" / "agents_remember"
        ):
            return "checkout"
    return "installed"


def inspect_build(build: ServingBuild, *, role: str = "mcp") -> BuildCurrentnessPayload:
    """Inspect a known boot identity, including checkouts explicitly requested by status."""
    return _COMPARISONS.inspect(build, role)


def retain_live_builds(builds: Iterable[ServingBuild]) -> None:
    """A complete registry scan owns cache reclamation, always retaining this process's boot."""
    _COMPARISONS.retain([build_info.process_serving_build(), *builds])


def build_currentness(
    _config: McpRuntimeConfig, *, inspect_checkout: bool = False, role: str = "mcp"
) -> BuildCurrentnessPayload | None:
    """Ordinary checkout calls neither compare files nor carry a staleness annotation."""
    build = build_info.process_serving_build()
    if build_mode(build) == "checkout" and not inspect_checkout:
        return None
    return inspect_build(build, role=role)
