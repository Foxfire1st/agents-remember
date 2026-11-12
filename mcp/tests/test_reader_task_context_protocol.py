from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from unittest import mock

import anyio

from mcp import types

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.server import create_server
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.services import reset_worktree_services
from mcp.shared.memory import create_connected_server_and_client_session
from test_worktree_support import open_external_contract_fixture

PROTOCOL_WAIT_SECONDS = 60


class RegisteredTaskReaderContextProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        declare_test_process()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.contract = open_external_contract_fixture(self.root)
        assert self.contract.parent_contract_path is not None
        self.repo_id = self.contract.repo_name
        slug = self.contract.leaf_id.lower().replace("_", "-")
        self.task_ref = TaskDocumentRef(
            repository=self.repo_id,
            path=f"{self.contract.task_root.name}/{slug}.json",
        )
        write_task_doc(
            self.contract.task_root,
            TaskDocument.model_validate(
                {
                    "id": self.contract.leaf_id,
                    "slug": slug,
                    "title": "Reader protocol leaf",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": self.repo_id,
                    "createdAt": "2026-09-28T10:00:00+00:00",
                    "master": "task.md",
                    "seriesContractPath": cast(Path, self.contract.parent_contract_path).as_posix(),
                    "enclosures": [
                        {
                            "leafId": self.contract.leaf_id,
                            "enclosurePath": self.contract.contract_path.as_posix(),
                        }
                    ],
                }
            ),
        )
        self.task_context = {
            "task_document_ref": self.task_ref.model_dump(mode="json"),
            "contract_path": self.contract.contract_path.resolve().as_posix(),
        }

        self.settings = self.root / "settings" / "mcp.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.contract.coordination_root.as_posix(),
                    "workspaceRoot": self.root.as_posix(),
                    "repositories": {self.repo_id: {}},
                    "providers": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.config = load_config(self.settings)
        # The product factory pairs the process-wide request union with its server dispatcher.
        # A bare FastMCP drops tools/list after another test installs the extended union.
        self.server = create_server(self.config)

    def tearDown(self) -> None:
        reset_worktree_services()
        self.temporary.cleanup()

    async def _assert_naming_is_call_local(self, client: Any, marker_name: str) -> None:
        """The naming reads the call's own code root and carries no text."""

        base_args: dict[str, object] = {
            "repo_id": self.repo_id,
            "files": [marker_name],
        }
        leaf_args = {
            **base_args,
            "task_context": self.task_context,
        }

        async def read(arguments: dict[str, object]) -> dict[str, Any]:
            result = await client.call_tool("read_ar_files", arguments)
            return _structured(result)

        base_naming = await read(base_args)
        leaf_naming = await read(leaf_args)
        for naming in (base_naming, leaf_naming):
            self.assertEqual(naming["state"], "named", naming)
            for body_field in ("source", "content", "text", "body"):
                self.assertNotIn(f'"{body_field}":', json.dumps(naming), naming)
        self.assertEqual([entry["path"] for entry in base_naming["files"]], [marker_name])
        self.assertFalse(base_naming["files"][0]["exists"], base_naming)
        self.assertTrue(leaf_naming["files"][0]["exists"], leaf_naming)
        # The same call twice names the same: the server keeps no
        # per-session state about what it sent.
        self.assertEqual((await read(base_args))["files"], base_naming["files"])
        self.assertEqual((await read(leaf_args))["files"], leaf_naming["files"])
        concurrent = await asyncio.gather(read(base_args), read(leaf_args))
        self.assertFalse(concurrent[0]["files"][0]["exists"])
        self.assertTrue(concurrent[1]["files"][0]["exists"])
        # The removed object-form entry is refused by name, not read.
        removed = await client.call_tool(
            "read_ar_files",
            {
                "repo_id": self.repo_id,
                "files": [{"path": marker_name, "onboarding": False}],
                "task_context": self.task_context,
            },
        )
        removed_body = _structured(removed)
        self.assertEqual(
            (removed_body["state"], removed_body["refusalCode"]),
            ("refused", "argument-removed"),
            removed_body,
        )
        self.assertEqual(
            removed_body["refusalDetail"],
            "read the file with your own tools; this tool names the cards",
            removed_body,
        )

    def test_registered_readers_use_call_local_admitted_leaf_context(self) -> None:
        assert (
            self.contract.memory_worktree is not None
            and self.contract.parent_contract_path is not None
        )
        marker_name = ".__ar_reader_scope_protocol.txt"
        base_code = self.config.repositories[self.repo_id].path
        leaf_code = self.contract.code_worktree
        marker_contents = {
            "base": "BASE_PROTOCOL_ROOT\n",
            "leaf": "LEAF_PROTOCOL_ROOT\n",
        }
        markers = {"base": base_code / marker_name, "leaf": leaf_code / marker_name}
        status_before = {
            "base": _git_status(base_code),
            "leaf": _git_status(leaf_code),
        }
        self.assertEqual(status_before, {"base": "", "leaf": ""})
        self.assertTrue(all(not marker.exists() for marker in markers.values()))
        created: list[Path] = []

        async def exercise_protocol() -> None:
            async with create_connected_server_and_client_session(
                self.server._mcp_server
            ) as client:
                tools = (await client.list_tools()).tools
                by_name = {tool.name: tool for tool in tools}
                for tool_name in ("context_packet", "read_ar_files"):
                    schema = by_name[tool_name].inputSchema
                    self.assertEqual(
                        (
                            "task_context" in schema["properties"],
                            "task_context" in schema.get("required", []),
                        ),
                        (True, False),
                    )
                    task_schema = _resolve_schema(
                        schema,
                        schema["properties"]["task_context"],
                    )
                    self.assertEqual(
                        (
                            set(task_schema["required"]),
                            set(task_schema["properties"]),
                        ),
                        (
                            {"task_document_ref", "contract_path"},
                            {"task_document_ref", "contract_path"},
                        ),
                    )

                base_packet_result = await client.call_tool(
                    "context_packet",
                    {"repo_id": self.repo_id},
                )
                leaf_packet_result = await client.call_tool(
                    "context_packet",
                    {
                        "repo_id": self.repo_id,
                        "task_context": self.task_context,
                    },
                )
                base_packet = _structured(base_packet_result)
                leaf_packet = _structured(leaf_packet_result)
                self.assertEqual(base_packet["roots"]["code"], base_code.as_posix())
                self.assertEqual(leaf_packet["roots"]["code"], leaf_code.as_posix())
                # Rule 4 compacts the slim roots code-relative when shorter:
                # join a relative root with its absolute code anchor first.
                leaf_memory = leaf_packet["roots"]["memory"]
                leaf_memory_path = Path(leaf_memory)
                if not leaf_memory_path.is_absolute():
                    leaf_memory_path = Path(
                        os.path.normpath(os.path.join(leaf_packet["roots"]["code"], leaf_memory))
                    )
                self.assertEqual(
                    leaf_memory_path.as_posix(),
                    cast(Path, self.contract.memory_worktree).as_posix(),
                )
                self.assertNotEqual(base_packet["roots"]["memory"], leaf_packet["roots"]["memory"])

                # One marker only in the leaf checkout: existence itself
                # proves which code root each call read.
                leaf_marker = markers["leaf"]
                with leaf_marker.open("x", encoding="utf-8") as stream:
                    created.append(leaf_marker)
                    stream.write(marker_contents["leaf"])

                await self._assert_naming_is_call_local(client, marker_name)

                wrong_repo = await client.call_tool(
                    "context_packet",
                    {
                        "repo_id": "different-repository",
                        "task_context": self.task_context,
                    },
                )
                self.assertTrue(
                    wrong_repo.isError and "repo_id must match" in _tool_error_text(wrong_repo)
                )

                wrong_contract_context = {
                    **self.task_context,
                    "contract_path": cast(Path, self.contract.parent_contract_path).as_posix(),
                }
                wrong_contract = await client.call_tool(
                    "read_ar_files",
                    {
                        "repo_id": self.repo_id,
                        "files": [marker_name],
                        "task_context": wrong_contract_context,
                    },
                )
                self.assertTrue(
                    wrong_contract.isError
                    and "canonical enclosure" in _tool_error_text(wrong_contract)
                )

                missing_pair = await client.call_tool(
                    "context_packet",
                    {
                        "repo_id": self.repo_id,
                        "task_context": {
                            "task_document_ref": self.task_ref.model_dump(mode="json")
                        },
                    },
                )
                self.assertTrue(
                    missing_pair.isError and "contract_path" in _tool_error_text(missing_pair)
                )

        try:
            # The in-memory protocol session exercises registered handlers without launching the
            # stdio server, the host runtime, or any Git operation.
            _run_protocol(exercise_protocol)
        finally:
            for marker in created:
                marker.unlink(missing_ok=True)

        self.assertEqual(
            {"base": _git_status(base_code), "leaf": _git_status(leaf_code)},
            status_before,
        )

    def test_late_tool_list_response_times_out_and_closes_the_handler(self) -> None:
        handler_closed: list[bool] = []

        async def late_tools(request):
            try:
                await anyio.sleep_forever()
            finally:
                handler_closed.append(True)

        async def exercise_late_response() -> None:
            async with create_connected_server_and_client_session(
                self.server._mcp_server
            ) as client:

                async def list_tools() -> None:
                    await client.list_tools()

                # Start the short response deadline after session initialization; Git status
                # and startup keep their normal limits.
                with self.assertRaisesRegex(
                    AssertionError, "MCP reader protocol responses did not arrive"
                ):
                    await _await_protocol(list_tools, wait_seconds=3)

        with mock.patch.dict(
            self.server._mcp_server.request_handlers,
            {types.ListToolsRequest: late_tools},
        ):
            _run_protocol(exercise_late_response)
        self.assertEqual(handler_closed, [True])
        self.test_registered_readers_use_call_local_admitted_leaf_context()


