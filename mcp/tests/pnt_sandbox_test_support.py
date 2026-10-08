"""What the PNT sandbox test modules share: a sandbox directory, real child processes, a fake.

The tooling lives under ``scripts/pnt_sandbox`` and is not part of the package. Everything it
runs or starts goes through one ``Operations`` object, replaced here by a fake whose "processes"
are real child processes, so port ownership, process identity, environments and signalling are
exercised against the real ``/proc``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.paseo_host_contract import NODE_VERSION

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (REPOSITORY_ROOT / "scripts").as_posix()
# On the path only while the package is imported: left there, every directory under ``scripts``
# would be an importable top-level package for whatever test runs next in this worker. The test
# modules take the package's names from here.
sys.path.insert(0, SCRIPTS)
try:
    from pnt_sandbox import builder, commands, lock, procfs, record, safety
    from pnt_sandbox.build_roots import (
        resolve_roots,
    )
    from pnt_sandbox.environment import (
        foreign_variables,
        launcher_scrub,
        removed_names,
        sandbox_environment,
        sandbox_variables,
    )
    from pnt_sandbox.layout import (
        MARKER_NAME,
        PASEO_VERSION,
        REPOSITORY_ID,
        SANDBOX_SCHEMA,
        SandboxLayout,
        SandboxRefusal,
        embed_entries,
        host_settings_document,
        location_refusal,
        pi_provider_entry,
        settings_document,
    )
    from pnt_sandbox.lock import (
        sandbox_lock,
    )
    from pnt_sandbox.operations import (
        PNT_BUILD_FILES,
        Operations,
        StepFailed,
    )
    from pnt_sandbox.procfs import (
        ProcessIdentity,
        is_running,
        port_holders,
    )
    from pnt_sandbox.record import (
        PASEO_PROCESS_RECORD,
        SUPERVISOR_COMMAND,
        clear_stale_paseo_record,
        inspect_paseo_record,
        port_state,
        read_record,
        verified_supervisor,
        write_record,
    )
finally:
    sys.path.remove(SCRIPTS)

__all__ = [
    "LISTENER",
    "MARKER_NAME",
    "PASEO_PROCESS_RECORD",
    "PNT_BUILD_FILES",
    "REPOSITORY",
    "REPOSITORY_ID",
    "REPOSITORY_ROOT",
    "SANDBOX_SCHEMA",
    "SLEEPER",
    "SUPERVISOR",
    "SUPERVISOR_COMMAND",
    "TASK_ROOT",
    "FakeOperations",
    "Operations",
    "ProcessIdentity",
    "SandboxCase",
    "SandboxLayout",
    "SandboxRefusal",
    "StepFailed",
    "builder",
    "clear_stale_paseo_record",
    "commands",
    "embed_entries",
    "foreign_variables",
    "free_port",
    "host_settings_document",
    "inspect_paseo_record",
    "is_running",
    "launcher_scrub",
    "location_refusal",
    "lock",
    "pi_provider_entry",
    "port_holders",
    "port_state",
    "procfs",
    "read_record",
    "record",
    "removed_names",
    "resolve_roots",
    "safety",
    "sandbox_environment",
    "sandbox_lock",
    "sandbox_variables",
    "settings_document",
    "verified_supervisor",
    "write_record",
]

LISTENER = (
    "import socket, sys, time\n"
    "s = socket.socket()\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', int(sys.argv[1])))\n"
    "s.listen()\n"
    "print('listening', flush=True)\n"
    "time.sleep(120)\n"
)
SLEEPER = "import time\ntime.sleep(120)\n"
# A supervisor does not listen itself: its worker does, as with a real Paseo daemon.
SUPERVISOR = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.argv[3], '-c', sys.argv[2], sys.argv[1]])\n"
    "time.sleep(120)\n"
)
REPOSITORY = f"repositories.{REPOSITORY_ID}"
TASK_ROOT = f"{REPOSITORY}.taskRoot (receipts and reports of task-bound roles)"


# One claim per port a run has picked, held while the run's process lives.
_PORT_CLAIMS: list[socket.socket] = []
# How many ports below the system's own range the test ports are drawn from.
_PORT_STRETCH = 12000


def _candidate_port() -> int:
    """A port number to try: one the system does not hand out by itself, where that is known.

    A program that binds port 0 is given a port of the system's range. A port below that range
    is therefore not taken by such a program between the moment a test picks it and the moment
    the test's process listens on it.
    """
    try:
        text = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text(encoding="utf-8")
        lowest = int(text.split()[0])
    except (OSError, ValueError, IndexError):
        lowest = 0
    if lowest - _PORT_STRETCH > 1024:
        return random.randrange(lowest - _PORT_STRETCH, lowest)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def free_port() -> int:
    """A port nothing listens on and no other run of these modules has picked.

    The port is not held between the pick and the process that is to listen on it, so a second
    run could pick the same number in that time. Each run therefore claims the number under a
    name that every process of this machine sees (an abstract socket name), and looks for
    another port when the name is taken or the port is in use.
    """
    while True:
        port = _candidate_port()
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        claim = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            claim.bind(f"\0ar-pnt-sandbox-test-port-{port}")
        except OSError:
            claim.close()
            continue
        _PORT_CLAIMS.append(claim)
        return port


def release_port(port: int) -> None:
    """Give up this run's claim on a port it picked."""
    name = f"\0ar-pnt-sandbox-test-port-{port}".encode()
    for claim in [held for held in _PORT_CLAIMS if held.getsockname() == name]:
        _PORT_CLAIMS.remove(claim)
        claim.close()


