from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers import provider_lifecycle  # noqa: E402
from agents_remember.providers import lifecycle_service  # noqa: E402


class ProviderLifecycleRenderTests(unittest.TestCase):
    def test_captured_command_output_is_streamed_without_wrapper(self) -> None:
        data = {
            "provider": "codegraphcontext",
            "action": "run",
            "ok": True,
            "command": {
                "stdout": "native stdout\n",
                "stderr": "native stderr\n",
                "returncode": 0,
            },
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rendered = provider_lifecycle.render_captured_command_output(data)

        self.assertTrue(rendered)
        self.assertEqual(stdout.getvalue(), "native stdout\n")
        self.assertEqual(stderr.getvalue(), "native stderr\n")
        self.assertNotIn("command:", stdout.getvalue())

    def test_captured_command_output_ignores_non_command_results(self) -> None:
        self.assertFalse(provider_lifecycle.render_captured_command_output({"ok": False, "error": "missing"}))

    def test_cgc_run_json_streams_native_output_by_default(self) -> None:
        data = {
            "provider": "codegraphcontext",
            "action": "run",
            "ok": True,
            "command": {"stdout": "native stdout\n", "stderr": "", "returncode": 0},
        }
        args = SimpleNamespace(json=True, dry_run=False, lifecycle_json=False)
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            rendered = provider_lifecycle.render_cgc_run_result(data, args)

        self.assertTrue(rendered)
        self.assertEqual(stdout.getvalue(), "native stdout\n")
        self.assertNotIn('"command"', stdout.getvalue())

    def test_cgc_run_lifecycle_json_uses_compact_api_payload(self) -> None:
        data = {
            "provider": "codegraphcontext",
            "action": "run",
            "ok": True,
            "repoId": "example-repo",
            "command": {
                "stdout": "╭─ Table\n│ hit\n",
                "stderr": "",
                "returncode": 0,
                "durationSeconds": 0.123,
            },
        }
        args = SimpleNamespace(json=True, dry_run=False, lifecycle_json=True)
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            rendered = provider_lifecycle.render_cgc_run_result(data, args)

        self.assertTrue(rendered)
        self.assertIn('"outputLines"', stdout.getvalue())
        self.assertIn("╭─ Table", stdout.getvalue())
        self.assertIn('"returncode": 0', stdout.getvalue())
        self.assertNotIn('"command"', stdout.getvalue())
        self.assertNotIn("\\u256d", stdout.getvalue())


class ProviderLifecycleParserTests(unittest.TestCase):
    def parse_cgc(self, argv: list[str]):
        parser = provider_lifecycle.build_parser()
        args = parser.parse_args(["cgc", *argv])
        provider_lifecycle.normalize_cgc_args(args)
        args.coordination_root = args.coordination_root.resolve()
        if args.code_repo_root is not None:
            args.code_repo_root = args.code_repo_root.resolve()
        if args.repo_id is not None:
            args.repo_id = provider_lifecycle.stable_provider_id(args.repo_id)
        return args

    def parse_grepai(self, argv: list[str]):
        parser = provider_lifecycle.build_parser()
        args = parser.parse_args(["grepai", *argv])
        provider_lifecycle.normalize_grepai_args(args)
        args.coordination_root = args.coordination_root.resolve()
        if args.root is not None:
            args.root = args.root.resolve()
        if args.runtime_root is not None:
            args.runtime_root = args.runtime_root.resolve()
        return args

    def service_config(self, root: Path) -> lifecycle_service.ProviderLifecycleServiceConfig:
        coordination_root = root / "coordination"
        repo = root / "workspace" / "repo-a"
        memory = coordination_root / "memory-repos" / "memory-a"
        repo.mkdir(parents=True)
        memory.mkdir(parents=True)
        settings_path = root / "lifecycle-settings.json"
        provider_lifecycle.write_json(
            settings_path,
            {
                "contextProviders": {
                    "enabled": True,
                    "providers": {
                        "codegraphcontext-code": {
                            "enabled": True,
                            "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                            "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                            "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
                            "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                            "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                            "roots": [{"repoId": "repo-a", "path": repo.as_posix()}],
                            "backend": {
                                "runtimeRoot": "<coordination_root>/providers/data/codegraphcontext/falkordb",
                                "dataRoot": "<backendRuntimeRoot>/data",
                            },
                        },
                        "grepai-memory": {
                            "enabled": True,
                            "runtimeRoot": "<coordination_root>/providers/runners/grepai",
                            "roots": [{"projectId": "memory-a", "path": memory.as_posix()}],
                        },
                    },
                }
            },
        )
        return lifecycle_service.ProviderLifecycleServiceConfig(
            coordination_root=coordination_root,
            settings_path=settings_path,
            dry_run=True,
            timeout=1,
        )

    def test_visualize_accepts_named_options_after_subcommand(self) -> None:
        args = self.parse_cgc(
            [
                "visualize",
                "--coordination-root",
                "/tmp/ar",
                "--repo-id",
                "device-management",
                "--port",
                "8123",
                "--context",
                "default",
            ]
        )

        self.assertEqual(args.action, "visualize")
        self.assertEqual(args.port, 8123)
        self.assertEqual(args.context, "default")
        self.assertFalse(hasattr(args, "native_args"))

    def test_common_options_can_still_appear_before_subcommand(self) -> None:
        args = self.parse_cgc(
            [
                "--coordination-root",
                "/tmp/ar",
                "--repo-id",
                "device-management",
                "visualize",
                "--port",
                "8123",
            ]
        )

        self.assertEqual(args.action, "visualize")
        self.assertEqual(args.repo_id, "device-management")
        self.assertEqual(args.port, 8123)

    def test_cgc_defaults_coordination_root_to_installed_runtime_root(self) -> None:
        args = self.parse_cgc(
            [
                "status",
                "--repo-id",
                "device-management",
            ]
        )

        self.assertEqual(args.coordination_root, provider_lifecycle.default_coordination_root().resolve())

    def test_watchers_defaults_coordination_root_to_installed_runtime_root(self) -> None:
        parser = provider_lifecycle.build_parser()
        args = parser.parse_args(["watchers", "status"])

        self.assertEqual(args.coordination_root, provider_lifecycle.default_coordination_root())

    def test_grepai_run_dry_run_builds_managed_workspace_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            runtime_root = coordination_root / "providers" / "grepai"
            binary_path = coordination_root / "providers" / "_bin" / ("grepai.exe" if provider_lifecycle.os.name == "nt" else "grepai")
            binary_path.parent.mkdir(parents=True)
            binary_path.write_text("", encoding="utf-8")

            args = self.parse_grepai(
                [
                    "run",
                    "--coordination-root",
                    str(coordination_root),
                    "--runtime-root",
                    str(runtime_root),
                    "--dry-run",
                    "--",
                    "search",
                    "provider lifecycle",
                    "--workspace",
                    "agents-remember-memory",
                    "--project",
                    "memory-repos",
                    "--json",
                    "--compact",
                    "--limit",
                    "5",
                ]
            )

            result = provider_lifecycle.grepai_run(args, "run")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "run")
        self.assertEqual(result["workspace"], "agents-remember-memory")
        self.assertEqual(result["command"][1:4], ["search", "provider lifecycle", "--workspace"])
        self.assertEqual(result["cwd"], runtime_root.resolve().as_posix())
        self.assertIn("HOME", result["env"])

    def test_cgc_service_run_builds_command_without_cli_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.service_config(Path(tmp_dir))

            result = lifecycle_service.run_cgc_lifecycle(
                service_config,
                action="run",
                repo_id="repo-a",
                native_args=["find", "name", "Token"],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "codegraphcontext")
        self.assertEqual(result["action"], "run")
        self.assertEqual(result["command"][-3:], ["find", "name", "Token"])

    def test_watchers_service_reads_settings_without_cli_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.service_config(Path(tmp_dir))

            result = lifecycle_service.run_watchers_lifecycle(
                service_config,
                action="status",
            )

        self.assertEqual(result["provider"], "watchers")
        self.assertEqual(result["action"], "status")
        self.assertTrue(result["enabled"]["codegraphcontext-code"])
        self.assertTrue(result["enabled"]["grepai-memory"])

    def test_grepai_run_rejects_watcher_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            runtime_root = coordination_root / "providers" / "grepai"
            binary_path = coordination_root / "providers" / "_bin" / ("grepai.exe" if provider_lifecycle.os.name == "nt" else "grepai")
            binary_path.parent.mkdir(parents=True)
            binary_path.write_text("", encoding="utf-8")
            args = self.parse_grepai(
                [
                    "run",
                    "--coordination-root",
                    str(coordination_root),
                    "--runtime-root",
                    str(runtime_root),
                    "--dry-run",
                    "--",
                    "watch",
                    "--workspace",
                    "agents-remember-memory",
                ]
            )

            with self.assertRaisesRegex(provider_lifecycle.ContextProviderError, "use grepai start/stop/refresh"):
                provider_lifecycle.grepai_run(args, "run")

    def test_ephemeral_namespace_rejects_daemon_actions(self) -> None:
        original = provider_lifecycle.process_namespace_warning
        provider_lifecycle.process_namespace_warning = lambda: "sandbox init has --die-with-parent"
        try:
            with self.assertRaisesRegex(
                provider_lifecycle.ContextProviderError,
                "must run outside this ephemeral process namespace",
            ):
                provider_lifecycle.require_durable_process_namespace("watchers start")
        finally:
            provider_lifecycle.process_namespace_warning = original

    def test_process_namespace_status_reports_warning(self) -> None:
        original = provider_lifecycle.process_namespace_warning
        provider_lifecycle.process_namespace_warning = lambda: "sandbox init has --die-with-parent"
        try:
            self.assertEqual(
                provider_lifecycle.process_namespace_status(),
                {
                    "durableForDaemons": False,
                    "warning": "sandbox init has --die-with-parent",
                },
            )
        finally:
            provider_lifecycle.process_namespace_warning = original

    def test_visualize_rejects_ephemeral_process_namespace(self) -> None:
        original = provider_lifecycle.process_namespace_warning
        provider_lifecycle.process_namespace_warning = lambda: "sandbox init has --die-with-parent"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                repo = root / "repo"
                repo.mkdir()
                args = self.parse_cgc(
                    [
                        "visualize",
                        "--coordination-root",
                        str(root / "coordination"),
                        "--repo-id",
                        "repo",
                        "--code-repo-root",
                        str(repo),
                        "--port",
                        "8123",
                    ]
                )

                with self.assertRaisesRegex(
                    provider_lifecycle.ContextProviderError,
                    "must run outside this ephemeral process namespace",
                ):
                    provider_lifecycle.cgc_visualize(args)
        finally:
            provider_lifecycle.process_namespace_warning = original

    def test_visualize_dry_run_builds_explicit_server_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = root / "repo"
            repo.mkdir()
            args = self.parse_cgc(
                [
                    "visualize",
                    "--coordination-root",
                    str(root / "coordination"),
                    "--repo-id",
                    "repo",
                    "--code-repo-root",
                    str(repo),
                    "--dry-run",
                    "--port",
                    "8123",
                ]
            )

            result = provider_lifecycle.cgc_visualize(args)

        self.assertTrue(result["ok"])
        self.assertTrue(result["longRunning"])
        self.assertEqual(result["action"], "visualize")
        self.assertEqual(result["url"], "http://127.0.0.1:8123")
        self.assertEqual(result["command"][1:5], ["visualize", "--repo", repo.resolve().as_posix(), "--port"])
        self.assertEqual(result["command"][5], "8123")

    def test_run_rejects_visualizer_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = root / "repo"
            repo.mkdir()
            args = self.parse_cgc(
                [
                    "run",
                    "--coordination-root",
                    str(root / "coordination"),
                    "--repo-id",
                    "repo",
                    "--code-repo-root",
                    str(repo),
                    "--dry-run",
                    "--",
                    "visualize",
                    "--port",
                    "8123",
                ]
            )

            with self.assertRaisesRegex(
                provider_lifecycle.ContextProviderError,
                "use cgc visualize",
            ):
                provider_lifecycle.cgc_run(args)

    def test_run_allows_bounded_query_in_ephemeral_process_namespace(self) -> None:
        originals = {
            "process_namespace_warning": provider_lifecycle.process_namespace_warning,
            "ensure_cgc_runtime_layout": provider_lifecycle.ensure_cgc_runtime_layout,
            "cgc_status": provider_lifecycle.cgc_status,
            "run_command": provider_lifecycle.run_command,
        }
        provider_lifecycle.process_namespace_warning = lambda: "sandbox init has --die-with-parent"
        provider_lifecycle.ensure_cgc_runtime_layout = lambda layout: None
        provider_lifecycle.cgc_status = lambda args: {"ok": True}
        provider_lifecycle.run_command = lambda command, **kwargs: {
            "stdout": "hit\n",
            "stderr": "",
            "returncode": 0,
            "durationSeconds": 0.01,
        }
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                repo = root / "repo"
                repo.mkdir()
                args = self.parse_cgc(
                    [
                        "run",
                        "--coordination-root",
                        str(root / "coordination"),
                        "--repo-id",
                        "repo",
                        "--code-repo-root",
                        str(repo),
                        "--",
                        "find",
                        "name",
                        "Token",
                    ]
                )

                result = provider_lifecycle.cgc_run(args)

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "run")
            self.assertEqual(result["command"]["stdout"], "hit\n")
        finally:
            provider_lifecycle.process_namespace_warning = originals["process_namespace_warning"]
            provider_lifecycle.ensure_cgc_runtime_layout = originals["ensure_cgc_runtime_layout"]
            provider_lifecycle.cgc_status = originals["cgc_status"]
            provider_lifecycle.run_command = originals["run_command"]

    def test_grepai_start_output_pid_is_parsed(self) -> None:
        output = "\n".join(
            [
                "Workspace watcher agents-remember-memory started (PID 705881)",
                "Logs: /tmp/grepai-workspace-agents-remember-memory.log",
            ]
        )

        self.assertEqual(provider_lifecycle.grepai_watch_pid_from_output(output), 705881)
        self.assertIsNone(provider_lifecycle.grepai_watch_pid_from_output("Status: not running"))

    def test_docker_wait_for_postgres_requires_database_query(self) -> None:
        backend = {
            "containerName": "grepai-postgres",
            "postgresPassword": "grepai",
            "postgresUser": "grepai",
            "postgresDatabase": "grepai",
        }
        calls: list[list[str]] = []
        originals = {
            "docker_command": provider_lifecycle.docker_command,
            "run_command": provider_lifecycle.run_command,
        }

        def fake_run_command(command, **kwargs):
            calls.append(command)
            return {"returncode": 0, "stdout": "ok\n", "stderr": ""}

        provider_lifecycle.docker_command = lambda: "docker"
        provider_lifecycle.run_command = fake_run_command
        try:
            result = provider_lifecycle.docker_wait_for_postgres(backend, cwd=Path("/tmp"), timeout=1)
        finally:
            provider_lifecycle.docker_command = originals["docker_command"]
            provider_lifecycle.run_command = originals["run_command"]

        self.assertEqual(result["returncode"], 0)
        self.assertIn("pg_isready", calls[0])
        self.assertIn("psql", calls[1])
        self.assertIn("SELECT 1;", calls[1])

    def test_grepai_start_adopts_already_running_watcher(self) -> None:
        originals = {
            "grepai_layout_from_args": provider_lifecycle.grepai_layout_from_args,
            "require_durable_process_namespace": provider_lifecycle.require_durable_process_namespace,
            "ensure_grepai_runtime_layout": provider_lifecycle.ensure_grepai_runtime_layout,
            "grepai_probe_watcher": provider_lifecycle.grepai_probe_watcher,
            "run_command": provider_lifecycle.run_command,
        }
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                memory = root / "memory"
                memory.mkdir()
                layout = provider_lifecycle.grepai_runtime_layout(
                    coordination_root=root / "coordination",
                    workspace_name="agents-remember-memory",
                    roots=(provider_lifecycle.GrepaiMemoryRoot(project_id="memory", path=memory),),
                )
                layout.binary_path.parent.mkdir(parents=True, exist_ok=True)
                layout.binary_path.write_text("binary\n", encoding="utf-8")
                provider_lifecycle.grepai_layout_from_args = lambda args: (root / "settings.json", {}, layout)
                provider_lifecycle.require_durable_process_namespace = lambda action: None
                provider_lifecycle.ensure_grepai_runtime_layout = lambda runtime_layout: runtime_layout.state_file.parent.mkdir(parents=True, exist_ok=True)
                provider_lifecycle.grepai_probe_watcher = lambda command_name, runtime_layout, state_file, timeout: {
                    "running": True,
                    "pid": 1234,
                    "managedAlive": True,
                    "nativeRunning": False,
                    "status": {"returncode": 0, "stdout": "Status: running\n", "stderr": ""},
                }
                provider_lifecycle.run_command = lambda command, **kwargs: self.fail("start command should not run")

                args = SimpleNamespace(dry_run=False, timeout=1)
                result = provider_lifecycle.grepai_run(args, "start")
        finally:
            provider_lifecycle.grepai_layout_from_args = originals["grepai_layout_from_args"]
            provider_lifecycle.require_durable_process_namespace = originals["require_durable_process_namespace"]
            provider_lifecycle.ensure_grepai_runtime_layout = originals["ensure_grepai_runtime_layout"]
            provider_lifecycle.grepai_probe_watcher = originals["grepai_probe_watcher"]
            provider_lifecycle.run_command = originals["run_command"]

        self.assertTrue(result["ok"])
        self.assertTrue(result["alreadyRunning"])
        self.assertEqual(result["pid"], 1234)

    def test_grepai_start_timeout_can_adopt_running_watcher(self) -> None:
        probes = [
            {"running": False, "pid": None, "managedAlive": False, "nativeRunning": False, "status": {"returncode": 1}},
            {"running": True, "pid": 4321, "managedAlive": False, "nativeRunning": True, "status": {"returncode": 0}},
        ]
        originals = {
            "grepai_layout_from_args": provider_lifecycle.grepai_layout_from_args,
            "require_durable_process_namespace": provider_lifecycle.require_durable_process_namespace,
            "ensure_grepai_runtime_layout": provider_lifecycle.ensure_grepai_runtime_layout,
            "grepai_probe_watcher": provider_lifecycle.grepai_probe_watcher,
            "run_command": provider_lifecycle.run_command,
        }
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                memory = root / "memory"
                memory.mkdir()
                layout = provider_lifecycle.grepai_runtime_layout(
                    coordination_root=root / "coordination",
                    workspace_name="agents-remember-memory",
                    roots=(provider_lifecycle.GrepaiMemoryRoot(project_id="memory", path=memory),),
                )
                layout.binary_path.parent.mkdir(parents=True, exist_ok=True)
                layout.binary_path.write_text("binary\n", encoding="utf-8")
                provider_lifecycle.grepai_layout_from_args = lambda args: (root / "settings.json", {}, layout)
                provider_lifecycle.require_durable_process_namespace = lambda action: None
                provider_lifecycle.ensure_grepai_runtime_layout = lambda runtime_layout: runtime_layout.state_file.parent.mkdir(parents=True, exist_ok=True)
                provider_lifecycle.grepai_probe_watcher = lambda command_name, runtime_layout, state_file, timeout: probes.pop(0)
                provider_lifecycle.run_command = lambda command, **kwargs: {
                    "command": command,
                    "returncode": None,
                    "stdout": "",
                    "stderr": "",
                    "timedOut": True,
                }

                args = SimpleNamespace(dry_run=False, timeout=1)
                result = provider_lifecycle.grepai_run(args, "start")
        finally:
            provider_lifecycle.grepai_layout_from_args = originals["grepai_layout_from_args"]
            provider_lifecycle.require_durable_process_namespace = originals["require_durable_process_namespace"]
            provider_lifecycle.ensure_grepai_runtime_layout = originals["ensure_grepai_runtime_layout"]
            provider_lifecycle.grepai_probe_watcher = originals["grepai_probe_watcher"]
            provider_lifecycle.run_command = originals["run_command"]

        self.assertTrue(result["ok"])
        self.assertTrue(result["startupTimedOut"])
        self.assertEqual(result["pid"], 4321)

    def test_grepai_start_fails_when_launcher_exits_but_watcher_is_not_running(self) -> None:
        probes = [
            {"running": False, "pid": None, "managedAlive": False, "nativeRunning": False, "status": {"returncode": 0}},
            {"running": False, "pid": None, "managedAlive": False, "nativeRunning": False, "status": {"returncode": 0}},
        ]
        originals = {
            "grepai_layout_from_args": provider_lifecycle.grepai_layout_from_args,
            "require_durable_process_namespace": provider_lifecycle.require_durable_process_namespace,
            "ensure_grepai_runtime_layout": provider_lifecycle.ensure_grepai_runtime_layout,
            "grepai_probe_watcher": provider_lifecycle.grepai_probe_watcher,
            "run_command": provider_lifecycle.run_command,
        }
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                memory = root / "memory"
                memory.mkdir()
                layout = provider_lifecycle.grepai_runtime_layout(
                    coordination_root=root / "coordination",
                    workspace_name="agents-remember-memory",
                    roots=(provider_lifecycle.GrepaiMemoryRoot(project_id="memory", path=memory),),
                )
                layout.binary_path.parent.mkdir(parents=True, exist_ok=True)
                layout.binary_path.write_text("binary\n", encoding="utf-8")
                provider_lifecycle.grepai_layout_from_args = lambda args: (root / "settings.json", {}, layout)
                provider_lifecycle.require_durable_process_namespace = lambda action: None
                provider_lifecycle.ensure_grepai_runtime_layout = lambda runtime_layout: runtime_layout.state_file.parent.mkdir(parents=True, exist_ok=True)
                provider_lifecycle.grepai_probe_watcher = lambda command_name, runtime_layout, state_file, timeout: probes.pop(0)
                provider_lifecycle.run_command = lambda command, **kwargs: {
                    "command": command,
                    "returncode": 0,
                    "stdout": "Workspace watcher agents-remember-memory started (PID 1234)\n",
                    "stderr": "",
                    "timedOut": False,
                }

                args = SimpleNamespace(dry_run=False, timeout=1)
                result = provider_lifecycle.grepai_run(args, "start")
        finally:
            provider_lifecycle.grepai_layout_from_args = originals["grepai_layout_from_args"]
            provider_lifecycle.require_durable_process_namespace = originals["require_durable_process_namespace"]
            provider_lifecycle.ensure_grepai_runtime_layout = originals["ensure_grepai_runtime_layout"]
            provider_lifecycle.grepai_probe_watcher = originals["grepai_probe_watcher"]
            provider_lifecycle.run_command = originals["run_command"]

        self.assertFalse(result["ok"])
        self.assertEqual(result["pid"], 1234)
        self.assertIn("recoveryAction", result)

    def test_watchers_run_reports_partial_results_and_recovery_actions(self) -> None:
        originals = {
            "context_provider_enabled": provider_lifecycle.context_provider_enabled,
            "grepai_run": provider_lifecycle.grepai_run,
            "cgc_start_all": provider_lifecycle.cgc_start_all,
            "process_namespace_status": provider_lifecycle.process_namespace_status,
        }

        def fake_enabled(coordination_root, from_settings, provider):
            return Path("/tmp/settings.json"), True

        provider_lifecycle.context_provider_enabled = fake_enabled
        provider_lifecycle.grepai_run = lambda args, action: {"provider": "grepai", "action": action, "ok": True}
        provider_lifecycle.cgc_start_all = lambda args: {
            "provider": "codegraphcontext",
            "action": "start-all",
            "ok": False,
            "recoveryAction": "retry codegraphcontext start-all",
        }
        provider_lifecycle.process_namespace_status = lambda: {"durableForDaemons": True, "warning": None}
        try:
            args = SimpleNamespace(coordination_root=Path("/tmp/coordination"), from_settings=None, dry_run=True, timeout=1)
            result = provider_lifecycle.watchers_run(args, "start")
        finally:
            provider_lifecycle.context_provider_enabled = originals["context_provider_enabled"]
            provider_lifecycle.grepai_run = originals["grepai_run"]
            provider_lifecycle.cgc_start_all = originals["cgc_start_all"]
            provider_lifecycle.process_namespace_status = originals["process_namespace_status"]

        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(
            result["recoveryActions"],
            [
                {
                    "provider": "codegraphcontext",
                    "action": "start-all",
                    "recoveryAction": "retry codegraphcontext start-all",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
