"""Installed-runtime evidence capture for the 260718-CHATS-L0E substrate.

Opt-in production-seam proof: real installed harness -> real adapter -> control bridge -> IPC
server -> blocking client, exercising the evidence family and the codex resume channel against
the versions pinned in ``mcp/tests/fixtures/conversation_runtime``. Skips carry exact reasons on
machines without the pinned runtimes (CI); the Claude row stays honestly not-exercised while the
installed version mismatches the locked gate.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from agents_remember.errors import HarnessControlError
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    read_control_evidence,
    read_control_native_page,
    read_control_snapshot,
    read_submission_authority,
    read_submission_provenance,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_factories import create_harness_protocol_adapter
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    ControlIdentity,
    ControlOperationRef,
    LaunchSpec,
    NativeEvidenceFrame,
    PromptRequest,
)
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter


def _obj(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "conversation_runtime"
LIVE_OPT_IN = "AR_RUN_EVIDENCE_INSTALLED"
CODEX_PINNED = "0.144.5"
PI_PINNED = "0.80.7"
CLAUDE_LOCKED = "2.1.211"
PROMPT = "Reply with exactly the word OK and nothing else."


def _identity(session: str) -> ControlIdentity:
    return ControlIdentity(
        ar_session_id=session,
        tmux_name=f"ar-{session}",
        created_at="2026-07-19T09:00:00+00:00",
    )


def _launch(identity: ControlIdentity, harness_id: str, cwd: Path, argv: tuple[str, ...]):
    return LaunchSpec(
        identity=identity,
        harness_id=harness_id,
        cwd=cwd,
        argv=argv,
        env=dict(os.environ),
    )


class _ControlledEntry:
    def __init__(self, identity: ControlIdentity, endpoint: Path) -> None:
        self.id = identity.ar_session_id
        self.tmux_name = identity.tmux_name
        self.created_at = identity.created_at
        self.control_endpoint = endpoint


def _version_of(executable: str, args: tuple[str, ...] = ("--version",)) -> str:
    completed = subprocess.run(
        [executable, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


async def _wait_for_evidence_kind(entry, kind: str, *, timeout_seconds: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        page = await asyncio.to_thread(read_control_evidence, entry)
        if any(frame.kind == kind for frame in page.frames):
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"evidence kind {kind!r} never crossed the production seam")
        await asyncio.sleep(1.0)


@unittest.skipUnless(
    os.environ.get(LIVE_OPT_IN) == "1",
    f"set {LIVE_OPT_IN}=1 to exercise installed runtimes through the production evidence seam",
)
class CodexInstalledEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        executable = shutil.which("codex")
        if executable is None:
            self.skipTest("installed codex executable is unavailable")
        version = _version_of(executable).removeprefix("codex-cli ")
        if version != CODEX_PINNED:
            self.skipTest(
                f"installed codex {version} does not match pinned {CODEX_PINNED}; "
                "fixture evidence stays version-locked"
            )
        self.executable = executable

    async def test_live_evidence_family_and_resume_channel_through_production_seam(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ar-evidence-codex-") as tmp:
            root = Path(tmp)
            identity = _identity("codex-installed")
            adapter = CodexAppServerAdapter(CodexAppServerSettings(ephemeral=True))
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity, "codex", root, (self.executable, "app-server")))
            endpoint = LocalControlEndpoint.for_session(root / "ctl", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(identity, endpoint.path)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    PROMPT,
                    source="cockpit",
                    request_id="installed-codex-1",
                    expected_bridge_epoch=descriptor.bridge_epoch,
                )
                self.assertIn(receipt.acceptance, {"immediate", "queued"})
                await _wait_for_evidence_kind(entry, "completed", timeout_seconds=180.0)
                page = await asyncio.to_thread(read_control_evidence, entry)
                kinds = [frame.kind for frame in page.frames]
                self.assertIn("codex-notification", kinds)
                payloads = [frame.raw for frame in page.frames]
                item_payloads = [
                    _obj(payload["item"])
                    for payload in payloads
                    if isinstance(payload.get("item"), dict)
                ]
                item_types = {payload.get("type") for payload in item_payloads}
                self.assertIn("userMessage", item_types)
                self.assertIn("agentMessage", item_types)
                self.assertTrue(
                    all(
                        isinstance(payload.get("id"), str) and payload["id"]
                        for payload in item_payloads
                    )
                )
                usage = [
                    payload
                    for payload in payloads
                    if payload.get("codexMethod") == "thread/tokenUsage/updated"  # type: ignore[attr-defined]
                    or "tokenUsage" in payload
                ]
                self.assertTrue(usage, "thread/tokenUsage/updated never reached evidence")
                turn_completed = [
                    payload for payload in payloads if payload.get("turn", None) is not None
                ]
                self.assertTrue(
                    turn_completed or any("turnId" in payload for payload in payloads),
                    "turn/completed never reached evidence",
                )
                # Ephemeral threads honestly refuse thread/read includeTurns; the typed
                # failure is the visible capability boundary, never a guessed page.
                with self.assertRaises(HarnessControlError) as ephemeral_refusal:
                    await asyncio.to_thread(read_control_native_page, entry)
                self.assertIn("ephemeral", str(ephemeral_refusal.exception))
                # No leak across the same production seam.
                snapshot = await asyncio.to_thread(read_control_snapshot, entry)
                self.assertNotIn(AR_EVIDENCE_KEY, snapshot.raw)
                self.assertNotIn("arEvidence", json.dumps(snapshot.raw))
                provenance = await asyncio.to_thread(
                    read_submission_provenance,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                    request_ids=("installed-codex-1",),
                )
                self.assertEqual(provenance.provenance[0].source, "cockpit")
            finally:
                await server.close()
                await bridge.stop("forced")

            # Persisted thread: the native page crosses with typed identity, and a second
            # factory-built adapter resumes the exact thread through the new channel.
            thread_id, persisted_frames = await self._live_completed_thread(root)
            self.assertIn("userMessage", {f.native_type for f in persisted_frames})
            resume_identity = _identity("codex-installed-resume")
            resumed = create_harness_protocol_adapter(
                "codex",
                env=dict(os.environ),
                resume_thread_id=thread_id,
            )
            assert isinstance(resumed, CodexAppServerAdapter)
            resume_bridge = HarnessControlBridge(resume_identity, resumed)
            await resume_bridge.start(
                _launch(resume_identity, "codex", root, (self.executable, "app-server"))
            )
            try:
                snapshot = await resumed.snapshot()
                self.assertEqual(snapshot.vendor_session_id, thread_id)
                page = await resumed.read_native_page(cursor=None, limit=200, byte_budget=48 * 1024)
                self.assertIn("userMessage", {frame.native_type for frame in page.frames})
            finally:
                await resume_bridge.stop("forced")

    async def _live_completed_thread(
        self, cwd: Path
    ) -> tuple[str, tuple[NativeEvidenceFrame, ...]]:
        identity = _identity("codex-installed-persist")
        adapter = CodexAppServerAdapter(CodexAppServerSettings(ephemeral=False))
        await adapter.start(_launch(identity, "codex", cwd, (self.executable, "app-server")))
        events = adapter.subscribe()
        try:
            operation = ControlOperationRef(
                bridge_epoch="installed-resume-epoch",
                sequence=1,
                operation_id="installed-resume-prompt",
                kind="prompt",
            )
            await adapter.preflight_operation(operation)
            receipt = await adapter.submit(
                PromptRequest(
                    request_id="installed-resume-prompt",
                    source="durable",
                    text=PROMPT,
                    submitted_at="2026-07-19T09:00:00+00:00",
                    operation=operation,
                )
            )
            self.assertIn(receipt.acceptance, {"immediate", "queued"})
            deadline = asyncio.get_running_loop().time() + 180.0
            while asyncio.get_running_loop().time() < deadline:
                event = await asyncio.wait_for(anext(events), timeout=30.0)
                if event.kind == "completed":
                    break
            else:
                self.fail("persisted codex thread never completed its probe turn")
            snapshot = await adapter.snapshot()
            thread_id = snapshot.vendor_session_id
            assert thread_id is not None
            frames = (
                await adapter.read_native_page(cursor=None, limit=200, byte_budget=48 * 1024)
            ).frames
            return thread_id, frames
        finally:
            await adapter.stop("forced")


@unittest.skipUnless(
    os.environ.get(LIVE_OPT_IN) == "1",
    f"set {LIVE_OPT_IN}=1 to exercise installed runtimes through the production evidence seam",
)
class PiInstalledEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        executable = shutil.which("pi")
        if executable is None:
            self.skipTest("installed pi executable is unavailable")
        version = _version_of(executable)
        if version != PI_PINNED:
            self.skipTest(
                f"installed pi {version} does not match pinned {PI_PINNED}; "
                "fixture evidence stays version-locked"
            )
        self.executable = executable

    async def test_live_evidence_family_through_production_seam(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ar-evidence-pi-") as tmp:
            root = Path(tmp)
            identity = _identity("pi-installed")
            adapter = PiRpcAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity, "pi", root, (self.executable,)))
            endpoint = LocalControlEndpoint.for_session(root / "ctl", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(identity, endpoint.path)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    PROMPT,
                    source="cockpit",
                    request_id="installed-pi-1",
                    expected_bridge_epoch=descriptor.bridge_epoch,
                )
                self.assertIn(receipt.acceptance, {"immediate", "queued"})
                await _wait_for_evidence_kind(entry, "transcript", timeout_seconds=180.0)
                page = await asyncio.to_thread(read_control_evidence, entry)
                message_end = [
                    frame.raw for frame in page.frames if frame.raw.get("type") == "message_end"
                ]
                self.assertTrue(message_end, "message_end never reached evidence")
                self.assertTrue(
                    any(
                        isinstance(frame.raw, dict) and frame.kind.startswith("pi:")
                        for frame in page.frames
                    ),
                    "pi message/tool frames never reached evidence",
                )
                native = await asyncio.to_thread(read_control_native_page, entry)
                self.assertEqual(native.bridge_epoch, descriptor.bridge_epoch)
                self.assertTrue(native.frames, "durable entries never crossed the native page")
                self.assertTrue(all(frame.native_id for frame in native.frames))
                self.assertTrue(all(frame.native_type for frame in native.frames))
                snapshot = await asyncio.to_thread(read_control_snapshot, entry)
                self.assertNotIn(AR_EVIDENCE_KEY, snapshot.raw)
                provenance = await asyncio.to_thread(
                    read_submission_provenance,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                    request_ids=("installed-pi-1",),
                )
                self.assertEqual(provenance.provenance[0].source, "cockpit")
            finally:
                await server.close()
                await bridge.stop("forced")


class ClaudeInstalledHonestyTests(unittest.TestCase):
    def test_version_mismatch_keeps_claude_rows_not_exercised(self) -> None:
        executable = shutil.which("claude")
        if executable is None:
            self.skipTest("installed claude executable is unavailable")
        version = _version_of(executable).split(" ", 1)[0]
        fixture = json.loads((FIXTURE_ROOT / f"claude-{CLAUDE_LOCKED}.json").read_text())
        substrate_rows = [
            row
            for row in fixture["observations"]
            if row["operation"].startswith("substrate-evidence/")
        ]
        if version != CLAUDE_LOCKED:
            self.assertTrue(
                all(row["result"] == "not-exercised" for row in substrate_rows),
                "version-mismatched claude must keep substrate rows not-exercised",
            )
            for row in substrate_rows:
                self.assertIn(CLAUDE_LOCKED, row["reason"])
        else:
            self.skipTest(
                "locked claude install present; capture belongs to a locked-gate evidence run"
            )
