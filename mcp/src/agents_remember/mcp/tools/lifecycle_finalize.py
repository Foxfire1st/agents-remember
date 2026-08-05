"""Payload builder for the ``lifecycle_finalize_task`` tool."""

from __future__ import annotations

from agents_remember.application.worktree_tools import (
    NO_TASK_DOCS,
    FinalizeTaskDocs,
    lifecycle_finalize_task_tool,
)

from ..config import McpRuntimeConfig
from .base import _tool_payload


def lifecycle_finalize_task_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    docs: FinalizeTaskDocs = NO_TASK_DOCS,
    dry_run: bool = False,
    teardown_providers: bool = True,
) -> dict:
    return _tool_payload(
        "lifecycle_finalize_task",
        lifecycle_finalize_task_tool(
            config,
            contract_path,
            docs=docs,
            dry_run=dry_run,
            teardown_providers=teardown_providers,
        ),
    )
