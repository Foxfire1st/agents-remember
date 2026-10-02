"""Repeats, replaced executions and reply checks of the role launch, against the fake bridge."""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import test_paseo_launch as launch
from agents_remember.cli import orca_task_receipts, orca_task_routes
from agents_remember.cli.orca_task_preparation import OrcaHandoverRequest
from agents_remember.cli.orca_task_receipts import _message_binding_projection_reference
from agents_remember.cli.paseo_launch import agent_title
from agents_remember.tasks.document_refs import ResolvedTaskDocument

SCHEMA = "ar-orca-native-execution/v1"


class RepeatTestCase(launch.PaseoLaunchTestCase):
    """The launch fixture, with a capsule that can change and a count of the compilations."""

    def setUp(self) -> None:
        self.capsule = "capsule-digest"
        self.compilations = 0
        super().setUp()

    def compile_handover(self, request: OrcaHandoverRequest) -> dict[str, Any]:
        assert request.request_id is not None
        self.compilations += 1
        prepared = super().compile_handover(request)
        binding = {**prepared["messageBindingProjection"]["binding"], "capsuleDigest": self.capsule}
        prepared["capsuleDigest"] = self.capsule
        prepared["messageBindingProjection"] = {
            "requestId": str(request.request_id),
            "binding": binding,
            **_message_binding_projection_reference(self.config, request.request_id, binding),
        }
        return prepared

    def change_what_a_launch_is_compiled_from(self) -> None:
        """Edit every selected task document, the capsule and the first message."""

        for name in ("sprint", "master", "leaf"):
            resolved: ResolvedTaskDocument = getattr(self, name)
            edited = resolved.document.model_copy(
                update={"title": f"{resolved.document.title}, edited {uuid.uuid4().hex[:6]}"}
            )
            setattr(
                self,
                name,
                ResolvedTaskDocument(ref=resolved.ref, path=resolved.path, document=edited),
            )
        self.capsule = f"capsule-digest-{uuid.uuid4().hex[:6]}"
        self.prompt = f"A first message compiled later ({self.capsule})."

    def binding_files(self) -> set[str]:
        directory = self.config.coordination_root / "notes/reports/paseo-native-executions"
        return {path.stem for path in (directory / "message-bindings").glob("*.json")}


class RepeatAfterChangeTests(RepeatTestCase):
    def test_a_repeat_is_answered_from_its_receipt_whatever_changed_since_the_launch(self) -> None:
        cases = [
            (role, state)
            for role in ("worker", "manager", "architect")
            for state in ("unknown", "starting", "running")
        ]
        for role, state in cases:
            with self.subTest(role=role, state=state):
                request = self.request(role)
                # What the launch stores and sends: the compiled message behind the line that
                # names its artifact. The artifact holds the compiled message alone.
                compiled_at_launch = self.prompt
                first_message = self.first_message(request)
                artifact = Path(self.artifact(request)["path"])
                if state == "unknown":
                    self.runtime.fail("agent-create", "paseo_bridge_timeout", after_effect=True)
                    self.assertEqual(self.dispatch(request)[1]["status"], "unknown")
                elif state == "starting":
                    crash = RuntimeError("the backend process ended here")
                    with (
                        patch.object(orca_task_receipts, "run_launch_call", side_effect=crash),
                        self.assertRaises(RuntimeError),
                    ):
                        self.dispatch(request)
                else:
                    self.assertEqual(self.dispatch(request)[1]["status"], "running")
                saved = self.receipt(request)
                self.assertEqual(saved["status"], state)
                written = artifact.stat()
                compiled = self.compilations
                self.runtime.calls.clear()

                self.change_what_a_launch_is_compiled_from()
                status, public = self.dispatch(request)

                self.assertEqual((status, public["status"]), (200, "running"))
                self.assertEqual(public["execution"]["agentId"], saved["agentId"])
                self.assertEqual(self.compilations, compiled, "a repeat is not compiled again")
                created = [call[1] for call in self.runtime.calls if call[0] == "agent-create"]
                if state == "running":
                    # A launch that is resolved is not repeated; its agent is read once.
                    self.assertEqual(
                        self.runtime.calls, [("agent-state", {"agentId": saved["agentId"]})]
                    )
                else:
                    # The stored call runs again unchanged: the same agent id, the first message
                    # as saved with its artifact line, the saved recovery note and tool server.
                    self.assertEqual(
                        [(call["agentId"], call["prompt"]) for call in created],
                        [(saved["agentId"], first_message)],
                    )
                    stored = saved["replayRequest"]["agent"]
                    self.assertEqual(
                        [{key: call[key] for key in stored} for call in created], [stored]
                    )
                    self.assertIn("agents-remember-task", stored["mcpServers"])
                    self.assertIn(artifact.as_posix(), stored["systemPrompt"])
                # The artifact holds what was compiled at the launch. The repeat neither wrote it
                # again nor compared it with what a compilation would give now.
                self.assertEqual(artifact.read_text(encoding="utf-8"), compiled_at_launch)
                self.assertNotEqual(self.prompt, compiled_at_launch)
                self.assertEqual(
                    (artifact.stat().st_ino, artifact.stat().st_mtime_ns),
                    (written.st_ino, written.st_mtime_ns),
                )
                agents = [
                    agent
                    for agent in self.runtime.agents.values()
                    if agent["labels"]["ar.request-id"] == str(request.request_id)
                ]
                self.assertEqual([agent["id"] for agent in agents], [saved["agentId"]])
                # The rules that protect a request id still hold after the change.
                changed = self.request(role, request.request_id, agentId="codex", effortId="high")
                self.assertIn("already bound to different", str(self.refused(changed).detail))
                other = "system-specialist" if role == "architect" else "reviewer"
                elsewhere = self.refused(self.request(other, request.request_id))
                self.assertIn("different immutable message-binding", str(elsewhere.detail))
                self.close_execution(request, "completed")


