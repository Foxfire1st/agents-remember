"""Read identity through exactly the stdio definition the fixture actor receives."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from agents_remember.errors import AuthorityError
from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

from ..artifacts import json_digest

PROBE_STEP_TIMEOUT_SECONDS = 30


@asynccontextmanager
async def _probe_step(name: str) -> AsyncIterator[None]:
    """Each awaited boundary has its own limit; retain the underlying timeout cause."""
    started = time.monotonic()
    try:
        async with asyncio.timeout(PROBE_STEP_TIMEOUT_SECONDS):
            yield
    except TimeoutError as error:
        elapsed = time.monotonic() - started
        raise TimeoutError(
            f"fixture MCP {name}: limitSeconds={PROBE_STEP_TIMEOUT_SECONDS:g}, "
            f"elapsedSeconds={elapsed:.6f}"
        ) from error


def probe_tool_server(definition: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_probe(definition))


async def _probe(definition: dict[str, Any]) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=definition["command"],
        args=definition["args"],
        env=definition["env"],
    )
    stack = AsyncExitStack()
    try:
        async with _probe_step("stdio start"):
            streams = await stack.enter_async_context(stdio_client(parameters))
        async with _probe_step("session start"):
            session = await stack.enter_async_context(ClientSession(*streams))
        async with _probe_step("initialize"):
            await session.initialize()
        tools: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        cursor = None
        seen: set[str] = set()
        while True:
            async with _probe_step(f"list_tools page {len(pages) + 1}"):
                listing = await session.list_tools(cursor=cursor)
            page = listing.model_dump(mode="json", by_alias=True, exclude_none=True)
            tools.extend(page["tools"])
            pages.append({"cursor": cursor, "receiptSha256": json_digest(page)})
            cursor = listing.nextCursor
            if cursor is None:
                break
            if cursor in seen:
                raise AuthorityError("fixture MCP tool inventory repeats a pagination cursor")
            seen.add(cursor)
        names = [tool["name"] for tool in tools]
        if len(set(names)) != len(names):
            raise AuthorityError("fixture MCP tool inventory repeats a tool name")
        # Public list_tools also fills the SDK output-schema cache. Discover once
        # before call_tool, which still performs its normal result validation.
        async with _probe_step("server_info (including SDK result validation)"):
            result = await session.call_tool("server_info", {})
        if result.isError or not isinstance(result.structuredContent, dict):
            raise AuthorityError("fixture tool server did not return structured identity")
        info = result.structuredContent
        return {
            **info,
            "evaluationToolInventory": {
                "schema": "ar-eval-mcp-tool-inventory/v1",
                "serverInfoSha256": json_digest(info),
                "sameInitializedSession": True,
                "complete": True,
                "tools": tools,
                "pages": pages,
            },
        }
    finally:
        original_error = sys.exception()
        try:
            async with _probe_step("close"):
                await stack.aclose()
        except TimeoutError as error:
            if original_error is not None:
                raise TimeoutError(
                    f"{original_error}; cleanup also timed out: {error}"
                ) from original_error
            raise
