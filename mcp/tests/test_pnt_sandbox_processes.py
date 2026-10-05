"""The PNT sandbox tooling (PNT-R11): start, stop, reset, the lock and the process records.

See ``pnt_sandbox_test_support.py`` for the fake and its real child processes.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import sys
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pnt_sandbox_test_support import (
    LISTENER,
    PNT_BUILD_FILES,
    SUPERVISOR,
    SUPERVISOR_COMMAND,
    FakeOperations,
    Operations,
    ProcessIdentity,
    SandboxCase,
    SandboxLayout,
    SandboxRefusal,
    StepFailed,
    builder,
    clear_stale_paseo_record,
    commands,
    inspect_paseo_record,
    is_running,
    lock,
    port_holders,
    procfs,
    read_record,
    record,
    sandbox_lock,
    verified_supervisor,
    write_record,
)


def open_files(pid: int) -> set[str]:
    """What the descriptors of a process point at; one it closed since the listing is skipped."""
    targets: set[str] = set()
    for entry in Path(f"/proc/{pid}/fd").iterdir():
        with contextlib.suppress(OSError):
            targets.add(os.readlink(entry))
    return targets


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
        # A sandbox of an earlier layout counts as missing: the build adds what is new.
        self.mark_built(layout=builder.LAYOUT_VERSION - 1)
        self.assertFalse(builder.is_built(self.layout))

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

        shared: list[bool] = []

        def hold() -> None:
            # The lock is handed on, for the provision run to share.
            shared.append(first.held_lock is not None)
            provisioning.set()
            proceed.wait(30)

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
        self.assertEqual((outcome, shared, first.held_lock), ([0], [True], None))
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

        # The record loses only what this start wrote: entries that name other processes by the
        # time it fails are someone else's and stay.
        others = {"dashboard": self.child().as_record(), "paseo": self.child().as_record()}
        replaced = FakeOperations(self, answers=False)
        replaced.while_waiting = lambda: write_record(
            self.layout, {**read_record(self.layout), **others}
        )
        self.assertEqual(self.start(replaced), 1)
        record = read_record(self.layout)
        self.assertEqual({name: record.get(name) for name in others}, others)

    def test_a_failed_start_whose_runtime_cannot_be_stopped_says_so_and_keeps_its_record(
        self,
    ) -> None:
        self.mark_built()

        class Unstoppable(FakeOperations):
            report: dict[str, Any] | None = None

            def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
                if command != "stop":
                    return super().paseo(checkout, command, timeout)
                self.note("paseo stop", checkout)
                if self.report is None:
                    raise StepFailed("paseo stop", "no report from the command: timed out")
                return self.report

        refusing = {"ok": False, "error": {"message": "the daemon did not end"}}
        for report, said in (
            (None, "no report from the command: timed out"),
            (refusing, "the daemon did not end"),
        ):
            with self.subTest(said):
                ops = Unstoppable(self, answers=False)
                ops.report = report

                self.assertEqual(self.start(ops), 1)

                assert ops.supervisor is not None and ops.dashboard is not None
                self.assertEqual(ops.calls[-1], "paseo stop")
                self.assertIn(
                    f"paseo runtime: STILL RUNNING (pid {ops.supervisor.pid}): {said}", self.lines
                )
                # The dashboard this start started is gone from the record; the runtime, which
                # still runs, stays in it, and the lock is released.
                record = read_record(self.layout)
                self.assertNotIn("dashboard", record)
                self.assertEqual(record["paseo"], ops.supervisor.as_record())
                self.assertFalse(is_running(ops.dashboard))
                self.assertTrue(is_running(ops.supervisor))
                self.assertFalse(self.layout.lock_file.exists())
                # The next stop stops the runtime.
                later = FakeOperations(self)
                later.supervisor = ops.supervisor
                self.assertEqual(self.stop(later), 0)
                self.assertFalse(is_running(ops.supervisor))

    def test_a_provision_without_a_report_is_undone_only_when_it_started_the_daemon(self) -> None:
        self.mark_built()

        class Silent(FakeOperations):
            starts_one = False

            def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
                if command != "provision":
                    return super().paseo(checkout, command, timeout)
                self.note("paseo provision", checkout)
                if self.starts_one:
                    self.supervisor = self.case.supervisor()
                raise StepFailed("paseo provision", "no report from the command: timed out")

        before = Silent(self)
        before.supervisor = ran_before = self.supervisor()
        self.assertEqual(self.start(before), 1)
        self.assertEqual(before.calls[-1], "paseo provision")
        self.assertTrue(is_running(ran_before))
        self.assertIn("start failed at step 'paseo provision': no report", "\n".join(self.lines))
        self.assertEqual(self.stop(before), 0)

        started = Silent(self)
        started.starts_one = True
        self.assertEqual(self.start(started), 1)
        self.assertEqual(started.calls[-2:], ["paseo provision", "paseo stop"])
        self.assertIsNone(started.supervisor)

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

        class Unreadable(FakeOperations):
            def process_environment(self, pid: int) -> dict[str, str] | None:
                self.case.assertGreater(pid, 1)
                return None

        cases: dict[str, tuple[type[FakeOperations], dict[str, str]]] = {
            "CLAUDE_CODE_SESSION_ID": (FakeOperations, {"CLAUDE_CODE_SESSION_ID": "caller"}),
            "AR_SPAWN_ROLE, TMUX_TMPDIR": (
                FakeOperations,
                {"AR_SPAWN_ROLE": "worker", "TMUX_TMPDIR": "/tmp"},
            ),
            "cannot be read": (Unreadable, {}),
        }
        for names, (operations, carried) in cases.items():
            with self.subTest(names):
                ops = operations(self)
                ops.supervisor = supervisor = self.supervisor(listening=False, env=carried)
                with self.assertRaises(SandboxRefusal) as refused:
                    self.start(ops)
                self.assertIn(f"(pid {supervisor.pid})", str(refused.exception))
                self.assertIn(names, str(refused.exception))
                self.assertIn("'stop', then 'start'", str(refused.exception))
                self.assertEqual(ops.calls, [])
                self.assertTrue(is_running(supervisor))
                self.end(supervisor)

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
        # A record of an earlier boot names nothing: start times repeat across boots. A record
        # without a boot id is the earlier tooling's and is read; its dashboard is stopped.
        aged = self.dashboard(listening=False)
        write_record(self.layout, {"dashboard": aged.as_record()})
        stored = json.loads(self.layout.process_record.read_text(encoding="utf-8"))
        self.layout.process_record.write_text(json.dumps({**stored, "bootId": "an earlier boot"}))
        self.assertEqual(read_record(self.layout), {})
        del stored["bootId"]
        self.layout.process_record.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(self.stop(ops), 0)
        self.assertEqual(self.lines[-2], f"dashboard: stopped (pid {aged.pid})")
        self.assertFalse(is_running(aged))
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
        # Left by a start that was killed before the dashboard had bound its port.
        unrecorded = self.dashboard(listening=False)

        self.assertEqual(self.stop(ops), 1)
        self.assertIn(f"dashboard: NOT STOPPED: process {unrecorded.pid}", self.lines[-1])
        self.assertEqual(commands.reset(self.layout, ops, self.lines.append), 1)
        with self.assertRaisesRegex(SandboxRefusal, f"process {unrecorded.pid} runs this sandbox"):
            self.start(ops)

        self.assertTrue(is_running(unrecorded))
        self.assertTrue(self.layout.marker.is_file())
        self.assertIn("was not deleted", self.lines[-1])
        self.end(unrecorded)

        # The same for a supervisor of this home that the home's own record does not name.
        stray = self.supervisor(listening=False)
        self.assertEqual(self.stop(ops), 1)
        self.assertIn(f"paseo runtime: NOT STOPPED: process {stray.pid}", self.lines[-1])
        with self.assertRaisesRegex(SandboxRefusal, f"process {stray.pid} is a Paseo supervisor"):
            self.start(ops)
        self.assertEqual(ops.calls, [])
        self.assertTrue(is_running(stray))
        self.end(stray)

        # A runtime that does not stop keeps the directory as well.

        class Stuck(FakeOperations):
            def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
                self.note(f"paseo {command} for {timeout:g} s", checkout)
                return {"ok": False, "error": {"message": "refused"}}

        stuck = Stuck(self)
        stuck.supervisor = self.supervisor()
        self.assertEqual(commands.reset(self.layout, stuck, self.lines.append), 1)
        self.assertIn("paseo runtime: STILL RUNNING", "\n".join(self.lines))
        self.assertTrue(self.layout.marker.is_file())

    def test_one_directory_is_one_sandbox_however_it_is_spelled(self) -> None:
        self.mark_built()
        ops = FakeOperations(self)
        link = self.root / "alias"
        link.symlink_to(self.layout.root, target_is_directory=True)
        ports = {"paseo_port": self.layout.paseo_port, "dashboard_port": self.layout.dashboard_port}
        dotted = self.layout.root.parent / "elsewhere" / ".." / self.layout.root.name
        for spelled in (link, dotted):
            other = SandboxLayout(spelled, **ports)
            self.assertEqual(other, self.layout)
            self.assertEqual(other.lock_file, self.layout.lock_file)
            self.assertEqual(other.settings_file, self.layout.settings_file)

        self.assertEqual(self.start(ops), 0)
        dashboard = ops.dashboard
        self.assertEqual(commands.stop(SandboxLayout(link, **ports), ops, self.lines.append), 0)

        self.assertFalse(is_running(dashboard))
        self.assertIsNone(ops.supervisor)
        self.assertIn("dashboard: stopped", self.lines[-2])
        self.assertIn("paseo runtime: stopped", self.lines[-1])

    def test_the_lock_covers_a_build_and_a_provision_run_and_is_the_file_that_is_there(
        self,
    ) -> None:
        self.mark_built()
        path = self.layout.lock_file
        with sandbox_lock(self.layout, "start"), self.assertRaisesRegex(SandboxRefusal, "'start'"):
            builder.build(self.layout, self.checkout, FakeOperations(self), print, self.root)

        # The holder before us deletes the file between our opening and our locking it: the
        # lock that counts is on the file that is there afterwards.
        real, raced = lock.fcntl.flock, []

        def released_meanwhile(handle: Any, operation: int) -> None:
            if not raced:
                raced.append(path.unlink())
            real(handle, operation)

        with (
            mock.patch.object(lock.fcntl, "flock", released_meanwhile),
            sandbox_lock(self.layout, "stop"),
            self.assertRaisesRegex(SandboxRefusal, "'stop'"),
            sandbox_lock(self.layout, "start"),
        ):
            self.fail("two commands held the lock of one sandbox")
        self.assertFalse(path.exists())

        # A holder that has not written its record yet is still named, and so is the run that
        # keeps the lock of a command that has ended (no process has that id).
        ended = {"command": "start", "pid": 2**22 + 1, "since": "now"}
        holder = json.dumps({**ended, "child": {"name": "paseo provision", "pid": 4242}})
        with open(path, "a+", encoding="utf-8") as early:
            real(early, lock.fcntl.LOCK_EX)
            late = threading.Timer(0.3, lambda: (early.write(holder), early.flush()))
            late.start()
            with self.assertRaises(SandboxRefusal) as refused, sandbox_lock(self.layout, "stop"):
                self.fail("a second command took a lock that is held")
            late.join()
        self.assertIn(
            "its 'paseo provision' run (pid 4242) is still at work; 'start' "
            f"(pid {ended['pid']}, since now) itself has ended",
            str(refused.exception),
        )
        path.unlink()

        # A provision run shares the lock and is named: were the start killed, the sandbox
        # would stay locked until the run has ended.
        release = self.root / "release"
        program = (
            "import json, os, sys, time\n"
            "while not os.path.exists(sys.argv[1]):\n    time.sleep(0.05)\n"
            "print(json.dumps({'ok': True, 'names': sorted(os.environ)}))\n"
        )

        class Slow(Operations):
            def paseo_argv(self, checkout: Path, command: str) -> list[str]:
                return [sys.executable, "-c", program, release.as_posix(), checkout.name, command]

        ops = Slow(self.layout, {})
        reports: list[dict[str, Any]] = []
        afterwards: list[str] = []

        def provision() -> None:
            with sandbox_lock(self.layout, "start") as held:
                ops.held_lock = held
                reports.append(ops.paseo(self.checkout, "provision", 60))
                afterwards.append(path.read_text(encoding="utf-8"))

        thread = threading.Thread(target=provision)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.touch)
        self.assertTrue(
            procfs.wait_until(lambda: path.is_file() and "child" in path.read_text(), 30)
        )
        child = json.loads(path.read_text(encoding="utf-8"))["child"]["pid"]
        with self.assertRaises(SandboxRefusal) as refused, sandbox_lock(self.layout, "stop"):
            self.fail("a second command ran beside a provision run")
        self.assertRegex(
            str(refused.exception),
            rf"'start' \(pid {os.getpid()}, since [^)]+\), with its 'paseo provision' run "
            rf"\(pid {child}\);",
        )
        held_by_child = open_files(child)
        self.assertIn(path.as_posix(), held_by_child)
        release.touch()
        thread.join()
        self.assertEqual([report["ok"] for report in reports], [True])
        self.assertEqual(
            [sorted(json.loads(text)) for text in afterwards], [["command", "pid", "since"]]
        )
        self.assertLessEqual(
            {"AR_DAGGER_AUTHORITY_ROOT", "TMUX_TMPDIR", "PWD"}, set(reports[0]["names"])
        )

    def test_the_paseo_home_record_is_trusted_only_for_this_homes_supervisor(self) -> None:
        home = self.layout.paseo_home
        own = self.supervisor(listening=False)
        other_home = self.supervisor(self.root / "other-home", listening=False)
        impostor = self.child(env={"PASEO_HOME": home.as_posix()})

        homeless = self.child(name=SUPERVISOR_COMMAND)

        self.assertIsNone(verified_supervisor(home))
        self.assertEqual(inspect_paseo_record(home).kind, "absent")
        path = self.paseo_record(own)
        self.assertEqual(verified_supervisor(home), own)
        self.assertEqual(clear_stale_paseo_record(home).kind, "own")
        self.assertTrue(path.is_file())
        # The id passed to a process with another command line, to another home's supervisor, or
        # to a supervisor that names no home.
        for stale in (impostor, other_home, homeless):
            self.paseo_record(stale)
            self.assertIsNone(verified_supervisor(home))
            self.assertEqual(inspect_paseo_record(home).kind, "stale")

        # A supervisor whose environment cannot be read is neither this home's nor stale: the
        # record stays and no runtime command is run.
        self.mark_built()
        self.paseo_record(own)
        ops = FakeOperations(self)
        with mock.patch.object(record, "environment", return_value=None):
            self.assertEqual(clear_stale_paseo_record(home).kind, "unreadable")
            self.assertEqual(self.stop(ops), 1)
            self.assertIn("NOT STOPPED", self.lines[-1])
            self.assertIn("cannot be read", self.lines[-1])
            with self.assertRaisesRegex(SandboxRefusal, "cannot be read"):
                self.start(ops)
        self.assertTrue(path.is_file())
        self.assertNotIn("paseo provision", ops.calls)
        self.assertNotIn("paseo stop", ops.calls)
        self.end(own)

        # A record that names no process is deleted, and the command says so.
        for unusable in ("not a record", '{"pid": true}', '{"pid": 1}'):
            with self.subTest(unusable):
                path.write_text(unusable, encoding="utf-8")
                self.assertEqual(self.stop(ops), 0)
                self.assertEqual(
                    self.lines[-2],
                    f"paseo runtime: deleted the stale record {path}: it named no process",
                )
                self.assertFalse(path.exists())

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
                self.assertIn(f"deleted the stale record {record}", self.lines[-2])
                self.assertIn(f"it named process {process.pid}", self.lines[-2])
                self.assertEqual(self.lines[-1], "paseo runtime: not running")
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


if __name__ == "__main__":
    unittest.main()
