"""Focused regression tests for Wave 1a tool inspection fixes (compact answers, schemas, refusals)."""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from agent_evals.adapters import stdio_probe
from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.knowledge_write_answer import compact_write_answer
from agents_remember.application.landing_answer_shapes import project_landing_answer
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
)
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.application.worktree_tools import CloseoutApproval, CloseoutCommitMessages
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.read_only import (
    UnknownReadOnlyMark,
    read_only_annotations,
    read_only_hint,
)
from agents_remember.mcp.server import AgentsRememberMCP, create_server
from agents_remember.mcp.tool_results import build_early_refusal_result
from agents_remember.mcp.tools.core import server_info_payload
from agents_remember.mcp.tools.knowledge import _integrity_answer
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.mcp.tools.worktree import (
    worktree_checkpoint_landing_payload,
    worktree_closeout_apply_payload,
    worktree_closeout_preview_payload,
    worktree_status_payload,
)
from agents_remember.models.build_currentness import BuildCurrentnessPayload
from agents_remember.models.core import RunningServerPayload, ServingBuildPayload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools import tool_answer as tool_answer_module
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_EXCEPTIONS, bound_for
from agents_remember.tasks import read_task_doc
from agents_remember.worktrees.integration.integration_branch_authority import (
    IntegrationBranchAuthorityError,
)
from agents_remember.worktrees.modules.closeout_lineage import SourceLineageRefusal
from agents_remember.worktrees.modules.landing_commit_check import (
    CheckedTree,
    LandingCommitCheckRefusal,
)
from agents_remember.worktrees.modules.models import CommitPublicationRefusal
from agents_remember.worktrees.modules.start import WorktreeTargetResolutionError
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, ValidationError
from test_closeout_queue import MASTER_A, QueueFixture

from mcp import Client


class _TypedReply(BaseModel):
    value: int


def _config(tmp_path: Path) -> McpRuntimeConfig:
    coord = tmp_path / "coord"
    repo = tmp_path / "repo"
    memory = tmp_path / "memory"
    for directory in (coord, repo, memory):
        directory.mkdir(parents=True, exist_ok=True)
    return McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=coord,
        workspace_root=tmp_path / "workspace",
        transcript_root=coord / "logs" / "mcp",
        repositories={
            "demo": RepositoryScope(repo_id="demo", path=repo, memory_root=memory),
        },
    )


