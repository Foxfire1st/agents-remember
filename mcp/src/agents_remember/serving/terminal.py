"""The dashboard terminal host: a tmux-wrapped PTY per session, render-not-scrape (slice 6d).

Mode B2 launches the real harness *inside* a dashboard-owned terminal rather than scraping
its output: a pseudo-terminal (stdlib :mod:`pty`) carries the raw VT/ANSI byte stream that
xterm.js renders verbatim in the browser (slice 6e). The host language is irrelevant to the
wire format -- a Python PTY emits the same bytes a node-pty would -- so this stays stdlib-only
(no Node sidecar, one process).

Each session wraps its child in ``tmux new-session -A -s <name>`` so the session *persists*:
the tmux server outlives the dashboard process and the browser connection, so a restart or a
dropped WebSocket re-attaches the same live harness (``-A`` = attach-or-create) instead of
losing it. The host keeps a registry of live sessions correlated to a lifecycle/worktree.

Security posture (the decided B2 model): the spawn is a *fixed argv* (``Sequence[str]``), never
a shell string, so there is no shell-injection surface; the child runs as the dashboard's own OS
user with that user's existing credentials (``~/.claude``, no re-auth); the WebSocket bridge that
drives this host (slice 6d-2) stays ``127.0.0.1``-bound like the rest of ``serving/``.

The PTY/tmux plumbing is the one impure seam, so :class:`TerminalHost` takes an injectable
``spawn``: tests drive a fake command (``cat``) through a real PTY -- exercising write/read/resize
against the kernel -- without needing tmux, and the full tmux wrapping is covered by a
skip-when-unavailable integration test.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import re
import struct
import subprocess
import termios
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_READ_CHUNK = 65536
"""Max bytes drained from a PTY per non-blocking read (one WebSocket frame's worth)."""

_TERMINATE_TIMEOUT = 5.0
"""Seconds to reap a terminated child before giving up (the tmux *server* persists regardless)."""

_TMUX_NAME_PREFIX = "ar"
"""Prefix for derived tmux session names, namespacing dashboard sessions on the tmux server."""

_UNSAFE_TMUX_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
"""tmux session names cannot contain ``.`` or ``:``; everything outside this class is collapsed."""


@dataclass(frozen=True)
class PtyProcess:
    """A spawned child attached to a PTY master fd, with lifecycle controls.

    The return shape of a :data:`Spawner`. ``terminate``/``poll`` are callables (not the raw
    process) so a fake spawner can back a session with any process object -- the host never
    depends on :class:`subprocess.Popen` directly.
    """

    master_fd: int
    pid: int
    terminate: Callable[[], None]
    poll: Callable[[], int | None]

    @property
    def is_alive(self) -> bool:
        """``True`` until the child exits (``poll()`` returns its exit status)."""
        return self.poll() is None


Spawner = Callable[[Sequence[str], Path], PtyProcess]
"""Spawn ``argv`` in ``cwd`` attached to a fresh PTY; returns the master fd + controls."""


@dataclass
class TerminalSession:
    """One live terminal: a tmux-wrapped PTY child plus its lifecycle/worktree correlation."""

    sid: str
    tmux_name: str
    cwd: Path
    command: tuple[str, ...]
    lifecycle_id: str | None
    process: PtyProcess = field(repr=False)

    @property
    def master_fd(self) -> int:
        """The PTY master fd -- read child output / write child input through it."""
        return self.process.master_fd

    @property
    def pid(self) -> int:
        """The spawned child's process id."""
        return self.process.pid

    @property
    def is_alive(self) -> bool:
        """Whether the underlying child is still running."""
        return self.process.is_alive


def _tmux_session_name(sid: str) -> str:
    """Derive a tmux-safe session name from an arbitrary session id (deterministic)."""
    safe = _UNSAFE_TMUX_CHARS.sub("-", sid).strip("-")
    return f"{_TMUX_NAME_PREFIX}-{safe or 'session'}"


def _build_tmux_command(name: str, cwd: Path, harness: Sequence[str]) -> list[str]:
    """The persistent-session argv: ``tmux new-session -A -s <name> -c <cwd> -- <harness>``.

    Pure -- no I/O -- so the command construction is unit-testable on its own. ``-A`` attaches
    to an existing session of that name (persistence) or creates it; ``--`` ends tmux's option
    parsing so the fixed harness argv is never reinterpreted as tmux flags.
    """
    return ["tmux", "new-session", "-A", "-s", name, "-c", str(cwd), "--", *harness]


def _spawn_pty(argv: Sequence[str], cwd: Path) -> PtyProcess:
    """Spawn ``argv`` in ``cwd`` on a fresh PTY; the master fd is left non-blocking.

    The default :data:`Spawner`. ``start_new_session`` puts the child in its own session so the
    PTY slave becomes its controlling terminal (tmux needs a tty); the master is set non-blocking
    so :meth:`TerminalHost.read_nonblocking` never stalls the serving loop.
    """
    master_fd, slave_fd = pty.openpty()
    try:
        # argv is a fixed Sequence[str], never a shell string -- the B2 no-injection posture.
        proc = subprocess.Popen(
            list(argv),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(cwd),
            start_new_session=True,
            close_fds=True,
        )
    except BaseException:
        os.close(master_fd)
        raise
    finally:
        os.close(slave_fd)
    os.set_blocking(master_fd, False)

    def _terminate() -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_TERMINATE_TIMEOUT)

    return PtyProcess(
        master_fd=master_fd, pid=proc.pid, terminate=_terminate, poll=proc.poll
    )


