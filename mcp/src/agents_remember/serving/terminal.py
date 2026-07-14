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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_READ_CHUNK = 65536
"""Max bytes drained from a PTY per non-blocking read (one WebSocket frame's worth)."""

_TERMINATE_TIMEOUT = 5.0
"""Seconds to reap a terminated child before giving up (the tmux *server* persists regardless)."""

_DEFAULT_PTY_SIZE = (24, 80)
"""Initial ``(rows, cols)`` seeded on a freshly opened PTY so tmux never starts at ``0x0`` before the
first browser resize lands; the real size follows from :meth:`TerminalHost.resize` (``TIOCSWINSZ``)."""

_TMUX_NAME_PREFIX = "ar"
"""Prefix for derived tmux session names, namespacing dashboard sessions on the tmux server."""

_UNSAFE_TMUX_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
"""tmux session names cannot contain ``.`` or ``:``; everything outside this class is collapsed."""

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

TmuxProbe = Callable[[str], bool]
"""Return whether a tmux session name currently exists."""


TmuxProbeEvidence = Literal["alive", "pane-gone", "tmux-command-failed"]


@dataclass(frozen=True)
class TmuxProbeResult:
    """Evidence-bearing tmux session probe result."""

    exists: bool
    evidence: TmuxProbeEvidence


TmuxKiller = Callable[[str], None]
"""Kill a tmux session name if it still exists."""

TmuxCreator = Callable[[str, Path, Sequence[str], Mapping[str, str]], None]
"""Create a detached tmux session for a fixed harness argv, seeding ``env`` at spawn (L2)."""

TmuxConfigurer = Callable[[str], None]
"""Apply dashboard session options (mouse mode) to an existing tmux session name."""

TmuxModeCanceller = Callable[[str], None]
"""Cancel copy-mode on a tmux session name (no-op when the pane is not in a mode)."""

TmuxPaneModeProbe = Callable[[str], bool | None]
"""Return whether the target pane is in a tmux mode, or ``None`` when it cannot be queried."""


def _tmux_has_session(name: str) -> bool:
    """Whether tmux currently knows ``name``.

    This separate probe is required for durable rehydrate: ``tmux new-session -A`` would create a fresh
    session when ``name`` is gone, which would falsely report a stale catalog row as resumed.
    """
    return _tmux_probe_session(name).exists


def _tmux_probe_session(name: str) -> TmuxProbeResult:
    """Whether tmux knows ``name``, preserving why a negative probe happened."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_TERMINATE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return TmuxProbeResult(exists=False, evidence="tmux-command-failed")
    if result.returncode == 0:
        return TmuxProbeResult(exists=True, evidence="alive")
    if _tmux_missing_session_stderr(result.stderr if isinstance(result.stderr, str) else ""):
        return TmuxProbeResult(exists=False, evidence="pane-gone")
    return TmuxProbeResult(exists=False, evidence="tmux-command-failed")


def _tmux_missing_session_stderr(stderr: str) -> bool:
    text = stderr.lower()
    return "can't find session" in text or "session not found" in text


def _tmux_probe_result_from_bool(exists: bool) -> TmuxProbeResult:
    evidence: TmuxProbeEvidence = "alive" if exists else "pane-gone"
    return TmuxProbeResult(exists=exists, evidence=evidence)


def _tmux_kill_session(name: str) -> None:
    """Kill tmux session ``name``; no-op when tmux or the session is absent."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["tmux", "kill-session", "-t", name],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TERMINATE_TIMEOUT,
        )


def _env_flags(env: Mapping[str, str]) -> list[str]:
    """Flatten ``env`` into tmux ``-e KEY=VALUE`` new-session flags (L2 knob injection).

    Empty for an empty mapping so the argv is byte-identical to the legacy no-env spawn. tmux seeds
    each pair into the new session's environment (and thus the child harness process); the pairs stay
    argv items on a fixed ``Sequence[str]`` spawn, so there is no shell-injection surface. This is the
    minimal env-passthrough seam the terminal host lacked -- the same injection point the planned T3
    analytics env wiring and the role-knob (model/effort) resolution layer target.
    """
    return [flag for key, value in env.items() for flag in ("-e", f"{key}={value}")]


