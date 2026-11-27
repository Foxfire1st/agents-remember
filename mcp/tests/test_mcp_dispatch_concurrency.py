"""MIK-R213: synchronous dispatch yields, while its writes keep FIFO ordering."""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
import unittest
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.tool_answer_log import current_tool_call, resolve_log_roots
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.kernel import git_command, request_memo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.recorded_reads import record_read, recorded_reads
from agents_remember.mcp.registration import role_agents
from agents_remember.mcp.registration.memory import register_memory_tools
from agents_remember.mcp.server import AgentsRememberMCP
from agents_remember.models.role_agents import RoleStartCall, RoleStartSetCall
from agents_remember.observer.tool_events import call_target_role
from agents_remember.serving import heap_diag
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_record_cache import (
    operation_record_cache,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ToolAnnotations
from test_stored_operation_record_seat_fields import _integrate_record
from test_tool_answer_rows_quality import _scoped_pair

from mcp import Client


class _ObservedWriteLock(asyncio.Lock):
    """Signal actual queued acquisition attempts without estimating scheduling time."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting: asyncio.Queue[None] = asyncio.Queue()

    async def acquire(self) -> bool:
        if self.locked():
            self.waiting.put_nowait(None)
        return await super().acquire()


def _server() -> AgentsRememberMCP:
    return AgentsRememberMCP("dispatch test")


async def _entered(event: threading.Event) -> None:
    if not await asyncio.to_thread(event.wait, HANG_GUARD_SECONDS):
        raise AssertionError("synchronous handler did not enter")


class DispatchConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_poll_and_read_answer_while_long_call_is_running(self) -> None:
        with TemporaryDirectory() as directory:
            contract, config, _ = _scoped_pair(Path(directory))
            server = _server()
            server._agents_remember_config = config
            register_memory_tools(server, config)
            entered, release = threading.Event(), threading.Event()

            @server.tool(annotations=ToolAnnotations(read_only_hint=False))
            def long_call() -> dict[str, Any]:
                entered.set()
                assert release.wait(HANG_GUARD_SECONDS), "the poll never released the long call"
                return {"ok": True}

            @server.tool(annotations=ToolAnnotations(read_only_hint=True))
            def read_call() -> dict[str, Any]:
                return {"ok": True, "answer": "read"}

            async with Client(server, mode="legacy") as client:
                started = time.perf_counter()
                long = asyncio.create_task(client.call_tool("long_call", {}))
                try:
                    await _entered(entered)
                    before = time.perf_counter()
                    poll = await asyncio.wait_for(
                        client.call_tool(
                            "memory_quality_check",
                            {
                                "request": {
                                    "mode": "poll",
                                    "repo_id": contract.repo_name,
                                    "run_id": "absent",
                                }
                            },
                        ),
                        HANG_GUARD_SECONDS,
                    )
                    poll_seconds = time.perf_counter() - before
                    before = time.perf_counter()
                    read = await asyncio.wait_for(
                        client.call_tool("read_call", {}), HANG_GUARD_SECONDS
                    )
                    read_seconds = time.perf_counter() - before
                    self.assertFalse(long.done(), "the poll waited for the long call")
                    self.assertFalse(poll.is_error)
                    self.assertFalse(read.is_error)
                finally:
                    release.set()
                    await long
                print(
                    f"dispatch timings: long={time.perf_counter() - started:.6f}s "
                    f"poll={poll_seconds:.6f}s read={read_seconds:.6f}s"
                )

    async def test_cancelled_write_drains_before_next_write_and_keeps_context(self) -> None:
        server = _server()
        entered, release = threading.Event(), threading.Event()
        sequence: list[str] = []
        local = contextvars.ContextVar("dispatch-test", default="missing")
        seen: list[tuple[str, str]] = []

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def writer(label: str) -> dict[str, str]:
            call = current_tool_call()
            seen.append((local.get(), call.tool_name if call is not None else "missing"))
            sequence.append(label + "-start")
            if label == "first":
                entered.set()
                assert release.wait(HANG_GUARD_SECONDS)
            sequence.append(label + "-end")
            return {"label": label}

        lock = _ObservedWriteLock()
        server._synchronous_write_lock = lock
        token = local.set("request-context")
        first = asyncio.create_task(server.call_tool("writer", {"label": "first"}))
        second = None
        try:
            await _entered(entered)
            second = asyncio.create_task(server.call_tool("writer", {"label": "second"}))
            await asyncio.wait_for(lock.waiting.get(), HANG_GUARD_SECONDS)
            first.cancel()
            # Deliver each cancellation to the task, without waiting for real time.
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            self.assertEqual(sequence, ["first-start"])
            self.assertFalse(first.done(), "cancelled call abandoned its live writer")
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            if second is not None:
                await second
            local.reset(token)
        self.assertEqual(sequence, ["first-start", "first-end", "second-start", "second-end"])
        self.assertEqual(seen, [("request-context", "writer")] * 2)

    async def test_conditional_reads_bypass_but_coerced_false_write_waits(self) -> None:
        server = _server()
        entered, release = threading.Event(), threading.Event()
        seen: list[bool] = []

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def writer() -> dict[str, bool]:
            entered.set()
            assert release.wait(HANG_GUARD_SECONDS)
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def citation_fix(dry_run: bool = False, mode: str = "sync") -> dict[str, bool]:
            seen.append(dry_run)
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def knowledge_diff(afterRevision: str) -> dict[str, bool]:
            seen.append(False)  # This operation still writes its result report.
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def context_packet(include_freshness: bool = False) -> dict[str, bool]:
            return {"ok": True}

        lock = _ObservedWriteLock()
        server._synchronous_write_lock = lock
        first = asyncio.create_task(server.call_tool("writer", {}))
        waiting = diff = None
        try:
            await _entered(entered)
            await asyncio.wait_for(server.call_tool("context_packet", {}), HANG_GUARD_SECONDS)
            for arguments in ({"dry_run": True}, {"mode": "start"}, {"mode": "poll"}):
                await asyncio.wait_for(
                    server.call_tool("citation_fix", arguments), HANG_GUARD_SECONDS
                )
            waiting = asyncio.create_task(server.call_tool("citation_fix", {"dry_run": "false"}))
            diff = asyncio.create_task(
                server.call_tool("knowledge_diff", {"afterRevision": "HEAD"})
            )
            # Both writing calls have attempted acquisition behind the live writer.
            await asyncio.wait_for(lock.waiting.get(), HANG_GUARD_SECONDS)
            await asyncio.wait_for(lock.waiting.get(), HANG_GUARD_SECONDS)
            self.assertEqual(seen, [True, False, False])
            self.assertFalse(waiting.done())
            self.assertFalse(
                diff.done(), "a knowledge diff writes a report even with two revisions"
            )
        finally:
            release.set()
            await first
            if waiting is not None:
                await waiting
            if diff is not None:
                await diff
        self.assertEqual(seen, [True, False, False, False, False])

    async def test_concurrent_calls_keep_context_caches_and_answer_logs_separate(self) -> None:
        with TemporaryDirectory() as directory:
            _, config, _ = _scoped_pair(Path(directory))
            server = _server()
            server._agents_remember_config = config
            together = threading.Barrier(2)
            cancellations = {label: threading.Event() for label in ("one", "two")}

            @server.tool(annotations=ToolAnnotations(read_only_hint=True))
            def knowledge_read(label: str) -> dict[str, Any]:
                together.wait(HANG_GUARD_SECONDS)
                call = current_tool_call()
                self.assertIsNotNone(call)
                assert call is not None
                self.assertEqual(dict(call.arguments), {"label": label})
                self.assertEqual(call_target_role.get(), label)
                self.assertIs(git_command._GIT_CANCELLATION.get(), cancellations[label])
                record_read(Path(label), identity=label)
                blobs = git_command._BLOB_READS.get()
                lookups = git_command._OBJECT_LOOKUPS.get()
                assert blobs is not None and lookups is not None
                blobs[(label, label)] = label.encode()
                self.assertEqual(len(blobs), 1)
                self.assertEqual(lookups, {})
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

            async def invoke(label: str) -> dict[str, Any]:
                token = call_target_role.set(label)
                try:
                    with (
                        recorded_reads() as reads,
                        git_command.shared_blob_reads(),
                        git_command.memo_git_object_lookups(),
                        git_command.cancellable_git_commands(cancellations[label]),
                    ):
                        result = await server.call_tool("knowledge_read", {"label": label})
                        assert isinstance(result, CallToolResult)
                        assert result.structured_content is not None
                        answer = result.structured_content
                        self.assertEqual(reads, {label: label})
                        self.assertEqual(
                            git_command._BLOB_READS.get(), {(label, label): label.encode()}
                        )
                        return answer
                finally:
                    call_target_role.reset(token)

            answers = await asyncio.gather(invoke("one"), invoke("two"))
            self.assertEqual([answer["payload"]["label"] for answer in answers], ["one", "two"])
            logs = resolve_log_roots(config, "knowledge_read").coord_log_dir
            assert logs is not None
            rows = [
                json.loads(line)
                for path in logs.glob("ar-tools-*.jsonl")
                for line in path.read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(sorted(row["arguments"]["label"] for row in rows), ["one", "two"])
            for answer in answers:
                detail = json.loads(Path(answer["log"]).read_text())
                self.assertEqual(detail["payload"]["label"], answer["payload"]["label"])

    async def test_async_handler_keeps_its_event_loop_and_existing_concurrency(self) -> None:
        server = _server()
        entered, release = asyncio.Event(), asyncio.Event()
        event_loop = asyncio.get_running_loop()

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        async def waiting_writer() -> dict[str, bool]:
            self.assertIs(asyncio.get_running_loop(), event_loop)
            entered.set()
            await release.wait()
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        async def answering_writer() -> dict[str, bool]:
            release.set()
            return {"ok": True}

        first = asyncio.create_task(server.call_tool("waiting_writer", {}))
        try:
            await asyncio.wait_for(entered.wait(), HANG_GUARD_SECONDS)
            await asyncio.wait_for(server.call_tool("answering_writer", {}), HANG_GUARD_SECONDS)
        finally:
            release.set()
            await first


class ToolCallTrimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(patch("agents_remember.mcp.server.server_startup.start_after_discovery"))

    async def test_trim_follows_success_refusal_and_error_after_call_scope_exits(self) -> None:
        server = _server()
        events: list[str] = []
        failure = RuntimeError("handler failed")

        @server.tool()
        def succeeds() -> dict[str, bool]:
            events.append("success")
            return {"ok": True}

        @server.tool()
        async def refuses() -> dict[str, bool]:
            events.append("refusal")
            return {"ok": False}

        @server.tool()
        async def raises() -> None:
            events.append("error")
            raise failure

        def trim() -> None:
            self.assertIsNone(current_tool_call())
            events.append("trim")

        with (
            patch.object(heap_diag, "trim_malloc", side_effect=trim),
            patch("agents_remember.mcp.server._log_raised_tool_call") as log_error,
        ):
            succeeded = await server.call_tool("succeeds", {})
            assert isinstance(succeeded, CallToolResult)
            self.assertEqual(succeeded.structured_content, {"ok": True})
            refused = await server.call_tool("refuses", {})
            assert isinstance(refused, CallToolResult)
            self.assertEqual(refused.structured_content, {"ok": False})
            with self.assertRaisesRegex(ToolError, "handler failed") as caught:
                await server.call_tool("raises", {})

        self.assertIs(caught.exception.__cause__, failure)
        log_error.assert_called_once_with("raises", {}, None, caught.exception)
        self.assertEqual(events, ["success", "trim", "refusal", "trim", "error", "trim"])

    async def test_trim_lookup_failure_preserves_tool_answer_and_original_error(self) -> None:
        server = _server()
        failure = RuntimeError("handler failed")

        @server.tool()
        async def succeeds() -> dict[str, bool]:
            return {"ok": True}

        @server.tool()
        async def raises() -> None:
            raise failure

        unresolved = {"resolved": False, "func": None}
        with (
            patch.object(
                heap_diag.ctypes.util, "find_library", side_effect=OSError("libc lookup failed")
            ) as lookup,
            patch("agents_remember.mcp.server._log_raised_tool_call") as log_error,
        ):
            with patch.dict(heap_diag._malloc_trim, unresolved):
                succeeded = await server.call_tool("succeeds", {})
                assert isinstance(succeeded, CallToolResult)
                self.assertEqual(succeeded.structured_content, {"ok": True})
            with (
                patch.dict(heap_diag._malloc_trim, unresolved),
                self.assertRaisesRegex(ToolError, "handler failed") as caught,
            ):
                await server.call_tool("raises", {})

        self.assertEqual(lookup.call_count, 2)
        self.assertIs(caught.exception.__cause__, failure)
        log_error.assert_called_once_with("raises", {}, None, caught.exception)

    async def test_blocked_trim_yields_and_drains_through_repeated_cancellation(self) -> None:
        server = _server()
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        entered, release = asyncio.Event(), threading.Event()
        first_trim = threading.Event()

        @server.tool()
        async def responds() -> dict[str, bool]:
            return {"ok": True}

        def trim() -> None:
            self.assertNotEqual(threading.get_ident(), loop_thread)
            if first_trim.is_set():
                return
            first_trim.set()
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(HANG_GUARD_SECONDS)

        with patch.object(heap_diag, "trim_malloc", side_effect=trim) as trim_spy:
            first = asyncio.create_task(server.call_tool("responds", {}))
            try:
                await asyncio.wait_for(entered.wait(), HANG_GUARD_SECONDS)
                answer = await asyncio.wait_for(
                    server.call_tool("responds", {}), HANG_GUARD_SECONDS
                )
                assert isinstance(answer, CallToolResult)
                self.assertEqual(answer.structured_content, {"ok": True})
                first.cancel()
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(0)
                self.assertFalse(first.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await first
        self.assertEqual(trim_spy.call_count, 2)


class _BlockedRoleStart:
    def __init__(self, journal: LifecycleOperationStore) -> None:
        self.journal = journal
        self.loop = asyncio.get_running_loop()
        self.entered: asyncio.Future[request_memo._RequestOwners] = self.loop.create_future()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.reused_record = False

    def __call__(
        self, _config: McpRuntimeConfig, _call: RoleStartCall | RoleStartSetCall
    ) -> dict[str, bool]:
        request = request_memo._REQUEST_OWNERS.get()
        assert request is not None
        first = self.journal.read()
        assert first is not None
        memo = operation_record_cache()
        assert self.journal.path.as_posix() in memo.entries
        self.loop.call_soon_threadsafe(self.entered.set_result, request)
        try:
            assert self.release.wait(HANG_GUARD_SECONDS)
            self.reused_record = self.journal.read() == first and operation_record_cache() is memo
            return {"ok": True}
        finally:
            self.finished.set()

    def trim_reading(self) -> tuple[bool, bool, bool]:
        request = self.entered.result()
        with request.lock:
            return (
                self.finished.is_set(),
                not request.values,
                request_memo._REQUEST_OWNERS.get() is None,
            )


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("role_start", {"role": "worker"}), ("role_start_set", {})],
)
def test_cancelled_role_start_drains_before_clearing_journal_and_trimming(
    tmp_path: Path, tool: str, arguments: dict[str, str]
) -> None:
    journal = tmp_path / "operation.json"
    journal.write_text(_integrate_record(tmp_path).model_dump_json(), encoding="utf-8")
    config = McpRuntimeConfig(tmp_path / "settings.json", tmp_path, tmp_path, tmp_path)
    server = _server()
    role_agents.register_role_agent_tools(server, config)

    @server.tool()
    async def responds() -> dict[str, bool]:
        return {"ok": True}

    async def exercise() -> None:
        backend = _BlockedRoleStart(LifecycleOperationStore(journal))
        tools = SimpleNamespace(role_start_payload=backend, role_start_set_payload=backend)
        trims: list[tuple[bool, bool, bool]] = []
        with (
            patch.object(role_agents, "tools", tools),
            patch("agents_remember.mcp.server.server_startup.start_after_discovery"),
            patch.object(
                heap_diag, "trim_malloc", side_effect=lambda: trims.append(backend.trim_reading())
            ),
        ):
            arguments_with_id = {"request_id": "dbe0e872-5a45-4f00-8c60-ab1f842e2001", **arguments}
            pending = asyncio.create_task(server.call_tool(tool, arguments_with_id))
            try:
                request = await asyncio.wait_for(backend.entered, HANG_GUARD_SECONDS)
                response = await asyncio.wait_for(
                    server.call_tool("responds", {}), HANG_GUARD_SECONDS
                )
                assert isinstance(response, CallToolResult)
                assert response.structured_content == {"ok": True}
                assert trims == [(False, False, True)]
                pending.cancel()
                await asyncio.sleep(0)
                pending.cancel()
                await asyncio.sleep(0)
                with request.lock:
                    assert request.values, (
                        "journal memo cleared before the role-start worker drained"
                    )
                assert trims == [(False, False, True)] and not pending.done()
            finally:
                backend.release.set()
                with suppress(asyncio.CancelledError):
                    await asyncio.wait_for(pending, HANG_GUARD_SECONDS)
                await _entered(backend.finished)
        assert pending.cancelled()
        assert backend.reused_record
        assert trims == [(False, False, True), (True, True, True)]

    asyncio.run(exercise())