def ended(pid: int, seconds: float = 10.0) -> bool:
    """Wait until a killed session leader and what it started have left the process table.

    A kill returns before the process is gone: for a moment it is still read from ``/proc`` as
    running, and a port it listened on is still held.
    """
    deadline = time.monotonic() + seconds
    while procfs.read_identity(pid) is not None or procfs.session_members(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)
    return True


class SandboxCase(unittest.TestCase):
    """A sandbox directory, a directory that looks like a PNT build, and real child processes."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.layout = SandboxLayout(
            self.root / "sandbox", paseo_port=free_port(), dashboard_port=free_port()
        )
        for port in (self.layout.paseo_port, self.layout.dashboard_port):
            self.addCleanup(release_port, port)
        self.checkout = self.root / "checkout"
        for name in PNT_BUILD_FILES:
            (self.checkout / name).parent.mkdir(parents=True, exist_ok=True)
            (self.checkout / name).write_text("", encoding="utf-8")
        self.lines: list[str] = []

    def mark_built(self, layout: int = builder.LAYOUT_VERSION) -> None:
        self.layout.root.mkdir(parents=True, exist_ok=True)
        marker = {"schema": SANDBOX_SCHEMA, "layout": layout, "state": "built"}
        self.layout.marker.write_text(json.dumps(marker), encoding="utf-8")

    def child(
        self,
        *args: str,
        code: str = SLEEPER,
        name: str = sys.executable,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        listening: bool = False,
    ) -> ProcessIdentity:
        """A real process, leader of its own session, with the given command line."""
        argv = [name, "-c", code, *args]
        process = subprocess.Popen(
            argv,
            executable=sys.executable,
            env=env or {},
            cwd=cwd,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(self._reap, process)
        if listening:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline().strip(), b"listening")
        identity = procfs.started_identity(process.pid, argv)
        assert identity is not None
        return identity

    def end(self, identity: ProcessIdentity) -> None:
        """Kill a child of this test with its session, and return when both are gone."""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(identity.pid, signal.SIGKILL)
        self.assertTrue(ended(identity.pid), f"process {identity.pid} did not end")

    @staticmethod
    def _reap(process: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if process.stdout is not None:
            process.stdout.close()

    def dashboard(self, *, listening: bool = True) -> ProcessIdentity:
        """A process that looks like this sandbox's dashboard: command line and directory."""
        command = ("agents_remember.cli", "dashboard", "--config")
        code = LISTENER if listening else SLEEPER
        return self.child(
            str(self.layout.dashboard_port),
            *command,
            self.layout.settings_file.as_posix(),
            code=code,
            cwd=self.layout.root,
            listening=listening,
        )

    def supervisor(
        self,
        home: Path | None = None,
        *,
        listening: bool = True,
        env: dict[str, str] | None = None,
    ) -> ProcessIdentity:
        """A process that looks like a Paseo supervisor of ``home``; its worker listens."""
        environment = {
            "PASEO_HOME": (home or self.layout.paseo_home).as_posix(),
            **sandbox_variables(self.layout),
            **(env or {}),
        }
        if not listening:
            return self.child(name=SUPERVISOR_COMMAND, env=environment)
        return self.child(
            str(self.layout.paseo_port),
            LISTENER,
            sys.executable,
            code=SUPERVISOR,
            name=SUPERVISOR_COMMAND,
            env=environment,
            listening=True,
        )

    def paseo_record(self, identity: ProcessIdentity) -> Path:
        """Paseo's own process record of the sandbox home, naming ``identity``."""
        record = self.layout.paseo_home / PASEO_PROCESS_RECORD
        record.parent.mkdir(parents=True, exist_ok=True)
        document = {"pid": identity.pid, "startedAt": "2026-10-02T00:00:00.000Z"}
        record.write_text(json.dumps(document), encoding="utf-8")
        return record

    def good_roots(self) -> dict[str, Any]:
        """What the build's resolver reports for a sandbox whose every root is its own."""
        layout = self.layout
        return {
            "packageRoot": (self.checkout / "mcp" / "src" / "agents_remember").as_posix(),
            "roots": {
                "configPath": layout.settings_file.as_posix(),
                "coordinationRoot": layout.coordination.as_posix(),
                "workspaceRoot": layout.projects.as_posix(),
                "transcriptRoot": (layout.coordination / "logs" / "mcp").as_posix(),
                "harnessSkillRoot": layout.harness_skills.as_posix(),
                "receipts.taskless": (layout.coordination / "notes" / "reports").as_posix(),
                "receipts.messageBindings": (layout.coordination / "notes").as_posix(),
                "reports.taskless": (layout.projects / ".agents-remember").as_posix(),
                "daggerAuthorityRoot": layout.dagger_authority.as_posix(),
                f"{REPOSITORY}.path": layout.repository.as_posix(),
                f"{REPOSITORY}.memoryRoot": layout.memory.as_posix(),
                TASK_ROOT: layout.task_root.as_posix(),
                f"{REPOSITORY}.leafEnclosures": (layout.coordination / "worktrees").as_posix(),
                "paseoRuntime.home": layout.paseo_home.as_posix(),
                "paseoRuntime.installPrefix": layout.paseo_prefix.as_posix(),
                "productNode.root": (
                    layout.root / f"data/agents-remember/node/node-v{NODE_VERSION}-linux-x64"
                ).as_posix(),
                "productNode.cache": (layout.root / "cache/agents-remember/node").as_posix(),
            },
            "values": {
                "dashboard.port": layout.dashboard_port,
                "dashboard.autoStart": False,
                "paseoRuntime.listen": layout.paseo_listen,
                "paseoRuntime.version": PASEO_VERSION,
                "paseoRuntime.embed": embed_entries(layout),
                "paseoRuntime.providers.pi": pi_provider_entry(),
                "repositories": [REPOSITORY_ID],
            },
            "errors": {},
        }