def _wire_json(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is None
    text_blocks = [b for b in result.content if isinstance(b, TextContent)]
    assert len(text_blocks) == 1
    assert "\n" not in text_blocks[0].text
    parsed = json.loads(text_blocks[0].text)
    assert isinstance(parsed, dict)
    return parsed


# ── Rule 1: Compact projections & compact early refusals ───────────────────


def test_server_info_compacts_large_running_servers_and_retains_full_list_in_log(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    servers = [
        RunningServerPayload(
            pid=10000 + index,
            role=("worker", "reviewer", "manager", "curator")[index % 4],
            loadedDigest=f"sha256:{index:064x}",
            startedAt=f"2026-11-28T{index % 24:02d}:{index % 60:02d}:00Z",
            configPath=str(config.config_path),
            packageRoot=str(tmp_path / "pkg"),
            currentness=BuildCurrentnessPayload(
                mode="installed",
                state="stale" if index % 5 == 0 else "current",
            ),
        )
        for index in range(128)
    ]
    serving_build = ServingBuildPayload(
        version="3.1.0",
        commit="22bbb3620d00",
        sourceDigest="sha256:" + "a" * 64,
        bootedAt="2026-11-28T00:00:00Z",
        packageRoot=str(tmp_path / "pkg"),
        pythonExecutable=sys.executable,
    )
    with (
        patch("agents_remember.mcp.tools.core.server_inventory", return_value=servers),
        tool_call_context("server_info", {}, config=config),
    ):
        answer = server_info_payload(config, serving_build)

    assert answer["ok"] is True
    assert answer.get("overflow") is not True
    summary = answer["runningServers"]
    assert isinstance(summary, dict)
    assert summary["count"] == 128
    assert summary["staleCount"] == sum(1 for item in servers if item.currentness.state == "stale")
    assert summary["roles"] == {"worker": 32, "reviewer": 32, "manager": 32, "curator": 32}
    assert len(summary["recent"]) == 5
    assert set(summary["recent"][0]) <= {"pid", "role", "loadedDigest", "startedAt", "state"}
    logged = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
    assert len(logged["runningServers"]) == 128


def test_knowledge_integrity_check_compacts_routes_and_worklist_without_overflow(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    routes = [
        {
            "familyId": f"FAM-{index:06d}",
            "title": f"Family title {index}",
            "status": ("ok", "differs", "routeless")[index % 3],
            "differs": index % 3 == 1,
            "state": {
                "routeUnassigned": index % 5 == 0,
                "routeless": index % 3 == 2,
            },
            "recordedRoutes": [f"pkg/mod_{index}/a.py", f"pkg/mod_{index}/b.py"],
            "derivedRoutes": [f"pkg/mod_{index}/a.py", f"pkg/mod_{index}/c.py"],
            "suggestion": "review routes",
        }
        for index in range(115)
    ]
    worklist_items = [
        {
            "id": f"item-{index:03d}",
            "kind": ("reached_family", "touched_invariant", "onboarding_trace")[index % 3],
            "target": f"INV-{index:06d}",
            "reason": "changed code path " * 8,
        }
        for index in range(50)
    ]
    whole = {
        "ok": True,
        "operation": "knowledge_integrity_check",
        "state": "reported",
        "memoryRoot": str(tmp_path / "memory"),
        "codeRoot": str(tmp_path / "repo"),
        "bases": ["a" * 40],
        "validation": {
            "ok": True,
            "ruleCounts": {},
            "violations": {"count": 0, "items": [], "truncated": False},
        },
        "worklistState": "present",
        "worklist": {
            "path": str(tmp_path / "knowledge-worklist.json"),
            "digest": "b" * 64,
            "itemCount": 50,
            "state": "complete",
            "owner": "261125-DEP-L5",
            "itemsByKind": {
                "reached_family": 17,
                "touched_invariant": 17,
                "onboarding_trace": 16,
            },
            "items": worklist_items,
        },
        "routes": routes,
    }
    with tool_call_context("knowledge_integrity_check", {}, config=config):
        answer = complete_tool_response(
            "knowledge_integrity_check", whole, answer=_integrity_answer(whole)
        )

    assert answer["ok"] is True
    assert answer.get("overflow") is not True
    assert answer["routes"]["familyCount"] == 115
    assert answer["routes"]["differs"] == sum(1 for row in routes if row["differs"])
    assert answer["worklist"]["itemCount"] == 50
    assert answer["worklist"]["owner"] == "261125-DEP-L5"
    assert "items" not in answer["worklist"]
    logged = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
    assert len(logged["routes"]) == 115
    assert len(logged["worklist"]["items"]) == 50


def test_worktree_sync_and_knowledge_write_cap_large_lists_without_overflow(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    long_root = (
        "/home/firefox/projects/ar-coordination/worktrees/agents-remember/"
        "5_very-long-leaf-name-for-deep-root-measurement-20c4ebeb37-ar"
    )
    contract_str = (
        f"{long_root}/tasks/agents-remember/261125_long-master/enclosures/l5/leaf-contract.md"
    )
    sync_whole: dict[str, Any] = {
        "ok": False,
        "operation": "worktree_sync",
        "state": "sync-resolution-required",
        "status": "sync-resolution-required",
        "contract_path": contract_str,
        "phase": "code-resolution-required",
        "summary": "Code sync stopped with merge conflicts in 60 files; resolve and continue.",
        "resolution": {
            "side": "code",
            "owner": "agent",
            "worktree": f"{long_root}/5_very-long-leaf-name-for-deep-root-measurement-20c4ebeb37",
            "files": [
                f"mcp/src/agents_remember/worktrees/integration/module_{index:02d}.py"
                for index in range(60)
            ],
            "wipRestore": False,
        },
        "nextTool": "worktree_sync",
        "nextArgs": {"contract_path": contract_str, "resolution_action": "continue"},
        "cancelArgs": {"contract_path": contract_str, "resolution_action": "cancel"},
    }
    with tool_call_context("worktree_sync", {"contract_path": contract_str}, config=config):
        sync_answer = complete_tool_response(
            "worktree_sync",
            sync_whole,
            answer=project_landing_answer("worktree_sync", sync_whole),
        )
    sync_tokens = count_response_tokens(sync_answer)
    assert sync_answer.get("overflow") is not True
    assert sync_tokens > 400
    assert sync_tokens <= bound_for("worktree_sync") <= 2 * sync_tokens
    assert len(sync_answer["resolution"]["files"]) == 5
    assert sync_answer["resolution"]["fileCount"] == 60
    assert sync_answer["resolution"]["filesTruncated"] is True
    assert sync_answer["nextTool"] == "worktree_sync"
    assert sync_answer["cancelArgs"]["resolution_action"] == "cancel"
    assert len(sync_whole["resolution"]["files"]) == 60
    with (
        patch.dict(tool_answer_module.SHAPED_TOOL_BOUNDS, {("worktree_sync", None): 400}),
        tool_call_context("worktree_sync", {"contract_path": contract_str}, config=config),
    ):
        at_400 = complete_tool_response(
            "worktree_sync",
            sync_whole,
            answer=project_landing_answer("worktree_sync", sync_whole),
        )
    assert at_400.get("overflow") is True
    assert "resolution" not in at_400

    write_whole: dict[str, Any] = {
        "ok": False,
        "operation": "knowledge_write",
        "state": "refused",
        "owner": "261125-DEP-L5",
        "memoryRoot": str(tmp_path / "memory"),
        "codeTree": str(tmp_path / "repo"),
        "counts": {"records": 20},
        "violations": [
            {"rule": f"R{index:02d}", "path": f"knowledge/inv-{index:02d}.json", "detail": "x" * 60}
            for index in range(20)
        ],
        "written": [f"knowledge/invariants/INV-{index:06d}.json" for index in range(20)],
        "removed": [f"knowledge/old/OLD-{index:06d}.json" for index in range(12)],
        "reportPath": str(tmp_path / "write-report.json"),
        "refusalCode": "validation-failed",
        "refusalDetail": "Validation failed with 20 violations.",
    }
    with tool_call_context("knowledge_write", {}, config=config):
        write_answer = complete_tool_response(
            "knowledge_write",
            write_whole,
            answer=compact_write_answer(write_whole),
        )
    assert write_answer.get("overflow") is not True
    assert len(write_answer["violations"]) == 5
    assert write_answer["violationCount"] == 20
    assert write_answer["violationsTruncated"] is True
    assert len(write_answer["written"]) == 5
    assert write_answer["writtenCount"] == 20
    assert write_answer["writtenTruncated"] is True
    assert len(write_answer["removed"]) == 5
    assert write_answer["removedCount"] == 12
    assert write_answer["removedTruncated"] is True
    assert write_answer["reportPath"] == str(tmp_path / "write-report.json")
    assert "operation" not in write_answer


def test_early_refusal_is_compact_single_line_and_knowledge_read_bound_unchanged() -> None:
    result = build_early_refusal_result(
        {"ok": False, "code": "server-build-stale", "label": "café"}
    )
    assert result.is_error is False
    assert result.structured_content is None
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert "\n" not in block.text
    assert block.text == '{"ok":false,"code":"server-build-stale","label":"café"}'
    assert TOOL_ANSWER_EXCEPTIONS[("knowledge_read", None)][0] == 5000


# ── Rule 2: Schemas, structuredContent, annotations, stdio_probe ───────────


def test_typed_basemodel_keeps_structured_output_while_generic_dict_drops_it(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    server = create_server(config)

    custom = AgentsRememberMCP("typed-probe")

    @custom.tool()
    def typed_tool() -> _TypedReply:
        return _TypedReply(value=7)

    async def exercise() -> tuple[list[dict[str, Any]], list[dict[str, Any]], CallToolResult]:
        async with Client(server, mode="legacy") as client:
            public_tools = [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in (await client.list_tools()).tools
            ]
        async with Client(custom, mode="legacy") as client:
            custom_tools = [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in (await client.list_tools()).tools
            ]
            typed_res = await client.call_tool("typed_tool", {})
        return public_tools, custom_tools, typed_res

    public_tools, custom_tools, typed_res = anyio.run(exercise)
    assert len(public_tools) == 44
    assert all("outputSchema" not in tool for tool in public_tools)
    assert "outputSchema" in custom_tools[0]
    assert typed_res.structured_content == {"value": 7}


def test_read_only_annotations_merges_site_hints_and_central_read_only_wins() -> None:
    merged = read_only_annotations(
        "context_packet",
        ToolAnnotations(
            title="Context Packet",
            read_only_hint=True,  # Site mistakenly tries to promote; central False MUST win.
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    assert read_only_hint("context_packet") is False
    assert read_only_hint("knowledge_diff") is False
    assert merged.read_only_hint is False
    assert merged.title == "Context Packet"
    assert merged.destructive_hint is False
    assert merged.idempotent_hint is True
    assert merged.open_world_hint is False

    with pytest.raises(UnknownReadOnlyMark, match="unclassified_wave1a_probe"):
        read_only_annotations("unclassified_wave1a_probe")


def test_stdio_probe_reads_text_content_json_without_structured_content() -> None:
    info = {"ok": True, "servingBuild": {"sourceDigest": "sha256:" + "1" * 64}}

    @asynccontextmanager
    async def streams(_):
        yield (None, None)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            pass

        async def list_tools(self, params=None):
            del params
            return ListToolsResult(
                tools=[Tool(name="server_info", input_schema={"type": "object"})],
                next_cursor=None,
            )

        async def call_tool(self, name, arguments):
            assert (name, arguments) == ("server_info", {})
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(info, separators=(",", ":")))],
                structured_content=None,
            )

    with (
        patch("agent_evals.adapters.stdio_probe.stdio_client", streams),
        patch("agent_evals.adapters.stdio_probe.ClientSession", return_value=Session()),
    ):
        probed = stdio_probe.probe_tool_server(
            {"command": "/fixture/python", "args": [], "env": {}}
        )

    assert probed["ok"] is True
    assert probed["servingBuild"] == info["servingBuild"]
    assert probed["evaluationToolInventory"]["complete"] is True


# ── Rule 3: omitted-slug review guard, task_doc.create injection, MCP refusals ──


def test_omitted_slug_review_refuses_without_touching_master_task_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    server = create_server(config)

    async def exercise() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with Client(server, mode="legacy") as client:
            created_master = _wire_json(
                await client.call_tool(
                    "task_doc",
                    {
                        "repo_id": "demo",
                        "task_name": "261128_review_target",
                        "slug": "task",
                        "operation": "create",
                        "fields": {
                            "id": "M1",
                            "kind": "master",
                            "title": "Target Guard Master",
                            "type": "Master (Code)",
                            "createdAt": "2026-11-28T00:00",
                        },
                    },
                )
            )
            assert created_master["ok"] is True, created_master
            created_leaf = _wire_json(
                await client.call_tool(
                    "task_doc",
                    {
                        "repo_id": "demo",
                        "task_name": "261128_review_target",
                        "slug": "1_leaf",
                        "operation": "create",
                        "fields": {
                            "id": "L1",
                            "kind": "subTask",
                            "title": "Target Guard Leaf",
                            "type": "Sub-Task (Code)",
                            "createdAt": "2026-11-28T00:00",
                        },
                    },
                )
            )
            assert created_leaf["ok"] is True, created_leaf
            master_path = (
                config.coordination_root / "tasks" / "demo" / "261128_review_target" / "task.json"
            )
            before_bytes = master_path.read_bytes()

            begin_refused = _wire_json(
                await client.call_tool(
                    "task_doc",
                    {
                        "repo_id": "demo",
                        "task_name": "261128_review_target",
                        "operation": "begin_review",
                        "review": {},
                    },
                )
            )
            assert master_path.read_bytes() == before_bytes

            record_refused = _wire_json(
                await client.call_tool(
                    "task_doc",
                    {
                        "repo_id": "demo",
                        "task_name": "261128_review_target",
                        "operation": "record_review",
                        "review": {"verdict": "pass", "findings": []},
                    },
                )
            )
            assert master_path.read_bytes() == before_bytes
            return created_master, created_leaf, begin_refused, record_refused

    created_master, created_leaf, begin_refused, record_refused = anyio.run(exercise)
    assert created_master["ok"] is True
    assert created_leaf["ok"] is True
    assert begin_refused["ok"] is False
    assert begin_refused["code"] == "review-target-slug-required"
    assert record_refused["ok"] is False
    assert record_refused["code"] == "review-target-slug-required"

    # Verify task_doc.create injected repo and slug from top-level parameters.
    leaf_json = json.loads(
        (
            config.coordination_root / "tasks" / "demo" / "261128_review_target" / "1_leaf.json"
        ).read_text(encoding="utf-8")
    )
    assert leaf_json["repo"] == "demo"
    assert leaf_json["slug"] == "1_leaf"

    # A leaf-bound reviewer passing slug="task" gets review-target-mismatch with zero writes.
    master_path = config.coordination_root / "tasks" / "demo" / "261128_review_target" / "task.json"
    before_bytes = master_path.read_bytes()
    binding = AgentBinding(
        agent_id="3b7c1d2e-5f4a-4c6b-9d8e-0a1b2c3d4e5f",
        role="reviewer",
        request_id="9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a",
        report_path="/tmp/reviewer.md",
        master_ref=TaskDocumentRef(repository="demo", path="261128_review_target/task.json"),
        task_ref=TaskDocumentRef(repository="demo", path="261128_review_target/1_leaf.json"),
    )
    for key, value in binding.environment().items():
        monkeypatch.setenv(key, value)
    bound_server = create_server(config)

    async def call_mismatch() -> dict[str, Any]:
        async with Client(bound_server, mode="legacy") as client:
            return _wire_json(
                await client.call_tool(
                    "task_doc",
                    {
                        "repo_id": "demo",
                        "task_name": "261128_review_target",
                        "slug": "task",
                        "operation": "begin_review",
                        "review": {},
                    },
                )
            )

    mismatch = anyio.run(call_mismatch)
    assert mismatch["ok"] is False
    assert mismatch["code"] == "review-target-mismatch"
    assert master_path.read_bytes() == before_bytes


def test_atomic_master_record_route_review_without_slug_succeeds_and_preserves_leaf(
    tmp_path: Path,
) -> None:
    fixture = QueueFixture(tmp_path / "atomic", atomic_a=True)
    leaf_contract = fixture.contracts[MASTER_A]
    assert leaf_contract.parent_contract_path is not None
    series_contract = leaf_contract.parent_contract_path
    master_path = leaf_contract.task_root / "task.json"
    leaf_path = leaf_contract.task_root / "leaf-a.json"
    assert master_path.is_file() and leaf_path.is_file()
    before_leaf_bytes = leaf_path.read_bytes()

    report_rel = "notes/reports/master-route-review.md"
    report_path = leaf_contract.task_root / report_rel
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Master Route Review\nPASS\n", encoding="utf-8")

    server = create_server(fixture.cfg)

    async def invoke() -> dict[str, Any]:
        async with Client(server, mode="legacy") as client:
            return _wire_json(
                await client.call_tool(
                    "task_doc",
                    {
                        "repo_id": leaf_contract.repo_name,
                        "task_name": leaf_contract.task_root.name,
                        "contract_path": series_contract.as_posix(),
                        "operation": "record_route_review",
                        "review": {
                            "verdict": "pass",
                            "verdictRef": report_rel,
                            "routes": [
                                {
                                    "route": "master-series",
                                    "verdict": "pass",
                                    "evidenceRef": report_rel,
                                }
                            ],
                            "findings": [],
                        },
                    },
                )
            )

    result = anyio.run(invoke)
    assert result["ok"] is True, result
    recorded_master = read_task_doc(master_path)
    assert recorded_master.routeReview is not None
    assert recorded_master.routeReview.verdict == "pass"
    assert leaf_path.read_bytes() == before_leaf_bytes


def test_task_doc_create_refuses_mismatched_top_level_slug_or_repo(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with tool_call_context("task_doc", {}, config=config):
        master_ok = task_doc_payload(
            config,
            TaskDocTarget(repo_id="demo", task_name="261128_create", slug="task"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "M1",
                    "slug": "series",
                    "title": "Master using task.json stem",
                    "kind": "master",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
                }
            ),
            call=TaskDocCall(),
        )
        subtask_task_slug_refusal = task_doc_payload(
            config,
            TaskDocTarget(repo_id="demo", task_name="261128_create", slug="task"),
            operation="create",
            edit=TaskDocEdit(
                fields={"id": "L1", "slug": "1_a", "title": "Mismatch", "kind": "subTask"}
            ),
            call=TaskDocCall(),
        )
        slug_refusal = task_doc_payload(
            config,
            TaskDocTarget(repo_id="demo", task_name="261128_create", slug="1_a"),
            operation="create",
            edit=TaskDocEdit(fields={"id": "L1", "slug": "1_b", "title": "Mismatch"}),
            call=TaskDocCall(),
        )
        repo_refusal = task_doc_payload(
            config,
            TaskDocTarget(repo_id="demo", task_name="261128_create", slug="1_a"),
            operation="create",
            edit=TaskDocEdit(fields={"id": "L1", "repo": "other", "title": "Mismatch"}),
            call=TaskDocCall(),
        )
    assert master_ok["ok"] is True
    assert (
        read_task_doc(
            config.coordination_root / "tasks" / "demo" / "261128_create" / "task.json"
        ).slug
        == "series"
    )
    assert (
        subtask_task_slug_refusal["ok"] is False
        and subtask_task_slug_refusal["code"] == "task-doc-slug-mismatch"
    )
    assert slug_refusal["ok"] is False and slug_refusal["code"] == "task-doc-slug-mismatch"
    assert repo_refusal["ok"] is False and repo_refusal["code"] == "task-doc-repo-mismatch"


def test_task_doc_and_worktree_status_boundaries_convert_typed_errors(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    with tool_call_context("task_doc", {}, config=config):
        with patch(
            "agents_remember.mcp.tools.task_doc.task_doc_tool",
            side_effect=TaskDocError("cannot proceed", code="custom-task-error"),
        ):
            td = task_doc_payload(
                config,
                TaskDocTarget(repo_id="demo", task_name="t", slug="1_a"),
                operation="set_status",
            )
        assert td["ok"] is False and td["code"] == "custom-task-error"
        for unexpected in (ImportError("missing dep"), RuntimeError("bug")):
            with (
                patch("agents_remember.mcp.tools.task_doc.task_doc_tool", side_effect=unexpected),
                pytest.raises(type(unexpected)),
            ):
                task_doc_payload(
                    config,
                    TaskDocTarget(repo_id="demo", task_name="t", slug="1_a"),
                    operation="set_status",
                )

    with tool_call_context("worktree_status", {}, config=config):
        with patch(
            "agents_remember.mcp.tools.worktree.worktree_status_tool",
            side_effect=WorktreeTargetResolutionError("no target", code="worktree-target-required"),
        ):
            ws = worktree_status_payload(config, TaskRef(repo_id="demo"))
        assert ws["ok"] is False and ws["code"] == "worktree-target-required"

        class _Dummy(BaseModel):
            x: int

        val_err: ValidationError | None = None
        try:
            _Dummy.model_validate({"x": "not-an-int"})
        except ValidationError as exc:
            val_err = exc
        assert val_err is not None
        with patch("agents_remember.mcp.tools.worktree.worktree_status_tool", side_effect=val_err):
            ws_val = worktree_status_payload(config, TaskRef(repo_id="demo"))
        assert ws_val["ok"] is False and ws_val["code"] == "worktree-status-validation-failed"
        for unexpected in (ImportError("missing"), RuntimeError("bug")):
            with (
                patch(
                    "agents_remember.mcp.tools.worktree.worktree_status_tool",
                    side_effect=unexpected,
                ),
                pytest.raises(type(unexpected)),
            ):
                worktree_status_payload(config, TaskRef(repo_id="demo"))


def test_closeout_and_checkpoint_boundaries_convert_typed_errors(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    contract = str(tmp_path / "coord" / "contract.md")
    messages = CloseoutCommitMessages(code="c", memory=None)
    approval = CloseoutApproval(intent_note="ok", dry_run=False)
    lineage_err = SourceLineageRefusal(
        "source-lineage-stale",
        "stale branch",
        {"state": "blocked", "status": "source-lineage-stale", "summary": "stale branch"},
    )
    commit_check_err = LandingCommitCheckRefusal(
        "failed",
        checked=CheckedTree(repo=tmp_path, tree="a" * 40, hook="pre-commit"),
        step="ruff",
        log=tmp_path / "hook.log",
    )
    for tool_name, invoke in (
        (
            "worktree_closeout_preview",
            lambda: worktree_closeout_preview_payload(config, contract, messages),
        ),
        (
            "worktree_closeout_apply",
            lambda: worktree_closeout_apply_payload(config, contract, messages, approval),
        ),
    ):
        target = f"agents_remember.mcp.tools.worktree.{tool_name}_tool"
        with tool_call_context(tool_name, {}, config=config):
            with patch(target, side_effect=lineage_err):
                refused_lineage = invoke()
            assert refused_lineage["ok"] is False
            assert refused_lineage["code"] == "source-lineage-stale"
            assert refused_lineage["nextTool"] == "worktree_sync"
            with patch(target, side_effect=commit_check_err):
                refused_check = invoke()
            assert refused_check["ok"] is False
            assert refused_check["code"] == "commit-check-failed"
            with patch(target, side_effect=CommitPublicationRefusal("branch moved")):
                refused_pub = invoke()
            assert refused_pub["ok"] is False
            assert refused_pub["code"] == "commit-publication-refused"
            for unexpected in (ImportError("missing"), RuntimeError("bug")):
                with patch(target, side_effect=unexpected), pytest.raises(type(unexpected)):
                    invoke()

    with tool_call_context("worktree_checkpoint_landing", {}, config=config):
        with patch(
            "agents_remember.mcp.tools.worktree.worktree_checkpoint_landing_tool",
            side_effect=CloseoutQueueError("queue-blocked", "not ready"),
        ):
            cq = worktree_checkpoint_landing_payload(config, contract)
        assert cq["ok"] is False and cq["code"] == "queue-blocked"
        with patch(
            "agents_remember.mcp.tools.worktree.worktree_checkpoint_landing_tool",
            side_effect=IntegrationBranchAuthorityError(
                "not a series", code="contract-not-a-series"
            ),
        ):
            iba = worktree_checkpoint_landing_payload(config, contract)
        assert iba["ok"] is False and iba["code"] == "contract-not-a-series"
        for unexpected in (ImportError("missing"), RuntimeError("bug")):
            with (
                patch(
                    "agents_remember.mcp.tools.worktree.worktree_checkpoint_landing_tool",
                    side_effect=unexpected,
                ),
                pytest.raises(type(unexpected)),
            ):
                worktree_checkpoint_landing_payload(config, contract)
