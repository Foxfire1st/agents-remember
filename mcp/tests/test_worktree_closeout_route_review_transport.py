"""Public closeout transport keeps route-review refusals actionable."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.application import worktree_tools
from agents_remember.application.lifecycle.certification_refusal import (
    certification_admission_refusal,
)
from agents_remember.errors import CertificationContractError
from agents_remember.mcp.registration.closeout import register_closeout_tools
from agents_remember.observer.ambient import (
    AmbientLifecycle,
    AmbientTiming,
    install_ambient,
    reset_ambient,
)
from agents_remember.observer.store import EventStore
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.route_review import RouteReviewError
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from test_closeout_queue import MASTER_A, QueueFixture, git

from mcp import ClientSession, StdioServerParameters


async def _call_registered_closeout_preview(
    settings_path: Path,
    contract_path: str,
) -> CallToolResult:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", settings_path.as_posix()],
        env=dict(os.environ),
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await asyncio.wait_for(session.initialize(), timeout=60)
        result = await asyncio.wait_for(
            session.call_tool(
                "worktree_closeout_preview",
                {
                    "contract_path": contract_path,
                    "code_commit_message": "Add feature",
                },
            ),
            timeout=60,
        )
    return result


def test_registered_closeout_preview_preserves_route_review_reason_and_retry(
    tmp_path: Path,
) -> None:
    fixture = QueueFixture(tmp_path, memory_mode="internal")
    contract = fixture.contracts[MASTER_A]
    task_doc_path = contract.task_root / "leaf-a.json"
    document = read_task_doc(task_doc_path)
    write_task_doc(contract.task_root, document.model_copy(update={"routeReview": None}))
    before_task_doc = task_doc_path.read_bytes()
    before_contract = contract.contract_path.read_bytes()
    before_code = git(contract.code_worktree, "rev-parse", "HEAD")

    result = asyncio.run(
        _call_registered_closeout_preview(
            fixture.config_path,
            contract.contract_path.as_posix(),
        )
    )
    assert result.isError is False
    assert result.structuredContent is not None
    payload = result.structuredContent
    assert isinstance(payload, dict)
    text_blocks = [block for block in result.content if isinstance(block, TextContent)]
    assert text_blocks
    assert json.loads(text_blocks[0].text) == payload

    assert payload["ok"] is False
    assert payload["status"] == "route-review-required"
    assert payload["detail"] == "the current code change has no independent route-review record"
    assert payload["expected"] == {"routeReview": "current passing review bound to this candidate"}
    assert payload["observed"] == {"routeReviewStatus": "route-review-required"}
    assert payload["nextAction"] == "record_route_review"
    assert payload["nextTool"] == "task_doc"
    assert payload["nextArgs"] == {
        "repo_id": "repo-a",
        "operation": "record_route_review",
        "contract_path": contract.contract_path.as_posix(),
    }
    assert "review" not in payload["nextArgs"]
    assert payload["nextStep"] == {
        "summary": "Record or refresh the route review for the exact current candidate, then retry closeout.",
        "nextOperation": "record_route_review",
        "nextTool": "task_doc",
        "nextArgs": payload["nextArgs"],
        "nextRequiredArgs": ["review"],
    }
    assert task_doc_path.read_bytes() == before_task_doc
    assert contract.contract_path.read_bytes() == before_contract
    assert git(contract.code_worktree, "rev-parse", "HEAD") == before_code


def test_certification_admission_refusal_keeps_route_review_status() -> None:
    error = CertificationContractError(
        "selected certification route-review authority refused",
        (
            {
                "code": "route-review-required",
                "path": "routeReview",
                "detail": "the current code change has no independent route-review record",
            },
        ),
    )

    payload = certification_admission_refusal("worktree_closeout_apply", error)

    assert payload["status"] == "route-review-required"
    assert payload["detail"] == "the current code change has no independent route-review record"
    assert payload["nextAction"] == "record_route_review"
    next_step = cast("dict[str, object]", payload["nextStep"])
    assert next_step["nextOperation"] == "record_route_review"
    assert "nextTool" not in next_step


def test_closeout_apply_admission_catches_route_review_with_contract_guidance(
    tmp_path: Path,
) -> None:
    fixture = QueueFixture(tmp_path, memory_mode="internal")
    contract = fixture.contracts[MASTER_A]
    fixture.declare(MASTER_A)
    contract = fixture.contracts[MASTER_A]
    error = RouteReviewError(
        "route-review-stale",
        "the code candidate changed after independent route review; rerun route review",
    )

    server = FastMCP("route-review-apply-test")
    register_closeout_tools(server, fixture.cfg)
    reset_ambient()
    ambient = AmbientLifecycle(
        EventStore(tmp_path / "observer"),
        timing=AmbientTiming(heartbeat_seconds=3600),
    )
    ambient.start(fleeting=True)
    install_ambient(ambient)
    try:
        with mock.patch.object(
            worktree_tools, "start_or_observe_closeout_operation", side_effect=error
        ):
            result = asyncio.run(
                server.call_tool(
                    "worktree_closeout_apply",
                    {
                        "contract_path": contract.contract_path.as_posix(),
                        "intent_note": "developer approved exact closeout",
                        "code_commit_message": "Add feature",
                    },
                )
            )
    finally:
        reset_ambient()
    assert isinstance(result, tuple)
    _content, payload = result
    assert isinstance(payload, dict)

    assert payload["ok"] is False
    assert payload["status"] == "route-review-stale"
    assert payload["contractPath"] == contract.contract_path.as_posix()
    assert payload["nextArgs"]["operation"] == "record_route_review"
    assert payload["nextArgs"]["repo_id"] == "repo-a"
    assert "review" not in payload["nextArgs"]
    assert payload["nextStep"]["nextTool"] == "task_doc"
