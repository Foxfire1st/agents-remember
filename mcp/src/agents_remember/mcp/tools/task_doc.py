"""Payload builders for the task-domain tools (``task_doc``, ``task_reopen``)."""

from __future__ import annotations

from typing import Any

from agents_remember.application.task_doc_tools import (
    DEFAULT_TASK_DOC_CALL,
    NO_EDIT,
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
    task_reopen_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .base import _tool_payload


def task_doc_payload(
    config: McpRuntimeConfig,
    target: TaskDocTarget,
    *,
    operation: str,
    edit: TaskDocEdit = NO_EDIT,
    call: TaskDocCall = DEFAULT_TASK_DOC_CALL,
) -> dict[str, Any]:
    return _tool_payload(
        "task_doc",
        task_doc_tool(config, target, operation=operation, edit=edit, call=call),
    )


def task_reopen_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "task_reopen",
        task_reopen_tool(
            config,
            contract_path=contract_path,
            dry_run=dry_run,
        ),
    )
