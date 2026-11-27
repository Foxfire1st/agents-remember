"""Tests for direct compact ``CallToolResult`` construction and MCP 2.x wire parity.

Replaces the 1.x ``compact_content`` monkeypatch tests: verifies that
``AgentsRememberMCP`` builds ``CallToolResult`` directly in the MCP adapter
layer with compact JSON text blocks, preserves ``structured_content`` and
``outputSchema`` parity (including bare-``dict`` explainer tools with none),
and serves the SEP-2640 skills extension and resources over real JSON-RPC
framing via ``Client(server, mode="legacy")``.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from mcp import Client

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.kernel.primitives.version import SERVER_VERSION
from agents_remember.mcp.registration.skills_extension import (
    SkillsGetParams,
    SkillsGetRequest,
    SkillsGetResult,
    SkillsListParams,
    SkillsListRequest,
    SkillsListResult,
)
from agents_remember.mcp.server import AgentsRememberMCP, create_server
from agents_remember.mcp.tool_results import compact_content_blocks
from agents_remember.models.build_currentness import BuildCurrentnessPayload
from agents_remember.models.skill_resources import SKILL_INDEX_URI, SKILLS_EXTENSION_ID
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.role_tool_scope import ROLE_TOOL_ALLOWLISTS, tools_for_role
from agents_remember.models.tools.tool_schema_compaction import compact_tool_schema
from test_config import settings_payload


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class CompactToolResultWireTests(unittest.TestCase):
    def test_non_json_text_blocks_pass_through(self) -> None:
        [block] = compact_content_blocks("not json")
        assert isinstance(block, TextContent)
        self.assertEqual(block.text, "not json")

    def test_dict_content_blocks_are_compact_utf8_json(self) -> None:
        [block] = compact_content_blocks({"ok": True, "label": "café", "items": [1, 2]})
        assert isinstance(block, TextContent)
        self.assertEqual(block.text, '{"ok":true,"label":"café","items":[1,2]}')
        self.assertNotIn("\n", block.text)

    def test_wire_tool_list_answers_and_extension_match_start_tree_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            _write_json(path, settings_payload(root))
            config = load_config(path)
            server = create_server(config)
            observed = anyio.run(self._exercise_legacy_wire, server)

        # Capabilities and serverInfo match the start tree's full initialize surface.
        self.assertEqual(
            observed["capabilities"],
            {
                "experimental": {},
                "extensions": {SKILLS_EXTENSION_ID: {}},
                "prompts": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
                "tools": {"listChanged": False},
            },
        )
        self.assertEqual(
            observed["serverInfo"],
            {"name": "Agents Remember", "version": SERVER_VERSION},
        )
        # All 44 public tools advertise their <name>DictOutput outputSchema.
        self.assertEqual(len(observed["tools"]), 44)
        for tool in observed["tools"]:
            self.assertEqual(
                tool.get("outputSchema"),
                {
                    "additionalProperties": True,
                    "title": f"{tool['name']}DictOutput",
                    "type": "object",
                },
            )

        # Compact text mirrors structured_content byte-for-byte with no newlines.
        info = observed["server_info"]
        self.assertFalse(info.is_error)
        text_blocks = [b for b in info.content if isinstance(b, TextContent)]
        self.assertTrue(text_blocks, "expected a text content block")
        self.assertNotIn("\n", text_blocks[0].text)
        parsed = json.loads(text_blocks[0].text)
        self.assertIsNotNone(info.structured_content)
        self.assertEqual(parsed, info.structured_content)
        self.assertTrue(parsed["ok"])

        # Removed argument refusal returns compact structured data.
        refused = observed["removed_refresh"]
        self.assertFalse(refused.is_error)
        self.assertIsNotNone(refused.structured_content)
        self.assertEqual(refused.structured_content["refusalCode"], "argument-removed")
        self.assertEqual(json.loads(refused.content[0].text), refused.structured_content)

        # Validation and unknown-tool errors preserve exact error text on the wire.
        bad_args = observed["bad_args"]
        self.assertTrue(bad_args.is_error)
        self.assertIsNone(bad_args.structured_content)
        self.assertIn("Error executing tool read_ar_files:", bad_args.content[0].text)
        self.assertIn("read_ar_filesArguments", bad_args.content[0].text)

        unknown = observed["unknown_tool"]
        self.assertTrue(unknown.is_error)
        self.assertIsNone(unknown.structured_content)
        self.assertEqual(unknown.content[0].text, "Unknown tool: no_such_tool_xyz")

        # Extension methods and capsule resources answer over the same legacy wire session.
        self.assertGreaterEqual(len(observed["skills_list"].skills), 1)
        self.assertEqual(
            observed["skills_get"].skill["uri"],
            observed["skills_list"].skills[0]["uri"],
        )
        self.assertEqual(observed["skills_get_err"]["code"], -32602)
        self.assertIn(SKILL_INDEX_URI, observed["resource_uris"])
        self.assertIn("skills", json.loads(observed["index_text"]))

    def test_explainer_tools_and_early_refusals_preserve_start_tree_wire_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            _write_json(path, settings_payload(root))
            config = load_config(path)
            (config.coordination_root / "tasks" / "agents-remember" / "261125_test").mkdir(
                parents=True
            )
            (config.coordination_root / "explainer" / "agents-remember" / "261125_test").mkdir(
                parents=True
            )
            ws = root / "explainer" / "agents-remember" / "261125_test" / "leaf1"
            ws.mkdir(parents=True)
            binding = {
                "capability": "explainer",
                "agent": "agent-1",
                "request": "req-1",
                "repository": "agents-remember",
                "task": "261125_test",
                "leaf": "leaf1",
                "workspace": str(ws),
                "generation": 1,
            }
            keys = ("AR_EXPLAINER_BINDING", "AR_EXPLAINER_WORKSPACE", "AR_PASEO_AGENT_ID")
            backup = {k: os.environ.get(k) for k in keys}
            os.environ["AR_EXPLAINER_BINDING"] = json.dumps(binding)
            os.environ["AR_EXPLAINER_WORKSPACE"] = str(ws)
            os.environ.pop("AR_PASEO_AGENT_ID", None)
            try:
                server = create_server(config)
                tools, forged_refusal = anyio.run(self._exercise_explainer, server)
                stale = BuildCurrentnessPayload(
                    mode="installed",
                    state="stale",
                    loadedDigest="a" * 64,
                    installedDigest="b" * 64,
                    firstSeenAt="2026-11-05T00:00:00Z",
                    remedy="Restart or reconnect this MCP server.",
                )
                with patch("agents_remember.mcp.server.build_currentness", return_value=stale):
                    stale_refusal = anyio.run(self._exercise_stale_refusal, server)
            finally:
                for k, v in backup.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

        by_name = {tool["name"]: tool for tool in tools}
        for name in ("render", "define_category", "remove_category", "grade"):
            self.assertNotIn("outputSchema", by_name[name])

        # Normal bare-dict tool calls (no outputSchema) emit compact JSON text and no structuredContent.
        normal_bare = anyio.run(self._exercise_bare_dict_tool)
        self.assertFalse(normal_bare.is_error)
        self.assertIsNone(normal_bare.structured_content)
        self.assertEqual(normal_bare.content[0].text, '{"ok":true}')

        # Early-return refusals match the start tree's indented JSON text + structuredContent.
        self.assertFalse(forged_refusal.is_error)
        self.assertIsInstance(forged_refusal.structured_content, dict)
        self.assertEqual(forged_refusal.structured_content["refusal"], "not-this-explainer")
        self.assertEqual(
            forged_refusal.content[0].text,
            json.dumps(forged_refusal.structured_content, indent=2),
        )

        self.assertFalse(stale_refusal.is_error)
        self.assertIsInstance(stale_refusal.structured_content, dict)
        self.assertEqual(stale_refusal.structured_content["code"], "server-build-stale")
        self.assertEqual(
            stale_refusal.content[0].text,
            json.dumps(stale_refusal.structured_content, indent=2),
        )

    @staticmethod
    async def _exercise_legacy_wire(server: Any) -> dict[str, Any]:
        async with Client(server, mode="legacy") as client:
            init = client.session.initialize_result
            assert init is not None
            tools_res = await client.list_tools()
            resources_res = await client.list_resources()
            skills_list = await client.session.send_request(
                SkillsListRequest(params=SkillsListParams()),
                SkillsListResult,
            )
            first_uri = skills_list.skills[0]["uri"]
            skills_get = await client.session.send_request(
                SkillsGetRequest(params=SkillsGetParams(uri=first_uri)),
                SkillsGetResult,
            )
            skills_get_err: dict[str, Any] = {}
            try:
                await client.session.send_request(
                    SkillsGetRequest(params=SkillsGetParams(uri="skill://absent/SKILL.md")),
                    SkillsGetResult,
                )
            except MCPError as exc:
                skills_get_err = {"code": exc.error.code, "message": exc.error.message}
            index_read = await client.read_resource(SKILL_INDEX_URI)
            server_info = await client.call_tool("server_info", {})
            removed_refresh = await client.call_tool(
                "read_ar_files",
                {"repo_id": "demo", "files": ["README.md"], "refresh": True},
            )
            bad_args = await client.call_tool("read_ar_files", {"repo_id": "demo"})
            unknown_tool = await client.call_tool("no_such_tool_xyz", {})
        return {
            "capabilities": init.capabilities.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "serverInfo": init.server_info.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "tools": [
                t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools_res.tools
            ],
            "resource_uris": [str(r.uri) for r in resources_res.resources],
            "index_text": index_read.contents[0].text,  # type: ignore[union-attr]
            "skills_list": skills_list,
            "skills_get": skills_get,
            "skills_get_err": skills_get_err,
            "server_info": server_info,
            "removed_refresh": removed_refresh,
            "bad_args": bad_args,
            "unknown_tool": unknown_tool,
        }

    @staticmethod
    async def _exercise_explainer(server: Any) -> tuple[list[dict[str, Any]], Any]:
        async with Client(server, mode="legacy") as client:
            tools_res = await client.list_tools()
            refused = await client.call_tool(
                "define_category",
                {"name": "c", "scale": [{"grade": "A", "meaning": "good"}], "binding": "#1"},
            )
        return (
            [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools_res.tools],
            refused,
        )

    @staticmethod
    async def _exercise_stale_refusal(server: Any) -> Any:
        async with Client(server, mode="legacy") as client:
            return await client.call_tool(
                "worktree_closeout_apply",
                {"contract_path": "/tmp/c", "intent_note": "stale build wire probe"},
            )

    @staticmethod
    async def _exercise_bare_dict_tool() -> Any:
        server = AgentsRememberMCP("bare-dict-probe")

        @server.tool()
        def bare() -> dict:
            return {"ok": True}

        async with Client(server, mode="legacy") as client:
            return await client.call_tool("bare", {})

    def test_every_roles_wire_tool_list_keeps_exact_bytes_and_unmutated_stored_tools(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            _write_json(path, settings_payload(root))
            config = load_config(path)
            backup = {k: v for k, v in os.environ.items() if k.startswith("AR_")}
            try:
                for key in list(backup):
                    os.environ.pop(key, None)
                unbound_server = create_server(config)
                unbound_wire, base_listed = anyio.run(self._wire_and_base_tools, unbound_server)
                unbound_by_name = {tool["name"]: tool for tool in unbound_wire}
                # Schema compaction shapes the public listing, never the stored tools.
                self.assertTrue(
                    any("title" in t.input_schema for t in base_listed),
                    "base MCPServer.list_tools() should still hold uncompacted stored schemas",
                )
                for listed, raw_base in zip(unbound_wire, base_listed, strict=True):
                    self.assertEqual(
                        listed["inputSchema"], compact_tool_schema(raw_base.input_schema)
                    )
                # Central read-only marking replaces the whole annotation object (no extra hints).
                self.assertEqual(
                    unbound_by_name["server_info"]["annotations"], {"readOnlyHint": True}
                )
                self.assertNotIn(
                    "refresh", unbound_by_name["read_ar_files"]["inputSchema"]["properties"]
                )

                # Every canonical role + alias keeps exact byte-for-byte tool definitions & order.
                ref_shapes = {
                    "architect": (False, False, False),
                    "orchestrator": (True, False, False),
                    "manager": (True, True, False),
                    "worker": (True, True, True),
                    "curator": (True, True, True),
                    "reviewer": (True, True, True),
                    "consolidator": (True, True, True),
                    "investigator": (True, True, False),
                    "bootstrap": (False, False, False),
                }
                for role in (*ROLE_TOOL_ALLOWLISTS, "system-specialist"):
                    for key in list(os.environ):
                        if key.startswith("AR_"):
                            os.environ.pop(key, None)
                    canonical = "investigator" if role == "system-specialist" else role
                    has_sprint, has_master, has_task = ref_shapes[canonical]
                    binding = AgentBinding(
                        agent_id="3b7c1d2e-5f4a-4c6b-9d8e-0a1b2c3d4e5f",
                        role=canonical,
                        request_id="9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a",
                        report_path="/tmp/seat-report.md",
                        sprint_ref=TaskDocumentRef(repository="demo", path="t/task.json")
                        if has_sprint
                        else None,
                        master_ref=TaskDocumentRef(repository="demo", path="task.json")
                        if has_master
                        else None,
                        task_ref=TaskDocumentRef(repository="demo", path="t/leaf.json")
                        if has_task
                        else None,
                    )
                    for key, value in binding.environment().items():
                        os.environ[key] = value
                    os.environ["AR_ROLE"] = role
                    role_wire, _ = anyio.run(self._wire_and_base_tools, create_server(config))
                    expected_slice = [unbound_by_name[name] for name in tools_for_role(canonical)]
                    actual_bytes = json.dumps(
                        role_wire, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                    expected_bytes = json.dumps(
                        expected_slice, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                    self.assertEqual(actual_bytes, expected_bytes, f"wire drift on role {role}")
            finally:
                for key in list(os.environ):
                    if key.startswith("AR_"):
                        os.environ.pop(key, None)
                os.environ.update(backup)

    @staticmethod
    async def _wire_and_base_tools(
        server: AgentsRememberMCP,
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        base_listed = await MCPServer.list_tools(server)
        async with Client(server, mode="legacy") as client:
            listed = await client.list_tools()
        wire = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listed.tools]
        return wire, base_listed

    def test_no_private_mcp_sdk_access_remains_in_repository(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        hits: list[str] = []
        for folder in ("mcp/src", "mcp/test_support", "scripts", "mcp/tests"):
            base = repo_root / folder
            if not base.exists():
                continue
            for py_file in sorted(base.rglob("*.py")):
                rel = py_file.relative_to(repo_root).as_posix()
                hits.extend(_private_mcp_sdk_hits(py_file, rel))
        self.assertEqual(hits, [], f"unexpected private MCP SDK accesses: {hits}")


_PRIVATE_MCP_ATTRS = frozenset(
    {
        "_tool_manager",
        "_lowlevel_server",
        "_mcp_server",
        "_subscriptions",
        "_resource_manager",
        "_prompt_manager",
        "fn_metadata",
        "request_handlers",
        "_convert_to_content",
        "_handle_message",
        "_handle_request",
        "_receive_request_type",
    }
)
_INTERNAL_MCP_PREFIXES = (
    "mcp.server.fastmcp",
    "mcp.server.mcpserver.tools",
    "mcp.server.mcpserver.utilities",
)


def _is_internal_mcp_module(module: str) -> bool:
    if module != "mcp" and not module.startswith("mcp."):
        return False
    return module.startswith(_INTERNAL_MCP_PREFIXES) or any(
        part.startswith("_") for part in module.split(".")
    )


def _private_mcp_sdk_hits(py_file: Path, rel: str) -> list[str]:
    hits: list[str] = []
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _PRIVATE_MCP_ATTRS:
            hits.append(f"{rel}:{node.lineno}: .{node.attr}")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and _is_internal_mcp_module(node.module)
        ):
            hits.append(f"{rel}:{node.lineno}: from {node.module}")
        elif isinstance(node, ast.Import):
            hits.extend(
                f"{rel}:{node.lineno}: import {alias.name}"
                for alias in node.names
                if _is_internal_mcp_module(alias.name)
            )
    return hits


if __name__ == "__main__":
    unittest.main()
