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
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.serving.app import (
    _MAX_IMAGE_BYTES,
    _TERMINAL_EXIT_FRAME,
    _apply_terminal_input,
    create_app,
    resolve_terminal_launch,
)
from agents_remember.serving.terminal import TerminalHost


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


def _which(*installed: str) -> Callable[[str], str | None]:
    """A ``shutil.which`` fake: resolves only the named commands (else ``None``)."""
    present = set(installed)

    def which(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in present else None

    return which


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

    def __init__(self, sid: str, master_fd: int, cwd: Path | None = None) -> None:
        self.sid = sid
        self.master_fd = master_fd
        self.cwd = cwd
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

    def __init__(self, sid: str = "live", cwd: Path | None = None) -> None:
        self._master, self.peer = socket.socketpair()
        self._master.setblocking(False)
        self.peer.settimeout(2.0)
        self.session = _FakeSession(sid, self._master.fileno(), cwd=cwd)
        self.resizes: list[tuple[int, int]] = []
        self.opened: list[dict[str, object]] = []
        self.shutdown_called = False

    def get(self, sid: str) -> _FakeSession | None:
        return self.session if sid == self.session.sid else None

    def open(
        self,
        sid: str,
        *,
        cwd: Path,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        suspend_unsafe: bool = False,
    ) -> SimpleNamespace:
        self.opened.append(
            {
                "sid": sid,
                "cwd": cwd,
                "command": list(command),
                "lifecycle_id": lifecycle_id,
                "suspend_unsafe": suspend_unsafe,
            }
        )
        return SimpleNamespace(sid=sid, cwd=cwd)

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
        self.host = _FakeTerminalHost(cwd=self.tmp)
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

    def test_post_open_spawns_shell_at_workspace_root(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/term-1", json={"kind": "terminal"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session"], "term-1")
        self.assertEqual(body["kind"], "terminal")
        self.assertEqual(len(self.host.opened), 1)
        opened = self.host.opened[0]
        self.assertEqual(opened["sid"], "term-1")
        self.assertEqual(opened["cwd"], self.tmp)  # workspace_root (== _config's tmp)
        self.assertEqual(len(cast(list, opened["command"])), 1)  # the shell argv
        self.assertFalse(opened["suspend_unsafe"])  # a shell keeps Ctrl-Z (job control)

    def test_post_open_rejects_unknown_kind(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/x", json={"kind": "bogus"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.opened, [])

    def test_get_harnesses_lists_supported_set_with_detection(self) -> None:
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            response = client.get("/api/harnesses")
        self.assertEqual(response.status_code, 200)
        harnesses = response.json()["harnesses"]
        self.assertEqual([h["id"] for h in harnesses], ["claude", "codex", "pi"])
        self.assertEqual(
            {h["id"]: h["detected"] for h in harnesses},
            {"claude": True, "codex": False, "pi": False},
        )

    def test_post_open_harness_spawns_registry_argv_at_workspace_root(self) -> None:
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-1", json={"kind": "harness", "harness": "claude"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["kind"], body["harness"]), ("harness", "claude"))
        self.assertEqual(len(self.host.opened), 1)
        opened = self.host.opened[0]
        self.assertEqual(opened["command"], ["claude"])  # server-resolved argv, never wire-supplied
        self.assertEqual(opened["cwd"], self.tmp)  # workspace_root
        self.assertTrue(opened["suspend_unsafe"])  # a bare-pane harness gets the Ctrl-Z strip

    def test_post_open_harness_rejects_uninstalled(self) -> None:
        with patch("shutil.which", _which()), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-2", json={"kind": "harness", "harness": "claude"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.opened, [])

    def test_post_open_harness_rejects_unknown_id(self) -> None:
        with patch("shutil.which", _which("gemini")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-3", json={"kind": "harness", "harness": "gemini"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.opened, [])


class TerminalImageEndpointTests(unittest.TestCase):
    """`POST /api/terminal/{session}/image` (slice 6f): save a pasted image under the session cwd."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.host = _FakeTerminalHost(cwd=self.tmp)
        self.app = create_app(
            _config(self.tmp), interval=100, terminal_host=cast(TerminalHost, self.host)
        )

    def tearDown(self) -> None:
        self.host.close()
        self._dir.cleanup()

    def test_saves_under_session_cwd_and_returns_path(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-body"
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("shot.png", png, "image/png")}
            )
        self.assertEqual(response.status_code, 200)
        path = Path(response.json()["path"])
        self.assertEqual(path.parent.name, ".dashboard-pastes")
        self.assertTrue(path.is_relative_to(self.tmp.resolve()))
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(path.read_bytes(), png)  # flushed to disk before the path is injected

    def test_hostile_filename_yields_uuid_name_under_cwd(self) -> None:
        # The client filename is used ONLY to derive the extension; the basename is always a uuid, so a
        # path-bearing name cannot escape the session cwd. Pins the no-traversal contract.
        png = b"\x89PNG\r\n\x1a\n" + b"body"
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image",
                files={"file": ("../../etc/passwd.png", png, "image/png")},
            )
        self.assertEqual(response.status_code, 200)
        path = Path(response.json()["path"])
        self.assertTrue(path.is_relative_to(self.tmp.resolve()))
        self.assertEqual(path.parent.name, ".dashboard-pastes")
        stem = path.stem  # the uuid hex, never the client-supplied name
        self.assertEqual(len(stem), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in stem))

    def test_rejects_non_image_type(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("notes.txt", b"hello", "text/plain")}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-type")

    def test_rejects_non_image_content_for_image_extension(self) -> None:
        # Magic-byte sniff: an image extension with non-image bytes is rejected, not saved as a .png.
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("shot.png", b"not a real png", "image/png")}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-type")

    def test_rejects_empty_body(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("shot.png", b"", "image/png")}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-type")

    def test_rejects_oversize_post_read(self) -> None:
        with patch("agents_remember.serving.app._MAX_IMAGE_BYTES", 8), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"xx", "image/png")}
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["status"], "too-large")

    def test_rejects_oversize_via_content_length(self) -> None:
        # A blatantly oversize upload is rejected fast on Content-Length, without patching the cap down.
        big = b"\x00" * (_MAX_IMAGE_BYTES + 10_000)
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("big.png", big, "image/png")}
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["status"], "too-large")

    def test_unknown_session_is_404(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/ghost/image", files={"file": ("shot.png", b"\x89PNG", "image/png")}
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-session")


class ResolveTerminalLaunchTests(unittest.TestCase):
    def test_terminal_kind_is_shell_at_workspace_root(self) -> None:
        cwd, argv = resolve_terminal_launch(
            "terminal", workspace_root=Path("/ws"), shell="/bin/zsh"
        )
        self.assertEqual((cwd, argv), (Path("/ws"), ["/bin/zsh"]))

    def test_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch("nope", workspace_root=Path("/ws"), shell="/bin/sh")

    def test_harness_kind_resolves_registry_argv_at_workspace_root(self) -> None:
        cwd, argv = resolve_terminal_launch(
            "harness",
            workspace_root=Path("/ws"),
            shell="/bin/sh",
            harness="claude",
            which=_which("claude"),
        )
        self.assertEqual((cwd, argv), (Path("/ws"), ["claude"]))

    def test_harness_kind_without_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch(
                "harness", workspace_root=Path("/ws"), shell="/bin/sh", which=_which("claude")
            )

    def test_harness_kind_unknown_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch(
                "harness",
                workspace_root=Path("/ws"),
                shell="/bin/sh",
                harness="gemini",
                which=_which("gemini"),
            )

    def test_harness_kind_not_installed_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch(
                "harness",
                workspace_root=Path("/ws"),
                shell="/bin/sh",
                harness="claude",
                which=_which(),
            )


if __name__ == "__main__":
    unittest.main()