class ReplacedExecutionTests(RepeatTestCase):
    def test_a_start_without_a_receipt_archives_the_agent_of_the_newest_archived_receipt(
        self,
    ) -> None:
        first = self.request("worker")
        self.dispatch(first)
        closed = self.close_execution(first, "completed")
        old_agent = closed["execution"]["agentId"]
        path = self.receipt_path(first)
        with self.subTest("a new request that is refused leaves the closed receipt in place"):
            refused = self.refused(self.request("worker", agentId="codex", modelId="gpt-z"))
            self.assertIn("does not offer model", str(refused.detail))
            self.assertEqual(self.receipt(first)["requestId"], str(first.request_id))
            self.assertFalse((path.parent / "history").exists())
        with self.subTest("a launch ended between archiving the old receipt and creating its own"):
            orca_task_receipts._archive_receipt(path, closed)
            self.runtime.calls.clear()
            second = self.request("worker")
            self.assertEqual(self.dispatch(second)[1]["status"], "running")
            self.assertEqual(self.runtime.calls[0], ("agent-archive", {"agentId": old_agent}))
            self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])
        with self.subTest("the newest archived receipt of this selection, and no other, is read"):
            newest = self.close_execution(second, "stopped")
            orca_task_receipts._archive_receipt(path, newest)
            other_selection = {
                "schema": SCHEMA,
                "requestId": str(uuid.uuid4()),
                "role": "reviewer",
                "selection": {**newest["selection"], "role": "reviewer"},
                "createdAt": "9999-01-01T00:00:00+00:00",
                "execution": {"kind": "paseo-agent", "agentId": "a-reviewer-agent"},
            }
            history = path.parent / "history"
            orca_task_receipts._write_receipt(
                history / f"{other_selection['requestId']}.json", other_selection
            )
            (history / "unreadable.json").write_text("{", encoding="utf-8")
            self.runtime.calls.clear()
            third = self.request("worker")
            self.assertEqual(self.dispatch(third)[1]["status"], "running")
            self.assertEqual(
                self.runtime.calls[0],
                ("agent-archive", {"agentId": newest["execution"]["agentId"]}),
            )
        with self.subTest("an agent still to be archived survives a receipt moved to the history"):
            # A launch that lost against this closed receipt moved it; it never ran a call itself.
            rejected = self.receipt(third)
            rejected.update(status="rejected", execution={}, pendingArchiveAgentId=old_agent)
            orca_task_receipts._write_receipt(path, rejected)
            orca_task_receipts._archive_receipt(path, rejected)
            self.runtime.agents[old_agent]["archivedAt"] = None
            self.runtime.calls.clear()
            self.assertEqual(self.dispatch(self.request("worker"))[1]["status"], "running")
            self.assertEqual(self.runtime.calls[0], ("agent-archive", {"agentId": old_agent}))
        with self.subTest("a taskless role and a first start archive nothing"):
            self.runtime.calls.clear()
            self.dispatch(self.request("architect"))
            self.dispatch(self.request("orchestrator"))
            self.assertNotIn("agent-archive", [call[0] for call in self.runtime.calls])

    def test_a_launch_that_loses_takes_its_message_binding_file_back(self) -> None:
        write_binding = orca_task_routes._write_message_binding_projection

        def lose_against(competitor: dict[str, Any], mine: Any) -> Any:
            path = self.receipt_path(mine)

            def another_process_starts_first(*args: Any) -> dict[str, str]:
                self.assertTrue(orca_task_receipts._create_receipt(path, competitor))
                return write_binding(*args)

            return patch.object(
                orca_task_routes,
                "_write_message_binding_projection",
                side_effect=another_process_starts_first,
            )

        def competitor(request_id: uuid.UUID, status: str) -> dict[str, Any]:
            return {
                "schema": SCHEMA,
                "requestId": str(request_id),
                "role": "manager",
                "status": status,
                "execution": {},
            }

        for status in ("starting", "rejected"):
            with self.subTest("another request wins", status=status):
                mine = self.request("manager")
                with lose_against(competitor(uuid.uuid4(), status), mine):
                    self.assertEqual(self.refused(mine).status_code, 409)
                self.assertNotIn(str(mine.request_id), self.binding_files())
                # The artifact the loser wrote is its own, at the path of its own request id; it
                # is left in place (no execution names it, and no other request can reach it).
                own = Path(self.artifact(mine)["path"])
                self.assertIn(str(mine.request_id), own.name)
                self.assertEqual(own.read_text(encoding="utf-8"), self.prompt)
                self.receipt_path(mine).unlink(missing_ok=True)
        with self.subTest("the same request wins in another process: its file stays"):
            mine = self.request("manager")
            theirs = self.request("manager", mine.request_id)
            crash = RuntimeError("the other process is still at work")
            with (
                patch.object(orca_task_receipts, "run_launch_call", side_effect=crash),
                self.assertRaises(RuntimeError),
            ):
                self.dispatch(theirs)
            winner = self.receipt(theirs)
            shared = Path(winner["handoverArtifact"]["canonicalPath"])
            written = shared.stat()
            self.receipt_path(theirs).unlink()
            with lose_against(winner, mine):
                status, public = self.dispatch(mine)
            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertEqual(public["execution"]["agentId"], winner["agentId"])
            self.assertIn(str(mine.request_id), self.binding_files())
            # Both processes compiled the same request, so the loser found the winner's artifact
            # and reused it: the file the winner's receipt names is the one written first.
            self.assertEqual(self.receipt(mine)["handoverArtifact"], winner["handoverArtifact"])
            self.assertEqual(shared.read_text(encoding="utf-8"), self.prompt)
            self.assertEqual(
                (shared.stat().st_ino, shared.stat().st_mtime_ns),
                (written.st_ino, written.st_mtime_ns),
            )


