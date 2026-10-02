"""The PNT sandbox tooling (PNT-R11): the safety check's four cases and the refusals.

The tooling lives under ``scripts/pnt_sandbox`` and is not part of the package. Everything it
runs or starts goes through one ``Operations`` object, replaced here by a fake whose "processes"
are real child processes, so port ownership, process identity, environments and signalling are
exercised against the real ``/proc``. The defaulted-root case resolves through the build's own
configuration loader, exactly as the shipped check does.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (REPOSITORY_ROOT / "scripts").as_posix()
# On the path only while the package is imported: left there, every directory under ``scripts``
# would be an importable top-level package for whatever test runs next in this worker.
sys.path.insert(0, SCRIPTS)
try:
    from pnt_sandbox import builder, commands, procfs, safety
    from pnt_sandbox.build_roots import resolve_roots
    from pnt_sandbox.environment import (
        foreign_variables,
        launcher_scrub,
        removed_names,
        sandbox_environment,
        sandbox_variables,
    )
    from pnt_sandbox.layout import (
        MARKER_NAME,
        REPOSITORY_ID,
        SANDBOX_SCHEMA,
        SandboxLayout,
        SandboxRefusal,
        embed_entries,
        location_refusal,
        settings_document,
    )
    from pnt_sandbox.operations import PNT_BUILD_FILES, Operations
    from pnt_sandbox.procfs import ProcessIdentity, is_running, port_holders, read_identity
    from pnt_sandbox.record import (
        PASEO_PROCESS_RECORD,
        SUPERVISOR_COMMAND,
        clear_stale_paseo_record,
        port_state,
        read_record,
        verified_supervisor,
        write_record,
    )
finally:
    sys.path.remove(SCRIPTS)

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
# Every exact name the scrub removes, spelled out here so that a name dropped from the tool's
# own table is noticed.
# fmt: off
REMOVED_EXACT = (
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "GIT_DIR",
    "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "TMUX", "TMUX_PANE", "OLDPWD",
    "AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SSE_PORT", "CLAUDE_DOC_FOCUS_PATHS", "CLAUDE_EFFORT", "CLAUDE_JOB_DIR",
    "CLAUDE_PID", "CODEX_CI", "CODEX_THREAD_ID",
)
REMOVED_BY_PREFIX = (
    "PASEO_HOME", "AR_SPAWN_ROLE", "AR_ORCA_RUNTIME_ROOT", "AR_DAGGER_AUTHORITY_DIGEST",
    "AGENTS_REMEMBER_BENCHMARK_MCP_SRC", "ORCA_USER_DATA_PATH",
)
KEPT = (
    "PATH", "HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
)
# The roots the build's own code answers for a settings file with one repository, one provider
# and an Orca block: spelled out, so a root the resolver stops reporting is noticed.
RESOLVED_ROOTS = (
    "configPath", "coordinationRoot", "workspaceRoot", "transcriptRoot", "harnessSkillRoot",
    "agenticSettings", "observerRoot", "dashboardDaemonDir", "daggerAuthorityRoot",
    "receipts.taskless", "receipts.messageBindings", "reports.taskless",
    "orcaRuntime.runtimeRoot", "orcaRuntime.userDataPath", "paseoRuntime.home",
    "paseoRuntime.installPrefix", "providers.grepai-memory.runtimeRoot",
    "providers.grepai-memory.logRoot",
    TASK_ROOT,
    *(f"{REPOSITORY}.{name}" for name in (
    "path", "memoryRoot", "leafEnclosures", "resolver.code_repository_root",
    "resolver.memory_root", "resolver.onboarding_root", "resolver.task_root",
    "resolver.temp_root",
    )),
)
# fmt: on


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class SandboxCase(unittest.TestCase):
    """A sandbox directory, a directory that looks like a PNT build, and real child processes."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.layout = SandboxLayout(
            self.root / "sandbox", paseo_port=free_port(), dashboard_port=free_port()
        )
        self.checkout = self.root / "checkout"
        for name in PNT_BUILD_FILES:
            (self.checkout / name).parent.mkdir(parents=True, exist_ok=True)
            (self.checkout / name).write_text("", encoding="utf-8")
        self.lines: list[str] = []

    def mark_built(self) -> None:
        self.layout.root.mkdir(parents=True, exist_ok=True)
        marker = {"schema": SANDBOX_SCHEMA, "layout": builder.LAYOUT_VERSION, "state": "built"}
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
        process = subprocess.Popen(
            [name, "-c", code, *args],
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
        identity = read_identity(process.pid)
        assert identity is not None
        return identity

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
            },
            "values": {
                "dashboard.port": layout.dashboard_port,
                "dashboard.autoStart": False,
                "paseoRuntime.listen": layout.paseo_listen,
                "paseoRuntime.version": "0.11.0-beta.2",
                "paseoRuntime.embed": embed_entries(layout),
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

    def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
        self.case.assertGreater(timeout, 0)
        self.note(f"paseo {command}", checkout)
        if command == "provision":
            self.before_provision()
            if self.provision_report is not None:
                return self.provision_report
            if self.supervisor is not None:
                return {"ok": True, "daemon": {"action": "untouched"}}
            self.supervisor = self.case.supervisor()
            return {"ok": True, "daemon": {"action": "started"}}
        stopped, self.supervisor = self.supervisor, None
        if stopped is None:
            return {"ok": True, "action": "not running", "pid": None}
        os.killpg(stopped.pid, signal.SIGKILL)
        return {"ok": True, "action": "stopped", "pid": stopped.pid}

    def spawn_dashboard(self, checkout: Path) -> ProcessIdentity:
        self.note("spawn dashboard", checkout)
        self.dashboard = self.new_dashboard()
        return self.dashboard

    def paseo_supervisor(self) -> ProcessIdentity | None:
        return self.supervisor

    def dashboard_answers(self) -> bool:
        return self.answers

    def stop_tmux_server(self) -> str | None:
        return None


class SafetyCheckTests(SandboxCase):
    def judge(self, report: dict[str, Any]) -> safety.SafetyReport:
        return safety.check(self.layout, self.checkout, lambda _mode: report)

    def found(self, report: dict[str, Any]) -> list[str]:
        return [finding.key for finding in self.judge(report).findings]

    def test_roots_inside_the_sandbox_pass(self) -> None:
        report = self.judge(self.good_roots())

        self.assertTrue(report.ok, report.findings)
        self.assertEqual(report.roots["paseoRuntime.home"], self.layout.paseo_home.as_posix())

    def test_a_root_outside_the_sandbox_fails_and_is_named(self) -> None:
        memory = f"{REPOSITORY}.memoryRoot"
        outside = self.root / "live" / "memory-repos" / "ar-agents-remember"
        outside.mkdir(parents=True)
        linked = self.layout.coordination / "memory-repos"
        linked.parent.mkdir(parents=True)
        linked.symlink_to(outside.parent, target_is_directory=True)
        cases = {
            "a path outside": outside.as_posix(),
            "a path inside that is a link to the outside": (linked / outside.name).as_posix(),
            "a sibling whose name only starts like the sandbox": f"{self.layout.root}-old/memory",
        }
        for label, path in cases.items():
            with self.subTest(label):
                roots = self.good_roots()
                roots["roots"][memory] = path

                report = self.judge(roots)

                self.assertEqual([finding.key for finding in report.findings], [memory])
                self.assertIn("outside the sandbox directory", report.findings[0].problem)

        # A path the configuration holds under a key the check has no name for is judged too.
        linked.unlink()
        unnamed = self.good_roots()
        unnamed["roots"]["orcaRuntime.userDataPath"] = "/home/dev/.config/orca-dev"
        self.assertEqual(self.found(unnamed), ["orcaRuntime.userDataPath"])

    def test_a_root_that_cannot_be_resolved_fails_closed_and_is_named(self) -> None:
        unresolved = self.good_roots()
        unresolved["roots"]["paseoRuntime.home"] = None
        unresolved["errors"]["paseoRuntime.home"] = "the build resolves no path for this root"
        self.assertEqual(self.found(unresolved), ["paseoRuntime.home"])

        required = (
            "coordinationRoot",
            "workspaceRoot",
            "transcriptRoot",
            "harnessSkillRoot",
            "receipts.taskless",
            "receipts.messageBindings",
            "reports.taskless",
            "daggerAuthorityRoot",
            "paseoRuntime.home",
            "paseoRuntime.installPrefix",
            f"{REPOSITORY}.path",
            f"{REPOSITORY}.memoryRoot",
            TASK_ROOT,
            f"{REPOSITORY}.leafEnclosures",
        )
        for key in required:
            with self.subTest(missing=key):
                report = self.good_roots()
                del report["roots"][key]
                self.assertEqual(self.found(report), [key])

        values = {
            "dashboard.port": 8765,
            "dashboard.autoStart": True,
            "paseoRuntime.listen": "127.0.0.1:6799",
            "paseoRuntime.version": "0.10.2",
            "paseoRuntime.embed": [
                {**entry, "frameBaseUrl": "http://127.0.0.1:6799"}
                for entry in embed_entries(self.layout)
            ],
        }
        for key, value in values.items():
            with self.subTest(value=key):
                report = self.good_roots()
                report["values"][key] = value
                self.assertEqual(self.found(report), [key])

        promised = self.good_roots()
        promised["expected"] = ["observerRoot"]
        other_build = self.good_roots()
        other_build["packageRoot"] = "/installed/site-packages/agents_remember"
        no_repository = self.good_roots()
        no_repository["values"]["repositories"] = []
        unreadable = self.good_roots()
        unreadable["roots"] = {"configPath": None}
        unreadable["errors"] = {"configPath": "ConfigError: MCP settings must define workspaceRoot"}
        cases = {
            "observerRoot": promised,
            "build": other_build,
            "repositories": no_repository,
            "configPath": unreadable,
        }
        for key, report in cases.items():
            with self.subTest(key):
                self.assertEqual(self.found(report), [key])

        def disagreeing(mode: str) -> dict[str, Any]:
            report = self.good_roots()
            if mode == "mcp":
                report["roots"]["coordinationRoot"] = (self.layout.root / "other").as_posix()
            return report

        def broken(_mode: str) -> dict[str, Any]:
            raise RuntimeError("the resolver could not run")

        disagreed = safety.check(self.layout, self.checkout, disagreeing)
        self.assertEqual([finding.key for finding in disagreed.findings], ["coordinationRoot"])
        self.assertIn("another process", disagreed.findings[0].problem)
        report = safety.check(self.layout, self.checkout, broken)
        self.assertEqual(
            [finding.key for finding in report.findings], ["dashboard roots", "tool server roots"]
        )

    def test_a_root_the_build_fills_in_by_default_is_caught(self) -> None:
        """Resolved by the build's own loader: no settings key names these roots."""
        settings = self.layout.settings_file
        settings.parent.mkdir(parents=True)
        document = settings_document(self.layout, None)
        del document["transcriptRoot"]
        document["providers"] = {"grepai-memory": {}}
        # A path relative to the repository names a file in it, not a root.
        document["repositories"][REPOSITORY_ID] = {"certificationProfile": "mcp/profile.json"}
        orca = self.root / "orca"
        document["orcaRuntime"] = {
            "runtimeRoot": (orca / "source").as_posix(),
            "userDataPath": (orca / "profile").as_posix(),
        }
        memory = f"{REPOSITORY}.memoryRoot"
        authority = {"AR_DAGGER_AUTHORITY_ROOT": self.layout.dagger_authority.as_posix()}

        def judged() -> tuple[dict[str, Any], dict[str, str]]:
            settings.write_text(json.dumps(document), encoding="utf-8")
            resolved = resolve_roots(settings.as_posix())
            report = safety.evaluate(self.layout, REPOSITORY_ROOT, resolved)
            return resolved, {finding.key: finding.problem for finding in report.findings}

        with mock.patch.dict(os.environ, authority):
            resolved, inside = judged()
        # The exact set of roots the build's own code answers for this configuration: a root the
        # resolver stops reporting, or a path-bearing key it does not know by name, shows here.
        self.assertEqual(sorted(resolved["roots"]), sorted(RESOLVED_ROOTS))
        self.assertEqual(
            sorted(set(resolved["roots"]) - set(resolved["expected"])),
            ["orcaRuntime.runtimeRoot", "orcaRuntime.userDataPath"],
        )
        for key in (memory, "transcriptRoot", "daggerAuthorityRoot", "observerRoot"):
            self.assertNotIn(key, inside)
        for key in ("orcaRuntime.runtimeRoot", "orcaRuntime.userDataPath"):
            self.assertIn("outside the sandbox directory", inside[key])
        # Nothing was created for the repository, so the build's resolver cannot answer for it.
        self.assertIn("cannot be resolved", inside[f"{REPOSITORY}.resolver.memory_root"])

        # Without the sandbox's variable the build keeps its registry in the user's home.
        with mock.patch.dict(os.environ):
            os.environ.pop("AR_DAGGER_AUTHORITY_ROOT", None)
            _resolved, defaulted = judged()
        self.assertIn("outside the sandbox directory", defaulted["daggerAuthorityRoot"])

        document["coordinationRoot"] = (self.root / "live" / "ar-coordination").as_posix()
        del document["harnessSkillRoot"]
        _resolved, outside = judged()
        for key in (memory, "transcriptRoot", "coordinationRoot", TASK_ROOT, "observerRoot"):
            self.assertIn("outside the sandbox directory", outside[key])
        self.assertNotIn(f"{REPOSITORY}.path", outside)
        self.assertIn("cannot be resolved", outside["harnessSkillRoot"])

        del document["paseoRuntime"], document["dashboard"]
        _resolved, unconfigured = judged()
        self.assertIn("cannot be resolved", unconfigured["paseoRuntime.home"])
        self.assertIn("8765", unconfigured["dashboard.port"])


class StartAndStopTests(SandboxCase):
    def start(self, ops: FakeOperations, checkout: Path | None = None) -> int:
        return commands.start(
            self.layout, checkout or self.checkout, ops, self.lines.append, self.root / "no-eve"
        )

    def stop(self, ops: FakeOperations) -> int:
        return commands.stop(self.layout, ops, self.lines.append)

    def test_start_refuses_a_checkout_that_is_not_a_pnt_build(self) -> None:
        ops = FakeOperations(self)
        incomplete = self.root / "incomplete"
        for name in PNT_BUILD_FILES[:-1]:
            (incomplete / name).parent.mkdir(parents=True, exist_ok=True)
            (incomplete / name).write_text("", encoding="utf-8")

        for checkout in (self.root, incomplete):
            with (
                self.subTest(checkout=checkout.name),
                self.assertRaisesRegex(SandboxRefusal, "is not a PNT build checkout"),
            ):
                self.start(ops, checkout)

        self.assertEqual(ops.calls, [])
        self.assertFalse(self.layout.root.exists())
        self.assertFalse(self.layout.lock_file.exists())

    def test_start_refuses_a_reserved_port_held_by_a_foreign_process(self) -> None:
        self.mark_built()
        for port in (self.layout.dashboard_port, self.layout.paseo_port):
            with self.subTest(port=port), socket.socket() as foreign:
                foreign.bind(("127.0.0.1", port))
                foreign.listen()
                ops = FakeOperations(self)

                with self.assertRaises(SandboxRefusal) as refused:
                    self.start(ops)

                self.assertIn(
                    f"port {port} is held by process {os.getpid()}", str(refused.exception)
                )
                self.assertEqual(ops.calls, [])
                self.assertEqual(read_record(self.layout), {})

        # A port that is taken while the sandbox is being checked is refused as well.
        with socket.socket() as late:
            ops = FakeOperations(self)

            def take_the_port() -> None:
                if late.getsockname()[1] == 0:
                    late.bind(("127.0.0.1", self.layout.dashboard_port))
                    late.listen()

            ops.before_resolving = take_the_port
            with self.assertRaisesRegex(SandboxRefusal, f"port {self.layout.dashboard_port} "):
                self.start(ops)
            self.assertNotIn("paseo provision", ops.calls)

    def test_start_reports_already_running_and_changes_nothing(self) -> None:
        self.mark_built()
        ops = FakeOperations(self)
        self.assertEqual(self.start(ops), 0)
        self.assertEqual(self.lines[-1], self.layout.dashboard_url)
        record = self.layout.process_record.read_bytes()
        ops.calls.clear()

        self.assertEqual(self.start(ops), 0)

        self.assertEqual(self.lines[-1], f"already running: {self.layout.dashboard_url}")
        self.assertEqual(ops.calls, [])
        self.assertEqual(self.layout.process_record.read_bytes(), record)
        self.assertFalse(self.layout.lock_file.exists())

        # Both processes alive is not "running" while neither holds its port.
        self.assertEqual(self.stop(ops), 0)
        idle = FakeOperations(self)
        idle.supervisor = self.supervisor(listening=False)
        write_record(self.layout, {"dashboard": self.dashboard(listening=False).as_record()})
        self.assertEqual(self.start(idle), 0)
        self.assertEqual(self.lines[-1], self.layout.dashboard_url)
        self.assertIn("paseo provision", idle.calls)

    def test_start_builds_a_missing_sandbox_first(self) -> None:
        ops = FakeOperations(self)

        self.assertEqual(self.start(ops), 0)

        self.assertTrue(builder.is_built(self.layout))
        self.assertLess(ops.calls.index("tool calls"), ops.calls.index("paseo provision"))
        self.assertTrue((self.layout.repository / "textkit" / "case.py").is_file())
        self.assertEqual(self.stop(ops), 0)

    def test_start_starts_nothing_when_the_safety_check_fails(self) -> None:
        self.mark_built()
        ops = FakeOperations(self)
        ops.roots["roots"]["coordinationRoot"] = "/home/firefox/projects/ar-coordination"

        with self.assertRaisesRegex(SandboxRefusal, "safety check failed"):
            self.start(ops)

        self.assertNotIn("paseo provision", ops.calls)
        self.assertNotIn("spawn dashboard", ops.calls)
        self.assertTrue(any("FAIL coordinationRoot" in line for line in self.lines))

    def test_two_starts_at_once_leave_one_sandbox_that_stop_ends(self) -> None:
        self.mark_built()
        first = FakeOperations(self)
        provisioning, proceed = threading.Event(), threading.Event()

        def hold() -> None:
            provisioning.set()
            self.assertTrue(proceed.wait(30))

        first.before_provision = hold
        outcome: list[int] = []
        thread = threading.Thread(target=lambda: outcome.append(self.start(first)))
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(proceed.set)
        self.assertTrue(provisioning.wait(30))

        second = FakeOperations(self)
        for command in (self.start, self.stop):
            with self.subTest(command=command.__name__), self.assertRaises(SandboxRefusal) as held:
                command(second)
            self.assertIn(f"'start' (pid {os.getpid()}", str(held.exception))
        with self.assertRaisesRegex(SandboxRefusal, "another command is running"):
            commands.reset(self.layout, second, self.lines.append)
        self.assertEqual(second.calls, [])

        proceed.set()
        thread.join()
        self.assertEqual(outcome, [0])
        record = read_record(self.layout)
        self.assertEqual(ProcessIdentity.from_record(record["dashboard"]), first.dashboard)
        self.assertEqual(ProcessIdentity.from_record(record["paseo"]), first.supervisor)
        self.assertEqual(self.start(first), 0)
        self.assertEqual(self.lines[-1], f"already running: {self.layout.dashboard_url}")

        dashboard = first.dashboard
        self.assertEqual(self.stop(first), 0)
        self.assertFalse(is_running(dashboard))
        self.assertIsNone(first.supervisor)
        self.assertEqual(port_holders(self.layout.dashboard_port), [])
        self.assertEqual(commands.reset(self.layout, first, self.lines.append), 0)
        self.assertFalse(self.layout.root.exists())
        self.assertFalse(self.layout.lock_file.exists())

    def test_a_start_that_fails_half_way_stops_what_it_started(self) -> None:
        self.mark_built()
        ops = FakeOperations(self, answers=False)

        self.assertEqual(self.start(ops), 1)

        assert ops.dashboard is not None
        self.assertFalse(is_running(ops.dashboard))
        self.assertEqual(ops.calls[-3:], ["paseo provision", "spawn dashboard", "paseo stop"])
        self.assertIn("start failed at step 'dashboard'", "\n".join(self.lines))
        self.assertIn("did not answer", "\n".join(self.lines))
        self.assertIn(f"log file: {self.layout.dashboard_log}", self.lines)
        record = read_record(self.layout)
        self.assertNotIn("dashboard", record)
        self.assertNotIn("paseo", record)

        # A daemon that ran before the failed start is not this start's to stop.
        before = FakeOperations(self, answers=False)
        before.supervisor = self.supervisor()
        self.assertEqual(self.start(before), 1)
        self.assertNotIn("paseo stop", before.calls)
        self.assertTrue(is_running(before.supervisor))
        self.assertEqual(self.stop(before), 0)

    def test_a_failed_start_names_the_step_that_failed(self) -> None:
        self.mark_built()
        refused = FakeOperations(self)
        refused.provision_report = {
            "ok": False,
            "daemon": {"action": "untouched"},
            "error": {"message": "the install step failed", "detail": "npm ETARGET"},
        }
        self.assertEqual(self.start(refused), 1)
        self.assertIn(
            "start failed at step 'paseo provision': the install step failed npm ETARGET",
            self.lines,
        )
        self.assertNotIn("spawn dashboard", refused.calls)
        self.assertNotIn("paseo stop", refused.calls)

        exiting = FakeOperations(self, answers=False)

        def exits_at_once() -> ProcessIdentity:
            dashboard = self.dashboard(listening=False)
            os.kill(dashboard.pid, signal.SIGKILL)
            return dashboard

        exiting.new_dashboard = exits_at_once
        self.assertEqual(self.start(exiting), 1)
        self.assertIn("start failed at step 'dashboard': the dashboard process exited", self.lines)

        # Something else answers on the dashboard's port: the start did not come up.
        answered = FakeOperations(self)
        with socket.socket() as foreign:

            def beside_a_foreign_listener() -> ProcessIdentity:
                foreign.bind(("127.0.0.1", self.layout.dashboard_port))
                foreign.listen()
                return self.dashboard(listening=False)

            answered.new_dashboard = beside_a_foreign_listener
            self.assertEqual(self.start(answered), 1)
        self.assertIn("is answered by another process", "\n".join(self.lines[-4:]))
        self.assertIsNone(answered.supervisor)

    def test_start_refuses_a_running_daemon_that_lacks_the_sandbox_environment(self) -> None:
        self.mark_built()
        cases = {
            "CLAUDE_CODE_SESSION_ID": self.supervisor(
                listening=False, env={"CLAUDE_CODE_SESSION_ID": "caller"}
            ),
            "AR_SPAWN_ROLE, TMUX_TMPDIR": self.supervisor(
                listening=False, env={"AR_SPAWN_ROLE": "worker", "TMUX_TMPDIR": "/tmp"}
            ),
        }
        for names, supervisor in cases.items():
            with self.subTest(names):
                ops = FakeOperations(self)
                ops.supervisor = supervisor
                with self.assertRaises(SandboxRefusal) as refused:
                    self.start(ops)
                self.assertIn(f"(pid {supervisor.pid})", str(refused.exception))
                self.assertIn(names, str(refused.exception))
                self.assertIn("'stop', then 'start'", str(refused.exception))
                self.assertEqual(ops.calls, [])
                self.assertTrue(is_running(supervisor))

    def test_stop_signals_only_the_process_start_recorded(self) -> None:
        self.mark_built()
        ops = FakeOperations(self)
        foreign = self.child()
        earlier = ProcessIdentity(foreign.pid, foreign.start_ticks - 1, foreign.argv)
        renamed = ProcessIdentity(foreign.pid, foreign.start_ticks, ("another", "command"))
        for label, stale in {"start time": earlier, "command line": renamed}.items():
            with self.subTest(label):
                write_record(self.layout, {"dashboard": stale.as_record()})
                self.assertEqual(self.stop(ops), 0)
                self.assertEqual(
                    self.lines[-2],
                    f"dashboard: not running (the recorded process {foreign.pid} is gone)",
                )
                # Signalling re-reads the identity itself, whoever calls it.
                self.assertEqual(
                    procfs.terminate(stale, grace_seconds=0.2, kill_seconds=0.2), "not running"
                )
        # The right identity is not enough: the process must be a dashboard of this sandbox,
        # by its command line, its settings file and its working directory.
        command = ("0", "agents_remember.cli", "dashboard", "--config")
        settings = self.layout.settings_file.as_posix()
        others = {
            "another command in the sandbox": self.child(cwd=self.layout.root),
            "another settings file": self.child(*command, "/other.json", cwd=self.layout.root),
            "another directory": self.child(*command, settings, cwd=self.root),
        }
        for label, other in others.items():
            with self.subTest(label):
                write_record(self.layout, {"dashboard": other.as_record()})
                self.assertEqual(self.stop(ops), 0)
                self.assertIn("is not a dashboard of this sandbox", self.lines[-2])
                self.assertTrue(is_running(other))
        # A record of an earlier boot names nothing: start times repeat across boots.
        write_record(self.layout, {"dashboard": self.dashboard(listening=False).as_record()})
        stored = json.loads(self.layout.process_record.read_text(encoding="utf-8"))
        self.layout.process_record.write_text(json.dumps({**stored, "bootId": "an earlier boot"}))
        self.assertEqual(read_record(self.layout), {})
        self.assertTrue(is_running(foreign))
        self.assertEqual(ops.calls, [])

        # A start that sees such a record starts a dashboard of its own.
        write_record(self.layout, {"dashboard": earlier.as_record()})
        self.assertEqual(self.start(ops), 0)
        own = ops.dashboard
        assert own is not None
        self.assertNotEqual(own.pid, foreign.pid)
        self.assertEqual(self.stop(ops), 0)
        self.assertFalse(is_running(own))
        self.assertTrue(is_running(foreign))
        self.assertFalse(self.layout.process_record.exists())

        # What the dashboard left running in its own session is stopped with it.
        leader = self.child(
            str(self.layout.dashboard_port),
            LISTENER,
            sys.executable,
            *command[1:],
            settings,
            code=SUPERVISOR,
            cwd=self.layout.root,
            listening=True,
        )
        write_record(self.layout, {"dashboard": leader.as_record()})
        self.assertEqual(self.stop(ops), 0)
        self.assertEqual(port_holders(self.layout.dashboard_port), [])

    def test_a_dashboard_the_record_does_not_name_is_reported_and_keeps_the_directory(self) -> None:
        self.mark_built()
        ops = FakeOperations(self)
        unrecorded = self.dashboard()

        self.assertEqual(self.stop(ops), 1)
        self.assertIn(f"NOT STOPPED: process {unrecorded.pid}", self.lines[-1])
        self.assertEqual(commands.reset(self.layout, ops, self.lines.append), 1)

        self.assertTrue(is_running(unrecorded))
        self.assertTrue(self.layout.marker.is_file())
        self.assertIn("was not deleted", self.lines[-1])

        # A runtime that does not stop keeps the directory as well.
        os.kill(unrecorded.pid, signal.SIGKILL)

        class Stuck(FakeOperations):
            def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
                self.note(f"paseo {command} for {timeout:g} s", checkout)
                return {"ok": False, "error": {"message": "refused"}}

        stuck = Stuck(self)
        stuck.supervisor = self.supervisor()
        self.assertEqual(commands.reset(self.layout, stuck, self.lines.append), 1)
        self.assertIn("paseo runtime: STILL RUNNING", "\n".join(self.lines))
        self.assertTrue(self.layout.marker.is_file())

    def test_the_paseo_home_record_is_trusted_only_for_this_homes_supervisor(self) -> None:
        home = self.layout.paseo_home
        own = self.supervisor(listening=False)
        other_home = self.supervisor(self.root / "other-home", listening=False)
        impostor = self.child(env={"PASEO_HOME": home.as_posix()})

        self.assertIsNone(verified_supervisor(home))
        record = self.paseo_record(own)
        self.assertEqual(verified_supervisor(home), own)
        self.assertIsNone(clear_stale_paseo_record(home))
        self.assertTrue(record.is_file())
        # The id passed to a process with another command line, or to another home's supervisor.
        for stale in (impostor, other_home):
            self.paseo_record(stale)
            self.assertIsNone(verified_supervisor(home))

    def test_a_stale_paseo_record_is_deleted_before_any_runtime_command(self) -> None:
        self.mark_built()
        foreign = {
            "a live foreign process": self.child(),
            "the supervisor of another home": self.supervisor(
                self.root / "other-home", listening=False
            ),
        }
        for label, process in foreign.items():
            with self.subTest(label, command="stop"):
                record = self.paseo_record(process)
                ops = FakeOperations(self)

                self.assertEqual(self.stop(ops), 0)

                self.assertFalse(record.exists())
                self.assertEqual(ops.calls, [])
                self.assertIn(f"deleted the stale record {record}", self.lines[-1])
                self.assertIn(f"it named process {process.pid}", self.lines[-1])
            with self.subTest(label, command="start"):
                record = self.paseo_record(process)
                ops = FakeOperations(self)
                ops.before_provision = lambda record=record: self.assertFalse(record.exists())

                self.assertEqual(self.start(ops), 0)

                said = [line for line in self.lines if "the stale Paseo process record" in line]
                self.assertIn(f"deleted the stale Paseo process record {record}", said[-1])
                self.assertIn(f"it named process {process.pid}", said[-1])
                self.assertEqual(self.stop(ops), 0)
            self.assertTrue(is_running(process))


class BuildInputTests(SandboxCase):
    def test_a_directory_that_is_not_a_sandbox_is_neither_used_nor_deleted(self) -> None:
        ops = FakeOperations(self)
        project = self.root / "projects"
        (project / ".claude").mkdir(parents=True)
        self.assertIn(
            "per-project harness configuration", location_refusal(project / "x" / "sbx") or ""
        )
        self.assertIsNone(location_refusal(self.layout.root))
        self.assertIn("directory of its own", location_refusal(Path.home()) or "")
        for name in (".claude", ".codex", ".pi", ".hermes", ".dsh"):
            refusal = location_refusal(self.root / "scratch" / name / "sandbox") or ""
            self.assertIn(f"inside a harness configuration directory ({name})", refusal)
        self.assertFalse((self.root / "scratch").exists())

        self.layout.root.mkdir()
        (self.layout.root / "notes.txt").write_text("not a sandbox", encoding="utf-8")
        with self.assertRaisesRegex(SandboxRefusal, "not a sandbox this tool built"):
            builder.build(self.layout, self.checkout, ops, self.lines.append, self.root / "no-eve")
        with self.assertRaisesRegex(SandboxRefusal, "refusing to delete"):
            commands.reset(self.layout, ops, self.lines.append)

        self.assertEqual(ops.calls, [])
        self.assertEqual([path.name for path in self.layout.root.iterdir()], ["notes.txt"])
        self.assertFalse((self.layout.root / MARKER_NAME).exists())

        # A sandbox directory that cannot be removed is reported, not a traceback.
        self.mark_built()
        linked = SandboxLayout(
            self.root / "link", self.layout.paseo_port, self.layout.dashboard_port
        )
        linked.root.symlink_to(self.layout.root, target_is_directory=True)
        self.assertEqual(commands.reset(linked, FakeOperations(self), self.lines.append), 1)
        self.assertIn("could not be deleted completely", self.lines[-1])
        self.assertTrue(self.layout.marker.is_file())

    def test_the_settings_name_only_sandbox_paths_and_the_reserved_ports(self) -> None:
        layout = SandboxLayout(self.layout.root)
        env_file = self.root / "eve-project" / ".env.local"
        settings = settings_document(layout, env_file)
        runtime = settings["paseoRuntime"]

        self.assertEqual(settings["dashboard"], {"autoStart": False, "port": 9797})
        self.assertEqual(
            (runtime["listen"], runtime["version"]), ("127.0.0.1:6820", "0.11.0-beta.2")
        )
        self.assertEqual(
            runtime["embed"],
            [
                {"dashboardOrigin": origin, "frameBaseUrl": "http://127.0.0.1:6820"}
                for origin in ("http://127.0.0.1:9797", "http://localhost:9797")
            ],
        )
        self.assertEqual(runtime["providers"]["hermes"]["command"], ["hermes", "acp"])
        self.assertEqual(runtime["providers"]["eve"]["options"], {"supportsMcpServers": False})
        self.assertEqual(
            list(settings_document(layout, None)["paseoRuntime"]["providers"]), ["hermes"]
        )
        paths = [
            settings[key]
            for key in ("coordinationRoot", "workspaceRoot", "transcriptRoot", "harnessSkillRoot")
        ]
        paths += [
            runtime["home"],
            runtime["installPrefix"],
            *runtime["providers"]["eve"]["command"][:3:2],
        ]
        for path in paths:
            self.assertIn(layout.root, Path(path).parents, path)

    def test_the_environment_loses_what_selects_another_runtime_and_keeps_logins(self) -> None:
        removed = (*REMOVED_EXACT, *REMOVED_BY_PREFIX)
        base = {
            **dict.fromkeys(removed, "of the caller"),
            **dict.fromkeys(KEPT, "kept"),
            "PATH": os.environ["PATH"],
            "PWD": "/the/callers/directory",
        }
        own = {
            "TMUX_TMPDIR": self.layout.tmux_dir.as_posix(),
            "PYTHONPYCACHEPREFIX": self.layout.pycache.as_posix(),
            "GIT_OPTIONAL_LOCKS": "0",
            "AR_DAGGER_AUTHORITY_ROOT": self.layout.dagger_authority.as_posix(),
        }

        environment = sandbox_environment(base, self.layout)

        self.assertEqual(set(base) - set(environment), set(removed))
        self.assertEqual(removed_names(base), sorted(removed))
        self.assertEqual({name: environment[name] for name in own}, own)
        self.assertEqual({environment[name] for name in KEPT if name != "PATH"}, {"kept"})
        scrub = launcher_scrub(self.layout)
        self.assertLessEqual({*REMOVED_EXACT, *own, "PWD"}, set(scrub["names"]))
        self.assertEqual(scrub["prefixes"], ["AGENTS_REMEMBER_", "AR_", "ORCA_", "PASEO_"])

        # What a running process carries decides whether it has the sandbox's environment.
        home = {"PASEO_HOME": self.layout.paseo_home.as_posix()}
        self.assertEqual(foreign_variables({**environment, **home}, self.layout), [])
        carried = {**environment, **home, "CODEX_THREAD_ID": "x", "GIT_OPTIONAL_LOCKS": "1"}
        del carried["TMUX_TMPDIR"]
        self.assertEqual(
            foreign_variables(carried, self.layout),
            ["CODEX_THREAD_ID", "GIT_OPTIONAL_LOCKS", "TMUX_TMPDIR"],
        )

        # The children really receive it: a command that is run, and the dashboard.
        self.layout.root.mkdir()
        names = "import json, os; print(json.dumps(dict(os.environ)))"

        class Real(Operations):
            def dashboard_argv(self, checkout: Path) -> list[str]:
                return [sys.executable, "-c", SLEEPER, checkout.as_posix()]

        ops = Real(self.layout, base)
        ran = json.loads(ops.run([sys.executable, "-c", names], cwd=self.root, timeout=60).stdout)
        with warnings.catch_warnings():
            # The dashboard is left running on purpose; Python remarks on that when it lets go.
            warnings.simplefilter("ignore", ResourceWarning)
            dashboard = ops.spawn_dashboard(self.checkout)
        self.addCleanup(os.kill, dashboard.pid, signal.SIGKILL)
        started = procfs.environment(dashboard.pid) or {}
        for received, directory in ((ran, self.root), (started, self.layout.root)):
            self.assertEqual(set(removed) & set(received), set())
            self.assertEqual({name: received[name] for name in own}, own)
            self.assertEqual(received["PWD"], directory.as_posix())
            self.assertEqual(received["CLAUDE_CODE_OAUTH_TOKEN"], "kept")

    def test_a_port_is_held_whoever_holds_it_and_on_whichever_address(self) -> None:
        port = self.layout.paseo_port
        fake = self.root / "proc"
        (fake / "net").mkdir(parents=True)
        header = "  sl  local_address remote_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
        listening = f"   0: {'0' * 31}1:{port:04X} {'0' * 32}:0000 0A 0:0 00:0 0 1001 0 4242 1 0 100 0 0 10 0\n"
        (fake / "net" / "tcp").write_text(header, encoding="utf-8")
        (fake / "net" / "tcp6").write_text(header + listening, encoding="utf-8")

        bystander = self.child()
        with mock.patch.object(procfs, "PROC", fake):
            # Listening on the IPv6 loopback only, held by a process this user cannot inspect.
            self.assertEqual(port_holders(port), [None])
            self.assertEqual(port_holders(port + 1), [])
            unreadable = port_state(port, bystander)
        self.assertEqual(unreadable.foreign, (None,))
        self.assertFalse(unreadable.held_by_sandbox)

        # The holder is the supervisor's worker: it belongs to the supervisor, to nobody else.
        supervisor = self.supervisor()
        self.assertNotIn(supervisor.pid, port_holders(port))
        self.assertTrue(port_state(port, supervisor).held_by_sandbox)
        self.assertEqual(port_state(port, bystander).foreign, tuple(port_holders(port)))


if __name__ == "__main__":
    unittest.main()