class TerminalHost:
    """A registry of live tmux-wrapped PTY sessions for the dashboard.

    The backend half of Mode B2: :meth:`open` launches a persistent harness terminal,
    :meth:`write`/:meth:`read_nonblocking` bridge browser keystrokes <-> child output, and
    :meth:`resize` propagates the xterm.js viewport to the PTY (and thus the child via SIGWINCH).
    The WebSocket endpoint (slice 6d-2) is the live driver; the visual (xterm.js) is slice 6e.
    """

    def __init__(self, *, spawn: Spawner | None = None) -> None:
        self._spawn: Spawner = spawn or _spawn_pty
        self._sessions: dict[str, TerminalSession] = {}

    def open(
        self,
        sid: str,
        *,
        cwd: Path | str,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
    ) -> TerminalSession:
        """Open (or re-attach) the session ``sid`` running ``command`` in ``cwd``.

        Idempotent: a live session for ``sid`` is returned as-is (tmux ``-A`` would re-attach the
        same session anyway); a dead one is reaped and replaced. ``lifecycle_id`` correlates the
        session to a lifecycle/worktree for the registry views.
        """
        existing = self._sessions.get(sid)
        if existing is not None and existing.is_alive:
            return existing
        if existing is not None:
            self._discard(existing)
        root = Path(cwd)
        harness = tuple(command)
        tmux_name = name or _tmux_session_name(sid)
        process = self._spawn(_build_tmux_command(tmux_name, root, harness), root)
        session = TerminalSession(
            sid=sid,
            tmux_name=tmux_name,
            cwd=root,
            command=harness,
            lifecycle_id=lifecycle_id,
            process=process,
        )
        self._sessions[sid] = session
        return session

    def get(self, sid: str) -> TerminalSession | None:
        """The live session for ``sid``, or ``None`` if never opened / already closed."""
        return self._sessions.get(sid)

    def sessions(self) -> list[TerminalSession]:
        """Every registered session (registry view)."""
        return list(self._sessions.values())

    def for_lifecycle(self, lifecycle_id: str) -> list[TerminalSession]:
        """Sessions correlated to ``lifecycle_id`` (the lifecycle/worktree spine)."""
        return [s for s in self._sessions.values() if s.lifecycle_id == lifecycle_id]

    def write(self, sid: str, data: bytes) -> None:
        """Write browser keystrokes to the session's PTY (raises ``KeyError`` if unknown)."""
        os.write(self._require(sid).master_fd, data)

    def read_nonblocking(self, sid: str, max_bytes: int = _READ_CHUNK) -> bytes:
        """Drain up to ``max_bytes`` of child output without blocking.

        Returns ``b""`` when no output is pending *and* when the child has exited (the master
        read raises ``OSError``/``EIO`` once the slave closes) -- callers poll :attr:`is_alive`
        to tell the two apart.
        """
        fd = self._require(sid).master_fd
        try:
            return os.read(fd, max_bytes)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def resize(self, sid: str, *, cols: int, rows: int) -> None:
        """Set the PTY window size (``TIOCSWINSZ`` -> SIGWINCH to the child / tmux client)."""
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._require(sid).master_fd, termios.TIOCSWINSZ, winsize)

    def close(self, sid: str) -> None:
        """Detach + reap the session's child and drop it from the registry (no-op if unknown).

        The tmux *server* keeps the underlying session alive (persistence), so a later
        :meth:`open` with the same name re-attaches the still-running harness.
        """
        session = self._sessions.pop(sid, None)
        if session is not None:
            self._discard(session)

    def shutdown(self) -> None:
        """Close every session (dashboard teardown)."""
        for sid in list(self._sessions):
            self.close(sid)

    def _discard(self, session: TerminalSession) -> None:
        with contextlib.suppress(ProcessLookupError):
            session.process.terminate()
        with contextlib.suppress(OSError):
            os.close(session.master_fd)

    def _require(self, sid: str) -> TerminalSession:
        session = self._sessions.get(sid)
        if session is None:
            raise KeyError(sid)
        return session
