"""The File Size Budget rail: bands, exit codes, wrapper wiring, and scope."""

from __future__ import annotations

import runpy
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality import check, file_size
from agents_remember.code_quality import scope as quality_scope
from test_code_quality_check import REPOSITORY_ROOT, sample_config


class FileSizeBandsTests(unittest.TestCase):
    def test_bands_follow_the_written_standard(self) -> None:
        self.assertEqual(file_size.band_for(1199), "under-limit")
        self.assertEqual(file_size.band_for(1200), "hard-limit-exceeded")
        self.assertEqual(file_size.band_for(1999), "hard-limit-exceeded")
        self.assertEqual(file_size.band_for(2000), "architectural-failure")
        self.assertEqual(file_size.band_for(3999), "architectural-failure")
        self.assertEqual(file_size.band_for(4000), "emergency-cleanup")

    def test_measure_counts_newlines_like_wc_and_flags_only_hard_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            over = root / "over.py"
            under = root / "under.py"
            over.write_text("x = 1\n" * 1200, encoding="utf-8")
            under.write_text("x = 1\n" * 1199, encoding="utf-8")

            findings = file_size.measure([over, under])

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].path, over)
            self.assertEqual(findings[0].line_count, 1200)
            self.assertEqual(findings[0].band, "hard-limit-exceeded")
            self.assertEqual(file_size.line_count(under), 1199)

    def test_render_names_the_band(self) -> None:
        finding = file_size.FileSizeFinding(
            path=Path("mcp/tests/test_x.py"),
            line_count=2004,
            band="architectural-failure",
        )
        line = file_size.render(finding)
        self.assertIn("2004", line)
        self.assertIn("architectural-failure", line)
        self.assertIn("mcp/tests/test_x.py", line)