class FakeOperations(Operations):
    """Records every call that would run or start something; its processes are real children."""

    dashboard_wait_seconds = 0.5

    def __init__(self, case: SandboxCase, *, answers: bool = True) -> None:
        super().__init__(case.layout, {})
        self.case = case
        self.answers = answers
        self.roots = case.good_roots()
        self.provision_report: dict[str, Any] | None = None
        self.supervisor: ProcessIdentity | None = None
        self.dashboard: ProcessIdentity | None = None
        self.calls: list[str] = []
        self.before_provision: Callable[[], None] = lambda: None
        self.before_resolving: Callable[[], None] = lambda: None
        self.while_waiting: Callable[[], None] = lambda: None
        self.new_dashboard: Callable[[], ProcessIdentity] = case.dashboard

    def note(self, call: str, checkout: Path) -> None:
        """Record a call; every one is made for an absolute checkout path."""
        self.case.assertTrue(checkout.is_absolute(), checkout)
        self.calls.append(call)

    def build_python(self, checkout: Path) -> Path:
        self.case.assertTrue(checkout.is_absolute(), checkout)
        return Path(sys.executable)

    def prepare_python(self, checkout: Path) -> list[str]:
        self.note("prepare python", checkout)
        return []

    def prepare_bundle(self, checkout: Path) -> list[str]:
        self.note("prepare bundle", checkout)
        return []

    def render_settings(self, checkout: Path) -> dict[str, Any]:
        self.note("public starter render", checkout)
        spec = importlib.util.spec_from_file_location(
            "fake_case_renderer", REPOSITORY_ROOT / "scripts/harness/render_starter.py"
        )
        assert spec is not None and spec.loader is not None
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        renderer.render_settings(
            self.layout.settings_file,
            self.layout.projects,
            [REPOSITORY_ID],
            renderer.RenderOptions(
                self.layout.coordination, self.layout.paseo_port, self.layout.dashboard_port
            ),
        )
        return {"renderedSettingsPath": self.layout.settings_file.as_posix(), "invocations": 1}

    def resolve_roots(self, checkout: Path, mode: str) -> dict[str, Any]:
        self.note(f"resolve roots as {mode}", checkout)
        self.before_resolving()
        return self.roots

    def tool_calls(self, checkout: Path, request: Path) -> dict[str, Any]:
        """Stand in for the build's tool server: the memory repository with its first commit."""
        self.note("tool calls", checkout)
        self.case.assertTrue(request.is_file())
        memory = self.layout.memory
        memory.mkdir(parents=True)
        identity = ("-c", "user.name=t", "-c", "user.email=t@invalid.example")
        for args in (
            ("init", "-q", "-b", "main"),
            (*identity, "commit", "-q", "--allow-empty", "-m", "x"),
        ):
            subprocess.run(["git", *args], cwd=memory, check=True, capture_output=True)
        return {"ok": True, "results": []}

    def runtime_install(self, checkout: Path) -> dict[str, Any]:
        self.note("runtime_install", checkout)
        self.before_provision()
        if self.provision_report is not None:
            return {"ok": True, "host": self.provision_report}
        if self.supervisor is not None:
            return {"ok": True, "host": {"ok": True, "daemon": {"action": "untouched"}}}
        self.supervisor = self.case.supervisor()
        return {"ok": True, "host": {"ok": True, "daemon": {"action": "started"}}}

    def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
        self.case.assertGreater(timeout, 0)
        self.note(f"paseo {command}", checkout)
        if command == "provision":
            raise AssertionError(
                "sandbox start must use public runtime_install, not direct provision"
            )
        stopped, self.supervisor = self.supervisor, None
        if stopped is None:
            return {"ok": True, "action": "not running", "pid": None}
        # The real command answers when the daemon has ended, its port given up.
        self.case.end(stopped)
        return {"ok": True, "action": "stopped", "pid": stopped.pid}

    def spawn_dashboard(self, checkout: Path) -> ProcessIdentity:
        self.note("spawn dashboard", checkout)
        self.dashboard = self.new_dashboard()
        return self.dashboard

    def paseo_supervisor(self) -> ProcessIdentity | None:
        return self.supervisor

    def dashboard_answers(self) -> bool:
        self.while_waiting()
        return self.answers

    def stop_tmux_server(self) -> str | None:
        return None
