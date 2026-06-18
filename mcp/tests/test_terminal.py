"""Tests for the dashboard terminal host (slice 6d-1, ``serving.terminal``).

Three layers, mirroring the host's one impure seam:

* **Pure command construction** -- ``_build_tmux_command`` / ``_tmux_session_name`` have no I/O.
* **Registry** -- a pipe-backed *fake* spawner exercises open/idempotency/replace/close/lookup
  without a real child or tmux.
* **Real PTY** -- a spawner that unwraps the tmux argv and runs the bare harness on a real
  :func:`pty.openpty` master drives write/read/resize/exit against the kernel (no tmux needed).
* **tmux integration** -- one end-to-end test through the real default spawner, skipped when
  ``tmux`` is unavailable (CI-safe, per the slice plan).
"""

from __future__ import annotations

import contextlib
import fcntl
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
        self.host = TerminalHost(spawn=self.spawner)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        self.host.shutdown()

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

    @unittest.skipUnless(_HAS_TRUE, "needs `true` for an immediately-exiting child")
    def test_read_empty_after_child_exit(self) -> None:
        self.host.open("done", cwd=self.tmp, command=["true"])
        _wait_dead(self.host, "done")
        session = self.host.get("done")
        assert session is not None
        self.assertFalse(session.is_alive)
        self.assertEqual(self.host.read_nonblocking("done"), b"")


@unittest.skipUnless(_HAS_TMUX, "needs tmux for the end-to-end persistence path")
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
