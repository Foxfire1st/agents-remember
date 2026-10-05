from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.registration.core import register_core_tools
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.services import reset_worktree_services
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from test_worktree_support import open_external_contract_fixture


class RegisteredTaskReaderContextProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        declare_test_process()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.contract = open_external_contract_fixture(self.root)
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
                    "seriesContractPath": self.contract.parent_contract_path.as_posix(),
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
        self.server = FastMCP("task-reader-context-protocol-test")
        register_core_tools(self.server, self.config)
        bind_worktree_services(build_default_worktree_services())

    def tearDown(self) -> None:
        reset_worktree_services()
        self.temporary.cleanup()

    def test_registered_readers_use_call_local_admitted_leaf_context(self) -> None:
        marker_name = ".__ar_reader_scope_protocol.txt"
        base_code = self.config.repositories[self.repo_id].path
        leaf_code = self.contract.code_worktree
        marker_contents = {"base": "BASE_PROTOCOL_ROOT\n", "leaf": "LEAF_PROTOCOL_ROOT\n"}
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
                    {"repo_id": self.repo_id, "include_providers": False},
                )
                leaf_packet_result = await client.call_tool(
                    "context_packet",
                    {
                        "repo_id": self.repo_id,
                        "include_providers": False,
                        "task_context": self.task_context,
                    },
                )
                base_packet = _structured(base_packet_result)
                leaf_packet = _structured(leaf_packet_result)
                self.assertEqual(base_packet["repo"]["root"], base_code.as_posix())
                self.assertEqual(leaf_packet["repo"]["root"], leaf_code.as_posix())
                self.assertEqual(
                    leaf_packet["paths"]["memoryRoot"],
                    self.contract.memory_worktree.as_posix(),
                )
                self.assertNotEqual(
                    base_packet["paths"]["memoryRoot"], leaf_packet["paths"]["memoryRoot"]
                )

                for label, marker in markers.items():
                    with marker.open("x", encoding="utf-8") as stream:
                        created.append(marker)
                        stream.write(marker_contents[label])

                base_args = {
                    "repo_id": self.repo_id,
                    "files": [{"path": marker_name, "onboarding": False}],
                }
                leaf_args = {
                    **base_args,
                    "task_context": self.task_context,
                }

                async def read(arguments: dict[str, object]) -> str:
                    result = await client.call_tool("read_ar_files", arguments)
                    return _structured(result)["files"][0]["source"]

                self.assertEqual(
                    (
                        await read(base_args),
                        await read(leaf_args),
                        await read(base_args),
                        await read(leaf_args),
                    ),
                    (marker_contents["base"], marker_contents["leaf"]) * 2,
                )

                concurrent_base, concurrent_leaf = await asyncio.gather(
                    read(base_args),
                    read(leaf_args),
                )
                self.assertEqual(concurrent_base, marker_contents["base"])
                self.assertEqual(concurrent_leaf, marker_contents["leaf"])

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
                    "contract_path": self.contract.parent_contract_path.as_posix(),
                }
                wrong_contract = await client.call_tool(
                    "read_ar_files",
                    {**base_args, "task_context": wrong_contract_context},
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
            asyncio.run(exercise_protocol())
        finally:
            for marker in created:
                marker.unlink(missing_ok=True)

        self.assertEqual(
            {"base": _git_status(base_code), "leaf": _git_status(leaf_code)},
            status_before,
        )


def _resolve_schema(root: dict[str, object], schema: dict[str, object]) -> dict[str, object]:
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


def _structured(result) -> dict[str, object]:
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
        ["git", "-C", root.as_posix(), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


if __name__ == "__main__":
    unittest.main()
