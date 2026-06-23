"""Payload builder for the ``lifecycle_finalize_task`` tool."""

from __future__ import annotations

from agents_remember.controllers.worktree_tools import lifecycle_finalize_task_tool

from ..config import McpRuntimeConfig
from .base import _tool_payload


def lifecycle_finalize_task_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    task_doc_path: str | None = None,
    master_doc_path: str | None = None,
    subtask_number: str = "",
    dry_run: bool = False,
    teardown_providers: bool = True,
) -> dict:
    return _tool_payload(
        "lifecycle_finalize_task",
        lifecycle_finalize_task_tool(
            config,
            contract_path=contract_path,
            task_doc_path=task_doc_path,
            master_doc_path=master_doc_path,
            subtask_number=subtask_number,
            dry_run=dry_run,
            teardown_providers=teardown_providers,
        ),
    )
