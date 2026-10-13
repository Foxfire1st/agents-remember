"""Focused document-rail reads and the shared first-start preparation seam."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents_remember.cli import (
    paseo_role_tools,
    role_document_chat_tree,
    role_document_chats,
    role_launch_progress,
    role_launch_receipts,
    role_launch_workspace,
)
from agents_remember.errors import RolePreparationError
from agents_remember.models.role_launcher import RoleSelection
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_paseo_launch import REPO, ROLE_REFS
from test_paseo_role_tools import RoleToolsTestCase
from test_paseo_status import StatusTestCase


def with_nested_role(record: dict[str, Any]) -> bytes:
    """The record as bytes, the role of its stored selection nested 52,000 lists deep.

    On the stack a thread has here the parser still reads a value that deep, and it cannot be
    turned into text any more, which is how a role is compared.
    """

    marked = json.dumps({**record, "selection": {**record["selection"], "role": "@role"}})
    return marked.replace('"@role"', "[" * 52_000 + '"worker"' + "]" * 52_000).encode()


class DocumentChatReadTests(StatusTestCase):
    def test_old_id_route_answers_canonically_with_one_notice(self) -> None:
        request = self.launched("investigator")
        app = FastAPI()
        role_document_chats.register_document_chat_route(app, self.config)
        response = TestClient(app).post(
            "/api/role-launch/document-chats",
            json={"role": "system-specialist", "requestId": str(request.request_id)},
        )
        self.assertEqual(response.status_code, 200)
        answer = response.json()
        self.assertEqual(answer["role"], "investigator")
        self.assertEqual(answer["warning"].count("Role 'system-specialist' was supplied"), 1)
        self.assertEqual(answer["agents"][0]["agentId"], self.receipt(request)["agentId"])

    def test_exact_started_projects_request_is_read_without_substituting_an_architect(self) -> None:
        request = self.launched("investigator")
        selection = RoleSelection(role="investigator")
        result = role_document_chats.document_chats(self.config, selection, 0, request.request_id)
        self.assertEqual(result["agents"][0]["agentId"], self.receipt(request)["agentId"])
        self.assertEqual(result["agents"][0]["labels"]["ar.role"], "investigator")
        self.runtime.agents[self.receipt(request)["agentId"]]["archivedAt"] = "today"
        self.assertEqual(
            role_document_chats.document_chats(self.config, selection, 0, request.request_id)[
                "agents"
            ],
            [],
        )

    def test_read_filters_archived_and_missing_agents_and_keeps_document_workspaces(self) -> None:
        worker = self.launched("worker")
        reviewer = self.launched("reviewer")
        selection = RoleSelection.model_validate(
            worker.model_dump(include=set(RoleSelection.model_fields))
        )
        answer = role_document_chats.document_chats(self.config, selection, 0)
        self.assertEqual(len(answer["agents"]), 2)
        self.assertEqual(
            {row["workspaceId"] for row in answer["agents"]},
            {self.receipt(worker)["execution"]["workspaceId"]},
        )
        self.runtime.agents[self.receipt(worker)["agentId"]]["archivedAt"] = "today"
        self.runtime.agents.pop(self.receipt(reviewer)["agentId"])
        self.assertEqual(
            role_document_chats.document_chats(self.config, selection, 0)["agents"], []
        )

    def test_projects_uses_last_started_live_architect_and_pages_old_archived_records(self) -> None:
        requests = [self.launched("architect") for _ in range(5)]
        receipts = [self.receipt(request) for request in requests]
        records = list(reversed(receipts))
        with patch.object(role_document_chats, "_records", return_value=records):
            for receipt in records[:3]:
                self.runtime.agents[receipt["agentId"]]["archivedAt"] = "today"
            selection = RoleSelection(role="architect")
            first = role_document_chats.document_chats(self.config, selection, 0)
            self.assertEqual(first, {"agents": [], "nextOffset": 3})
            second = role_document_chats.document_chats(self.config, selection, 3)
            self.assertEqual(second["agents"][0]["agentId"], records[3]["agentId"])

    def test_a_damaged_record_refuses_the_read_of_its_document_and_never_fails_it(self) -> None:
        worker = self.launched("worker")
        path, good = self.receipt_path(worker), self.receipt(worker)
        self.replace(role_document_chats, "resolve_role_launch_context", side_effect=self.context)
        app = FastAPI()
        role_document_chats.register_document_chat_route(app, self.config)
        client = TestClient(app, raise_server_exceptions=False)
        selection = worker.model_dump(
            mode="json", by_alias=True, include=set(RoleSelection.model_fields)
        )
        healthy = client.post("/api/role-launch/document-chats", json=selection)
        self.assertEqual((healthy.status_code, len(healthy.json()["agents"])), (200, 1))
        damage: dict[str, bytes] = {
            "bytes that are not UTF-8": b"\xff",
            "a number too long to read": b'{"schema": ' + b"1" * 5000 + b"}",
            "nesting too deep to read": b"[" * 200_000,
            "an execution that is no object": json.dumps({**good, "execution": "broken"}).encode(),
            "a role nested too deep to compare": with_nested_role(good),
        }
        for label, content in damage.items():
            with self.subTest(label):
                path.write_bytes(content)
                refusal = client.post("/api/role-launch/document-chats", json=selection)
                self.assertEqual(refusal.status_code, 409, refusal.text)
                self.assertRegex(
                    refusal.json()["detail"], "receipt is (unreadable|malformed); reconcile"
                )


class DocumentChatTreeTests(StatusTestCase):
    """The chats of every document the tree lists, known before a click."""

    @staticmethod
    def selection(role: str) -> dict[str, Any]:
        refs = {name: ref.model_dump(mode="json") for name, ref in ROLE_REFS[role].items()}
        return {"role": role, **refs}

    def tree(self, selections: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        app = FastAPI()
        role_document_chat_tree.register_document_chat_tree_route(app, self.config)
        response = TestClient(app).post(
            "/api/role-launch/document-chats/tree", json={"selections": selections}
        )
        return response.status_code, response.json()

    def agent_ids(self, chat: dict[str, Any]) -> list[str]:
        return sorted(row["agentId"] for row in chat["agents"])

    def other_leaves(self, count: int) -> list[dict[str, Any]]:
        """Selections of further leaves of the fixture's master; none has a launch record."""

        refs = [{"repository": REPO, "path": f"master/{n:03d}_other.json"} for n in range(count)]
        return [{**self.selection("worker"), "taskDocumentRef": ref} for ref in refs]

    def record_of(self, leaf: dict[str, Any], record: dict[str, Any], padding: int) -> Path:
        """Store ``record`` as the Worker's record of ``leaf``, larger by ``padding`` bytes."""

        path = role_launch_receipts._receipt_path(self.config, RoleSelection.model_validate(leaf))
        stored = {**record, "selection": leaf, "replayRequest": "x" * padding}
        path.write_text(json.dumps(stored), encoding="utf-8")
        return path

    @contextmanager
    def watched(self) -> Iterator[tuple[list[Path], list[dict[str, Any]]]]:
        """While the block runs: each file opened, and what a request keeps of its records."""

        opened: list[Path] = []
        kept: list[dict[str, Any]] = []
        open_file, answer_from = Path.open, role_document_chat_tree._live_agents

        def opening(path: Path, *args: Any, **kwargs: Any) -> Any:
            opened.append(path)
            return open_file(path, *args, **kwargs)

        def answering(records: list[dict[str, Any]] | None, live: Any) -> Any:
            kept.extend(records or [])
            return answer_from(records, live)

        with (
            patch.object(Path, "open", opening),
            patch.object(role_document_chat_tree, "_live_agents", answering),
        ):
            yield opened, kept

    def test_every_listed_document_is_answered_from_one_read_of_the_hosts_agents(self) -> None:
        started = {
            role: self.launched(role) for role in ("worker", "reviewer", "manager", "orchestrator")
        }
        agent = {role: self.receipt(request)["agentId"] for role, request in started.items()}
        listed = [self.selection(role) for role in ("worker", "manager", "orchestrator")]
        status, answer = self.tree(listed)
        self.assertEqual(status, 200, answer)
        self.assertEqual(self.commands(), ["agents-live"])
        self.assertEqual(
            [self.agent_ids(chat) for chat in answer["chats"]],
            [
                sorted([agent["worker"], agent["reviewer"]]),
                [agent["manager"]],
                [agent["orchestrator"]],
            ],
        )
        # A click then shows what the read of that one document shows.
        single = role_document_chats.document_chats(
            self.config, RoleSelection.model_validate(listed[0]), 0
        )
        self.assertEqual(answer["chats"][0]["agents"], single["agents"])
        self.agent_of(started["worker"])["archivedAt"] = "today"
        self.runtime.agents.pop(agent["manager"])
        _, later = self.tree(listed)
        self.assertEqual(self.agent_ids(later["chats"][0]), [agent["reviewer"]])
        self.assertEqual(later["chats"][1], {"agents": []})

    def test_the_host_is_read_once_at_any_tree_size_and_a_tree_over_the_limit_is_refused(
        self,
    ) -> None:
        self.launched("worker")
        leaf = self.selection("worker")
        limit = role_document_chat_tree.TREE_SELECTION_LIMIT
        for size in (8, limit - 1):
            with self.subTest(size=size):
                self.runtime.calls.clear()
                status, answer = self.tree([leaf, *self.other_leaves(size)])
                self.assertEqual(status, 200, answer)
                self.assertEqual(self.commands(), ["agents-live"])
                self.assertEqual(len(answer["chats"][0]["agents"]), 1)
                self.assertEqual(answer["chats"][1:], [{"agents": []}] * size)
        self.runtime.calls.clear()
        status, _refusal = self.tree(self.other_leaves(limit + 1))
        self.assertEqual(status, 422)
        self.assertEqual(self.commands(), [])

    def test_what_the_read_cannot_tell_is_answered_as_unknown_and_never_as_no_chat(self) -> None:
        worker = self.launched("worker")
        manager = self.launched("manager")
        self.launched("architect")
        listed = [self.selection("worker"), self.selection("manager"), self.selection("architect")]
        manager_agent = self.receipt(manager)["agentId"]
        # The Projects chats are no document of the tree: their own read answers for them.
        self.assertEqual(self.tree(listed)[1]["chats"][2], {"agents": None})
        # A listing that did not end: an agent it did not reach is unknown, not gone.
        self.runtime.unlisted = {self.receipt(worker)["agentId"]}
        _, partial = self.tree(listed)
        self.assertEqual(partial["chats"][0], {"agents": None})
        self.assertEqual(self.agent_ids(partial["chats"][1]), [manager_agent])
        self.runtime.unlisted = set()
        self.runtime.fail("agents-live", "paseo_daemon_unreachable", "no daemon")
        status, unreachable = self.tree(listed)
        self.assertEqual(status, 200)
        self.assertEqual(
            unreachable,
            {"chats": [], "unavailable": True, "detail": "paseo_daemon_unreachable: no daemon"},
        )

    def test_a_damaged_record_leaves_its_own_document_unknown_and_every_other_answered(
        self,
    ) -> None:
        worker, manager = self.launched("worker"), self.launched("manager")
        path, good = self.receipt_path(worker), self.receipt(worker)
        manager_agent = [self.receipt(manager)["agentId"]]
        listed = [self.selection("worker"), self.selection("manager")]
        host = good["execution"]
        leaf_without_path = {**good["selection"], "taskDocumentRef": {"repository": REPO}}
        damage: dict[str, Any] = {
            "bytes that are not UTF-8": b"\xff",
            "bytes that are no JSON": b"not a record",
            "a number too long to read": b'{"schema": ' + b"1" * 5000 + b"}",
            "nesting too deep to read": b"[" * 200_000,
            "a list": [good],
            "another schema": {**good, "schema": "another/v1"},
            "an execution that is no object": {**good, "execution": "broken"},
            "a kind that is no text": {**good, "execution": {**host, "kind": ["paseo-agent"]}},
            "an agent id that is no text": {**good, "execution": {**host, "agentId": ["bad"]}},
            "a workspace id that is no text": {**good, "execution": {**host, "workspaceId": 7}},
            "no role": {key: value for key, value in good.items() if key != "role"},
            "a role that is no text": {**good, "role": ["worker"]},
            "a role nested too deep to compare": with_nested_role(good),
            "a selection that is no object": {**good, "selection": "the leaf"},
            "a document reference without its path": {**good, "selection": leaf_without_path},
            "a creation time that is no text": {**good, "createdAt": float("nan")},
            "a text no answer can carry": {**good, "createdAt": "\ud800"},
        }

        def only_the_worker_document_is_unknown() -> None:
            self.runtime.calls.clear()
            status, answer = self.tree(listed)
            self.assertEqual(status, 200, answer)
            # Unknown, never "no chat"; the other document keeps its answer from the one listing.
            self.assertEqual(answer["chats"][0], {"agents": None})
            self.assertEqual(self.agent_ids(answer["chats"][1]), manager_agent)
            self.assertEqual(self.commands(), ["agents-live"])

        for label, content in damage.items():
            with self.subTest(label):
                path.write_bytes(
                    content if isinstance(content, bytes) else json.dumps(content).encode()
                )
                only_the_worker_document_is_unknown()
        with self.subTest("a directory in the record's place"):
            path.unlink()
            path.mkdir()
            only_the_worker_document_is_unknown()

    def test_a_document_named_many_times_is_read_once(self) -> None:
        worker = self.launched("worker")
        leaf, path = self.selection("worker"), self.receipt_path(worker)
        agent = [self.receipt(worker)["agentId"]]
        for size in (8, role_document_chat_tree.TREE_SELECTION_LIMIT):
            with self.subTest(size=size):
                self.runtime.calls.clear()
                with self.watched() as (opened, kept):
                    status, answer = self.tree([leaf] * size)
                self.assertEqual(status, 200, answer)
                self.assertEqual(opened.count(path), 1)
                # One kept record serves every selection that names the document.
                self.assertEqual(len({id(record) for record in kept}), 1)
                self.assertEqual(self.agent_ids(answer["chats"][0]), agent)
                self.assertEqual(answer["chats"], [answer["chats"][0]] * size)
                self.assertEqual(self.commands(), ["agents-live"])

    def test_a_request_reads_and_keeps_no_more_than_its_byte_limits_allow(self) -> None:
        worker = self.launched("worker")
        record = self.receipt(worker)
        agent = [record["agentId"]]
        kept_bytes: list[int] = []
        for count, padding in ((4, 2_000), (16, 40_000)):
            with self.subTest(records=count, padding=padding):
                *leaves, large_leaf = self.other_leaves(count + 1)
                paths = [self.record_of(leaf, record, padding) for leaf in leaves]
                large = self.record_of(large_leaf, record, 2 * padding)
                size = paths[0].stat().st_size
                # A record over the record limit is not read: its document is unknown, no other.
                with (
                    patch.object(role_document_chat_tree, "RECORD_BYTES_LIMIT", size),
                    self.watched() as (opened, kept),
                ):
                    _, answer = self.tree([large_leaf, *leaves])
                self.assertEqual(answer["chats"][0], {"agents": None})
                self.assertEqual(
                    [self.agent_ids(chat) for chat in answer["chats"][1:]], [agent] * count
                )
                self.assertEqual([file for file in opened if file in {large, *paths}], paths)
                # What is kept of a record leaves the record's large field behind.
                self.assertEqual(len(kept), count)
                kept_bytes.append(max(len(json.dumps(part)) for part in kept))
                # Records beyond the request's bytes are not read: the earlier ones are known.
                within = count // 2
                with (
                    patch.object(role_document_chat_tree, "REQUEST_BYTES_LIMIT", within * size),
                    self.watched() as (opened, _kept),
                ):
                    _, answer = self.tree(leaves)
                self.assertEqual(
                    [self.agent_ids(chat) for chat in answer["chats"][:within]], [agent] * within
                )
                self.assertEqual(answer["chats"][within:], [{"agents": None}] * (count - within))
                self.assertEqual([file for file in opened if file in paths], paths[:within])
        self.assertLess(kept_bytes[1], 2_000)
        self.assertEqual(kept_bytes[0], kept_bytes[1])