def _run_protocol(exercise: Callable[[], Awaitable[None]]) -> None:
    anyio.run(_await_protocol, exercise)


async def _await_protocol(
    exercise: Callable[[], Awaitable[None]],
    *,
    wait_seconds: float = PROTOCOL_WAIT_SECONDS,
) -> None:
    try:
        with anyio.fail_after(wait_seconds):
            await exercise()
    except TimeoutError as error:
        raise AssertionError(
            "MCP reader protocol responses did not arrive and the session did not "
            f"finish within {wait_seconds} seconds"
        ) from error


def _resolve_schema(root: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definitions = root.get("$defs", {})
        assert isinstance(definitions, dict)
        resolved = definitions.get(reference.removeprefix("#/$defs/"))
        assert isinstance(resolved, dict)
        return resolved
    alternatives = schema.get("anyOf")
    assert isinstance(alternatives, list)
    for alternative in alternatives:
        if isinstance(alternative, dict) and alternative.get("$ref"):
            return _resolve_schema(root, alternative)
    raise AssertionError(f"No object schema reference found: {schema!r}")


def _structured(result: types.CallToolResult) -> dict[str, Any]:
    if result.isError:
        raise AssertionError(_tool_error_text(result))
    assert result.structuredContent is not None
    return result.structuredContent


def _tool_error_text(result) -> str:
    return "\n".join(
        block.text for block in result.content if isinstance(getattr(block, "text", None), str)
    )


def _git_status(root: Path) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            root.as_posix(),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=PROTOCOL_WAIT_SECONDS,
    )
    return result.stdout


if __name__ == "__main__":
    unittest.main()
