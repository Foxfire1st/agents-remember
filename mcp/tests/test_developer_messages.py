"""MIK-R188 evidence 3: the developer's send from the tray and the host-wide seat list.

The route, the composition, the files, the held queue and the list run for real; a fake host
(the role tools' fixture) answers the bridge calls.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli import developer_delivery, developer_message_files
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.cli.developer_message_files import (
    append_sent,
    read_developer_message,
    read_sent,
)
from agents_remember.cli.developer_message_forms import (
    OPENING_CALLS,
    FormRoots,
    written_form,
)
from agents_remember.cli.developer_messages import (
    HELD_TEXT_LIMIT,
    DeveloperMessageRequest,
    register_developer_chat_routes,
)
from agents_remember.cli.developer_seats import seat_list
from agents_remember.cli.role_held_messages import (
    HeldMessage,
    end_waiting_as,
    hold_message,
    store_path_for,
    sweep_held_messages,
)
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA
from agents_remember.cli.role_message_files import MessageFileError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.serving.app import create_app
from agents_remember.serving.paseo.provider_limits import provider_limits_path
from agents_remember.serving.projector import ProjectionCadence
from fastapi import FastAPI
from fastapi.testclient import TestClient
from paseo_launch_test_support import SERVER_ID
from pydantic import TypeAdapter
from test_paseo_launch import LEAF_REF, REPO
from test_paseo_role_tools import ARCHIVED_AT, RoleToolsTestCase

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
INVARIANT_FILE = "knowledge/invariants/INV-1ZKP4R-turn-end.json"
FAMILY_FILE = "knowledge/families/FAM-S140B6-turn-ends.json"
# An invariant only the base memory tree holds: a before-only review block names it.
REMOVED_FILE = "knowledge/invariants/INV-0LD0R3-retired-rule.json"


def record_text(record_id: str, revision: int) -> str:
    return json.dumps({"id": record_id, "revision": revision}) + "\n"


def revision_id(record_id: str, revision: int) -> str:
    """The revision id ``knowledge_read`` hands out for one record revision."""

    return text_uuid("revision", f"{record_id}@{revision}")


def commit_tree(root: Path, files: dict[str, str | None], message: str) -> str:
    """Write (or, for None, delete) the files, commit them, and name the commit's tree."""

    for name, text in files.items():
        path = root / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    git = ["git", "-C", root.as_posix(), "-c", "user.name=t", "-c", "user.email=t@t"]
    if not (root / ".git").exists():
        subprocess.run([*git, "init", "-q"], check=True)
    for step in (["add", "-A"], ["commit", "-q", "-m", message]):
        subprocess.run([*git, *step], check=True)
    return subprocess.run(
        [*git, "rev-parse", "HEAD^{tree}"], check=True, capture_output=True, text=True
    ).stdout.strip()


def references(trees: dict[str, str]) -> dict[str, dict[str, Any]]:
    """One reference of each kind on comparison #8, whose memory trees are ``trees``'s."""

    comparison = {
        "repository": REPO,
        "task": "master",
        "leaf": "01_LEAF",
        "number": 8,
        "pinRef": "refs/ar/review/master/01_LEAF/8",
        "trees": trees,
    }
    return {
        "invariant": {
            "kind": "invariant",
            "repository": REPO,
            "id": "INV-1ZKP4R",
            "revisionId": revision_id("INV-1ZKP4R", 2),
            "memoryTree": trees["memoryCandidate"],
        },
        "family": {
            "kind": "family",
            "repository": REPO,
            "id": "FAM-S140B6",
            "memoryTree": trees["memoryCandidate"],
        },
        "review-block": {
            "kind": "review-block",
            "comparison": comparison,
            "block": {
                "kind": "entry",
                "entry": "PRF-09JM5B",
                "side": "after",
                "path": "src/turn_ended.ts",
                "range": {"start": 99, "end": 144},
                "trees": trees,
            },
            "invariant": "INV-1ZKP4R",
            "invariantRevisionId": revision_id("INV-1ZKP4R", 2),
        },
        "code-without-invariant": {
            "kind": "code-without-invariant",
            "comparison": comparison,
            "hunk": {
                "kind": "hunk",
                "path": "src/lane.py",
                "before": {"start": 3, "count": 2},
                "after": {"start": 3, "count": 4},
                "trees": {"codeBase": trees["codeBase"], "codeCandidate": trees["codeCandidate"]},
            },
        },
        "task": {"kind": "task", "reference": LEAF_REF.model_dump(), "id": "01_LEAF"},
        "file-line": {
            "kind": "file-line",
            "repository": REPO,
            "scope": "mainline",
            "path": "src/app.py",
            "start": 4,
            "end": 9,
        },
        "change-set": {"kind": "change-set", "comparison": comparison},
    }


def parsed_reference(raw: dict[str, Any]) -> Any:
    """One wire reference as the route parses it."""

    pick = {"id": "p", "kind": "reference", "title": "t", "note": "", "reference": raw}
    body = {"messageId": str(uuid.uuid4()), "repository": REPO, "text": "", "picks": [pick]}
    return TypeAdapter(DeveloperMessageRequest).validate_python(body).picks[0].reference


def text_pick(text: str = "the copied words", **provenance: Any) -> dict[str, Any]:
    return {
        "id": "pick-text",
        "kind": "text",
        "title": "A passage",
        "note": "",
        "text": text,
        "provenance": {"source": "chat", **provenance},
    }


def image_pick() -> dict[str, Any]:
    return {
        "id": "pick-image",
        "kind": "image",
        "title": "Screenshot",
        "note": "",
        "image": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()},
    }


