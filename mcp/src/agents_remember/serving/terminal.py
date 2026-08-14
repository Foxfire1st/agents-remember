"""The dashboard terminal host: a registry of tmux-wrapped PTY sessions, render-not-scrape (slice 6d).

Mode B2 launches the real harness *inside* a dashboard-owned terminal rather than scraping its
output; the raw VT/ANSI byte stream and the client that carries it are
:mod:`agents_remember.serving.terminal_pty`, and every tmux command is
:mod:`agents_remember.serving.terminal_tmux`.

Each session wraps its child in ``tmux [-T sync] new-session -A -s <name>`` so the session *persists*:
the tmux server outlives the dashboard process and the browser connection, so a restart or a
dropped WebSocket re-attaches the same live harness (``-A`` = attach-or-create) instead of
losing it. The host keeps a registry of live sessions correlated to a lifecycle/worktree.

Security posture (the decided B2 model): the spawn is a *fixed argv* (``Sequence[str]``), never
a shell string, so there is no shell-injection surface; the child runs as the dashboard's own OS
user with that user's existing credentials (``~/.claude``, no re-auth); the WebSocket bridge that
drives this host (slice 6d-2) stays ``127.0.0.1``-bound like the rest of ``serving/``.

The PTY/tmux plumbing is the one impure seam, so :class:`TerminalHost` takes an injectable
:class:`TerminalHostSeams`: tests drive a fake command (``cat``) through a real PTY -- exercising
write/read/resize against the kernel -- without needing tmux, and the full tmux wrapping is covered
by a skip-when-unavailable integration test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.serving.terminal_pty import Spawner, TerminalSession, spawn_pty
from agents_remember.serving.terminal_tmux import (
    TmuxConfigurer,
    TmuxCreator,
    TmuxKiller,
    TmuxModeCanceller,
    TmuxProbe,
    TmuxProbeResult,
    build_tmux_command,
    tmux_cancel_copy_mode,
    tmux_create_detached,
    tmux_enable_mouse,
    tmux_kill_session,
    tmux_probe_result_from_bool,
    tmux_probe_session,
    tmux_session_name,
)


@dataclass(frozen=True)
class TerminalHostSeams:
    """The one impure boundary of :class:`TerminalHost`: the PTY spawner and the tmux commands.

    These are not independent switches -- they are a single surface, the host's entire contact with
    the operating system. A test that fakes tmux replaces the surface, not a handful of unrelated
    arguments, so the substitution is visible as one decision at the call site. ``None`` on any
    field keeps that seam's real implementation.
    """

    spawn: Spawner | None = None
    tmux_probe: TmuxProbe | None = None
    tmux_killer: TmuxKiller | None = None
    tmux_creator: TmuxCreator | None = None
    tmux_configurer: TmuxConfigurer | None = None
    tmux_mode_canceller: TmuxModeCanceller | None = None


@dataclass(frozen=True)
class TerminalSessionBinding:
    """Durable tmux session metadata without an attached PTY client."""

    sid: str
    tmux_name: str
    cwd: Path
    command: tuple[str, ...]
    lifecycle_id: str | None
    suspend_unsafe: bool = False


@dataclass(frozen=True)
class TerminalSessionSpec:
    """How ONE hosted tmux session is created: what runs, where, and under whose identity.

    The request half of the pair whose answer is :class:`TerminalSessionBinding` /
    :class:`~agents_remember.serving.terminal_pty.TerminalSession`. :meth:`TerminalHost.open`,
    :meth:`TerminalHost.ensure` and :meth:`TerminalHost.attach` all take the same spec because they
    create (or re-reach) the *same* durable session through different client shapes -- keeping it one
    object is what makes that sameness checkable instead of six parallel parameter lists drifting apart.
    """

    cwd: Path
    command: tuple[str, ...]
    lifecycle_id: str | None = None
    name: str | None = None
    suspend_unsafe: bool = False
    env: Mapping[str, str] | None = None
    """Spawn env seeded at CREATION (``tmux new-session -e KEY=VALUE``, the L2 knob-injection seam);
    inert once the durable session exists, and never used by :meth:`TerminalHost.attach`."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "command", tuple(self.command))

    def tmux_name_for(self, sid: str) -> str:
        """The durable tmux identity for ``sid``: the spec's explicit name or the derived one."""

        return self.name or tmux_session_name(sid)


