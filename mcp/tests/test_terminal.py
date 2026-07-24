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
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.terminal import (
    PtyProcess,
    TerminalHost,
    TerminalSession,
    _build_tmux_command,
    _parse_tmux_version,
    _spawn_pty,
    _tmux_cancel_copy_mode,
    _tmux_client_environment,
    _tmux_create_detached,
    _tmux_enable_mouse,
    _tmux_kill_session,
    _tmux_probe_session,
    _tmux_session_name,
    _tmux_supports_client_capabilities,
    _tmux_version,
    ensure_terminal_input_ready,
    pane_in_mode,
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
    return _read_until_session(host, session, marker, timeout)


def _read_until_session(
    host: TerminalHost, session: TerminalSession, marker: bytes, timeout: float = 10.0
) -> bytes:
    """Accumulate output from one concrete (possibly unregistered) PTY client."""
    fd = session.master_fd
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and marker not in buf:
        readable, _, _ = select.select([fd], [], [], 0.2)
        if readable:
            buf.extend(host.read_session(session))
    return bytes(buf)


def _wait_dead(host: TerminalHost, sid: str, timeout: float = 5.0) -> None:
    session = host.get(sid)
    assert session is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and session.is_alive:
        time.sleep(0.02)


class BuildCommandTests(unittest.TestCase):
    def test_tmux_client_environment_owns_terminal_identity(self) -> None:
        child = _tmux_client_environment(
            {
                "TMUX": "/tmp/tmux/default,12,3",
                "TMUX_PANE": "%9",
                "TERM": "dumb",
                "AR_KEEP": "required",
            }
        )
        self.assertNotIn("TMUX", child)
        self.assertNotIn("TMUX_PANE", child)
        self.assertEqual(child["TERM"], "xterm-256color")
        self.assertEqual(child["AR_KEEP"], "required")

    def test_every_terminal_host_tmux_client_uses_owned_identity(self) -> None:
        calls: list[dict[str, object]] = []

        def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(kwargs)
            stdout = "0\n" if "display-message" in argv else ""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        contaminated = {
            "TMUX": "/tmp/tmux/default,12,3",
            "TMUX_PANE": "%9",
            "TERM": "dumb",
            "AR_KEEP": "required",
        }
        with (
            mock.patch.dict(os.environ, contaminated, clear=False),
            mock.patch("agents_remember.serving.terminal.subprocess.run", side_effect=run),
        ):
            self.assertTrue(_tmux_probe_session("ar-worker").exists)
            _tmux_kill_session("ar-worker")
            _tmux_create_detached("ar-worker", Path("/work/tree"), ["cat"])
            _tmux_enable_mouse("ar-worker")
            _tmux_cancel_copy_mode("ar-worker")
            self.assertFalse(pane_in_mode("ar-worker"))

        self.assertEqual(len(calls), 6)
        for call in calls:
            child = call["env"]
            self.assertIsInstance(child, dict)
            assert isinstance(child, dict)
            self.assertNotIn("TMUX", child)
            self.assertNotIn("TMUX_PANE", child)
            self.assertEqual(child["TERM"], "xterm-256color")
            self.assertEqual(child["AR_KEEP"], "required")

    def test_build_tmux_command_wraps_harness(self) -> None:
        argv = _build_tmux_command(
            "ar-lc1", Path("/work/tree"), ["claude", "--resume"], client_capabilities=True
        )
        self.assertEqual(
            argv,
            [
                "tmux",
                "-T",
                "sync",
                "new-session",
                "-A",
                "-s",
                "ar-lc1",
                "-c",
                "/work/tree",
                "--",
                "claude",
                "--resume",
            ],
        )

    def test_build_tmux_command_omits_capability_flag_without_support(self) -> None:
        # tmux < 3.2 has no -T global and rejects unknown globals hard (usage, exit 1, no session),
        # so the client-capability assertion must vanish rather than take the whole attach down.
        argv = _build_tmux_command(
            "ar-lc1", Path("/work/tree"), ["claude", "--resume"], client_capabilities=False
        )
        self.assertEqual(
            argv,
            [
                "tmux",
                "new-session",
                "-A",
                "-s",
                "ar-lc1",
                "-c",
                "/work/tree",
                "--",
                "claude",
                "--resume",
            ],
        )

    def test_build_tmux_command_consults_version_probe_by_default(self) -> None:
        # The default path is the one every browser attach takes: no explicit capability argument.
        with mock.patch("agents_remember.serving.terminal._tmux_version", return_value=(3, 1)):
            old = _build_tmux_command("ar-lc1", Path("/work/tree"), ["cat"])
        with mock.patch("agents_remember.serving.terminal._tmux_version", return_value=(3, 2)):
            new = _build_tmux_command("ar-lc1", Path("/work/tree"), ["cat"])
        self.assertNotIn("-T", old)
        self.assertEqual(new[:3], ["tmux", "-T", "sync"])

    def test_parse_tmux_version_reads_release_forms(self) -> None:
        self.assertEqual(_parse_tmux_version("tmux 3.4\n"), (3, 4))
        self.assertEqual(_parse_tmux_version("tmux 3.2a\n"), (3, 2))
        self.assertEqual(_parse_tmux_version("tmux 3.1c\n"), (3, 1))
        self.assertEqual(_parse_tmux_version("tmux 2.8\n"), (2, 8))
        self.assertEqual(_parse_tmux_version("tmux next-3.6\n"), (3, 6))
        # Non-numeric builds are unknown, not new-enough: never assert a capability we cannot prove.
        self.assertIsNone(_parse_tmux_version("tmux master\n"))
        self.assertIsNone(_parse_tmux_version("tmux openbsd-7.5\n"))
        self.assertIsNone(_parse_tmux_version(""))

    def test_client_capability_floor_is_tmux_3_2(self) -> None:
        # -T shipped in tmux 3.2 ("CHANGES FROM 3.1c TO 3.2"); 3.1c's option string carries no T.
        self.assertFalse(_tmux_supports_client_capabilities((3, 1)))
        self.assertFalse(_tmux_supports_client_capabilities((2, 9)))
        self.assertTrue(_tmux_supports_client_capabilities((3, 2)))
        self.assertTrue(_tmux_supports_client_capabilities((3, 4)))
        self.assertTrue(_tmux_supports_client_capabilities((4, 0)))
        # tmux absent or unparseable -- degrade, do not gamble the spawn.
        self.assertFalse(_tmux_supports_client_capabilities(None))

    def test_tmux_version_probes_once_per_process(self) -> None:
        # This gates the argv of every attach; a fork per WebSocket connect is not acceptable, and
        # the answer cannot change while the daemon runs.
        calls: list[Sequence[str]] = []

        def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(argv))
            return subprocess.CompletedProcess(list(argv), 0, stdout="tmux 3.4\n", stderr="")

        _tmux_version.cache_clear()
        try:
            with mock.patch("agents_remember.serving.terminal.subprocess.run", side_effect=run):
                self.assertEqual(_tmux_version(), (3, 4))
                self.assertEqual(_tmux_version(), (3, 4))
        finally:
            _tmux_version.cache_clear()
        self.assertEqual(calls, [["tmux", "-V"]])

    def test_tmux_version_is_none_when_probe_fails(self) -> None:
        # No tmux, a hung tmux, and a pre-1.4 tmux (no -V at all) are all "unknown", never "new enough".
        outcomes: list[Exception | subprocess.CompletedProcess[str]] = [
            FileNotFoundError("tmux"),
            subprocess.TimeoutExpired(["tmux", "-V"], 5.0),
            subprocess.CompletedProcess(["tmux", "-V"], 1, stdout="usage: tmux\n", stderr=""),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                _tmux_version.cache_clear()
                try:
                    with mock.patch(
                        "agents_remember.serving.terminal.subprocess.run",
                        side_effect=[outcome],
                    ):
                        self.assertIsNone(_tmux_version())
                finally:
                    _tmux_version.cache_clear()

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
        self.created_env: list[dict[str, str]] = []
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

    def _create_tmux(
        self, name: str, cwd: Path, command: Sequence[str], env: Mapping[str, str]
    ) -> None:
        self.created_tmux.append((name, cwd, tuple(command)))
        self.created_env.append(dict(env))
        self.existing_tmux.add(name)

    def test_open_builds_tmux_command_and_registers(self) -> None:
        # Pinned to a known-modern tmux so the argv assertion does not depend on the host's binary.
        with mock.patch("agents_remember.serving.terminal._tmux_version", return_value=(3, 4)):
            session = self.host.open("lc1", cwd=self.tmp, command=["claude"], lifecycle_id="LC-1")
        self.assertEqual(
            self.spawner.calls[0][:6], ["tmux", "-T", "sync", "new-session", "-A", "-s"]
        )
        self.assertEqual(self.spawner.calls[0][-2:], ["--", "claude"])
        self.assertEqual(session.tmux_name, "ar-lc1")
        self.assertEqual(session.lifecycle_id, "LC-1")
        self.assertIs(self.host.get("lc1"), session)
        self.assertEqual(self.host.sessions(), [session])
        self.assertEqual(self.host.for_lifecycle("LC-1"), [session])
        self.assertEqual(self.host.for_lifecycle("other"), [])

    def test_open_and_attach_drop_capability_flag_on_old_tmux(self) -> None:
        # The whole point of M5: on tmux < 3.2 the client argv must still be *runnable*. -T is the
        # sole client-attaching global here, so keeping it would cost every browser attach (both
        # kinds), not just the synchronized-output framing it buys.
        with mock.patch("agents_remember.serving.terminal._tmux_version", return_value=(3, 1)):
            durable = self.host.open("lc1", cwd=self.tmp, command=["claude"], lifecycle_id="LC-1")
            self.host.attach(
                "lc1", cwd=self.tmp, command=["claude"], lifecycle_id="LC-1", name=durable.tmux_name
            )
        self.assertEqual(len(self.spawner.calls), 2)
        for argv in self.spawner.calls:
            self.assertNotIn("-T", argv)
            self.assertNotIn("sync", argv)
            self.assertEqual(argv[:4], ["tmux", "new-session", "-A", "-s"])

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
        self.assertEqual(self.spawner.calls[0][6], "custom")

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
            master_fd=write_fd,
            pid=7000 + len(self.read_fds),
            terminate=lambda: None,
            poll=lambda: None,
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


class TerminalInputReadinessTests(unittest.TestCase):
    def test_copy_mode_is_cancelled_and_rechecked_before_input(self) -> None:
        modes = iter([True, False])
        cancelled: list[str] = []
        ready = ensure_terminal_input_ready(
            "ar-worker",
            mode_probe=lambda _name: next(modes),
            mode_canceller=cancelled.append,
        )
        self.assertTrue(ready)
        self.assertEqual(cancelled, ["ar-worker"])

    def test_uncleared_or_unobservable_copy_mode_blocks_input(self) -> None:
        for observations in ([True, True], [None]):
            with self.subTest(observations=observations):
                modes = iter(observations)
                self.assertFalse(
                    ensure_terminal_input_ready(
                        "ar-worker",
                        mode_probe=lambda _name, modes=modes: next(modes),
                        mode_canceller=lambda _name: None,
                    )
                )

    def test_pane_mode_probe_parses_only_exact_tmux_flags(self) -> None:
        for stdout, expected in (("1\n", True), ("0\n", False), ("unknown\n", None)):
            with self.subTest(stdout=stdout):
                completed = subprocess.CompletedProcess([], 0, stdout=stdout)
                with mock.patch(
                    "agents_remember.serving.terminal.subprocess.run",
                    return_value=completed,
                ):
                    self.assertIs(pane_in_mode("ar-worker"), expected)


@unittest.skipUnless(
    _HAS_TMUX,
    "needs tmux for the end-to-end persistence path",
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
                    env=_tmux_client_environment(os.environ),
                )

    def test_real_tmux_ensure_and_attach_ignore_launcher_identity(self) -> None:
        contaminated = {
            "TMUX": "/tmp/tmux-1000/default,295199,20",
            "TMUX_PANE": "%21",
            "TERM": "dumb",
        }
        with mock.patch.dict(os.environ, contaminated, clear=False):
            binding = self.host.ensure(
                "tmux-it",
                cwd=self.tmp,
                command=["sh", "-c", "printf AR_READY_MARKER; sleep 10"],
                lifecycle_id="LC-tmux",
            )
            session = self.host.attach(
                "tmux-it",
                cwd=self.tmp,
                command=["sh", "-c", "printf AR_READY_MARKER; sleep 10"],
                lifecycle_id="LC-tmux",
                name=binding.tmux_name,
            )
        self.tmux_name = binding.tmux_name
        try:
            self.assertIn(
                b"AR_READY_MARKER",
                _read_until_session(self.host, session, b"AR_READY_MARKER"),
            )
        finally:
            self.host.close_session(session)


def fcntl_ioctl_getwinsize(fd: int) -> bytes:
    """Read the PTY window size (``TIOCGWINSZ``) -- inverse of the host's resize ioctl."""
    return fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
