"""Payload builders for the task-domain tools (``task_doc``, ``task_reopen``)."""

from __future__ import annotations

from typing import Any

from agents_remember.application.task_docs.task_doc_tools import (
    DEFAULT_TASK_DOC_CALL,
    NO_EDIT,
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
    task_reopen_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.tools.tool_answer import is_valid_tool_code

from .base import _tool_payload


def _task_doc_refusal_code(error: TaskDocError) -> str:
    if error.code is not None and is_valid_tool_code(error.code):
        return error.code
    prefix, sep, _rest = str(error).partition(":")
    candidate = prefix.strip()
    if sep and "-" in candidate and is_valid_tool_code(candidate):
        return candidate
    return "task-doc-refused"


def task_doc_payload(
    config: McpRuntimeConfig,
    target: TaskDocTarget,
    *,
    operation: str,
    edit: TaskDocEdit = NO_EDIT,
    call: TaskDocCall = DEFAULT_TASK_DOC_CALL,
) -> dict[str, Any]:
    try:
        payload = task_doc_tool(config, target, operation=operation, edit=edit, call=call)
    except TaskDocError as error:
        code = _task_doc_refusal_code(error)
        payload = {
            "ok": False,
            "operation": "task_doc",
            "code": code,
            "status": code,
            "detail": str(error),
            **({"slug": target.slug} if target.slug is not None else {}),
        }
    return _tool_payload("task_doc", payload)


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
