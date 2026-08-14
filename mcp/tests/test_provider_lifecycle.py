from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers import (
    lifecycle,
    lifecycle_service,
)
from agents_remember.providers.lifecycle import command_runner


class ProviderCommandRunnerTests(unittest.TestCase):
    def test_success_uses_devnull_or_explicit_stdin_and_unlimited_timeout(self) -> None:
        completed = command_runner.subprocess.CompletedProcess(
            ["tool"], 0, stdout="out", stderr="err"
        )
        with (
            mock.patch.object(command_runner, "subprocess_env", return_value={"SAFE": "1"}),
            mock.patch.object(command_runner.subprocess, "run", return_value=completed) as run,
        ):
            result = command_runner.run_command(["tool"], cwd=Path("/tmp"), timeout=0)
            command_runner.run_command(["tool"], cwd=Path("/tmp"), stdin_text="request", timeout=2)

        self.assertEqual((result["returncode"], result["timedOut"]), (0, False))
        self.assertIs(run.call_args_list[0].kwargs["stdin"], command_runner.subprocess.DEVNULL)
        self.assertIsNone(run.call_args_list[0].kwargs["timeout"])
        self.assertEqual(run.call_args_list[1].kwargs["input"], "request")
        self.assertEqual(run.call_args_list[1].kwargs["timeout"], 2)

    def test_timeout_is_raised_or_returned_only_when_allowed(self) -> None:
        error = command_runner.subprocess.TimeoutExpired(
            ["tool"], 3, output=b"partial", stderr="problem"
        )
        with mock.patch.object(command_runner.subprocess, "run", side_effect=error):
            with self.assertRaises(command_runner.subprocess.TimeoutExpired):
                command_runner.run_command(["tool"], cwd=Path("/tmp"), timeout=3)
            result = command_runner.run_command(
                ["tool"], cwd=Path("/tmp"), timeout=3, allow_timeout=True
            )

        self.assertTrue(result["timedOut"])
        self.assertEqual(result["stdout"], "partial")
        self.assertEqual(result["stderr"], "problem")


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
        self.assertEqual(
            lifecycle.yaml_port_mapping("127.0.0.1", "auto", 11434), '"127.0.0.1::11434"'
        )
        self.assertEqual(
            lifecycle.yaml_port_mapping("127.0.0.1", 5432, 5432), '"127.0.0.1:5432:5432"'
        )


class ProviderComposeMemoryCapTests(unittest.TestCase):
    """L12: every provider service ships an explicit memory cap (watchers 512m by
    developer directive), so a runaway container OOM-recycles itself under its
    unless-stopped restart policy instead of exhausting host RAM and swap."""

    def test_cgc_compose_services_are_memory_capped(self) -> None:
        base = lifecycle.provider_asset_text("compose", "codegraphcontext.compose.yaml")
        self.assertIn("mem_limit: 2g", base)  # falkordb
        self.assertIn("mem_limit: 1g", base)  # batch runner
        watcher = lifecycle.provider_asset_text("compose", "codegraphcontext.watcher.yaml.tmpl")
        self.assertIn("mem_limit: 512m", watcher)
        self.assertIn("restart: unless-stopped", watcher)

    def test_grepai_compose_services_are_memory_capped(self) -> None:
        base = lifecycle.provider_asset_text("compose", "grepai.compose.yaml")
        self.assertEqual(base.count("mem_limit: 512m"), 2)  # postgres + watcher
        self.assertIn("mem_limit: 2g", base)  # ollama
        self.assertEqual(base.count("restart: unless-stopped"), 3)


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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_lifecycle.py:132).
    def parse_grepai(self, argv: list[str]):  # pragma: no cover
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

    def _cgc_build_args(self, root: Path, *, no_cache: bool | None):
        service_config = self.service_config(root)
        argv = [
            "install-all",
            "--coordination-root",
            str(service_config.coordination_root),
            "--from-settings",
            str(service_config.settings_path),
            "--dry-run",
        ]
        if no_cache:
            argv.append("--no-cache")
        return self.parse_cgc(argv)

    def _grepai_build_args(self, root: Path, *, no_cache: bool | None):
        service_config = self.service_config(root)
        argv = [
            "install",
            "--coordination-root",
            str(service_config.coordination_root),
            "--from-settings",
            str(service_config.settings_path),
            "--dry-run",
        ]
        if no_cache:
            argv.append("--no-cache")
        return self.parse_grepai(argv)


class LifecycleSettingsPathTests(unittest.TestCase):
    """260703-L13 (GQ3): the implicit coordinator system/settings.json fallback is gone.

    Every lifecycle settings reader requires the explicit ``--from-settings``
    path; a missing one refuses loudly instead of silently reading (or
    empty-defaulting on) coordinator state the authority discipline already
    rejected as a provider settings source.
    """

    def test_cgc_settings_reader_refuses_without_explicit_path(self) -> None:
        with self.assertRaisesRegex(
            lifecycle.ContextProviderError,
            "explicit --from-settings path.*not an authority source",
        ):
            lifecycle.cgc_settings_from_file(None)

    def test_grepai_settings_reader_refuses_without_explicit_path(self) -> None:
        with self.assertRaisesRegex(
            lifecycle.ContextProviderError, "explicit --from-settings path"
        ):
            lifecycle.grepai_settings_from_file(None)

    def test_enabled_probe_refuses_without_explicit_path(self) -> None:
        with self.assertRaisesRegex(
            lifecycle.ContextProviderError, "explicit --from-settings path"
        ):
            lifecycle.context_provider_enabled(None, "grepai-memory")

    def test_explicit_settings_path_keeps_working(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "lifecycle-settings.json"
            lifecycle.write_json(
                settings_path,
                {
                    "contextProviders": {
                        "enabled": True,
                        "providers": {"grepai-memory": {"enabled": True}},
                    }
                },
            )
            path, enabled = lifecycle.context_provider_enabled(settings_path, "grepai-memory")
        self.assertEqual(path, settings_path)
        self.assertTrue(enabled)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
