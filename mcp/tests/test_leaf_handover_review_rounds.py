"""A bound Reviewer owns ordinary code-review bookkeeping."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
import pytest
from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.mcp.server import create_server
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.ambient import reset_ambient
from agents_remember.worktrees.services import reset_worktree_services
from mcp.shared.memory import create_connected_server_and_client_session
from test_task_doc_review_public import _create
from test_task_document import _config


def test_reviewer_binding_opens_repairs_and_preserves_sealed_round_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    created = _create(config)
    binding = AgentBinding(
        agent_id=str(uuid4()),
        role="reviewer",
        request_id=str(uuid4()),
        report_path=str(tmp_path / "reviewer-report.md"),
        sprint_ref=TaskDocumentRef(repository="agents-remember", path="sprint/task.json"),
        master_ref=TaskDocumentRef(repository="agents-remember", path="review-api/task.json"),
        task_ref=TaskDocumentRef(repository="agents-remember", path="review-api/review_api.json"),
    )
    for name, value in binding.environment().items():
        monkeypatch.setenv(name, value)
    server = create_server(config)
    document_path = Path(created["docPath"])
    markdown_path = document_path.with_suffix(".md")
    calls: list[tuple[str, str]] = []

    async def exercise() -> None:
        with anyio.fail_after(60):
            async with create_connected_server_and_client_session(server._mcp_server) as client:
                info = await client.call_tool("server_info", {})
                assert not info.isError and info.structuredContent is not None
                assert info.structuredContent["toolServer"] == "agents-remember-task"
                assert info.structuredContent["agentBinding"] == binding.as_report()

                async def call(operation: str, review: dict[str, Any]) -> Any:
                    # Every round/result runs under this same Reviewer server environment;
                    # no Manager binding is installed or used between handovers.
                    current = read_agent_binding()
                    assert current is not None and current == binding
                    calls.append((current.role, operation))
                    return await client.call_tool(
                        "task_doc",
                        {
                            "repo_id": "agents-remember",
                            "task_name": "review-api",
                            "slug": "review_api",
                            "operation": operation,
                            "review": review,
                        },
                    )

                async def successful(operation: str, review: dict[str, Any]) -> dict[str, Any]:
                    result = await call(operation, review)
                    assert not result.isError and result.structuredContent is not None, (
                        result.content
                    )
                    return result.structuredContent["reviewState"]

                async def refused(operation: str, review: dict[str, Any], expected: str) -> None:
                    before = (document_path.read_bytes(), markdown_path.read_bytes())
                    result = await call(operation, review)
                    assert result.isError
                    assert expected in "\n".join(
                        getattr(content, "text", "") for content in result.content
                    )
                    assert (document_path.read_bytes(), markdown_path.read_bytes()) == before

                first = await successful("begin_review", {})
                assert (first["round"], first["pending"]) == (1, True)
                baseline = [{"findingId": "R99-F1", "description": "fixture code defect"}]
                blocked = await successful(
                    "record_review", {"verdict": "block", "findings": baseline}
                )
                assert (blocked["pending"], blocked["baselineFindings"]) == (False, baseline)
                second = await successful("begin_review", {})
                assert (
                    second["round"],
                    second["pending"],
                    second["baselineFindings"],
                    second["remainingFindingIds"],
                ) == (2, True, baseline, ["R99-F1"])

                await refused(
                    "record_review",
                    {"verdict": "block", "remainingFindingIds": ["R99-CHANGED"]},
                    "review-remaining-invalid",
                )
                await refused(
                    "record_review",
                    {"verdict": "block", "findings": [{"findingId": "R99-CHANGED"}]},
                    "review-successor-new-findings",
                )
                for expected_round in (2, 3):
                    if expected_round == 3:
                        third = await successful("begin_review", {})
                        assert (third["round"], third["pending"]) == (3, True)
                    result = await successful(
                        "record_review", {"verdict": "block", "remainingFindingIds": ["R99-F1"]}
                    )
                    assert (
                        result["round"],
                        result["pending"],
                        result["baselineFindings"],
                        result["remainingFindingIds"],
                    ) == (expected_round, False, baseline, ["R99-F1"])
                await refused(
                    "begin_review",
                    {},
                    "review-budget-exhausted: review budget exhausted: count=3, limit=3",
                )

    try:
        asyncio.run(exercise())
    finally:
        reset_ambient()
        reset_worktree_services()
    assert calls == [
        ("reviewer", operation)
        for operation in (
            "begin_review",
            "record_review",
            "begin_review",
            "record_review",
            "record_review",
            "record_review",
            "begin_review",
            "record_review",
            "begin_review",
        )
    ]
