"""Runtime evidence reports native observations without borrowing the worker's PATH."""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters import stdio_probe
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.adapters.stdio_probe import probe_tool_server
from agent_evals.artifacts import json_digest
from agent_evals.runtime_evidence import collect_runtime_evidence
from agents_remember.errors import AuthorityError
from mcp.types import CallToolResult, ListToolsResult, Tool

from mcp import ClientSession


def _command_capture(command):
    event = NativeEvent(
        "tool_call",
        "s",
        7,
        {"call_id": "c", "name": "terminal", "arguments": {"command": command}},
        "retained",
        "a" * 64,
    )
    return NativeCapture("hermes", "s", (event,), (), (), ())


@pytest.mark.parametrize(
    ("command", "state"),
    [
        (["/fixture/bin/python", "-m", "check"], "explicit"),
        (["check", "--json"], "derived"),
        ("check | cat", "unresolved"),
        (["/bin/sh", "-c", "check"], "unresolved"),
        ("PATH=/other check", "unresolved"),
        (["./check"], "unresolved"),
        ("check; check", "unresolved"),
    ],
)
def test_only_recorded_trial_path_is_used(command, state, monkeypatch):
    monkeypatch.setenv("PATH", "/worker/never-use")
    snapshot = {"path": "/fixture/bin", "executables": {"check": {"path": "/fixture/bin/check"}}}
    snapshot["sha256"] = json_digest(snapshot)
    result = collect_runtime_evidence(_command_capture(command), path_snapshot=snapshot)
    actual = result["commands"][0]["resolution"]
    assert actual["state"] == state
    if state == "derived":
        assert actual["path"] == "/fixture/bin/check"
        assert actual["pathSnapshotSha256"] == snapshot["sha256"]
    if state == "unresolved":
        assert result["unresolved"][0]["observationId"] == "event-7"
        assert actual["reason"]
        assert actual["argv0"]


def test_snapshot_tampering_cannot_become_derived_resolution():
    snapshot = {
        "path": "/fixture/bin",
        "executables": {"check": {"path": "/fixture/bin/check"}},
        "sha256": "wrong",
    }
    result = collect_runtime_evidence(_command_capture(["check"]), path_snapshot=snapshot)
    assert result["commands"][0]["resolution"] == {
        "argv0": "check",
        "state": "unresolved",
        "reason": "recorded trial PATH snapshot digest differs or is absent",
    }


def test_unparseable_shell_keeps_command_and_explicitly_unknown_argv0():
    result = collect_runtime_evidence(_command_capture("python 'unterminated"))
    command = result["commands"][0]
    assert command["command"] == "python 'unterminated"
    assert command["resolution"]["argv0"] is None
    assert command["resolution"]["state"] == "unresolved"


def test_missing_command_shape_is_reported_instead_of_silently_skipped():
    result = collect_runtime_evidence(_command_capture(None))
    assert len(result["commands"]) == 1
    assert result["commands"][0]["command"] is None
    assert result["unresolved"][0]["observationId"] == "event-7"


def test_probe_collects_inventory_in_the_identity_session():
    calls = []
    info = {"servingBuild": {"sourceDigest": "sha256:fixture"}}

    @asynccontextmanager
    async def streams(_):
        yield ("read", "write")

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            calls.append("initialize")

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return CallToolResult(content=[], structuredContent=info)

        async def list_tools(self, *, cursor):
            calls.append(("list_tools", cursor))
            return ListToolsResult(
                tools=[
                    Tool(
                        name="first" if cursor is None else "second", inputSchema={"type": "object"}
                    )
                ],
                nextCursor="page-2" if cursor is None else None,
            )

    with (
        patch("agent_evals.adapters.stdio_probe.stdio_client", streams),
        patch("agent_evals.adapters.stdio_probe.ClientSession", return_value=Session()) as factory,
    ):
        result = probe_tool_server({"command": "/fixture/python", "args": [], "env": {}})
    assert factory.call_count == 1
    assert calls == [
        "initialize",
        ("list_tools", None),
        ("list_tools", "page-2"),
        ("server_info", {}),
    ]
    inventory = result["evaluationToolInventory"]
    assert inventory["sameInitializedSession"] and inventory["complete"]
    assert inventory["serverInfoSha256"] == json_digest(info)
    assert [tool["name"] for tool in inventory["tools"]] == ["first", "second"]
    assert len(inventory["pages"]) == 2


