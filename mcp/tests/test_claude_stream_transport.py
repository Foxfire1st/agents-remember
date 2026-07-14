from __future__ import annotations

import asyncio
import unittest
from typing import cast

from agents_remember.errors import HarnessControlError
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
    def __init__(self) -> None:
        self.closed = False
        self.payloads: list[bytes] = []

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def write(self, payload: bytes) -> None:
        self.payloads.append(payload)

    async def drain(self) -> None:
        return None


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