class FirstStartPreparationTests(RoleToolsTestCase):
    def test_start_reads_recorded_master_parent_and_same_request_reuses_the_enclosure(self) -> None:
        series = self.master.path.parent / "series-contract.md"
        series.parent.mkdir(parents=True, exist_ok=True)
        series.write_text("contract selected by the production reader\n", encoding="utf-8")
        request_id = uuid.uuid4()
        with patch.object(
            role_launch_workspace,
            "load_contract",
            return_value=SimpleNamespace(parent_task_name="master"),
        ) as read:
            first = self.start(self.architect, "worker", request_id=request_id)
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["preparation"], {"enclosure": "created", "workspace": "created"})
            self.assertEqual(self.enclosures.start_calls[0].parent_task, "master")
            read.assert_called_once_with(series)
            repeat = self.start(self.architect, "worker", request_id=request_id)
        self.assertEqual(repeat["agentId"], first["agentId"])
        self.assertEqual(len(self.enclosures.start_calls), 1)

    def test_preparation_refusals_keep_exact_status_reason_and_repair_on_role_start(self) -> None:
        cases = {
            "atomic-series-contract-edge-mismatch": "reconcile",
            "choose_stale_base_recovery": "source branch",
            "reopen-required": "task_reopen",
            "leaf_enclosure_start_timeout": "worktree_status",
            "missing-integration-branch": "integrationBranch",
        }
        for status, remedy in cases.items():
            with self.subTest(status=status):
                self.enclosures.start_result = {
                    "ok": False,
                    "state": status,
                    "summary": "integrationBranch missing"
                    if status == "missing-integration-branch"
                    else "exact refusal",
                }
                result = self.start(self.architect, "worker")
                self.assertEqual(result["preparationStatus"], status)
                self.assertIn(remedy, result["nextAction"])
                self.assertIn(self.enclosures.start_result["summary"], result["detail"])
                self.assertFalse(self.enclosures.started)
                self.assertIsNone(
                    json.loads(bytes(role_launch_progress.read(uuid.uuid4()).body))["phase"]
                )
        error = role_launch_workspace._preparation_refusal(
            {
                "ok": False,
                "state": "x",
                "summary": "own words",
                "nextTool": "repair",
                "nextArgs": {"exact": "args"},
            },
            self.leaf.path,
            "agents-remember",
        )
        self.assertIsInstance(error, RolePreparationError)
        self.assertIn('repair({"exact": "args"})', str(error))

    def test_masterless_starts_report_opened_without_a_creation_guess(self) -> None:
        for role in ("investigator", "orchestrator"):
            with self.subTest(role=role):
                result = self.start(self.architect, role)
                self.assertTrue(result["ok"], result)
                self.assertEqual(
                    result["preparation"], {"enclosure": "not-applicable", "workspace": "opened"}
                )
                self.assertIn("Projects workspace was opened", result["detail"])
                self.assertIn("host does not report whether it was created", result["detail"])
                self.assertEqual(self.enclosures.start_calls, [])
        request = self.launched("architect")
        result = paseo_role_tools._started(self.config, request)
        self.assertEqual(result["preparation"]["workspace"], "opened")
        self.assertIn("host does not report whether it was created", result["detail"])

    def test_progress_has_one_request_and_is_reclaimed_when_dispatch_ends(self) -> None:
        request_id = uuid.uuid4()
        role_launch_progress.begin(request_id)
        self.assertEqual(
            json.loads(bytes(role_launch_progress.read(request_id).body))["phase"], "preparing"
        )
        role_launch_progress.starting()
        self.assertEqual(
            json.loads(bytes(role_launch_progress.read(request_id).body))["phase"], "starting"
        )
        role_launch_progress.finish()
        self.assertIsNone(json.loads(bytes(role_launch_progress.read(request_id).body))["phase"])
