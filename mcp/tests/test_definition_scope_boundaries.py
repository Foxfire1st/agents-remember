"""Real dispatch and worker boundaries own separate computation lifetimes."""

import asyncio
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest
import uvicorn
from agents_remember.application.memory_quality import runs
from agents_remember.application.memory_scope import MemoryScopeIdentity
from agents_remember.cli import __main__ as cli
from agents_remember.kernel.request_memo import request_memo_scope
from agents_remember.mcp import server as mcp_server
from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from httpx import ASGITransport, AsyncClient
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from test_definition_scope import count_parses
from test_stored_operation_record_seat_fields import _integrate_record

pytestmark = pytest.mark.integration
LINES = ["def answer():", "    return 42"]


def twice() -> int:
    for _ in range(2):
        assert "answer" in extents.definitions("answer.py", LINES)
    return 0


def _locate_anchors_twice(name: str) -> None:
    for _ in range(2):
        assert grammars.typescript_anchor_identifier(f"{name}<T>(value)") == name
        assert grammars.typescript_anchor_identifier(f"object.{name}()") is None


def _anchor_parses(calls: list[bytes], name: str) -> list[bytes]:
    return [source for source in calls if name.encode() in source]


@pytest.fixture
def operation_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[LifecycleOperationStore, list[Path]]:
    journal = tmp_path / "operation.json"
    journal.write_text(_integrate_record(tmp_path).model_dump_json(), encoding="utf-8")
    reads: list[Path] = []
    read_uncached = LifecycleOperationStore._read_uncached

    def count_read(store: LifecycleOperationStore) -> LifecycleOperationRecord | None:
        reads.append(store.path)
        return read_uncached(store)

    monkeypatch.setattr(LifecycleOperationStore, "_read_uncached", count_read)
    return LifecycleOperationStore(journal), reads


def test_common_mcp_boundary_shares_sync_handler_context_and_discards_between_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = count_parses(monkeypatch)
    monkeypatch.setattr(mcp_server.server_startup, "start_after_discovery", lambda _config: None)
    server = mcp_server.AgentsRememberMCP("definition boundary test")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def locate() -> int:
        _locate_anchors_twice("nativeAnchorProbe")
        return twice()

    async def invoke() -> None:
        await server.call_tool("locate", {})
        first_anchor_parses = _anchor_parses(calls, "nativeAnchorProbe")
        assert len(first_anchor_parses) == len(set(first_anchor_parses)) >= 2
        assert len(calls) - len(first_anchor_parses) == 1
        await server.call_tool("locate", {})
        assert _anchor_parses(calls, "nativeAnchorProbe") == first_anchor_parses * 2
        assert len(calls) - 2 * len(first_anchor_parses) == 2

    asyncio.run(invoke())
    assert extents._definitions_in_call.get() is None


def test_native_journal_reuse_owns_copies_and_reobserves_each_file_witness(
    monkeypatch: pytest.MonkeyPatch,
    operation_record: tuple[LifecycleOperationStore, list[Path]],
) -> None:
    store, reads = operation_record
    warm = store.read()
    assert warm is not None
    monkeypatch.setattr(mcp_server.server_startup, "start_after_discovery", lambda _config: None)
    server = mcp_server.AgentsRememberMCP("journal boundary test")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def observe(replace_journal: bool = False) -> int:
        first = store.read()
        assert first is not None and first.recordRevision == warm.recordRevision
        assert first.result is not None
        first.result["extra"] = "caller-owned"
        first.recordRevision += 1
        again = store.read()
        assert again is not None and again.recordRevision == warm.recordRevision
        assert again.result is not None and "extra" not in again.result
        if replace_journal:
            successor = store.path.with_suffix(".next")
            successor.write_text(first.model_dump_json(), encoding="utf-8")
            successor.replace(store.path)
            current = store.read()
            assert current is not None and current.recordRevision == first.recordRevision
            assert current.result is not None and current.result["extra"] == "caller-owned"
        return again.recordRevision

    async def invoke() -> None:
        await server.call_tool("observe", {})
        assert reads == [store.path] * 2
        await server.call_tool("observe", {"replace_journal": True})
        assert reads == [store.path] * 4

    asyncio.run(invoke())
    assert (current := store.read()) is not None
    assert current.recordRevision == warm.recordRevision + 1
    assert reads == [store.path] * 5


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


def test_dashboard_requests_reuse_the_process_anchor_and_journal_owners(
    monkeypatch: pytest.MonkeyPatch,
    operation_record: tuple[LifecycleOperationStore, list[Path]],
) -> None:
    calls = count_parses(monkeypatch)
    store, reads = operation_record

    def endpoint(_request: Request) -> JSONResponse:
        _locate_anchors_twice("dashboardAnchorProbe")
        record = store.read()
        assert record is not None
        return JSONResponse({"revision": record.recordRevision})

    app = Starlette(routes=[Route("/", endpoint)])

    async def request_twice() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://asgi") as client:
            first = await client.get("/")
            assert first.status_code == 200
            parsed = len(_anchor_parses(calls, "dashboardAnchorProbe"))
            assert parsed >= 2
            assert reads == [store.path]
            second = await client.get("/")
            assert second.status_code == 200 and second.json() == first.json()
            assert len(_anchor_parses(calls, "dashboardAnchorProbe")) == parsed
            assert reads == [store.path]

    def serve(_args: object) -> int:
        asyncio.run(request_twice())
        return 0

    monkeypatch.setattr(cli.dashboard, "run", serve)
    assert cli.main(["dashboard"]) == 0


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
        _locate_anchors_twice("detachedAnchorProbe")
        completed.set()
        return {"parsed": True}

    _locate_anchors_twice("detachedAnchorProbe")
    warm_anchor_parses = _anchor_parses(calls, "detachedAnchorProbe")
    assert len(warm_anchor_parses) == len(set(warm_anchor_parses)) >= 2
    with extents.definition_scope(), request_memo_scope():
        twice()
        _locate_anchors_twice("detachedAnchorProbe")
        assert _anchor_parses(calls, "detachedAnchorProbe") == warm_anchor_parses * 2
        admission = runs.start_quality_run(identity, work)
        assert admission.state == "started"
        assert completed.wait(HANG_GUARD_SECONDS)
        twice()
    for thread in threads:
        thread.join(HANG_GUARD_SECONDS)
        assert not thread.is_alive()
    assert _anchor_parses(calls, "detachedAnchorProbe") == warm_anchor_parses * 3
    assert len(calls) - 3 * len(warm_anchor_parses) == 2
    assert admission.run_id is not None
    snapshot = runs.poll_quality_run(identity.repo_id, admission.run_id)
    assert snapshot is not None and snapshot.status == "completed"
    assert extents._definitions_in_call.get() is None
