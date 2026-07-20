"""Installed-runtime production proof for the L3 control API (260718-CHATS-L3, R7).

Opt-in (`AR_RUN_CONTROL_INSTALLED=1`, version-locked): real installed harness
-> real adapter -> control bridge -> IPC server -> real catalog row -> the L0
composition -> a real uvicorn wire into the L3 control routes. Proves live
interrupt acknowledgement AND settlement, source-aware queue truth, withdrawal
recovery, typed attachment submit, and evidence-bound telemetry through the
registered production routes — never through scenario fixtures.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
import uvicorn
from _control_plane import OPERATOR, TINY_PNG
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.router import register_conversation_routes
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    ConversationScope,
)
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    read_control_evidence,
    read_submission_authority,
)
from agents_remember.serving.harness_control_ipc import (
    HarnessControlServer,
    LocalControlEndpoint,
)
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    LaunchSpec,
)
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    utc_now,
)
from fastapi import FastAPI

LIVE_OPT_IN = "AR_RUN_CONTROL_INSTALLED"
CODEX_PINNED = "0.144.5"
PI_PINNED = "0.80.7"
NOW = "2026-07-20T13:00:00+00:00"
LONG_PROMPT = "Write a 600-word essay about the ocean. Start writing immediately."


def _identity(session: str) -> ControlIdentity:
    return ControlIdentity(
        ar_session_id=session, tmux_name=f"ar-{session}", created_at=NOW
    )


def _version_of(executable: str) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


class _LiveHost:
    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return True


class _ControlledEntry:
    def __init__(self, identity: ControlIdentity, endpoint: Path) -> None:
        self.id = identity.ar_session_id
        self.tmux_name = identity.tmux_name
        self.created_at = identity.created_at
        self.control_endpoint = endpoint


class _LiveHarness:
    """Real adapter + bridge + IPC + catalog + composition + uvicorn wire."""

    def __init__(self, root: Path, identity: ControlIdentity, adapter: object, harness: str) -> None:
        self.identity = identity
        self.adapter = adapter
        self.bridge = HarnessControlBridge(identity, adapter)  # type: ignore[arg-type]
        self.endpoint = LocalControlEndpoint.for_session(root / "ctl", identity)
        self.server = HarnessControlServer(self.endpoint, self.bridge)
        catalog = TerminalCatalog(root / "terminal-sessions.json")
        catalog.upsert(
            TerminalCatalogEntry(
                id=identity.ar_session_id,
                label=identity.ar_session_id,
                kind="harness",
                harness=harness,
                lifecycle_id=None,
                cwd=root,
                tmux_name=identity.tmux_name,
                command=("live",),
                created_at=NOW,
                last_attached_at=NOW,
                status="running",
                control_endpoint=self.endpoint.path,
            )
        )
        self.runtime = ConversationRuntime(
            scope=ConversationScope(workspace_root=root, coordination_root=root),
            catalog=catalog,
            host=_LiveHost(),
            harness_registry=list,
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(root),
            authorization=LocalOperatorAuthorizationResolver.for_workspace(root),
        )
        self.app = FastAPI()
        register_conversation_routes(self.app, self.runtime)
        self.control_entry = _ControlledEntry(identity, self.endpoint.path)
        self._auth_patcher = mock.patch(
            "agents_remember.serving.conversation.control.api.resolve_conversation_authorization",
            lambda request: OPERATOR,
        )

    async def start(self, launch: LaunchSpec) -> None:
        await self.bridge.start(launch)
        await self.server.start()
        self.epoch = (
            await asyncio.to_thread(read_submission_authority, self.control_entry)
        ).bridge_epoch
        self._auth_patcher.start()
        self._uvicorn = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
        )
        self._uvicorn_task = asyncio.create_task(self._uvicorn.serve())
        while not self._uvicorn.started:
            await asyncio.sleep(0.05)
        port = self._uvicorn.servers[0].sockets[0].getsockname()[1]
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0)

    async def stop(self) -> None:
        self._auth_patcher.stop()
        await self.client.aclose()
        self._uvicorn.should_exit = True
        await self._uvicorn_task
        await self.server.close()
        await self.bridge.stop("forced")

    def params(self) -> dict[str, str]:
        return {"expectedBridgeEpoch": self.epoch}

    async def wait_evidence(self, predicate, *, timeout: float, description: str) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            page = await asyncio.to_thread(read_control_evidence, self.control_entry)
            if any(predicate(frame) for frame in page.frames):
                return
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"{description} never crossed the production seam")
            await asyncio.sleep(0.5)

    async def typed_submit(self, request_id: str, text: str) -> httpx.Response:
        return await self.client.post(
            f"/api/terminal/{self.identity.ar_session_id}/conversation/submit",
            json={
                "expectedBridgeEpoch": self.epoch,
                "requestId": request_id,
                "disposition": "next",
                "content": [{"type": "text", "text": text}],
                "draftRevision": 1,
            },
        )


@unittest.skipUnless(
    os.environ.get(LIVE_OPT_IN) == "1",
    f"set {LIVE_OPT_IN}=1 to exercise installed runtimes through the L3 control routes",
)
class CodexInstalledControlApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        executable = shutil.which("codex")
        if executable is None:
            self.skipTest("installed codex executable is unavailable")
        version = _version_of(executable).removeprefix("codex-cli ")
        if version != CODEX_PINNED:
            self.skipTest(
                f"installed codex {version} does not match pinned {CODEX_PINNED}"
            )
        self.executable = executable

    async def test_live_interrupt_settlement_queue_recovery_assets_and_telemetry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ar-l3-codex-") as tmp:
            root = Path(tmp)
            identity = _identity("codex-l3")
            adapter = CodexAppServerAdapter(CodexAppServerSettings(ephemeral=True))
            harness = _LiveHarness(root, identity, adapter, "codex")
            await harness.start(
                LaunchSpec(
                    identity=identity,
                    harness_id="codex",
                    cwd=root,
                    argv=(self.executable, "app-server"),
                    env=dict(os.environ),
                )
            )
            session = identity.ar_session_id
            try:
                submit = await harness.typed_submit("l3-codex-long", LONG_PROMPT)
                self.assertEqual(submit.status_code, 200, submit.text)
                self.assertIn(submit.json()["acceptance"], {"immediate", "queued"})
                await harness.wait_evidence(
                    lambda frame: isinstance(frame.raw.get("turnId"), str),
                    timeout=60.0,
                    description="codex turn id",
                )
                page = await asyncio.to_thread(read_control_evidence, harness.control_entry)
                turn_id = next(
                    frame.raw["turnId"]
                    for frame in page.frames
                    if isinstance(frame.raw.get("turnId"), str)
                )
                interrupt = await harness.client.post(
                    f"/api/terminal/{session}/conversation/interrupt",
                    params=harness.params(),
                    json={"turnId": turn_id, "requestId": "l3-int-1"},
                )
                self.assertEqual(interrupt.status_code, 202, interrupt.text)
                operation = interrupt.json()
                self.assertEqual(operation["acknowledgement"], "accepted")
                self.assertEqual(operation["settlement"], "pending")
                await harness.wait_evidence(
                    lambda frame: frame.kind == "completed",
                    timeout=120.0,
                    description="codex interrupted settlement",
                )
                status = await harness.client.post(
                    f"/api/terminal/{session}/conversation/interrupt-status",
                    params=harness.params(),
                    json={"turnId": turn_id, "requestId": "l3-int-1"},
                )
                self.assertEqual(status.status_code, 200, status.text)
                settled = status.json()
                self.assertEqual(settled["settlement"], "interrupted")
                self.assertGreater(settled["revision"], operation["revision"])
                # Queue truth over the wire: the long prompt enumerates, never a body.
                queue = await harness.client.get(
                    f"/api/terminal/{session}/operation-queue", params=harness.params()
                )
                self.assertEqual(queue.status_code, 200)
                self.assertNotIn("600-word", json.dumps(queue.json()))
                # Withdrawal recovery through the production routes; the head
                # is a fast-completing prompt so usage evidence arrives early.
                head = await harness.typed_submit(
                    "l3-codex-head", "Write one short sentence about the sky."
                )
                self.assertIn(head.json()["acceptance"], {"immediate", "queued"})
                queued = await harness.typed_submit("l3-codex-recovery", "the exact queued body")
                self.assertIn(queued.json()["acceptance"], {"immediate", "queued"})
                queue = await harness.client.get(
                    f"/api/terminal/{session}/operation-queue", params=harness.params()
                )
                row = next(
                    item
                    for item in queue.json()["items"]
                    if item["phase"] == "queued" and item.get("cockpit")
                )
                withdrawn = await harness.client.post(
                    f"/api/terminal/{session}/operation-queue/withdraw",
                    params=harness.params(),
                    json={
                        "operationRef": row["operationRef"],
                        "withdrawalRef": row["cockpit"]["withdrawalRef"],
                        "withdrawRequestId": "l3-wd-1",
                    },
                )
                self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
                self.assertEqual(withdrawn.json()["recovery"]["text"], "the exact queued body")
                pending = await harness.client.get(
                    f"/api/terminal/{session}/operation-queue/pending-withdrawal-recoveries",
                    params=harness.params(),
                )
                self.assertEqual(len(pending.json()["items"]), 1)
                recovery_ref = pending.json()["items"][0]["recoveryRef"]
                fetched = await harness.client.post(
                    f"/api/terminal/{session}/operation-queue/withdraw-recovery",
                    params=harness.params(),
                    json={"recoveryRef": recovery_ref},
                )
                self.assertEqual(fetched.json()["text"], "the exact queued body")
                # Typed attachment submit through the production routes.
                staged = await harness.client.post(
                    f"/api/terminal/{session}/conversation/attachments",
                    params=harness.params(),
                    data={"requestId": "l3-codex-asset", "metadata": json.dumps([{"kind": "image"}])},
                    files=[("assets", ("dot.png", TINY_PNG, "image/png"))],
                )
                self.assertEqual(staged.status_code, 200, staged.text)
                (receipt,) = staged.json()["receipts"]
                asset_submit = await harness.client.post(
                    f"/api/terminal/{session}/conversation/submit",
                    json={
                        "expectedBridgeEpoch": harness.epoch,
                        "requestId": "l3-codex-asset",
                        "disposition": "next",
                        "content": [
                            {"type": "text", "text": "Describe this image in one short sentence."},
                            {
                                "type": "asset-ref",
                                "assetId": receipt["assetId"],
                                "kind": receipt["kind"],
                                "name": receipt["name"],
                                "mimeType": receipt["mimeType"],
                                "alt": receipt["alt"],
                                "altProvenance": receipt["altProvenance"],
                                "sha256": receipt["sha256"],
                            },
                        ],
                        "draftRevision": 1,
                    },
                )
                self.assertEqual(asset_submit.status_code, 200, asset_submit.text)
                self.assertIn(asset_submit.json()["acceptance"], {"immediate", "queued"})
                # Telemetry: the live turn produced token usage with full evidence.
                await harness.wait_evidence(
                    lambda frame: isinstance(frame.raw.get("tokenUsage"), dict),
                    timeout=120.0,
                    description="codex token usage",
                )
                telemetry = await harness.client.get(
                    f"/api/terminal/{session}/conversation/telemetry", params=harness.params()
                )
                self.assertEqual(telemetry.status_code, 200)
                usage = telemetry.json().get("usage")
                self.assertIsNotNone(usage)
                self.assertEqual(usage["unit"], "tokens")
                self.assertEqual(usage["precision"], "exact")
                self.assertEqual(usage["runtimeVersion"], CODEX_PINNED)
                self.assertEqual(usage["fixtureId"], "codex-0.144.5-installed-20260718")
            finally:
                await harness.stop()

    async def test_settled_live_turn_projects_once_on_the_conversation_page(self) -> None:
        """260718-CHATS-L5 F1 (L4 verdict blocker): a settled live codex turn projects EXACTLY once.

        On a hosted (persisted) codex thread the live notification channel and ``thread/read`` use
        DISJOINT id namespaces for the same settled turn (live ``msg_*``/UUID item ids vs positional
        ``item-N``), so the store's id-keyed dedupe cannot converge them and the post-turn native
        re-walk minted a duplicate ``native-history`` twin of every settled turn — developer-visible
        duplicate rows on the flagship ``+ Chat`` topology. This drives a persisted thread through the
        real production wire: open the page (clean hydration), settle a turn, re-read the page, and
        assert the turn's user message projects once — not twice. An isolated ``CODEX_HOME`` keeps the
        persisted thread contained to the temp root.
        """
        marker = "ar-f1-marker-7c3e91"
        with tempfile.TemporaryDirectory(prefix="ar-l5-codex-f1-") as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            identity = _identity("codex-l5-f1")
            adapter = CodexAppServerAdapter(CodexAppServerSettings(ephemeral=False))
            harness = _LiveHarness(root, identity, adapter, "codex")
            env = dict(os.environ)
            env["CODEX_HOME"] = str(codex_home)
            await harness.start(
                LaunchSpec(
                    identity=identity,
                    harness_id="codex",
                    cwd=root,
                    argv=(self.executable, "app-server"),
                    env=env,
                )
            )
            session = identity.ar_session_id

            def _carries(item: dict, needle: str) -> bool:
                return item.get("role") == "user" and needle in json.dumps(item)

            async def _user_twins(needle: str) -> list[dict]:
                # Re-read a few times: the projector's background poll consumes the live frames and
                # each page() re-walks thread/read — the exact surface F1's twin would appear on.
                items: list[dict] = []
                for _ in range(8):
                    await asyncio.sleep(1.0)
                    page = await harness.client.get(
                        f"/api/terminal/{session}/conversation", params=harness.params()
                    )
                    self.assertEqual(page.status_code, 200, page.text)
                    items = page.json().get("items", [])
                    if any(_carries(item, needle) for item in items):
                        break
                return [item for item in items if _carries(item, needle)]

            try:
                # Open the page BEFORE chatting: the projector hydrates clean (empty thread/read),
                # exactly as a developer opening a fresh + Chat.
                opened = await harness.client.get(
                    f"/api/terminal/{session}/conversation", params=harness.params()
                )
                self.assertEqual(opened.status_code, 200, opened.text)

                submit = await harness.typed_submit(
                    "l5-f1-turn-1", f"Say hello briefly. Reference token: {marker}."
                )
                self.assertEqual(submit.status_code, 200, submit.text)
                await harness.wait_evidence(
                    lambda frame: frame.kind == "completed",
                    timeout=180.0,
                    description="codex turn-1 settlement",
                )
                first = await _user_twins(marker)
                self.assertEqual(
                    len(first), 1, f"turn 1 user message must project once, got {len(first)}: {first}"
                )

                # 2/2: a second settled turn must also project its user message exactly once, and the
                # first turn must not have grown a twin on the intervening refreshes.
                marker2 = "ar-f1-marker-b40d5f"
                submit2 = await harness.typed_submit(
                    "l5-f1-turn-2", f"Say goodbye briefly. Reference token: {marker2}."
                )
                self.assertEqual(submit2.status_code, 200, submit2.text)
                await harness.wait_evidence(
                    lambda frame: frame.kind == "completed",
                    timeout=180.0,
                    description="codex turn-2 settlement",
                )
                second = await _user_twins(marker2)
                self.assertEqual(
                    len(second), 1, f"turn 2 user message must project once, got {len(second)}: {second}"
                )
                page = await harness.client.get(
                    f"/api/terminal/{session}/conversation", params=harness.params()
                )
                still_first = [item for item in page.json().get("items", []) if _carries(item, marker)]
                self.assertEqual(
                    len(still_first), 1, "the first settled turn must stay single across later refreshes"
                )
            finally:
                await harness.stop()


@unittest.skipUnless(
    os.environ.get(LIVE_OPT_IN) == "1",
    f"set {LIVE_OPT_IN}=1 to exercise installed runtimes through the L3 control routes",
)
class PiInstalledControlApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        executable = shutil.which("pi")
        if executable is None:
            self.skipTest("installed pi executable is unavailable")
        version = _version_of(executable)
        if version != PI_PINNED:
            self.skipTest(f"installed pi {version} does not match pinned {PI_PINNED}")
        self.executable = executable

    async def test_live_abort_guarded_interrupt_and_stale_identity_refusal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ar-l3-pi-") as tmp:
            root = Path(tmp)
            identity = _identity("pi-l3")
            adapter = PiRpcAdapter()
            harness = _LiveHarness(root, identity, adapter, "pi")
            await harness.start(
                LaunchSpec(
                    identity=identity,
                    harness_id="pi",
                    cwd=root,
                    argv=(self.executable,),
                    env=dict(os.environ),
                )
            )
            session = identity.ar_session_id
            try:
                submit = await harness.typed_submit("l3-pi-long", LONG_PROMPT)
                self.assertEqual(submit.status_code, 200, submit.text)
                self.assertIn(submit.json()["acceptance"], {"immediate", "queued"})
                interrupt = await harness.client.post(
                    f"/api/terminal/{session}/conversation/interrupt",
                    params=harness.params(),
                    json={"turnId": "l3-pi-long", "requestId": "l3-int-pi-1"},
                )
                self.assertEqual(interrupt.status_code, 202, interrupt.text)
                self.assertEqual(interrupt.json()["acknowledgement"], "accepted")
                await harness.wait_evidence(
                    lambda frame: True,
                    timeout=60.0,
                    description="pi activity after abort",
                )
                deadline = asyncio.get_running_loop().time() + 120.0
                settled = None
                while asyncio.get_running_loop().time() <= deadline:
                    status = await harness.client.post(
                        f"/api/terminal/{session}/conversation/interrupt-status",
                        params=harness.params(),
                        json={"turnId": "l3-pi-long", "requestId": "l3-int-pi-1"},
                    )
                    if status.json()["settlement"] != "pending":
                        settled = status.json()
                        break
                    await asyncio.sleep(1.0)
                assert settled is not None, "pi abort never settled through the status route"
                self.assertEqual(settled["settlement"], "interrupted")
                # Stale expected identity after settlement: typed refusal, zero writes.
                stale = await harness.client.post(
                    f"/api/terminal/{session}/conversation/interrupt",
                    params=harness.params(),
                    json={"turnId": "l3-pi-long", "requestId": "l3-int-pi-2"},
                )
                self.assertEqual(stale.status_code, 422, stale.text)
                self.assertEqual(stale.json()["acknowledgement"], "rejected")
            finally:
                await harness.stop()


class ClaudeInstalledHonestyTests(unittest.TestCase):
    def test_claude_control_gate_stays_unverified_at_version_mismatch(self) -> None:
        executable = shutil.which("claude")
        if executable is None:
            self.skipTest("installed claude executable is unavailable")
        version = _version_of(executable).split(" ", 1)[0]
        fixture = json.loads(
            (
                Path(__file__).parent / "fixtures" / "conversation_runtime" / "claude-2.1.211.json"
            ).read_text()
        )
        rows = [
            row
            for row in fixture["observations"]
            if row["operation"].startswith("control-plane/")
        ]
        if version != "2.1.211":
            self.assertTrue(rows)
            self.assertTrue(all(row["result"] == "not-exercised" for row in rows))
        else:
            self.skipTest("locked claude install present; capture belongs to a locked-gate run")


if __name__ == "__main__":
    unittest.main()