def _tmux_create_detached(
    name: str, cwd: Path, harness: Sequence[str], env: Mapping[str, str] | None = None
) -> None:
    """Create tmux session ``name`` without attaching a local PTY client, seeding ``env`` at spawn."""
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            name,
            "-c",
            str(cwd),
            *_env_flags(env or {}),
            "--",
            *harness,
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_TERMINATE_TIMEOUT,
    )


def _tmux_enable_mouse(name: str) -> None:
    """Enable per-session mouse mode; no-op when tmux or the session is absent (idempotent).

    With ``mouse on`` tmux requests mouse tracking from the attached client, so browser wheel
    input reaches tmux as mouse reports: tmux scrolls its own pane history (copy-mode) for
    normal-buffer TUIs (Codex) and passes the events through to panes whose app requested mouse
    tracking itself (Claude Code). Without it, wheel movement can only be guessed into key
    presses the inner TUI may not bind. Tradeoff: pane text selection needs Shift+drag while
    mouse reporting is active.
    """
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["tmux", "set-option", "-t", name, "mouse", "on"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TERMINATE_TIMEOUT,
        )


def _tmux_cancel_copy_mode(name: str) -> None:
    """Leave copy-mode on session ``name``; harmless error when no mode is active.

    Wheel scrolling under ``mouse on`` enters tmux copy-mode, which captures the keyboard: typed
    letters are (mostly unbound) copy-mode keys, so they never reach the pane app until the operator
    scrolls back to the bottom. tmux has no any-key-cancels binding, so the host cancels explicitly
    when typing follows mouse traffic -- the view snaps to the live bottom and the keystrokes land in
    the app's composer.
    """
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["tmux", "send-keys", "-t", name, "-X", "cancel"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TERMINATE_TIMEOUT,
        )


