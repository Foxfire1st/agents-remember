"""Installed-runtime control-plane evidence capture for the 260718-CHATS-L2E substrate.

Opt-in production-seam proof: real installed harness -> real adapter -> control bridge -> IPC
server -> blocking client, exercising the interrupt write, operation-timeline enumeration,
asset channel, and withdrawal recovery against the pinned versions. Skips carry exact reasons
on machines without the pinned runtimes; the Claude rows stay honestly not-exercised while the
installed version mismatches the locked gate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    LaunchSpec,
)
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    interrupt_control,
    read_control_evidence,
    read_operation_timeline,
    read_submission_authority,
    submit_control_prompt,
    withdraw_control_submission,
)
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "conversation_runtime"
LIVE_OPT_IN = "AR_RUN_CONTROL_PLANE_INSTALLED"
CODEX_PINNED = "0.144.5"
PI_PINNED = "0.80.7"
CLAUDE_LOCKED = "2.1.211"
LONG_PROMPT = "Write a 600-word essay about the ocean. Start writing immediately."
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:59).
def _identity(session: str) -> ControlIdentity:  # pragma: no cover
    return ControlIdentity(
        ar_session_id=session,
        tmux_name=f"ar-{session}",
        created_at="2026-07-19T13:00:00+00:00",
    )


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:67).
def _launch(
    identity: ControlIdentity, harness_id: str, cwd: Path, argv: tuple[str, ...]
):  # pragma: no cover
    return LaunchSpec(
        identity=identity,
        harness_id=harness_id,
        cwd=cwd,
        argv=argv,
        env=dict(os.environ),
    )


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:77).
def _obj(value: object) -> Mapping[str, object]:  # pragma: no cover
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


class _ControlledEntry:
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:83).
    def __init__(self, identity: ControlIdentity, endpoint: Path) -> None:  # pragma: no cover
        self.id = identity.ar_session_id
        self.tmux_name = identity.tmux_name
        self.created_at = identity.created_at
        self.control_endpoint = endpoint


def _version_of(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise unittest.SkipTest(f"installed harness is not runnable: {error}") from error
    return completed.stdout.strip()


# 260731-EFA-L7 R10: AR_RUN_CONTROL_PLANE_INSTALLED-gated helper.
async def _wait_evidence(
    entry, predicate, *, timeout_seconds: float, description: str
) -> None:  # pragma: no cover
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        page = await asyncio.to_thread(read_control_evidence, entry)
        if any(predicate(frame) for frame in page.frames):
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{description} never crossed the production seam")
        await asyncio.sleep(0.5)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:115).
def _stage_png(root: Path, request_id: str, asset_id: str) -> dict[str, object]:  # pragma: no cover
    target = root / "assets" / request_id / asset_id
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(TINY_PNG)
    return {
        "assetId": asset_id,
        "mimeType": "image/png",
        "byteSize": len(TINY_PNG),
        "sha256": hashlib.sha256(TINY_PNG).hexdigest(),
    }


@pytest.mark.ar_run_control_plane_installed
@unittest.skipUnless(
    os.environ.get(LIVE_OPT_IN) == "1",
    f"set {LIVE_OPT_IN}=1 to exercise installed runtimes through the control-plane seam",
)
class CodexInstalledControlPlaneTests(unittest.IsolatedAsyncioTestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:133).
    def setUp(self) -> None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: AR_RUN_CONTROL_PLANE_INSTALLED-gated test body.
    async def test_live_interrupt_timeline_assets_and_recovery(self) -> None:  # pragma: no cover
        with tempfile.TemporaryDirectory(prefix="ar-plane-codex-") as tmp:
            root = Path(tmp)
            identity = _identity("codex-plane")
            adapter = CodexAppServerAdapter(CodexAppServerSettings(ephemeral=True))
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity, "codex", root, (self.executable, "app-server")))
            endpoint = LocalControlEndpoint.for_session(root / "ctl", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(identity, endpoint.path)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    LONG_PROMPT,
                    ControlSubmission(
                        source="cockpit", request_id="plane-codex-long", expected_bridge_epoch=epoch
                    ),
                )
                self.assertIn(receipt.acceptance, {"immediate", "queued"})
                await _wait_evidence(
                    entry,
                    lambda frame: (
                        "turnId" in frame.raw or frame.raw.get("codexMethod") == "turn/started"
                    ),
                    timeout_seconds=60.0,
                    description="codex turn start",
                )
                ack = await asyncio.to_thread(
                    interrupt_control,
                    entry,
                    expected_bridge_epoch=epoch,
                )
                self.assertEqual(ack.acknowledgement, "accepted")
                self.assertEqual(ack.bridge_epoch, epoch)
                self.assertTrue(ack.vendor_correlation_id)
                self.assertIsNotNone(ack.operation)
                await _wait_evidence(
                    entry,
                    lambda frame: frame.kind == "completed",
                    timeout_seconds=120.0,
                    description="codex interrupted settlement",
                )
                page = await asyncio.to_thread(read_control_evidence, entry)
                completed = [frame.raw for frame in page.frames if frame.kind == "completed"]
                statuses = {
                    _obj(payload["turn"]).get("status")
                    for payload in completed
                    if isinstance(payload.get("turn"), dict)
                }
                self.assertIn("interrupted", statuses)
                # A second interrupt against the settled thread fails typed with no active turn.
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=epoch,
                    )
                # Timeline: the long prompt enumerates with kind/source/sequence, never a body.
                timeline = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                row = next(
                    item for item in timeline.items if item.operation_id == "plane-codex-long"
                )
                self.assertEqual((row.kind, row.source), ("prompt", "cockpit"))
                self.assertGreater(row.sequence, 0)
                self.assertTrue(row.payload_digest_present)
                self.assertEqual(timeline.bridge_epoch, epoch)
                # Asset channel: a real staged PNG rides to native acceptance.
                staged = _stage_png(endpoint.path.parent, "plane-codex-asset", "img-1")
                asset_receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "Describe this image in one short sentence.",
                    ControlSubmission(
                        source="cockpit",
                        request_id="plane-codex-asset",
                        expected_bridge_epoch=epoch,
                        assets=[staged],
                    ),
                )
                self.assertEqual(asset_receipt.acceptance, "immediate")
                self.assertEqual(asset_receipt.raw.get("assetIds"), ["img-1"])
                # Withdrawal recovery: a queued cockpit prompt withdraws with its exact body.
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    LONG_PROMPT,
                    ControlSubmission(
                        source="cockpit", request_id="plane-codex-head", expected_bridge_epoch=epoch
                    ),
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "the exact queued body",
                    ControlSubmission(
                        source="cockpit",
                        request_id="plane-codex-recovery",
                        expected_bridge_epoch=epoch,
                    ),
                )
                withdrawn = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="plane-codex-recovery",
                )
                self.assertEqual(withdrawn.outcome, "withdrawn")
                assert withdrawn.recovery is not None
                self.assertEqual(withdrawn.recovery.text, "the exact queued body")
                replay = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="plane-codex-recovery",
                )
                self.assertIsNone(replay.recovery)
            finally:
                await server.close()
                await bridge.stop("forced")


@pytest.mark.ar_run_control_plane_installed
@unittest.skipUnless(
    os.environ.get(LIVE_OPT_IN) == "1",
    f"set {LIVE_OPT_IN}=1 to exercise installed runtimes through the control-plane seam",
)
class PiInstalledControlPlaneTests(unittest.IsolatedAsyncioTestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:279).
    def setUp(self) -> None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:291).
    async def test_live_abort_guard_timeline_and_assets(self) -> None:  # pragma: no cover
        with tempfile.TemporaryDirectory(prefix="ar-plane-pi-") as tmp:
            root = Path(tmp)
            identity = _identity("pi-plane")
            adapter = PiRpcAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity, "pi", root, (self.executable,)))
            endpoint = LocalControlEndpoint.for_session(root / "ctl", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(identity, endpoint.path)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    LONG_PROMPT,
                    ControlSubmission(
                        source="cockpit", request_id="plane-pi-long", expected_bridge_epoch=epoch
                    ),
                )
                self.assertIn(receipt.acceptance, {"immediate", "queued"})
                await _wait_evidence(
                    entry,
                    lambda frame: True,
                    timeout_seconds=60.0,
                    description="pi activity before abort",
                )
                ack = await asyncio.to_thread(
                    interrupt_control,
                    entry,
                    expected_bridge_epoch=epoch,
                    expected_operation_id="plane-pi-long",
                )
                self.assertEqual(ack.acknowledgement, "accepted")
                self.assertEqual(ack.bridge_epoch, epoch)
                self.assertIsNotNone(ack.operation)
                self.assertEqual(ack.operation.operation_id, "plane-pi-long")  # type: ignore[union-attr]
                # Stale reconcile after settlement/successor: the A identity fails typed.
                await _wait_evidence(
                    entry,
                    lambda frame: (
                        frame.kind in {"transcript", "pi:agent_end", "pi:agent_settled"}
                        or frame.raw.get("type") == "agent_settled"
                    ),
                    timeout_seconds=120.0,
                    description="pi settlement",
                )
                second = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "Write one short sentence about the sky.",
                    ControlSubmission(
                        source="cockpit", request_id="plane-pi-second", expected_bridge_epoch=epoch
                    ),
                )
                self.assertIn(second.acceptance, {"immediate", "queued"})
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=epoch,
                        expected_operation_id="plane-pi-long",
                    )
                timeline = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                row = next(item for item in timeline.items if item.operation_id == "plane-pi-long")
                self.assertEqual((row.kind, row.source), ("prompt", "cockpit"))
                staged = _stage_png(endpoint.path.parent, "plane-pi-asset", "img-1")
                asset_receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "Describe this image in one short sentence.",
                    ControlSubmission(
                        source="cockpit",
                        request_id="plane-pi-asset",
                        expected_bridge_epoch=epoch,
                        assets=[staged],
                    ),
                )
                self.assertIn(asset_receipt.acceptance, {"immediate", "queued"})
                self.assertEqual(asset_receipt.raw.get("assetIds"), ["img-1"])
            finally:
                await server.close()
                await bridge.stop("forced")


class ClaudeInstalledHonestyTests(unittest.TestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_installed.py:381).
    def test_version_mismatch_keeps_claude_control_plane_rows_not_exercised(
        self,
    ) -> None:  # pragma: no cover
        executable = shutil.which("claude")
        if executable is None:
            self.skipTest("installed claude executable is unavailable")
        version = _version_of(executable).split(" ", 1)[0]
        fixture = json.loads((FIXTURE_ROOT / f"claude-{CLAUDE_LOCKED}.json").read_text())
        rows = [
            row for row in fixture["observations"] if row["operation"].startswith("control-plane/")
        ]
        if version != CLAUDE_LOCKED:
            self.assertTrue(rows, "expected control-plane honesty rows in the claude fixture")
            self.assertTrue(all(row["result"] == "not-exercised" for row in rows))
            for row in rows:
                self.assertIn(CLAUDE_LOCKED, row["reason"])
        else:
            self.skipTest(
                "locked claude install present; capture belongs to a locked-gate evidence run"
            )