class DeveloperSendTests(RoleToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        app = FastAPI()
        register_developer_chat_routes(app, self.config)
        self.client = TestClient(app)
        (self.config.repositories[REPO].path / "src").mkdir(parents=True)
        memory = self.config.repositories[REPO].memory_root
        assert memory is not None
        # The memory checkout at two commits: the candidate revises INV-1ZKP4R and drops
        # INV-0LD0R3; the family is at revision 1 in both.
        base = commit_tree(
            memory,
            {
                INVARIANT_FILE: record_text("INV-1ZKP4R", 1),
                FAMILY_FILE: record_text("FAM-S140B6", 1),
                REMOVED_FILE: record_text("INV-0LD0R3", 3),
            },
            "base",
        )
        candidate = commit_tree(
            memory,
            {INVARIANT_FILE: record_text("INV-1ZKP4R", 2), REMOVED_FILE: None},
            "candidate",
        )
        self.trees = {
            "codeBase": "a" * 40,
            "codeCandidate": "b" * 40,
            "memoryBase": base,
            "memoryCandidate": candidate,
        }
        self.references = references(self.trees)
        self.comparison = self.references["change-set"]["comparison"]

    def reference_pick(self, kind: str, note: str = "") -> dict[str, Any]:
        return {
            "id": f"pick-{kind}",
            "kind": "reference",
            "title": f"{kind} title",
            "note": note,
            "reference": self.references[kind],
        }

    def send(
        self, agent_id: str, *picks: dict[str, Any], text: str = "Please look.", **body: Any
    ) -> dict[str, Any]:
        payload = {
            "messageId": str(uuid.uuid4()),
            "repository": REPO,
            "text": text,
            "picks": list(picks),
            **body,
        }
        answer = self.client.post(f"/api/role-chats/{agent_id}/message", json=payload)
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def sends(self) -> list[dict[str, Any]]:
        return [payload for command, payload in self.runtime.calls if command == "agent-send"]

    def assert_filed(self, answer: dict[str, Any], folder: Path, recipient: str) -> str:
        """The file lies in its folder, with rule 9's head, and its SHA-256 is the one named."""

        path = Path(answer["messageFile"])
        self.assertEqual(path.parent, folder)
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), answer["messageFileSha256"])
        head = dict(line.split(": ", 1) for line in data.decode().split("\n\n", 1)[0].splitlines())
        self.assertEqual(
            {key: head[key] for key in ("sender", "recipientAgentId", "messageId")},
            {
                "sender": "developer",
                "recipientAgentId": recipient,
                "messageId": answer["messageId"],
            },
        )
        self.assertRegex(head["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        (filed,) = read_developer_message(self.config, answer["messageId"])
        self.assertEqual(
            (filed.path, filed.sha256, filed.recipient_agent_id),
            (path, answer["messageFileSha256"], recipient),
        )
        return data.decode()

    def test_a_send_to_two_targets_composes_one_message_each_in_rule_8_order(self) -> None:
        _request, worker = self.started("worker", status="idle")
        _request, architect = self.started("architect", status="idle")
        picks = (
            self.reference_pick("review-block", note="explain"),
            {
                "id": "pick-text",
                "kind": "text",
                "title": "A passage",
                "note": "",
                "text": "the copied words",
                "provenance": {
                    "source": "chat",
                    "agentId": worker,
                    "messageId": "m-1",
                    "messageTime": "14:05",
                },
            },
            {
                "id": "pick-image",
                "kind": "image",
                "title": "Screenshot",
                "note": "is this a bug?",
                "image": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()},
            },
        )
        answers = {agent: self.send(agent, *picks) for agent in (worker, architect)}

        self.assertEqual(
            [answer["state"] for answer in answers.values()], ["delivered", "delivered"]
        )
        first, second = self.sends()
        messages = self.config.coordination_root / "notes" / "reports" / "developer-messages" / REPO
        for payload, agent, folder, opening in (
            (first, worker, messages / "01_LEAF", "From developer · 01_LEAF"),
            (second, architect, messages / "taskless" / architect, "From developer"),
        ):
            with self.subTest(target=opening):
                lines = payload["text"].split("\n")
                self.assertEqual(payload["agentId"], agent)
                self.assertEqual(lines[:2], [opening, "Please look."])
                order = [line for line in lines if re.match(r"^\[\d\] ", line)]
                self.assertEqual([line[:4] for line in order], ["[1] ", "[2] ", "[3] "])
                self.assertIn("review block · review-block title", order[0])
                self.assertIn(f"from the chat of worker (agent {worker}) · 14:05", order[1])
                self.assertTrue(lines[lines.index(order[0]) + 1].startswith("    knowledge_read "))
                self.assertIn("    note: explain", lines)
                self.assertIn("    note: is this a bug?", lines)
                answer = answers[agent]
                self.assertEqual(
                    lines[-1],
                    f"Message file: {answer['messageFile']} (SHA-256 {answer['messageFileSha256']})",
                )
                # The image and the text go natively; the text names each with its saved file.
                self.assertEqual(
                    payload["images"],
                    [{"data": base64.b64encode(PNG).decode(), "mimeType": "image/png"}],
                )
                (attachment,) = payload["attachments"]
                self.assertEqual(
                    (attachment["type"], attachment["mimeType"], attachment["text"]),
                    ("text", "text/plain", "the copied words"),
                )
                self.assertTrue(
                    attachment["title"].startswith("Pick 2 · A passage · from the chat of worker")
                )
                image_file = folder / f"{hashlib.sha256(PNG).hexdigest()}.png"
                self.assertEqual(image_file.read_bytes(), PNG)
                self.assertIn(
                    f"file {image_file.as_posix()} (SHA-256 {hashlib.sha256(PNG).hexdigest()})",
                    payload["text"],
                )
                filed = self.assert_filed(answer, folder, agent)
                self.assertIn("## message\nPlease look.\n\n[1] review block", filed)
                self.assertNotIn("Message file:", filed)
        with self.subTest("the sent-picks log lists each send"):
            for agent in (worker, architect):
                listed = self.client.get(f"/api/role-chats/{agent}/sent").json()["entries"]
                self.assertEqual(
                    [
                        (entry["pickIds"], entry["state"], entry["messageFile"], entry["sha256"])
                        for entry in listed
                    ],
                    [
                        (
                            ["pick-review-block", "pick-text", "pick-image"],
                            "delivered",
                            answers[agent]["messageFile"],
                            answers[agent]["messageFileSha256"],
                        )
                    ],
                )

    def test_a_busy_target_is_held_as_text_that_names_the_message_file(self) -> None:
        request, worker = self.started("worker", status="running")
        held = self.send(
            worker,
            self.reference_pick("task"),
            {
                "id": "pick-image",
                "kind": "image",
                "title": "Screenshot",
                "note": "",
                "image": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()},
            },
        )

        self.assertEqual((held["state"], held["reason"]), ("held", "recipient-in-turn"))
        self.assertIn("nothing has to be sent again", held["detail"])
        folder = self.receipt(request)["workspace"]["path"]
        (row,) = json.loads(store_path_for(folder, worker).read_text())["messages"]
        self.assertEqual(
            (row["senderAgentId"], row["senderLine"]), ("developer", "From developer · 01_LEAF")
        )
        self.assertTrue(row["text"].startswith("Please look.\n\n[1] task · task title\n    read "))
        self.assertTrue(
            row["text"].endswith(
                f"Message file: {held['messageFile']} (SHA-256 {held['messageFileSha256']})"
            )
        )
        self.assertTrue(Path(held["messageFile"]).is_file())
        with self.subTest("a later send waits behind it; nothing overtakes"):
            behind = self.send(worker, text="second")
            self.assertEqual((behind["state"], behind["reason"]), ("held", "held-behind-waiting"))
            self.assertEqual(
                len(json.loads(store_path_for(folder, worker).read_text())["messages"]), 2
            )
            self.assertEqual(self.received(worker), [])
        with self.subTest(
            "the turn ends: one delivery of both, in order, under the developer's line"
        ):
            self.runtime.agents[worker].update(status="idle")
            self.assertEqual(self.send(worker, text="third")["state"], "delivered")
            (combined,) = self.received(worker)
            self.assertLess(combined.index("Please look."), combined.index("second"))
            self.assertLess(combined.index("second"), combined.index("third"))
            self.assertEqual(combined.count("From developer · 01_LEAF"), 3)
        # The log reads how the held sends ended: the delivery of the batch delivered them too.
        states = [entry["state"] for entry in read_sent(self.config, worker, 10)["entries"]]
        self.assertEqual(states, ["delivered", "delivered", "delivered"])
        with self.subTest("a held entry keeps a bounded text; the file keeps the whole message"):
            self.runtime.agents[worker].update(status="running")
            for size in (100, HELD_TEXT_LIMIT * 3):
                long = self.send(worker, text="y" * size)
                row = json.loads(store_path_for(folder, worker).read_text())["messages"][-1]
                self.assertLess(len(row["text"]), HELD_TEXT_LIMIT + 400)
                self.assertTrue(row["text"].endswith(long["messageFileSha256"] + ")"))
                self.assertIn("y" * size, Path(long["messageFile"]).read_text())

    def test_an_archived_target_is_refused_by_name_and_the_other_target_is_served(self) -> None:
        _request, archived = self.started("reviewer", status="idle")
        _request, worker = self.started("worker", status="idle")
        self.runtime.agents[archived].update(status="closed", archivedAt=ARCHIVED_AT)

        refused = self.send(archived, self.reference_pick("invariant"))
        served = self.send(worker, self.reference_pick("invariant"))

        self.assertEqual((refused["state"], refused["reason"]), ("refused", "recipient-archived"))
        self.assertEqual(served["state"], "delivered")
        kept = Path(refused["messageFile"])
        self.assertTrue(kept.name.endswith(".undelivered.md"))
        self.assertIn("delivery: refused as recipient-archived", kept.read_text())
        (filed,) = read_developer_message(self.config, refused["messageId"])
        self.assertEqual((filed.path, filed.delivered), (kept, False))
        self.assertEqual(read_sent(self.config, archived, 5)["entries"][0]["state"], "refused")
        with self.subTest("an agent no launch record names is refused before anything is filed"):
            unknown = self.send(str(uuid.uuid4()), self.reference_pick("invariant"))
            self.assertEqual(
                (unknown["state"], unknown["reason"], unknown["messageFile"]),
                ("refused", "recipient-not-found", None),
            )
        with self.subTest("a closed session is resumed, then sent to; nothing is started"):
            self.runtime.agents[worker].update(status="closed")
            self.runtime.calls.clear()
            resumed = self.send(worker, text="after the close")
            self.assertEqual((resumed["state"], resumed.get("resumed")), ("delivered", True))
            self.assertIn("agent-resume", [command for command, _payload in self.runtime.calls])
            self.assertNotIn("agent-create", [command for command, _payload in self.runtime.calls])

    def test_each_reference_kind_has_a_written_form_that_opens_with_its_call(self) -> None:
        roots = FormRoots(self.config)
        code = self.config.repositories[REPO].path.as_posix()
        memory = str(self.config.repositories[REPO].memory_root)
        invariant = revision_id("INV-1ZKP4R", 2)
        expected_first = {
            "invariant": f"knowledge_read memoryRoot={memory} view=invariant recordId=INV-1ZKP4R invariantRevisionId={invariant}",
            "family": f"knowledge_read memoryRoot={memory} view=family recordId=FAM-S140B6 familyRevisionId={revision_id('FAM-S140B6', 1)}",
            "review-block": f"knowledge_read memoryRoot={memory} view=invariant recordId=INV-1ZKP4R invariantRevisionId={invariant}",
            "code-without-invariant": f"git -C {code} diff {'a' * 40} {'b' * 40} -- src/lane.py",
            "task": f"read {self.leaf.path.with_suffix('.md') if self.leaf.path.with_suffix('.md').is_file() else self.leaf.path}",
            "file-line": f"read {code}/src/app.py lines 4-9",
            "change-set": f"git -C {code} diff {'a' * 40} {'b' * 40}",
        }
        for kind, first in expected_first.items():
            with self.subTest(kind=kind):
                form = written_form(parsed_reference(self.references[kind]), roots)
                self.assertTrue(form[0].startswith(OPENING_CALLS[kind]))
                self.assertEqual(form[0], first)
                self.assertTrue(form[-1].startswith("names: "))
        candidate = self.trees["memoryCandidate"]
        self.assertEqual(
            written_form(parsed_reference(self.references["review-block"]), roots)[1:3],
            (
                f"git -C {memory} show {candidate}:{INVARIANT_FILE}",
                f"git -C {code} show {'b' * 40}:src/turn_ended.ts",
            ),
        )
        self.assertEqual(
            written_form(parsed_reference(self.references["invariant"]), roots)[1],
            f"git -C {memory} show {candidate}:{INVARIANT_FILE}",
        )
        with self.subTest("a reference whose root this server cannot name is refused by name"):
            _request, worker = self.started("worker", status="idle")
            other = {**self.references["invariant"], "repository": "elsewhere"}
            answer = self.send(worker, {**self.reference_pick("invariant"), "reference": other})
            self.assertEqual(
                (answer["state"], answer["reason"], answer["messageFile"]),
                ("refused", "reference-unresolvable", None),
            )
            self.assertEqual(self.sends(), [])

    def test_bounds_and_a_reused_message_id_are_refused_by_name_before_anything_is_filed(
        self,
    ) -> None:
        _request, worker = self.started("worker", status="idle")
        image = {"id": "big", "kind": "image", "title": "big", "note": ""}
        cases = {
            "too-many-picks": {"picks": [self.reference_pick("task")] * 21},
            "image-too-large": {
                "picks": [
                    {
                        **image,
                        "image": {
                            "mimeType": "image/png",
                            "data": base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode(),
                        },
                    }
                ]
            },
            "message-too-large": {"text": "x" * (20 * 1024 * 1024 + 1)},
            "empty-message": {"text": " "},
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                answer = self.send(worker, **body)
                self.assertEqual(
                    (answer["state"], answer["reason"], answer["messageFile"]),
                    ("refused", reason, None),
                )
        self.assertFalse(
            (self.config.coordination_root / "notes" / "reports" / "developer-messages").exists()
        )
        self.assertEqual(self.sends(), [])
        with self.subTest("one message id is one message"):
            body = {"messageId": str(uuid.uuid4()), "repository": REPO, "text": "once", "picks": []}
            self.assertEqual(
                self.client.post(f"/api/role-chats/{worker}/message", json=body).json()["state"],
                "delivered",
            )
            again = self.client.post(f"/api/role-chats/{worker}/message", json=body).json()
            self.assertEqual((again["state"], again["reason"]), ("refused", "message-id-reused"))
            self.assertEqual(len(self.sends()), 1)

    def test_references_from_a_comparison_name_the_leafs_memory_and_their_tree(self) -> None:
        """P-T2: an invariant only the leaf added gets its full form, from the leaf's memory."""

        memory = self.config.repositories[REPO].memory_root
        assert memory is not None
        # The leaf's memory worktree shares the repository memory's objects, as a leaf's does.
        leaf = self.root / "leaf-memory"
        subprocess.run(
            [
                "git",
                "-C",
                memory.as_posix(),
                "worktree",
                "add",
                "-q",
                "-b",
                "leaf",
                leaf.as_posix(),
            ],
            check=True,
        )
        record = "knowledge/invariants/INV-NEW001-added-by-the-leaf.json"
        tree = commit_tree(leaf, {record: record_text("INV-NEW001", 1)}, "leaf")
        comparison = {**self.comparison, "trees": {**self.trees, "memoryCandidate": tree}}
        live = {(REPO, "master", "01_LEAF"): leaf}
        roots = FormRoots(self.config, leaf_memory_root=lambda *key: live.get(key))
        revision = revision_id("INV-NEW001", 1)
        invariant = {
            "kind": "invariant",
            "repository": REPO,
            "id": "INV-NEW001",
            "revisionId": revision,
            "memoryTree": tree,
            "comparison": comparison,
        }
        opened = f"knowledge_read memoryRoot={leaf} view=invariant recordId=INV-NEW001"
        self.assertEqual(
            written_form(parsed_reference(invariant), roots)[:2],
            (f"{opened} invariantRevisionId={revision}", f"git -C {leaf} show {tree}:{record}"),
        )
        block = {
            **self.references["review-block"],
            "comparison": comparison,
            "invariant": "INV-NEW001",
            "invariantRevisionId": revision,
            "block": {**self.references["review-block"]["block"], "trees": comparison["trees"]},
        }
        self.assertEqual(
            written_form(parsed_reference(block), roots)[:2],
            (f"{opened} invariantRevisionId={revision}", f"git -C {leaf} show {tree}:{record}"),
        )
        with self.subTest("once the leaf's worktree is gone, the repository's memory is named"):
            live.clear()
            first = written_form(parsed_reference(invariant), roots)[0]
            self.assertTrue(first.startswith(f"knowledge_read memoryRoot={memory} "), first)
        with self.subTest("the route renders with the leaf resolver it is given"):
            app = FastAPI()
            register_developer_chat_routes(app, self.config, leaf_memory_root=lambda *_key: leaf)
            _request, worker = self.started("worker", status="idle")
            pick = {
                "id": "p",
                "kind": "reference",
                "title": "t",
                "note": "",
                "reference": invariant,
            }
            body = {"messageId": str(uuid.uuid4()), "repository": REPO, "text": "", "picks": [pick]}
            answer = TestClient(app).post(f"/api/role-chats/{worker}/message", json=body).json()
            self.assertEqual(answer["state"], "delivered")
            self.assertIn(f"    {opened} invariantRevisionId={revision}", self.sends()[-1]["text"])

    def test_a_send_without_a_repository_takes_the_servers_one_repository(self) -> None:
        """P-T3: a page that names no repository can still send to a seat without a task."""

        _request, architect = self.started("architect", status="idle")
        _request, worker = self.started("worker", status="idle")
        body = {"messageId": str(uuid.uuid4()), "text": "from the Chats page", "picks": []}
        answer = self.client.post(f"/api/role-chats/{architect}/message", json=body)
        self.assertEqual((answer.status_code, answer.json()["state"]), (200, "delivered"))
        folder = self.config.coordination_root / "notes" / "reports" / "developer-messages"
        self.assertEqual(
            Path(answer.json()["messageFile"]).parent, folder / REPO / "taskless" / architect
        )
        two = replace(
            self.config,
            repositories={
                **self.config.repositories,
                "other": RepositoryScope(repo_id="other", path=self.root / "other"),
            },
        )
        app = FastAPI()
        register_developer_chat_routes(app, two)
        client = TestClient(app)
        for target, expected in (
            (architect, ("refused", "unknown-repository")),
            (worker, ("delivered", None)),
        ):
            with self.subTest(target=target):
                body = {"messageId": str(uuid.uuid4()), "text": "which one?", "picks": []}
                answer = client.post(f"/api/role-chats/{target}/message", json=body).json()
                self.assertEqual((answer["state"], answer.get("reason")), expected)
        self.assertEqual(len(self.sends()), 2)

    def test_a_body_over_its_bound_is_refused_by_name_before_it_is_parsed(self) -> None:
        """P-T4: an oversized body is refused before it is read whole or parsed as JSON."""

        _request, worker = self.started("worker", status="idle")
        # Not JSON at all: only a bound read before any parse can name it message-too-large.
        oversized = b"{" + b"x" * 30_000_000
        chunks = [
            oversized[start : start + (1 << 20)] for start in range(0, len(oversized), 1 << 20)
        ]
        for label, content in (("a declared length", oversized), ("a chunked body", iter(chunks))):
            with self.subTest(label):
                answer = self.client.post(
                    f"/api/role-chats/{worker}/message",
                    content=content,
                    headers={"content-type": "application/json"},
                )
                self.assertEqual(answer.status_code, 200, answer.text[:200])
                self.assertEqual(
                    (answer.json()["state"], answer.json()["reason"], answer.json()["messageFile"]),
                    ("refused", "message-too-large", None),
                )
        self.assertEqual(self.sends(), [])

    def test_an_unsupported_image_type_and_a_malformed_body_are_refused_by_name(self) -> None:
        """P-T7: the delivery answer names the refusal; nothing answers a bare 422."""

        _request, worker = self.started("worker", status="idle")
        bitmap = {
            "id": "bmp",
            "kind": "image",
            "title": "shot",
            "note": "",
            "image": {"mimeType": "image/bmp", "data": base64.b64encode(b"BM").decode()},
        }
        answer = self.send(worker, bitmap)
        self.assertEqual(
            (answer["state"], answer["reason"], answer["messageFile"]),
            ("refused", "image-type-unsupported", None),
        )
        untitled = {
            "id": "t",
            "kind": "reference",
            "note": "",
            "reference": self.references["task"],
        }
        answer = self.send(worker, untitled)
        self.assertEqual((answer["state"], answer["reason"]), ("refused", "invalid-request"))
        self.assertIn("picks.0.title", answer["detail"])
        self.assertEqual(self.sends(), [])

    def test_record_references_name_the_revision_the_memory_side_they_name_holds(self) -> None:
        """Rule 2: an invariant, a family and a review block carry their revision id, read at the
        memory tree the reference names; a revision that tree does not hold is never sent."""

        roots = FormRoots(self.config)
        memory = self.config.repositories[REPO].memory_root
        base, candidate = self.trees["memoryBase"], self.trees["memoryCandidate"]
        family = {**self.references["family"], "comparison": self.comparison}
        standalone = {**self.references["invariant"]}
        del standalone["revisionId"]
        at_base = {**standalone, "memoryTree": base, "comparison": self.comparison}
        block = self.references["review-block"]
        before_only = {
            **{key: value for key, value in block.items() if key != "invariantRevisionId"},
            "block": {
                **block["block"],
                "entry": "RLZ-0LD0R3",
                "side": "before",
                "path": "src/old.py",
            },
            "invariant": "INV-0LD0R3",
        }
        cases = {
            "a family": (family, "familyRevisionId", revision_id("FAM-S140B6", 1)),
            "a standalone invariant": (
                standalone,
                "invariantRevisionId",
                revision_id("INV-1ZKP4R", 2),
            ),
            "an invariant at the base tree": (
                at_base,
                "invariantRevisionId",
                revision_id("INV-1ZKP4R", 1),
            ),
            "a before-only review block": (
                before_only,
                "invariantRevisionId",
                revision_id("INV-0LD0R3", 3),
            ),
        }
        for label, (raw, key, revision) in cases.items():
            with self.subTest(label):
                form = written_form(parsed_reference(raw), roots)
                self.assertTrue(form[0].endswith(f" {key}={revision}"), form[0])
                self.assertIn(f"revision {revision}", form[-1])
                self.assertNotIn("not given", form[-1])
        self.assertEqual(
            written_form(parsed_reference(before_only), roots)[1],
            f"git -C {memory} show {base}:{REMOVED_FILE}",
        )
        with self.subTest("a revision the named tree does not hold is refused, never fabricated"):
            _request, worker = self.started("worker", status="idle")
            stale = {**self.references["invariant"], "revisionId": revision_id("INV-1ZKP4R", 1)}
            absent = {**standalone, "id": "INV-0LD0R3"}
            answers = [
                self.send(worker, {**self.reference_pick("invariant"), "reference": reference})
                for reference in (stale, absent)
            ]
            self.assertEqual(
                [(one["state"], one["reason"], one["messageFile"]) for one in answers],
                [
                    ("refused", "reference-contradictory", None),
                    ("refused", "reference-unresolvable", None),
                ],
            )
            self.assertIn(candidate, answers[1]["detail"])
            self.assertEqual(self.sends(), [])

    def test_a_reference_whose_parts_name_different_objects_is_refused_before_it_is_filed(
        self,
    ) -> None:
        """Rule 2: a reference names one exact object. One whose task, leaf, number, pin or trees
        disagree is refused by name before anything is filed; a genuine one still opens."""

        _request, worker = self.started("worker", status="idle")
        block = self.references["review-block"]
        lane = self.references["code-without-invariant"]
        elsewhere = {**self.trees, "codeCandidate": "e" * 40}
        contradictory = {
            "a pin of another task and leaf": {
                **block,
                "comparison": {
                    **self.comparison,
                    "pinRef": "refs/ar/review/OTHER-TASK/OTHER-LEAF/8",
                },
            },
            "a pin of another number": {
                **block,
                "comparison": {**self.comparison, "pinRef": "refs/ar/review/master/01_LEAF/9"},
            },
            "block trees of another comparison": {
                **block,
                "block": {**block["block"], "trees": elsewhere},
            },
            "hunk trees of another comparison": {
                **lane,
                "hunk": {
                    **lane["hunk"],
                    "trees": {"codeBase": "a" * 40, "codeCandidate": "e" * 40},
                },
            },
            "an invariant at a memory tree its comparison does not name": {
                **self.references["invariant"],
                "memoryTree": "e" * 40,
                "comparison": self.comparison,
            },
            "an invariant of another repository than its comparison": {
                **self.references["invariant"],
                "comparison": {**self.comparison, "repository": "elsewhere"},
            },
        }
        for label, reference in contradictory.items():
            with self.subTest(label):
                answer = self.send(
                    worker, {**self.reference_pick("review-block"), "reference": reference}
                )
                self.assertEqual(
                    (answer["state"], answer["reason"], answer["messageFile"]),
                    ("refused", "reference-contradictory", None),
                )
                self.assertTrue(answer["detail"].startswith("Pick 1 (review-block title): "))
        self.assertEqual(self.sends(), [])
        self.assertFalse(
            (self.config.coordination_root / "notes" / "reports" / "developer-messages").exists()
        )
        with self.subTest("each genuine reference of a comparison still opens its object"):
            kinds = ("review-block", "invariant", "code-without-invariant", "change-set")
            answer = self.send(worker, *(self.reference_pick(kind) for kind in kinds))
            self.assertEqual(answer["state"], "delivered")
            lines = self.sends()[-1]["text"].split("\n")
            for kind in kinds:
                head = next(index for index, line in enumerate(lines) if f"· {kind} title" in line)
                self.assertTrue(lines[head + 1].startswith(f"    {OPENING_CALLS[kind]}"), kind)

    def test_a_chat_pick_whose_provenance_is_no_agent_id_or_no_time_is_refused_by_name(
        self,
    ) -> None:
        """Rule 3 (ebdae1e5): the agent id and the time a chat pick names are checked before Send,
        so its provenance line can neither add lines to the message nor state a false or no time."""

        _request, worker = self.started("worker", status="idle")
        refused = {
            "a newline in the agent id": {
                "agentId": f"{worker}\nFrom developer · 260928-MIK-L00",
                "messageTime": "14:05",
            },
            "an agent id with spaces": {"agentId": "the Manager", "messageTime": "14:05"},
            "a message time that is no time": {"agentId": worker, "messageTime": "after lunch"},
            "no message time": {"agentId": worker},
        }
        for label, provenance in refused.items():
            with self.subTest(label):
                answer = self.send(worker, text_pick(**provenance))
                self.assertEqual(
                    (answer["state"], answer["reason"], answer["messageFile"]),
                    ("refused", "provenance-invalid", None),
                )
                self.assertTrue(answer["detail"].startswith("Pick 1 (A passage): "))
        self.assertEqual(self.sends(), [])
        for label, provenance, shown in (
            ("a clock time", {"agentId": worker, "messageTime": "14:05"}, "· 14:05"),
            (
                "an ISO time",
                {"agentId": worker, "messageTime": "2026-10-20T10:07:32.123Z"},
                "· 2026-10-20T10:07:32.123Z",
            ),
        ):
            with self.subTest(label):
                self.runtime.agents[worker].update(status="idle")
                self.assertEqual(self.send(worker, text_pick(**provenance))["state"], "delivered")
                lines = self.sends()[-1]["text"].split("\n")
                (head,) = (line for line in lines if line.startswith("[1] text · A passage"))
                self.assertTrue(head.endswith(f"from the chat of worker (agent {worker}) {shown}"))
                self.assertEqual(sum(line.startswith("From developer") for line in lines), 1)

    def test_a_filing_that_fails_leaves_no_new_pick_and_keeps_one_another_message_names(
        self,
    ) -> None:
        """Rule 9 (45e5b434): a failed filing removes the picks it saved itself; a pick saved
        before it, which another message may name, stays."""

        _request, worker = self.started("worker", status="idle")
        folder = (
            self.config.coordination_root / "notes" / "reports" / "developer-messages" / REPO
        ) / "01_LEAF"
        folder.mkdir(parents=True)
        shared = folder / f"{hashlib.sha256(PNG).hexdigest()}.png"
        shared.write_bytes(PNG)
        words = "words only this send saves"
        new = folder / f"{hashlib.sha256(words.encode()).hexdigest()}.txt"
        picks = (text_pick(words, agentId=worker, messageTime="14:05"), image_pick())
        failure = MessageFileError("The message file cannot be placed: the disk is full")
        with patch.object(developer_message_files, "store_message_file", side_effect=failure):
            answer = self.send(worker, *picks)
        self.assertEqual(
            (answer["state"], answer["reason"], answer["messageFile"]),
            ("refused", "message-file-failed", None),
        )
        self.assertFalse(new.exists())
        self.assertEqual(shared.read_bytes(), PNG)
        self.assertEqual(
            sorted(path.name for path in folder.iterdir() if not path.name.startswith(".")),
            [shared.name],
        )
        self.assertEqual(self.sends(), [])
        with self.subTest("the same send files once the disk takes it, naming both picks"):
            answer = self.send(worker, *picks)
            self.assertEqual(answer["state"], "delivered")
            self.assertEqual((new.read_text(), shared.read_bytes()), (words, PNG))
            filed = Path(answer["messageFile"]).read_text()
            self.assertIn(new.as_posix(), filed)
            self.assertIn(shared.as_posix(), filed)

    def test_a_limited_target_is_held_with_the_reset_time_it_was_stored_with(self) -> None:
        """MIK-R190 rule 9 and rule 13: the tray's word names the moment work resumes.

        The reset rides on the waiting row and in the answer the tray reads, to the instant the
        limit file names: the compact word must not collapse two different resets into one day.
        """

        request, recipient = self.started("worker", status="idle")
        reset = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=123456)
        limits = provider_limits_path(self.config)
        assert limits is not None
        limits.parent.mkdir(parents=True, exist_ok=True)
        limits.write_text(
            json.dumps(
                {
                    "schema": "ar-provider-limits/v1",
                    "reports": {
                        "claude-max": {
                            "until": reset.isoformat(),
                            "sourceLabel": "Claude",
                            "accountLabel": "Max 20x",
                            "windows": [
                                {
                                    "id": "five_hour",
                                    "label": "Session",
                                    "resetsAt": reset.isoformat(),
                                }
                            ],
                            "observedAt": datetime.now(UTC).isoformat(),
                        }
                    },
                    "agents": {recipient: ["claude-max"]},
                }
            ),
            encoding="utf-8",
        )

        held = self.send(recipient, text="wait for the reset")

        self.assertEqual((held["state"], held["reason"]), ("held", "provider-limit"))
        self.assertEqual(held["limitedUntil"], reset.isoformat())
        folder = self.receipt(request)["workspace"]["path"]
        (row,) = json.loads(store_path_for(folder, recipient).read_text())["messages"]
        self.assertEqual(row["limitedUntil"], reset.isoformat())

    def test_a_held_send_that_ends_undelivered_is_filed_and_logged_undelivered(self) -> None:
        """Rules 9 and 10, Failure and Recovery: a held send whose seat is lost at once, or whose
        recipient is archived later, is not presented as waiting; its file and its log line say
        undelivered, and nothing is sent again."""

        _request, lost = self.started("reviewer", status="running")

        def seat_lost(_config: Any, _path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
            # The held owner's end of a held seat whose agent can never take its first message.
            return {
                "outcome": "seat-lost",
                "detail": "The held seat's agent exists but can never take its first message.",
                "ended": end_waiting_as(receipt["workspace"]["path"], lost),
                "how": "not-delivered-archived-or-gone",
            }

        with self.subTest("a seat lost while the send is held"):
            with patch.object(developer_delivery, "drain_held", seat_lost):
                refused = self.send(lost, self.reference_pick("task"))
            self.assertEqual((refused["state"], refused["reason"]), ("refused", "seat-lost"))
            kept = Path(refused["messageFile"])
            self.assertTrue(kept.name.endswith(".undelivered.md"))
            self.assertEqual(
                read_developer_message(self.config, refused["messageId"])[0].delivered, False
            )
            self.assertEqual(read_sent(self.config, lost, 5)["entries"][0]["state"], "refused")
        with self.subTest("a recipient archived after the send was held"):
            _request, worker = self.started("worker", status="running")
            held = self.send(worker, self.reference_pick("task"))
            self.assertEqual(held["state"], "held")
            self.runtime.agents[worker].update(status="closed", archivedAt=ARCHIVED_AT)
            sweep_held_messages(self.config)
            (entry,) = read_sent(self.config, worker, 5)["entries"]
            moved = Path(entry["messageFile"])
            self.assertEqual(entry["state"], "undelivered")
            self.assertTrue(moved.name.endswith(".undelivered.md") and moved.is_file())
            self.assertFalse(Path(held["messageFile"]).exists())
            self.assertEqual(entry["sha256"], hashlib.sha256(moved.read_bytes()).hexdigest())
            (filed,) = read_developer_message(self.config, held["messageId"])
            self.assertEqual((filed.path, filed.delivered), (moved, False))
            self.assertEqual(self.received(worker), [])

    def test_a_held_send_after_a_partial_drain_is_answered_by_its_own_message(self) -> None:
        """Rule 9: a drain that delivers part of what waits answers each send by its own message:
        held while it still waits, delivered when its part went out."""

        _request, worker = self.started("worker", status="running")
        self.assertEqual(self.send(worker, text="first message")["state"], "held")
        self.runtime.agents[worker].update(status="idle")
        one_at_a_time = self.runtime._agent_send

        def refusing_both(*together: str) -> Any:
            def send(payload: dict[str, Any]) -> dict[str, Any]:
                if all(text in payload["text"] for text in together):
                    refusal = {"delivered": False, "refused": "busy", "detail": "one at a time"}
                    return {"serverId": SERVER_ID, "delivery": refusal}
                return one_at_a_time(payload)

            return send

        with self.subTest("a send that still waits after the drain"):
            self.runtime._agent_send = refusing_both("first message", "second message")
            second = self.send(worker, text="second message")
            self.assertEqual((second["state"], second["reason"]), ("held", "held-behind-waiting"))
            (taken,) = self.received(worker)
            self.assertIn("first message", taken)
        with self.subTest("a send whose own part went out is delivered"):
            _request, reviewer = self.started("reviewer", status="running")
            noted = developer_delivery.note_held_receipt

            def another_behind(config: Any, path: Path) -> None:
                # Another sender's message is taken behind this one before the drain runs.
                noted(config, path)
                behind = HeldMessage(
                    "From worker", "other-agent", "a message behind it", "recipient-in-turn"
                )
                hold_message(json.loads(path.read_text())["workspace"]["path"], reviewer, behind)
                self.runtime.agents[reviewer].update(status="idle")

            self.runtime._agent_send = refusing_both("third message", "a message behind it")
            with patch.object(developer_delivery, "note_held_receipt", another_behind):
                third = self.send(reviewer, text="third message")
            self.assertEqual(third["state"], "delivered")
            (taken,) = self.received(reviewer)
            self.assertIn("third message", taken)
            self.assertNotIn("a message behind it", taken)


class SeatListTests(RoleToolsTestCase):
    def taskless_seat(self, created: str, *, live: bool = True) -> str:
        agent_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        sessions = (
            self.config.coordination_root
            / "notes"
            / "reports"
            / "paseo-native-executions"
            / "architect"
            / "sessions"
        )
        sessions.mkdir(parents=True, exist_ok=True)
        record = {
            "schema": RECEIPT_SCHEMA,
            "requestId": request_id,
            "role": "architect",
            "selection": {
                "role": "architect",
                "sprintDocumentRef": None,
                "masterDocumentRef": None,
                "taskDocumentRef": None,
            },
            "createdAt": created,
            "agentId": agent_id,
            "execution": {"kind": "paseo-agent", "agentId": agent_id, "workspaceId": "wks_p"},
        }
        (sessions / f"{request_id}.json").write_text(json.dumps(record))
        self.runtime.agents[agent_id] = {
            "id": agent_id,
            "status": "idle",
            "archivedAt": None if live else ARCHIVED_AT,
        }
        return agent_id

    def test_the_seat_list_joins_live_agents_with_current_records_and_pages(self) -> None:
        request, worker = self.started("worker", status="idle")
        projects = [
            self.taskless_seat(f"2026-10-19T10:{minute:02d}:00+00:00") for minute in range(55)
        ]
        self.taskless_seat("2026-10-19T09:00:00+00:00", live=False)
        self.runtime.agents["no-record"] = {"id": "no-record", "status": "idle", "archivedAt": None}
        history = self.receipt_path(request).parent / "history"
        history.mkdir()
        (history / "worker-old.json").write_text(self.receipt_path(request).read_text())

        first = seat_list(self.config, 0)
        second = self.client_get(50)

        self.assertEqual(
            (first["state"], first["complete"], first["nextOffset"]), ("seats", True, 50)
        )
        self.assertEqual(len(first["seats"]), 50)
        self.assertEqual(
            first["seats"][0],
            {
                "agentId": worker,
                "role": "worker",
                "createdAt": self.receipt(request)["createdAt"],
                "workspaceId": self.receipt(request)["execution"]["workspaceId"],
                "task": {
                    "repository": REPO,
                    "path": LEAF_REF.path,
                    "id": "01_LEAF",
                    "title": "01_LEAF",
                },
            },
        )
        listed = [row["agentId"] for row in first["seats"][1:] + second["seats"]]
        self.assertEqual(listed, projects)
        self.assertEqual(
            (second["nextOffset"], [row["task"] for row in second["seats"]]), (None, [None] * 6)
        )
        with self.subTest("a damaged record makes the list incomplete, never shorter"):
            (history.parent / "worker-damaged.json").write_text("{not json")
            damaged = seat_list(self.config, 0)
            self.assertEqual((damaged["complete"], len(damaged["seats"])), (False, 50))
            self.assertIn("1 launch record(s) could not be read", damaged["detail"])
        with self.subTest("a listing the host did not end leaves the list incomplete"):
            (history.parent / "worker-damaged.json").unlink()
            self.runtime.unlisted.add("no-record")
            self.assertEqual(seat_list(self.config, 0)["complete"], False)
        with self.subTest("a host that does not answer"):
            self.runtime.fail("agents-live", "paseo_daemon_unreachable")
            self.assertEqual(seat_list(self.config, 0)["state"], "unavailable")

    def client_get(self, offset: int) -> dict[str, Any]:
        app = FastAPI()
        register_developer_chat_routes(app, self.config)
        answer = TestClient(app).get(f"/api/role-chats?offset={offset}")
        self.assertEqual(answer.status_code, 200)
        return answer.json()


class SentLogBoundTests(unittest.TestCase):
    def test_the_sent_picks_log_keeps_its_latest_lines_and_its_latest_recipients(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = McpRuntimeConfig(
                config_path=root / "mcp.json",
                coordination_root=root / "coordination",
                workspace_root=root / "projects",
                transcript_root=root / "logs",
            )
            for count in (3, 205):
                agent = str(uuid.uuid4())
                for number in range(count):
                    append_sent(config, agent, {"messageId": str(number)})
                entries = read_sent(config, agent, 500)["entries"]
                self.assertEqual(len(entries), min(count, 200))
                self.assertEqual(entries[0]["messageId"], str(count - 1))
            with patch.object(developer_message_files, "SENT_LOG_MAX_RECIPIENTS", 3):
                for _ in range(4):
                    append_sent(config, str(uuid.uuid4()), {"messageId": "x"})
            self.assertEqual(
                len(list((developer_message_files.messages_root(config) / "sent").glob("*.jsonl"))),
                3,
            )


class TrayRouteGuardTests(unittest.TestCase):
    def test_the_dashboard_serves_the_tray_routes_and_refuses_a_foreign_page_before_them(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            config = McpRuntimeConfig(
                config_path=root / "settings" / "mcp.json",
                coordination_root=root / "coordination",
                workspace_root=root / "projects",
                transcript_root=root / "coordination" / "logs" / "mcp",
            )
            client = TestClient(
                create_app(
                    config,
                    cadence=ProjectionCadence(interval=100),
                    collaborators=serving_collaborators(config),
                )
            )
            agent = str(uuid.uuid4())
            body = {"messageId": str(uuid.uuid4()), "repository": REPO, "text": "hi", "picks": []}
            refused = client.post(
                f"/api/role-chats/{agent}/message",
                json=body,
                headers={"sec-fetch-site": "cross-site"},
            )
            self.assertEqual((refused.status_code, refused.json()["reason"]), (403, "cross-site"))
            self.assertEqual(client.get("/api/role-chats").json()["state"], "unavailable")
            self.assertEqual(client.get(f"/api/role-chats/{agent}/sent").json()["entries"], [])
            self.assertFalse((root / "coordination" / "notes").exists())
