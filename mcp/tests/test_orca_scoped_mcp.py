from __future__ import annotations

import json
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
    prepare_codex_projects_mcp,
    prepare_codex_scoped_mcp,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
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
                    "agents_remember.cli.orca_scoped_mcp.mcp_config_from_scope_profile",
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

    def test_projects_override_is_taskless_safe_and_names_the_active_server(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            coordination = root / "coordination"
            repository = root / "agents-remember"
            memory = coordination / "memory-repos" / "ar-agents-remember"
            settings = root / "settings" / "ar.json"
            user_codex = root / "codex-home" / "config.toml"
            group_codex = projects / ".codex" / "config.toml"
            for path in (
                projects,
                coordination,
                repository,
                memory,
                settings.parent,
                user_codex.parent,
                group_codex.parent,
            ):
                path.mkdir(parents=True, exist_ok=True)
            settings.write_text("{}\n", encoding="utf-8")
            user_bytes = "[mcp_servers]\nother = {}\n"
            group_bytes = "# preserved project layer\n"
            user_codex.write_text(user_bytes, encoding="utf-8")
            group_codex.write_text(group_bytes, encoding="utf-8")
            config = McpRuntimeConfig(
                config_path=settings,
                coordination_root=coordination,
                workspace_root=projects,
                transcript_root=coordination / "logs",
                repositories={
                    "agents-remember": RepositoryScope(
                        "agents-remember", repository, memory_root=memory
                    )
                },
            )
            server_identity = {
                "version": "candidate",
                "configPath": settings.as_posix(),
                "coordinationRoot": coordination.as_posix(),
                "workspaceRoot": projects.as_posix(),
                "packageRoot": (root / "candidate" / "agents_remember").as_posix(),
                "sourceDigest": "sha256:candidate",
            }
            with (
                patch(
                    "agents_remember.cli.orca_scoped_mcp.importlib.util.find_spec",
                    return_value=SimpleNamespace(
                        origin=(root / "candidate" / "agents_remember" / "__init__.py").as_posix()
                    ),
                ),
                patch(
                    "agents_remember.cli.orca_scoped_mcp._read_codex_mcp_entry",
                    side_effect=lambda _root, entry, _args: entry,
                ) as read_codex,
                patch(
                    "agents_remember.cli.orca_scoped_mcp._probe_projects_mcp",
                    return_value=(None, server_identity, None),
                ) as probe,
            ):
                prepared = prepare_codex_projects_mcp(
                    config, workspace_root=projects, repository_id=None
                )

            read_root, entry, launch_args = read_codex.call_args.args
            self.assertEqual(read_root, projects)
            self.assertEqual(entry["cwd"], projects.as_posix())
            self.assertEqual(
                entry["args"][:4],
                ["-m", "agents_remember.mcp", "--config", settings.as_posix()],
            )
            self.assertEqual(entry["args"][4], "--scope-profile")
            self.assertEqual(entry["args"][5], prepared.profile_path.as_posix())
            self.assertEqual(launch_args, prepared.launch_args)
            self.assertEqual(
                shlex.split(shlex.join(prepared.launch_args)), list(prepared.launch_args)
            )
            probe.assert_called_once_with(entry, prepared.config, None)
            assert prepared.profile_path is not None
            self.assertTrue(prepared.profile_path.is_file())
            self.assertIsNone(prepared.context_packet)
            self.assertTrue(
                all(scope.contract_path is None for scope in prepared.config.repositories.values())
            )
            self.assertEqual(
                json.loads(prepared.profile_path.read_text(encoding="utf-8"))["repositoryId"],
                None,
            )
            self.assertTrue(prepared.verification["noRepositorySelected"])
            self.assertEqual(prepared.verification["server"]["sourceDigest"], "sha256:candidate")
            self.assertNotIn("availableTools", prepared.verification)
            self.assertEqual(user_codex.read_text(encoding="utf-8"), user_bytes)
            self.assertEqual(group_codex.read_text(encoding="utf-8"), group_bytes)

    def test_projects_override_checks_only_the_selected_repository_context(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            coordination = root / "coordination"
            repository = root / "agents-remember"
            memory = root / "memory"
            settings = root / "settings" / "ar.json"
            for path in (projects, coordination, repository, memory, settings.parent):
                path.mkdir(parents=True, exist_ok=True)
            config = McpRuntimeConfig(
                config_path=settings,
                coordination_root=coordination,
                workspace_root=projects,
                transcript_root=coordination / "logs",
                repositories={
                    "agents-remember": RepositoryScope(
                        "agents-remember", repository, memory_root=memory
                    )
                },
            )
            context = {
                "repo": {
                    "root": repository.resolve().as_posix(),
                    "state": "available",
                    "branch": "project",
                    "head": "a" * 40,
                },
                "paths": {
                    "coordinationRoot": coordination.resolve().as_posix(),
                    "memoryRoot": memory.resolve().as_posix(),
                },
                "worktree": {"state": "inactive"},
            }
            server_identity = {
                "version": "candidate",
                "configPath": settings.resolve().as_posix(),
                "coordinationRoot": coordination.resolve().as_posix(),
                "workspaceRoot": projects.resolve().as_posix(),
                "packageRoot": "/candidate/agents_remember",
                "sourceDigest": "sha256:candidate",
            }
            with (
                patch(
                    "agents_remember.cli.orca_scoped_mcp.importlib.util.find_spec",
                    return_value=SimpleNamespace(origin="/candidate/agents_remember/__init__.py"),
                ),
                patch(
                    "agents_remember.cli.orca_scoped_mcp._read_codex_mcp_entry",
                    side_effect=lambda _root, entry, _args: entry,
                ),
                patch(
                    "agents_remember.cli.orca_scoped_mcp._probe_projects_mcp",
                    return_value=(context, server_identity, {"sourceMatchesCodeRoot": True}),
                ) as probe,
            ):
                prepared = prepare_codex_projects_mcp(
                    config, workspace_root=projects, repository_id="agents-remember"
                )

            probe.assert_called_once()
            scoped_config = probe.call_args.args[1]
            self.assertEqual(probe.call_args.args[2:], ("agents-remember",))
            self.assertEqual(scoped_config.repositories["agents-remember"].path, repository)
            self.assertEqual(scoped_config.repositories["agents-remember"].memory_root, memory)
            self.assertIsNone(scoped_config.repositories["agents-remember"].contract_path)
            self.assertEqual(prepared.context_packet, context)
            self.assertEqual(prepared.verification["selectedRepository"], "agents-remember")
            self.assertEqual(prepared.verification["codeRoot"], repository.resolve().as_posix())
            self.assertEqual(prepared.verification["memoryRoot"], memory.resolve().as_posix())
            self.assertTrue(prepared.verification["repositoryContextVerified"])
            self.assertFalse(prepared.verification["noRepositorySelected"])


if __name__ == "__main__":
    unittest.main()
