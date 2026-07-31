"""Live capability gate registry tests with doubled native boundaries (260718-CHATS-L2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.serving.conversation.library import gates as gates_module
from agents_remember.serving.conversation.library.errors import LibraryStoreError
from agents_remember.serving.conversation.library.gates import (
    LOCKED_CODEX_RUNTIME_VERSION,
    GateProbes,
    LibraryGateRegistry,
)
from agents_remember.serving.harnesses import Harness

CODEX = Harness(id="codex", name="Codex", command="codex", argv=("codex",))
CLAUDE = Harness(id="claude", name="Claude", command="claude", argv=("claude",))
PI = Harness(id="pi", name="Pi", command="pi", argv=("pi",))
REGISTRY = (CODEX, CLAUDE, PI)


class _FakeHelperHost:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else {"rows": [], "nextCursor": None}
        self.error = error
        self.calls = 0

    async def call(self, _harness: str, _operation: str, _payload: object):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result, "0.80.7", "0.80.7"


def _registry(
    tmp: str,
    *,
    helper_host: object,
    codex_probe: object = None,
    which: object = None,
) -> LibraryGateRegistry:
    return LibraryGateRegistry(
        harness_registry=lambda: REGISTRY,
        workspace_root=Path(tmp),
        helper_host=helper_host,  # type: ignore[arg-type]
        probes=GateProbes(
            codex_probe=codex_probe,  # type: ignore[arg-type]
            which=which,  # type: ignore[arg-type]
        ),
    )


def _installed_which(tmp: str):
    binary = Path(tmp) / "bin"
    binary.mkdir(exist_ok=True)
    exe = binary / "codex"
    exe.write_text("#!/bin/sh\n")
    return lambda command: str(exe) if command == "codex" else None


class CodexGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    async def test_version_match_enables_codex_with_partial_completeness(self) -> None:
        probes = 0

        async def probe(_harness, _root, _env) -> str:
            nonlocal probes
            probes += 1
            return LOCKED_CODEX_RUNTIME_VERSION

        gates = _registry(
            self.tmp,
            helper_host=_FakeHelperHost(),
            codex_probe=probe,
            which=_installed_which(self.tmp),
        )
        capabilities = await gates.history_capabilities("codex")
        assert capabilities.list.state == "supported"
        assert capabilities.read.state == "supported"
        assert capabilities.completeness.state == "partial"
        assert capabilities.tool_completeness.state == "partial"
        # The landed L0E opener channel carries the exact codex resume target.
        assert capabilities.resume.state == "supported"
        assert "resume channel" in capabilities.resume.reason
        evidence = capabilities.list.evidence
        assert evidence is not None and evidence.runtime_version == LOCKED_CODEX_RUNTIME_VERSION
        assert evidence.fixture_id
        # Cached: a second call does not re-probe.
        await gates.history_capabilities("codex")
        assert probes == 1

    async def test_version_drift_still_enables_codex_when_the_probe_passes(self) -> None:
        # 260718-CHATS-L5F R4 (developer ruling 2026-07-21): THE CONTRACT IS THE ONLY GATE. A codex
        # runtime that differs from the fixture's captured version is NOT demoted — the connect+list
        # probe passing is the proof; the observed version rides the evidence as informational only.
        async def probe(_harness, _root, _env) -> str:
            return "0.999.0"

        gates = _registry(
            self.tmp,
            helper_host=_FakeHelperHost(),
            codex_probe=probe,
            which=_installed_which(self.tmp),
        )
        capabilities = await gates.history_capabilities("codex")
        assert capabilities.list.state == "supported"
        assert capabilities.read.state == "supported"
        evidence = capabilities.list.evidence
        assert evidence is not None and evidence.runtime_version == "0.999.0"

    async def test_failed_probe_is_unverified_not_unavailable(self) -> None:
        async def probe(_harness, _root, _env) -> str:
            raise LibraryStoreError("connection failed closed")

        gates = _registry(
            self.tmp,
            helper_host=_FakeHelperHost(),
            codex_probe=probe,
            which=_installed_which(self.tmp),
        )
        capabilities = await gates.history_capabilities("codex")
        assert capabilities.list.state == "unverified"
        assert "failed closed" in capabilities.list.reason

    async def test_missing_binary_and_unknown_harness_are_unavailable(self) -> None:
        gates = _registry(self.tmp, helper_host=_FakeHelperHost(), which=lambda _c: None)
        missing = await gates.history_capabilities("codex")
        assert missing.list.state == "unavailable"
        assert "not installed" in missing.list.reason
        unknown = await gates.history_capabilities("codex")
        assert unknown.list.evidence_tier == "none"

        empty = LibraryGateRegistry(
            harness_registry=lambda: (),
            workspace_root=Path(self.tmp),
            helper_host=_FakeHelperHost(),  # type: ignore[arg-type]
        )
        result = await empty.history_capabilities("codex")
        assert result.list.state == "unavailable"
        assert "unknown harness" in result.list.reason


class HelperGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _which(self, command: str) -> str | None:
        exe = Path(self.tmp) / "bin" / command
        exe.parent.mkdir(exist_ok=True)
        exe.write_text("#!/bin/sh\n")
        return str(exe)

    async def test_helper_success_enables_pi_with_full_completeness(self) -> None:
        original = gates_module.helper_preflight
        gates_module.helper_preflight = lambda harness, *, node=None: type(
            "PF", (), {"reason": None}
        )()
        try:
            gates = _registry(
                self.tmp,
                helper_host=_FakeHelperHost(),
                which=self._which,
            )
            capabilities = await gates.history_capabilities("pi")
        finally:
            gates_module.helper_preflight = original
        assert capabilities.list.state == "supported"
        assert capabilities.resume.state == "supported"
        assert capabilities.completeness.state == "supported"
        assert capabilities.tool_completeness.state == "supported"

    async def test_helper_failure_is_unverified(self) -> None:
        original = gates_module.helper_preflight
        gates_module.helper_preflight = lambda harness, *, node=None: type(
            "PF", (), {"reason": None}
        )()
        try:
            gates = _registry(
                self.tmp,
                helper_host=_FakeHelperHost(error=LibraryStoreError("incompatible")),
                which=self._which,
            )
            capabilities = await gates.history_capabilities("claude")
        finally:
            gates_module.helper_preflight = original
        assert capabilities.list.state == "unverified"
        assert "failed closed" in capabilities.list.reason

    async def test_missing_helper_dependencies_are_unverified(self) -> None:
        original = gates_module.helper_preflight
        gates_module.helper_preflight = lambda harness, *, node=None: type(
            "PF", (), {"reason": "locked helper dependencies are not installed"}
        )()
        try:
            gates = _registry(self.tmp, helper_host=_FakeHelperHost(), which=self._which)
            capabilities = await gates.history_capabilities("claude")
        finally:
            gates_module.helper_preflight = original
        assert capabilities.list.state == "unverified"
        assert "helper" in capabilities.list.reason


if __name__ == "__main__":
    unittest.main()
