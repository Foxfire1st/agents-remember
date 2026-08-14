from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from unittest import mock

from agents_remember.providers import lifecycle, lifecycle_service
from agents_remember.providers.cgc.context.core import CgcInstance, CgcRepo
from agents_remember.providers.cgc.lifecycle import installation
from agents_remember.providers.cgc.lifecycle import refresh as cgc_refresh_lifecycle
from agents_remember.providers.grepai.lifecycle import core as grepai_core
from agents_remember.providers.lifecycle import compose_runtime
from test_provider_lifecycle import ProviderLifecycleParserTests


class ProviderLifecycleParserTests1(ProviderLifecycleParserTests):
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
            # --from-settings is explicit and empty: the manual-override path
            # carries no docker runtime block (the L13 removal of the implicit
            # coordinator system/settings.json fallback makes the flag required).
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
        # command[0] is resolved by compose_runtime.docker_command (via
        # compose_plan/run_compose), so patch that symbol rather than the
        # re-exported grepai_actions.docker_command, which is never consulted.
        original = compose_runtime.docker_command
        compose_runtime.docker_command = lambda: "docker"
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
            compose_runtime.docker_command = original

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "docker")
        command = result["command"]["command"]
        self.assertEqual(Path(command[0]).stem.lower(), "docker")
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

                with mock.patch.object(compose_runtime, "docker_command", return_value="docker"):
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
        self.assertEqual(result["backend"]["ports"]["postgres"]["hostPort"], 61432)
        self.assertEqual(result["backend"]["ports"]["postgres"]["containerPort"], 5432)
        self.assertEqual(result["embedder"]["ports"]["http"]["hostPort"], 61434)
        self.assertEqual(result["embedder"]["ports"]["http"]["containerPort"], 11434)
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
            {"memory-a": "/grepai/roots/memory-a"},
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
                lifecycle_service.CgcLifecycleRequest(
                    action="run",
                    repo_id="repo-a",
                    native_args=("find", "name", "Token"),
                ),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "codegraphcontext")
        self.assertEqual(result["action"], "run")
        self.assertEqual(result["command"]["command"][-3:], ["find", "name", "Token"])
        self.assertEqual(result["command"]["overrideMode"], "stdin")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_lifecycle_parser_1.py:221).
    def test_grepai_compose_override_renders_dynamic_settings(self) -> None:  # pragma: no cover
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
        # the live memory root is bind-mounted read-write into the watcher
        for root in layout.roots:
            self.assertIn(
                f'"{root.path.as_posix()}:/grepai/roots/{root.project_id}"',
                render.override_yaml,
            )
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
        getuid = getattr(os, "getuid", None)
        getgid = getattr(os, "getgid", None)
        if callable(getuid) and callable(getgid):
            self.assertIn(
                f'user: "{getuid()}:{getgid()}"',
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
        # FalkorDB v4 writes to /var/lib/falkordb/data; binding /data leaves
        # graphs in the ephemeral container layer and loses them on recreate.
        self.assertIn(':/var/lib/falkordb/data"', render.override_yaml)
        self.assertNotIn(':/data"', render.override_yaml)
        self.assertIn("cgc-watch-guard.py", render.override_yaml)
        self.assertEqual(len(render.override_sha256), 64)

    def test_cgc_compose_honors_configured_data_destination(self) -> None:
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
            provider_settings["backend"]["dataDestination"] = "/custom/falkordb/data"

            render = lifecycle.cgc_compose_render(provider_settings, layouts)

        self.assertIn(':/custom/falkordb/data"', render.override_yaml)

    def test_cgc_indexing_state_probe_classifies_graph_content(self) -> None:
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
            _, _, layouts = lifecycle.cgc_all_layouts_from_settings(args)
            layout = layouts[0]

            cases = [
                (
                    {
                        "returncode": 0,
                        "stdout": "count(f)\n10472\nCached execution: 1",
                        "stderr": "",
                    },
                    "indexed",
                ),
                (
                    {"returncode": 0, "stdout": "count(f)\n0\nCached execution: 0", "stderr": ""},
                    "empty",
                ),
                (
                    {
                        "returncode": 0,
                        "stdout": "(error) ERR Invalid graph operation on empty key",
                        "stderr": "",
                    },
                    "empty",
                ),
                (
                    {
                        "returncode": 0,
                        "stdout": "(error) LOADING Redis is loading the dataset in memory",
                        "stderr": "",
                    },
                    "backend-unreachable",
                ),
                (
                    {"returncode": 1, "stdout": "", "stderr": "no such container"},
                    "backend-unreachable",
                ),
            ]
            for result, expected in cases:
                with (
                    mock.patch.object(installation, "run_command", return_value=result),
                    mock.patch.object(installation, "docker_command", return_value="docker"),
                ):
                    self.assertEqual(
                        installation.cgc_graph_content_state(layout),
                        expected,
                        msg=f"result={result}",
                    )

    def test_cgc_indexing_state_probe_reports_in_progress_scan(self) -> None:
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
            _, _, layouts = lifecycle.cgc_all_layouts_from_settings(args)
            layout = layouts[0]
            inspect_data = {
                "State": {
                    "Status": "running",
                    "Running": True,
                    "StartedAt": "2026-06-09T18:22:32.879967779Z",
                }
            }
            scan_logs = {
                "returncode": 0,
                "stdout": "Watching /repo for changes...\n⚠  Not indexed yet. Performing initial scan...\n",
                "stderr": "",
            }

            with (
                mock.patch.object(installation, "run_command", return_value=scan_logs),
                mock.patch.object(installation, "docker_command", return_value="docker"),
            ):
                state = installation.cgc_indexing_state_probe(
                    layout, inspect_data, watcher_running=True
                )
            self.assertEqual(state, "indexing")

            done_logs = {
                "returncode": 0,
                "stdout": (
                    "⚠  Not indexed yet. Performing initial scan...\n"
                    "✓ Initial scan complete\ncount(f)\n42\n"
                ),
                "stderr": "",
            }
            with (
                mock.patch.object(installation, "run_command", return_value=done_logs),
                mock.patch.object(installation, "docker_command", return_value="docker"),
            ):
                state = installation.cgc_indexing_state_probe(
                    layout, inspect_data, watcher_running=True
                )
            self.assertEqual(state, "indexed")

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

            with mock.patch.object(compose_runtime, "docker_command", return_value="docker"):
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

            with mock.patch.object(compose_runtime, "docker_command", return_value="docker"):
                result = lifecycle.cgc_start(args)

        self.assertTrue(result["ok"])
        self.assertTrue(result["parallel"])
        command = result["command"]["command"]
        self.assertIn("up", command)
        self.assertIn("-d", command)
        # The render carries every configured watcher service, so orphan
        # removal targets exactly the watchers of de-configured repos.
        self.assertIn("--remove-orphans", command)
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
            # The index argument and bind-mount target are the POSIX container
            # path (no Windows drive letter), mounted read-only as
            # host:container:ro. On POSIX hosts host == container.
            self.assertNotIn(":", repo_path)
            self.assertIn(f':{repo_path}:ro"', override_yaml)
            self.assertIn("watcher-repo-a:", override_yaml)
            self.assertIn("watcher-repo-b:", override_yaml)

    def test_cgc_runtime_containment_allows_workflow_local_provider_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            code_repo = root / "workspace" / "repo-a"
            code_repo.mkdir(parents=True)
            layout = lifecycle.cgc_runtime_layout(
                CgcRepo(
                    coordination_root=coordination_root,
                    repo_id="repo-a",
                    code_repo_root=code_repo,
                ),
                instance=CgcInstance(
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
                CgcRepo(
                    coordination_root=coordination_root,
                    repo_id="repo-a",
                    code_repo_root=code_repo,
                ),
                instance=CgcInstance(
                    runtime_root=code_repo / ".codegraphcontext",
                ),
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
