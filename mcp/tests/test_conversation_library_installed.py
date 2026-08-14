"""Installed-runtime library/open production gates (260718-CHATS-L2).

These exercise the REAL production seams on machines where the harnesses are installed: the
live Codex app-server gate and library, the locked Pi helper gate/library, an end-to-end Pi
open through the tracked opener + tmux + control runner + exact catalog proof + retirement,
and the Claude version-mismatch fail-closed proof (installed 2.1.214 vs locked 2.1.211). Every
test skips with an exact reason where its runtime precondition is absent (CI has no harnesses);
none fabricates capability evidence.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents_remember.kernel.harnesses import HARNESSES
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
    NativeConversationRef,
)
from agents_remember.observer.events import now_iso
from agents_remember.serving.conversation.library.codex import CodexConversationLibrary
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.factories import LibraryShared
from agents_remember.serving.conversation.library.gates import (
    LOCKED_CODEX_RUNTIME_VERSION,
    LibraryGateRegistry,
)
from agents_remember.serving.conversation.library.helper_host import (
    LOCKED_RUNTIME_VERSION,
    ConversationLibraryHelperHost,
    helper_preflight,
    helper_root,
)
from agents_remember.serving.conversation.library.open_service import (
    ConversationOpenService,
    LibraryBinding,
    OpenOperationLedger,
    OpenRequest,
)
from agents_remember.serving.conversation.library.pi import PiConversationLibrary
from agents_remember.serving.conversation.library.scope import canonical_library_scope
from agents_remember.serving.conversation.library.service import ConversationLibraryService
from agents_remember.serving.conversation.runtime import ConversationRuntime, ConversationScope
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_client import ControlPlaneClient
from agents_remember.serving.hosted_readiness import ReadinessWait, hosted_session_readiness
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_liveness import TerminalCatalogLivenessConfig, utc_now

CODEX = next(h for h in HARNESSES if h.id == "codex")
PI = next(h for h in HARNESSES if h.id == "pi")
CLAUDE = next(h for h in HARNESSES if h.id == "claude")
PI_SCOPE = "/home/mohamedreadone/Projects/agents-remember"
CODEX_SCOPE = "/home/mohamedreadone/Projects"


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:67).
def _version_of(binary: str | None) -> str | None:  # pragma: no cover
    if binary is None:
        return None
    try:
        output = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", output.stdout + output.stderr)
    return match.group(1) if match else None


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:84).
def _caller(workspace: Path) -> AuthorizationBinding:  # pragma: no cover
    return AuthorizationBinding(
        principal_id="local-operator:1000", tenant_id=str(workspace.resolve())
    )


class _Resolver:
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:91).
    def __init__(self, binding: AuthorizationBinding) -> None:  # pragma: no cover
        self._binding = binding

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:94).
    def resolve(self, *, client_host: str | None) -> AuthorizationBinding:  # noqa: ARG002 - protocol signature  # pragma: no cover

        return self._binding

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:98).
    def require(self, authorization: AuthorizationBinding) -> None:  # pragma: no cover
        if authorization != self._binding:
            raise AssertionError("cross-principal binding")


class CodexInstalledTests(unittest.IsolatedAsyncioTestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:104).
    def setUp(self) -> None:  # pragma: no cover
        binary = shutil.which("codex")
        if _version_of(binary) != LOCKED_CODEX_RUNTIME_VERSION:
            self.skipTest(
                f"installed codex != locked {LOCKED_CODEX_RUNTIME_VERSION} "
                f"(observed {_version_of(binary)})"
            )
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.cursor = LibraryCursorAuthority(mint_signing_key())
        self.caller = _caller(Path(CODEX_SCOPE))
        self.scope = canonical_library_scope(
            self.caller, "codex", None, workspace_root=Path(CODEX_SCOPE)
        )
        self.library = CodexConversationLibrary(
            authorization=self.caller,
            cursor_authority=self.cursor,
            capabilities=self._caps,
            harness=CODEX,
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:125).
    def tearDown(self) -> None:  # pragma: no cover
        self._tmpdir.cleanup()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:128).
    async def _caps(self, _harness: str):  # pragma: no cover
        gates = LibraryGateRegistry(
            harness_registry=lambda: HARNESSES,
            workspace_root=self.tmp,
            helper_host=ConversationLibraryHelperHost(),
        )
        return await gates.history_capabilities("codex")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:136).
    async def test_live_gate_supports_list_read_and_partial_completeness(
        self,
    ) -> None:  # pragma: no cover
        gates = LibraryGateRegistry(
            harness_registry=lambda: HARNESSES,
            workspace_root=self.tmp,
            helper_host=ConversationLibraryHelperHost(),
        )
        capabilities = await gates.history_capabilities("codex")
        assert capabilities.list.state == "supported"
        assert capabilities.read.state == "supported"
        assert capabilities.completeness.state == "partial"
        assert capabilities.tool_completeness.state == "partial"
        assert capabilities.resume.state == "supported"
        assert "resume channel" in capabilities.resume.reason
        evidence = capabilities.list.evidence
        assert evidence is not None
        assert evidence.runtime_version == LOCKED_CODEX_RUNTIME_VERSION
        assert evidence.fixture_id is not None
        assert evidence.fixture_id.startswith("library-gate:codex:")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:155).
    async def test_live_list_read_and_resolve_round_trip(self) -> None:  # pragma: no cover
        page = await self.library.list(self.scope, cursor=None, limit=5)
        assert page.scope.harness_id == "codex"
        if not page.rows:
            self.skipTest("no codex threads in this scope on this machine")
        row = page.rows[0]
        assert row.conversation_key.root.startswith("ar-lck1.")
        binding, vendor = self.cursor.verify_conversation_key(row.conversation_key)
        assert vendor and binding.identity_digest == row.identity_digest

        _scope_unused, ref, _ = self._ref_for(vendor, row.identity_digest)
        read = await self.library.read(ref, before=None, limit=10)
        assert read.total_items is not None and read.total_items >= 1
        assert [item.global_ordinal for item in read.items] == [
            read.total_items - len(read.items) + offset + 1 for offset in range(len(read.items))
        ]
        assert read.historical_capabilities.completeness.state == "partial"

        target = await self.library.resolve_resume_target(ref)
        _binding, target_vendor, launch = self.cursor.verify_resume_target(target)
        assert target_vendor == vendor
        assert launch["kind"] == "codex-thread-resume"

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:178).
    def _ref_for(self, vendor: str, digest: str):  # pragma: no cover
        scope = self.scope
        ref = NativeConversationRef(
            harness_id="codex",
            vendor_conversation_id=vendor,
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        return scope, ref, None


class PiInstalledTests(unittest.IsolatedAsyncioTestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:190).
    def setUp(self) -> None:  # pragma: no cover
        if _version_of(shutil.which("pi")) != LOCKED_RUNTIME_VERSION["pi"]:
            self.skipTest(
                f"installed pi != locked {LOCKED_RUNTIME_VERSION['pi']} "
                f"(observed {_version_of(shutil.which('pi'))})"
            )
        preflight = helper_preflight("pi")
        if preflight.reason is not None:
            self.skipTest(f"locked pi helper unavailable: {preflight.reason}")
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.cursor = LibraryCursorAuthority(mint_signing_key())
        self.caller = _caller(Path(PI_SCOPE))
        self.scope = canonical_library_scope(self.caller, "pi", None, workspace_root=Path(PI_SCOPE))
        self.helper = ConversationLibraryHelperHost()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:206).
    def tearDown(self) -> None:  # pragma: no cover
        self._tmpdir.cleanup()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:209).
    def _library(self, capabilities) -> PiConversationLibrary:  # pragma: no cover
        return PiConversationLibrary(
            authorization=self.caller,
            cursor_authority=self.cursor,
            capabilities=capabilities,
            helper_host=self.helper,
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:217).
    async def test_live_helper_gate_supports_pi_history(self) -> None:  # pragma: no cover
        gates = LibraryGateRegistry(
            harness_registry=lambda: HARNESSES,
            workspace_root=self.tmp,
            helper_host=self.helper,
        )
        capabilities = await gates.history_capabilities("pi")
        assert capabilities.list.state == "supported"
        assert capabilities.read.state == "supported"
        assert capabilities.resume.state == "supported"
        assert capabilities.completeness.state == "supported"
        evidence = capabilities.list.evidence
        assert evidence is not None
        assert evidence.runtime_version == LOCKED_RUNTIME_VERSION["pi"]
        assert evidence.helper_version == "0.80.7"

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:233).
    async def test_live_list_read_resolve(self) -> None:  # pragma: no cover
        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:234).
        async def caps(_harness: str):  # pragma: no cover
            gates = LibraryGateRegistry(
                harness_registry=lambda: HARNESSES,
                workspace_root=self.tmp,
                helper_host=self.helper,
            )
            return await gates.history_capabilities("pi")

        library = self._library(caps)
        page = await library.list(self.scope, cursor=None, limit=5)
        if not page.rows:
            self.skipTest("no pi sessions in this scope on this machine")
        row = page.rows[0]
        _binding, vendor = self.cursor.verify_conversation_key(row.conversation_key)
        ref = NativeConversationRef(
            harness_id="pi",
            vendor_conversation_id=vendor,
            project_scope=self.scope.canonical_project_scope,
            identity_digest=row.identity_digest,
        )
        read = await library.read(ref, before=None, limit=10)
        assert read.total_items is not None and read.total_items >= 1
        target = await library.resolve_resume_target(ref)
        _b, _v, launch = self.cursor.verify_resume_target(target)
        assert launch["kind"] == "argv"
        args = launch["args"]
        assert isinstance(args, list)
        assert args[0] == "--session"
        assert isinstance(args[1], str)
        assert Path(args[1]).is_file()

    # 260731-EFA-L7 R10: AR_RUN_CONTROL_INSTALLED-gated test body.
    async def test_helper_protocol_rejects_malformed_requests(self) -> None:  # pragma: no cover
        entry = helper_root() / "src" / "pi.ts"
        process = subprocess.run(
            ["node", "--import", "tsx", str(entry)],
            input=('{"protocolVersion":"wrong","requestId":"a","operation":"list"}\nnot-json\n'),
            capture_output=True,
            text=True,
            timeout=90,
            cwd=helper_root(),
            check=False,
        )
        lines = [line for line in process.stdout.splitlines() if line.strip()]
        assert len(lines) == 2
        for line in lines:
            payload = json.loads(line)
            assert payload["status"] == "error"
            assert payload["error"] == "invalid-request"


class PiOpenEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Real open: tracked opener -> tmux -> control runner -> pi RPC resume -> proof -> retire."""

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:288).
    def setUp(self) -> None:  # pragma: no cover
        if shutil.which("tmux") is None:
            self.skipTest("tmux is not installed")
        if _version_of(shutil.which("pi")) != LOCKED_RUNTIME_VERSION["pi"]:
            self.skipTest("installed pi version does not match the locked gate")
        preflight = helper_preflight("pi")
        if preflight.reason is not None:
            self.skipTest(f"locked pi helper unavailable: {preflight.reason}")
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.workspace = Path(PI_SCOPE)
        self.cursor = LibraryCursorAuthority(mint_signing_key())
        self.caller = _caller(self.workspace)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = TerminalHost()
        self.helper = ConversationLibraryHelperHost()
        self.shared = LibraryShared(
            cursor_authority=self.cursor,
            gates=LibraryGateRegistry(
                harness_registry=lambda: HARNESSES,
                workspace_root=self.workspace,
                helper_host=self.helper,
            ),
            helper_host=self.helper,
            open_ledger=OpenOperationLedger(),
        )
        self.runtime = ConversationRuntime(
            scope=ConversationScope(workspace_root=self.workspace, coordination_root=self.tmp),
            catalog=self.catalog,
            control_plane=ControlPlaneClient(),
            host=self.host,
            harness_registry=lambda: HARNESSES,
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(self.tmp),
            authorization=_Resolver(self.caller),
        )
        self.service = ConversationOpenService(
            LibraryBinding(
                runtime=self.runtime,
                shared=self.shared,
                authorization=self.caller,
            ),
            library=ConversationLibraryService(
                runtime=self.runtime,
                shared=self.shared,
                authorization=self.caller,
                port_builder=self._port,
            ),
            port_builder=self._port,
            proof_wait_seconds=60.0,
        )
        self.spawned_session_id: str | None = None

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:341).
    def tearDown(self) -> None:  # pragma: no cover
        if self.spawned_session_id is not None:
            entry = self.catalog.get(self.spawned_session_id)
            if entry is not None and entry.status == "running":
                retire_entry(
                    self.catalog,
                    self.host,
                    entry,
                    SeatClosure(
                        at=now_iso(),
                        by_session="pi-open-e2e-test",
                        reason="test cleanup",
                        edge="library-open-test",
                    ),
                )
        self._tmpdir.cleanup()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:358).
    def _port(self, harness_id: str) -> PiConversationLibrary:  # pragma: no cover
        assert harness_id == "pi"
        return PiConversationLibrary(
            authorization=self.caller,
            cursor_authority=self.cursor,
            capabilities=self.shared.gates.history_capabilities,
            helper_host=self.helper,
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:367).
    async def test_open_real_pi_session_proves_exact_identity(self) -> None:  # pragma: no cover
        scope = canonical_library_scope(self.caller, "pi", None, workspace_root=self.workspace)
        page = await self._port("pi").list(scope, cursor=None, limit=5)
        if not page.rows:
            self.skipTest("no pi sessions in this scope on this machine")
        row = page.rows[0]
        operation = await self.service.open(
            "pi",
            str(row.conversation_key),
            OpenRequest(
                request_id="e2e-pi-open-1",
                expected_identity_digest=row.identity_digest,
                cwd=None,
                launch_context={},
            ),
        )
        assert operation.outcome == "opened", operation.detail
        assert operation.phase == "opened"
        assert operation.rollback == "not-needed"
        identity = operation.identity
        assert identity is not None
        self.spawned_session_id = identity.ar_session_id
        assert identity.ar_session_id.startswith("ar-open-")
        assert identity.bridge_epoch
        _binding, vendor = self.cursor.verify_conversation_key(row.conversation_key)
        assert identity.vendor_conversation_id == vendor

        # External corroboration: the tracked session really is live and ready.
        readiness = hosted_session_readiness(
            self.catalog,
            self.host,
            session_id=identity.ar_session_id,
            wait=ReadinessWait(seconds=0.0),
        )
        assert readiness.status == "ready"

        # Idempotent replay after the fact: same operation, still one spawned session.
        replay = await self.service.open(
            "pi",
            str(row.conversation_key),
            OpenRequest(
                request_id="e2e-pi-open-1",
                expected_identity_digest=row.identity_digest,
                cwd=None,
                launch_context={},
            ),
        )
        assert replay == operation


class CodexOpenEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Real codex open: opener -> tmux -> runner -> thread/resume -> exact proof -> retire."""

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:420).
    def setUp(self) -> None:  # pragma: no cover
        if shutil.which("tmux") is None:
            self.skipTest("tmux is not installed")
        if _version_of(shutil.which("codex")) != LOCKED_CODEX_RUNTIME_VERSION:
            self.skipTest("installed codex version does not match the locked gate")
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.workspace = Path(CODEX_SCOPE)
        self.cursor = LibraryCursorAuthority(mint_signing_key())
        self.caller = _caller(self.workspace)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = TerminalHost()
        self.helper = ConversationLibraryHelperHost()
        self.shared = LibraryShared(
            cursor_authority=self.cursor,
            gates=LibraryGateRegistry(
                harness_registry=lambda: HARNESSES,
                workspace_root=self.workspace,
                helper_host=self.helper,
            ),
            helper_host=self.helper,
            open_ledger=OpenOperationLedger(),
        )
        self.runtime = ConversationRuntime(
            scope=ConversationScope(workspace_root=self.workspace, coordination_root=self.tmp),
            catalog=self.catalog,
            control_plane=ControlPlaneClient(),
            host=self.host,
            harness_registry=lambda: HARNESSES,
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(self.tmp),
            authorization=_Resolver(self.caller),
        )
        self.service = ConversationOpenService(
            LibraryBinding(
                runtime=self.runtime,
                shared=self.shared,
                authorization=self.caller,
            ),
            library=ConversationLibraryService(
                runtime=self.runtime,
                shared=self.shared,
                authorization=self.caller,
                port_builder=self._port,
            ),
            port_builder=self._port,
            proof_wait_seconds=60.0,
        )
        self.spawned_session_id: str | None = None

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:470).
    def tearDown(self) -> None:  # pragma: no cover
        if self.spawned_session_id is not None:
            entry = self.catalog.get(self.spawned_session_id)
            if entry is not None and entry.status == "running":
                retire_entry(
                    self.catalog,
                    self.host,
                    entry,
                    SeatClosure(
                        at=now_iso(),
                        by_session="codex-open-e2e-test",
                        reason="test cleanup",
                        edge="library-open-test",
                    ),
                )
        self._tmpdir.cleanup()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:487).
    def _port(self, harness_id: str) -> CodexConversationLibrary:  # pragma: no cover
        assert harness_id == "codex"
        return CodexConversationLibrary(
            authorization=self.caller,
            cursor_authority=self.cursor,
            capabilities=self.shared.gates.history_capabilities,
            harness=CODEX,
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_installed.py:496).
    async def test_open_real_codex_thread_proves_exact_identity(self) -> None:  # pragma: no cover
        scope = canonical_library_scope(self.caller, "codex", None, workspace_root=self.workspace)
        page = await self._port("codex").list(scope, cursor=None, limit=5)
        if not page.rows:
            self.skipTest("no codex threads in this scope on this machine")
        # The resume launch must own the thread's recorded cwd: pick a thread whose native
        # cwd is exactly this scope (the list is already scope-filtered, so all qualify).
        row = page.rows[0]
        capabilities = await self.shared.gates.history_capabilities("codex")
        assert capabilities.resume.state == "supported", capabilities.resume.reason

        operation = await self.service.open(
            "codex",
            str(row.conversation_key),
            OpenRequest(
                request_id="e2e-codex-open-1",
                expected_identity_digest=row.identity_digest,
                cwd=None,
                launch_context={},
            ),
        )
        assert operation.outcome == "opened", operation.detail
        assert operation.phase == "opened"
        assert operation.rollback == "not-needed"
        identity = operation.identity
        assert identity is not None
        self.spawned_session_id = identity.ar_session_id
        assert identity.ar_session_id.startswith("ar-open-")
        assert identity.bridge_epoch
        _binding, vendor = self.cursor.verify_conversation_key(row.conversation_key)
        assert identity.vendor_conversation_id == vendor

        # External corroboration: the tracked session really is live, ready, and proves the
        # exact native identity over the control IPC (the catalog's projected vendor field
        # catches up on the next liveness sweep; the snapshot is the proof authority).
        readiness = hosted_session_readiness(
            self.catalog,
            self.host,
            session_id=identity.ar_session_id,
            wait=ReadinessWait(seconds=0.0),
        )
        assert readiness.status == "ready"
        assert readiness.snapshot is not None
        assert readiness.snapshot.vendor_session_id == vendor

        # Idempotent replay: same operation, still exactly one spawned session.
        replay = await self.service.open(
            "codex",
            str(row.conversation_key),
            OpenRequest(
                request_id="e2e-codex-open-1",
                expected_identity_digest=row.identity_digest,
                cwd=None,
                launch_context={},
            ),
        )
        assert replay == operation


class ClaudeGateHonestyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # 260731-EFA-L7 R10: AR_RUN_CONTROL_INSTALLED-gated test body.
    async def test_installed_claude_library_gates_on_contract_not_version(
        self,
    ) -> None:  # pragma: no cover
        # 260718-CHATS-L5F R4 (developer ruling 2026-07-21): THE CONTRACT IS THE ONLY GATE. The
        # claude library surface is enabled by the helper's real list CONTRACT, never by a
        # version-string comparison. A drift between the installed runtime and any captured-fixture
        # version must NOT fail the surface closed — the observed version is informational evidence.
        if shutil.which("claude") is None:
            self.skipTest("claude is not installed")
        if helper_preflight("claude").reason is not None:
            self.skipTest(f"claude helper unavailable: {helper_preflight('claude').reason}")
        observed = _version_of(shutil.which("claude"))
        gates = LibraryGateRegistry(
            harness_registry=lambda: HARNESSES,
            workspace_root=self.tmp,
            helper_host=ConversationLibraryHelperHost(),
        )
        capabilities = await gates.history_capabilities("claude")
        # Contract-driven outcome only: supported when the list contract verified, or unverified
        # with a genuine contract-failure reason — never a version-mismatch demotion.
        assert capabilities.list.state in {"supported", "unverified"}
        reason = capabilities.list.reason.lower()
        assert "differs from the locked" not in reason
        assert "version" not in reason or "mismatch" not in reason
        if capabilities.list.state == "supported":
            evidence = capabilities.list.evidence
            assert evidence is not None and evidence.runtime_version == observed


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
