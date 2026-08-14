from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from agents_remember.providers import lifecycle
from agents_remember.providers.cgc.context.core import to_container_path
from agents_remember.providers.cgc.lifecycle import query as cgc_query
from agents_remember.providers.cgc.lifecycle.runner import cgc_runner_patch_script
from agents_remember.providers.grepai.lifecycle import backend as grepai_backend
from agents_remember.providers.lifecycle import process_status as lifecycle_process_status
from agents_remember.providers.lifecycle import watchers as watcher_lifecycle
from test_provider_lifecycle import ProviderLifecycleParserTests


class ProviderLifecycleParserTests2(ProviderLifecycleParserTests):
    def test_grepai_direct_run_does_not_special_case_native_watcher_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            runtime_root = coordination_root / "providers" / "grepai"
            settings_path = root / "lifecycle-settings.json"
            lifecycle.write_json(settings_path, {})
            args = self.parse_grepai(
                [
                    "run",
                    "--coordination-root",
                    str(coordination_root),
                    "--from-settings",
                    str(settings_path),
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
            service_config = self.service_config(root)
            repo = root / "workspace" / "repo-a"
            args = self.parse_cgc(
                [
                    "visualize",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                    "--repo-id",
                    "repo-a",
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
        command = result["command"]["command"]
        self.assertEqual(Path(command[0]).stem.lower(), "docker")
        self.assertIn("compose", command)
        self.assertEqual(command[-10:-5], ["run", "--rm", "-p", "127.0.0.1:8123:8123", "runner"])
        self.assertEqual(
            command[-5:-1],
            # --repo is the in-container mount path (drive stripped on Windows),
            # not the host path; matches as_posix() on POSIX hosts.
            ["visualize", "--repo", to_container_path(repo.resolve()), "--port"],
        )
        self.assertEqual(command[-1], "8123")
        self.assertEqual(result["command"]["overrideMode"], "stdin")

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
            "cgc_backend_status": cgc_query.cgc_backend_status,
            "run_compose": cgc_query.run_compose,
        }
        lifecycle_process_status.process_namespace_warning = lambda: (
            "sandbox init has --die-with-parent"
        )
        cgc_query.ensure_cgc_runtime_layout = lambda layout: None
        cgc_query.cgc_status = lambda args: {"ok": True}
        # cgc run now gates on backend readiness (FalkorDB), not the watcher.
        cgc_query.cgc_backend_status = lambda args: {"ok": True}
        cgc_query.run_compose = lambda render, command_args, **kwargs: {
            "stdout": "hit\n",
            "stderr": "",
            "returncode": 0,
            "durationSeconds": 0.01,
        }
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                service_config = self.service_config(root)
                args = self.parse_cgc(
                    [
                        "run",
                        "--coordination-root",
                        str(service_config.coordination_root),
                        "--from-settings",
                        str(service_config.settings_path),
                        "--repo-id",
                        "repo-a",
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
            cgc_query.cgc_backend_status = originals["cgc_backend_status"]
            cgc_query.run_compose = originals["run_compose"]

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

        def fake_enabled(from_settings, provider):
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

    def test_cgc_runner_image_build_no_cache_inserts_flag_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = self._cgc_build_args(Path(tmp_dir), no_cache=True)
            _, _, layouts = lifecycle.cgc_all_layouts_from_settings(args)

            result = lifecycle.cgc_runner_image_build(args, layouts[0])

        self.assertTrue(result["ok"])
        self.assertTrue(result["dryRun"])
        command = result["command"]["command"]
        self.assertIn("--no-cache", command)
        self.assertEqual(command[-1], "runner")

    def test_cgc_runner_image_build_without_no_cache_has_no_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = self._cgc_build_args(Path(tmp_dir), no_cache=False)
            _, _, layouts = lifecycle.cgc_all_layouts_from_settings(args)

            result = lifecycle.cgc_runner_image_build(args, layouts[0])

        self.assertTrue(result["ok"])
        self.assertNotIn("--no-cache", result["command"]["command"])

    def test_grepai_runner_image_build_no_cache_inserts_flag_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = self._grepai_build_args(Path(tmp_dir), no_cache=True)
            _, provider_settings, layout = lifecycle.grepai_layout_from_args(args)
            runner = lifecycle.grepai_runner_settings(provider_settings, layout)

            result = lifecycle.grepai_runner_image_build(args, runner=runner)

        self.assertTrue(result["ok"])
        self.assertTrue(result["dryRun"])
        command = result["command"]["command"]
        self.assertIn("--no-cache", command)
        self.assertEqual(command[-1], "watcher")

    def test_grepai_runner_image_build_without_no_cache_has_no_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = self._grepai_build_args(Path(tmp_dir), no_cache=False)
            _, provider_settings, layout = lifecycle.grepai_layout_from_args(args)
            runner = lifecycle.grepai_runner_settings(provider_settings, layout)

            result = lifecycle.grepai_runner_image_build(args, runner=runner)

        self.assertTrue(result["ok"])
        self.assertNotIn("--no-cache", result["command"]["command"])
