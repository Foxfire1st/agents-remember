"""Installed stale builds refuse publication while retaining useful reads and previews."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from agents_remember.application.runtime.install import RuntimeInstallRequest, run_runtime_install
from agents_remember.application.task_docs.task_doc_tools import VALID_OPERATIONS
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_build_currentness import tool_build_currentness
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.mcp.build_staleness import is_stale_build_write
from agents_remember.mcp.server import AgentsRememberMCP, _complete_fastmcp_settings
from agents_remember.mcp.tools.core import runtime_install_payload
from agents_remember.models.build_currentness import BuildCurrentnessPayload
from agents_remember.models.tokens import count_response_tokens
from agents_remember.serving.daemon import DaemonState, EnsureResult
from mcp.shared.memory import create_connected_server_and_client_session
from test_tool_answer_rows_quality import _scoped_pair

_STALE = BuildCurrentnessPayload(
    mode="installed",
    state="stale",
    loadedDigest="a" * 64,
    installedDigest="b" * 64,
    firstSeenAt="2026-11-05T00:00:00Z",
    remedy="Restart or reconnect this MCP server.",
)


def _answer(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple):
        return result[1]
    return json.loads(result[0].text)


class MCPBuildStalenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_refusal_is_structured_over_the_mcp_transport(self) -> None:
        with TemporaryDirectory() as directory:
            _, config, _ = _scoped_pair(Path(directory))
            _complete_fastmcp_settings()
            server = AgentsRememberMCP("stale-wire-test")
            server._agents_remember_config = config
            entered: list[bool] = []

            @server.tool(name="worktree_closeout_apply")
            def writer() -> dict[str, Any]:
                entered.append(True)
                return {"ok": True}

            with (
                patch("agents_remember.mcp.server.build_currentness", return_value=_STALE),
                patch("agents_remember.mcp.server.server_startup.start_after_discovery"),
            ):
                async with create_connected_server_and_client_session(server._mcp_server) as client:
                    result = await client.call_tool("worktree_closeout_apply", {})
            self.assertFalse(result.isError)
            self.assertEqual(entered, [])
            assert result.structuredContent is not None
            self.assertEqual(result.structuredContent["code"], "server-build-stale")
            self.assertEqual(result.structuredContent["buildCurrentness"]["state"], "stale")

    async def test_each_publication_refuses_before_its_handler_and_dry_run_answers(self) -> None:
        publications = (
            "worktree_closeout_apply",
            "worktree_integrate",
            "worktree_checkpoint_landing",
            "worktree_record_landing",
            "direct_landing",
            "worktree_sync",
            "lifecycle_finalize_task",
            "citation_note_revision",
            "citation_fix",
            "route_index_refresh",
            "memory_init",
            "memory_baseline_adopt",
            "memory_carryover_apply",
            "task_reopen",
            "knowledge_write",
            "task_doc",
            "curator_coherence",
        )
        with TemporaryDirectory() as directory:
            _, config, _ = _scoped_pair(Path(directory))
            _complete_fastmcp_settings()
            server = AgentsRememberMCP("staleness-test")
            server._agents_remember_config = config
            entered: list[str] = []

            def writer(
                dry_run: bool = False,
                commit: bool = False,
                operation: str = "set_step",
                request: dict[str, str] | None = None,
            ) -> dict[str, Any]:
                entered.append("entered")
                return complete_tool_response("runtime_install", {"ok": True})

            for name in publications:
                server.add_tool(writer, name=name)
            with (
                patch("agents_remember.mcp.server.build_currentness", return_value=_STALE),
                patch("agents_remember.mcp.server.server_startup.start_after_discovery"),
            ):
                for name in publications:
                    with self.subTest(tool=name):
                        args: dict[str, Any] = {}
                        if name == "knowledge_write":
                            args["commit"] = True
                        if name == "curator_coherence":
                            args["request"] = {"action": "publish"}
                        before = len(entered)
                        refused = _answer(await server.call_tool(name, args))
                        self.assertEqual(len(entered), before)
                        self.assertFalse(refused["ok"])
                        self.assertEqual(refused["code"], "server-build-stale")
                        self.assertIn("a" * 64, refused["error"])
                        self.assertIn("b" * 64, refused["error"])
                        self.assertIn("Restart or reconnect", refused["error"])
                        self.assertEqual(refused["buildCurrentness"]["state"], "stale")
                        preview = _answer(await server.call_tool(name, {**args, "dry_run": True}))
                        self.assertTrue(preview["ok"])
                        self.assertEqual(len(entered), before + 1)
                        self.assertEqual(preview["buildCurrentness"]["state"], "stale")

    async def test_reads_messages_planning_and_unknown_tools_are_not_publication(self) -> None:
        answered = (
            ("server_info", {}),
            ("read_ar_files", {}),
            ("role_message", {}),
            ("knowledge_write", {"commit": False}),
            ("curator_coherence", {"request": {"action": "status"}}),
            ("curator_coherence", {"request": {"action": "validate"}}),
            ("curator_coherence", {"request": {"action": "prepare"}}),
            ("citation_fix", {"mode": "poll"}),
            ("not_a_publication", {}),
        )
        for name, arguments in answered:
            with self.subTest(tool=name, args=arguments):
                self.assertFalse(is_stale_build_write(name, arguments))
        with TemporaryDirectory() as directory:
            _, config, _ = _scoped_pair(Path(directory))
            _complete_fastmcp_settings()
            server = AgentsRememberMCP("allowed-test")
            server._agents_remember_config = config

            @server.tool(name="role_message")
            def message() -> dict[str, Any]:
                return complete_tool_response("runtime_install", {"ok": True})

            with patch("agents_remember.mcp.server.server_startup.start_after_discovery"):
                for reading in (
                    _STALE,
                    None,
                    BuildCurrentnessPayload(mode="installed", state="unknown"),
                ):
                    with patch(
                        "agents_remember.mcp.server.build_currentness", return_value=reading
                    ):
                        answer = _answer(await server.call_tool("role_message", {}))
                        self.assertTrue(answer["ok"])
                        self.assertEqual("buildCurrentness" in answer, reading is _STALE)

    def test_task_document_publication_excludes_the_declared_read(self) -> None:
        for operation in VALID_OPERATIONS:
            with self.subTest(operation=operation):
                self.assertEqual(
                    is_stale_build_write("task_doc", {"operation": operation}),
                    operation != "linkage_report",
                )
                self.assertFalse(
                    is_stale_build_write("task_doc", {"operation": operation, "dry_run": True})
                )
        self.assertFalse(is_stale_build_write("task_doc", {"operation": "unknown"}))

    def test_public_preview_keeps_every_host_action_in_its_named_log(self) -> None:
        with TemporaryDirectory() as directory:
            _, config, _ = _scoped_pair(Path(directory))
            actions = [f"write daemon.preview.setting_{number} = false" for number in range(50)]
            full = {"ok": True, "dryRun": True, "host": {"ok": True, "changes": actions}}
            with (
                patch("agents_remember.mcp.tools.core.run_runtime_install", return_value=full),
                tool_call_context("runtime_install", {"dry_run": True}, config=config),
            ):
                answer = runtime_install_payload(config, RuntimeInstallRequest(dry_run=True))
            self.assertNotIn("reportPath", answer)
            retained = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
            self.assertEqual(retained["host"]["changes"], actions)
            self.assertEqual(len(retained["host"]["changes"]), 50)

    def test_stale_annotation_survives_answer_overflow(self) -> None:
        with tool_build_currentness(_STALE):
            answer = complete_tool_response(
                "runtime_install", {"ok": True, "messages": ["x " * 2000]}
            )
        self.assertTrue(answer["overflow"])
        self.assertEqual(answer["buildCurrentness"]["state"], "stale")
        self.assertLessEqual(count_response_tokens(answer), 400)

    def test_runtime_install_previews_dashboard_without_restarting_and_reports_failures(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            _, config, _ = _scoped_pair(Path(directory))
            state = DaemonState(
                pid=123,
                host="localhost",
                port=8765,
                version="test",
                config_path=str(config.config_path),
                log_path="dashboard.log",
                started_at="2026-11-05T00:00:00Z",
                source_digest="a" * 64,
            )
            with (
                patch(
                    "agents_remember.application.runtime.install.install_runtime_from_config",
                    side_effect=lambda *_: {"ok": True},
                ),
                patch(
                    "agents_remember.application.runtime.install.install_host",
                    return_value={"ok": True},
                ),
                patch(
                    "agents_remember.application.runtime.install.server_inventory", return_value=[]
                ),
                patch(
                    "agents_remember.application.runtime.install.plan_running_dashboard",
                    return_value=EnsureResult("would-restart", state, "digest changed"),
                ) as preview,
                patch(
                    "agents_remember.application.runtime.install.ensure_running_dashboard",
                    return_value=EnsureResult("failed", state, "restart failed"),
                ) as ensure,
            ):
                planned = run_runtime_install(config, RuntimeInstallRequest(dry_run=True))
                preview.assert_called_once_with(config)
                ensure.assert_not_called()
                self.assertTrue(planned["ok"])
                self.assertEqual(planned["dashboard"]["action"], "would-restart")
                real = run_runtime_install(config, RuntimeInstallRequest(dry_run=False))
                ensure.assert_called_once_with(config)
                self.assertFalse(real["ok"])
                self.assertEqual(real["dashboard"]["detail"], "restart failed")
                self.assertEqual(real["dashboard"]["pid"], 123)
                self.assertEqual(real["runningServers"], [])
