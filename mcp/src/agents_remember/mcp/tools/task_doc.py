"""Payload builder for the ``task_doc`` authoring tool."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.task_doc_tools import task_doc_tool

from ..config import McpRuntimeConfig
from .base import _tool_payload


def task_doc_payload(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    operation: str,
    task_name: str | None = None,
    contract_path: str | None = None,
    slug: str | None = None,
    fields: dict[str, Any] | None = None,
    step: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    subtask: dict[str, Any] | None = None,
    section: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "task_doc",
        task_doc_tool(
            config,
            repo_id=repo_id,
            operation=operation,
            task_name=task_name,
            contract_path=contract_path,
            slug=slug,
            fields=fields,
            step=step,
            decision=decision,
            subtask=subtask,
            section=section,
            dry_run=dry_run,
        ),
    )
