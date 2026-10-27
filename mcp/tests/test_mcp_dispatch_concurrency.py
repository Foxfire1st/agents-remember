"""MIK-R213: synchronous dispatch yields, while its writes keep FIFO ordering."""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents_remember.application.tool_answer_log import current_tool_call, resolve_log_roots
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.kernel import git_command
from agents_remember.kernel.recorded_reads import record_read, recorded_reads
from agents_remember.mcp.registration.memory import register_memory_tools
from agents_remember.mcp.server import AgentsRememberMCP, _complete_fastmcp_settings
from agents_remember.observer.tool_events import call_target_role
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ToolAnnotations
from test_tool_answer_rows_quality import _scoped_pair


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
    _complete_fastmcp_settings()
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

            @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
            def long_call() -> dict[str, Any]:
                entered.set()
                assert release.wait(HANG_GUARD_SECONDS), "the poll never released the long call"
                return {"ok": True}

            @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
            def read_call() -> dict[str, Any]:
                return {"ok": True, "answer": "read"}

            async with create_connected_server_and_client_session(server._mcp_server) as client:
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
                    self.assertFalse(poll.isError)
                    self.assertFalse(read.isError)
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

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
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

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def writer() -> dict[str, bool]:
            entered.set()
            assert release.wait(HANG_GUARD_SECONDS)
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def citation_fix(dry_run: bool = False, mode: str = "sync") -> dict[str, bool]:
            seen.append(dry_run)
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def knowledge_diff(afterRevision: str) -> dict[str, bool]:
            seen.append(False)  # This operation still writes its result report.
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
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

            @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
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
                        _, answer = await server.call_tool("knowledge_read", {"label": label})
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

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
        async def waiting_writer() -> dict[str, bool]:
            self.assertIs(asyncio.get_running_loop(), event_loop)
            entered.set()
            await release.wait()
            return {"ok": True}

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
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
