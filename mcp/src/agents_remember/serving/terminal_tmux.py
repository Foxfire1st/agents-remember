"""Every command the dashboard runs against the ``tmux`` binary, and nothing else.

The dashboard's terminal sessions are durable because tmux owns them: the tmux server outlives the
dashboard process and the browser connection, so a restart or a dropped WebSocket re-attaches the
same live harness instead of losing it. This module is the whole of that contact -- probe, create,
configure, kill, cancel a mode, and build the argv a PTY client attaches with -- so the layers above
it (:mod:`agents_remember.serving.terminal_pty`, :mod:`agents_remember.serving.terminal`) never
spell a tmux command themselves.

Nothing here knows about a PTY, a session registry or an HTTP request: every function takes a tmux
session name and answers about it. Every spawn is a *fixed argv* (``Sequence[str]``), never a shell
string, so there is no shell-injection surface (the decided B2 posture).
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

_TMUX_COMMAND_TIMEOUT = 5.0
"""Seconds one tmux command may run before it is abandoned.

These are short administrative queries on serving paths (attach, liveness sweep), so a wedged tmux
server must not stall the caller."""

_TMUX_NAME_PREFIX = "ar"
"""Prefix for derived tmux session names, namespacing dashboard sessions on the tmux server."""

_UNSAFE_TMUX_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
"""tmux session names cannot contain ``.`` or ``:``; everything outside this class is collapsed."""

_TMUX_VERSION = re.compile(r"\btmux\s+(?:next-)?(\d+)\.(\d+)")
"""The ``major.minor`` pair inside ``tmux -V`` output (``tmux 3.4``, ``tmux 3.2a``, ``tmux next-3.6``).

Deliberately narrow: builds that report no numeric release (``tmux master``, OpenBSD base's
``tmux openbsd-7.5``) do not match and are treated as *unknown*, never as new-enough. A capability we
cannot prove is one we must not assert on the argv."""

_TMUX_CLIENT_CAPABILITY_MIN_VERSION = (3, 2)
"""First tmux release accepting the client-capability global ``-T <capabilities>``.

Introduced in tmux 3.2 ("CHANGES FROM 3.1c TO 3.2"); 3.1c's own option string carries no ``T``. tmux
rejects an unknown global *hard* -- usage block, exit 1, no session -- so passing ``-T`` to an older
binary would kill every browser attach rather than costing a redraw optimisation."""


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


def tmux_client_environment(parent: Mapping[str, str]) -> dict[str, str]:
    """Construct the environment for a dashboard-owned tmux client process.

    The dashboard daemon may itself have been launched from inside tmux, but that outer client is
    not the terminal identity of any child tmux command the dashboard runs. Remove the launcher's
    tmux identity and declare the terminal grammar the browser PTY implements while retaining
    unrelated credentials and process settings. This applies equally to the attached PTY client and
    the administrative clients that must find/configure its server-side session before attachment.
    """
    child = dict(parent)
    child.pop("TMUX", None)
    child.pop("TMUX_PANE", None)
    child["TERM"] = "xterm-256color"
    return child


def _parse_tmux_version(text: str) -> tuple[int, int] | None:
    """Extract ``(major, minor)`` from ``tmux -V`` output, or ``None`` when it is not a numeric release.

    Pure, so the whole version gate is testable without an old tmux binary on the box.
    """
    match = _TMUX_VERSION.search(text)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)))


@lru_cache(maxsize=1)
def _tmux_version() -> tuple[int, int] | None:
    """The local tmux release, probed once per process (``None`` when tmux is absent/unparseable).

    Cached: this gates the argv of *every* browser attach, and a subprocess per attach would put a
    fork on the WebSocket connect path for an answer that cannot change while the daemon runs.
    """
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_TMUX_COMMAND_TIMEOUT,
            env=tmux_client_environment(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        # Pre-1.4 tmux has no -V at all (usage + non-zero); unknown, so assume nothing.
        return None
    return _parse_tmux_version(result.stdout if isinstance(result.stdout, str) else "")


def _tmux_supports_client_capabilities(version: tuple[int, int] | None) -> bool:
    """Whether this tmux accepts ``-T`` (unknown versions answer ``False``).

    Below the floor the synchronized-output framing is simply unavailable -- a redraw the browser may
    see mid-repaint, which is a cosmetic loss, unlike the doomed argv it replaces. Note ``new-session
    -e`` (:func:`_env_flags`) shipped in the same 3.2 release and carries its own, pre-existing floor
    on the detached-create path; this probe covers only the client-capability assertion.
    """
    return version is not None and version >= _TMUX_CLIENT_CAPABILITY_MIN_VERSION


def tmux_probe_session(name: str) -> TmuxProbeResult:
    """Whether tmux knows ``name``, preserving why a negative probe happened.

    A separate probe from ``new-session -A``, and durable rehydrate needs it to be:
    ``-A`` would CREATE a fresh session when ``name`` is gone, which reports a stale
    catalog row as resumed. ``TerminalHost.has_session`` is the caller; a boolean-only
    ``_tmux_has_session`` wrapper sat here until 260731-EFA-L6 and had none, and dropping
    the evidence kind is exactly what a caller must not do -- see
    :class:`TmuxProbeResult`.
    """
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_TMUX_COMMAND_TIMEOUT,
            env=tmux_client_environment(os.environ),
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


def tmux_probe_result_from_bool(exists: bool) -> TmuxProbeResult:
    evidence: TmuxProbeEvidence = "alive" if exists else "pane-gone"
    return TmuxProbeResult(exists=exists, evidence=evidence)


def tmux_kill_session(name: str) -> None:
    """Kill tmux session ``name``; no-op when tmux or the session is absent."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["tmux", "kill-session", "-t", name],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TMUX_COMMAND_TIMEOUT,
            env=tmux_client_environment(os.environ),
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


