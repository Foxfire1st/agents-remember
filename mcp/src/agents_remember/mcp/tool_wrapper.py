"""Per-tool registration wrapper for stale-build checks, sync offload, and compact results.

Wraps each callable passed to ``AgentsRememberMCP.add_tool`` in an async
function that receives already-validated keyword arguments from ``MCPServer``,
enforces the stale-build publication refusal, offloads synchronous handlers
through ``thread_workers`` under the server's write lock when required, and
returns a compact ``CallToolResult``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, InputRequiredResult, ToolAnnotations
from pydantic import BaseModel

from agents_remember.application.tool_build_currentness import current_tool_build_currentness

from .build_staleness import is_stale_build_write, stale_build_refusal
from .thread_workers import run_in_detached_request_thread, run_in_request_thread
from .tool_results import (
    StructuredOutputSpec,
    build_call_tool_result,
    build_early_refusal_result,
)


@dataclass(frozen=True, slots=True)
class RegisteredToolPolicy:
    """Registration-time execution policy bound to one wrapped tool callable."""

    tool_name: str
    annotations: ToolAnnotations | None
    output_spec: StructuredOutputSpec | None
    acquire_write_lock: Callable[[str, dict[str, Any]], asyncio.Lock]
    on_write_lock_cancelled: Callable[[str, dict[str, Any]], None]


def is_async_callable(fn: Callable[..., Any]) -> bool:
    """Whether ``fn`` is an async function or callable whose ``__call__`` is async."""
    if inspect.iscoroutinefunction(fn):
        return True
    return callable(fn) and inspect.iscoroutinefunction(type(fn).__call__)


def admitted_arguments(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Normalize validated keyword arguments for staleness and write-lock classification."""
    admitted: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, Context):
            continue
        admitted[key] = value.model_dump() if isinstance(value, BaseModel) else value
    return admitted


def nonwriting_call(
    tool_name: str, annotations: ToolAnnotations | None, arguments: dict[str, Any]
) -> bool:
    """Only advertised reads and explicitly non-writing modes bypass write ordering."""
    if annotations is not None and annotations.read_only_hint is True:
        return True
    if tool_name == "context_packet":
        return arguments.get("include_freshness", False) is False
    if tool_name == "memory_quality_check":
        request = arguments.get("request")
        return isinstance(request, dict) and request.get("mode") in ("start", "poll")
    if tool_name == "citation_fix":
        return arguments.get("mode") in ("start", "poll") or arguments.get("dry_run") is True
    return False


def wrap_registered_tool(
    fn: Callable[..., Any], policy: RegisteredToolPolicy
) -> Callable[..., Any]:
    """Wrap one tool callable for public ``MCPServer.add_tool`` registration."""
    fn_is_async = is_async_callable(fn)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> CallToolResult | InputRequiredResult:
        admitted = admitted_arguments(kwargs)
        if (reading := current_tool_build_currentness()) is not None and is_stale_build_write(
            policy.tool_name, admitted
        ):
            refused = await run_in_detached_request_thread(
                stale_build_refusal, policy.tool_name, reading
            )
            return build_early_refusal_result(refused)
        if fn_is_async:
            raw = await fn(*args, **kwargs)
        else:
            raw = await _run_sync_call(policy, admitted, partial(fn, *args, **kwargs))
        return build_call_tool_result(policy.output_spec, raw)

    return wrapper


async def _run_sync_call(
    policy: RegisteredToolPolicy,
    admitted: dict[str, Any],
    invoke: Callable[[], Any],
) -> Any:
    if nonwriting_call(policy.tool_name, policy.annotations, admitted):
        return await run_in_request_thread(invoke)
    lock = policy.acquire_write_lock(policy.tool_name, admitted)
    try:
        await lock.acquire()
    except asyncio.CancelledError:
        policy.on_write_lock_cancelled(policy.tool_name, admitted)
        raise
    try:
        return await run_in_request_thread(invoke)
    finally:
        lock.release()


__all__ = [
    "RegisteredToolPolicy",
    "admitted_arguments",
    "is_async_callable",
    "nonwriting_call",
    "wrap_registered_tool",
]
