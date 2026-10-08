#!/usr/bin/env python3
"""Call the tool server of the PNT build under test, over stdio, on the sandbox settings.

Run with the build's own interpreter (``<checkout>/mcp/.venv/bin/python``), so the server it
starts is ``agents_remember.mcp`` from that checkout. The request file names the settings file,
the roots the server must report before anything is written, and the calls to make. A call with
an ``unless`` probe is skipped when the probe succeeds, which is what makes a rebuild leave
existing task documents alone.

Prints one JSON document: ``{"ok", "packageRoot", "results": [{"name", "tool", "action",
"ok", "detail"}], "error"}``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

_DETAIL_LIMIT = 600


def _payload(result: Any) -> tuple[bool, Any]:
    """Whether a tool call succeeded, and what it returned."""
    raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload: Any = raw.get("structuredContent")
    if not isinstance(payload, dict):
        text = "\n".join(
            block.get("text", "")
            for block in raw.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"text": text}
    failed = bool(raw.get("isError")) or (isinstance(payload, dict) and payload.get("ok") is False)
    return not failed, payload


def _admission_error(info: Any, expect: dict[str, Any]) -> str | None:
    """Refuse a server whose roots are not exactly the sandbox's."""
    if not isinstance(info, dict):
        return "server_info returned no object"
    for key in ("coordinationRoot", "workspaceRoot"):
        reported = info.get(key)
        if not isinstance(reported, str) or Path(reported).resolve() != Path(expect[key]).resolve():
            return f"the tool server reports {key}={reported!r}, expected {expect[key]!r}"
    if info.get("allowedRepoIds") != expect["allowedRepoIds"]:
        return f"the tool server admits repositories {info.get('allowedRepoIds')!r}"
    if info.get("allowedProviderIds"):
        return f"the tool server has providers configured: {info.get('allowedProviderIds')!r}"
    expected_package = expect.get("packageRoot")
    actual_package = (info.get("servingBuild") or {}).get("packageRoot")
    if expected_package is not None and actual_package != expected_package:
        return f"the tool server package is {actual_package!r}, expected {expected_package!r}"
    return None


async def _run(request: dict[str, Any]) -> dict[str, Any]:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", request["config"]],
        env=dict(os.environ),
        cwd=request["cwd"],
    )
    results: list[dict[str, Any]] = []
    with open(request["serverLog"], "a", encoding="utf-8") as server_log:
        return await _call_all(server, server_log, request, results)


async def _call_all(
    server: StdioServerParameters,
    server_log: Any,
    request: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    async with (
        stdio_client(server, errlog=server_log) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        ok, info = _payload(await session.call_tool("server_info", {}))
        refusal = None if ok else "server_info failed"
        refusal = refusal or _admission_error(info, request["expect"])
        package_root = (info.get("servingBuild") or {}).get("packageRoot") if ok else None
        if refusal is not None:
            return {"ok": False, "packageRoot": package_root, "results": [], "error": refusal}
        for call in request["calls"]:
            probe = call.get("unless")
            if probe is not None:
                present, _existing = _payload(
                    await session.call_tool(probe["tool"], probe["arguments"])
                )
                if present:
                    results.append(
                        {
                            "name": call["name"],
                            "tool": call["tool"],
                            "action": "present",
                            "ok": True,
                        }
                    )
                    continue
            ok, payload = _payload(await session.call_tool(call["tool"], call["arguments"]))
            result = {"name": call["name"], "tool": call["tool"], "action": "called", "ok": ok}
            if call["tool"] == "runtime_install":
                result["payload"] = payload
            if not ok:
                result["detail"] = json.dumps(payload)[:_DETAIL_LIMIT]
            results.append(result)
            if not ok:
                return {
                    "ok": False,
                    "packageRoot": package_root,
                    "results": results,
                    "error": f"{call['name']}: {call['tool']} failed",
                }
    return {"ok": True, "packageRoot": package_root, "results": results, "error": None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="JSON file describing the calls.")
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    try:
        report = asyncio.run(_run(request))
    except Exception as error:  # the report is the contract; a traceback is not
        report = {"ok": False, "packageRoot": None, "results": [], "error": repr(error)}
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
