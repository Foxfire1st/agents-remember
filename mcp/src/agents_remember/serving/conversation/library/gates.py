"""Runtime capability gates for the dormant native conversation library (260718-CHATS-L2).

A harness's library features are enabled only by a live production-path CONTRACT probe against the
installed runtime: Codex proves ``thread/list`` over a real app-server connection; Claude and Pi
prove the repository helper's handshake plus a real native list call. A missing binary, uninstalled
helper, or failed probe demotes the whole harness history surface to ``unverified``/``unavailable``
with an exact reason — fail closed and visible, never invented parity.

THE CONTRACT IS THE ONLY GATE (developer ruling 2026-07-21, 260718-CHATS-L5F R4): the probe result
alone decides the state. The observed CLI/runtime/helper version is recorded as informational
evidence metadata and is NEVER compared to a locked constant to demote a capability — harnesses
auto-update, and a version predicate is exactly what made a natively-working install fail closed.

The gate result is cached once per installed-executable fingerprint per harness (at most the
three normalized harnesses, so the cache is bounded by construction); an executable change re-runs
the contract probe.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.harnesses import Harness
from agents_remember.observer.events import now_iso
from agents_remember.serving.conversation.library import codex as codex_library
from agents_remember.serving.conversation.library.errors import LibraryStoreError
from agents_remember.serving.conversation.library.helper_host import (
    ConversationLibraryHelperHost,
    HelperHarness,
    helper_preflight,
)
from agents_remember.serving.conversation.models import (
    CapabilityEvidence,
    FeatureCapability,
    HarnessId,
    HistoryCapabilities,
)
from agents_remember.serving.harnesses import Which

# Informational only (260718-CHATS-L5F R4): the codex version whose contract was captured in the
# landed fixture. It is NOT a gate — nothing compares it to the observed runtime. Retained as a
# published reference/metadata value (and used by installed tests as an environment skip guard).
LOCKED_CODEX_RUNTIME_VERSION = "0.144.5"
_NORMALIZED: tuple[HarnessId, ...] = ("codex", "claude", "pi")

CodexProbe = Callable[[Harness, Path, Mapping[str, str]], Awaitable[str]]
"""Awaitable probe: connect, prove list, return the observed CLI version."""


def _unavailable(reason: str) -> FeatureCapability:
    return FeatureCapability(state="unavailable", reason=reason, evidence_tier="none")


def _unavailable_history(reason: str) -> HistoryCapabilities:
    feature = _unavailable(reason)
    return HistoryCapabilities(
        list=feature,
        read=feature,
        resume=feature,
        completeness=feature,
        tool_completeness=feature,
    )


def _gated(state: str, reason: str, evidence: CapabilityEvidence) -> FeatureCapability:
    return FeatureCapability(
        state=state,  # type: ignore[arg-type] - state is a validated CapabilityState literal
        reason=reason,
        evidence_tier="runtime-fixture",
        evidence=evidence,
    )


def _unverified_history(reason: str, evidence: CapabilityEvidence) -> HistoryCapabilities:
    feature = _gated("unverified", reason, evidence)
    return HistoryCapabilities(
        list=feature,
        read=feature,
        resume=feature,
        completeness=feature,
        tool_completeness=feature,
    )


def _codex_supported(evidence: CapabilityEvidence) -> HistoryCapabilities:
    note = (
        "installed Codex 0.144.5 app-server list/read passed through the production app-server seam"
    )
    partial = (
        "Codex historical pages omit persisted tool/command detail the vendor does not store; "
        "the preview carries a permanent completeness note"
    )
    return HistoryCapabilities(
        list=_gated("supported", note, evidence),
        read=_gated("supported", note, evidence),
        resume=_gated(
            "supported",
            "exact thread/resume launch rides the tracked opener through the landed resume "
            "channel; proven end-to-end by the installed-runtime open gate",
            evidence,
        ),
        completeness=_gated("partial", partial, evidence),
        tool_completeness=_gated("partial", partial, evidence),
    )


def _claude_supported(evidence: CapabilityEvidence) -> HistoryCapabilities:
    note = (
        "installed Claude with locked SDK 0.3.207 helper list/read passed through the "
        "production helper seam"
    )
    partial = (
        "Claude SDK history rebuilds chronological user/assistant chains; system, thinking, "
        "tool, and permission records appear only where the installed history persists them"
    )
    return HistoryCapabilities(
        list=_gated("supported", note, evidence),
        read=_gated("supported", note, evidence),
        resume=_gated(
            "supported",
            "exact --resume launch rides the existing tracked opener through the stream-json "
            "permission protocol",
            evidence,
        ),
        completeness=_gated("partial", partial, evidence),
        tool_completeness=_gated("partial", partial, evidence),
    )


def _pi_supported(evidence: CapabilityEvidence) -> HistoryCapabilities:
    note = (
        "installed Pi 0.80.7 with the locked helper SessionManager list/read passed through "
        "the production helper seam"
    )
    complete = "native append-only entries are the complete session line, tool records included"
    return HistoryCapabilities(
        list=_gated("supported", note, evidence),
        read=_gated("supported", note, evidence),
        resume=_gated(
            "supported",
            "exact --session launch rides the existing tracked opener",
            evidence,
        ),
        completeness=_gated("supported", complete, evidence),
        tool_completeness=_gated("supported", complete, evidence),
    )


@dataclass(frozen=True)
class GateProbes:
    """How the registry finds out what is actually installed, as one substitutable surface.

    A gate answers "can this harness serve a library here?" only by probing the machine: the codex
    app-server probe, PATH lookup and the process environment are the three ways it looks. Faking
    one while leaving the others live probes two different machines.
    """

    codex_probe: CodexProbe | None = None
    which: Which | None = None
    environment: Callable[[], Mapping[str, str]] = lambda: os.environ


DEFAULT_GATE_PROBES = GateProbes()


class LibraryGateRegistry:
    """Live production-path capability gates, cached per installed executable fingerprint."""

    def __init__(
        self,
        *,
        harness_registry: Callable[[], Sequence[Harness]],
        workspace_root: Path,
        helper_host: ConversationLibraryHelperHost,
        probes: GateProbes = DEFAULT_GATE_PROBES,
    ) -> None:
        codex_probe = probes.codex_probe
        which = probes.which
        environment = probes.environment
        self._registry = harness_registry
        self._workspace_root = workspace_root
        self._helper_host = helper_host
        self._codex_probe = codex_probe or self._default_codex_probe
        self._which = which
        self._environment = environment
        self._cache: dict[str, tuple[str, HistoryCapabilities]] = {}

    async def history_capabilities(self, harness_id: HarnessId) -> HistoryCapabilities:
        """Gate one harness's dormant history surface; fail closed with an exact reason."""

        harness = next((item for item in self._registry() if item.id == harness_id), None)
        if harness is None:
            return _unavailable_history(f"unknown harness: {harness_id!r}")
        executable = self._resolve_executable(harness)
        if executable is None:
            return _unavailable_history(f"harness not installed: {harness_id!r}")
        return await self._gated(harness_id, harness, executable)

    async def _gated(
        self,
        harness_id: HarnessId,
        harness: Harness,
        executable: Path,
    ) -> HistoryCapabilities:
        fingerprint = _install_fingerprint(executable)
        cached = self._cache.get(harness_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        capabilities = await self._run_gate(harness_id, harness, executable)
        if harness_id in _NORMALIZED:
            # Bounded by construction: one entry per normalized harness id.
            self._cache[harness_id] = (fingerprint, capabilities)
        return capabilities

    def _resolve_executable(self, harness: Harness) -> Path | None:
        resolver = self._which if self._which is not None else shutil.which
        resolved = resolver(harness.command)
        if resolved is None:
            return None
        try:
            return Path(resolved).resolve(strict=True)
        except OSError:
            return None

    async def _run_gate(
        self,
        harness_id: HarnessId,
        harness: Harness,
        executable: Path,
    ) -> HistoryCapabilities:
        observed_at = now_iso()
        if harness_id == "codex":
            return await self._codex_gate(harness, executable, observed_at)
        return await self._helper_gate(harness_id, executable, observed_at)

    async def _codex_gate(
        self,
        harness: Harness,
        executable: Path,
        observed_at: str,
    ) -> HistoryCapabilities:
        env = dict(self._environment())
        try:
            observed_version = await self._codex_probe(harness, self._workspace_root, env)
        except LibraryStoreError as exc:
            evidence = CapabilityEvidence(
                runtime_version="unknown",
                fixture_id="library-gate:codex:none",
                observed_at=observed_at,
            )
            return _unverified_history(
                f"the production Codex app-server probe failed closed: {exc}", evidence
            )
        evidence = CapabilityEvidence(
            runtime_version=observed_version,
            fixture_id=f"library-gate:codex:{_short(executable)}",
            observed_at=observed_at,
        )
        # 260718-CHATS-L5F R4 (developer ruling 2026-07-21): the real connect+list probe above is
        # the gate. If it succeeded the history contract verified against the running app-server;
        # the observed CLI version rides the evidence as informational metadata only and is never
        # compared to a locked constant.
        return _codex_supported(evidence)

    async def _helper_gate(
        self,
        harness_id: HelperHarness,
        executable: Path,
        observed_at: str,
    ) -> HistoryCapabilities:
        del executable  # the helper observes the installed runtime itself
        preflight = helper_preflight(harness_id, node=self._node())
        if preflight.reason is not None:
            evidence = CapabilityEvidence(
                runtime_version="unknown",
                fixture_id=f"library-gate:{harness_id}:none",
                observed_at=observed_at,
            )
            return _unverified_history(
                f"the locked library helper cannot run: {preflight.reason}", evidence
            )
        try:
            _result, runtime_version, helper_version = await self._helper_host.call(
                harness_id,
                "list",
                {"canonicalProjectScope": str(self._workspace_root), "cursor": None, "limit": 1},
            )
        except LibraryStoreError as exc:
            evidence = CapabilityEvidence(
                runtime_version="unknown",
                fixture_id=f"library-gate:{harness_id}:none",
                observed_at=observed_at,
            )
            return _unverified_history(f"the locked helper gate failed closed: {exc}", evidence)
        evidence = CapabilityEvidence(
            runtime_version=runtime_version,
            helper_version=helper_version,
            fixture_id=f"library-gate:{harness_id}:{runtime_version}",
            observed_at=observed_at,
        )
        if harness_id == "claude":
            return _claude_supported(evidence)
        return _pi_supported(evidence)

    def _node(self) -> str | None:
        resolver = self._which if self._which is not None else shutil.which
        return resolver("node")

    @staticmethod
    async def _default_codex_probe(
        harness: Harness,
        workspace_root: Path,
        env: Mapping[str, str],
    ) -> str:
        return await codex_library.probe_app_server_version(
            harness,
            workspace_root=workspace_root,
            env=env,
        )


def _install_fingerprint(executable: Path) -> str:
    stat_result = executable.stat()
    return hashlib.sha256(
        f"{executable}:{stat_result.st_size}:{stat_result.st_mtime_ns}".encode()
    ).hexdigest()


def _short(executable: Path) -> str:
    return _install_fingerprint(executable)[:12]


__all__ = ["LOCKED_CODEX_RUNTIME_VERSION", "LibraryGateRegistry"]
