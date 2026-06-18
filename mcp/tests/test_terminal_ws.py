"""Tests for the terminal WebSocket bridge (slice 6d-2, ``serving.app`` ``/api/terminal``).

Two layers:

* **Frame parsing** -- `_apply_terminal_input` is pure-ish (parse a client text frame into a
  `stdin` write or a `resize`); driven against a recording host, no socket.
* **Bridge** -- the `@app.websocket("/api/terminal/{session}")` endpoint via Starlette's
  TestClient against a **fake host** backed by a real `socketpair` (so PTY<->WS forwarding,
  the EOF/exit frame, and the unknown-session refusal all exercise the real add_reader pump).
"""

from __future__ import annotations

import contextlib
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.serving.app import (
    _TERMINAL_EXIT_FRAME,
    _apply_terminal_input,
    create_app,
)
from agents_remember.serving.terminal import TerminalHost


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


class _RecordingHost:
    """Captures `write`/`resize` calls for the frame-parser unit tests."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []

    def write(self, _sid: str, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, _sid: str, *, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))


class _FakeSession:
    """A terminal-session stand-in: the fields `_bridge_terminal` reads."""

    def __init__(self, sid: str, master_fd: int) -> None:
        self.sid = sid
        self.master_fd = master_fd
        self.alive = True

    @property
    def is_alive(self) -> bool:
        return self.alive


class _FakeTerminalHost:
    """A `TerminalHost` duck-type backed by a `socketpair` (no real PTY).

    The endpoint's master fd is one socket end; the test drives the other (`peer`) to
    simulate child output (`feed`), read delivered stdin (`read_child_input`), and end the
    child (`end`).
    """

    def __init__(self, sid: str = "live") -> None:
        self._master, self.peer = socket.socketpair()
        self._master.setblocking(False)
        self.peer.settimeout(2.0)
        self.session = _FakeSession(sid, self._master.fileno())
        self.resizes: list[tuple[int, int]] = []
        self.shutdown_called = False

    def get(self, sid: str) -> _FakeSession | None:
        return self.session if sid == self.session.sid else None

    def read_nonblocking(self, _sid: str, max_bytes: int = 65536) -> bytes:
        try:
            return self._master.recv(max_bytes)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def write(self, _sid: str, data: bytes) -> None:
        self._master.sendall(data)

    def resize(self, _sid: str, *, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    def shutdown(self) -> None:
        self.shutdown_called = True

    # --- test drivers (the "child" side of the socketpair) ---
    def feed(self, data: bytes) -> None:
        self.peer.sendall(data)

    def read_child_input(self, n: int = 4096) -> bytes:
        return self.peer.recv(n)

    def end(self) -> None:
        self.session.alive = False
        self.peer.close()

    def close(self) -> None:
        for sock in (self._master, self.peer):
            with contextlib.suppress(OSError):
                sock.close()


class ApplyTerminalInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = _RecordingHost()

    def _apply(self, payload: object) -> None:
        _apply_terminal_input(cast(TerminalHost, self.host), "s", json.dumps(payload))

    def test_stdin_frame_writes_bytes(self) -> None:
        self._apply({"type": "stdin", "data": "ls -al\n"})
        self.assertEqual(self.host.writes, [b"ls -al\n"])

    def test_resize_frame_forwards_dimensions(self) -> None:
        self._apply({"type": "resize", "cols": 120, "rows": 40})
        self.assertEqual(self.host.resizes, [(120, 40)])

    def test_resize_with_non_int_is_ignored(self) -> None:
        self._apply({"type": "resize", "cols": "wide", "rows": 40})
        self.assertEqual(self.host.resizes, [])

    def test_unknown_type_is_ignored(self) -> None:
        self._apply({"type": "signal", "data": "INT"})
        self.assertEqual((self.host.writes, self.host.resizes), ([], []))

    def test_malformed_json_is_ignored(self) -> None:
        _apply_terminal_input(cast(TerminalHost, self.host), "s", "not json{")
        self.assertEqual((self.host.writes, self.host.resizes), ([], []))

    def test_non_object_json_is_ignored(self) -> None:
        _apply_terminal_input(cast(TerminalHost, self.host), "s", "[1, 2, 3]")
        self.assertEqual((self.host.writes, self.host.resizes), ([], []))


class TerminalWebSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.host = _FakeTerminalHost()
        self.app = create_app(
            _config(self.tmp), interval=100, terminal_host=cast(TerminalHost, self.host)
        )

    def tearDown(self) -> None:
        self.host.close()
        self._dir.cleanup()

    def test_unknown_session_is_refused(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/ghost"
        ) as ws, self.assertRaises(WebSocketDisconnect) as ctx:
            ws.receive_text()
        self.assertEqual(ctx.exception.code, 4404)

    def test_pty_output_forwarded_as_binary(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/live"
        ) as ws:
            self.host.feed(b"\x1b[32mok\x1b[0m")
            self.assertEqual(ws.receive_bytes(), b"\x1b[32mok\x1b[0m")

    def test_client_stdin_written_to_pty(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/live"
        ) as ws:
            ws.send_text(json.dumps({"type": "stdin", "data": "echo hi\n"}))
            self.assertEqual(self.host.read_child_input(), b"echo hi\n")

    def test_client_resize_forwarded_in_order(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/live"
        ) as ws:
            ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
            # A following stdin we can read back proves the resize frame was processed first.
            ws.send_text(json.dumps({"type": "stdin", "data": "x"}))
            self.assertEqual(self.host.read_child_input(), b"x")
        self.assertEqual(self.host.resizes, [(100, 30)])

    def test_child_exit_sends_exit_frame_then_closes(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/live"
        ) as ws:
            self.host.end()
            self.assertEqual(ws.receive_text(), _TERMINAL_EXIT_FRAME)
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_bytes()

    def test_host_shutdown_on_app_teardown(self) -> None:
        with TestClient(self.app):
            pass
        self.assertTrue(self.host.shutdown_called)


if __name__ == "__main__":
    unittest.main()
