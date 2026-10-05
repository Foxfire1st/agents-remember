"""Payload builder for the ``read_ar_files`` tool."""

from __future__ import annotations

from typing import Any

from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.application.task_scoped_mcp import task_scoped_mcp_config_for_reader
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskScopedReaderContext

from .base import _tool_payload


def read_ar_files_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    files: list[dict[str, Any]],
    refresh: bool = False,
    *,
    task_context: TaskScopedReaderContext | None = None,
) -> dict[str, Any]:
    scoped_config = task_scoped_mcp_config_for_reader(
        config,
        repository_id=repo_id,
        task_context=task_context,
    )
    return _tool_payload(
        "read_ar_files",
        read_ar_files_tool(scoped_config, repo_id=repo_id, files=files, refresh=refresh),
    )