def test_probe_rejects_repeating_inventory_cursor():
    @asynccontextmanager
    async def streams(_):
        yield (None, None)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            pass

        async def call_tool(self, *_args):
            return CallToolResult(content=[], structuredContent={"servingBuild": {}})

        async def list_tools(self, **_kwargs):
            return ListToolsResult(tools=[], nextCursor="same")

    with (
        patch("agent_evals.adapters.stdio_probe.stdio_client", streams),
        patch("agent_evals.adapters.stdio_probe.ClientSession", return_value=Session()),
        pytest.raises(AuthorityError, match="repeats a pagination cursor"),
    ):
        probe_tool_server({"command": "/fixture/python", "args": [], "env": {}})


@pytest.mark.parametrize("valid", [True, False])
def test_probe_real_sdk_schema_cache_avoids_duplicate_discovery_and_still_validates(
    monkeypatch, valid
):
    """Run installed SDK call/list methods against synthetic, model-free wire answers."""
    sdk = ClientSession(cast(Any, None), cast(Any, None))
    calls = []

    async def send_request(request, result_type, **_):
        if result_type is ListToolsResult:
            cursor = request.root.params.cursor if request.root.params else None
            calls.append(("list_tools", cursor))
            return ListToolsResult(
                tools=[
                    Tool(name="other", inputSchema={})
                    if cursor is None
                    else Tool(
                        name="server_info",
                        inputSchema={},
                        outputSchema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
                    )
                ],
                nextCursor="second" if cursor is None else None,
            )
        assert result_type is CallToolResult
        calls.append(("server_info", None))
        return CallToolResult(content=[], structuredContent={"ok": True if valid else "wrong"})

    async def initialize():
        pass

    @asynccontextmanager
    async def session(*_):
        yield sdk

    @asynccontextmanager
    async def streams(_):
        yield (None, None)

    monkeypatch.setattr(sdk, "send_request", send_request)
    monkeypatch.setattr(sdk, "initialize", initialize)
    monkeypatch.setattr(stdio_probe, "ClientSession", session)
    monkeypatch.setattr(stdio_probe, "stdio_client", streams)
    if valid:
        result = probe_tool_server({"command": "/fixture/python", "args": [], "env": {}})
        assert result["evaluationToolInventory"]["sameInitializedSession"]
    else:
        with pytest.raises(RuntimeError, match="Invalid structured content"):
            probe_tool_server({"command": "/fixture/python", "args": [], "env": {}})
    assert calls == [("list_tools", None), ("list_tools", "second"), ("server_info", None)]


def _timed_probe_boundary(monkeypatch, blocked=None, *, delay=0.0):
    """Model-free transport/session boundary; no child server is launched."""
    calls = []

    async def step(name):
        calls.append(name)
        blocked_steps = blocked if isinstance(blocked, tuple) else (blocked,)
        await asyncio.sleep(1 if name in blocked_steps else delay)

    @asynccontextmanager
    async def streams(_):
        await step("stdio start")
        try:
            yield (None, None)
        finally:
            await step("stdio close")

    class Session:
        async def __aenter__(self):
            await step("session start")
            return self

        async def __aexit__(self, *_):
            await step("session close")

        async def initialize(self):
            await step("initialize")

        async def call_tool(self, *_):
            await step("server_info")
            return CallToolResult(content=[], structuredContent={"servingBuild": {}})

        async def list_tools(self, *, cursor):
            await step(f"list_tools page {1 if cursor is None else 2}")
            return ListToolsResult(tools=[], nextCursor="second" if cursor is None else None)

    monkeypatch.setattr(stdio_probe, "stdio_client", streams)
    monkeypatch.setattr(stdio_probe, "ClientSession", lambda *_: Session())
    monkeypatch.setattr(stdio_probe, "PROBE_STEP_TIMEOUT_SECONDS", 0.03, raising=False)
    original_timeout = asyncio.timeout
    monkeypatch.setattr(
        stdio_probe.asyncio,
        "timeout",
        lambda seconds: original_timeout(0.03 if seconds == 30 else seconds),
    )
    return calls


