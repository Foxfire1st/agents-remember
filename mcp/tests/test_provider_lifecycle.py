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

from agents_remember.providers import lifecycle, lifecycle_service
from agents_remember.providers.cgc.lifecycle import query as cgc_query
from agents_remember.providers.cgc.lifecycle.runner import cgc_runner_patch_script
from agents_remember.providers.grepai.lifecycle import actions as grepai_actions
from agents_remember.providers.grepai.lifecycle import backend as grepai_backend
from agents_remember.providers.grepai.lifecycle import core as grepai_core
from agents_remember.providers.lifecycle import process_status as lifecycle_process_status
from agents_remember.providers.lifecycle import watchers as watcher_lifecycle


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
            rendered = lifecycle.render_captured_command_output(data)

        self.assertTrue(rendered)
        self.assertEqual(stdout.getvalue(), "native stdout\n")
        self.assertEqual(stderr.getvalue(), "native stderr\n")
        self.assertNotIn("command:", stdout.getvalue())

    def test_captured_command_output_ignores_non_command_results(self) -> None:
        self.assertFalse(
            lifecycle.render_captured_command_output({"ok": False, "error": "missing"})
        )

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
            rendered = lifecycle.render_cgc_run_result(data, args)

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
            rendered = lifecycle.render_cgc_run_result(data, args)

        self.assertTrue(rendered)
        self.assertIn('"outputLines"', stdout.getvalue())
        self.assertIn("╭─ Table", stdout.getvalue())
        self.assertIn('"returncode": 0', stdout.getvalue())
        self.assertNotIn('"command"', stdout.getvalue())
        self.assertNotIn("\\u256d", stdout.getvalue())


