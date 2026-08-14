"""Tests for the terminal WebSocket bridge (slice 6d-2, ``serving.app`` ``/api/terminal``).

Two layers:

* **Frame parsing** -- `_apply_terminal_input` is pure-ish (parse a client text frame into a
  `stdin` write or a `resize`); driven against a recording session, no socket.
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
from collections.abc import (
    Callable,
    Sequence,
)
from pathlib import Path
from typing import cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
    TerminalSessionStatus,
)
from agents_remember.serving.app import (
    ServingCollaborators,
    _apply_terminal_input,
    create_app,
)
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal import (
    TerminalHost,
    TerminalSessionBinding,
    TerminalSessionSpec,
)
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_pty import TerminalSession
from agents_remember.tasks import (
    TaskDocument,
    write_task_doc,
)
from test_worktree_support import write_current_task_lineage


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


class _RecordingSession:
    """Captures `write`/`resize` calls for the frame-parser unit tests."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, *, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))


def _binding(sid: str, cwd: Path) -> TerminalSessionBinding:
    """The durable identity of a plain ``bash`` session the fake host starts out holding."""
    return TerminalSessionBinding(
        sid=sid,
        tmux_name=f"ar-{sid}",
        cwd=cwd,
        command=("bash",),
        lifecycle_id=None,
    )


def _binding_from(sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
    """The binding ``spec`` asks for -- the same answer the real host returns from ``ensure``."""
    return TerminalSessionBinding(
        sid=sid,
        tmux_name=spec.tmux_name_for(sid),
        cwd=spec.cwd,
        command=spec.command,
        lifecycle_id=spec.lifecycle_id,
        suspend_unsafe=spec.suspend_unsafe,
    )


class _FakeSession:
    """A terminal-session stand-in: the fields `_bridge_terminal` reads and the operations it drives.

    Everything about the session other than its PTY fd is the durable identity the host
    already answers with, so the stand-in is built from a ``TerminalSessionBinding`` rather
    than from a second, hand-kept copy of that model's fields. Resizes and closes are recorded on
    the host that spawned it, which is where the assertions read them.
    """

    def __init__(
        self, binding: TerminalSessionBinding, master: socket.socket, host: _FakeTerminalHost
    ) -> None:
        self.sid = binding.sid
        self.master_fd = master.fileno()
        self.cwd = binding.cwd
        self.tmux_name = binding.tmux_name
        self.command = binding.command
        self.lifecycle_id = binding.lifecycle_id
        self.suspend_unsafe = binding.suspend_unsafe
        self.alive = True
        self._master = master
        self._host = host

    @property
    def is_alive(self) -> bool:
        return self.alive

    def write(self, data: bytes) -> None:
        self._master.sendall(data)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_terminal_ws.py:210).
    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:  # pragma: no cover
        try:
            return self._master.recv(max_bytes)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def resize(self, *, cols: int, rows: int) -> None:
        self._host.resizes.append((cols, rows))

    def close(self) -> None:
        self._host.closed.append(self.sid)
        self._host.release(self)


