from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers import lifecycle
from agents_remember.providers.cgc.lifecycle.core import cgc_layout_from_args
from agents_remember.providers.cgc.lifecycle.installation import (
    cgc_doctor,
    cgc_install,
    cgc_install_commands,
    cgc_install_preflight,
    cgc_runtime_root_containment_check,
)


def _provider_instance(provider_id: str, coordination_root: Path) -> dict[str, object]:
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


def _write_cgc_settings(root: Path) -> tuple[Path, Path]:
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
                        "instance": _provider_instance("codegraphcontext-code", coordination_root),
                        "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                        "requirementsFile": (
                            "<coordination_root>/providers/requirements/codegraphcontext.txt"
                        ),
                        "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                        "roots": [{"repoId": "repo-a", "path": repo.as_posix()}],
                        "backend": {
                            "image": "falkordb/falkordb:v4.18.7",
                            "runtimeRoot": (
                                "<coordination_root>/providers/data/codegraphcontext/falkordb"
                            ),
                            "dataRoot": "<backendRuntimeRoot>/data",
                        },
                    },
                },
            }
        },
    )
    return coordination_root, settings_path


def _parse_cgc(argv: list[str]):
    parser = lifecycle.build_parser()
    args = parser.parse_args(["cgc", *argv])
    lifecycle.normalize_cgc_args(args)
    args.coordination_root = args.coordination_root.resolve()
    if args.code_repo_root is not None:
        args.code_repo_root = args.code_repo_root.resolve()
    if args.repo_id is not None:
        args.repo_id = lifecycle.stable_provider_id(args.repo_id)
    return args


def _scoped_install_args(root: Path, *, extra: list[str] | None = None):
    coordination_root, settings_path = _write_cgc_settings(root)
    argv = [
        "install",
        "--coordination-root",
        str(coordination_root),
        "--from-settings",
        str(settings_path),
        "--repo-id",
        "repo-a",
        "--dry-run",
    ]
    argv.extend(extra or [])
    return _parse_cgc(argv)


class CgcInstallDryRunTests(unittest.TestCase):
    def test_scoped_install_dry_run_returns_expected_shape_without_docker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = _scoped_install_args(Path(tmp_dir))

            with mock.patch(
                "agents_remember.providers.lifecycle.compose_runtime.docker_command",
                return_value="docker",
            ):
                result = cgc_install(args)

        self.assertEqual(result["provider"], "codegraphcontext")
        self.assertEqual(result["action"], "install")
        self.assertTrue(result["ok"])
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["repoId"], "repo-a")
        # Dry-run never reaches the doctor/backend/patch keys produced by the
        # real install path.
        self.assertNotIn("doctor", result)
        self.assertNotIn("backend", result)
        commands = result["commands"]
        self.assertIsInstance(commands, list)
        self.assertEqual(len(commands), 2)
        # The two planned compose invocations are an image build and a doctor run.
        self.assertEqual(commands[0]["command"][-2:], ["build", "runner"])
        self.assertEqual(
            commands[1]["command"][-5:],
            ["run", "--rm", "--no-deps", "runner", "doctor"],
        )

    def test_dry_run_does_not_create_runtime_layout_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = _scoped_install_args(Path(tmp_dir))
            layout = cgc_layout_from_args(args)

            cgc_install(args)

            # cgc_install_preflight short-circuits before ensure_cgc_runtime_layout
            # in dry-run, so the runtime + state file must not be materialized.
            self.assertFalse(layout.runtime_root.exists())
            self.assertFalse(layout.state_file.exists())

    def test_install_all_dry_run_aggregates_per_repo_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root, settings_path = _write_cgc_settings(root)
            # No --repo-id routes cgc_install to cgc_install_all.
            args = _parse_cgc(
                [
                    "install",
                    "--coordination-root",
                    str(coordination_root),
                    "--from-settings",
                    str(settings_path),
                    "--dry-run",
                ]
            )

            with mock.patch(
                "agents_remember.providers.lifecycle.compose_runtime.docker_command",
                return_value="docker",
            ):
                result = cgc_install(args)

        self.assertEqual(result["action"], "install-all")
        self.assertTrue(result["ok"])
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["backend"]["ok"])
        self.assertEqual([r["action"] for r in result["results"]], ["install"])
        self.assertEqual(result["results"][0]["repoId"], "repo-a")


class CgcInstallPreflightTests(unittest.TestCase):
    def test_preflight_dry_run_returns_dry_run_result_and_no_executed_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = _scoped_install_args(Path(tmp_dir))
            layout = cgc_layout_from_args(args)
            _, commands = cgc_install_commands(args, layout)

            results, early_result, backend_result = cgc_install_preflight(args, layout, commands)

        self.assertEqual(results, [])
        self.assertIsNone(backend_result)
        assert early_result is not None
        self.assertTrue(early_result["dryRun"])
        self.assertTrue(early_result["ok"])
        self.assertEqual(early_result["action"], "install")
        self.assertEqual(early_result["commands"], commands)


class CgcDoctorTests(unittest.TestCase):
    def test_doctor_dry_run_assembles_named_checks_without_docker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root, settings_path = _write_cgc_settings(root)
            args = _parse_cgc(
                [
                    "doctor",
                    "--coordination-root",
                    str(coordination_root),
                    "--from-settings",
                    str(settings_path),
                    "--repo-id",
                    "repo-a",
                    "--dry-run",
                ]
            )

            result = cgc_doctor(args)

        self.assertEqual(result["provider"], "codegraphcontext")
        self.assertEqual(result["action"], "doctor")
        self.assertTrue(result["dryRun"])
        check_names = [check["name"] for check in result["checks"]]
        self.assertEqual(
            check_names,
            [
                "runtime-root-contained",
                "source-artifact-clean",
                "cgc-runner-image",
                "cgc-image-patches",
            ],
        )
        # The runner image was never built, so that check fails and pulls the
        # overall doctor verdict down.
        runner_check = next(c for c in result["checks"] if c["name"] == "cgc-runner-image")
        self.assertFalse(runner_check["ok"])
        self.assertFalse(result["ok"])
        # In dry-run the doctor command is a plan (with a compose summary), not a
        # captured run.
        assert result["command"] is not None
        self.assertIn("compose", result["command"])
        self.assertEqual(
            result["command"]["command"][-5:],
            ["run", "--rm", "--no-deps", "runner", "doctor"],
        )

    def test_doctor_runtime_containment_check_passes_for_coordination_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = _scoped_install_args(Path(tmp_dir))
            layout = cgc_layout_from_args(args)

            check = cgc_runtime_root_containment_check(layout)

        self.assertEqual(check["name"], "runtime-root-contained")
        self.assertTrue(check["ok"])
        self.assertTrue(check["details"]["outsideSourceRepo"])


if __name__ == "__main__":
    unittest.main()
