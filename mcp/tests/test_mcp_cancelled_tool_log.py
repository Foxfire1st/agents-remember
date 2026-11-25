"""A cancelled MCP request leaves one record of its outcome."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agents_remember.application.tool_answer_log import ToolCallRecord, ToolLogOutcome
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp import server as server_module
from agents_remember.mcp import thread_workers
from agents_remember.mcp.registration import role_agents
from agents_remember.models.role_agents import RoleMessageCall
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from test_mcp_dispatch_concurrency import _entered, _ObservedWriteLock, _server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def logging_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> server_module.AgentsRememberMCP:
    monkeypatch.setattr(server_module.server_startup, "start_after_discovery", lambda _: None)
    monkeypatch.setattr(server_module, "build_currentness", lambda _: None)
    server = _server()
    server._agents_remember_config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "tool-log",
    )
    return server


def _logged_call(tmp_path: Path, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    (row,) = [
        json.loads(line)
        for path in (tmp_path / "tool-log").glob("ar-tools-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert row["tool"] == tool
    assert row["arguments"] == arguments
    return row


def _logged_detail(tmp_path: Path, row: dict[str, Any]) -> dict[str, Any]:
    (path,) = (tmp_path / "tool-log").glob(f"ar-tools-*/{row['detail']}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("fails", [True, False], ids=["late-error", "late-result"])
@pytest.mark.anyio
async def test_cancelled_running_writer_logs_once(
    logging_server: server_module.AgentsRememberMCP, tmp_path: Path, fails: bool
) -> None:
    entered, release = threading.Event(), threading.Event()

    @logging_server.tool(name="knowledge_read")
    def writer(label: str) -> dict[str, Any]:
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS)
        if fails:
            raise ValueError(f"worker failed: {label}")
        return complete_tool_response(
            "knowledge_read",
            {
                "ok": True,
                "state": "view",
                "view": label,
                "repositoryId": "fixture",
                "payload": {"label": label},
            },
        )

    call = asyncio.create_task(logging_server.call_tool("knowledge_read", {"label": "test"}))
    try:
        await _entered(entered)
        call.cancel()
        await asyncio.sleep(0)
        assert not call.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await call

    row = _logged_call(tmp_path, "knowledge_read", {"label": "test"})
    if fails:
        assert row["code"] == "raised"
        detail = _logged_detail(tmp_path, row)
        assert detail["error"] == "the call raised ValueError: worker failed: test"
        assert detail["requestCancelled"] is True
    else:
        assert row["code"] is None
        assert row["ok"] is True


@pytest.mark.parametrize("waiting_for", ["write-lock", "build-currentness"])
@pytest.mark.anyio
async def test_cancelled_before_dispatch_logs_wait(
    logging_server: server_module.AgentsRememberMCP,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    waiting_for: str,
) -> None:
    entered, release = threading.Event(), threading.Event()
    lock = _ObservedWriteLock()
    logging_server._synchronous_write_lock = lock
    ran = threading.Event()

    @logging_server.tool()
    def writer(label: str) -> dict[str, bool]:
        ran.set()
        return {"ok": True}

    def slow_currentness(_: McpRuntimeConfig) -> None:
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS)

    if waiting_for == "build-currentness":
        monkeypatch.setattr(server_module, "build_currentness", slow_currentness)
    else:
        await lock.acquire()
    call = asyncio.create_task(logging_server.call_tool("writer", {"label": "test"}))
    try:
        if waiting_for == "build-currentness":
            await _entered(entered)
        else:
            await asyncio.wait_for(lock.waiting.get(), HANG_GUARD_SECONDS)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, HANG_GUARD_SECONDS)
        assert not ran.is_set()
        row = _logged_call(tmp_path, "writer", {"label": "test"})
        assert row["code"] == "cancelled"
        detail = _logged_detail(tmp_path, row)
        assert detail["waitingFor"] == waiting_for
        assert "before the tool ran" in detail["error"]
    finally:
        release.set()
        if lock.locked():
            lock.release()


_WAITING_MESSAGE = {
    "text": "hello",
    "agent_id": "fac5fdea-8b8c-482d-a452-e71399559b1d",
    "wait": True,
}


@pytest.fixture
def role_server(logging_server: server_module.AgentsRememberMCP) -> server_module.AgentsRememberMCP:
    config = logging_server._agents_remember_config
    assert isinstance(config, McpRuntimeConfig)
    role_agents.register_role_agent_tools(logging_server, config)
    return logging_server


async def _cancel_promptly(call: asyncio.Task[Any]) -> None:
    call.cancel()
    done, _ = await asyncio.wait((call,), timeout=HANG_GUARD_SECONDS)
    assert call in done and call.cancelled(), "request waited for the role worker"


def _assert_detached_error(tmp_path: Path, message: str) -> None:
    row = _logged_call(tmp_path, "role_message", _WAITING_MESSAGE)
    assert row["code"] == "raised"
    detail = _logged_detail(tmp_path, row)
    assert detail["requestCancelled"] is True
    assert detail["error"] == f"the call raised OSError: {message}"


@pytest.mark.anyio
async def test_detached_role_worker_logs_the_error_after_cancellation(
    role_server: server_module.AgentsRememberMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    logged = asyncio.Event()
    original_record = server_module.record_tool_call

    def fail_late(_config: McpRuntimeConfig, call: RoleMessageCall) -> dict[str, bool]:
        assert call.wait
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS * 2)
        raise OSError("detached role worker failed")

    def record(entry: ToolCallRecord) -> ToolLogOutcome:
        result = original_record(entry)
        loop.call_soon_threadsafe(logged.set)
        return result

    monkeypatch.setattr(role_agents, "tools", SimpleNamespace(role_message_payload=fail_late))
    monkeypatch.setattr(server_module, "record_tool_call", record)
    call = asyncio.create_task(role_server.call_tool("role_message", _WAITING_MESSAGE))
    try:
        await _entered(entered)
        await _cancel_promptly(call)
        assert not logged.is_set()
        release.set()
        await asyncio.wait_for(logged.wait(), HANG_GUARD_SECONDS)
    finally:
        release.set()
        with suppress(asyncio.CancelledError):
            await call

    _assert_detached_error(tmp_path, "detached role worker failed")


@pytest.mark.anyio
async def test_both_registered_role_tools_use_the_detached_thread_helper(
    role_server: server_module.AgentsRememberMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    def settings(_config: McpRuntimeConfig, **_arguments: Any) -> dict[str, bool]:
        return {"ok": True}

    def message(_config: McpRuntimeConfig, _call: RoleMessageCall) -> dict[str, bool]:
        return {"ok": True}

    backends = SimpleNamespace(user_settings_init_payload=settings, role_message_payload=message)
    offload = AsyncMock(wraps=thread_workers.run_in_detached_request_thread)
    monkeypatch.setattr(role_agents, "tools", backends)
    monkeypatch.setattr(role_agents, "run_in_detached_request_thread", offload)
    await role_server.call_tool("user_settings_init", {"target": "role-settings", "dry_run": True})
    await role_server.call_tool("role_message", _WAITING_MESSAGE)

    settings_call, message_call = offload.await_args_list
    assert settings_call.args[0] is settings
    assert settings_call.kwargs["target"] == "role-settings"
    assert settings_call.kwargs["dry_run"] is True
    assert message_call.args[0] is message
    assert message_call.args[2].wait is True


@pytest.mark.anyio
async def test_waiting_role_message_cancels_before_its_worker_returns(
    role_server: server_module.AgentsRememberMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, result_logged = (threading.Event() for _ in range(3))

    def finish_late(_config: McpRuntimeConfig, call: RoleMessageCall) -> dict[str, Any]:
        assert call.wait
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS * 2)
        result = complete_tool_response(
            "role_message", {"ok": True, "status": "accepted", "detail": "accepted by test host"}
        )
        result_logged.set()
        return result

    monkeypatch.setattr(role_agents, "tools", SimpleNamespace(role_message_payload=finish_late))
    call = asyncio.create_task(role_server.call_tool("role_message", _WAITING_MESSAGE))
    try:
        await _entered(entered)
        await _cancel_promptly(call)
        assert not result_logged.is_set()
        assert not (tmp_path / "tool-log").exists()
    finally:
        release.set()
        with suppress(asyncio.CancelledError):
            await call
        await _entered(result_logged)

    row = _logged_call(tmp_path, "role_message", _WAITING_MESSAGE)
    assert row["ok"] is True and row["code"] is None


def test_detached_role_worker_logs_during_runner_shutdown(
    role_server: server_module.AgentsRememberMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, finished = (threading.Event() for _ in range(3))

    def fail_during_shutdown(_config: McpRuntimeConfig, call: RoleMessageCall) -> dict[str, bool]:
        assert call.wait
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS)
        finished.set()
        raise OSError("role worker failed during executor shutdown")

    class ReleaseAtShutdown(ThreadPoolExecutor):
        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            release.set()
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    async def cancel_then_close_runner() -> None:
        asyncio.get_running_loop().set_default_executor(ReleaseAtShutdown(max_workers=2))
        call = asyncio.create_task(role_server.call_tool("role_message", _WAITING_MESSAGE))
        await _entered(entered)
        await _cancel_promptly(call)
        assert not release.is_set() and not finished.is_set()
        assert not (tmp_path / "tool-log").exists()

    monkeypatch.setattr(
        role_agents, "tools", SimpleNamespace(role_message_payload=fail_during_shutdown)
    )
    # Runner cancels outstanding tasks before joining the executor and releasing the worker.
    asyncio.run(cancel_then_close_runner())
    assert finished.is_set()
    _assert_detached_error(tmp_path, "role worker failed during executor shutdown")


@pytest.mark.anyio
async def test_cancelled_queued_role_work_never_starts(
    role_server: server_module.AgentsRememberMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = asyncio.Event()
    release, ran = threading.Event(), threading.Event()

    class ObservedExecutor(ThreadPoolExecutor):
        def submit[**P, R](
            self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
        ) -> Future[R]:
            future = super().submit(fn, *args, **kwargs)
            submitted.set()
            return future

    original_to_thread = asyncio.to_thread

    async def offload[**P, R](
        function: Callable[P, R], /, *arguments: P.args, **keywords: P.kwargs
    ) -> R | None:
        if function is server_module.build_currentness:
            return None
        return await original_to_thread(function, *arguments, **keywords)

    def message(_config: McpRuntimeConfig, _call: RoleMessageCall) -> dict[str, bool]:
        ran.set()
        return {"ok": True}

    monkeypatch.setattr(server_module.asyncio, "to_thread", offload)
    monkeypatch.setattr(role_agents, "tools", SimpleNamespace(role_message_payload=message))
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ObservedExecutor(max_workers=1))
    blocker = loop.run_in_executor(None, release.wait, HANG_GUARD_SECONDS * 2)
    submitted.clear()
    # Exercise the request without queuing its separate final allocator trim behind the blocker.
    call = asyncio.create_task(role_server._execute_tool_call("role_message", _WAITING_MESSAGE))
    try:
        await asyncio.wait_for(submitted.wait(), HANG_GUARD_SECONDS)
        assert not ran.is_set()
        await _cancel_promptly(call)
    finally:
        release.set()
        await blocker
        with suppress(asyncio.CancelledError):
            await call
    await loop.run_in_executor(None, lambda: None)
    assert not ran.is_set(), "cancelled queued role work ran after the executor became available"
    row = _logged_call(tmp_path, "role_message", _WAITING_MESSAGE)
    assert row["code"] == "cancelled"
    assert _logged_detail(tmp_path, row)["waitingFor"] == "worker-thread"