class FileSizeCliTests(unittest.TestCase):
    def test_cli_fails_non_zero_on_a_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "over.py"
            source.write_text("x = 1\n" * 1200, encoding="utf-8")

            completed = run_detector([str(source)])

            self.assertEqual(completed.returncode, 1, completed.stdout)
            self.assertIn("hard-limit-exceeded", completed.stdout)
            self.assertIn("file-size FAIL", completed.stdout)

    def test_report_mode_prints_the_same_findings_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "over.py"
            source.write_text("x = 1\n" * 1200, encoding="utf-8")

            completed = run_detector([str(source), "--report"])

            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("hard-limit-exceeded", completed.stdout)
            self.assertIn("REPORT COMPLETE", completed.stdout)

    def test_cli_refuses_an_empty_measurement(self) -> None:
        completed = run_detector([])
        self.assertEqual(completed.returncode, 1)
        self.assertIn("refusing to certify an empty measurement", completed.stdout)

    def test_main_in_process_enforces_and_reports_the_same_branches(self) -> None:
        """In-process ``main`` coverage: the wrapper measures what pytest imports.

        The subprocess CLI runs are unmeasured by Coverage.py, so the same paths are
        exercised in-process here -- enforced exit, report exit, an empty measurement,
        and an unreadable file -- keeping the detector's own complexity gate honest.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            over = root / "over.py"
            under = root / "under.py"
            missing = root / "missing.py"
            over.write_text("x = 1\n" * 1200, encoding="utf-8")
            under.write_text("x = 1\n" * 100, encoding="utf-8")

            self.assertEqual(file_size.main([str(over)]), 1)
            self.assertEqual(file_size.main([str(over), "--report"]), 0)
            self.assertEqual(file_size.main([str(under)]), 0)
            self.assertEqual(file_size.main([]), 1)
            self.assertEqual(file_size.main([str(missing)]), 1)

            # The ``__main__`` guard is a changed line too; execute the module as main
            # in-process so Coverage.py measures it (the CLI subprocess is unmeasured).
            with (
                mock.patch.object(sys, "argv", ["file_size", str(over), "--report"]),
                redirect_stdout(StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                runpy.run_path(
                    str(MCP_SRC / "agents_remember" / "code_quality" / "file_size.py"),
                    run_name="__main__",
                )
            self.assertEqual(raised.exception.code, 0)


class FileSizeWrapperWiringTests(unittest.TestCase):
    def test_file_size_is_an_enforcing_step_wired_into_the_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_repository_and_source(root)

            steps = {
                step.name: step
                for step in check.quality_steps(sample_config(root, source), root / "coverage.json")
            }

            step = steps["file-size"]
            self.assertTrue(step.enforcing)
            self.assertIn("agents_remember.code_quality.file_size", step.command)
            self.assertIn(source.as_posix(), step.command)

    def test_unarmed_step_reports_and_armed_step_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_repository_and_source(root)
            base_config = sample_config(root, source)

            unarmed = check.quality_steps(base_config, root / "coverage.json")
            armed = check.quality_steps(
                check.CheckConfig(
                    project_root=root,
                    scope=base_config.scope,
                    coverage_json=base_config.coverage_json,
                    threshold=base_config.threshold,
                    top=base_config.top,
                    file_size_armed=True,
                ),
                root / "coverage.json",
            )

            unarmed_command = next(step.command for step in unarmed if step.name == "file-size")
            armed_command = next(step.command for step in armed if step.name == "file-size")
            self.assertIn("--report", unarmed_command)
            self.assertNotIn("--report", armed_command)

    def test_arming_flag_reads_pyproject_and_rejects_non_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                '[tool.agents_remember]\nfile_size_armed = "yes"\n',
                encoding="utf-8",
            )
            with self.assertRaises(quality_scope.ScopeError) as raised:
                quality_scope.file_size_armed(root)
            self.assertIn("must be a boolean", str(raised.exception))

            (root / "pyproject.toml").write_text(
                "[tool.agents_remember]\nfile_size_armed = true\n",
                encoding="utf-8",
            )
            self.assertTrue(quality_scope.file_size_armed(root))
            (root / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            self.assertFalse(quality_scope.file_size_armed(root))

    def test_repository_declares_the_arming_key_armed_at_full_scope(self) -> None:
        with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
        section = data["tool"]["agents_remember"]
        # S4 at full scope (post-L16 sync, manager signal 01KZAMC4CWQXF2ZVTW2N9PC7AG):
        # the rail is armed so any over-limit file fails the wrapper. The flag must stay a
        # boolean so the wrapper never misreads it.
        self.assertIs(section["file_size_armed"], True)


class FileSizeScopeTests(unittest.TestCase):
    def test_scope_covers_python_and_dashboard_src_typescript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_sample_repository_and_source(root)
            dashboard = root / "dashboard" / "src"
            dashboard.mkdir(parents=True)
            (dashboard / "panel.tsx").write_text("export const x = 1\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                (
                    "[tool.pytest.ini_options]\n"
                    'testpaths = ["tests"]\n'
                    "[tool.agents_remember]\n"
                    "file_size_armed = false\n"
                ),
                encoding="utf-8",
            )
            run_git(root, "add", "-A")

            scope = check.derive_scope(root)

            self.assertIn(Path("dashboard/src/panel.tsx"), scope.size_paths)
            self.assertIn(Path("scripts/sync.py"), scope.size_paths)
            self.assertIn(Path("pkg/__init__.py"), scope.size_paths)

    def test_scope_size_paths_default_to_python_without_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_repository_and_source(root)

            scope = check.derive_scope(root)

            self.assertEqual(scope.size_paths, scope.lint_paths)
            self.assertIn(source.relative_to(root), scope.size_paths)


def run_detector(extra: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agents_remember.code_quality.file_size",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env={"PYTHONPATH": str(MCP_SRC), "PATH": "/usr/bin:/bin"},
    )


def write_sample_repository_and_source(root: Path) -> Path:
    """A real git repository with a package, tests, a script, and a sample module."""
    run_git(root, "init", "--quiet", "--initial-branch=main")
    (root / "pyproject.toml").write_text(
        (
            "[tool.pytest.ini_options]\n"
            'testpaths = ["tests"]\n'
            "[tool.agents_remember]\n"
            "file_size_armed = false\n"
        ),
        encoding="utf-8",
    )
    for directory in ("pkg", "tests", "scripts"):
        (root / directory).mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_pkg.py").write_text(
        "def test_nothing() -> None: ...\n", encoding="utf-8"
    )
    (root / "scripts" / "sync.py").write_text("value = 1\n", encoding="utf-8")
    source = root / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    run_git(root, "add", "-A")
    return source


def run_git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
