"""The PNT sandbox tooling (PNT-R11): the safety check's four cases and the refusals.

The tooling lives under ``scripts/pnt_sandbox`` and is not part of the package. Everything it
runs or starts goes through one ``Operations`` object, replaced here by a fake whose "processes"
are real child processes, so port ownership, process identity and signalling are exercised
against the real ``/proc``. The defaulted-root case resolves through the build's own
configuration loader, exactly as the shipped check does.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY_ROOT / "scripts"
# A string, never a Path: the import machinery ignores a non-``str`` entry.
if SCRIPTS.as_posix() not in sys.path:
    sys.path.insert(0, SCRIPTS.as_posix())

from pnt_sandbox import builder, commands, safety
from pnt_sandbox.build_roots import resolve_roots
from pnt_sandbox.environment import removed_names, sandbox_environment
from pnt_sandbox.layout import (
    MARKER_NAME,
    REPOSITORY_ID,
    SANDBOX_SCHEMA,
    SandboxLayout,
    SandboxRefusal,
    location_refusal,
    settings_document,
)
from pnt_sandbox.operations import PNT_BUILD_FILES, Operations
from pnt_sandbox.procfs import ProcessIdentity, is_running, read_identity
from pnt_sandbox.record import (
    PASEO_PROCESS_RECORD,
    SUPERVISOR_COMMAND,
    read_record,
    verified_supervisor,
    write_record,
)

LISTENER = (
    "import socket, sys, time\n"
    "s = socket.socket()\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', int(sys.argv[1])))\n"
    "s.listen()\n"
    "print('listening', flush=True)\n"
    "time.sleep(120)\n"
)


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
        self, port: int | None = None, name: str = sys.executable, home: Path | None = None
    ) -> ProcessIdentity:
        """A real process of its own session; with ``port`` it listens there.

        ``name`` is its command line's first word and ``home`` the Paseo home its environment
        names, which is how a Paseo supervisor looks from outside.
        """
        code = LISTENER if port is not None else "import time\ntime.sleep(120)\n"
        process = subprocess.Popen(
            [name, "-c", code, str(port or 0)],
            executable=sys.executable,
            env={"PASEO_HOME": home.as_posix()} if home is not None else {},
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(self._reap, process)
        if port is not None:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline().strip(), b"listening")
        identity = read_identity(process.pid)
        assert identity is not None
        return identity

    @staticmethod
    def _reap(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()

    def good_roots(self) -> dict[str, Any]:
        """What the build's resolver reports for a sandbox whose every root is its own."""
        layout = self.layout
        repository = f"repositories.{REPOSITORY_ID}"
        return {
            "packageRoot": (self.checkout / "mcp" / "src" / "agents_remember").as_posix(),
            "roots": {
                "settings": layout.settings_file.as_posix(),
                "coordinationRoot": layout.coordination.as_posix(),
                "workspaceRoot (Projects folder)": layout.projects.as_posix(),
                "transcriptRoot": (layout.coordination / "logs" / "mcp").as_posix(),
                "harnessSkillRoot": layout.harness_skills.as_posix(),
                "receipts.taskless": (layout.coordination / "notes" / "reports").as_posix(),
                "receipts.messageBindings": (layout.coordination / "notes").as_posix(),
                "reports.taskless": (layout.projects / ".agents-remember").as_posix(),
                f"{repository}.path": layout.repository.as_posix(),
                f"{repository}.memoryRoot": layout.memory.as_posix(),
                f"{repository}.taskRoot (receipts and reports of task-bound roles)": (
                    layout.task_root.as_posix()
                ),
                f"{repository}.leafEnclosures": (layout.coordination / "worktrees").as_posix(),
                "paseoRuntime.home": layout.paseo_home.as_posix(),
                "paseoRuntime.installPrefix": layout.paseo_prefix.as_posix(),
            },
            "values": {
                "dashboard.port": layout.dashboard_port,
                "dashboard.autoStart": False,
                "paseoRuntime.listen": layout.paseo_listen,
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
        self.supervisor: ProcessIdentity | None = None
        self.dashboard: ProcessIdentity | None = None
        self.calls: list[str] = []

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
        return self.roots

    def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
        self.case.assertGreater(timeout, 0)
        self.note(f"paseo {command}", checkout)
        if command == "provision":
            if self.supervisor is not None:
                return {"ok": True, "daemon": {"action": "untouched"}}
            self.supervisor = self.case.child(self.layout.paseo_port)
            return {"ok": True, "daemon": {"action": "started"}}
        stopped, self.supervisor = self.supervisor, None
        if stopped is None:
            return {"ok": True, "action": "not running", "pid": None}
        os.kill(stopped.pid, signal.SIGKILL)
        return {"ok": True, "action": "stopped", "pid": stopped.pid}

    def spawn_dashboard(self, checkout: Path) -> ProcessIdentity:
        self.note("spawn dashboard", checkout)
        self.dashboard = self.case.child(self.layout.dashboard_port)
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

    def test_roots_inside_the_sandbox_pass(self) -> None:
        report = self.judge(self.good_roots())

        self.assertTrue(report.ok, report.findings)
        self.assertEqual(report.roots["paseoRuntime.home"], self.layout.paseo_home.as_posix())

    def test_a_root_outside_the_sandbox_fails_and_is_named(self) -> None:
        memory = f"repositories.{REPOSITORY_ID}.memoryRoot"
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

    def test_a_root_that_cannot_be_resolved_fails_closed_and_is_named(self) -> None:
        unresolved = self.good_roots()
        unresolved["roots"]["paseoRuntime.home"] = None
        unresolved["errors"]["paseoRuntime.home"] = "the build resolves no path for this root"
        missing = self.good_roots()
        del missing["roots"][f"repositories.{REPOSITORY_ID}.leafEnclosures"]
        defaulted_port = self.good_roots()
        defaulted_port["values"]["dashboard.port"] = 8765
        other_build = self.good_roots()
        other_build["packageRoot"] = "/installed/site-packages/agents_remember"
        cases = {
            "paseoRuntime.home": unresolved,
            f"repositories.{REPOSITORY_ID}.leafEnclosures": missing,
            "dashboard.port": defaulted_port,
            "build": other_build,
        }
        for key, roots in cases.items():
            with self.subTest(key):
                self.assertEqual([finding.key for finding in self.judge(roots).findings], [key])

        def broken(_mode: str) -> dict[str, Any]:
            raise RuntimeError("the resolver could not run")

        report = safety.check(self.layout, self.checkout, broken)
        self.assertEqual(
            [finding.key for finding in report.findings], ["dashboard roots", "tool server roots"]
        )

    def test_a_root_the_build_fills_in_by_default_is_caught(self) -> None:
        """Resolved by the build's own loader: no settings key names these roots."""
        settings = self.layout.settings_file
        settings.parent.mkdir(parents=True)
        document = settings_document(self.layout, None)
        del document["transcriptRoot"], document["harnessSkillRoot"]
        memory = f"repositories.{REPOSITORY_ID}.memoryRoot"

        def judged() -> dict[str, str]:
            settings.write_text(json.dumps(document), encoding="utf-8")
            report = safety.evaluate(
                self.layout, REPOSITORY_ROOT, resolve_roots(settings.as_posix())
            )
            return {finding.key: finding.problem for finding in report.findings}

        inside = judged()
        self.assertNotIn(memory, inside)
        self.assertNotIn("transcriptRoot", inside)
        self.assertIn("cannot be resolved", inside["harnessSkillRoot"])

        document["coordinationRoot"] = (self.root / "live" / "ar-coordination").as_posix()
        outside = judged()
        for key in (memory, "transcriptRoot", "coordinationRoot"):
            self.assertIn("outside the sandbox directory", outside[key])
        self.assertNotIn(f"repositories.{REPOSITORY_ID}.path", outside)

        del document["paseoRuntime"], document["dashboard"]
        unconfigured = judged()
        self.assertIn("cannot be resolved", unconfigured["paseoRuntime.home"])
        self.assertIn("8765", unconfigured["dashboard.port"])


class StartAndStopTests(SandboxCase):
    def start(self, ops: FakeOperations, checkout: Path | None = None) -> int:
        return commands.start(
            self.layout, checkout or self.checkout, ops, self.lines.append, self.root / "no-eve"
        )

    def test_start_refuses_a_checkout_that_is_not_a_pnt_build(self) -> None:
        ops = FakeOperations(self)

        with self.assertRaisesRegex(SandboxRefusal, "is not a PNT build checkout"):
            self.start(ops, self.root)

        self.assertEqual(ops.calls, [])
        self.assertFalse(self.layout.root.exists())

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

    def test_start_starts_nothing_when_the_safety_check_fails(self) -> None:
        self.mark_built()
        ops = FakeOperations(self)
        ops.roots["roots"]["coordinationRoot"] = "/home/firefox/projects/ar-coordination"

        with self.assertRaisesRegex(SandboxRefusal, "safety check failed"):
            self.start(ops)

        self.assertNotIn("paseo provision", ops.calls)
        self.assertNotIn("spawn dashboard", ops.calls)
        self.assertTrue(any("FAIL coordinationRoot" in line for line in self.lines))

    def test_a_start_that_fails_half_way_stops_what_it_started(self) -> None:
        self.mark_built()
        ops = FakeOperations(self, answers=False)

        self.assertEqual(self.start(ops), 1)

        assert ops.dashboard is not None
        self.assertFalse(is_running(ops.dashboard))
        self.assertEqual(ops.calls[-3:], ["paseo provision", "spawn dashboard", "paseo stop"])
        self.assertIn("start failed at step 'dashboard'", "\n".join(self.lines))
        self.assertIn(f"log file: {self.layout.dashboard_log}", self.lines)
        self.assertNotIn("dashboard", read_record(self.layout))

        # A daemon that ran before the failed start is not this start's to stop.
        before = FakeOperations(self, answers=False)
        before.supervisor = self.child(self.layout.paseo_port)
        self.assertEqual(self.start(before), 1)
        self.assertNotIn("paseo stop", before.calls)
        self.assertTrue(is_running(before.supervisor))

    def test_stop_signals_only_the_process_start_recorded(self) -> None:
        self.mark_built()
        foreign = self.child()
        stale = ProcessIdentity(foreign.pid, foreign.start_ticks - 1, foreign.argv)
        write_record(self.layout, {"dashboard": stale.as_record()})
        self.layout.paseo_home.mkdir(parents=True)
        lock = {"pid": foreign.pid, "startedAt": "2026-10-02T00:00:00.000Z"}
        (self.layout.paseo_home / PASEO_PROCESS_RECORD).write_text(json.dumps(lock), "utf-8")
        ops = FakeOperations(self)
        ops.supervisor = verified_supervisor(self.layout.paseo_home)

        self.assertEqual(commands.stop(self.layout, ops, self.lines.append), 0)

        self.assertTrue(is_running(foreign))
        self.assertEqual(ops.calls, [])
        self.assertIn(
            f"dashboard: not running (the recorded process {foreign.pid} is gone)", self.lines
        )
        self.assertIn("nothing was signalled", self.lines[-1])

        own = self.child()
        write_record(self.layout, {"dashboard": own.as_record()})
        self.assertEqual(commands.stop(self.layout, ops, self.lines.append), 0)
        self.assertFalse(is_running(own))
        self.assertTrue(is_running(foreign))
        self.assertFalse(self.layout.process_record.exists())

    def test_the_paseo_home_record_is_trusted_only_for_the_same_process(self) -> None:
        home = self.layout.paseo_home
        home.mkdir(parents=True)
        supervisor = self.child(name=SUPERVISOR_COMMAND, home=home)
        other_home = self.child(name=SUPERVISOR_COMMAND, home=self.root / "other-home")
        impostor = self.child(home=home)

        def named(identity: ProcessIdentity) -> None:
            record = {"pid": identity.pid, "startedAt": "2026-10-02T00:00:00.000Z"}
            (home / PASEO_PROCESS_RECORD).write_text(json.dumps(record), encoding="utf-8")

        self.assertIsNone(verified_supervisor(home))
        named(supervisor)
        self.assertEqual(verified_supervisor(home), supervisor)
        # The id passed to a process with another command line: the record is stale.
        named(impostor)
        self.assertIsNone(verified_supervisor(home))
        # The id passed to the supervisor of another home: only the exact process that start
        # recorded, same id and same start time, is still trusted.
        named(other_home)
        self.assertIsNone(verified_supervisor(home))
        self.assertEqual(verified_supervisor(home, other_home), other_home)
        earlier = ProcessIdentity(other_home.pid, other_home.start_ticks - 1, other_home.argv)
        self.assertIsNone(verified_supervisor(home, earlier))

        # stop leaves such a supervisor alone, says so, and does not report the sandbox as down.
        ops = FakeOperations(self)
        self.assertEqual(commands.stop(self.layout, ops, self.lines.append), 1)
        self.assertEqual(ops.calls, [])
        self.assertTrue(is_running(other_home))
        self.assertIn("NOT STOPPED", self.lines[-1])


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

        self.layout.root.mkdir()
        (self.layout.root / "notes.txt").write_text("not a sandbox", encoding="utf-8")
        with self.assertRaisesRegex(SandboxRefusal, "not a sandbox this tool built"):
            builder.build(self.layout, self.checkout, ops, self.lines.append, self.root / "no-eve")
        with self.assertRaisesRegex(SandboxRefusal, "refusing to delete"):
            commands.reset(self.layout, ops, self.lines.append)

        self.assertEqual(ops.calls, [])
        self.assertEqual([path.name for path in self.layout.root.iterdir()], ["notes.txt"])
        self.assertFalse((self.layout.root / MARKER_NAME).exists())

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
        base = {
            "PATH": "/usr/bin",
            "HOME": "/home/dev",
            "ANTHROPIC_API_KEY": "kept",
            "OPENAI_API_KEY": "kept",
            "PASEO_HOME": "/home/dev/paseo-spike/home",
            "AR_SPAWN_ROLE": "worker",
            "AR_ORCA_RUNTIME_ROOT": "/orca",
            "AGENTS_REMEMBER_BENCHMARK_MCP_SRC": "/other/src",
            "ORCA_USER_DATA_PATH": "/orca-data",
            "PYTHONPATH": "/other/agents-remember/mcp/src",
            "GIT_DIR": "/home/dev/projects/agents-remember/.git",
            "TMUX": "/tmp/tmux-1000/default,1,0",
        }
        # The calling harness session goes by exact name; what shares its prefix stays.
        calling_session = (
            "CLAUDECODE",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_CODE_ENTRYPOINT",
            "CLAUDE_CODE_EXECPATH",
            "CLAUDE_CODE_MESSAGING_SOCKET",
            "CLAUDE_CODE_MESSAGING_TOKEN",
            "CLAUDE_CODE_SESSION_ATTENDED",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_EFFORT",
            "CLAUDE_PID",
        )
        base.update(dict.fromkeys(calling_session, "of the calling session"))
        same_prefix = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CONFIG_DIR"}
        base.update(dict.fromkeys(same_prefix, "kept"))

        environment = sandbox_environment(base, self.layout)

        kept = {"PATH", "HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", *same_prefix}
        self.assertEqual(set(calling_session) & set(environment), set())
        self.assertEqual(
            {name: environment[name] for name in same_prefix}, dict.fromkeys(same_prefix, "kept")
        )
        self.assertEqual(set(base) - set(environment), set(base) - kept)
        self.assertEqual(removed_names(base), sorted(set(base) - kept))
        self.assertEqual(environment["TMUX_TMPDIR"], self.layout.tmux_dir.as_posix())
        self.assertEqual(environment["PYTHONPYCACHEPREFIX"], self.layout.pycache.as_posix())
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")


if __name__ == "__main__":
    unittest.main()
