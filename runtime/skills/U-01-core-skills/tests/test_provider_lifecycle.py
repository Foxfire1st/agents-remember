from __future__ import annotations

import contextlib
import io
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


RUNTIME_ROOT = Path(__file__).resolve().parents[3]
PROVIDER_LIFECYCLE_PATH = RUNTIME_ROOT / "scripts" / "provider-lifecycle.py"
SPEC = importlib.util.spec_from_file_location("provider_lifecycle", PROVIDER_LIFECYCLE_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to load provider lifecycle module from {PROVIDER_LIFECYCLE_PATH}")
provider_lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider_lifecycle)


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


if __name__ == "__main__":
    unittest.main()
