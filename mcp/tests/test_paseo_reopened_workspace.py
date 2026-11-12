"""Archived task workspace recovery through the real bridge and an idempotent fake host."""

from __future__ import annotations

import hashlib
import json
import unittest
from typing import Any

import test_paseo_bridge_agents as bridge_support


def workspace_key(*parts: str) -> str:
    return (
        "ar-task-workspace:v2:"
        + hashlib.sha256(json.dumps(list(parts), separators=(",", ":")).encode()).hexdigest()
    )


class ReopenedWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = bridge_support.AgentCommandScriptTests()
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        self.payload: dict[str, Any] = {
            "cwd": "/tasks/leaf/worktree",
            "masterProject": {"directory": "/tasks/master", "key": "repo/m/task.json", "name": "M"},
            "task": {"key": "repo/m/leaf.json", "title": "Reopened leaf"},
        }
        self.base_key = workspace_key("repo/m/task.json", "repo/m/leaf.json")

    def test_archived_replay_opens_a_successor_without_touching_the_archive(self) -> None:
        archived = {"id": "wks_archived", "archivedAt": "2026-10-15T00:00:00Z"}
        reply = self.host.call(
            "workspace-open", self.payload, workspaceReplays={self.base_key: archived}
        )
        calls = self.host.recorded()
        creates = [row["options"] for row in calls if row["via"] == "workspaces.create"]
        self.assertEqual(len(creates), 2)
        self.assertEqual(creates[0]["idempotencyKey"], self.base_key)
        self.assertNotEqual(creates[1]["idempotencyKey"], self.base_key)
        self.assertEqual(creates[1]["source"], creates[0]["source"])
        self.assertNotEqual(reply["workspace"]["id"], archived["id"])
        self.assertEqual(reply["workspace"]["directory"], self.payload["cwd"])
        self.assertEqual(reply["workspace"]["name"], "Reopened leaf")
        self.assertEqual(reply["preparation"], "created")
        self.assertEqual(
            [row for row in calls if row["via"] == "workspace.setTitle"],
            [
                {
                    "via": "workspace.setTitle",
                    "id": reply["workspace"]["id"],
                    "title": "Reopened leaf",
                }
            ],
        )
        self.assertFalse(any(row["via"] in {"archive", "workspaces.open"} for row in calls))

    def test_completed_replay_hidden_from_live_listing_uses_its_original_identity(self) -> None:
        # The real host replays the creation's OLD live descriptor, but its refresh listing
        # hides the now archived workspace. Completed creation replay validates existence.
        replays = {self.base_key: {"id": "wks_hidden"}}
        successor = workspace_key(self.base_key, "wks_hidden")
        for repeated in (False, True):
            with self.subTest(repeated=repeated):
                reply = self.host.call(
                    "workspace-open",
                    self.payload,
                    workspaceReplays=replays,
                    hiddenWorkspaceKeys=[self.base_key],
                )
                calls = self.host.recorded()
                self.assertEqual(
                    [
                        row["options"]["idempotencyKey"]
                        for row in calls
                        if row["via"] == "workspaces.create"
                    ],
                    [self.base_key, successor],
                )
                self.assertNotEqual(reply["workspace"]["id"], "wks_hidden")
                self.assertEqual(reply["preparation"], "found" if repeated else "created")
                self.assertEqual(
                    [row["id"] for row in calls if row["via"] == "workspace.setTitle"],
                    [reply["workspace"]["id"]],
                )
                replays[successor] = {"id": reply["workspace"]["id"]}

    def test_retry_follows_the_same_keys_through_archiving_and_archived_generations(self) -> None:
        first_key = workspace_key(self.base_key, "wks_archiving")
        live_key = workspace_key(first_key, "wks_archived")
        replays = {
            self.base_key: {"id": "wks_archiving", "archivingAt": "2026-10-15T00:00:00Z"},
            first_key: {"id": "wks_archived", "archivedAt": "2026-10-15T01:00:00Z"},
        }
        first = self.host.call("workspace-open", self.payload, workspaceReplays=replays)
        first_calls = self.host.recorded()
        self.assertEqual(first["preparation"], "created")
        # After a failed agent-create the host replays the previously created live workspace.
        replays[live_key] = {"id": first["workspace"]["id"]}
        repeated = self.host.call("workspace-open", self.payload, workspaceReplays=replays)
        self.assertEqual(first["workspace"], repeated["workspace"])
        self.assertEqual(repeated["preparation"], "found")
        for calls in (first_calls, self.host.recorded()):
            self.assertEqual(
                [
                    row["options"]["idempotencyKey"]
                    for row in calls
                    if row["via"] == "workspaces.create"
                ],
                [self.base_key, first_key, live_key],
            )
            self.assertEqual(
                [row["id"] for row in calls if row["via"] == "workspace.setTitle"],
                [first["workspace"]["id"]],
            )

    def test_archive_traversal_has_a_fixed_bound_and_names_the_task_and_count(self) -> None:
        replays = {}
        key = self.base_key
        for generation in range(9):
            replays[key] = {"id": f"wks_{generation}", "archivedAt": "2026-10-15T00:00:00Z"}
            key = workspace_key(key, f"wks_{generation}")
        refused = self.host.failure("workspace-open", self.payload, workspaceReplays=replays)
        self.assertEqual(refused.code, "paseo_call_failed")
        self.assertIn("repo/m/leaf.json", str(refused))
        self.assertIn("9 archived workspaces", str(refused))
        self.assertEqual(
            len([row for row in self.host.recorded() if row["via"] == "workspaces.create"]), 9
        )
        self.assertFalse(any(row["via"] == "workspace.setTitle" for row in self.host.recorded()))
        # The last allowed successor can still be live, and the traversal stops there.
        del replays[next(reversed(replays))]
        reply = self.host.call("workspace-open", self.payload, workspaceReplays=replays)
        self.assertEqual(reply["preparation"], "created")
        self.assertEqual(
            len([row for row in self.host.recorded() if row["via"] == "workspaces.create"]), 9
        )

    def test_successor_placement_mismatch_stops_without_renaming_or_further_creation(self) -> None:
        successor = workspace_key(self.base_key, "wks_archived")
        for mismatch in (
            {"projectId": "prj_wrong"},
            {"workspaceDirectory": "/wrong"},
            {"id": ""},
        ):
            with self.subTest(mismatch=mismatch):
                replays = {
                    self.base_key: {"id": "wks_archived", "archivedAt": "2026-10-15T00:00:00Z"},
                    successor: mismatch,
                }
                refused = self.host.failure(
                    "workspace-open", self.payload, workspaceReplays=replays
                )
                self.assertEqual(refused.code, "paseo_call_failed")
                calls = self.host.recorded()
                self.assertEqual(
                    len([row for row in calls if row["via"] == "workspaces.create"]), 2
                )
                self.assertFalse(
                    any(row["via"] in {"workspace.setTitle", "workspaces.open"} for row in calls)
                )
        # Even an archived descriptor with wrong placement is not a successor authority.
        refused = self.host.failure(
            "workspace-open",
            self.payload,
            workspaceReplays={
                self.base_key: {"id": "wks_wrong", "projectId": "prj_wrong", "archivedAt": "old"}
            },
        )
        self.assertIn("different placement", str(refused))
        self.assertEqual(
            len([row for row in self.host.recorded() if row["via"] == "workspaces.create"]), 1
        )