class ProviderLifecycleParserTests(unittest.TestCase):
    def parse_cgc(self, argv: list[str]):
        parser = lifecycle.build_parser()
        args = parser.parse_args(["cgc", *argv])
        lifecycle.normalize_cgc_args(args)
        args.coordination_root = args.coordination_root.resolve()
        if args.code_repo_root is not None:
            args.code_repo_root = args.code_repo_root.resolve()
        if args.repo_id is not None:
            args.repo_id = lifecycle.stable_provider_id(args.repo_id)
        return args

    def parse_grepai(self, argv: list[str]):
        parser = lifecycle.build_parser()
        args = parser.parse_args(["grepai", *argv])
        lifecycle.normalize_grepai_args(args)
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
        lifecycle.write_json(
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

        self.assertEqual(args.coordination_root, lifecycle.default_coordination_root().resolve())

    def test_watchers_defaults_coordination_root_to_installed_runtime_root(self) -> None:
        parser = lifecycle.build_parser()
        args = parser.parse_args(["watchers", "status"])

        self.assertEqual(args.coordination_root, lifecycle.default_coordination_root())

    def test_grepai_direct_run_requires_settings_backed_docker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            runtime_root = coordination_root / "providers" / "grepai"
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
                ]
            )

            result = lifecycle.grepai_run(args, "run")

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "run")
        self.assertEqual(result["mode"], "unsupported")
        self.assertIn("Docker-only", result["message"])
        self.assertNotIn("command", result)

    def test_grepai_settings_backed_run_uses_docker_without_host_binary(self) -> None:
        original = grepai_actions.docker_command
        grepai_actions.docker_command = lambda: "docker"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service_config = self.service_config(Path(tmp_dir))

                result = lifecycle_service.run_grepai_lifecycle(
                    service_config,
                    action="run",
                    native_args=[
                        "search",
                        "provider lifecycle",
                        "--workspace",
                        "agents-remember-memory",
                        "--project",
                        "memory-a",
                    ],
                )
        finally:
            grepai_actions.docker_command = original

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "docker")
        self.assertEqual(
            result["command"][:4],
            ["docker", "exec", "ar-grepai-watcher", "grepai"],
        )
        self.assertEqual(result["command"][4:7], ["search", "provider lifecycle", "--workspace"])
        self.assertNotIn("_bin", " ".join(result["command"]))

    def test_grepai_start_dry_run_builds_complete_docker_stack(self) -> None:
        originals = {
            "grepai_release_arch": grepai_core.grepai_release_arch,
        }
        grepai_core.grepai_release_arch = lambda: "amd64"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service_config = self.service_config(Path(tmp_dir))
                args = self.parse_grepai(
                    [
                        "start",
                        "--coordination-root",
                        str(service_config.coordination_root),
                        "--from-settings",
                        str(service_config.settings_path),
                        "--dry-run",
                    ]
                )

                result = lifecycle.grepai_run(args, "start")
        finally:
            grepai_core.grepai_release_arch = originals["grepai_release_arch"]

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "docker")
        self.assertEqual(result["backend"]["network"]["name"], "ar-grepai-memory")
        self.assertIn("pgvector/pgvector:pg16", result["backend"]["commands"][2])
        self.assertIn("ollama/ollama:latest", result["embedder"]["commands"][2])
        self.assertEqual(result["watcher"]["containerName"], "ar-grepai-watcher")
        self.assertEqual(result["watcher"]["image"]["image"], "agents-remember/grepai:0.35.0")
        self.assertEqual(
            result["workspaceState"]["dsn"],
            "postgres://grepai:grepai@ar-grepai-postgres:5432/grepai?sslmode=disable",
        )
        self.assertEqual(
            result["workspaceState"]["projectPaths"],
            {"memory-a": "/grepai/runtime/index-roots/memory-a"},
        )
        self.assertEqual(
            result["workspaceState"]["embedder"]["endpoint"],
            "http://ar-grepai-ollama:11434",
        )

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

    def test_grepai_direct_run_does_not_special_case_native_watcher_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            runtime_root = coordination_root / "providers" / "grepai"
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

            result = lifecycle.grepai_run(args, "run")

        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "unsupported")
        self.assertIn("Docker-only", result["message"])

    def test_ephemeral_namespace_rejects_daemon_actions(self) -> None:
        original = lifecycle_process_status.process_namespace_warning
        lifecycle_process_status.process_namespace_warning = lambda: (
            "sandbox init has --die-with-parent"
        )
        try:
            with self.assertRaisesRegex(
                lifecycle.ContextProviderError,
                "must run outside this ephemeral process namespace",
            ):
                lifecycle.require_durable_process_namespace("watchers start")
        finally:
            lifecycle_process_status.process_namespace_warning = original

    def test_process_namespace_status_reports_warning(self) -> None:
        original = lifecycle_process_status.process_namespace_warning
        lifecycle_process_status.process_namespace_warning = lambda: (
            "sandbox init has --die-with-parent"
        )
        try:
            self.assertEqual(
                lifecycle.process_namespace_status(),
                {
                    "durableForDaemons": False,
                    "warning": "sandbox init has --die-with-parent",
                },
            )
        finally:
            lifecycle_process_status.process_namespace_warning = original

    def test_visualize_rejects_ephemeral_process_namespace(self) -> None:
        original = lifecycle_process_status.process_namespace_warning
        lifecycle_process_status.process_namespace_warning = lambda: (
            "sandbox init has --die-with-parent"
        )
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
                    lifecycle.ContextProviderError,
                    "must run outside this ephemeral process namespace",
                ):
                    lifecycle.cgc_visualize(args)
        finally:
            lifecycle_process_status.process_namespace_warning = original

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

            result = lifecycle.cgc_visualize(args)

        self.assertTrue(result["ok"])
        self.assertTrue(result["longRunning"])
        self.assertEqual(result["action"], "visualize")
        self.assertEqual(result["url"], "http://127.0.0.1:8123")
        self.assertEqual(Path(result["command"][0]).name, "docker")
        self.assertEqual(result["command"][1:4], ["run", "--rm", "--network"])
        self.assertIn("ar-cgc-code", result["command"])
        self.assertIn("agents-remember/codegraphcontext:0.4.10", result["command"])
        self.assertEqual(
            result["command"][-5:-1],
            ["visualize", "--repo", repo.resolve().as_posix(), "--port"],
        )
        self.assertEqual(result["command"][-1], "8123")

    def test_cgc_runner_patch_script_embeds_replacements_as_python_data(self) -> None:
        script = cgc_runner_patch_script()

        compile(script, "patch_cgc.py", "exec")
        self.assertIn("operations = [", script)
        self.assertNotIn("json.loads({", script)

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
                lifecycle.ContextProviderError,
                "use cgc visualize",
            ):
                lifecycle.cgc_run(args)

    def test_run_allows_bounded_query_in_ephemeral_process_namespace(self) -> None:
        originals = {
            "process_namespace_warning": lifecycle_process_status.process_namespace_warning,
            "ensure_cgc_runtime_layout": cgc_query.ensure_cgc_runtime_layout,
            "cgc_status": cgc_query.cgc_status,
            "run_command": cgc_query.run_command,
        }
        lifecycle_process_status.process_namespace_warning = lambda: (
            "sandbox init has --die-with-parent"
        )
        cgc_query.ensure_cgc_runtime_layout = lambda layout: None
        cgc_query.cgc_status = lambda args: {"ok": True}
        cgc_query.run_command = lambda command, **kwargs: {
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

                result = lifecycle.cgc_run(args)

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "run")
            self.assertEqual(result["command"]["stdout"], "hit\n")
        finally:
            lifecycle_process_status.process_namespace_warning = originals[
                "process_namespace_warning"
            ]
            cgc_query.ensure_cgc_runtime_layout = originals["ensure_cgc_runtime_layout"]
            cgc_query.cgc_status = originals["cgc_status"]
            cgc_query.run_command = originals["run_command"]

    def test_docker_wait_for_postgres_requires_database_query(self) -> None:
        backend = {
            "containerName": "grepai-postgres",
            "postgresPassword": "grepai",
            "postgresUser": "grepai",
            "postgresDatabase": "grepai",
        }
        calls: list[list[str]] = []
        originals = {
            "docker_command": grepai_backend.docker_command,
            "run_command": grepai_backend.run_command,
        }

        def fake_run_command(command, **kwargs):
            calls.append(command)
            return {"returncode": 0, "stdout": "ok\n", "stderr": ""}

        grepai_backend.docker_command = lambda: "docker"
        grepai_backend.run_command = fake_run_command
        try:
            result = lifecycle.docker_wait_for_postgres(backend, cwd=Path("/tmp"), timeout=1)
        finally:
            grepai_backend.docker_command = originals["docker_command"]
            grepai_backend.run_command = originals["run_command"]

        self.assertEqual(result["returncode"], 0)
        self.assertIn("pg_isready", calls[0])
        self.assertIn("psql", calls[1])
        self.assertIn("SELECT 1;", calls[1])

    def test_watchers_run_reports_partial_results_and_recovery_actions(self) -> None:
        originals = {
            "context_provider_enabled": watcher_lifecycle.context_provider_enabled,
            "grepai_run": watcher_lifecycle.grepai_run,
            "cgc_start_all": watcher_lifecycle.cgc_start_all,
            "process_namespace_status": watcher_lifecycle.process_namespace_status,
        }

        def fake_enabled(coordination_root, from_settings, provider):
            return Path("/tmp/settings.json"), True

        watcher_lifecycle.context_provider_enabled = fake_enabled
        watcher_lifecycle.grepai_run = lambda args, action: {
            "provider": "grepai",
            "action": action,
            "ok": True,
        }
        watcher_lifecycle.cgc_start_all = lambda args: {
            "provider": "codegraphcontext",
            "action": "start-all",
            "ok": False,
            "recoveryAction": "retry codegraphcontext start-all",
        }
        watcher_lifecycle.process_namespace_status = lambda: {
            "durableForDaemons": True,
            "warning": None,
        }
        try:
            args = SimpleNamespace(
                coordination_root=Path("/tmp/coordination"),
                from_settings=None,
                dry_run=True,
                timeout=1,
            )
            result = lifecycle.watchers_run(args, "start")
        finally:
            watcher_lifecycle.context_provider_enabled = originals["context_provider_enabled"]
            watcher_lifecycle.grepai_run = originals["grepai_run"]
            watcher_lifecycle.cgc_start_all = originals["cgc_start_all"]
            watcher_lifecycle.process_namespace_status = originals["process_namespace_status"]

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
