"""The internal command that starts a leaf enclosure in a process of its own."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.worktree_tool_requests import StartExecution, TaskIdentity
from agents_remember.cli import leaf_enclosure_start
from agents_remember.cli.__main__ import build_parser
from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig

REQUEST = {
    "repoId": "sandbox-app",
    "taskName": "sbx-text-helpers",
    "worktreeName": "01-slugify-0123456789",
    "leafId": "SBX-M1-L1",
    "parentTask": "sbx-sprint",
}
IDENTITY = TaskIdentity(
    repo_id="sandbox-app",
    task_name="sbx-text-helpers",
    worktree_name="01-slugify-0123456789",
    leaf_id="SBX-M1-L1",
    parent_task="sbx-sprint",
)


class LeafEnclosureStartCommandTests(unittest.TestCase):
    def run_command(self, request: Any, start: Any) -> tuple[int, str, list[str]]:
        """Run the command in this process with the settings and the worktree owner replaced."""

        order: list[str] = []
        args = build_parser().parse_args(
            [leaf_enclosure_start.COMMAND, "--config", "/sandbox/settings.json"]
        )
        self.assertIs(args.func, leaf_enclosure_start.run)

        def load(path: str) -> str:
            order.append(f"settings loaded from {path}")
            return "the loaded settings"

        def start_tool(config: Any, identity: TaskIdentity, **options: Any) -> dict[str, Any]:
            order.append("worktree start")
            self.assertEqual((config, identity), ("the loaded settings", IDENTITY))
            self.assertEqual(options, {"execution": StartExecution(skip_provider_setup=True)})
            print("a line the worktree start prints")
            if isinstance(start, BaseException):
                raise start
            return start

        output = io.StringIO()
        with (
            patch.object(
                leaf_enclosure_start,
                "declare_process_role",
                side_effect=lambda role: order.append(f"role {role}"),
            ),
            patch.object(leaf_enclosure_start, "bind_worktree_services"),
            patch.object(leaf_enclosure_start, "load_config", side_effect=load),
            patch.object(leaf_enclosure_start, "worktree_start_tool", side_effect=start_tool),
            patch.object(leaf_enclosure_start.sys, "stdin", io.StringIO(json.dumps(request))),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            status = args.func(args)
        return status, output.getvalue(), order

    def test_the_command_runs_the_worktree_start_and_answers_with_one_json_object(self) -> None:
        status, output, order = self.run_command(REQUEST, {"ok": True, "state": "started"})
        self.assertEqual((status, json.loads(output)), (0, {"ok": True}))
        # The tool server's role is declared before the settings file is loaded.
        self.assertEqual(
            order,
            ["role mcp", "settings loaded from /sandbox/settings.json", "worktree start"],
        )
        refusals: dict[str, tuple[Any, Any, dict[str, str]]] = {
            "the worktree start refuses": (
                REQUEST,
                {"ok": False, "state": "refused", "summary": "the base branch is missing"},
                {"code": "refused", "message": "the base branch is missing"},
            ),
            "the worktree start raises": (
                REQUEST,
                ConfigError("unknown repository sandbox-app"),
                {"code": "ConfigError", "message": "unknown repository sandbox-app"},
            ),
            "the request is incomplete": (
                {**REQUEST, "leafId": ""},
                {"ok": True},
                {
                    "code": "ValueError",
                    "message": "the request must be a JSON object with repoId, taskName, "
                    "worktreeName, leafId, parentTask as text.",
                },
            ),
        }
        for label, (request, start, error) in refusals.items():
            with self.subTest(label):
                status, output, _order = self.run_command(request, start)
                self.assertEqual((status, json.loads(output)), (1, {"ok": False, "error": error}))

    def test_the_real_child_process_answers_through_the_build_s_own_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = McpRuntimeConfig(
                config_path=root / "settings" / "no-such-settings.json",
                coordination_root=root / "coordination",
                workspace_root=root / "projects",
                transcript_root=root / "coordination" / "logs" / "mcp",
            )

            outcome = leaf_enclosure_start.start_leaf_enclosure_in_child(config, IDENTITY)

            self.assertIs(outcome["ok"], False)
            self.assertEqual(outcome["state"], "ConfigError")
            self.assertIn("MCP settings file does not exist", outcome["summary"])
            self.assertIn(config.config_path.as_posix(), outcome["summary"])
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