class TerminalHost:
    """A registry of live tmux-wrapped PTY sessions for the dashboard.

    The backend half of Mode B2: :meth:`open` and :meth:`attach` launch clients against a persistent
    harness terminal, and the client itself
    (:class:`~agents_remember.serving.terminal_pty.TerminalSession`) carries the browser keystrokes
    <-> child output bridge. The WebSocket endpoint (slice 6d-2) is the live driver; the visual
    (xterm.js) is slice 6e.

    Two kinds of answer, and the difference is what the registry means. :meth:`open` REGISTERS a
    session under ``sid``, so :meth:`get`, :meth:`sessions`, :meth:`for_lifecycle`, :meth:`close` and
    :meth:`shutdown` can reach it later. :meth:`attach` deliberately does not: browser tabs cannot
    share one PTY master fd, so each connection owns its client and releases it itself.
    """

    def __init__(self, seams: TerminalHostSeams | None = None) -> None:
        resolved = seams or TerminalHostSeams()
        self._spawn: Spawner = resolved.spawn or spawn_pty
        probe = resolved.tmux_probe
        self._tmux_probe: Callable[[str], TmuxProbeResult] = (
            tmux_probe_session
            if probe is None
            else lambda name: tmux_probe_result_from_bool(probe(name))
        )
        self._tmux_killer: TmuxKiller = resolved.tmux_killer or tmux_kill_session
        self._tmux_creator: TmuxCreator = resolved.tmux_creator or tmux_create_detached
        self._tmux_configurer: TmuxConfigurer = resolved.tmux_configurer or tmux_enable_mouse
        self._tmux_mode_canceller: TmuxModeCanceller = (
            resolved.tmux_mode_canceller or tmux_cancel_copy_mode
        )
        self._sessions: dict[str, TerminalSession] = {}

    def open(self, sid: str, spec: TerminalSessionSpec) -> TerminalSession:
        """Open (or re-attach) the session ``sid`` as described by ``spec``, and register it.

        Idempotent: a live session for ``sid`` is returned as-is (tmux ``-A`` would re-attach the
        same session anyway); a dead one is reaped and replaced. ``spec.lifecycle_id`` correlates the
        session to a lifecycle/worktree for the registry views. ``spec.env`` seeds spawn env at
        creation (L2 knob injection); it is inert on a re-attach (the durable session keeps its
        original env).
        """
        existing = self._sessions.get(sid)
        if existing is not None and existing.is_alive:
            return existing
        if existing is not None:
            existing.close()
        session = self._spawn_session(sid, spec)
        self._sessions[sid] = session
        return session

    def ensure(self, sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
        """Ensure the durable tmux session exists without attaching a PTY client.

        ``spec.env`` seeds spawn env (``tmux new-session -e KEY=VALUE``) when the session is created
        here; it is inert once the session already exists (durable sessions keep their creation env).
        This is the detached path the agent-facing spawn tool composes over, so knob injection lands
        here.
        """
        tmux_name = spec.tmux_name_for(sid)
        if not self.has_session(tmux_name):
            self._tmux_creator(tmux_name, spec.cwd, spec.command, spec.env or {})
        # Session options are (re-)asserted on every ensure so pre-existing durable sessions
        # created before an option was introduced pick it up too.
        self._tmux_configurer(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )

    def attach(self, sid: str, spec: TerminalSessionSpec) -> TerminalSession:
        """Attach one unregistered PTY client to the durable tmux session ``sid``.

        Browser tabs cannot share one PTY master fd: reads race and one tab closing would close the
        client's fd out from under another. Each WebSocket therefore gets its own tmux client process
        attached to the same tmux server-side session. The catalog/tmux name remains the durable identity;
        the returned client is a per-connection handle its holder releases with
        :meth:`~agents_remember.serving.terminal_pty.TerminalSession.close`.
        """
        # Attach reaches an ALREADY-created session, so it never seeds spawn env: the durable
        # session keeps the environment it was created with.
        session = self._spawn_session(sid, replace(spec, env=None))
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

    def close(self, sid: str) -> None:
        """Release the registered client for ``sid`` and drop it from the registry (no-op if unknown).

        The tmux *server* keeps the underlying session alive (persistence), so a later
        :meth:`open` with the same name re-attaches the still-running harness.
        """
        session = self._sessions.pop(sid, None)
        if session is not None:
            session.close()

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:
        """Explicitly kill a dashboard-owned tmux session and drop any local client."""
        session = self._sessions.pop(sid, None)
        target = session.tmux_name if session is not None else tmux_name
        if target is not None:
            self._tmux_killer(target)
        if session is not None:
            session.close()

    def shutdown(self) -> None:
        """Close every registered session (dashboard teardown)."""
        for sid in list(self._sessions):
            self.close(sid)

    def _spawn_session(self, sid: str, spec: TerminalSessionSpec) -> TerminalSession:
        tmux_name = spec.tmux_name_for(sid)
        argv = build_tmux_command(tmux_name, spec.cwd, spec.command, spec.env)
        process = self._spawn(argv, spec.cwd)
        return TerminalSession(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            process=process,
            suspend_unsafe=spec.suspend_unsafe,
            mode_canceller=self._tmux_mode_canceller,
        )
