"""Production-route tests for the conversation control API (260718-CHATS-L3, R7).

Every test drives the REAL composition on one loop: bridge + IPC server on a
real user-private socket, a real catalog row, the L0 register_conversation_routes
composition, and HTTP over a real uvicorn wire. The only double is the
structural fake adapter at the harness edge (interrupt- and asset-capable).
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest import mock

import httpx
import uvicorn
from _control_plane import OPERATOR, TINY_PNG, FakeControlAdapter, make_harness
from agents_remember.serving.harness_control_client import submit_control_prompt
from agents_remember.serving.harness_control_models import AR_EVIDENCE_KEY

SESSION = "ar-api-ctl"


class ControlApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.epoch = self.harness.epoch
        self._auth_patcher = mock.patch(
            "agents_remember.serving.conversation.control.api.resolve_conversation_authorization",
            lambda request: OPERATOR,
        )
        self._auth_patcher.start()
        self._uvicorn = uvicorn.Server(
            uvicorn.Config(
                self.harness.app, host="127.0.0.1", port=0, log_level="warning", access_log=False
            )
        )
        self._uvicorn_task = asyncio.create_task(self._uvicorn.serve())
        while not self._uvicorn.started:
            await asyncio.sleep(0.05)
        port = self._uvicorn.servers[0].sockets[0].getsockname()[1]
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self._uvicorn.should_exit = True
        await self._uvicorn_task
        self._auth_patcher.stop()
        await self.harness.stop()

    def _params(self, epoch: str | None = None) -> dict[str, str]:
        return {"expectedBridgeEpoch": epoch or self.epoch}

    async def _typed_submit(self, request_id: str, text: str) -> httpx.Response:
        return await self.client.post(
            f"/api/terminal/{SESSION}/conversation/submit",
            json={
                "expectedBridgeEpoch": self.epoch,
                "requestId": request_id,
                "disposition": "next",
                "content": [{"type": "text", "text": text}],
                "draftRevision": 1,
            },
        )

    async def _queue(self) -> dict:
        response = await self.client.get(
            f"/api/terminal/{SESSION}/operation-queue", params=self._params()
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def _withdraw_row(self, row: dict, withdraw_request_id: str) -> httpx.Response:
        return await self.client.post(
            f"/api/terminal/{SESSION}/operation-queue/withdraw",
            params=self._params(),
            json={
                "operationRef": row["operationRef"],
                "withdrawalRef": row["cockpit"]["withdrawalRef"],
                "withdrawRequestId": withdraw_request_id,
            },
        )

    # -- interrupt over the wire ----------------------------------------------

    async def test_interrupt_ack_settle_replay_and_single_write(self) -> None:
        response = await self._typed_submit("i-body", "generate")
        self.assertEqual(response.status_code, 200)
        self.adapter.set_activity("running")
        interrupt = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/interrupt",
            params=self._params(),
            json={"turnId": "turn-i-body", "requestId": "int-1"},
        )
        self.assertEqual(interrupt.status_code, 202, interrupt.text)
        operation = interrupt.json()
        self.assertEqual(operation["acknowledgement"], "accepted")
        self.assertEqual(operation["settlement"], "pending")
        self.assertEqual(operation["requestFingerprint"].startswith("sha256:"), True)
        replay = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/interrupt",
            params=self._params(),
            json={"turnId": "turn-i-body", "requestId": "int-1"},
        )
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(replay.json()["revision"], operation["revision"])
        self.adapter.settle_turn("interrupted")
        status = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/interrupt-status",
            params=self._params(),
            json={"turnId": "turn-i-body", "requestId": "int-1"},
        )
        self.assertEqual(status.status_code, 200, status.text)
        settled = status.json()
        self.assertEqual(settled["settlement"], "interrupted")
        self.assertGreater(settled["revision"], operation["revision"])
        self.assertTrue(settled["settledAt"])
        self.assertEqual(len(self.adapter.interrupt_calls), 1)

    async def test_interrupt_conflicts_and_epoch_failures_are_typed(self) -> None:
        await self._typed_submit("i-body", "generate")
        self.adapter.set_activity("running")
        await self.client.post(
            f"/api/terminal/{SESSION}/conversation/interrupt",
            params=self._params(),
            json={"turnId": "turn-i-body", "requestId": "int-2"},
        )
        conflict = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/interrupt",
            params=self._params(),
            json={"turnId": "turn-other", "requestId": "int-2"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["status"], "request-conflict")
        epoch = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/interrupt",
            params=self._params(epoch="wrong-epoch"),
            json={"turnId": "turn-i-body", "requestId": "int-3"},
        )
        self.assertEqual(epoch.status_code, 409)
        self.assertEqual(epoch.json()["status"], "bridge-epoch-mismatch")
        self.assertEqual(epoch.json()["expectedBridgeEpoch"], "wrong-epoch")
        missing = await self.client.post(
            "/api/terminal/ar-missing/conversation/interrupt",
            params=self._params(),
            json={"turnId": "turn-1", "requestId": "int-4"},
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["status"], "unknown-session")

    async def test_remote_peer_fails_closed_typed_403(self) -> None:
        self._auth_patcher.stop()
        response = await self.client.get(
            f"/api/terminal/{SESSION}/operation-queue",
            params=self._params(),
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "authorization-failed")
        self._auth_patcher.start()

    # -- queue / withdraw / recovery over the wire -----------------------------

    async def test_queue_truth_privacy_and_withdrawal_flow(self) -> None:
        self.adapter.set_activity("running")
        await self._typed_submit("q-body", "the wire cockpit draft")
        await asyncio.to_thread(
            submit_control_prompt,
            self.harness.control_entry,
            "terminal wire body",
            source="terminal",
            request_id="q-term",
        )
        queue = await self._queue()
        self.assertEqual(len(queue["items"]), 2)
        by_source = {row["source"]: row for row in queue["items"]}
        self.assertNotIn("terminal wire body", json.dumps(queue))
        cockpit = by_source["cockpit"]
        self.assertEqual(cockpit["cockpit"]["redactedPreview"], "the wire cockpit draft")
        withdrawn = await self._withdraw_row(cockpit, "wd-wire-1")
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        body = withdrawn.json()
        self.assertEqual(body["outcome"], "withdrawn")
        self.assertEqual(body["recovery"]["text"], "the wire cockpit draft")
        self.assertEqual(body["recovery"]["submittedDraftRevision"], 1)
        pending = await self.client.get(
            f"/api/terminal/{SESSION}/operation-queue/pending-withdrawal-recoveries",
            params=self._params(),
        )
        self.assertEqual(pending.status_code, 200)
        items = pending.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertNotIn("the wire cockpit draft", json.dumps(pending.json()))
        recovery_ref = items[0]["recoveryRef"]
        fetched = await self.client.post(
            f"/api/terminal/{SESSION}/operation-queue/withdraw-recovery",
            params=self._params(),
            json={"recoveryRef": recovery_ref},
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["text"], "the wire cockpit draft")
        acked = await self.client.post(
            f"/api/terminal/{SESSION}/operation-queue/withdraw-recovery-ack",
            params=self._params(),
            json={"recoveryRef": recovery_ref, "disposition": "replace-current-draft"},
        )
        self.assertEqual(acked.status_code, 200)
        self.assertEqual(acked.json()["recoveryState"], "acknowledged")
        refetched = await self.client.post(
            f"/api/terminal/{SESSION}/operation-queue/withdraw-recovery",
            params=self._params(),
            json={"recoveryRef": recovery_ref},
        )
        self.assertEqual(refetched.status_code, 404)
        self.assertEqual(refetched.json()["status"], "not-found")
        forged = await self.client.post(
            f"/api/terminal/{SESSION}/operation-queue/withdraw",
            params=self._params(),
            json={
                "operationRef": cockpit["operationRef"],
                "withdrawalRef": cockpit["cockpit"]["withdrawalRef"][:-4] + "aaaa",
                "withdrawRequestId": "wd-wire-2",
            },
        )
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(forged.json()["status"], "ref-invalid")

    # -- attachments over the wire ---------------------------------------------

    async def test_attachment_stage_submit_status_reconcile(self) -> None:
        staged = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/attachments",
            params=self._params(),
            data={"requestId": "att-wire-1", "metadata": json.dumps([{"kind": "image"}])},
            files=[("assets", ("dot.png", TINY_PNG, "image/png"))],
        )
        self.assertEqual(staged.status_code, 200, staged.text)
        payload = staged.json()
        (receipt,) = payload["receipts"]
        self.assertEqual(receipt["alt"], "dot.png, image/png")
        self.assertEqual(receipt["altProvenance"], "filename-mime-fallback")
        self.assertEqual(payload["operation"]["phase"], "staged")
        submit = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/submit",
            json={
                "expectedBridgeEpoch": self.epoch,
                "requestId": "att-wire-1",
                "disposition": "next",
                "content": [
                    {"type": "text", "text": "describe"},
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
        self.assertEqual(submit.status_code, 200, submit.text)
        answer = submit.json()
        self.assertEqual(answer["acceptance"], "immediate")
        self.assertEqual(answer["attachment"]["phase"], "dispatching")
        (request,) = self.adapter.submit_requests
        self.assertEqual(len(request.assets), 1)
        status = await self.client.get(
            f"/api/terminal/{SESSION}/conversation/attachments/att-wire-1/status",
            params=self._params(),
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["phase"], "dispatching")
        reconcile = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/attachments/att-wire-1/reconcile",
            params=self._params(),
        )
        self.assertEqual(reconcile.status_code, 200)
        missing = await self.client.get(
            f"/api/terminal/{SESSION}/conversation/attachments/att-missing/status",
            params=self._params(),
        )
        self.assertEqual(missing.status_code, 404)

    async def test_attachment_limits_are_typed_over_the_wire(self) -> None:
        refused = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/attachments",
            params=self._params(),
            data={"requestId": "att-wire-2", "metadata": json.dumps([{"kind": "image"}])},
            files=[("assets", ("note.txt", b"hello", "text/plain"))],
        )
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(refused.json()["status"], "rejected")
        unsupported_kind = await self.client.post(
            f"/api/terminal/{SESSION}/conversation/attachments",
            params=self._params(),
            data={"requestId": "att-wire-3", "metadata": json.dumps([{"kind": "file"}])},
            files=[("assets", ("doc.pdf", b"%PDF", "application/pdf"))],
        )
        self.assertEqual(unsupported_kind.status_code, 422)
        self.assertEqual(unsupported_kind.json()["status"], "unsupported")

    # -- policy / telemetry over the wire ---------------------------------------

    async def test_policy_is_read_only_with_no_mutation_surface(self) -> None:
        projection = await self.client.get(
            f"/api/terminal/{SESSION}/conversation/policy", params=self._params()
        )
        self.assertEqual(projection.status_code, 200)
        body = projection.json()
        self.assertEqual(body["repoPolicy"]["state"], "supported")
        self.assertEqual(body["harnessMode"]["state"], "unverified")
        for method in ("patch", "put", "delete"):
            mutated = await getattr(self.client, method)(
                f"/api/terminal/{SESSION}/conversation/policy", params=self._params()
            )
            self.assertEqual(mutated.status_code, 405)

    async def test_telemetry_usage_and_absent_metrics(self) -> None:
        self.adapter.emit(
            "codex-notification",
            {
                "codexMethod": "thread/tokenUsage/updated",
                AR_EVIDENCE_KEY: {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "tokenUsage": {
                        "total": {"inputTokens": 61, "outputTokens": 41, "cachedInputTokens": 11},
                        "last": {"inputTokens": 9, "outputTokens": 4},
                    },
                },
            },
        )
        projection = await self.client.get(
            f"/api/terminal/{SESSION}/conversation/telemetry", params=self._params()
        )
        self.assertEqual(projection.status_code, 200)
        body = projection.json()
        self.assertEqual(
            body["usage"]["value"], {"inputTokens": 61, "outputTokens": 41, "cachedTokens": 11}
        )
        self.assertEqual(body["usage"]["unit"], "tokens")
        self.assertEqual(body["usage"]["precision"], "exact")
        self.assertNotIn("cost", body)
        self.assertNotIn("rateLimits", body)

    # -- production-honesty scans ------------------------------------------------

    def test_no_paste_pty_or_native_queue_substitution_in_control_modules(self) -> None:
        root = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents_remember"
            / "serving"
            / "conversation"
            / "control"
        )
        forbidden = (
            "terminal_paste",
            "PtySurface",
            "tmux_pane",
            "capture_pane",
            "harness_terminal_surface",
            "queue_update",
            "turn/steer",
            "followUp",
            "follow_up_submit",
        )
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{path.name} references {token}")


if __name__ == "__main__":
    unittest.main()
