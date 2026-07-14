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
import os
import socket
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast
from unittest import mock
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
)
from agents_remember.serving.terminal import TerminalHost, TerminalSessionBinding
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalSessionStatus,
)
from agents_remember.serving.terminal_opener import resolve_terminal_launch
from agents_remember.tasks import TaskDocument, write_task_doc


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


def _write_leaf_task(
    coordination_root: Path,
    *,
    repo: str,
    master: str,
    doc_id: str,
    slug: str | None = None,
) -> None:
    slug = slug or doc_id
    task_root = coordination_root / "tasks" / repo / master
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": master.upper(),
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": repo,
                "createdAt": "2026-07-07T10:00",
                "subTasks": [
                    {
                        "number": doc_id,
                        "name": "Leaf",
                        "file": f"{slug}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": doc_id,
                "slug": slug,
                "title": "Leaf",
                "kind": "subTask",
                "repo": repo,
                "createdAt": "2026-07-07T10:01",
                "master": "task.md",
            }
        ),
    )


def _catalog_entry(
    session_id: str,
    *,
    cwd: Path,
    status: TerminalSessionStatus = "running",
    tmux_name: str | None = None,
    command: Sequence[str] = ("bash",),
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Terminal {session_id}",
        kind="terminal",
        harness=None,
        lifecycle_id=None,
        cwd=cwd,
        tmux_name=tmux_name or f"ar-{session_id}",
        command=tuple(command),
        created_at="2026-06-26T00:00:00Z",
        last_attached_at="2026-06-26T00:00:00Z",
        status=status,
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

    def __init__(
        self,
        sid: str,
        master_fd: int,
        *,
        cwd: Path | None = None,
        tmux_name: str | None = None,
        command: Sequence[str] = ("bash",),
        lifecycle_id: str | None = None,
        suspend_unsafe: bool = False,
    ) -> None:
        self.sid = sid
        self.master_fd = master_fd
        self.cwd = cwd
        self.tmux_name = tmux_name or f"ar-{sid}"
        self.command = tuple(command)
        self.lifecycle_id = lifecycle_id
        self.suspend_unsafe = suspend_unsafe
        self.alive = True

    @property
    def is_alive(self) -> bool:
        return self.alive


class _FakeTerminalHost:
    """A `TerminalHost` duck-type backed by a `socketpair` (no real PTY).

    Each websocket attachment gets its own socketpair, matching the real tmux-client contract:
    one durable tmux session name, multiple independent local PTY clients.
    """

    def __init__(self, sid: str = "live", cwd: Path | None = None) -> None:
        self._masters: dict[int, socket.socket] = {}
        self._peers: dict[int, socket.socket] = {}
        self.registry_session: _FakeSession | None = self._new_client(sid, cwd=cwd)
        self.session = self.registry_session
        self.attachments: list[_FakeSession] = []
        self.resizes: list[tuple[int, int]] = []
        self.ensured: list[dict[str, object]] = []
        self.opened: list[dict[str, object]] = []
        self.attached: list[dict[str, object]] = []
        self.probe_names: set[str] = set()
        self.terminated: list[str] = []
        self.closed: list[str] = []
        self.shutdown_called = False

    def get(self, sid: str) -> _FakeSession | None:
        if self.registry_session is not None and sid == self.registry_session.sid:
            return self.registry_session
        return None

    def open(
        self,
        sid: str,
        *,
        cwd: Path,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
        suspend_unsafe: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> _FakeSession:
        self.opened.append(
            {
                "sid": sid,
                "cwd": cwd,
                "command": list(command),
                "lifecycle_id": lifecycle_id,
                "name": name,
                "suspend_unsafe": suspend_unsafe,
                "env": dict(env or {}),
            }
        )
        self.registry_session = self._new_client(
            sid,
            cwd=cwd,
            tmux_name=name or f"ar-{sid}",
            command=command,
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )
        self.session = self.registry_session
        self.probe_names.add(self.session.tmux_name)
        return self.session

    def ensure(
        self,
        sid: str,
        *,
        cwd: Path,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
        suspend_unsafe: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> TerminalSessionBinding:
        tmux_name = name or f"ar-{sid}"
        self.ensured.append(
            {
                "sid": sid,
                "cwd": cwd,
                "command": list(command),
                "lifecycle_id": lifecycle_id,
                "name": name,
                "suspend_unsafe": suspend_unsafe,
                "env": dict(env or {}),
            }
        )
        self.probe_names.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=cwd,
            command=tuple(command),
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )

    def attach(
        self,
        sid: str,
        *,
        cwd: Path,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
        suspend_unsafe: bool = False,
    ) -> _FakeSession:
        self.attached.append(
            {
                "sid": sid,
                "cwd": cwd,
                "command": list(command),
                "lifecycle_id": lifecycle_id,
                "name": name,
                "suspend_unsafe": suspend_unsafe,
            }
        )
        session = self._new_client(
            sid,
            cwd=cwd,
            tmux_name=name or f"ar-{sid}",
            command=command,
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )
        self.attachments.append(session)
        self.session = session
        return session

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.probe_names

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:
        target = tmux_name or (
            self.registry_session.tmux_name
            if self.registry_session is not None and sid == self.registry_session.sid
            else None
        )
        if target is not None:
            self.terminated.append(target)
            self.probe_names.discard(target)
        for session in [self.registry_session, *self.attachments]:
            if session is not None and sid == session.sid:
                session.alive = False

    def close(self, sid: str | None = None) -> None:
        if sid is None:
            for sock in [*self._masters.values(), *self._peers.values()]:
                with contextlib.suppress(OSError):
                    sock.close()
            self._masters.clear()
            self._peers.clear()
            return
        if self.registry_session is not None and sid == self.registry_session.sid:
            self.closed.append(sid)
            self._close_client(self.registry_session)
            self.registry_session = None

    def close_session(self, session: _FakeSession) -> None:
        self.closed.append(session.sid)
        self._close_client(session)

    def read_nonblocking(self, sid: str, max_bytes: int = 65536) -> bytes:
        session = self.get(sid)
        if session is None:
            return b""
        return self.read_session(session, max_bytes=max_bytes)

    def read_session(self, session: _FakeSession, max_bytes: int = 65536) -> bytes:
        master = self._masters[id(session)]
        try:
            return master.recv(max_bytes)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def write(self, sid: str, data: bytes) -> None:
        session = self.get(sid)
        if session is not None:
            self.write_session(session, data)

    def write_session(self, session: _FakeSession, data: bytes) -> None:
        self._masters[id(session)].sendall(data)

    def resize(self, _sid: str, *, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    def resize_session(self, _session: _FakeSession, *, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    def shutdown(self) -> None:
        self.shutdown_called = True

    # --- test drivers (the "child" side of the socketpair) ---
    def feed(self, data: bytes, session: _FakeSession | None = None) -> None:
        self.feed_to(session or self.session, data)

    def feed_to(self, session: _FakeSession | None, data: bytes) -> None:
        assert session is not None
        self._peers[id(session)].sendall(data)

    def feed_all(self, data: bytes) -> None:
        for session in list(self.attachments):
            if session.is_alive:
                self.feed_to(session, data)

    def read_child_input(self, n: int = 4096, session: _FakeSession | None = None) -> bytes:
        target = session or self.session
        assert target is not None
        return self._peers[id(target)].recv(n)

    def end(self, session: _FakeSession | None = None) -> None:
        target = session or self.session
        assert target is not None
        target.alive = False
        self._peers[id(target)].close()

    def _new_client(
        self,
        sid: str,
        *,
        cwd: Path | None = None,
        tmux_name: str | None = None,
        command: Sequence[str] = ("bash",),
        lifecycle_id: str | None = None,
        suspend_unsafe: bool = False,
    ) -> _FakeSession:
        master, peer = socket.socketpair()
        master.setblocking(False)
        peer.settimeout(2.0)
        session = _FakeSession(
            sid,
            master.fileno(),
            cwd=cwd,
            tmux_name=tmux_name,
            command=command,
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )
        self._masters[id(session)] = master
        self._peers[id(session)] = peer
        return session

    def _close_client(self, session: _FakeSession) -> None:
        session.alive = False
        for sockets in (self._masters, self._peers):
            sock = sockets.pop(id(session), None)
            if sock is not None:
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
        _write_leaf_task(self.tmp, repo="repo", master="master", doc_id="leaf-1")
        _write_leaf_task(
            self.tmp,
            repo="agents-remember",
            master="260628_operations-integration",
            doc_id="260628-L5",
        )
        self.host = _FakeTerminalHost(cwd=self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.app = create_app(
            _config(self.tmp),
            interval=100,
            terminal_host=cast(TerminalHost, self.host),
            terminal_catalog=self.catalog,
        )

    def tearDown(self) -> None:
        self.host.close()
        self._dir.cleanup()

    def _register_live(self) -> None:
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        self.host.probe_names.add("ar-live")

    def test_unknown_session_is_refused(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/ghost"
        ) as ws, self.assertRaises(WebSocketDisconnect) as ctx:
            ws.receive_text()
        self.assertEqual(ctx.exception.code, 4404)

    def test_pty_output_forwarded_as_binary(self) -> None:
        self._register_live()
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/live"
        ) as ws:
            self.host.feed(b"\x1b[32mok\x1b[0m")
            self.assertEqual(ws.receive_bytes(), b"\x1b[32mok\x1b[0m")

    def test_client_stdin_written_to_pty(self) -> None:
        self._register_live()
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/live"
        ) as ws:
            ws.send_text(json.dumps({"type": "stdin", "data": "echo hi\n"}))
            self.assertEqual(self.host.read_child_input(), b"echo hi\n")

    def test_client_resize_forwarded_in_order(self) -> None:
        self._register_live()
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/live"
        ) as ws:
            ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
            # A following stdin we can read back proves the resize frame was processed first.
            ws.send_text(json.dumps({"type": "stdin", "data": "x"}))
            self.assertEqual(self.host.read_child_input(), b"x")
        self.assertEqual(self.host.resizes, [(100, 30)])

    def test_client_disconnect_detaches_local_pty_without_exiting_catalog_row(self) -> None:
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        self.host.probe_names.add("ar-live")

        with TestClient(self.app) as client, client.websocket_connect("/api/terminal/live"):
            pass

        self.assertEqual(self.host.closed, ["live"])
        entry = self.catalog.get("live")
        assert entry is not None
        self.assertEqual(entry.status, "running")

    def test_parallel_websockets_use_independent_terminal_clients(self) -> None:
        self._register_live()

        with TestClient(self.app) as client:  # noqa: SIM117 - ws2 must close while ws1 remains open.
            with client.websocket_connect("/api/terminal/live") as ws1:
                with client.websocket_connect("/api/terminal/live") as ws2:
                    self.assertEqual(len(self.host.attachments), 2)
                    self.host.feed_all(b"shared-output")
                    self.assertEqual(ws1.receive_bytes(), b"shared-output")
                    self.assertEqual(ws2.receive_bytes(), b"shared-output")
                self.host.feed_to(self.host.attachments[0], b"still-open")
                self.assertEqual(ws1.receive_bytes(), b"still-open")

        self.assertEqual(self.host.closed, ["live", "live"])

    def test_child_exit_sends_exit_frame_then_closes(self) -> None:
        self._register_live()
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
            response = client.post(
                "/api/terminal/term-1",
                json={"kind": "terminal", "label": "Terminal 1", "lifecycleId": "LC1"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session"], "term-1")
        self.assertEqual(body["kind"], "terminal")
        self.assertEqual(body["label"], "Terminal 1")
        self.assertEqual(body["lifecycleId"], "LC1")
        self.assertEqual(body["tmuxName"], "ar-term-1")
        self.assertEqual(body["status"], "running")
        self.assertEqual(len(self.host.ensured), 1)
        ensured = self.host.ensured[0]
        self.assertEqual(ensured["sid"], "term-1")
        self.assertEqual(ensured["cwd"], self.tmp)  # workspace_root (== _config's tmp)
        self.assertEqual(len(cast(list, ensured["command"])), 1)  # the shell argv
        self.assertEqual(ensured["lifecycle_id"], "LC1")
        self.assertFalse(ensured["suspend_unsafe"])  # a shell keeps Ctrl-Z (job control)
        self.assertEqual(self.host.opened, [])
        self.assertEqual(self.host.closed, [])
        entry = self.catalog.get("term-1")
        assert entry is not None
        self.assertEqual(entry.label, "Terminal 1")
        self.assertEqual(entry.lifecycle_id, "LC1")
        self.assertEqual(entry.status, "running")

    def test_get_terminal_sessions_lists_catalog_entries(self) -> None:
        with TestClient(self.app) as client:
            client.post("/api/terminal/term-1", json={"kind": "terminal", "label": "Terminal 1"})
            response = client.get("/api/terminal/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["sessions"][0],
            {
                "id": "term-1",
                "label": "Terminal 1",
                "kind": "terminal",
                "cwd": str(self.tmp),
                "tmuxName": "ar-term-1",
                "command": [os.environ.get("SHELL") or "/bin/bash"],
                "createdAt": response.json()["sessions"][0]["createdAt"],
                "lastAttachedAt": response.json()["sessions"][0]["lastAttachedAt"],
                "status": "running",
                "seatRole": "terminal",
            },
        )

    def test_post_open_keeps_detached_session_available_for_first_websocket(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/term-1", json={"kind": "terminal", "label": "Terminal 1"}
            )
            self.assertEqual(response.status_code, 200)
            with client.websocket_connect("/api/terminal/term-1") as ws:
                self.host.feed(b"first-attach")
                self.assertEqual(ws.receive_bytes(), b"first-attach")

        self.assertEqual(len(self.host.ensured), 1)
        self.assertEqual(len(self.host.attached), 1)
        self.assertEqual(self.host.closed, ["term-1"])
        entry = self.catalog.get("term-1")
        assert entry is not None
        self.assertEqual(entry.status, "running")

    def test_websocket_attaches_missing_host_from_catalog_when_tmux_exists(self) -> None:
        self.catalog.upsert(
            _catalog_entry("restored", cwd=self.tmp, tmux_name="ar-restored", command=("bash",))
        )
        self.host.probe_names.add("ar-restored")
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/restored"
        ) as ws:
            self.host.feed(b"restored-output")
            self.assertEqual(ws.receive_bytes(), b"restored-output")
        self.assertEqual(self.host.attached[0]["sid"], "restored")
        self.assertEqual(self.host.attached[0]["name"], "ar-restored")
        entry = self.catalog.get("restored")
        assert entry is not None
        self.assertEqual(entry.status, "running")

    def test_websocket_attaches_landed_catalog_session_for_inspection(self) -> None:
        self.catalog.upsert(
            _catalog_entry(
                "landed", cwd=self.tmp, status="landed", tmux_name="ar-landed", command=("bash",)
            )
        )
        self.host.probe_names.add("ar-landed")
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/landed"
        ) as ws:
            self.host.feed(b"landed-output")
            self.assertEqual(ws.receive_bytes(), b"landed-output")
        self.assertEqual(self.host.attached[0]["sid"], "landed")
        entry = self.catalog.get("landed")
        assert entry is not None
        self.assertEqual(entry.status, "landed")

    def test_websocket_marks_stale_catalog_session_exited(self) -> None:
        self.catalog.upsert(_catalog_entry("stale", cwd=self.tmp, tmux_name="ar-stale"))
        with TestClient(self.app) as client, client.websocket_connect(
            "/api/terminal/stale"
        ) as ws, self.assertRaises(WebSocketDisconnect) as ctx:
            ws.receive_text()
        self.assertEqual(ctx.exception.code, 4404)
        entry = self.catalog.get("stale")
        assert entry is not None
        self.assertEqual(entry.status, "exited")

    def test_get_terminal_sessions_marks_stale_tmux_rows_exited(self) -> None:
        self.catalog.upsert(_catalog_entry("stale", cwd=self.tmp, tmux_name="ar-stale"))
        with TestClient(self.app) as client:
            response = client.get("/api/terminal/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sessions"][0]["status"], "exited")

    def test_terminate_marks_catalog_and_kills_tmux(self) -> None:
        with TestClient(self.app) as client:
            client.post("/api/terminal/term-1", json={"kind": "terminal", "label": "Terminal 1"})
            response = client.post("/api/terminal/term-1/terminate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "terminated")
        self.assertEqual(self.host.terminated, ["ar-term-1"])
        entry = self.catalog.get("term-1")
        assert entry is not None
        self.assertEqual(entry.status, "terminated")

    def test_landed_cleanup_closes_only_landed_rows_and_reports_skips(self) -> None:
        self.catalog.upsert(_catalog_entry("landed", cwd=self.tmp, status="landed", tmux_name="ar-landed"))
        self.catalog.upsert(_catalog_entry("running", cwd=self.tmp, tmux_name="ar-running"))
        self.catalog.upsert(_catalog_entry("exited", cwd=self.tmp, status="exited", tmux_name="ar-exited"))

        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/landed-cleanup",
                json={"sessionIds": ["landed", "running", "exited", "ghost"]},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "cleaned")
        self.assertEqual(body["closed"], 1)
        self.assertEqual(body["skipped"], 3)
        self.assertEqual(body["closedSessions"], ["landed"])
        self.assertEqual(
            body["skippedSessions"],
            [
                {"session": "running", "reason": "status:running"},
                {"session": "exited", "reason": "status:exited"},
                {"session": "ghost", "reason": "unknown-session"},
            ],
        )
        self.assertEqual(self.host.terminated, ["ar-landed"])
        landed = self.catalog.get("landed")
        running = self.catalog.get("running")
        exited = self.catalog.get("exited")
        assert landed is not None
        assert running is not None
        assert exited is not None
        self.assertEqual(landed.status, "terminated")
        self.assertEqual(landed.retired_reason, "landed group cleanup")
        self.assertEqual(running.status, "running")
        self.assertEqual(exited.status, "exited")

    def test_post_open_rejects_unknown_kind(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/x", json={"kind": "bogus"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.ensured, [])

    def test_post_open_claims_leaf_and_persists_it(self) -> None:
        leaf = "agents-remember/260628_operations-integration/260628-L5"
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/term-1", json={"kind": "terminal", "leafKey": leaf}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["leafKey"], leaf)
        entry = self.catalog.get("term-1")
        assert entry is not None
        self.assertEqual(entry.leaf_key, leaf)

    def test_post_open_rejects_unmatchable_leaf_ref(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/term-1",
                json={"kind": "terminal", "leafKey": "repo/master/missing-leaf"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "leaf-ref-not-found")
        self.assertIn("<repo>/<master-folder>/<doc-id>", response.json()["detail"])
        self.assertEqual(self.host.ensured, [])
        self.assertIsNone(self.catalog.get("term-1"))

    def test_post_open_null_leaf_still_works(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/term-1", json={"kind": "terminal"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["leafKey"])
        entry = self.catalog.get("term-1")
        assert entry is not None
        self.assertIsNone(entry.leaf_key)

    def test_post_open_409_when_leaf_taken_by_other_running_session(self) -> None:
        leaf = "repo/master/leaf-1"
        with TestClient(self.app) as client:
            first = client.post("/api/terminal/owner", json={"kind": "terminal", "leafKey": leaf})
            self.assertEqual(first.status_code, 200)
            # A second session claiming the same leaf is refused; its own session is never spawned.
            second = client.post(
                "/api/terminal/intruder", json={"kind": "terminal", "leafKey": leaf}
            )
        self.assertEqual(second.status_code, 409)
        body = second.json()
        self.assertEqual(body["status"], "leaf-taken")
        self.assertEqual(body["leafKey"], leaf)
        self.assertEqual(body["session"], "owner")
        self.assertIsNone(self.catalog.get("intruder"))  # no row created for the refused claim
        self.assertEqual([e["sid"] for e in self.host.ensured], ["owner"])

    def test_post_open_reclaims_own_leaf_on_reopen(self) -> None:
        leaf = "repo/master/leaf-1"
        with TestClient(self.app) as client:
            client.post("/api/terminal/term-1", json={"kind": "terminal", "leafKey": leaf})
            # Re-opening the SAME session with the SAME leaf is allowed (not a conflict with itself).
            response = client.post(
                "/api/terminal/term-1", json={"kind": "terminal", "leafKey": leaf}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["leafKey"], leaf)

    def test_attach_leaf_binds_existing_session_when_free(self) -> None:
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/live/attach-leaf", json={"leafKey": leaf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["leafKey"], leaf)
        entry = self.catalog.get("live")
        assert entry is not None
        self.assertEqual(entry.leaf_key, leaf)

    def test_attach_leaf_rejects_unmatchable_ref_without_mutating(self) -> None:
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/attach-leaf",
                json={"leafKey": "repo/master/missing-leaf"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "leaf-ref-not-found")
        entry = self.catalog.get("live")
        assert entry is not None
        self.assertIsNone(entry.leaf_key)

    def test_attach_leaf_409_when_taken_by_other_running_session(self) -> None:
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(_catalog_entry("owner", cwd=self.tmp, tmux_name="ar-owner"))
        self.catalog.upsert(
            _catalog_entry("seeker", cwd=self.tmp, tmux_name="ar-seeker").with_leaf_key(None)
        )
        self.host.probe_names.update({"ar-owner", "ar-seeker"})
        with TestClient(self.app) as client:
            client.post("/api/terminal/owner/attach-leaf", json={"leafKey": leaf})
            response = client.post("/api/terminal/seeker/attach-leaf", json={"leafKey": leaf})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "leaf-taken")
        seeker = self.catalog.get("seeker")
        assert seeker is not None
        self.assertIsNone(seeker.leaf_key)  # the refused attach left it unbound

    def test_attach_leaf_404_for_unknown_session(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/ghost/attach-leaf", json={"leafKey": "repo/master/leaf-1"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-session")

    def test_attach_leaf_404_for_terminated_session(self) -> None:
        self.catalog.upsert(_catalog_entry("dead", cwd=self.tmp, status="terminated"))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/dead/attach-leaf", json={"leafKey": "repo/master/leaf-1"}
            )
        self.assertEqual(response.status_code, 404)

    def test_attach_leaf_404_for_landed_session(self) -> None:
        self.catalog.upsert(_catalog_entry("landed", cwd=self.tmp, status="landed"))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/landed/attach-leaf", json={"leafKey": "repo/master/leaf-1"}
            )
        self.assertEqual(response.status_code, 404)

    def test_terminal_shares_a_leaf_with_its_chat_but_two_chats_conflict(self) -> None:
        # L5 fix 2: uniqueness is per (leaf, role). A terminal must not 409 against the leaf's chat,
        # but a second agent chat (and a second terminal) for the same leaf still conflicts.
        leaf = "repo/master/leaf-1"
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            chat = client.post(
                "/api/terminal/chat-1",
                json={"kind": "harness", "harness": "claude", "leafKey": leaf},
            )
            self.assertEqual(chat.status_code, 200)
            # A terminal on the SAME leaf is allowed alongside the chat (no 409).
            term = client.post("/api/terminal/term-1", json={"kind": "terminal", "leafKey": leaf})
            self.assertEqual(term.status_code, 200)
            self.assertEqual(term.json()["leafKey"], leaf)
            # A second chat is refused -- the chat slot is taken by chat-1.
            second_chat = client.post(
                "/api/terminal/chat-2",
                json={"kind": "harness", "harness": "claude", "leafKey": leaf},
            )
            self.assertEqual(second_chat.status_code, 409)
            self.assertEqual(second_chat.json()["session"], "chat-1")
            # A second terminal is refused too -- the terminal slot is taken by term-1.
            second_term = client.post(
                "/api/terminal/term-2", json={"kind": "terminal", "leafKey": leaf}
            )
            self.assertEqual(second_term.status_code, 409)
            self.assertEqual(second_term.json()["session"], "term-1")

    def test_attach_leaf_terminal_does_not_conflict_with_existing_chat(self) -> None:
        # The attach-leaf path is role-scoped too: a terminal can claim a leaf already held by a chat.
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(
            _catalog_entry("term", cwd=self.tmp, tmux_name="ar-term").with_leaf_key(None)
        )
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            client.post(
                "/api/terminal/chat-1",
                json={"kind": "harness", "harness": "claude", "leafKey": leaf},
            )
            response = client.post("/api/terminal/term/attach-leaf", json={"leafKey": leaf})
        self.assertEqual(response.status_code, 200)
        entry = self.catalog.get("term")
        assert entry is not None
        self.assertEqual(entry.leaf_key, leaf)

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
        self.assertEqual({h["control"] for h in harnesses}, {"unsupported"})

    def test_post_open_harness_spawns_registry_argv_at_workspace_root(self) -> None:
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-1",
                json={"kind": "harness", "harness": "claude", "label": "Claude Code 1"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["kind"], body["harness"]), ("harness", "claude"))
        self.assertEqual(body["label"], "Claude Code 1")
        self.assertEqual(len(self.host.ensured), 1)
        ensured = self.host.ensured[0]
        self.assertEqual(ensured["command"], ["claude"])  # server-resolved argv, never wire-supplied
        self.assertEqual(ensured["cwd"], self.tmp)  # workspace_root
        self.assertTrue(ensured["suspend_unsafe"])  # a bare-pane harness gets the Ctrl-Z strip
        entry = self.catalog.get("h-1")
        assert entry is not None
        self.assertEqual(entry.kind, "harness")
        self.assertEqual(entry.harness, "claude")

    def test_post_open_harness_rejects_uninstalled(self) -> None:
        with patch("shutil.which", _which()), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-2", json={"kind": "harness", "harness": "claude"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.ensured, [])

    def test_post_open_harness_rejects_unknown_id(self) -> None:
        with patch("shutil.which", _which("gemini")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-3", json={"kind": "harness", "harness": "gemini"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.ensured, [])


class TerminalImageEndpointTests(unittest.TestCase):
    """`POST /api/terminal/{session}/image` (slice 6f): save a pasted image under the session cwd."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.host = _FakeTerminalHost(cwd=self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.app = create_app(
            _config(self.tmp),
            interval=100,
            terminal_host=cast(TerminalHost, self.host),
            terminal_catalog=self.catalog,
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

    def test_saves_under_catalog_cwd_after_dashboard_restart(self) -> None:
        restored_cwd = self.tmp / "restored"
        restored_cwd.mkdir()
        self.catalog.upsert(_catalog_entry("restored", cwd=restored_cwd, tmux_name="ar-restored"))
        png = b"\x89PNG\r\n\x1a\n" + b"catalog-body"
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/restored/image", files={"file": ("shot.png", png, "image/png")}
            )
        self.assertEqual(response.status_code, 200)
        path = Path(response.json()["path"])
        self.assertTrue(path.is_relative_to(restored_cwd.resolve()))
        self.assertEqual(path.read_bytes(), png)


class MalformedSettingsScratchTerminalTests(unittest.TestCase):
    """L16 review follow-up (L16R-1): a malformed agentic settings file must fail the
    launches that USE the registry (harness opens), never a plain scratch terminal --
    the /api/terminal route loads the effective registry only when the request
    resolves a harness."""

    def test_plain_terminal_open_skips_the_settings_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            system = tmp / "system"
            system.mkdir(parents=True)
            (system / "settings.json").write_text("{not json", encoding="utf-8")
            config = _config(tmp)
            app = create_app(config)
            seen: dict[str, object] = {}

            def fake_open(**kwargs: object) -> object:
                seen.update(kwargs)
                raise RuntimeError("stop-before-tmux")

            with mock.patch("agents_remember.serving.app.open_terminal_session", fake_open):
                client = TestClient(app, raise_server_exceptions=False)
                client.post("/api/terminal/scratch-1", json={"kind": "terminal"})
            # The registry load never ran for a scratch terminal: harnesses arrives as
            # None (builtin fallback) despite the malformed settings file on disk.
            self.assertIn("harnesses", seen)
            self.assertIsNone(seen["harnesses"])


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
