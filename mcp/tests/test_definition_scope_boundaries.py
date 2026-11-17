"""Real dispatch and worker boundaries own separate definition lifetimes."""

import asyncio
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.request import urlopen

import pytest
import uvicorn
from agents_remember.application.memory_quality import runs
from agents_remember.application.memory_scope import MemoryScopeIdentity
from agents_remember.cli import __main__ as cli
from agents_remember.mcp import server as mcp_server
from agents_remember.memory_quality.style.citations import extents
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from test_definition_scope import count_parses

pytestmark = pytest.mark.integration
LINES = ["def answer():", "    return 42"]


def twice() -> int:
    for _ in range(2):
        assert "answer" in extents.definitions("answer.py", LINES)
    return 0


def test_common_mcp_boundary_shares_sync_handler_context_and_discards_between_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = count_parses(monkeypatch)
    monkeypatch.setattr(mcp_server.server_startup, "start_after_discovery", lambda _config: None)
    mcp_server._complete_fastmcp_settings()
    server = mcp_server.AgentsRememberMCP("definition boundary test")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def locate() -> int:
        return twice()

    async def invoke() -> None:
        await server.call_tool("locate", {})
        assert len(calls) == 1
        await server.call_tool("locate", {})
        assert len(calls) == 2

    asyncio.run(invoke())
    assert extents._definitions_in_call.get() is None


@pytest.mark.parametrize(
    "command,options",
    [
        ("knowledge_validate", ["knowledge-validate", "/memory", "--code", "/code"]),
        ("knowledge_convert", ["knowledge-convert", "/memory", "--code", "/code"]),
        ("knowledge_census", ["knowledge-census", "report", "/memory"]),
    ],
)
def test_cli_boundary_clears_success_and_error_scopes(
    monkeypatch: pytest.MonkeyPatch, command: str, options: list[str]
) -> None:
    calls = count_parses(monkeypatch)
    adapter = getattr(cli, command)
    monkeypatch.setattr(adapter, "run", lambda _args: twice())
    assert cli.main(options) == 0
    assert extents._definitions_in_call.get() is None
    assert cli.main(options) == 0
    assert extents._definitions_in_call.get() is None
    assert len(calls) == 2

    def fail(_args: object) -> int:
        twice()
        raise ValueError("failed command")

    monkeypatch.setattr(adapter, "run", fail)
    with pytest.raises(ValueError, match="failed command"):
        cli.main(options)
    assert extents._definitions_in_call.get() is None
    assert len(calls) == 3


class _StartedServer(uvicorn.Server):
    def __init__(self, app: Starlette) -> None:
        super().__init__(uvicorn.Config(app, log_level="warning", lifespan="off", ws="none"))
        self.ready = threading.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.ready.set()


def _request_twice(server: _StartedServer, port: int) -> list[dict[str, Any]]:
    try:
        assert server.ready.wait(HANG_GUARD_SECONDS)
        answers = []
        for _ in range(2):
            with urlopen(f"http://127.0.0.1:{port}/", timeout=HANG_GUARD_SECONDS) as response:
                answers.append(json.load(response))
        return answers
    finally:
        server.should_exit = True


@pytest.mark.parametrize("options", [[], ["--reload"], ["--daemon"]])
def test_dashboard_cli_serves_requests_without_a_process_definition_scope(
    monkeypatch: pytest.MonkeyPatch, options: list[str]
) -> None:
    calls = count_parses(monkeypatch)
    contexts: list[object] = []

    def endpoint(_request: Request) -> JSONResponse:
        extents.definitions("answer.py", LINES)
        return JSONResponse(
            {"parses": len(calls), "scoped": extents._definitions_in_call.get() is not None}
        )

    server = _StartedServer(Starlette(routes=[Route("/", endpoint)]))
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as client:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        def serve(_args: object) -> int:
            contexts.append(extents._definitions_in_call.get())
            server.run(sockets=[listener])
            contexts.append(extents._definitions_in_call.get())
            return 0

        monkeypatch.setattr(cli.dashboard, "run", serve)
        answers = client.submit(_request_twice, server, port)
        try:
            assert cli.main(["dashboard", *options]) == 0
        finally:
            server.should_exit = True
        assert answers.result(timeout=HANG_GUARD_SECONDS) == [
            {"parses": 1, "scoped": False},
            {"parses": 2, "scoped": False},
        ]
    assert contexts == [None, None]
    assert len(calls) == 2
    assert extents._definitions_in_call.get() is None


def test_background_worker_gets_isolated_scope_without_inheriting_starting_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = count_parses(monkeypatch)
    monkeypatch.setattr(runs, "_registry", {})
    identity = runs.QualityRunIdentity(
        repo_id="definition-test",
        scope=MemoryScopeIdentity(
            authority="official",
            authority_path="/fixture",
            code_root="/code",
            onboarding_root="/memory/onboarding",
        ),
        checks=(),
        detail_limit=1,
        publish_curator_report=False,
    )
    completed = threading.Event()
    threads: list[threading.Thread] = []
    thread_type = threading.Thread

    def tracked_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        thread = thread_type(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(runs.threading, "Thread", tracked_thread)

    def work() -> dict[str, object]:
        twice()
        completed.set()
        return {"parsed": True}

    with extents.definition_scope():
        twice()
        admission = runs.start_quality_run(identity, work)
        assert admission.state == "started"
        assert completed.wait(HANG_GUARD_SECONDS)
        twice()
    for thread in threads:
        thread.join(HANG_GUARD_SECONDS)
        assert not thread.is_alive()
    assert len(calls) == 2
    assert admission.run_id is not None
    snapshot = runs.poll_quality_run(identity.repo_id, admission.run_id)
    assert snapshot is not None and snapshot.status == "completed"
    assert extents._definitions_in_call.get() is None