class RuntimeReplyTests(RepeatTestCase):
    def reply_with(self, change: Any) -> None:
        def changed(payload: dict[str, Any]) -> dict[str, Any]:
            created = launch.FakeRuntime._agent_create(self.runtime, payload)
            return change(json.loads(json.dumps(created)))

        self.runtime._agent_create = changed  # type: ignore[method-assign]

    def test_a_reply_that_does_not_name_the_minted_agent_and_the_server_is_no_answer(self) -> None:
        def another_agent(reply: dict[str, Any]) -> dict[str, Any]:
            reply["agent"]["id"] = str(uuid.uuid4())
            return reply

        def no_server(reply: dict[str, Any]) -> dict[str, Any]:
            del reply["serverId"]
            return reply

        for label, change in {"another agent id": another_agent, "no server id": no_server}.items():
            with self.subTest(label):
                self.reply_with(change)
                request = self.request("architect")
                status, public = self.dispatch(request)
                self.assertEqual((status, public["status"]), (202, "unknown"))
                self.assertIn("paseo_bridge_invalid_reply", public["detail"])
                self.assertEqual(self.receipt(request)["execution"], {})

    def test_the_execution_names_the_workspace_the_agent_is_in(self) -> None:
        request = self.request("manager")
        self.runtime.fail("agent-create", "paseo_bridge_timeout", after_effect=True)
        self.dispatch(request)
        agent_id = self.receipt(request)["agentId"]
        # The agent exists in a workspace that is no longer the one the folder opens.
        self.runtime.agents[agent_id]["workspaceId"] = "wks_of_the_first_attempt"

        status, public = self.dispatch(request)

        self.assertEqual((status, public["status"]), (200, "running"))
        self.assertEqual(public["execution"]["workspaceId"], "wks_of_the_first_attempt")
        self.assertNotIn("wks_of_the_first_attempt", self.runtime.workspaces.values())

    def test_the_folder_is_resolved_and_the_title_is_cut_at_the_runtime_s_limit(self) -> None:
        real = self.root / "the-real-projects-folder"
        real.mkdir()
        self.config.workspace_root.symlink_to(real, target_is_directory=True)
        long_id = "SPRINT-" + "X" * 300
        self.sprint = ResolvedTaskDocument(
            ref=self.sprint.ref,
            path=self.sprint.path,
            document=self.sprint.document.model_copy(update={"id": long_id}),
        )

        self.assertEqual(self.dispatch(self.request("orchestrator"))[0], 200)

        opened, created = self.runtime.launch_calls()
        # The runtime keys a workspace by the path text: a link and its target would be two.
        self.assertEqual(opened, ("workspace-open", {"cwd": real.as_posix()}))
        self.assertEqual(len(created[1]["title"]), 200)
        self.assertEqual(created[1]["title"], f"Orchestrator · {long_id}"[:200])
        context = self.context(self.config, self.request("orchestrator"))
        self.assertEqual(agent_title(context), created[1]["title"])

    def test_can_start_follows_whether_the_task_bound_execution_is_open(self) -> None:
        flags = {
            status: orca_task_receipts._public_execution({"role": "worker", "status": status})[
                "canStart"
            ]
            for status in (
                "starting",
                "running",
                "interrupted",
                "unknown",
                "completed",
                "failed",
                "stopped",
                "rejected",
            )
        }
        self.assertEqual(
            flags,
            {
                "starting": False,
                "running": False,
                "interrupted": False,
                "unknown": False,
                "completed": True,
                "failed": True,
                "stopped": True,
                "rejected": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
