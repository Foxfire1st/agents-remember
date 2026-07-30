from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from agents_remember.errors import CodexAppServerError
from agents_remember.serving.codex_app_server_protocol import (
    CODEX_REMOTE_COMPATIBILITY_CEILING_BYTES,
    CodexStdioTransport,
)
from agents_remember.serving.codex_app_server_state import (
    activity_from_thread_status,
    parse_thread_open_response,
)
from agents_remember.serving.harness_control_models import ControlIdentity, LaunchSpec


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def launch(tmp_path: Path, script: str) -> LaunchSpec:
    return LaunchSpec(
        identity=ControlIdentity(
            ar_session_id="transport-test",
            tmux_name="ar-transport-test",
            created_at="2026-07-14T12:00:00+00:00",
        ),
        harness_id="codex",
        cwd=tmp_path,
        argv=(sys.executable, "-u", "-c", script),
    )


@pytest.mark.anyio
async def test_stdio_transport_correlates_responses_and_server_messages(tmp_path: Path) -> None:
    script = r"""
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        print(json.dumps({"id": message["id"], "result": {"ok": True}}), flush=True)
    elif message.get("method") == "ping":
        print(json.dumps({
            "id": "approval-1",
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-1"},
        }), flush=True)
        print(json.dumps({"id": message["id"], "result": {"pong": True}}), flush=True)
    elif "result" in message:
        break
"""
    transport = CodexStdioTransport()
    await transport.start(launch(tmp_path, script))
    messages = transport.messages()
    try:
        assert await transport.request("initialize", {}) == {"ok": True}
        assert await transport.request("ping", {}) == {"pong": True}
        server_request = await anext(messages)
        assert server_request["id"] == "approval-1"
        await transport.respond("approval-1", {"decision": "accept"})
    finally:
        await transport.stop("graceful")


@pytest.mark.anyio
async def test_stdio_transport_fails_malformed_json_loudly(tmp_path: Path) -> None:
    script = 'import sys; print("not-json", flush=True); sys.stdin.read()'
    transport = CodexStdioTransport()
    await transport.start(launch(tmp_path, script))
    messages = transport.messages()
    try:
        with pytest.raises(CodexAppServerError, match="malformed JSONL"):
            await anext(messages)
    finally:
        await transport.stop("forced")


@pytest.mark.anyio
async def test_stdio_transport_fails_oversized_stream_message_loudly(tmp_path: Path) -> None:
    script = 'import sys; print("x" * 128, flush=True); sys.stdin.read()'
    transport = CodexStdioTransport(max_message_bytes=32)
    await transport.start(launch(tmp_path, script))
    messages = transport.messages()
    try:
        with pytest.raises(CodexAppServerError, match="stream limit"):
            await anext(messages)
    finally:
        await transport.stop("forced")


@pytest.mark.anyio
@pytest.mark.parametrize("payload_bytes", [1 << 20, 4 << 20, 4_846_576])
async def test_stdio_transport_accepts_increasing_valid_messages_below_codex_fuse(
    tmp_path: Path,
    payload_bytes: int,
) -> None:
    script = f"""
import json
import sys

request = json.loads(sys.stdin.readline())
prefix = '{{"id":' + str(request["id"]) + ',"result":{{"payload":"'
suffix = '"}}}}'
content_size = {payload_bytes} - len(prefix.encode()) - len(suffix.encode())
sys.stdout.write(prefix + ("x" * content_size) + suffix + "\\n")
sys.stdout.flush()
"""
    transport = CodexStdioTransport()
    await transport.start(launch(tmp_path, script))
    try:
        result = await transport.request("large", {})
        payload = result["payload"]
        assert isinstance(payload, str)
        assert len(payload.encode()) < payload_bytes
    finally:
        await transport.stop("forced")


@pytest.mark.anyio
@pytest.mark.parametrize(("payload_bytes", "accepted"), [(4096, True), (4097, False)])
async def test_stdio_transport_fuse_counts_json_payload_but_not_its_newline(
    tmp_path: Path,
    payload_bytes: int,
    accepted: bool,
) -> None:
    script = f"""
import json
import sys

request = json.loads(sys.stdin.readline())
prefix = '{{"id":' + str(request["id"]) + ',"result":{{"payload":"'
suffix = '"}}}}'
content_size = {payload_bytes} - len(prefix.encode()) - len(suffix.encode())
sys.stdout.write(prefix + ("x" * content_size) + suffix + "\\n")
sys.stdout.flush()
sys.stdin.read()
"""
    transport = CodexStdioTransport(max_message_bytes=4096)
    await transport.start(launch(tmp_path, script))
    try:
        if accepted:
            assert isinstance((await transport.request("boundary", {}))["payload"], str)
        else:
            with pytest.raises(CodexAppServerError, match=r"(stream|byte) limit"):
                await transport.request("boundary", {})
    finally:
        await transport.stop("forced")


