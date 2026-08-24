"""Pure contract tests for the one direct Python diagnostic command."""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from agents_remember.testing.direct_runner import (
    DiagnosticExecutionError,
    DirectDiagnosticCompleted,
    DirectDiagnosticRefused,
    help_text,
    main,
    run_direct_diagnostic,
)
from agents_remember.testing.pytest_diagnostic_reporter import (
    DIAGNOSTIC_REPORT_ENV,
    DIAGNOSTIC_REPORT_SCHEMA,
)


class DirectTestRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        package = self.root / "mcp" / "src" / "agents_remember"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        tests = self.root / "mcp" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_plain.py").write_text(
            "def test_second():\n    assert 3 * 3 == 9\n\n"
            "def test_first():\n    assert 2 + 2 == 4\n",
            encoding="utf-8",
        )
        (tests / "test_unsafe.py").write_text(
            "import subprocess\n\ndef test_unsafe():\n    subprocess.run(['true'], check=False)\n",
            encoding="utf-8",
        )
        (self.root / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["mcp/tests"]\n'
            'addopts = ["-n=auto", "--strict-markers", "--strict-config"]\n',
            encoding="utf-8",
        )
        self.calls: list[tuple[list[str], Path, Mapping[str, str]]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def executor(
        self,
        command: list[str],
        cwd: Path,
        environ: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, cwd, environ))
        nodes = command[command.index("--basetemp") + 2 :]
        report = {
            "schemaVersion": DIAGNOSTIC_REPORT_SCHEMA,
            "pytestExitCode": 0,
            "nodes": [{"nodeId": node, "outcome": "passed"} for node in nodes],
        }
        Path(environ[DIAGNOSTIC_REPORT_ENV]).write_text(
            json.dumps(report),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="one diagnostic line\n",
            stderr="",
        )

    def test_success_runs_exact_nodes_serially_under_canonical_config(self) -> None:
        targets = (
            "mcp/tests/test_plain.py::test_second",
            "mcp/tests/test_plain.py::test_first",
        )
        result = run_direct_diagnostic(
            self.root,
            targets,
            environ={
                "AR_DAGGER_TEST_ATTESTATION": "secret-not-for-diagnostics",
                "GIT_DIR": "/real/repository/.git",
            },
            executor=self.executor,
        )

        self.assertIsInstance(result, DirectDiagnosticCompleted)
        assert isinstance(result, DirectDiagnosticCompleted)
        self.assertEqual(tuple(node.node_id for node in result.outcomes), targets)
        self.assertEqual(result.payload()["certifying"], False)
        self.assertEqual(result.payload()["pytestStdout"], "one diagnostic line\n")
        self.assertEqual(result.payload()["pytestStderr"], "")
        command, cwd, environment = self.calls[0]
        self.assertEqual(cwd, self.root)
        self.assertIn((self.root / "pyproject.toml").as_posix(), command)
        self.assertIn("--noconftest", command)
        self.assertIn("agents_remember.testing.pytest_bootstrap", command)
        self.assertIn("agents_remember.testing.pytest_diagnostic_reporter", command)
        self.assertIn("-n=0", command)
        self.assertEqual(tuple(command[-2:]), targets)
        self.assertNotIn("AR_DAGGER_TEST_ATTESTATION", environment)
        self.assertNotIn("GIT_DIR", environment)

    def test_every_refusal_executes_zero_nodes_and_never_falls_back(self) -> None:
        unsafe = "mcp/tests/test_unsafe.py::test_unsafe"
        safe = "mcp/tests/test_plain.py::test_first"
        requests = (
            (),
            ("-n=4", safe),
            ("--maxfail=1", safe),
            (safe, unsafe),
            ("mcp/tests/test_plain.py",),
            tuple(safe for _ in range(9)),
        )
        for request in requests:
            with self.subTest(request=request):
                result = run_direct_diagnostic(
                    self.root,
                    request,
                    executor=lambda *_args: self.fail("a refused request executed pytest"),
                )
                self.assertIsInstance(result, DirectDiagnosticRefused)
                assert isinstance(result, DirectDiagnosticRefused)
                self.assertFalse(result.payload()["executed"])
                self.assertEqual(result.payload()["executedNodeCount"], 0)
        self.assertEqual(self.calls, [])

    def test_missing_or_contradictory_child_report_is_an_infrastructure_error(self) -> None:
        target = ("mcp/tests/test_plain.py::test_first",)
        with self.assertRaises(DiagnosticExecutionError):
            run_direct_diagnostic(
                self.root,
                target,
                executor=lambda command, _cwd, _env: subprocess.CompletedProcess(command, 0),
            )

    def test_candidate_change_during_execution_discards_the_result(self) -> None:
        target = ("mcp/tests/test_plain.py::test_first",)

        def mutate_after_execution(
            command: list[str],
            cwd: Path,
            environ: Mapping[str, str],
        ) -> subprocess.CompletedProcess[str]:
            completed = self.executor(command, cwd, environ)
            (self.root / "mcp/tests/test_plain.py").write_text(
                "def test_first():\n    assert False\n",
                encoding="utf-8",
            )
            return completed

        with self.assertRaisesRegex(DiagnosticExecutionError, "candidate changed"):
            run_direct_diagnostic(
                self.root,
                target,
                executor=mutate_after_execution,
            )

    def test_repository_wrapper_is_executable_and_pins_the_direct_route(self) -> None:
        wrapper = Path(__file__).parents[2] / "scripts" / "test-python"
        source = wrapper.read_text(encoding="utf-8")

        self.assertTrue(wrapper.stat().st_mode & stat.S_IXUSR)
        self.assertIn("uv --no-config run", source)
        self.assertIn('--project "$repository_root/mcp/pyproject.toml"', source)
        self.assertIn("--python 3.12", source)
        self.assertEqual(source.count("agents_remember.testing.direct_runner"), 1)
        self.assertNotIn("dagger", source.lower())

    def test_help_and_cli_output_are_unmistakably_non_certifying(self) -> None:
        self.assertIn("NON-CERTIFYING", help_text())
        self.assertIn("at most eight", help_text())
        output: list[str] = []
        exit_code = main(
            ["mcp/tests/test_plain.py::test_first"],
            candidate_root=self.root,
            executor=self.executor,
            printer=output.append,
        )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output[-1])
        self.assertEqual(payload["altitude"], "diagnostic")
        self.assertFalse(payload["certifying"])
        self.assertIn("NON-CERTIFYING", payload["message"])


if __name__ == "__main__":
    unittest.main()
