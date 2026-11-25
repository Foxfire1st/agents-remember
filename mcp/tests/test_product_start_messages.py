"""The product's start message to a Reviewer and a Curator.

The moment's file and message are composed by ``file_briefs.moments`` (its own tests cover what
they say); here they are stubbed, and the cases prove when the product sends, through which route,
under which line, and what the start answers. The fake runtime of the role tools is the host.
"""

from __future__ import annotations

import json
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agents_remember.application.file_briefs import moments
from agents_remember.application.file_briefs.moments import (
    MomentMessage,
    MomentReport,
    StartRequest,
    moment_message,
)
from agents_remember.cli import product_start_message, role_held_messages, role_launch_routes
from agents_remember.cli.paseo_role_tools import start_leaf_set
from agents_remember.cli.product_messages import (
    PRODUCT_SENDER,
    ProductMessage,
    send_product_message,
)
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.kernel.direct_execution_grants import (
    DirectExecutionGrant,
    grant_path_for,
    write_grant_file,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.models.role_agents import RoleStartResponse, RoleStartSetCall
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    contract_publication_text,
    default_series_contract,
    write_contract,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_index_test_support import commit_all, git
from paseo_role_tools_test_support import RoleToolsTestCase, binding, selection_of
from test_moment_composer import CAFE, build_change
from test_moment_composer import RULE as CAFE_RULE
from test_paseo_launch import LEAF_REF, MASTER_REF, REPO, SPRINT_REF

LEAF = "261112-KNO-L9"
PRODUCT_SENDER_WORDS = "From Agents Remember (product) · "
PRODUCT_LINE = f"{PRODUCT_SENDER_WORDS}{LEAF}"


class ProductStartMessageTests(RoleToolsTestCase):
    """Who gets the start message, when, and what the start answers about it."""

    def setUp(self) -> None:
        super().setUp()
        self.composed: list[StartRequest] = []
        self.moved = 0
        self.idle_on_create = False
        self.replace(product_start_message, "start_request", side_effect=self.leaf_moment)
        self.replace(moments, "compose_start_message", side_effect=self.compose)
        create = self.runtime._agent_create

        def created(payload: dict[str, Any]) -> dict[str, Any]:
            reply = create(payload)
            if self.idle_on_create and payload.get("prompt") is not None:
                self.runtime.agents[payload["agentId"]]["status"] = "idle"
            return reply

        self.replace(self.runtime, "_agent_create", side_effect=created)

    def leaf_moment(
        self, _config: Any, _receipt: dict[str, Any], moment: Any, limit: int
    ) -> StartRequest:
        root = Path(self.root)
        return StartRequest(moment, LEAF, root / "memory", root / "code", "base", root, limit)

    def compose(self, request: StartRequest) -> MomentMessage:
        """The message the real composer makes over ``self.moved`` moved rules."""

        self.composed.append(request)
        moved = tuple(
            SimpleNamespace(
                head=SimpleNamespace(
                    id=f"R{number:03d}", title=SimpleNamespace(text=f"Rule number {number}")
                )
            )
            for number in range(self.moved)
        )
        report = MomentReport(
            moment=request.moment,
            leaf=request.leaf,
            changed=("a.py",),
            briefs=(),
            rules=cast(Any, moved),
            moved=cast(Any, moved),
            families=1,
            without_proof=0,
            documents=0,
        )
        file = request.memory_root / "briefs" / "_task" / f"{request.moment}.md"
        return MomentMessage(moment_message(report, file.as_posix(), request.limit), file, report)

    def selection(self, role: str) -> RoleSelection:
        return RoleSelection.model_validate(
            {
                "role": role,
                "sprintDocumentRef": SPRINT_REF,
                "masterDocumentRef": MASTER_REF,
                "taskDocumentRef": LEAF_REF,
            }
        )

    def held_rows(self, receipt: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
        store = Path(receipt["workspace"]["path"]) / ".ar-product" / "held-messages"
        return json.loads((store / f"{agent_id}.json").read_text())["messages"]

    def test_a_direct_reviewer_start_sends_one_message_and_a_repeat_none(self) -> None:
        self.idle_on_create = True
        request_id = uuid.uuid4()

        started = self.start(self.architect, "reviewer", request_id=request_id)

        agent_id = started["agentId"]
        report = started["productMessage"]
        self.assertEqual(
            (report["state"], report["moment"], report["recipientAgentId"]),
            ("delivered", "review-start", agent_id),
        )
        self.assertTrue(report["file"].endswith("briefs/_task/review-start.md"))
        product_texts = [text for text in self.received(agent_id) if PRODUCT_LINE in text]
        self.assertEqual(len(product_texts), 1)
        self.assertTrue(product_texts[0].startswith(f"{PRODUCT_LINE}\nReview start for {LEAF}."))
        self.assertIn(report["file"], product_texts[0])
        filed = Path(report["messageFile"]).read_text(encoding="utf-8")
        self.assertIn(PRODUCT_LINE, filed)
        modeled = RoleStartResponse.model_validate(started).productMessage
        self.assertEqual(modeled.state if modeled is not None else None, "delivered")
        receipt = _read_receipt(_receipt_path(self.config, self.selection("reviewer")))
        assert receipt is not None
        self.assertEqual(receipt["productStartMessage"]["state"], "delivered")

        with self.subTest("a repeat with the same request id sends none"):
            repeat = self.start(self.architect, "reviewer", request_id=request_id)

            self.assertEqual(repeat["agentId"], agent_id)
            self.assertNotIn("productMessage", repeat)
            self.assertEqual(len([t for t in self.received(agent_id) if PRODUCT_LINE in t]), 1)
            self.assertEqual(len(self.composed), 1)

        with self.subTest("a replacement is a new start and gets its own"):
            self.runtime.agents[agent_id]["status"] = "idle"
            replaced = self.start(self.architect, "reviewer", replaces_agent_id=agent_id)

            self.assertEqual(replaced["status"], "running", replaced)
            self.assertNotEqual(replaced["agentId"], agent_id)
            self.assertEqual(replaced["productMessage"]["state"], "delivered")
            self.assertEqual(
                len([t for t in self.received(replaced["agentId"]) if PRODUCT_LINE in t]), 1
            )

    def test_a_dashboard_start_sends_one_message_and_a_replay_none(self) -> None:
        """The launcher's dispatch route is a start as role_start is: one message per execution."""

        self.idle_on_create = True
        endpoint = role_launch_routes._bind_dispatch_endpoint(self.config)
        request = self.request("reviewer")

        response = endpoint(request)

        self.assertEqual(response.status_code, 200, bytes(response.body))
        agent_id = self.receipt(request)["execution"]["agentId"]
        product_texts = [text for text in self.received(agent_id) if PRODUCT_LINE in text]
        self.assertEqual(len(product_texts), 1)
        self.assertTrue(product_texts[0].startswith(f"{PRODUCT_LINE}\nReview start for {LEAF}."))
        recorded = self.receipt(request)["productStartMessage"]
        self.assertEqual((recorded["state"], recorded["moment"]), ("delivered", "review-start"))
        self.assertTrue(Path(recorded["file"]).name == "review-start.md")

        with self.subTest("a replay of the same request sends none"):
            endpoint(request)

            self.assertEqual(len([t for t in self.received(agent_id) if PRODUCT_LINE in t]), 1)
            self.assertEqual(len(self.composed), 1)

        with self.subTest("a replacement from the launcher gets its own"):
            self.runtime.agents[agent_id]["status"] = "idle"
            replacement = RoleDispatchRequest.model_validate(
                {
                    **request.model_dump(mode="json", by_alias=True, exclude_none=True),
                    "requestId": str(uuid.uuid4()),
                    "replacesAgentId": agent_id,
                }
            )
            answer = endpoint(replacement)

            self.assertEqual(answer.status_code, 200, bytes(answer.body))
            replaced = self.receipt(replacement)["execution"]["agentId"]
            self.assertNotEqual(replaced, agent_id)
            self.assertEqual(len([t for t in self.received(replaced) if PRODUCT_LINE in t]), 1)

    def test_a_worker_start_gets_no_product_message(self) -> None:
        started = self.start(self.architect, "worker")

        self.assertEqual(started["status"], "running")
        self.assertNotIn("productMessage", started)
        self.assertEqual(self.composed, [])

    def test_a_busy_recipient_gets_it_held_under_the_product_sender(self) -> None:
        started = self.start(self.architect, "curator")

        agent_id = started["agentId"]
        report = started["productMessage"]
        self.assertEqual((report["state"], report["moment"]), ("held", "curator-start"))
        self.assertEqual(self.received(agent_id), [])
        receipt = _read_receipt(_receipt_path(self.config, self.selection("curator")))
        assert receipt is not None
        rows = self.held_rows(receipt, agent_id)
        self.assertEqual(
            [(row["id"], row["senderAgentId"], row["senderLine"]) for row in rows],
            [(report["heldMessageId"], PRODUCT_SENDER, PRODUCT_LINE)],
        )
        self.assertTrue(rows[0]["text"].startswith(f"Curator start, your list of work for {LEAF}."))

        with self.subTest("the held message is delivered once the recipient can take it"):
            self.runtime.agents[agent_id]["status"] = "idle"
            path = _receipt_path(self.config, self.selection("curator"))
            current = _read_receipt(path)
            assert current is not None

            outcome = role_held_messages.drain_held(self.config, path, current)

            delivered = [text for text in self.received(agent_id) if PRODUCT_LINE in text]
            self.assertEqual(len(delivered), 1, outcome)
            self.assertTrue(
                delivered[0].startswith(
                    f"{PRODUCT_LINE}\nCurator start, your list of work for {LEAF}."
                )
            )
            self.assertIn(Path(report["file"]).as_posix(), delivered[0])
            self.assertEqual(self.held_rows(current, agent_id), [])
            filed = Path(report["messageFile"]).read_text(encoding="utf-8")
            self.assertIn(PRODUCT_LINE, filed)
            self.assertIn(report["heldMessageId"], json.dumps(outcome, default=str))

    def test_a_held_curator_gets_it_only_when_its_seat_opens(self) -> None:
        manager = binding("manager")
        result = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        curator = result["seats"]["curator"]
        self.assertTrue(curator["held"])
        self.assertNotIn("productMessage", curator)
        self.assertEqual(self.composed, [])
        self.runtime.calls.clear()

        opened = self.message(manager, agent_id=curator["agentId"], text="Curate the plan.")

        self.assertTrue(opened["openedHeldSeat"])
        self.assertEqual([request.moment for request in self.composed], ["curator-start"])
        prompt = next(p for c, p in self.runtime.calls if c == "agent-create")["prompt"]
        self.assertLess(prompt.index(PRODUCT_LINE), prompt.index("Curate the plan."))
        self.assertIn(f"{PRODUCT_LINE}\nCurator start, your list of work for {LEAF}.", prompt)

    def test_a_reviewer_a_set_opens_at_once_gets_it_in_its_seat(self) -> None:
        self.idle_on_create = True
        result = start_leaf_set(
            self.config,
            RoleStartSetCall(
                request_id=uuid.uuid4(),
                reviewer_message="Judge the plan.",
                **selection_of("worker"),
            ),
            environment=binding("manager").environment(),
        )

        reviewer, curator = result["seats"]["reviewer"], result["seats"]["curator"]
        self.assertEqual(reviewer["productMessage"]["moment"], "review-start")
        self.assertNotIn("productMessage", curator)
        self.assertEqual([request.moment for request in self.composed], ["review-start"])

    def test_the_text_sent_stays_within_the_limit_and_counts_what_it_leaves_out(self) -> None:
        self.idle_on_create = True
        self.moved = 300

        started = self.start(self.architect, "reviewer")

        limit = self.composed[0].limit
        self.assertEqual(limit, 1000)
        sent = next(t for t in self.received(started["agentId"]) if PRODUCT_LINE in t)
        text = sent.removeprefix(f"{PRODUCT_LINE}\n")
        self.assertLessEqual(len(text), limit)
        self.assertRegex(text, r"\((\d+) more in the file\)")
        named = int(text.split(" more in the file")[0].rsplit("(", 1)[1])
        self.assertEqual(named + text.count("Rule number"), 300)

        with self.subTest("a text over the route's limit is refused, never cut by the route"):
            path = _receipt_path(self.config, self.selection("reviewer"))
            receipt = _read_receipt(path)
            assert receipt is not None
            answer = send_product_message(
                self.config,
                ProductMessage(started["agentId"], path, receipt, LEAF, "x" * (limit + 1)),
            )
            self.assertEqual((answer["status"], answer["refusal"]), ("refused", "message-too-long"))


class ProductStartMessageRouteTests(RoleToolsTestCase):
    """The launcher's dispatch and a direct-grant leaf, end to end, with the real composer.

    Nothing of the message is stubbed: the moment is composed over a real Git change and a
    converted memory, its file is written and read back, and the seat receives the message.
    """

    def setUp(self) -> None:
        super().setUp()
        create = self.runtime._agent_create

        def created(payload: dict[str, Any]) -> dict[str, Any]:
            reply = create(payload)
            if payload.get("prompt") is not None:
                self.runtime.agents[payload["agentId"]]["status"] = "idle"
            return reply

        self.replace(self.runtime, "_agent_create", side_effect=created)
        self.code, self.memory, self.base, _coordination = build_change(Path(self.root) / "change")

    def enclosure_moment(
        self, _config: Any, _receipt: dict[str, Any], moment: Any, limit: int
    ) -> StartRequest:
        """An enclosure leaf's moment over the real change (the fake enclosures write no contract)."""

        return StartRequest(
            moment, LEAF, self.memory, self.code, self.base, self.config.coordination_root, limit
        )

    def assert_one_real_message(self, agent_id: str, moment: str, file: str) -> None:
        texts = [text for text in self.received(agent_id) if PRODUCT_SENDER_WORDS in text]
        self.assertEqual(len(texts), 1, texts)
        opening = "Review start" if moment == "review-start" else "Curator start, your list of work"
        self.assertIn(f"\n{opening} for ", texts[0])
        self.assertIn("The change touches 2 files with 1 rule", texts[0])
        self.assertIn(f"Anchors moved at: {CAFE_RULE}", texts[0])
        self.assertIn(file, texts[0])
        written = Path(file).read_text(encoding="utf-8")
        self.assertIn(CAFE, written)
        self.assertIn("Brewing always returns a full cup.", written)

    def test_launcher_starts_of_both_roles_send_a_real_moment(self) -> None:
        self.replace(product_start_message, "start_request", side_effect=self.enclosure_moment)
        app = FastAPI()
        role_launch_routes.register_role_launch_routes(app, self.config)
        client = self.enterContext(TestClient(app))

        def dispatch(request: RoleDispatchRequest) -> int:
            return client.post(
                "/api/role-launch/dispatch",
                json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
            ).status_code

        for role, moment in (("reviewer", "review-start"), ("curator", "curator-start")):
            with self.subTest(role=role):
                request = self.request(role)
                self.assertEqual(dispatch(request), 200)
                receipt = self.receipt(request)
                agent_id = receipt["execution"]["agentId"]
                recorded = receipt["productStartMessage"]
                self.assertEqual((recorded["state"], recorded["moment"]), ("delivered", moment))
                self.assert_one_real_message(agent_id, moment, recorded["file"])

                self.assertEqual(dispatch(request), 200)
                self.assert_one_real_message(agent_id, moment, recorded["file"])

                self.runtime.agents[agent_id]["status"] = "idle"
                replacement = RoleDispatchRequest.model_validate(
                    {
                        **request.model_dump(mode="json", by_alias=True, exclude_none=True),
                        "requestId": str(uuid.uuid4()),
                        "replacesAgentId": agent_id,
                    }
                )
                self.assertEqual(dispatch(replacement), 200)
                replaced = self.receipt(replacement)
                self.assertNotEqual(replaced["execution"]["agentId"], agent_id)
                self.assert_one_real_message(
                    replaced["execution"]["agentId"],
                    moment,
                    replaced["productStartMessage"]["file"],
                )

    def grant_direct_execution(self) -> None:
        """Make the leaf a direct-grant leaf: a grant, and the master's series contract."""

        master_folder = self.leaf.path.parent
        series = default_series_contract(
            ContractTask(
                name="Master",
                repo_name=REPO,
                coordination_root=self.config.coordination_root,
                workflow_kind="light-task",
                memory_mode="external",
            ),
            task_root=master_folder,
            code=RepoBranchPlan(
                repo_path=self.code,
                source_branch="main",
                work_branch="ar/master",
                base_commit=self.base,
            ),
            memory=RepoBranchPlan(
                repo_path=self.memory,
                source_branch="main",
                work_branch="ar/master",
                base_commit=git(self.memory, "rev-parse", "HEAD"),
            ),
        )
        series = replace(series, memory_worktree=self.memory)
        series.worktree_group.mkdir(parents=True, exist_ok=True)
        write_contract(series.contract_path, series)
        publish_new_lifecycle_operation_location(
            series, contract_text=contract_publication_text(series.contract_path, series)
        )
        write_grant_file(
            grant_path_for(self.config.coordination_root, LEAF_REF),
            DirectExecutionGrant(
                leaf=LEAF_REF,
                granted_by="architect",
                granted_at="2026-11-20T00:00:00+00:00",
                granter_agent_id="fixture",
            ),
        )

    def test_direct_grant_starts_of_both_roles_send_a_real_moment(self) -> None:
        ignore = self.memory / ".gitignore"
        initial_ignore = ignore.read_bytes()
        self.grant_direct_execution()
        for role, moment in (("reviewer", "review-start"), ("curator", "curator-start")):
            with self.subTest(role=role):
                request_id = uuid.uuid4()
                started = self.start(self.architect, role, request_id=request_id)

                self.assertEqual(started["status"], "running", started)
                receipt = _read_receipt(_receipt_path(self.config, self.selection(role)))
                assert receipt is not None
                self.assertEqual(receipt["workspace"]["enclosurePreparation"], "direct")
                report = started["productMessage"]
                self.assertEqual((report["state"], report["moment"]), ("delivered", moment))
                self.assertEqual(
                    Path(report["file"]), self.memory / "briefs" / "_task" / f"{moment}.md"
                )
                self.assertEqual(ignore.read_bytes(), initial_ignore)
                self.assertEqual(git(self.memory, "status", "--porcelain"), "")
                agent_id = started["agentId"]
                self.assert_one_real_message(agent_id, moment, report["file"])

                repeat = self.start(self.architect, role, request_id=request_id)
                self.assertNotIn("productMessage", repeat)
                self.assert_one_real_message(agent_id, moment, report["file"])

                self.runtime.agents[agent_id]["status"] = "idle"
                replaced = self.start(self.architect, role, replaces_agent_id=agent_id)
                self.assertEqual(replaced["status"], "running", replaced)
                self.assertNotEqual(replaced["agentId"], agent_id)
                self.assert_one_real_message(
                    replaced["agentId"], moment, replaced["productMessage"]["file"]
                )

    def test_held_curator_without_ignore_line_gets_the_moment_in_its_scratch(self) -> None:
        ignore = self.memory / ".gitignore"
        ignore.write_text("# The owner's settings\n", encoding="utf-8")
        commit_all(self.memory, "converted memory without the brief ignore")
        self.grant_direct_execution()
        manager = binding("manager")
        result = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        curator = result["seats"]["curator"]
        self.assertTrue(curator["held"])
        receipt = _read_receipt(_receipt_path(self.config, self.selection("curator")))
        assert receipt is not None
        file = Path(receipt["workspace"]["scratchRoot"]) / "curator-start.md"
        self.assertFalse(file.exists())
        self.runtime.calls.clear()

        opened = self.message(manager, agent_id=curator["agentId"], text="Curate the plan.")

        self.assertTrue(opened["openedHeldSeat"], opened)
        prompt = next(payload for call, payload in self.runtime.calls if call == "agent-create")[
            "prompt"
        ]
        self.assertIn(file.as_posix(), prompt)
        self.assertLess(prompt.index(file.as_posix()), prompt.index("Curate the plan."))
        self.assertIn("Brewing always returns a full cup.", file.read_text(encoding="utf-8"))
        self.assertEqual(ignore.read_text(encoding="utf-8"), "# The owner's settings\n")
        self.assertFalse((self.memory / "briefs").exists())
        self.assertEqual(git(self.memory, "status", "--porcelain"), "")

    def test_direct_grant_unconverted_start_sends_a_notice_without_a_file(self) -> None:
        ignore = self.memory / ".gitignore"
        ignore.unlink()
        (self.memory / LAYOUT_MARKER_PATH).unlink()
        commit_all(self.memory, "unconverted memory without the brief ignore")
        self.grant_direct_execution()

        started = self.start(self.architect, "reviewer")

        self.assertEqual(started["status"], "running", started)
        report = started["productMessage"]
        self.assertEqual((report["state"], report["moment"]), ("delivered", "review-start"))
        self.assertNotIn("file", report)
        receipt = _read_receipt(_receipt_path(self.config, self.selection("reviewer")))
        assert receipt is not None
        self.assertFalse((Path(receipt["workspace"]["scratchRoot"]) / "review-start.md").exists())
        lines = [text for text in self.received(started["agentId"]) if PRODUCT_SENDER_WORDS in text]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("The memory is unconverted, so there is no moment file.", lines[0])
        self.assertFalse(ignore.exists())
        self.assertFalse((self.memory / "briefs").exists())
        self.assertEqual(git(self.memory, "status", "--porcelain"), "")

    def selection(self, role: str) -> RoleSelection:
        return RoleSelection.model_validate(
            {
                "role": role,
                "sprintDocumentRef": SPRINT_REF,
                "masterDocumentRef": MASTER_REF,
                "taskDocumentRef": LEAF_REF,
            }
        )


if __name__ == "__main__":
    unittest.main()