def pane_in_mode(name: str) -> bool | None:
    """Read tmux's exact ``pane_in_mode`` flag without sending input."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", name, "#{pane_in_mode}"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_TERMINATE_TIMEOUT,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if value == "1":
        return True
    if value == "0":
        return False
    return None


def ensure_terminal_input_ready(
    name: str,
    *,
    mode_probe: TmuxPaneModeProbe = pane_in_mode,
    mode_canceller: TmuxModeCanceller = _tmux_cancel_copy_mode,
) -> bool:
    """Cancel copy mode when present and prove the exact pane left it before input."""
    mode = mode_probe(name)
    if mode is None:
        return False
    if mode:
        mode_canceller(name)
        return mode_probe(name) is False
    return True


@dataclass
class TerminalSession:
    """One live terminal: a tmux-wrapped PTY child plus its lifecycle/worktree correlation."""

    sid: str
    tmux_name: str
    cwd: Path
    command: tuple[str, ...]
    lifecycle_id: str | None
    process: PtyProcess = field(repr=False)
    suspend_unsafe: bool = False
    """True for a bare-pane harness (no shell to ``fg``) -- :meth:`TerminalHost.write` strips Ctrl-Z for
    it; a plain shell session leaves False so its job control keeps working."""
    mouse_seen: bool = False
    """Set when this connection's stdin last carried mouse reports (wheel scrolling may have entered
    tmux copy-mode); the first typed input afterwards cancels copy-mode before it is written."""

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


@dataclass(frozen=True)
class TerminalSessionBinding:
    """Durable tmux session metadata without an attached PTY client."""

    sid: str
    tmux_name: str
    cwd: Path
    command: tuple[str, ...]
    lifecycle_id: str | None
    suspend_unsafe: bool = False


def _tmux_session_name(sid: str) -> str:
    """Derive a tmux-safe session name from an arbitrary session id (deterministic)."""
    safe = _UNSAFE_TMUX_CHARS.sub("-", sid).strip("-")
    return f"{_TMUX_NAME_PREFIX}-{safe or 'session'}"


def terminal_session_name(sid: str) -> str:
    """Public deterministic tmux identity used before a controlled session is launched."""

    return _tmux_session_name(sid)


def _build_tmux_command(
    name: str, cwd: Path, harness: Sequence[str], env: Mapping[str, str] | None = None
) -> list[str]:
    """The persistent-session argv: ``tmux new-session -A -s <name> -c <cwd> [-e K=V …] -- <harness>``.

    Pure -- no I/O -- so the command construction is unit-testable on its own. ``-A`` attaches
    to an existing session of that name (persistence) or creates it; the optional ``-e KEY=VALUE``
    flags seed spawn env (L2 knob injection); ``--`` ends tmux's option parsing so the fixed harness
    argv is never reinterpreted as tmux flags.
    """
    return [
        "tmux",
        "new-session",
        "-A",
        "-s",
        name,
        "-c",
        str(cwd),
        *_env_flags(env or {}),
        "--",
        *harness,
    ]


def _spawn_pty(argv: Sequence[str], cwd: Path) -> PtyProcess:
    """Spawn ``argv`` in ``cwd`` on a fresh PTY; the master fd is left non-blocking.

    The default :data:`Spawner`. The child runs through :func:`os.login_tty`, which makes the PTY
    slave its **controlling terminal** (``setsid`` + ``TIOCSCTTY`` + wires stdio): tmux opens
    ``/dev/tty`` to read its size and handle ``SIGWINCH``, so without a controlling terminal it stays
    stuck at 80x24 and ignores every resize. The master is left non-blocking so
    :meth:`TerminalHost.read_nonblocking` never stalls the serving loop.
    """
    master_fd, slave_fd = pty.openpty()
    with contextlib.suppress(OSError):
        # Seed a sane winsize before the child execs so tmux doesn't default to 0x0; the browser's
        # first resize (TerminalHost.resize) overrides it once the WebSocket is open.
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

    return PtyProcess(master_fd=master_fd, pid=proc.pid, terminate=_terminate, poll=proc.poll)


class TerminalHost:
    """A registry of live tmux-wrapped PTY sessions for the dashboard.

    The backend half of Mode B2: :meth:`open` launches a persistent harness terminal,
    :meth:`write`/:meth:`read_nonblocking` bridge browser keystrokes <-> child output, and
    :meth:`resize` propagates the xterm.js viewport to the PTY (and thus the child via SIGWINCH).
    The WebSocket endpoint (slice 6d-2) is the live driver; the visual (xterm.js) is slice 6e.
    """

    def __init__(
        self,
        *,
        spawn: Spawner | None = None,
        tmux_probe: TmuxProbe | None = None,
        tmux_killer: TmuxKiller | None = None,
        tmux_creator: TmuxCreator | None = None,
        tmux_configurer: TmuxConfigurer | None = None,
        tmux_mode_canceller: TmuxModeCanceller | None = None,
    ) -> None:
        self._spawn: Spawner = spawn or _spawn_pty
        self._tmux_probe: Callable[[str], TmuxProbeResult] = (
            _tmux_probe_session
            if tmux_probe is None
            else lambda name: _tmux_probe_result_from_bool(tmux_probe(name))
        )
        self._tmux_killer: TmuxKiller = tmux_killer or _tmux_kill_session
        self._tmux_creator: TmuxCreator = tmux_creator or _tmux_create_detached
        self._tmux_configurer: TmuxConfigurer = tmux_configurer or _tmux_enable_mouse
        self._tmux_mode_canceller: TmuxModeCanceller = tmux_mode_canceller or _tmux_cancel_copy_mode
        self._sessions: dict[str, TerminalSession] = {}

    def open(
        self,
        sid: str,
        *,
        cwd: Path | str,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
        suspend_unsafe: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> TerminalSession:
        """Open (or re-attach) the session ``sid`` running ``command`` in ``cwd``.

        Idempotent: a live session for ``sid`` is returned as-is (tmux ``-A`` would re-attach the
        same session anyway); a dead one is reaped and replaced. ``lifecycle_id`` correlates the
        session to a lifecycle/worktree for the registry views. ``env`` seeds spawn env at creation
        (L2 knob injection); it is inert on a re-attach (the durable session keeps its original env).
        """
        existing = self._sessions.get(sid)
        if existing is not None and existing.is_alive:
            return existing
        if existing is not None:
            self._discard(existing)
        session = self._spawn_session(
            sid,
            cwd=cwd,
            command=command,
            lifecycle_id=lifecycle_id,
            name=name,
            suspend_unsafe=suspend_unsafe,
            env=env,
        )
        self._sessions[sid] = session
        return session

    def ensure(
        self,
        sid: str,
        *,
        cwd: Path | str,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
        suspend_unsafe: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> TerminalSessionBinding:
        """Ensure the durable tmux session exists without attaching a PTY client.

        ``env`` seeds spawn env (``tmux new-session -e KEY=VALUE``) when the session is created here;
        it is inert once the session already exists (durable sessions keep their creation env). This
        is the detached path the agent-facing spawn tool composes over, so knob injection lands here.
        """
        root = Path(cwd)
        harness = tuple(command)
        tmux_name = name or _tmux_session_name(sid)
        if not self.has_session(tmux_name):
            self._tmux_creator(tmux_name, root, harness, env or {})
        # Session options are (re-)asserted on every ensure so pre-existing durable sessions
        # created before an option was introduced pick it up too.
        self._tmux_configurer(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=root,
            command=harness,
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )

    def attach(
        self,
        sid: str,
        *,
        cwd: Path | str,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
        suspend_unsafe: bool = False,
    ) -> TerminalSession:
        """Attach one unregistered PTY client to the durable tmux session ``sid``.

        Browser tabs cannot share one PTY master fd: reads race and one tab closing would close the
        client's fd out from under another. Each WebSocket therefore gets its own tmux client process
        attached to the same tmux server-side session. The catalog/tmux name remains the durable identity;
        this returned object is a per-connection handle closed with :meth:`close_session`.
        """
        session = self._spawn_session(
            sid,
            cwd=cwd,
            command=command,
            lifecycle_id=lifecycle_id,
            name=name,
            suspend_unsafe=suspend_unsafe,
        )
        # Attach targets an existing durable session, so option assertion cannot race creation here.
        self._tmux_configurer(session.tmux_name)
        return session

    def get(self, sid: str) -> TerminalSession | None:
        """The live session for ``sid``, or ``None`` if never opened / already closed."""
        return self._sessions.get(sid)

    def sessions(self) -> list[TerminalSession]:
        """Every registered session (registry view)."""
        return list(self._sessions.values())

    def has_session(self, tmux_name: str) -> bool:
        """Whether the persistent tmux session exists outside the in-process registry."""
        return self.probe_session(tmux_name).exists

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        """Probe the persistent tmux session and keep the evidence kind."""
        return self._tmux_probe(tmux_name)

    def for_lifecycle(self, lifecycle_id: str) -> list[TerminalSession]:
        """Sessions correlated to ``lifecycle_id`` (the lifecycle/worktree spine)."""
        return [s for s in self._sessions.values() if s.lifecycle_id == lifecycle_id]

    def write(self, sid: str, data: bytes) -> None:
        """Write browser keystrokes to the session's PTY (raises ``KeyError`` if unknown).

        For a **suspend-unsafe** session (a bare-pane harness), Ctrl-Z (:data:`_SUSPEND_BYTE`) is
        stripped first -- it would suspend the harness with no shell to ``fg`` it back, dropping the
        operator's message. A plain shell session keeps Ctrl-Z so its job control still works. The strip
        covers both injected packages and live xterm keystrokes for the harness; an all-Ctrl-Z frame to
        a harness becomes a no-op write, while an unknown sid still raises ``KeyError`` first.
        """
        self.write_session(
            self._require(sid), data
        )  # resolve first so an unknown sid still raises KeyError

    def write_session(self, session: TerminalSession, data: bytes) -> None:
        """Write browser keystrokes to one concrete PTY client.

        Mouse-report-only frames (wheel scrolling) arm ``mouse_seen``: tmux may have entered
        copy-mode, which captures the keyboard. The first non-mouse input afterwards cancels
        copy-mode before it is written, so typing anywhere in the scrollback snaps the view to the
        live bottom and reaches the pane app — at most one cancel per scroll-then-type cycle, and a
        harmless no-op for panes that never entered copy-mode (mouse-aware TUIs).
        """
        if session.suspend_unsafe:
            data = data.replace(_SUSPEND_BYTE, b"")
        if not data:
            return
        if _SGR_MOUSE_EVENT.sub(b"", data) == b"":
            session.mouse_seen = True
        elif session.mouse_seen:
            session.mouse_seen = False
            self._tmux_mode_canceller(session.tmux_name)
        os.write(session.master_fd, data)

    def read_nonblocking(self, sid: str, max_bytes: int = _READ_CHUNK) -> bytes:
        """Drain up to ``max_bytes`` of child output without blocking.

        Returns ``b""`` when no output is pending *and* when the child has exited (the master
        read raises ``OSError``/``EIO`` once the slave closes) -- callers poll :attr:`is_alive`
        to tell the two apart.
        """
        return self.read_session(self._require(sid), max_bytes=max_bytes)

    def read_session(self, session: TerminalSession, max_bytes: int = _READ_CHUNK) -> bytes:
        """Drain one concrete PTY client without blocking."""
        fd = session.master_fd
        try:
            return os.read(fd, max_bytes)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def resize(self, sid: str, *, cols: int, rows: int) -> None:
        """Set the PTY window size (``TIOCSWINSZ`` -> SIGWINCH to the child / tmux client)."""
        self.resize_session(self._require(sid), cols=cols, rows=rows)

    def resize_session(self, session: TerminalSession, *, cols: int, rows: int) -> None:
        """Set one concrete PTY client's window size."""
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)

    def close(self, sid: str) -> None:
        """Detach + reap the session's child and drop it from the registry (no-op if unknown).

        The tmux *server* keeps the underlying session alive (persistence), so a later
        :meth:`open` with the same name re-attaches the still-running harness.
        """
        session = self._sessions.pop(sid, None)
        if session is not None:
            self._discard(session)

    def close_session(self, session: TerminalSession) -> None:
        """Detach + reap one concrete PTY client without mutating the durable session registry."""
        self._discard(session)

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:
        """Explicitly kill a dashboard-owned tmux session and drop any local client."""
        session = self._sessions.pop(sid, None)
        target = session.tmux_name if session is not None else tmux_name
        if target is not None:
            self._tmux_killer(target)
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

    def _spawn_session(
        self,
        sid: str,
        *,
        cwd: Path | str,
        command: Sequence[str],
        lifecycle_id: str | None,
        name: str | None,
        suspend_unsafe: bool,
        env: Mapping[str, str] | None = None,
    ) -> TerminalSession:
        root = Path(cwd)
        harness = tuple(command)
        tmux_name = name or _tmux_session_name(sid)
        process = self._spawn(_build_tmux_command(tmux_name, root, harness, env), root)
        return TerminalSession(
            sid=sid,
            tmux_name=tmux_name,
            cwd=root,
            command=harness,
            lifecycle_id=lifecycle_id,
            process=process,
            suspend_unsafe=suspend_unsafe,
        )

    def _require(self, sid: str) -> TerminalSession:
        session = self._sessions.get(sid)
        if session is None:
            raise KeyError(sid)
        return session
