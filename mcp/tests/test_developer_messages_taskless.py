"""Recipient-only tray sends: unified taskless filing, non-blocking repository context, and legacy read-back."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.cli.developer_delivery import DEVELOPER_SENDER
from agents_remember.cli.developer_message_files import (
    append_sent,
    message_stem,
    messages_root,
    read_developer_message,
    read_sent,
    target_folder,
)
from agents_remember.cli.developer_messages import register_developer_chat_routes
from agents_remember.cli.role_message_files import (
    MessageContent,
    render_message_file,
    sha256_file,
    store_message_file,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_paseo_launch import REPO
from test_paseo_role_tools import RoleToolsTestCase

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32))


class TasklessDeveloperSendTests(RoleToolsTestCase):
    def _client(self, config: McpRuntimeConfig) -> TestClient:
        app = FastAPI()
        register_developer_chat_routes(app, config)
        return TestClient(app)

    def _post(self, client: TestClient, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        answer = client.post(f"/api/role-chats/{agent_id}/message", json=payload)
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def _sends(self) -> list[dict[str, Any]]:
        return [payload for command, payload in self.runtime.calls if command == "agent-send"]

    def test_target_folder_routes_taskful_and_taskless_recipients(self) -> None:
        root = messages_root(self.config)
        agent_id = "b1d2c3e4-0000-4000-8000-000000000003"
        self.assertEqual(
            target_folder(self.config, REPO, "01_LEAF", agent_id), root / REPO / "01_LEAF"
        )
        for repository, task_id in ((REPO, None), (None, None), (None, "01_LEAF")):
            with self.subTest(repository=repository, task_id=task_id):
                self.assertEqual(
                    target_folder(self.config, repository, task_id, agent_id),
                    root / "taskless" / agent_id,
                )

    def test_taskless_send_without_repository_succeeds_across_server_repo_counts(self) -> None:
        _request, architect = self.started("architect", status="idle")
        expected_folder = messages_root(self.config) / "taskless" / architect
        configs = (
            ("single-repo", self.config),
            (
                "multi-repo",
                replace(
                    self.config,
                    repositories={
                        **self.config.repositories,
                        "other": RepositoryScope(repo_id="other", path=self.root / "other"),
                    },
                ),
            ),
            ("zero-repo", replace(self.config, repositories={})),
        )
        pick = {
            "id": "pick-image",
            "kind": "image",
            "title": "Screenshot",
            "note": "",
            "image": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()},
        }
        for label, config in configs:
            for extra in ({}, {"repository": None}):
                with self.subTest(server=label, extra=extra):
                    self.runtime.agents[architect].update(status="idle")
                    payload = {
                        "messageId": str(uuid.uuid4()),
                        "text": f"from {label}",
                        "picks": [pick],
                        **extra,
                    }
                    answer = self._post(self._client(config), architect, payload)
                    self.assertEqual(answer["state"], "delivered")
                    filed = Path(answer["messageFile"])
                    self.assertEqual(filed.parent, expected_folder)
                    blob = expected_folder / f"{hashlib.sha256(PNG).hexdigest()}.png"
                    self.assertEqual(blob.read_bytes(), PNG)
                    (read_back,) = read_developer_message(config, answer["messageId"])
                    self.assertEqual((read_back.path, read_back.delivered), (filed, True))
        self.assertEqual(len(self._sends()), 6)

    def test_unknown_request_repository_never_blocks_taskful_or_taskless_sends(self) -> None:
        _request, architect = self.started("architect", status="idle")
        _request, worker = self.started("worker", status="idle")
        root = messages_root(self.config)
        stale_repo = "stale-unconfigured-repo"
        cases = (
            ("taskful-default-server", self.config, worker, root / REPO / "01_LEAF"),
            ("taskless-default-server", self.config, architect, root / "taskless" / architect),
            (
                "taskless-zero-repo-server",
                replace(self.config, repositories={}),
                architect,
                root / "taskless" / architect,
            ),
        )
        for label, config, target, expected_folder in cases:
            with self.subTest(case=label):
                self.runtime.agents[target].update(status="idle")
                payload = {
                    "messageId": str(uuid.uuid4()),
                    "repository": stale_repo,
                    "text": f"context check {label}",
                    "picks": [],
                }
                answer = self._post(self._client(config), target, payload)
                self.assertEqual(answer["state"], "delivered")
                self.assertEqual(Path(answer["messageFile"]).parent, expected_folder)
        self.assertFalse((root / stale_repo).exists())

    def test_legacy_repo_scoped_taskless_files_read_back_dedupe_and_settle(self) -> None:
        request, architect = self.started("architect", status="idle")
        legacy_folder = messages_root(self.config) / REPO / "taskless" / architect
        when = datetime.now(UTC)

        legacy_id = str(uuid.uuid4())
        legacy_path = store_message_file(
            legacy_folder,
            message_stem(when, "architect", architect, legacy_id),
            render_message_file(
                MessageContent(
                    when=when,
                    sender_line=DEVELOPER_SENDER,
                    sender_task="none",
                    recipient_role="architect",
                    recipient_id=architect,
                    recipient_task="Projects",
                    message_id=legacy_id,
                    text="earlier taskless message",
                )
            ),
        )
        legacy_sha = sha256_file(legacy_path)

        (read_back,) = read_developer_message(self.config, legacy_id)
        self.assertEqual(
            (read_back.path, read_back.sha256, read_back.recipient_agent_id, read_back.delivered),
            (legacy_path, legacy_sha, architect, True),
        )

        reused = self._post(
            self._client(self.config),
            architect,
            {"messageId": legacy_id, "text": "duplicate send", "picks": []},
        )
        self.assertEqual((reused["state"], reused["reason"]), ("refused", "message-id-reused"))
        self.assertEqual(self._sends(), [])

        held_id = str(uuid.uuid4())
        held_path = store_message_file(
            legacy_folder,
            message_stem(when, "architect", architect, held_id),
            render_message_file(
                MessageContent(
                    when=when,
                    sender_line=DEVELOPER_SENDER,
                    sender_task="none",
                    recipient_role="architect",
                    recipient_id=architect,
                    recipient_task="Projects",
                    message_id=held_id,
                    text="earlier held taskless message",
                )
            ),
        )
        receipt_path = self.receipt_path(request)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["heldMessageLines"] = [
            {"messageId": "held-legacy-1", "how": "not-delivered-archived-or-gone"}
        ]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        append_sent(
            self.config,
            architect,
            {
                "time": when.isoformat(),
                "targetAgentId": architect,
                "pickIds": [],
                "messageId": held_id,
                "messageFile": held_path.as_posix(),
                "sha256": sha256_file(held_path),
                "state": "held",
                "heldMessageId": "held-legacy-1",
                "receipt": receipt_path.as_posix(),
                "requestId": receipt["requestId"],
            },
        )

        (settled,) = read_developer_message(self.config, held_id)
        self.assertFalse(settled.delivered)
        self.assertTrue(settled.path.name.endswith(".undelivered.md"))
        self.assertEqual(settled.path.parent, legacy_folder)
        self.assertEqual(read_sent(self.config, architect, 5)["entries"][0]["state"], "undelivered")
