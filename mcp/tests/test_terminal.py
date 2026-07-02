"""Tests for the dashboard terminal host (slice 6d-1, ``serving.terminal``).

Three layers, mirroring the host's one impure seam:

* **Pure command construction** -- ``_build_tmux_command`` / ``_tmux_session_name`` have no I/O.
* **Registry** -- a pipe-backed *fake* spawner exercises open/idempotency/replace/close/lookup
  without a real child or tmux.
* **Real PTY** -- a spawner that unwraps the tmux argv and runs the bare harness on a real
  :func:`pty.openpty` master drives write/read/resize/exit against the kernel (no tmux needed).
* **tmux integration** -- one end-to-end test through the real default spawner, skipped when
  ``tmux`` or a tmux-usable terminal capability is unavailable (CI-safe, per the slice plan).
"""

from __future__ import annotations

import contextlib
import fcntl
import importlib
import os
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from collections.abc import Sequence
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.terminal import (
    PtyProcess,
    TerminalHost,
    _build_tmux_command,
    _spawn_pty,
    _tmux_session_name,
)

_HAS_TMUX = shutil.which("tmux") is not None
_HAS_CAT = shutil.which("cat") is not None
_HAS_TRUE = shutil.which("true") is not None


def _term_supports_clear() -> bool:
    """Whether tmux can initialize against the current terminal database."""
    term = os.environ.get("TERM")
    if not term:
        return False
    try:
        curses = importlib.import_module("curses")
    except ImportError:
        return False
    try:
        curses.setupterm()
    except curses.error:
        return False
    return curses.tigetstr("clear") is not None


_HAS_TMUX_TERMINAL = _HAS_TMUX and _term_supports_clear()


class _FakeChild:
    """A pipe-backed stand-in for a spawned child: a real master fd, no real process."""

    def __init__(self) -> None:
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        self.returncode: int | None = None

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15
            with contextlib.suppress(OSError):
                os.close(self.write_fd)

    def poll(self) -> int | None:
        return self.returncode