def tmux_create_detached(
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
        timeout=_TMUX_COMMAND_TIMEOUT,
        env=tmux_client_environment(os.environ),
    )


def tmux_enable_mouse(name: str) -> None:
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
            timeout=_TMUX_COMMAND_TIMEOUT,
            env=tmux_client_environment(os.environ),
        )


def tmux_cancel_copy_mode(name: str) -> None:
    """Leave copy-mode on session ``name``; harmless error when no mode is active.

    Wheel scrolling under ``mouse on`` enters tmux copy-mode, which captures the keyboard: typed
    letters are (mostly unbound) copy-mode keys, so they never reach the pane app until the operator
    scrolls back to the bottom. tmux has no any-key-cancels binding, so the caller cancels explicitly
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
            timeout=_TMUX_COMMAND_TIMEOUT,
            env=tmux_client_environment(os.environ),
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
            timeout=_TMUX_COMMAND_TIMEOUT,
            text=True,
            env=tmux_client_environment(os.environ),
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
    mode_canceller: TmuxModeCanceller = tmux_cancel_copy_mode,
) -> bool:
    """Cancel copy mode when present and prove the exact pane left it before input."""
    mode = mode_probe(name)
    if mode is None:
        return False
    if mode:
        mode_canceller(name)
        return mode_probe(name) is False
    return True


def tmux_session_name(sid: str) -> str:
    """The deterministic tmux identity for an arbitrary session id.

    Callable before a session exists, so a catalog row and the pane it names can be correlated
    without launching anything.
    """
    safe = _UNSAFE_TMUX_CHARS.sub("-", sid).strip("-")
    return f"{_TMUX_NAME_PREFIX}-{safe or 'session'}"


def build_tmux_command(
    name: str,
    cwd: Path,
    harness: Sequence[str],
    env: Mapping[str, str] | None = None,
    *,
    client_capabilities: bool | None = None,
) -> list[str]:
    """The persistent-session argv: ``tmux [-T sync] new-session -A -s <name> ...``.

    Pure given an explicit ``client_capabilities``; the ``None`` default consults the process-cached
    :func:`_tmux_version` probe, so the command construction stays unit-testable on its own. ``-A``
    attaches to an existing session of that name (persistence) or creates it; the optional
    ``-e KEY=VALUE`` flags seed spawn env (L2 knob injection); ``--`` ends tmux's option parsing so the
    fixed harness argv is never reinterpreted as tmux flags. ``-T sync`` is a client-scoped capability
    assertion: xterm 6 implements DEC synchronized output, so tmux can frame each pane redraw
    atomically instead of exposing its intermediate top-to-bottom rewrite to the browser. It is
    omitted below tmux 3.2 (:data:`_TMUX_CLIENT_CAPABILITY_MIN_VERSION`), where the flag does not
    exist and would abort the spawn -- this is the sole client-attaching argv, so a hard dependency
    here would cost *all* terminal function on such a host rather than one redraw optimisation.
    """
    if client_capabilities is None:
        client_capabilities = _tmux_supports_client_capabilities(_tmux_version())
    return [
        "tmux",
        *(("-T", "sync") if client_capabilities else ()),
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
