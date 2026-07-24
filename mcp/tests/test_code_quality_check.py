from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality import check


class CodeQualityCheckTests(unittest.TestCase):
    def test_quality_check_runs_fixed_suite_and_crap_calculator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            commands: list[list[str]] = []
            output: list[str] = []

            exit_code = check.run_quality_check(
                check.CheckConfig(
                    project_root=root,
                    source_paths=[source],
                    test_paths=[root / "tests"],
                    coverage_json=root / "coverage.json",
                    threshold=30.0,
                    top=5,
                ),
                runner=fake_runner(commands, root / "coverage.json"),
                printer=output.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                command_modules(commands),
                ["ruff", "pyright", "radon", "radon", "pytest"],
            )
            pyright_command = commands[1]
            self.assertIn("--pythonpath", pyright_command)
            self.assertIn(sys.executable, pyright_command)
            self.assertIn(source.as_posix(), pyright_command)
            self.assertIn((root / "tests").as_posix(), pyright_command)
            self.assertTrue(any("CRAP-Calculator" in line for line in output))

    def test_quality_check_fails_when_a_fixed_step_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            coverage_json = root / "coverage.json"

            exit_code = check.run_quality_check(
                check.CheckConfig(
                    project_root=root,
                    source_paths=[source],
                    test_paths=[root / "tests"],
                    coverage_json=coverage_json,
                    threshold=30.0,
                    top=5,
                ),
                runner=fake_runner([], coverage_json, failing_step="ruff"),
                printer=lambda message: None,
            )

            self.assertEqual(exit_code, 1)

    def test_quality_check_fails_when_coverage_json_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)

            exit_code = check.run_quality_check(
                check.CheckConfig(
                    project_root=root,
                    source_paths=[source],
                    test_paths=[root / "tests"],
                    coverage_json=root / "missing-coverage.json",
                    threshold=30.0,
                    top=5,
                ),
                runner=lambda name, command, cwd, env: check.StepResult(name, 0, command),
                printer=lambda message: None,
            )

            self.assertEqual(exit_code, 1)

    def test_crap_threshold_fails_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            coverage_json = root / "coverage.json"

            exit_code = check.run_quality_check(
                check.CheckConfig(
                    project_root=root,
                    source_paths=[source],
                    test_paths=[root / "tests"],
                    coverage_json=coverage_json,
                    threshold=1.0,
                    top=5,
                ),
                runner=fake_runner([], coverage_json),
                printer=lambda message: None,
            )

            self.assertEqual(exit_code, 1)

    def test_cli_has_no_report_only_or_strict_opt_in_mode(self) -> None:
        parser = check.build_parser()

        help_text = parser.format_help()

        self.assertNotIn("report-only", help_text)
        self.assertNotIn("fail-on-crap-threshold", help_text)
        self.assertIn("mandatory CRAP threshold enforcement", help_text)

    def test_repository_gates_use_default_strict_wrapper(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        gate_files = [
            repository_root / ".githooks" / "pre-commit",
            repository_root / ".githooks" / "pre-push",
            repository_root / ".github" / "workflows" / "quality-checks.yml",
        ]

        for gate_file in gate_files:
            content = gate_file.read_text(encoding="utf-8")
            with self.subTest(gate_file=gate_file):
                self.assertIn("agents_remember.code_quality.check", content)
                self.assertNotIn("fail-on-crap-threshold", content)

    def test_run_fixed_checks_threads_checkout_source_onto_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            seen_env: list[Mapping[str, str]] = []

            def runner(
                name: str, command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> check.StepResult:
                seen_env.append(env)
                return check.StepResult(name, 0, command)

            with mock.patch.dict(os.environ, {"PYTHONPATH": "/pre-existing"}):
                check.run_fixed_checks(
                    check.CheckConfig(
                        project_root=root,
                        source_paths=[source],
                        test_paths=[root / "tests"],
                        coverage_json=root / "coverage.json",
                        threshold=30.0,
                        top=5,
                    ),
                    root / "coverage.json",
                    runner=runner,
                    printer=lambda message: None,
                )

            self.assertTrue(seen_env)
            entries = seen_env[0]["PYTHONPATH"].split(os.pathsep)
            # The checkout's own source import root comes first; a pre-existing
            # PYTHONPATH is preserved at the end.
            self.assertEqual(entries[0], str(source.resolve().parent))
            self.assertEqual(entries[-1], "/pre-existing")


def write_sample_source(root: Path) -> Path:
    source = root / "sample.py"
    source.write_text(
        "\n".join(
            [
                "def simple(value):",
                "    if value:",
                "        return value + 1",
                "    return 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return source


def fake_runner(
    commands: list[list[str]],
    coverage_json: Path,
    *,
    failing_step: str | None = None,
) -> check.CommandRunner:
    def run(name: str, command: list[str], cwd: Path, env: Mapping[str, str]) -> check.StepResult:
        commands.append(command)
        if name == "pytest":
            coverage_json.write_text(
                json.dumps(
                    {
                        "files": {
                            "sample.py": {
                                "executed_lines": [1, 2, 3, 4],
                                "missing_lines": [],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
        return check.StepResult(name, 1 if name == failing_step else 0, command)

    return run


def command_modules(commands: list[list[str]]) -> list[str]:
    return [command[2] for command in commands]


if __name__ == "__main__":
    unittest.main()
