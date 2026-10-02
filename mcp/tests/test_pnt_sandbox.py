"""The PNT sandbox tooling (PNT-R11): the safety check's four cases and what a build is given.

The defaulted-root case resolves through the build's own configuration loader, exactly as the
shipped check does. Starting, stopping and the process records are in
``test_pnt_sandbox_processes.py``.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import unittest
import warnings
from pathlib import Path
from typing import Any
from unittest import mock

from pnt_sandbox_test_support import (
    MARKER_NAME,
    REPOSITORY,
    REPOSITORY_ID,
    REPOSITORY_ROOT,
    SLEEPER,
    TASK_ROOT,
    FakeOperations,
    Operations,
    ProcessIdentity,
    SandboxCase,
    SandboxLayout,
    SandboxRefusal,
    builder,
    commands,
    embed_entries,
    foreign_variables,
    launcher_scrub,
    location_refusal,
    port_holders,
    port_state,
    procfs,
    removed_names,
    resolve_roots,
    safety,
    sandbox_environment,
    settings_document,
)

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

    def test_a_reset_that_cannot_delete_everything_can_be_repeated(self) -> None:
        self.mark_built()
        ops = FakeOperations(self)
        for name in ("projects", "run", "settings"):
            (self.layout.root / name / "kept").mkdir(parents=True)
        real = commands.shutil.rmtree

        def blocked(path: Path) -> None:
            if path.name == "run":
                raise PermissionError(13, "Permission denied", "kept")
            real(path)

        with mock.patch.object(commands.shutil, "rmtree", blocked):
            self.assertEqual(commands.reset(self.layout, ops, self.lines.append), 1)

        self.assertIn(
            f"{self.layout.root} could not be deleted completely (Permission denied); left "
            f"there: {self.layout.root / 'run'}. It is still marked",
            self.lines[-1],
        )
        self.assertIn("then run 'reset' again", self.lines[-1])
        self.assertEqual(
            sorted(path.name for path in self.layout.root.iterdir()), [MARKER_NAME, "run"]
        )
        # What is left is still this tool's, and nothing is built or started in it.
        for refused in (builder.build, commands.start):
            with self.assertRaisesRegex(SandboxRefusal, "did not finish; run 'reset' again"):
                refused(self.layout, self.checkout, ops, self.lines.append, self.root / "no-eve")
        self.assertEqual(ops.calls, [])

        # The same when a program wrote into the directory while it was being deleted.
        late = self.layout.root / "late.txt"

        def written_meanwhile(path: Path) -> None:
            real(path)
            late.write_text("", encoding="utf-8")

        with mock.patch.object(commands.shutil, "rmtree", written_meanwhile):
            self.assertEqual(commands.reset(self.layout, ops, self.lines.append), 1)
        self.assertIn(f"during the deletion); left there: {late}. It is still", self.lines[-1])
        self.assertEqual(
            sorted(path.name for path in self.layout.root.iterdir()), [MARKER_NAME, late.name]
        )
        # And when the emptied directory itself cannot be removed: it keeps its marker.
        full = OSError(39, "Directory not empty")
        with mock.patch.object(Path, "rmdir", side_effect=full):
            self.assertEqual(commands.reset(self.layout, ops, self.lines.append), 1)
        self.assertIn(f"(Directory not empty); left there: {self.layout.root}. ", self.lines[-1])
        self.assertEqual([path.name for path in self.layout.root.iterdir()], [MARKER_NAME])
        self.assertFalse(builder.is_built(self.layout))

        self.assertEqual(commands.reset(self.layout, ops, self.lines.append), 0)
        self.assertFalse(self.layout.root.exists())

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
        # Right after its start a child can still show an empty command line: the identity that
        # is recorded is the one it shows once the command line it was given is in place.
        real, early = procfs.read_identity, []

        def too_early(pid: int) -> ProcessIdentity | None:
            identity = real(pid)
            if early or identity is None:
                return identity
            early.append(identity)
            return ProcessIdentity(pid, identity.start_ticks, ("",))

        with warnings.catch_warnings(), mock.patch.object(procfs, "read_identity", too_early):
            # The dashboard is left running on purpose; Python remarks on that when it lets go.
            warnings.simplefilter("ignore", ResourceWarning)
            dashboard = ops.spawn_dashboard(self.checkout)
        self.addCleanup(os.kill, dashboard.pid, signal.SIGKILL)
        self.assertEqual(len(early), 1)
        self.assertTrue(procfs.is_running(dashboard))
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
