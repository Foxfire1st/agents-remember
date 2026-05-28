from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers import lifecycle, lifecycle_service
from agents_remember.providers.cgc.lifecycle import query as cgc_query
from agents_remember.providers.cgc.lifecycle import refresh as cgc_refresh_lifecycle
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

    def test_compose_auto_ports_render_with_empty_published_port(self) -> None:
        self.assertEqual(lifecycle.yaml_port_mapping("127.0.0.1", "auto", 11434), '"127.0.0.1::11434"')
        self.assertEqual(lifecycle.yaml_port_mapping("127.0.0.1", 5432, 5432), '"127.0.0.1:5432:5432"')


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

    def provider_instance(self, provider_id: str, coordination_root: Path) -> dict[str, object]:
        return {
            "id": "test",
            "scope": "workspace",
            "labels": {
                "agents-remember.provider": provider_id,
                "agents-remember.instance-id": "test",
                "agents-remember.scope": "workspace",
                "agents-remember.coordination-root": coordination_root.as_posix(),
            },
        }

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
                            "instance": self.provider_instance(
                                "codegraphcontext-code",
                                coordination_root,
                            ),
                            "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                            "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                            "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                            "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                            "roots": [{"repoId": "repo-a", "path": repo.as_posix()}],
                            "backend": {
                                "image": "falkordb/falkordb:v4.18.7",
                                "runtimeRoot": "<coordination_root>/providers/data/codegraphcontext/falkordb",
                                "dataRoot": "<backendRuntimeRoot>/data",
                            },
                        },
                        "grepai-memory": {
                            "enabled": True,
                            "instance": self.provider_instance(
                                "grepai-memory",
                                coordination_root,
                            ),
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

    def multi_repo_service_config(
        self, root: Path
    ) -> lifecycle_service.ProviderLifecycleServiceConfig:
        service_config = self.service_config(root)
        repo = root / "workspace" / "repo-b"
        repo.mkdir(parents=True)
        settings = lifecycle.read_json(service_config.settings_path)
        settings["contextProviders"]["providers"]["codegraphcontext-code"]["roots"].append(
            {"repoId": "repo-b", "path": repo.as_posix()}
        )
        lifecycle.write_json(service_config.settings_path, settings)
        return service_config

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
        command = result["command"]["command"]
        self.assertEqual(Path(command[0]).name, "docker")
        self.assertIn("compose", command)
        self.assertEqual(command[-10:-7], ["exec", "-T", "watcher"])
        self.assertEqual(command[-7], "grepai")
        self.assertEqual(command[-6:-3], ["search", "provider lifecycle", "--workspace"])
        self.assertNotIn("_bin", " ".join(command))
        self.assertEqual(result["command"]["overrideMode"], "stdin")

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
        self.assertEqual(result["backend"]["commands"][0]["command"][-3:], ["up", "-d", "postgres"])
        self.assertEqual(result["embedder"]["commands"][0]["command"][-3:], ["up", "-d", "ollama"])
        self.assertEqual(result["backend"]["compose"]["overrideMode"], "stdin")
        self.assertEqual(result["embedder"]["compose"]["overrideMode"], "stdin")
        self.assertEqual(
            result["backend"]["migration"]["network"]["networkName"], "ar-grepai-memory"
        )
        self.assertEqual(
            result["backend"]["migration"]["containers"]["watcher"]["containerName"],
            "ar-grepai-watcher",
        )
        self.assertEqual(result["watcher"]["containerName"], "ar-grepai-watcher")
        self.assertEqual(result["watcher"]["image"]["image"], "agents-remember/grepai:0.35.0")
        self.assertEqual(result["watcher"]["commands"][0]["command"][-3:], ["up", "-d", "watcher"])
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
        self.assertEqual(result["command"]["command"][-3:], ["find", "name", "Token"])
        self.assertEqual(result["command"]["overrideMode"], "stdin")

    def test_grepai_compose_override_renders_dynamic_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.service_config(Path(tmp_dir))
            args = self.parse_grepai(
                [
                    "status",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                    "--dry-run",
                ]
            )
            _, provider_settings, layout = lifecycle.grepai_layout_from_args(args)
            runner = lifecycle.grepai_runner_settings(provider_settings, layout)
            backend = lifecycle.grepai_backend_settings(provider_settings, layout)

            render = lifecycle.grepai_compose_render(provider_settings, layout, runner, backend)

        self.assertEqual(render.project_name, "agents-remember-grepai")
        self.assertNotIn("@", render.override_yaml)
        self.assertIn('image: "pgvector/pgvector:pg16"', render.override_yaml)
        self.assertIn('container_name: "ar-grepai-postgres"', render.override_yaml)
        self.assertIn('image: "ollama/ollama:latest"', render.override_yaml)
        self.assertIn('container_name: "ar-grepai-watcher"', render.override_yaml)
        self.assertIn('HOME: "/grepai/runtime/home"', render.override_yaml)
        self.assertIn('XDG_CACHE_HOME: "/grepai/runtime/cache/xdg"', render.override_yaml)
        self.assertIn('XDG_STATE_HOME: "/grepai/runtime/state/xdg"', render.override_yaml)
        self.assertNotIn(f'HOME: "{layout.home_root.as_posix()}"', render.override_yaml)
        self.assertIn('agents-remember.provider: "grepai-memory"', render.override_yaml)
        self.assertNotIn("legacy-provider-settings", render.override_yaml)
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            self.assertIn(
                f'user: "{os.getuid()}:{os.getgid()}"',
                render.override_yaml,
            )
        self.assertIn('"127.0.0.1::5432"', render.override_yaml)
        self.assertIn('"127.0.0.1::11434"', render.override_yaml)
        self.assertNotIn(":auto:", render.override_yaml)
        self.assertIn('name: "ar-grepai-memory"', render.override_yaml)
        self.assertEqual(len(render.override_sha256), 64)

    def test_grepai_compose_rejects_missing_instance_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.service_config(Path(tmp_dir))
            args = self.parse_grepai(
                [
                    "status",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                    "--dry-run",
                ]
            )
            _, provider_settings, layout = lifecycle.grepai_layout_from_args(args)
            provider_settings.pop("instance")
            runner = lifecycle.grepai_runner_settings(provider_settings, layout)
            backend = lifecycle.grepai_backend_settings(provider_settings, layout)

            with self.assertRaisesRegex(
                lifecycle.ContextProviderError,
                "grepai-memory settings must include instance.labels",
            ):
                lifecycle.grepai_compose_render(provider_settings, layout, runner, backend)

    def test_cgc_compose_override_renders_repo_watchers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.service_config(Path(tmp_dir))
            args = self.parse_cgc(
                [
                    "status",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                    "--repo-id",
                    "repo-a",
                    "--dry-run",
                ]
            )
            _, provider_settings, layouts = lifecycle.cgc_all_layouts_from_settings(args)

            render = lifecycle.cgc_compose_render(provider_settings, layouts)

        self.assertEqual(render.project_name, "agents-remember-cgc")
        self.assertNotIn("@", render.override_yaml)
        self.assertIn('image: "falkordb/falkordb:v4.18.7"', render.override_yaml)
        self.assertIn("watcher-repo-a:", render.override_yaml)
        self.assertIn('container_name: "ar-cgc-watcher-repo-a"', render.override_yaml)
        self.assertIn('FALKORDB_HOST: "ar-cgc-falkordb"', render.override_yaml)
        self.assertIn('"127.0.0.1::6379"', render.override_yaml)
        self.assertIn('"127.0.0.1::3000"', render.override_yaml)
        self.assertIn('agents-remember.provider: "codegraphcontext-code"', render.override_yaml)
        self.assertNotIn("legacy-provider-settings", render.override_yaml)
        self.assertNotIn(":auto:", render.override_yaml)
        self.assertIn(':ro"', render.override_yaml)
        self.assertEqual(len(render.override_sha256), 64)

    def test_cgc_compose_rejects_missing_instance_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.service_config(Path(tmp_dir))
            args = self.parse_cgc(
                [
                    "status",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                    "--repo-id",
                    "repo-a",
                    "--dry-run",
                ]
            )
            _, provider_settings, layouts = lifecycle.cgc_all_layouts_from_settings(args)
            provider_settings.pop("instance")

            with self.assertRaisesRegex(
                lifecycle.ContextProviderError,
                "codegraphcontext-code settings must include instance.labels",
            ):
                lifecycle.cgc_compose_render(provider_settings, layouts)

    def test_cgc_start_all_dry_run_reports_project_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.service_config(Path(tmp_dir))
            args = self.parse_cgc(
                [
                    "start",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                    "--dry-run",
                ]
            )

            result = lifecycle.cgc_start(args)

        self.assertTrue(result["ok"])
        migration = result["backend"]["migration"]
        self.assertEqual(migration["network"]["networkName"], "ar-cgc-code")
        self.assertEqual(
            migration["containers"]["watchers"]["repo-a"]["containerName"],
            "ar-cgc-watcher-repo-a",
        )

    def test_cgc_start_all_dry_run_uses_one_bulk_compose_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.multi_repo_service_config(Path(tmp_dir))
            args = self.parse_cgc(
                [
                    "start",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                    "--dry-run",
                ]
            )

            result = lifecycle.cgc_start(args)

        self.assertTrue(result["ok"])
        self.assertTrue(result["parallel"])
        command = result["command"]["command"]
        self.assertIn("up", command)
        self.assertIn("-d", command)
        self.assertLess(command.index("watcher-repo-a"), command.index("watcher-repo-b"))
        self.assertEqual(command.count("watcher-repo-a"), 1)
        self.assertEqual(command.count("watcher-repo-b"), 1)

    def test_cgc_refresh_all_indexes_repos_in_parallel_after_starting_watchers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service_config = self.multi_repo_service_config(Path(tmp_dir))
            args = self.parse_cgc(
                [
                    "refresh-all",
                    "--coordination-root",
                    str(service_config.coordination_root),
                    "--from-settings",
                    str(service_config.settings_path),
                ]
            )
            originals = {
                "cgc_start_all": cgc_refresh_lifecycle.cgc_start_all,
                "run_compose": cgc_refresh_lifecycle.run_compose,
            }
            barrier = threading.Barrier(2, timeout=2)
            lock = threading.Lock()
            active = 0
            max_active = 0
            index_calls: list[list[str]] = []
            runner_renders: dict[str, str] = {}

            def fake_run_compose(render, command_args, **kwargs):
                nonlocal active, max_active
                index_calls.append(command_args)
                runner_renders[command_args[5]] = render.override_yaml
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                barrier.wait()
                with lock:
                    active -= 1
                return {
                    "stdout": "",
                    "stderr": "",
                    "returncode": 0,
                    "durationSeconds": 0.1,
                    "timedOut": False,
                }

            cgc_refresh_lifecycle.cgc_start_all = lambda scoped_args: {
                "provider": "codegraphcontext",
                "action": "start-all",
                "ok": True,
                "backend": {"ok": True},
            }
            cgc_refresh_lifecycle.run_compose = fake_run_compose
            try:
                result = lifecycle.cgc_refresh_all(args)
            finally:
                cgc_refresh_lifecycle.cgc_start_all = originals["cgc_start_all"]
                cgc_refresh_lifecycle.run_compose = originals["run_compose"]

        self.assertTrue(result["ok"])
        self.assertTrue(result["parallel"])
        self.assertEqual(result["watchers"]["action"], "start-all")
        self.assertEqual(len(index_calls), 2)
        self.assertEqual(max_active, 2)
        self.assertTrue(
            all(call[:5] == ["run", "--rm", "--no-deps", "runner", "index"] for call in index_calls)
        )
        for repo_path, override_yaml in runner_renders.items():
            self.assertIn(f'{repo_path}:{repo_path}:ro"', override_yaml)
            self.assertIn("watcher-repo-a:", override_yaml)
            self.assertIn("watcher-repo-b:", override_yaml)

    def test_cgc_runtime_containment_allows_workflow_local_provider_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            code_repo = root / "workspace" / "repo-a"
            code_repo.mkdir(parents=True)
            layout = lifecycle.cgc_runtime_layout(
                coordination_root=coordination_root,
                repo_id="repo-a",
                code_repo_root=code_repo,
                runtime_root=(
                    coordination_root
                    / "worktrees"
                    / "repo-a"
                    / "task-ar"
                    / "provider-runtime"
                    / "providers"
                    / "runners"
                    / "codegraphcontext"
                    / "worktree-abc123"
                    / "repo-a"
                ),
            )

            result = lifecycle.cgc_runtime_root_containment_check(layout)

        self.assertTrue(result["ok"])
        self.assertFalse(result["details"]["underProviderRoot"])
        self.assertTrue(result["details"]["underCoordinationRoot"])
        self.assertTrue(result["details"]["outsideSourceRepo"])

    def test_cgc_runtime_containment_rejects_source_repo_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            code_repo = root / "workspace" / "repo-a"
            code_repo.mkdir(parents=True)
            layout = lifecycle.cgc_runtime_layout(
                coordination_root=coordination_root,
                repo_id="repo-a",
                code_repo_root=code_repo,
                runtime_root=code_repo / ".codegraphcontext",
            )

            result = lifecycle.cgc_runtime_root_containment_check(layout)

        self.assertFalse(result["ok"])
        self.assertFalse(result["details"]["outsideSourceRepo"])

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
        self.assertEqual(Path(command[0]).name, "docker")
        self.assertIn("compose", command)
        self.assertEqual(command[-10:-5], ["run", "--rm", "-p", "127.0.0.1:8123:8123", "runner"])
        self.assertEqual(
            command[-5:-1],
            ["visualize", "--repo", repo.resolve().as_posix(), "--port"],
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
            "run_compose": cgc_query.run_compose,
        }
        lifecycle_process_status.process_namespace_warning = lambda: (
            "sandbox init has --die-with-parent"
        )
        cgc_query.ensure_cgc_runtime_layout = lambda layout: None
        cgc_query.cgc_status = lambda args: {"ok": True}
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
