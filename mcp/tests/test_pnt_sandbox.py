"""The PNT sandbox tooling (PNT-R11): the safety check's four cases and what a build is given.

The defaulted-root case resolves through the build's own configuration loader, exactly as the
shipped check does. Starting, stopping and the process records are in
``test_pnt_sandbox_processes.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import signal
import sys
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from pnt_sandbox_test_support import (
    MARKER_NAME,
    PASEO_PROCESS_RECORD,
    REPOSITORY,
    REPOSITORY_ID,
    REPOSITORY_ROOT,
    SANDBOX_SCHEMA,
    SLEEPER,
    TASK_ROOT,
    FakeOperations,
    Operations,
    ProcessIdentity,
    SandboxCase,
    SandboxLayout,
    SandboxRefusal,
    StepFailed,
    builder,
    commands,
    embed_entries,
    foreign_variables,
    host_settings_document,
    launcher_scrub,
    location_refusal,
    pi_provider_entry,
    port_holders,
    port_state,
    procfs,
    removed_names,
    resolve_roots,
    safety,
    sandbox_environment,
    sandbox_lock,
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
# The roots the build's own code answers for a settings file with one repository and one
# provider: spelled out, so a root the resolver stops reporting is noticed.
RESOLVED_ROOTS = (
    "configPath", "coordinationRoot", "workspaceRoot", "transcriptRoot", "harnessSkillRoot",
    "agenticSettings", "observerRoot", "dashboardDaemonDir", "daggerAuthorityRoot",
    "receipts.taskless", "receipts.messageBindings", "reports.taskless",
    "paseoRuntime.home",
    "productNode.root", "productNode.cache",
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
        unnamed["roots"]["laterBlock.dataPath"] = "/home/dev/.config/later-block"
        self.assertEqual(self.found(unnamed), ["laterBlock.dataPath"])

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

        # The Pi provider entry is the fixture's or the check fails: without it a sandbox Pi
        # agent loads the developer's extensions, which reach the installed AR server.
        pi_entry = "paseoRuntime.providers.pi"
        self.assertEqual(self.good_roots()["values"][pi_entry], pi_provider_entry())
        changed = {"command": ["pi", "--no-extensions", "-e", "builtin:mcp"]}
        for label, value in (("dropped", None), ("changed", changed), ("emptied", {})):
            with self.subTest(pi_entry=label):
                report = self.good_roots()
                report["values"][pi_entry] = value
                self.assertEqual(self.found(report), [pi_entry])
        unreported = self.good_roots()
        del unreported["values"][pi_entry]
        self.assertEqual(self.found(unreported), [pi_entry])

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
        shared = self.layout.coordination / "system/settings.json"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text(json.dumps(host_settings_document(self.layout, None)))
        document = settings_document(self.layout)
        del document["transcriptRoot"]
        document["providers"] = {"grepai-memory": {}}
        # A path relative to the repository names a file in it, not a root.
        document["repositories"][REPOSITORY_ID] = {"certificationProfile": "mcp/profile.json"}
        # A block the build does not know holds paths outside the sandbox: the loader skips an
        # unknown top-level key, so the build uses none of them and the resolver reports none.
        document["laterBlock"] = {"dataPath": (self.root / "later-block").as_posix()}
        memory = f"{REPOSITORY}.memoryRoot"
        authority = {
            "AR_DAGGER_AUTHORITY_ROOT": self.layout.dagger_authority.as_posix(),
            "XDG_DATA_HOME": str(self.layout.root / "data"),
            "XDG_CACHE_HOME": str(self.layout.root / "cache"),
        }

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
        self.assertEqual(sorted(set(resolved["roots"]) - set(resolved["expected"])), [])
        for key in (memory, "transcriptRoot", "daggerAuthorityRoot", "observerRoot"):
            self.assertNotIn(key, inside)
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

        del document["dashboard"]
        shared.unlink(missing_ok=True)
        _resolved, unconfigured = judged()
        self.assertIn("cannot be resolved", unconfigured["paseoRuntime.home"])
        self.assertIn("8765", unconfigured["dashboard.port"])

    def test_product_node_and_cache_roots_are_required_and_cannot_escape(self) -> None:
        self.mark_built()
        roots = self.good_roots()
        for key in ("productNode.root", "productNode.cache"):
            with self.subTest(key):
                missing = {
                    **roots,
                    "roots": {name: value for name, value in roots["roots"].items() if name != key},
                }
                self.assertIn(
                    key,
                    {
                        item.key
                        for item in safety.evaluate(self.layout, self.checkout, missing).findings
                    },
                )
                outside = {**roots, "roots": {**roots["roots"], key: str(self.root / "outside")}}
                self.assertIn(
                    key,
                    {
                        item.key
                        for item in safety.evaluate(self.layout, self.checkout, outside).findings
                    },
                )
                link = self.layout.root / "escaped-link"
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(self.root / "outside", target_is_directory=True)
                escaped = {**roots, "roots": {**roots["roots"], key: str(link)}}
                self.assertIn(
                    key,
                    {
                        item.key
                        for item in safety.evaluate(self.layout, self.checkout, escaped).findings
                    },
                )
                for report in (missing, outside, escaped):
                    ops = FakeOperations(self)
                    ops.roots = report
                    with self.assertRaisesRegex(SandboxRefusal, "safety check failed"):
                        commands.start(
                            self.layout, self.checkout, ops, self.lines.append, self.root / "no-eve"
                        )
                    self.assertNotIn("runtime_install", ops.calls)
                    self.assertNotIn("spawn dashboard", ops.calls)
                link.unlink()


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
        # Nor is anything stopped there, although the directory holds what a stop acts on.
        records = (self.layout.paseo_home / PASEO_PROCESS_RECORD, self.layout.process_record)
        for record in records:
            record.parent.mkdir(parents=True)
            record.write_text('{"pid": 1}', encoding="utf-8")
        with self.assertRaises(SandboxRefusal) as refused:
            commands.stop(self.layout, ops, self.lines.append)
        self.assertEqual(
            str(refused.exception),
            f"{self.layout.root} is not a sandbox this tool built; nothing was stopped",
        )
        self.assertEqual(
            [record.read_text(encoding="utf-8") for record in records], ['{"pid": 1}'] * 2
        )
        self.assertFalse(self.layout.lock_file.exists())

        self.assertEqual(ops.calls, [])
        self.assertEqual(
            sorted(path.name for path in self.layout.root.iterdir()), ["notes.txt", "paseo", "run"]
        )
        self.assertFalse((self.layout.root / MARKER_NAME).exists())

    def test_the_marker_is_replaced_whole_when_its_state_changes(self) -> None:
        self.mark_built()
        before = self.layout.marker.read_text(encoding="utf-8")
        # The command is killed after the new marker was written and before it took the old
        # one's place: the old marker is still there, complete.
        with (
            mock.patch.object(Path, "replace", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            builder.mark(self.layout, builder.RESETTING)
        self.assertEqual(self.layout.marker.read_text(encoding="utf-8"), before)
        self.assertTrue(builder.is_built(self.layout))

        builder.mark(self.layout, builder.RESETTING)
        marker = json.loads(self.layout.marker.read_text(encoding="utf-8"))
        self.assertEqual(
            marker,
            {"schema": SANDBOX_SCHEMA, "layout": builder.LAYOUT_VERSION, "state": "resetting"},
        )
        # A reset deletes what an interrupted change left beside the marker.
        ops = FakeOperations(self)
        self.assertEqual(commands.reset(self.layout, ops, self.lines.append), 0)
        self.assertFalse(self.layout.root.exists())

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
            self.assertFalse(self.layout.lock_file.exists())
        self.assertEqual(ops.calls, [])
        # While a reset is still at work it holds the lock, and the refusal names it instead.
        with sandbox_lock(self.layout, "reset"):
            for refused in (builder.build, commands.start):
                with self.assertRaises(SandboxRefusal) as running:
                    refused(
                        self.layout, self.checkout, ops, self.lines.append, self.root / "no-eve"
                    )
                self.assertIn(
                    f"another command is running on the sandbox {self.layout.root}: 'reset' "
                    f"(pid {os.getpid()}, since ",
                    str(running.exception),
                )
                self.assertNotIn("did not finish", str(running.exception))
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
        settings = settings_document(layout)
        runtime = host_settings_document(layout, env_file)["paseoRuntime"]

        self.assertEqual(settings["dashboard"], {"autoStart": False, "port": 9797})
        self.assertEqual(runtime["listen"], "127.0.0.1:6820")
        self.assertEqual(
            runtime["embed"],
            [
                {"dashboardOrigin": origin, "frameBaseUrl": "http://127.0.0.1:6820"}
                for origin in ("http://127.0.0.1:9797", "http://localhost:9797")
            ],
        )
        self.assertEqual(runtime["providers"]["hermes"]["command"], ["hermes", "acp"])
        self.assertEqual(runtime["providers"]["eve"]["options"], {"supportsMcpServers": False})
        # Pi starts without the developer's extensions, one of which reaches the installed AR
        # server on the live roots, and with its own MCP, script and tool-search parts. The
        # entry replaces the command and nothing else of the provider Paseo knows by itself.
        self.assertEqual(
            runtime["providers"]["pi"],
            {
                "command": [
                    "pi",
                    "--no-extensions",
                    "-e",
                    "builtin:mcp",
                    "-e",
                    "builtin:codemode",
                    "-e",
                    "builtin:tool-search",
                ]
            },
        )
        self.assertEqual(
            list(host_settings_document(layout, None)["paseoRuntime"]["providers"]),
            ["hermes", "pi"],
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
            "XDG_DATA_HOME": (self.layout.root / "data").as_posix(),
            "XDG_STATE_HOME": (self.layout.root / "state").as_posix(),
            "XDG_CACHE_HOME": (self.layout.root / "cache").as_posix(),
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


def test_public_host_install_receipts_keep_payloads_and_name_admission_failures(
    tmp_path, monkeypatch
):
    layout = SandboxLayout(tmp_path / "sandbox")
    layout.run_dir.mkdir(parents=True)
    ops = Operations(layout, {})
    payload = {"ok": False, "host": {"ok": False, "error": {"code": "install_failed"}}}
    answer = SimpleNamespace(
        returncode=1, stdout=json.dumps({"results": [{"payload": payload}]}), stderr="", tail=""
    )
    monkeypatch.setattr(ops, "run", lambda *a, **k: answer)
    checkout = tmp_path / "candidate"
    assert ops.runtime_install(checkout) == payload
    assert json.loads((layout.run_dir / "host-install-receipt.json").read_text()) == payload
    request = json.loads((layout.run_dir / "host-install-request.json").read_text())
    assert request["expect"]["packageRoot"] == str(checkout / "mcp/src/agents_remember")
    assert request["calls"][0]["tool"] == "runtime_install"
    answer.stdout = json.dumps({"error": "wrong packageRoot", "results": []})
    with pytest.raises(StepFailed, match="wrong packageRoot"):
        ops.runtime_install(checkout)
    answer.stdout = "interrupted"
    answer.tail = "interrupted"
    with pytest.raises(StepFailed, match="unreadable host install receipt: interrupted"):
        ops.runtime_install(checkout)
    current = json.loads((layout.run_dir / "host-install-receipt.json").read_text())
    assert current["ok"] is False and "interrupted" in str(current["error"])
    history = list((layout.run_dir / "host-install-history").glob("*/host-install-receipt.json"))
    assert any(json.loads(path.read_text()) == payload for path in history)
    assert (layout.run_dir / "host-install-command.log").is_file()


def test_foreign_package_admission_prevents_public_install_calls(tmp_path, monkeypatch):

    spec = importlib.util.spec_from_file_location(
        "public_build_calls",
        Path(__file__).resolve().parents[2] / "scripts/pnt_sandbox/build_tool_calls.py",
    )
    assert spec is not None and spec.loader is not None
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    calls = []
    info = {
        "ok": True,
        "coordinationRoot": str(tmp_path / "coordination"),
        "workspaceRoot": str(tmp_path / "projects"),
        "allowedRepoIds": ["sandbox-app"],
        "allowedProviderIds": [],
        "servingBuild": {"packageRoot": "/other/build/mcp/src/agents_remember"},
    }

    class Session:
        def __init__(self, reader, writer):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def call_tool(self, name, _arguments):
            calls.append(name)
            assert name == "server_info", "foreign source reached a mutating tool"
            return SimpleNamespace(model_dump=lambda **kwargs: {"structuredContent": info})

    @contextlib.asynccontextmanager
    async def stream(*args, **kwargs):
        yield None, None

    monkeypatch.setattr(client, "ClientSession", Session)
    monkeypatch.setattr(client, "stdio_client", stream)
    request = {
        "config": str(tmp_path / "settings.json"),
        "cwd": str(tmp_path),
        "serverLog": str(tmp_path / "server.log"),
        "expect": {
            "coordinationRoot": info["coordinationRoot"],
            "workspaceRoot": info["workspaceRoot"],
            "allowedRepoIds": ["sandbox-app"],
            "packageRoot": str(tmp_path / "candidate/mcp/src/agents_remember"),
        },
        "calls": [{"name": "install", "tool": "runtime_install", "arguments": {}}],
    }
    result = asyncio.run(client._run(request))
    assert result["ok"] is False and "package" in result["error"]
    assert calls == ["server_info"] and result["results"] == []


@pytest.mark.parametrize("fault", ["cut", "timeout", "malformed-json", "malformed-shape"])
def test_public_install_retires_success_for_cut_timeout_and_malformed(tmp_path, monkeypatch, fault):
    layout = SandboxLayout(tmp_path / "sandbox")
    ops = Operations(layout, {})
    prior = {"ok": True, "host": {"ok": True, "changed": False}}
    success = json.dumps({"results": [{"payload": prior}]})
    answer = SimpleNamespace(returncode=0, stdout=success, stderr="", tail="")
    seen = []

    def child_run(*args, **kwargs):
        receipt = layout.run_dir / "host-install-receipt.json"
        seen.append(receipt.exists())
        (layout.run_dir / "host-install-server.log").write_text("current controlled server log")
        return answer

    monkeypatch.setattr(ops, "run", child_run)
    checkout = tmp_path / "product"
    assert ops.runtime_install(checkout) == prior
    if fault in {"cut", "timeout"}:
        answer.returncode = 137 if fault == "cut" else 124
        answer.stderr = answer.tail = f"controlled {fault}; a success body is untrusted"
    else:
        answer.stdout = "cut JSON" if fault == "malformed-json" else '{"results": [7]}'
        answer.tail = answer.stdout
    with pytest.raises(StepFailed) as failure:
        ops.runtime_install(checkout)
    assert seen == [False, False]
    current = json.loads((layout.run_dir / "host-install-receipt.json").read_text())
    assert current["ok"] is False
    assert failure.value.log is not None and failure.value.log.is_file()
    assert (layout.run_dir / "host-install-server.log").is_file()
    assert current["receipt"] == str(layout.run_dir / "host-install-receipt.json")
    assert current["log"] == str(failure.value.log)
    assert current["serverLog"] == str(layout.run_dir / "host-install-server.log")
    if fault in {"cut", "timeout"}:
        assert f"controlled {fault}" in current["error"]["message"]
        assert str(answer.returncode) in current["error"]["message"]
    else:
        assert answer.stdout in current["error"]["message"]
    history = list((layout.run_dir / "host-install-history").glob("*/host-install-receipt.json"))
    assert len(history) == 1 and json.loads(history[0].read_text()) == prior
