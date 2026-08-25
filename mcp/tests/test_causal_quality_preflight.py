"""Dagger-admitted wrapper contracts for causal-preflight continuation."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from _quality_admission import QUALITY_TEST_ADMISSION
from agents_remember.code_quality import check
from agents_remember.testing.causal_failures import CAUSAL_REPORT_SCHEMA


class CausalQualityPreflightTests(unittest.TestCase):
    def test_owned_causal_failure_runs_independent_pytest_population(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            causal_report = root / "causal-failures.json"
            commands: list[list[str]] = []

            def runner(
                name: str,
                command: list[str],
                cwd: Path,
                env: Mapping[str, str],
            ) -> check.StepResult:
                del cwd, env
                commands.append(command)
                if name != "causal-preflight":
                    return check.StepResult(name, 0, command)
                causal_report.write_text(
                    json.dumps(
                        {
                            "schemaVersion": CAUSAL_REPORT_SCHEMA,
                            "status": "failed",
                            "preflights": [{"causeId": "schema:fixture:v1"}],
                            "blockedGroups": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return check.StepResult(name, 1, command)

            exit_code = check.run_quality_check(
                _quality_config(root, causal_report),
                runner=runner,
                printer=lambda _message: None,
            )

        modules = _command_modules(commands)
        self.assertEqual(exit_code, 1)
        self.assertIn("pytest", modules)
        pytest_command = commands[modules.index("pytest")]
        self.assertEqual(
            pytest_command[pytest_command.index("--ar-causal-failure-report") + 1],
            causal_report.as_posix(),
        )

    def test_broken_preflight_tool_still_blocks_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands: list[list[str]] = []

            def runner(
                name: str,
                command: list[str],
                cwd: Path,
                env: Mapping[str, str],
            ) -> check.StepResult:
                del cwd, env
                commands.append(command)
                return check.StepResult(name, int(name == "causal-preflight"), command)

            exit_code = check.run_quality_check(
                _quality_config(root, root / "missing-causal-report.json"),
                runner=runner,
                printer=lambda _message: None,
            )

        self.assertEqual(exit_code, 1)
        self.assertNotIn("pytest", _command_modules(commands))


def _quality_config(root: Path, causal_report: Path) -> check.CheckConfig:
    source = root / "sample.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    return check.CheckConfig(
        project_root=root,
        scope=check.GateScope(
            lint_paths=[source],
            type_paths=[source],
            coverage_paths=[source],
            test_paths=[tests],
            size_paths=[source],
        ),
        admission=QUALITY_TEST_ADMISSION,
        coverage_json=root / "coverage.json",
        causal_failure_report=causal_report,
        threshold=30.0,
        top=5,
    )


def _command_modules(commands: list[list[str]]) -> list[str]:
    return [command[2] for command in commands]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