class _FakeSpawner:
    """Records the argv it was handed and backs each session with a :class:`_FakeChild`."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.children: list[_FakeChild] = []

    def __call__(self, argv: Sequence[str], _cwd: Path) -> PtyProcess:
        self.calls.append(list(argv))
        child = _FakeChild()
        self.children.append(child)
        return PtyProcess(
            master_fd=child.read_fd,
            pid=4242 + len(self.children),
            terminate=child.terminate,
            poll=child.poll,
        )


def _raw_spawn(argv: Sequence[str], cwd: Path) -> PtyProcess:
    """A spawner that drops the tmux wrapper and runs the bare harness on a real PTY.

    Lets the host's write/read/resize methods hit a real ``pty.openpty`` master without needing
    tmux installed -- the kernel PTY is the thing under test here, not the tmux client.
    """
    harness = list(argv[argv.index("--") + 1 :])
    return _spawn_pty(harness, cwd)


def _read_until(host: TerminalHost, sid: str, marker: bytes, timeout: float = 10.0) -> bytes:
    """Accumulate PTY output until ``marker`` appears or ``timeout`` elapses."""
    session = host.get(sid)
    assert session is not None
    fd = session.master_fd
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and marker not in buf:
        readable, _, _ = select.select([fd], [], [], 0.2)
        if readable:
            buf.extend(host.read_nonblocking(sid))
    return bytes(buf)


def _wait_dead(host: TerminalHost, sid: str, timeout: float = 5.0) -> None:
    session = host.get(sid)
    assert session is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and session.is_alive:
        time.sleep(0.02)


class BuildCommandTests(unittest.TestCase):
    def test_build_tmux_command_wraps_harness(self) -> None:
        argv = _build_tmux_command("ar-lc1", Path("/work/tree"), ["claude", "--resume"])
        self.assertEqual(
            argv,
            [
                "tmux", "new-session", "-A", "-s", "ar-lc1",
                "-c", "/work/tree", "--", "claude", "--resume",
            ],
        )

    def test_session_name_sanitizes_unsafe_chars(self) -> None:
        # tmux names cannot carry "." or ":" -- both collapse to a single hyphen.
        self.assertEqual(_tmux_session_name("lifecycle.42:closeout"), "ar-lifecycle-42-closeout")

    def test_session_name_falls_back_when_all_unsafe(self) -> None:
        self.assertEqual(_tmux_session_name("..."), "ar-session")


class TerminalHostRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spawner = _FakeSpawner()
        self.existing_tmux: set[str] = set()
        self.killed_tmux: list[str] = []
        self.created_tmux: list[tuple[str, Path, tuple[str, ...]]] = []
        self.configured_tmux: list[str] = []
        self.host = TerminalHost(
            spawn=self.spawner,
            tmux_probe=self.existing_tmux.__contains__,
            tmux_killer=self.killed_tmux.append,
            tmux_creator=self._create_tmux,
            tmux_configurer=self.configured_tmux.append,
        )
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        self.host.shutdown()

    def _create_tmux(self, name: str, cwd: Path, command: Sequence[str]) -> None:
        self.created_tmux.append((name, cwd, tuple(command)))
        self.existing_tmux.add(name)

    def test_open_builds_tmux_command_and_registers(self) -> None:
        session = self.host.open(
            "lc1", cwd=self.tmp, command=["claude"], lifecycle_id="LC-1"
        )
        self.assertEqual(self.spawner.calls[0][:4], ["tmux", "new-session", "-A", "-s"])
        self.assertEqual(self.spawner.calls[0][-2:], ["--", "claude"])
        self.assertEqual(session.tmux_name, "ar-lc1")
        self.assertEqual(session.lifecycle_id, "LC-1")
        self.assertIs(self.host.get("lc1"), session)
        self.assertEqual(self.host.sessions(), [session])
        self.assertEqual(self.host.for_lifecycle("LC-1"), [session])
        self.assertEqual(self.host.for_lifecycle("other"), [])

    def test_open_is_idempotent_for_live_session(self) -> None:
        first = self.host.open("lc1", cwd=self.tmp, command=["cat"])
        second = self.host.open("lc1", cwd=self.tmp, command=["cat"])
        self.assertIs(first, second)
        self.assertEqual(len(self.spawner.calls), 1)

    def test_attach_creates_unregistered_per_connection_clients(self) -> None:
        durable = self.host.open("lc1", cwd=self.tmp, command=["cat"], lifecycle_id="LC-1")
        first = self.host.attach(
            "lc1", cwd=self.tmp, command=["cat"], lifecycle_id="LC-1", name=durable.tmux_name
        )
        second = self.host.attach(
            "lc1", cwd=self.tmp, command=["cat"], lifecycle_id="LC-1", name=durable.tmux_name
        )

        self.assertIsNot(first, durable)
        self.assertIsNot(second, first)
        self.assertEqual(self.host.sessions(), [durable])
        # Every attach re-asserts session options against the existing durable session.
        self.assertEqual(self.configured_tmux, [durable.tmux_name, durable.tmux_name])
        self.host.close_session(first)
        self.assertIs(self.host.get("lc1"), durable)
        self.assertTrue(second.is_alive)

    def test_ensure_creates_detached_tmux_without_registered_client(self) -> None:
        binding = self.host.ensure("lc1", cwd=self.tmp, command=["cat"], lifecycle_id="LC-1")

        self.assertEqual(binding.sid, "lc1")
        self.assertEqual(binding.tmux_name, "ar-lc1")
        self.assertEqual(binding.cwd, self.tmp)
        self.assertEqual(binding.command, ("cat",))
        self.assertEqual(binding.lifecycle_id, "LC-1")
        self.assertEqual(self.created_tmux, [("ar-lc1", self.tmp, ("cat",))])
        self.assertEqual(self.configured_tmux, ["ar-lc1"])  # session options asserted post-create
        self.assertIsNone(self.host.get("lc1"))
        self.assertEqual(self.host.sessions(), [])

    def test_ensure_is_idempotent_when_tmux_session_exists(self) -> None:
        self.existing_tmux.add("ar-lc1")

        binding = self.host.ensure("lc1", cwd=self.tmp, command=["cat"])

        self.assertEqual(binding.tmux_name, "ar-lc1")
        self.assertEqual(self.created_tmux, [])
        # Options are re-asserted on ensure so durable sessions predating an option pick it up.
        self.assertEqual(self.configured_tmux, ["ar-lc1"])

    def test_open_replaces_dead_session(self) -> None:
        first = self.host.open("lc1", cwd=self.tmp, command=["cat"])
        self.spawner.children[0].terminate()  # simulate the child exiting
        self.assertFalse(first.is_alive)
        second = self.host.open("lc1", cwd=self.tmp, command=["cat"])
        self.assertIsNot(first, second)
        self.assertEqual(len(self.spawner.calls), 2)
        self.assertTrue(second.is_alive)

    def test_close_unregisters(self) -> None:
        self.host.open("lc1", cwd=self.tmp, command=["cat"])
        self.host.close("lc1")
        self.assertIsNone(self.host.get("lc1"))
        self.assertEqual(self.host.sessions(), [])

    def test_close_unknown_is_noop(self) -> None:
        self.host.close("never-opened")  # must not raise

    def test_write_unknown_session_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.host.write("ghost", b"x")

    def test_resize_unknown_session_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.host.resize("ghost", cols=80, rows=24)

    def test_custom_name_overrides_derived(self) -> None:
        session = self.host.open("lc1", cwd=self.tmp, command=["cat"], name="custom")
        self.assertEqual(session.tmux_name, "custom")
        self.assertEqual(self.spawner.calls[0][4], "custom")

    def test_has_session_uses_tmux_probe(self) -> None:
        self.existing_tmux.add("ar-lc1")
        self.assertTrue(self.host.has_session("ar-lc1"))
        self.assertFalse(self.host.has_session("ar-missing"))

    def test_terminate_kills_tmux_and_unregisters(self) -> None:
        self.host.open("lc1", cwd=self.tmp, command=["cat"])
        self.host.terminate("lc1")
        self.assertEqual(self.killed_tmux, ["ar-lc1"])
        self.assertIsNone(self.host.get("lc1"))
        self.assertEqual(self.host.sessions(), [])

    def test_terminate_unknown_uses_supplied_tmux_name(self) -> None:
        self.host.terminate("ghost", tmux_name="ar-ghost")
        self.assertEqual(self.killed_tmux, ["ar-ghost"])


@unittest.skipUnless(_HAS_CAT, "needs `cat` for a real PTY child")
class TerminalHostPtyTests(unittest.TestCase):
    """Drive the host against a real kernel PTY (tmux wrapper stripped by ``_raw_spawn``)."""

    def setUp(self) -> None:
        self.host = TerminalHost(spawn=_raw_spawn)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        self.host.shutdown()

    def test_write_then_read_roundtrip(self) -> None:
        self.host.open("io", cwd=self.tmp, command=["cat"])
        self.host.write("io", b"ar-terminal\n")  # cat echoes the line back through the PTY
        self.assertIn(b"ar-terminal", _read_until(self.host, "io", b"ar-terminal"))

    def test_read_nonblocking_returns_empty_when_idle(self) -> None:
        self.host.open("io", cwd=self.tmp, command=["cat"])  # cat waits for stdin
        self.assertEqual(self.host.read_nonblocking("io"), b"")

    def test_resize_sets_pty_winsize(self) -> None:
        session = self.host.open("io", cwd=self.tmp, command=["cat"])
        self.host.resize("io", cols=120, rows=40)
        packed = fcntl_ioctl_getwinsize(session.master_fd)
        rows, cols, _, _ = struct.unpack("HHHH", packed)
        self.assertEqual((rows, cols), (40, 120))

    def test_spawn_seeds_default_winsize(self) -> None:
        # A freshly opened PTY carries the seeded default (not 0x0) before any browser resize lands,
        # so tmux never starts degenerate; the real size follows from the first resize.
        session = self.host.open("io", cwd=self.tmp, command=["cat"])
        packed = fcntl_ioctl_getwinsize(session.master_fd)
        rows, cols, _, _ = struct.unpack("HHHH", packed)
        self.assertEqual((rows, cols), (24, 80))

    def test_harness_write_strips_ctrl_z_suspend_byte(self) -> None:
        # For a suspend-unsafe (bare-pane harness) session, 0x1a (Ctrl-Z) is dropped before it reaches
        # the PTY -- it would suspend the harness with no shell to `fg` it back. `cat` echoes whatever it
        # receives, so the readback proves the byte never arrived (the surrounding "a"/"b" are contiguous).
        self.host.open("z", cwd=self.tmp, command=["cat"], suspend_unsafe=True)
        self.host.write("z", b"a\x1ab\n")
        out = _read_until(self.host, "z", b"ab")
        self.assertIn(b"ab", out)
        self.assertNotIn(b"\x1a", out)

    def test_harness_write_all_ctrl_z_is_noop(self) -> None:
        # An all-Ctrl-Z frame to a harness collapses to empty and must not write (or raise) -- nothing cat.
        self.host.open("z2", cwd=self.tmp, command=["cat"], suspend_unsafe=True)
        self.host.write("z2", b"\x1a\x1a")
        self.assertEqual(self.host.read_nonblocking("z2"), b"")

    @unittest.skipUnless(_HAS_TRUE, "needs `true` for an immediately-exiting child")
    def test_read_empty_after_child_exit(self) -> None:
        self.host.open("done", cwd=self.tmp, command=["true"])
        _wait_dead(self.host, "done")
        session = self.host.get("done")
        assert session is not None
        self.assertFalse(session.is_alive)
        self.assertEqual(self.host.read_nonblocking("done"), b"")


class _PipeWriteSpawner:
    """Backs each session's ``master_fd`` with the WRITE end of a pipe so a test reads back exactly the
    bytes :meth:`TerminalHost.write` forwarded -- no PTY line discipline, so 0x1a is never consumed as a
    signal. The complement of :class:`_FakeSpawner` (which exposes a readable master)."""

    def __init__(self) -> None:
        self.read_fds: list[int] = []

    def __call__(self, _argv: Sequence[str], _cwd: Path) -> PtyProcess:
        read_fd, write_fd = os.pipe()
        self.read_fds.append(read_fd)
        return PtyProcess(
            master_fd=write_fd, pid=7000 + len(self.read_fds), terminate=lambda: None, poll=lambda: None
        )


class TerminalHostSuspendScopingTests(unittest.TestCase):
    """The 0x1a (Ctrl-Z) strip is scoped to suspend-unsafe harness sessions; shells keep job control."""

    def setUp(self) -> None:
        self.spawner = _PipeWriteSpawner()
        self.host = TerminalHost(spawn=self.spawner)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        self.host.shutdown()
        for read_fd in self.spawner.read_fds:
            with contextlib.suppress(OSError):
                os.close(read_fd)

    def test_harness_session_strips_ctrl_z(self) -> None:
        self.host.open("h", cwd=self.tmp, command=["claude"], suspend_unsafe=True)
        self.host.write("h", b"a\x1ab")
        self.assertEqual(os.read(self.spawner.read_fds[0], 64), b"ab")

    def test_shell_session_keeps_ctrl_z(self) -> None:
        # A plain shell (suspend_unsafe defaults False) must NOT lose Ctrl-Z -- it is the legitimate
        # job-control keystroke to background a foreground program back to the prompt.
        self.host.open("s", cwd=self.tmp, command=["bash"])
        self.host.write("s", b"a\x1ab")
        self.assertEqual(os.read(self.spawner.read_fds[0], 64), b"a\x1ab")

    def test_harness_all_ctrl_z_writes_nothing(self) -> None:
        self.host.open("h2", cwd=self.tmp, command=["claude"], suspend_unsafe=True)
        self.host.write("h2", b"\x1a\x1a")  # collapses to empty -> no os.write
        os.set_blocking(self.spawner.read_fds[0], False)
        with self.assertRaises(BlockingIOError):
            os.read(self.spawner.read_fds[0], 64)

    def test_unknown_session_all_ctrl_z_still_raises(self) -> None:
        # _require runs BEFORE the strip, so an all-0x1a frame to an unknown sid still raises -- pins the
        # require-before-strip ordering the suppress(KeyError) on the WS path would otherwise hide.
        with self.assertRaises(KeyError):
            self.host.write("ghost", b"\x1a\x1a")


class TerminalHostCopyModeCancelTests(unittest.TestCase):
    """Typing after wheel scrolling cancels tmux copy-mode ONCE so keys land in the pane app at the
    live bottom; mouse reports themselves pass through without cancelling."""

    def setUp(self) -> None:
        self.spawner = _PipeWriteSpawner()
        self.cancelled: list[str] = []
        self.host = TerminalHost(spawn=self.spawner, tmux_mode_canceller=self.cancelled.append)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        self.host.shutdown()
        for read_fd in self.spawner.read_fds:
            with contextlib.suppress(OSError):
                os.close(read_fd)

    def test_mouse_reports_pass_through_without_cancel(self) -> None:
        self.host.open("m", cwd=self.tmp, command=["codex"])
        self.host.write("m", b"\x1b[<64;10;5M\x1b[<65;10;6m")
        self.assertEqual(self.cancelled, [])
        self.assertEqual(os.read(self.spawner.read_fds[0], 64), b"\x1b[<64;10;5M\x1b[<65;10;6m")

    def test_typing_without_prior_mouse_never_cancels(self) -> None:
        self.host.open("m", cwd=self.tmp, command=["codex"])
        self.host.write("m", b"hello")
        self.assertEqual(self.cancelled, [])
        self.assertEqual(os.read(self.spawner.read_fds[0], 64), b"hello")

    def test_first_typing_after_mouse_cancels_copy_mode_once(self) -> None:
        self.host.open("m", cwd=self.tmp, command=["codex"])
        self.host.write("m", b"\x1b[<64;10;5M")  # wheel-up: tmux may now be in copy-mode
        self.host.write("m", b"h")  # first typed byte cancels, then passes through
        self.host.write("m", b"i")  # further typing does not cancel again
        self.assertEqual(self.cancelled, ["ar-m"])
        self.assertEqual(os.read(self.spawner.read_fds[0], 64), b"\x1b[<64;10;5Mhi")

    def test_mouse_after_typing_rearms_the_cancel(self) -> None:
        self.host.open("m", cwd=self.tmp, command=["codex"])
        self.host.write("m", b"\x1b[<64;10;5M")
        self.host.write("m", b"a")
        self.host.write("m", b"\x1b[<64;10;5M")
        self.host.write("m", b"b")
        self.assertEqual(self.cancelled, ["ar-m", "ar-m"])

    def test_mixed_mouse_and_typing_frame_counts_as_typing(self) -> None:
        # A frame that is not purely mouse reports is treated as typing: cancel while armed.
        self.host.open("m", cwd=self.tmp, command=["codex"])
        self.host.write("m", b"\x1b[<64;10;5M")
        self.host.write("m", b"\x1b[<64;10;5Mx")
        self.assertEqual(self.cancelled, ["ar-m"])


@unittest.skipUnless(
    _HAS_TMUX_TERMINAL,
    "needs tmux and a TERM entry with clear capability for the end-to-end persistence path",
)
class TerminalHostTmuxIntegrationTests(unittest.TestCase):
    """The full default spawner: a real tmux-wrapped PTY running a marker-printing child."""

    def setUp(self) -> None:
        self.host = TerminalHost()  # real _spawn_pty -> real tmux
        self.tmp = Path(tempfile.mkdtemp())
        self.tmux_name: str | None = None

    def tearDown(self) -> None:
        self.host.shutdown()
        if self.tmux_name is not None:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["tmux", "kill-session", "-t", self.tmux_name],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )

    def test_real_tmux_session_renders_child_output(self) -> None:
        session = self.host.open(
            "tmux-it",
            cwd=self.tmp,
            command=["sh", "-c", "printf AR_READY_MARKER; sleep 2"],
            lifecycle_id="LC-tmux",
        )
        self.tmux_name = session.tmux_name
        self.assertIn(b"AR_READY_MARKER", _read_until(self.host, "tmux-it", b"AR_READY_MARKER"))
        self.assertEqual(self.host.for_lifecycle("LC-tmux"), [session])


def fcntl_ioctl_getwinsize(fd: int) -> bytes:
    """Read the PTY window size (``TIOCGWINSZ``) -- inverse of the host's resize ioctl."""
    return fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
