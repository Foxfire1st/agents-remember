"""One pseudo-terminal and the live client attached to it.

A pseudo-terminal (stdlib :mod:`pty`) carries the raw VT/ANSI byte stream that xterm.js renders
verbatim in the browser (slice 6e). The host language is irrelevant to the wire format -- a Python
PTY emits the same bytes a node-pty would -- so this stays stdlib-only (no Node sidecar, one process).

:class:`TerminalSession` is one such client: the fd, the child, and the four operations that drive
them. Browser tabs cannot share one PTY master fd -- reads race, and one tab closing would close the
fd out from under another -- so every WebSocket gets its own client attached to the same durable
tmux session, and a caller that holds one needs nothing else to read, write, resize or release it.
The operations live here rather than on the host because every field they use is the client's own:
``suspend_unsafe`` and ``mouse_seen`` exist for :meth:`TerminalSession.write` alone.
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

from agents_remember.serving.terminal_tmux import (
    TmuxModeCanceller,
    tmux_cancel_copy_mode,
    tmux_client_environment,
)

_READ_CHUNK = 65536
"""Max bytes drained from a PTY per non-blocking read (one WebSocket frame's worth)."""

_REAP_TIMEOUT = 5.0
"""Seconds to reap a terminated child before giving up (the tmux *server* persists regardless)."""

_DEFAULT_PTY_SIZE = (24, 80)
"""Initial ``(rows, cols)`` seeded on a freshly opened PTY so tmux never starts at ``0x0`` before the
first browser resize lands; the real size follows from :meth:`TerminalSession.resize` (``TIOCSWINSZ``)."""

_SUSPEND_BYTE = b"\x1a"
"""Ctrl-Z (SIGTSTP), stripped from stdin writes to **suspend-unsafe** sessions only (bare-pane harnesses
like ``claude``). Such a pane has no job-control shell, so a suspend soft-locks it -- there is no ``fg``
to type -- and the operator's message is lost (Claude Code self-suspends on this byte). A plain
``kind="terminal"`` *shell* session keeps Ctrl-Z, where it is the legitimate keystroke to background a
foreground program to the prompt (``fg``/``bg`` work there). Scoping is per-session (slice 6f hardening)."""

_SGR_MOUSE_EVENT = re.compile(rb"\x1b\[<\d+(?:;\d+){2}[Mm]")
"""One SGR-encoded mouse report (``ESC[<b;x;yM`` / ``m``) -- what xterm.js sends when the client has
mouse tracking (tmux ``mouse on``). A stdin frame made only of these is wheel/click traffic, not typing;
tmux may have entered copy-mode from it, which captures the keyboard until cancelled."""


@dataclass(frozen=True)
class PtyProcess:
    """A spawned child attached to a PTY master fd, with lifecycle controls.

    The return shape of a :data:`Spawner`. ``terminate``/``poll`` are callables (not the raw
    process) so a fake spawner can back a session with any process object -- the session never
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
    """One live terminal client: a tmux-wrapped PTY child plus its lifecycle/worktree correlation."""

    sid: str
    tmux_name: str
    cwd: Path
    command: tuple[str, ...]
    lifecycle_id: str | None
    process: PtyProcess = field(repr=False)
    suspend_unsafe: bool = False
    """True for a bare-pane harness (no shell to ``fg``) -- :meth:`write` strips Ctrl-Z for it; a plain
    shell session leaves False so its job control keeps working."""
    mouse_seen: bool = False
    """Set when this connection's stdin last carried mouse reports (wheel scrolling may have entered
    tmux copy-mode); the first typed input afterwards cancels copy-mode before it is written."""
    mode_canceller: TmuxModeCanceller = field(default=tmux_cancel_copy_mode, repr=False)
    """Leaves tmux copy-mode on :attr:`tmux_name`; injected so a test can observe the cancel without
    a tmux server, and supplied by :class:`~agents_remember.serving.terminal.TerminalHost` from its
    own seams so every client it spawns cancels through the same one."""

    @property
    def master_fd(self) -> int:
        """The PTY master fd -- read child output / write child input through it."""
        return self.process.master_fd

    @property
    def is_alive(self) -> bool:
        """Whether the underlying child is still running."""
        return self.process.is_alive

    def write(self, data: bytes) -> None:
        """Write browser keystrokes to this PTY client.

        For a **suspend-unsafe** session (a bare-pane harness), Ctrl-Z (:data:`_SUSPEND_BYTE`) is
        stripped first -- it would suspend the harness with no shell to ``fg`` it back, dropping the
        operator's message. A plain shell session keeps Ctrl-Z so its job control still works. The
        strip covers both injected packages and live xterm keystrokes, and an all-Ctrl-Z frame to a
        harness becomes a no-op write.

        Mouse-report-only frames (wheel scrolling) arm :attr:`mouse_seen`: tmux may have entered
        copy-mode, which captures the keyboard. The first non-mouse input afterwards cancels
        copy-mode before it is written, so typing anywhere in the scrollback snaps the view to the
        live bottom and reaches the pane app -- at most one cancel per scroll-then-type cycle, and a
        harmless no-op for panes that never entered copy-mode (mouse-aware TUIs).
        """
        if self.suspend_unsafe:
            data = data.replace(_SUSPEND_BYTE, b"")
        if not data:
            return
        if _SGR_MOUSE_EVENT.sub(b"", data) == b"":
            self.mouse_seen = True
        elif self.mouse_seen:
            self.mouse_seen = False
            self.mode_canceller(self.tmux_name)
        os.write(self.master_fd, data)

    def read_nonblocking(self, max_bytes: int = _READ_CHUNK) -> bytes:
        """Drain up to ``max_bytes`` of child output without blocking.

        Returns ``b""`` when no output is pending *and* when the child has exited (the master
        read raises ``OSError``/``EIO`` once the slave closes) -- callers poll :attr:`is_alive`
        to tell the two apart.
        """
        try:
            return os.read(self.master_fd, max_bytes)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def resize(self, *, cols: int, rows: int) -> None:
        """Set the PTY window size (``TIOCSWINSZ`` -> SIGWINCH to the child / tmux client)."""
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def close(self) -> None:
        """Detach + reap this client's child and release its fd.

        The tmux *server* keeps the underlying session alive (persistence), so a later attach with
        the same name re-reaches the still-running harness.
        """
        with contextlib.suppress(ProcessLookupError):
            self.process.terminate()
        with contextlib.suppress(OSError):
            os.close(self.master_fd)


def spawn_pty(argv: Sequence[str], cwd: Path) -> PtyProcess:
    """Spawn ``argv`` in ``cwd`` on a fresh PTY; the master fd is left non-blocking.

    The default :data:`Spawner`. The child runs through :func:`os.login_tty`, which makes the PTY
    slave its **controlling terminal** (``setsid`` + ``TIOCSCTTY`` + wires stdio): tmux opens
    ``/dev/tty`` to read its size and handle ``SIGWINCH``, so without a controlling terminal it stays
    stuck at 80x24 and ignores every resize. The master is left non-blocking so
    :meth:`TerminalSession.read_nonblocking` never stalls the serving loop.
    """
    master_fd, slave_fd = pty.openpty()
    with contextlib.suppress(OSError):
        # Seed a sane winsize before the child execs so tmux doesn't default to 0x0; the browser's
        # first resize (TerminalSession.resize) overrides it once the WebSocket is open.
        rows, cols = _DEFAULT_PTY_SIZE
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    try:
        # argv is a fixed Sequence[str], never a shell string -- the B2 no-injection posture.
        # The PTY slave is the child's explicit stdio (stdin/out/err) -- never the inherited MCP stdio
        # pipe (the subprocess-hygiene guard, GitHub #49). os.login_tty then makes that slave the child's
        # controlling terminal (setsid + TIOCSCTTY); without it tmux has no /dev/tty to size against and
        # stays stuck at 80x24, ignoring every resize. pass_fds keeps slave_fd open past close_fds so
        # login_tty can re-claim it. The preexec_fn body is async-signal-safe syscalls only
        # (setsid/ioctl/dup2) and the spawn runs off the JSON-RPC threads, so PLW1509 is moot here.
        proc = subprocess.Popen(
            list(argv),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=lambda: os.login_tty(slave_fd),  # noqa: PLW1509 -- async-signal-safe; canonical PTY pattern
            pass_fds=(slave_fd,),
            cwd=str(cwd),
            close_fds=True,
            env=tmux_client_environment(os.environ),
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
            proc.wait(timeout=_REAP_TIMEOUT)

    return PtyProcess(master_fd=master_fd, pid=proc.pid, terminate=_terminate, poll=proc.poll)