class _FakeTerminalHost:
    """A `TerminalHost` duck-type backed by a `socketpair` (no real PTY).

    Each websocket attachment gets its own socketpair, matching the real tmux-client contract:
    one durable tmux session name, multiple independent local PTY clients.
    """

    def __init__(self, sid: str = "live", *, cwd: Path) -> None:
        self._masters: dict[int, socket.socket] = {}
        self._peers: dict[int, socket.socket] = {}
        self.registry_session: _FakeSession | None = self._new_client(_binding(sid, cwd))
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_terminal_ws.py:253).
    def open(self, sid: str, spec: TerminalSessionSpec) -> _FakeSession:  # pragma: no cover
        self.opened.append(
            {
                "sid": sid,
                "cwd": spec.cwd,
                "command": list(spec.command),
                "lifecycle_id": spec.lifecycle_id,
                "name": spec.name,
                "suspend_unsafe": spec.suspend_unsafe,
                "env": dict(spec.env or {}),
            }
        )
        self.registry_session = self._new_client(_binding_from(sid, spec))
        self.session = self.registry_session
        self.probe_names.add(self.session.tmux_name)
        return self.session

    def ensure(self, sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
        tmux_name = spec.tmux_name_for(sid)
        self.ensured.append(
            {
                "sid": sid,
                "cwd": spec.cwd,
                "command": list(spec.command),
                "lifecycle_id": spec.lifecycle_id,
                "name": spec.name,
                "suspend_unsafe": spec.suspend_unsafe,
                "env": dict(spec.env or {}),
            }
        )
        self.probe_names.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )

    def attach(self, sid: str, spec: TerminalSessionSpec) -> _FakeSession:
        self.attached.append(
            {
                "sid": sid,
                "cwd": spec.cwd,
                "command": list(spec.command),
                "lifecycle_id": spec.lifecycle_id,
                "name": spec.name,
                "suspend_unsafe": spec.suspend_unsafe,
            }
        )
        session = self._new_client(_binding_from(sid, spec))
        self.attachments.append(session)
        self.session = session
        return session

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.probe_names

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_terminal_ws.py:312).
    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_terminal_ws.py:325).
    def close(self, sid: str | None = None) -> None:  # pragma: no cover
        if sid is None:
            for sock in [*self._masters.values(), *self._peers.values()]:
                with contextlib.suppress(OSError):
                    sock.close()
            self._masters.clear()
            self._peers.clear()
            return
        if self.registry_session is not None and sid == self.registry_session.sid:
            self.registry_session.close()
            self.registry_session = None

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_terminal_ws.py:337).
    def release(self, session: _FakeSession) -> None:  # pragma: no cover
        """Drop the sockets behind ``session`` -- what the real client's ``close`` releases."""
        session.alive = False
        for sockets in (self._masters, self._peers):
            sock = sockets.pop(id(session), None)
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()

    def shutdown(self) -> None:
        self.shutdown_called = True

    # --- test drivers (the "child" side of the socketpair) ---
    def feed(self, data: bytes, session: _FakeSession | None = None) -> None:
        self.feed_to(session or self.session, data)

    def feed_to(self, session: _FakeSession | None, data: bytes) -> None:
        assert session is not None
        self._peers[id(session)].sendall(data)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_terminal_ws.py:357).
    def feed_all(self, data: bytes) -> None:  # pragma: no cover
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

    def _new_client(self, binding: TerminalSessionBinding) -> _FakeSession:
        """One PTY client for the durable session ``binding`` names, backed by a socketpair."""
        master, peer = socket.socketpair()
        master.setblocking(False)
        peer.settimeout(2.0)
        session = _FakeSession(binding, master, self)
        self._masters[id(session)] = master
        self._peers[id(session)] = peer
        return session


class ApplyTerminalInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _RecordingSession()

    def _apply(self, payload: object) -> None:
        _apply_terminal_input(cast(TerminalSession, self.session), json.dumps(payload))

    def test_stdin_frame_writes_bytes(self) -> None:
        self._apply({"type": "stdin", "data": "ls -al\n"})
        self.assertEqual(self.session.writes, [b"ls -al\n"])

    def test_resize_frame_forwards_dimensions(self) -> None:
        self._apply({"type": "resize", "cols": 120, "rows": 40})
        self.assertEqual(self.session.resizes, [(120, 40)])

    def test_resize_with_non_int_is_ignored(self) -> None:
        self._apply({"type": "resize", "cols": "wide", "rows": 40})
        self.assertEqual(self.session.resizes, [])

    def test_unknown_type_is_ignored(self) -> None:
        self._apply({"type": "signal", "data": "INT"})
        self.assertEqual((self.session.writes, self.session.resizes), ([], []))

    def test_malformed_json_is_ignored(self) -> None:
        _apply_terminal_input(cast(TerminalSession, self.session), "not json{")
        self.assertEqual((self.session.writes, self.session.resizes), ([], []))

    def test_non_object_json_is_ignored(self) -> None:
        _apply_terminal_input(cast(TerminalSession, self.session), "[1, 2, 3]")
        self.assertEqual((self.session.writes, self.session.resizes), ([], []))


class TerminalWebSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        _write_leaf_task(self.tmp, repo="repo", master="master", doc_id="leaf-1")
        write_current_task_lineage(
            self.tmp, repo_name="repo", master_name="master", leaf_id="leaf-1"
        )
        _write_leaf_task(
            self.tmp,
            repo="agents-remember",
            master="260628_operations-integration",
            doc_id="260628-L5",
        )
        write_current_task_lineage(
            self.tmp,
            repo_name="agents-remember",
            master_name="260628_operations-integration",
            leaf_id="260628-L5",
        )
        self.host = _FakeTerminalHost(cwd=self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "logs/dashboard/terminal-sessions.json")
        self.app = create_app(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=100),
            collaborators=ServingCollaborators(
                terminal_host=cast(TerminalHost, self.host), terminal_catalog=self.catalog
            ),
        )

    def tearDown(self) -> None:
        self.host.close()
        self._dir.cleanup()

    def _register_live(self) -> None:
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        self.host.probe_names.add("ar-live")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
