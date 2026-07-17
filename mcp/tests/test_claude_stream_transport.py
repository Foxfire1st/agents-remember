from __future__ import annotations

import asyncio
import unittest
from typing import cast

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.serving.claude_stream_transport import (
    MAX_CLAUDE_FRAME_BYTES,
    ClaudeSubprocessTransport,
)


class _Reader:
    def __init__(self, lines: list[bytes | Exception] | None = None) -> None:
        self.lines = list(lines or [])

    async def readline(self) -> bytes:
        value = self.lines.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def read(self, _size: int) -> bytes:
        return b""


class _Writer:
    def __init__(
        self,
        *,
        write_error: Exception | None = None,
        drain_error: Exception | None = None,
    ) -> None:
        self.closed = False
        self.payloads: list[bytes] = []
        self.write_error = write_error
        self.drain_error = drain_error

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def write(self, payload: bytes) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.payloads.append(payload)

    async def drain(self) -> None:
        if self.drain_error is not None:
            raise self.drain_error


class _BlockingFirstDrainWriter(_Writer):
    def __init__(self) -> None:
        super().__init__()
        self.first_drain_started = asyncio.Event()
        self.release_first_drain = asyncio.Event()
        self.drain_calls = 0

    async def drain(self) -> None:
        self.drain_calls += 1
        if self.drain_calls == 1:
            self.first_drain_started.set()
            await self.release_first_drain.wait()


class _Process:
    def __init__(
        self,
        *,
        stdout: _Reader | None = None,
        stdin: _Writer | None = None,
        returncode: int | None = None,
    ) -> None:
        self.stdout = stdout
        self.stdin = stdin
        self.stderr = _Reader()
        self.returncode = returncode
        self.kills = 0
        self.waits = 0

    def kill(self) -> None:
        self.kills += 1

    async def wait(self) -> int:
        self.waits += 1
        self.returncode = -9 if self.kills else 0
        return self.returncode


def _transport(process: _Process) -> ClaudeSubprocessTransport:
    transport = ClaudeSubprocessTransport()
    transport._process = cast(asyncio.subprocess.Process, process)
    return transport


class ClaudeStreamTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_guarded_write_is_lock_atomic_and_classifies_write_failures(self) -> None:
        writer = _Writer()
        transport = _transport(_Process(stdout=_Reader(), stdin=writer))
        guard_calls: list[str] = []

        def guard() -> None:
            self.assertEqual(writer.payloads, [])
            guard_calls.append("guarded")

        await transport.write_frame({"type": "prompt", "message": "hello"}, before_write=guard)
        self.assertEqual(guard_calls, ["guarded"])
        self.assertEqual(writer.payloads, [b'{"type":"prompt","message":"hello"}\n'])

        guarded_writer = _Writer()
        guarded = _transport(_Process(stdout=_Reader(), stdin=guarded_writer))

        def refuse() -> None:
            raise HarnessAdapterBusyError("busy before first byte")

        with self.assertRaises(HarnessAdapterBusyError):
            await guarded.write_frame({"type": "prompt"}, before_write=refuse)
        self.assertEqual(guarded_writer.payloads, [])

        for failing_writer in (
            _Writer(write_error=BrokenPipeError("write failed")),
            _Writer(drain_error=ConnectionResetError("drain failed")),
        ):
            with self.subTest(error=type(failing_writer.write_error or failing_writer.drain_error)):
                with self.assertRaises(HarnessAdapterDisconnectedError) as raised:
                    await _transport(_Process(stdout=_Reader(), stdin=failing_writer)).write_frame(
                        {"type": "prompt"}
                    )
                self.assertTrue(raised.exception.may_have_sent)

        closing_writer = _Writer()
        closing_writer.close()
        for process in (
            _Process(stdout=_Reader()),
            _Process(stdout=_Reader(), stdin=closing_writer),
        ):
            with self.assertRaises(HarnessAdapterDisconnectedError) as raised:
                await _transport(process).write_frame({"type": "prompt"})
            self.assertFalse(raised.exception.may_have_sent)

        with self.assertRaisesRegex(HarnessControlError, "input frame exceeds"):
            await transport.write_frame({"message": "x" * MAX_CLAUDE_FRAME_BYTES})

    async def test_write_lock_orders_concurrent_guards_payloads_and_drains(self) -> None:
        writer = _BlockingFirstDrainWriter()
        transport = _transport(_Process(stdout=_Reader(), stdin=writer))
        guard_calls: list[str] = []
        second_attempted = asyncio.Event()

        first = asyncio.create_task(
            transport.write_frame(
                {"message": "first"}, before_write=lambda: guard_calls.append("first")
            )
        )
        await asyncio.wait_for(writer.first_drain_started.wait(), 1)

        async def write_second() -> None:
            second_attempted.set()
            await transport.write_frame(
                {"message": "second"}, before_write=lambda: guard_calls.append("second")
            )

        second = asyncio.create_task(write_second())
        await second_attempted.wait()
        await asyncio.sleep(0)
        self.assertEqual(guard_calls, ["first"])
        self.assertEqual(writer.payloads, [b'{"message":"first"}\n'])
        self.assertFalse(second.done())

        writer.release_first_drain.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        self.assertEqual(guard_calls, ["first", "second"])
        self.assertEqual(
            writer.payloads,
            [b'{"message":"first"}\n', b'{"message":"second"}\n'],
        )
        self.assertEqual(writer.drain_calls, 2)

    async def test_read_frame_accepts_objects_and_rejects_invalid_framing(self) -> None:
        cases: tuple[tuple[bytes | Exception, str | None], ...] = (
            (b'{"type":"result"}\n', None),
            (b"", None),
            (b"[]\n", "must be an object"),
            (b"not-json\n", "invalid stream-json framing"),
            (b"x" * (MAX_CLAUDE_FRAME_BYTES + 1), "larger than"),
            (ValueError("limit"), "larger than"),
        )
        for line, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                process = _Process(stdout=_Reader([line]), stdin=_Writer())
                transport = _transport(process)
                if expected_error is None:
                    frame = await transport.read_frame()
                    if line:
                        self.assertEqual(frame, {"type": "result"})
                    else:
                        self.assertIsNone(frame)
                else:
                    with self.assertRaisesRegex(HarnessControlError, expected_error):
                        await transport.read_frame()

        with self.assertRaisesRegex(HarnessControlError, "stdout is unavailable"):
            await _transport(_Process(stdin=_Writer())).read_frame()

    async def test_stop_is_idempotent_and_honors_graceful_and_forced_modes(self) -> None:
        empty = ClaudeSubprocessTransport()
        await empty.stop("forced")

        graceful_process = _Process(stdout=_Reader(), stdin=_Writer())
        graceful = _transport(graceful_process)
        await graceful.stop("graceful")
        assert graceful_process.stdin is not None
        self.assertTrue(graceful_process.stdin.closed)
        self.assertEqual((graceful_process.kills, graceful_process.waits), (0, 1))

        forced_process = _Process(stdout=_Reader(), stdin=_Writer())
        forced = _transport(forced_process)
        await forced.stop("forced")
        self.assertEqual((forced_process.kills, forced_process.waits), (1, 1))


if __name__ == "__main__":
    unittest.main()