@pytest.mark.parametrize(
    ("blocked", "reported"),
    [
        ("stdio start", "stdio start"),
        ("session start", "session start"),
        ("initialize", "initialize"),
        ("server_info", "server_info"),
        ("list_tools page 1", "list_tools page 1"),
        ("list_tools page 2", "list_tools page 2"),
        ("session close", "close"),
        ("stdio close", "close"),
    ],
)
def test_probe_timeout_names_exact_step_limit_and_elapsed(monkeypatch, blocked, reported):
    _timed_probe_boundary(monkeypatch, blocked)
    with pytest.raises(TimeoutError) as raised:
        probe_tool_server({"command": "/fixture/python", "args": [], "env": {}})
    assert f"fixture MCP {reported}" in str(raised.value)
    assert "limitSeconds=0.03" in str(raised.value)
    assert "elapsedSeconds=" in str(raised.value)
    assert isinstance(raised.value.__cause__, TimeoutError)


def test_probe_step_budget_is_not_shared_with_previous_steps(monkeypatch):
    calls = _timed_probe_boundary(monkeypatch, delay=0.008)
    # Each observed boundary is below 30ms; their total exceeds the old whole-probe budget.
    result = probe_tool_server({"command": "/fixture/python", "args": [], "env": {}})
    assert result["evaluationToolInventory"]["complete"]
    assert len(calls) == 8


def test_probe_close_timeout_preserves_original_timeout_in_outward_cause(monkeypatch):
    _timed_probe_boundary(monkeypatch, ("initialize", "stdio close"))
    with pytest.raises(TimeoutError) as raised:
        probe_tool_server({"command": "/fixture/python", "args": [], "env": {}})
    assert "fixture MCP initialize" in str(raised.value)
    assert "fixture MCP close" in str(raised.value)
    assert str(raised.value).count("limitSeconds=0.03") == 2
    assert "fixture MCP initialize" in str(raised.value.__cause__)


def test_seventeen_concurrent_model_free_probes_keep_complete_inventory(monkeypatch):
    calls = _timed_probe_boundary(monkeypatch, delay=0.001)

    async def batch():
        return await asyncio.gather(
            *(
                stdio_probe._probe({"command": "/fixture/python", "args": [], "env": {}})
                for _ in range(17)
            )
        )

    results = asyncio.run(batch())
    assert len(results) == 17
    assert all(result["evaluationToolInventory"]["complete"] for result in results)
    assert calls.count("initialize") == calls.count("server_info") == 17
    assert calls.count("session close") == calls.count("stdio close") == 17


def test_real_ce09_commands_and_schema_observations_are_retained():
    fixture = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/ce09-runtime-real-records.json.gz").read_bytes()
        )
    )
    events = tuple(
        NativeEvent(
            row["normalized"]["kind"],
            fixture["attempt"],
            int(row["jsonPointer"].rsplit("/", 1)[1]),
            row["normalized"]["payload"],
            fixture["source"],
            fixture["sourceSha256"],
        )
        for row in fixture["events"]
    )
    capture = NativeCapture(
        "hermes",
        fixture["attempt"],
        events,
        (SourceFile(fixture["source"], fixture["sourceSha256"]),),
        (),
        (),
    )
    evidence = collect_runtime_evidence(capture)
    command = next(row for row in evidence["commands"] if row["observationId"] == "event-234")
    assert "agents-remember knowledge-worklist" in command["command"]
    assert command["resolution"]["state"] == "unresolved"
    assert command["source"]["sha256"] == fixture["sourceSha256"]
    assert any(
        "/home/test/.local/bin/agents-remember" in row["output"]
        for row in evidence["resolutionObservations"]
    )
    assert len(evidence["actorToolSchemas"]) == 2
    assert all(not row["completeInventory"] for row in evidence["actorToolSchemas"])
    assert any("unresolved" in item.lower() for item in evidence["proofLimits"])
