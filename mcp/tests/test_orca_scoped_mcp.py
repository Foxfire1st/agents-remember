from __future__ import annotations

import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from agents_remember.cli.orca_scoped_mcp import (
    _codex_config_override_args,
    _select_codex_mcp_entry,
    prepare_codex_scoped_mcp,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef


class CodexScopedMcpOverrideTests(unittest.TestCase):
    def test_cli_override_round_trips_as_one_properly_quoted_shell_argument(self) -> None:
        entry = {
            "command": "/opt/python with space/bin/python",
            "args": ["-m", "agents_remember.mcp", "--scope-profile", "/private/leaf profile.json"],
            "env": {"PYTHONPATH": "/candidate/mcp/src"},
            "cwd": "/worktrees/leaf group",
        }
        args = _codex_config_override_args(entry)
        self.assertEqual(args[0], "-c")
        self.assertIn("mcp_servers.agents-remember=", args[1])
        self.assertEqual(shlex.split(shlex.join(args)), list(args))

    def test_effective_override_wins_when_project_config_layer_is_disabled(self) -> None:
        workspace = Path("/tmp/leaf-workspace")
        expected = {
            "command": "/usr/bin/python3",
            "args": ["-m", "agents_remember.mcp", "--scope-profile", "/private/leaf.json"],
            "env": {"PYTHONPATH": "/leaf/mcp/src"},
            "cwd": workspace.as_posix(),
        }
        response = {
            "layers": [
                {
                    "name": {
                        "type": "project",
                        "dotCodexFolder": (workspace / ".codex").as_posix(),
                    },
                    "disabledReason": "project is not trusted",
                    "config": {
                        "mcp_servers": {"agents-remember": {"command": "/unscoped/global/server"}}
                    },
                }
            ],
            "config": {"mcp_servers": {"agents-remember": expected}},
        }
        self.assertEqual(_select_codex_mcp_entry(response, expected), expected)
        response["config"]["mcp_servers"]["agents-remember"] = {
            "command": "/unscoped/global/server"
        }
        with self.assertRaisesRegex(ValueError, "per-launch"):
            _select_codex_mcp_entry(response, expected)

    def test_preparation_never_edits_global_or_project_codex_files(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator = root / "coordination"
            group = root / "leaf-group"
            code = group / "leaf-code"
            memory = group / "leaf-memory"
            contract = (
                coordinator
                / "tasks"
                / "repo"
                / "master"
                / "enclosures"
                / "leaf"
                / "series-contract.md"
            )
            settings = root / "settings" / "ar.json"
            private_bindings = root / "private-bindings"
            global_codex = root / "global-codex" / "config.toml"
            group_codex = group / ".codex" / "config.toml"
            for path in (
                coordinator,
                code,
                memory,
                contract.parent,
                settings.parent,
                global_codex.parent,
                group_codex.parent,
            ):
                path.mkdir(parents=True, exist_ok=True)
            settings.write_text("{}\n", encoding="utf-8")
            original_global = "[mcp_servers]\ncontext7 = { url = 'https://example.invalid' }\n"
            original_group = "# unowned group content\n"
            global_codex.write_text(original_global, encoding="utf-8")
            group_codex.write_text(original_group, encoding="utf-8")
            config = cast(
                McpRuntimeConfig,
                SimpleNamespace(
                    config_path=settings,
                    coordination_root=coordinator,
                    repositories={},
                ),
            )
            task_ref = TaskDocumentRef(repository="repo", path="master/leaf.json")
            workspace = {
                "path": group.as_posix(),
                "contractPath": contract.as_posix(),
                "codeRoot": code.as_posix(),
                "memoryRoot": memory.as_posix(),
            }
            with (
                patch(
                    "agents_remember.cli.orca_scoped_mcp._private_binding_root",
                    return_value=private_bindings,
                ),
                patch(
                    "agents_remember.cli.orca_scoped_mcp.task_scoped_mcp_config_from_profile",
                    return_value=object(),
                ),
                patch(
                    "agents_remember.cli.orca_scoped_mcp._read_codex_mcp_entry",
                    side_effect=lambda _root, entry, _args: entry,
                ) as read_codex,
                patch(
                    "agents_remember.cli.orca_scoped_mcp._probe_scoped_mcp", return_value=({}, {})
                ),
                patch("agents_remember.cli.orca_scoped_mcp._assert_context_packet_roots"),
            ):
                scoped = prepare_codex_scoped_mcp(
                    config, task_document_ref=task_ref, workspace=workspace
                )

            read_root, entry, launch_args = read_codex.call_args.args
            self.assertEqual(read_root, group)
            self.assertEqual(launch_args, _codex_config_override_args(entry))
            self.assertEqual(scoped.launch_args, launch_args)
            self.assertEqual(global_codex.read_text(encoding="utf-8"), original_global)
            self.assertEqual(group_codex.read_text(encoding="utf-8"), original_group)
            self.assertTrue(scoped.profile_path.is_file())


if __name__ == "__main__":
    unittest.main()
