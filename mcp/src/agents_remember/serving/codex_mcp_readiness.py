"""Bounded Codex MCP-tool readiness for structurally spawned role seats."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from agents_remember.errors import CodexAppServerError
from agents_remember.serving.codex_app_server_protocol import (
    CodexAppServerTransport,
    JsonObject,
)

MCP_TOOL_READINESS_TIMEOUT_SECONDS = 30.0
MCP_TOOL_READINESS_POLL_SECONDS = 0.2
MCP_STATUS_PAGE_LIMIT = 32
REQUIRED_ROLE_MCP_TOOL = "dispatch_agent"

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class CodexMcpToolReadiness:
    """Exact connected server/tool evidence that admitted one role seat."""

    server_name: str
    runtime_status: str
    tool_name: str
    tool_count: int

    def to_json(self) -> dict[str, object]:
        return {
            "serverName": self.server_name,
            "runtimeStatus": self.runtime_status,
            "toolName": self.tool_name,
            "toolCount": self.tool_count,
        }


@dataclass(frozen=True)
class CodexMcpReadinessTiming:
    """Bounded polling policy, injectable as one deterministic test seam."""

    timeout_seconds: float = MCP_TOOL_READINESS_TIMEOUT_SECONDS
    poll_seconds: float = MCP_TOOL_READINESS_POLL_SECONDS
    clock: Clock = time.monotonic
    sleeper: Sleeper = asyncio.sleep


DEFAULT_MCP_READINESS_TIMING = CodexMcpReadinessTiming()


@dataclass(frozen=True)
class _ServerStatus:
    name: str
    runtime_status: str
    tool_names: tuple[str, ...]

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "runtimeStatus": self.runtime_status,
            "toolNames": list(self.tool_names),
        }


async def wait_for_codex_mcp_tool(
    transport: CodexAppServerTransport,
    *,
    thread_id: str,
    tool_name: str,
    timing: CodexMcpReadinessTiming = DEFAULT_MCP_READINESS_TIMING,
) -> CodexMcpToolReadiness:
    """Wait until one connected configured server advertises ``tool_name``.

    This is a role-seat startup gate, not a caller retry. The app-server thread already exists,
    while its configured MCP children may still be negotiating. A settled inventory without the
    required tool fails immediately; an unsettled inventory is polled only until the explicit
    deadline.
    """

    _validate_readiness_request(thread_id, tool_name, timing)
    deadline = timing.clock() + timing.timeout_seconds
    last: tuple[_ServerStatus, ...] = ()
    while True:
        last = await _read_server_statuses(transport, thread_id=thread_id)
        ready = _ready_server(last, tool_name)
        if ready is not None:
            return CodexMcpToolReadiness(
                server_name=ready.name,
                runtime_status=ready.runtime_status,
                tool_name=tool_name,
                tool_count=len(ready.tool_names),
            )
        if last and all(_server_is_settled(server) for server in last):
            raise _unavailable(tool_name, last, timed_out=False)
        if timing.clock() >= deadline:
            raise _unavailable(tool_name, last, timed_out=True)
        await timing.sleeper(timing.poll_seconds)


def _validate_readiness_request(
    thread_id: str,
    tool_name: str,
    timing: CodexMcpReadinessTiming,
) -> None:
    if not thread_id or thread_id != thread_id.strip():
        raise CodexAppServerError("Codex MCP readiness requires a non-empty thread id")
    if not tool_name or tool_name != tool_name.strip():
        raise CodexAppServerError("Codex MCP readiness requires a non-empty tool name")
    if timing.timeout_seconds <= 0 or timing.poll_seconds < 0:
        raise CodexAppServerError("Codex MCP readiness requires positive bounded timing")


def _ready_server(
    servers: tuple[_ServerStatus, ...],
    tool_name: str,
) -> _ServerStatus | None:
    return next(
        (
            server
            for server in servers
            if server.runtime_status == "connected" and tool_name in server.tool_names
        ),
        None,
    )


async def _read_server_statuses(
    transport: CodexAppServerTransport,
    *,
    thread_id: str,
) -> tuple[_ServerStatus, ...]:
    statuses: list[_ServerStatus] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(MCP_STATUS_PAGE_LIMIT):
        params: JsonObject = {"threadId": thread_id, "detail": "full"}
        if cursor is not None:
            params["cursor"] = cursor
        payload = await transport.request("mcpServerStatus/list", params)
        data = payload.get("data")
        if not isinstance(data, list):
            raise CodexAppServerError("Codex mcpServerStatus/list response requires data array")
        statuses.extend(_server_status(item) for item in data)
        raw_cursor = payload.get("nextCursor")
        if raw_cursor is None:
            return tuple(statuses)
        if not isinstance(raw_cursor, str) or not raw_cursor:
            raise CodexAppServerError("Codex mcpServerStatus/list returned an invalid cursor")
        if raw_cursor in seen:
            raise CodexAppServerError("Codex mcpServerStatus/list repeated a pagination cursor")
        seen.add(raw_cursor)
        cursor = raw_cursor
    raise CodexAppServerError("Codex mcpServerStatus/list exceeded the pagination limit")


def _server_status(raw: object) -> _ServerStatus:
    if not isinstance(raw, Mapping):
        raise CodexAppServerError("Codex MCP server status must be an object")
    name = _required_string(raw, "name", "Codex MCP server status requires a name")
    runtime_status = _required_string(
        raw,
        "runtimeStatus",
        f"Codex MCP server {name!r} requires runtimeStatus",
    )
    tool_names = _tool_names(name, raw.get("tools"))
    return _ServerStatus(name=name, runtime_status=runtime_status, tool_names=tool_names)


def _required_string(raw: Mapping[object, object], key: str, error: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise CodexAppServerError(error)
    return value


def _tool_names(server_name: str, tools: object) -> tuple[str, ...]:
    if tools is not None and not isinstance(tools, Mapping):
        raise CodexAppServerError(
            f"Codex MCP server {server_name!r} tools must be an object or null"
        )
    if isinstance(tools, Mapping) and any(not isinstance(key, str) or not key for key in tools):
        raise CodexAppServerError(
            f"Codex MCP server {server_name!r} tool names must be non-empty strings"
        )
    return tuple(sorted(tools)) if isinstance(tools, Mapping) else ()


def _server_is_settled(server: _ServerStatus) -> bool:
    return server.runtime_status in {
        "connected",
        "failed",
        "cancelled",
        "disabled",
        "authenticationRequired",
    }


def _unavailable(
    tool_name: str,
    servers: tuple[_ServerStatus, ...],
    *,
    timed_out: bool,
) -> CodexAppServerError:
    reason = "timed out waiting for" if timed_out else "settled without"
    inventory = [server.summary() for server in servers]
    return CodexAppServerError(
        f"Codex MCP startup {reason} required role tool {tool_name!r}; servers={inventory!r}"
    )