def test_default_transport_fuse_matches_codex_remote_compatibility_precedent() -> None:
    assert CODEX_REMOTE_COMPATIBILITY_CEILING_BYTES == 128 << 20


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload_bytes", "accepted"),
    [
        (CODEX_REMOTE_COMPATIBILITY_CEILING_BYTES, True),
        (CODEX_REMOTE_COMPATIBILITY_CEILING_BYTES + 1, False),
    ],
)
async def test_default_fuse_accepts_exactly_128_mib_payload_plus_newline_and_rejects_one_more(
    tmp_path: Path,
    payload_bytes: int,
    accepted: bool,
) -> None:
    script = f"""
import json
import sys

request = json.loads(sys.stdin.readline())
prefix = '{{"id":' + str(request["id"]) + ',"result":{{"payload":"'
suffix = '"}}}}'
content_size = {payload_bytes} - len(prefix.encode()) - len(suffix.encode())
sys.stdout.write(prefix + ("x" * content_size) + suffix + "\\n")
sys.stdout.flush()
sys.stdin.read()
"""
    transport = CodexStdioTransport()
    await transport.start(launch(tmp_path, script))
    try:
        if accepted:
            result = await transport.request("exact-default-boundary", {})
            payload = result["payload"]
            assert isinstance(payload, str)
            assert len(payload) == payload_bytes - len('{"id":1,"result":{"payload":"') - len('"}}')
        else:
            with pytest.raises(CodexAppServerError, match=r"(stream|byte) limit"):
                await transport.request("one-byte-over-default-boundary", {})
    finally:
        await transport.stop("forced")


@pytest.mark.anyio
async def test_above_fuse_is_one_explicit_shared_transport_fatal(
    tmp_path: Path,
) -> None:
    script = """
import json
import sys

first = json.loads(sys.stdin.readline())
second = json.loads(sys.stdin.readline())
prefix = '{"id":' + str(first["id"]) + ',"result":{"payload":"'
suffix = '"}}'
content_size = 4097 - len(prefix.encode()) - len(suffix.encode())
sys.stdout.write(prefix + ("x" * content_size) + suffix + "\\n")
sys.stdout.flush()
sys.stdin.read()
"""
    transport = CodexStdioTransport(max_message_bytes=4096)
    await transport.start(launch(tmp_path, script))
    messages = transport.messages()
    try:
        first = asyncio.create_task(transport.request("first-pending", {}))
        second = asyncio.create_task(transport.request("second-pending", {}))
        first_error, second_error = await asyncio.gather(
            first,
            second,
            return_exceptions=True,
        )
        with pytest.raises(CodexAppServerError) as event_info:
            await anext(messages)
        event_error = event_info.value
        assert isinstance(first_error, CodexAppServerError)
        assert first_error is second_error
        assert first_error is event_error
        assert "limit" in str(first_error)
    finally:
        await transport.stop("forced")


@pytest.mark.anyio
async def test_cancelled_request_neutralizes_late_response_and_next_request_survives(
    tmp_path: Path,
) -> None:
    script = r"""
import json
import sys

first = json.loads(sys.stdin.readline())
second = json.loads(sys.stdin.readline())
print(json.dumps({"id": first["id"], "result": {"late": True}}), flush=True)
print(json.dumps({"id": second["id"], "result": {"pong": True}}), flush=True)
"""
    transport = CodexStdioTransport()
    await transport.start(launch(tmp_path, script))
    try:
        cancelled = asyncio.create_task(transport.request("slow", {}))
        await asyncio.sleep(0.01)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert await transport.request("ping", {}) == {"pong": True}
    finally:
        await transport.stop("forced")


@pytest.mark.anyio
@pytest.mark.parametrize("size", [8, 64])
async def test_cancelled_requests_without_responses_need_no_retained_tombstones(
    tmp_path: Path,
    size: int,
) -> None:
    script = f"""
import json
import sys

for _ in range({size}):
    json.loads(sys.stdin.readline())
next_request = json.loads(sys.stdin.readline())
print(json.dumps({{"id": next_request["id"], "result": {{"pong": True}}}}), flush=True)
"""
    transport = CodexStdioTransport()
    await transport.start(launch(tmp_path, script))
    try:
        for index in range(size):
            cancelled = asyncio.create_task(transport.request(f"slow-{index}", {}))
            await asyncio.sleep(0.001)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
        assert await transport.request("ping", {}) == {"pong": True}
    finally:
        await transport.stop("forced")


def test_thread_open_parser_covers_fork_echo_and_structured_status() -> None:
    result = {
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": "/workspace",
        "reasoningEffort": "xhigh",
        "thread": {
            "id": "fork-1",
            "cliVersion": "0.144.3",
            "status": {"type": "active", "activeFlags": ["waitingOnUserInput"]},
            "turns": [],
        },
    }
    evidence = parse_thread_open_response(
        result,
        method="thread/fork",
        desired_effort="xhigh",
    )
    assert evidence.thread_id == "fork-1"
    assert evidence.effective_effort == "xhigh"
    assert activity_from_thread_status(evidence.status) == ("blocked", "queued")


def test_unknown_status_and_missing_echo_are_incompatible() -> None:
    with pytest.raises(CodexAppServerError, match="unsupported Codex thread status"):
        activity_from_thread_status({"type": "busy-ish"})
    with pytest.raises(CodexAppServerError, match="requires non-empty reasoningEffort"):
        parse_thread_open_response(
            {
                "model": "gpt-5.6-sol",
                "modelProvider": "openai",
                "cwd": "/workspace",
                "thread": {
                    "id": "thread-1",
                    "cliVersion": "0.144.3",
                    "status": {"type": "idle"},
                    "turns": [],
                },
            },
            method="thread/start",
            desired_effort="xhigh",
        )
