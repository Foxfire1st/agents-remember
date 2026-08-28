"""L6 closeout coverage tests: CRAP offenders and new CLI split helpers.

These tests cover the branch surface of ``scope_reporting.main`` (and its
extracted helpers) and ``scope.eslint_result_files`` so the strict quality
gate's CRAP rail and changed-line floor can pass for this wave.
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember_test_support.code_quality import diff_coverage, scope, scope_reporting
from agents_remember_test_support.code_quality.scope import GateScope, ScopeError


def _valid_repository(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        "\n".join(
            (
                "[tool.ruff]",
                "line-length = 100",
                "[tool.pyright]",
                'include = ["."]',
                "[tool.radon]",
                'cc_min = "B"',
                "[tool.coverage.run]",
                "branch = true",
                "[tool.pytest.ini_options]",
                'testpaths = ["tests"]',
                "[tool.agents_remember]",
                'product_package_roots = ["pkg"]',
                "verification_package_roots = []",
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/__init__.py").write_text("", encoding="utf-8")
    (root / "pkg/module.py").write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_module.py").write_text(
        "def test_value() -> None:\n    assert True\n", encoding="utf-8"
    )
    (root / "dashboard/src").mkdir(parents=True)
    (root / "dashboard/src/app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (root / "dashboard/package.json").write_text(
        json.dumps({"scripts": {"lint": "eslint ."}}), encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts/sync.py").write_text("print('src/generated.ts')\n", encoding="utf-8")
    (root / "dashboard/eslint.config.js").write_text("module.exports = [];\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "--quiet", "--initial-branch=main"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=scope@agents-remember.invalid",
            "-c",
            "user.name=Scope Tests",
            "commit",
            "--quiet",
            "-m",
            "seed",
        ],
        check=True,
    )


def _eslint_stub(dashboard: Path) -> None:
    exe = dashboard / "node_modules" / ".bin"
    exe.mkdir(parents=True)
    (exe / "eslint").write_text(
        '#!/bin/sh\nprintf \'[{"filePath":"src/app.ts"}]\'\n', encoding="utf-8"
    )
    (exe / "eslint").chmod(0o755)


class TestScopeReportingMain:
    def test_generated_command(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        rc = scope_reporting.main(
            [
                "--project-root",
                str(tmp_path),
                "generated",
                "--name",
                "x",
                "--script",
                "scripts/sync.py",
            ]
        )
        assert rc == 0

    def test_dashboard_command(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        _eslint_stub(tmp_path / "dashboard")
        rc = scope_reporting.main(["--project-root", str(tmp_path), "dashboard", "--step", "lint"])
        assert rc == 0

    def test_randomized_command(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        rc = scope_reporting.main(
            ["--project-root", str(tmp_path), "randomized-pytest", "--seed", "abc"]
        )
        assert rc == 0

    def test_untracked_command(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        rc = scope_reporting.main(["--project-root", str(tmp_path), "untracked"])
        assert rc == 0

    def test_hook_tier_command(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        rc = scope_reporting.main(["--project-root", str(tmp_path), "hook-tier", "--tier", "fast"])
        assert rc == 0

    def test_fixed_step_command(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        rc = scope_reporting.main(["--project-root", str(tmp_path), "fixed-step", "--name", "ruff"])
        assert rc == 0

    def test_error_path_returns_one(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("not a real pyproject\n", encoding="utf-8")
        rc = scope_reporting.main(["--project-root", str(tmp_path), "hook-tier", "--tier", "fast"])
        assert rc == 1


class TestEslintResultFiles:
    def test_missing_executable(self, tmp_path: Path) -> None:
        with pytest.raises(ScopeError, match="ESLint executable is missing"):
            scope.eslint_result_files(tmp_path)

    @pytest.mark.parametrize(
        ("returncode", "stdout", "stderr", "match"),
        [
            (2, "", "boom", "could not resolve"),
            (0, "not json", "", "invalid JSON"),
            (0, "{}", "", "not a list"),
            (0, '[{"nofile": 1}]', "", "no string filePath"),
            (0, "[]", "", "resolved zero files"),
        ],
    )
    def test_failure_branches(
        self, tmp_path: Path, returncode: int, stdout: str, stderr: str, match: str
    ) -> None:
        dashboard = tmp_path / "dashboard"
        _eslint_stub(dashboard)
        completed = subprocess.CompletedProcess(
            ["eslint"], returncode, stdout=stdout, stderr=stderr
        )
        with mock.patch.object(scope.subprocess, "run", return_value=completed) as run:
            with pytest.raises(ScopeError, match=match):
                scope.eslint_result_files(dashboard)
            run.assert_called_once()

    def test_valid_result(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        _eslint_stub(dashboard)
        completed = subprocess.CompletedProcess(
            ["eslint"], 0, stdout='[{"filePath": "src/app.ts"}]', stderr=""
        )
        with mock.patch.object(scope.subprocess, "run", return_value=completed):
            paths = scope.eslint_result_files(dashboard)
        assert paths == [Path("src/app.ts")]


class TestScopeReportingCoverage:
    @pytest.mark.parametrize(
        "name", ["ruff", "ruff-format", "pyright", "radon-cc", "radon-mi", "pytest"]
    )
    def test_fixed_step_all_names(self, tmp_path: Path, name: str) -> None:
        _valid_repository(tmp_path)
        rc = scope_reporting.main(["--project-root", str(tmp_path), "fixed-step", "--name", name])
        assert rc == 0

    def test_untracked_scope_lines(self) -> None:
        gate = SimpleNamespace(
            scope_roots=[Path("mcp")],
            untracked_paths=[Path("a.py"), Path("b.py"), Path("c.py")],
        )
        lines = scope_reporting.untracked_scope_lines(cast(GateScope, gate), sample_limit=2)
        assert "3 non-ignored untracked files" in lines[0]
        assert "NOT in this measurement" in lines[1]
        assert "1 more untracked file(s)" in lines[-1]
        empty = SimpleNamespace(scope_roots=[], untracked_paths=[])
        lines = scope_reporting.untracked_scope_lines(cast(GateScope, empty))
        assert lines[1] == "untracked: 0 files are outside the index/diff measurement"

    def test_randomized_pytest_zero_tests(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        (tmp_path / "empty_tests").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["empty_tests"]\n', encoding="utf-8"
        )
        with pytest.raises(scope_reporting.ScopeReportingError, match="zero Python test files"):
            scope_reporting.randomized_pytest_scope_line(tmp_path, "seed1")

    def test_parse_push_updates_skips_blank_and_accepts_four_fields(self) -> None:
        raw = "\nrefs/heads/main abc123 refs/heads/main def456\n\n"
        updates = scope_reporting.parse_push_updates(raw)
        assert len(updates) == 1
        assert updates[0].local_ref == "refs/heads/main"
        assert updates[0].summary().startswith("refs/heads/main@abc123")

    def test_parse_push_updates_rejects_wrong_field_count(self) -> None:
        with pytest.raises(scope_reporting.ScopeReportingError, match="expected 4"):
            scope_reporting.parse_push_updates("a b c\n")

    def test_validate_invocation_environment_pre_push_with_updates(self) -> None:
        env = {
            "AR_QUALITY_INVOCATION": "pre-push",
            "AR_QUALITY_PUSH_UPDATES": "refs/heads/main aaaa refs/heads/main bbbb",
        }
        scope_reporting.validate_invocation_environment(env)

    def test_validate_invocation_environment_pre_push_zero_updates(self) -> None:
        env = {"AR_QUALITY_INVOCATION": "pre-push", "AR_QUALITY_PUSH_UPDATES": ""}
        with pytest.raises(scope_reporting.ScopeReportingError, match="zero ref updates"):
            scope_reporting.validate_invocation_environment(env)

    def test_invocation_descriptions(self) -> None:
        for key, expected in (
            ("closeout-staged", "closeout staged candidate"),
            ("ci", "CI checkout at HEAD"),
            ("pre-commit-staged", "pre-commit staged"),
            ("pre-commit-sequencer", "pre-commit merge"),
            ("pre-push", "pre-push ref updates"),
            ("manual", "manual dirty tree"),
        ):
            env = {"AR_QUALITY_INVOCATION": key}
            if key == "pre-push":
                env["AR_QUALITY_PUSH_UPDATES"] = "refs/heads/main aaaa refs/heads/main bbbb"
            assert expected in scope_reporting.invocation_description(env)

    def test_pyright_config_description_with_venv(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        (tmp_path / ".venv").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pyright]\nvenvPath = "."\nvenv = ".venv"\n', encoding="utf-8"
        )
        line = scope_reporting.pyright_config_description(tmp_path, "python3")
        assert "venv=" in line and ".venv" in line

    def test_pyright_config_description_no_venv(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        line = scope_reporting.pyright_config_description(tmp_path, "python3")
        assert "no venv declaration" in line

    def test_pyright_config_description_missing_pyproject(self, tmp_path: Path) -> None:
        line = scope_reporting.pyright_config_description(tmp_path, "python3")
        assert "MISSING pyproject.toml [tool.pyright]" in line

    def test_coverage_result_scope_line(self, tmp_path: Path) -> None:
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({"files": {"a.py": {}, "b.py": {}}}), encoding="utf-8")
        line = scope_reporting.coverage_result_scope_line(coverage_json)
        assert "2 Coverage.py file records" in line

    def test_randomized_pytest_scope_line(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        line = scope_reporting.randomized_pytest_scope_line(tmp_path, "seed1")
        assert "randomized-pytest" in line

    def test_randomized_pytest_scope_line_bad_testpaths(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\ntestpaths = []\n", encoding="utf-8"
        )
        with pytest.raises(scope_reporting.ScopeReportingError, match="testpaths"):
            scope_reporting.randomized_pytest_scope_line(tmp_path, "seed1")

    def test_crap_scope_line(self, tmp_path: Path) -> None:
        line = scope_reporting.crap_scope_line(
            tmp_path / "pyproject.toml", tmp_path / "cov.json", 5, 20.0
        )
        assert "CRAP-Calculator" in line

    def test_diff_input_descriptions(self) -> None:
        base = diff_coverage.BaseResolution("a714114", "base")
        for key, expected in (
            ("closeout-staged", "staged candidate"),
            ("ci", "CI checkout at HEAD"),
            ("pre-push", "pushed ref ranges"),
            ("manual", "manual working tree"),
        ):
            assert expected in scope_reporting.diff_input_description(
                base, {"AR_QUALITY_INVOCATION": key}
            )

    def test_generated_scope_line_zero_targets(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        (tmp_path / "scripts" / "sync.py").write_text("pass\n", encoding="utf-8")
        with pytest.raises(scope_reporting.ScopeReportingError, match="zero generated targets"):
            scope_reporting.generated_scope_line(tmp_path, "x", tmp_path / "scripts" / "sync.py")

    def test_frontend_files_filters_suffixes_and_ignored_dirs(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        (dashboard / "src").mkdir(parents=True)
        (dashboard / "src" / "a.ts").write_text("", encoding="utf-8")
        (dashboard / "src" / "b.txt").write_text("", encoding="utf-8")
        (dashboard / "node_modules" / "c.ts").mkdir(parents=True)
        (dashboard / "node_modules" / "c.ts" / "d.ts").write_text("", encoding="utf-8")
        files = scope_reporting.frontend_files(dashboard)
        assert files == [dashboard / "src" / "a.ts"]

    def test_read_json_object_errors(self, tmp_path: Path) -> None:
        path = tmp_path / "pkg.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(scope_reporting.ScopeReportingError, match="could not read"):
            scope_reporting.read_json_object(path, "package")
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(scope_reporting.ScopeReportingError, match="not a JSON object"):
            scope_reporting.read_json_object(path, "package")

    def test_tsconfig_project_inputs(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        (dashboard / "src").mkdir(parents=True)
        (dashboard / "src" / "app.ts").write_text("", encoding="utf-8")
        (dashboard / "tsconfig.app.json").write_text(
            json.dumps({"files": ["src/app.ts"]}), encoding="utf-8"
        )
        inputs, finding = scope_reporting.tsconfig_project_inputs(
            dashboard, {"path": "tsconfig.app.json"}, 0
        )
        assert finding is None and inputs == {dashboard / "src" / "app.ts"}
        (dashboard / "worker" / "src").mkdir(parents=True)
        (dashboard / "worker" / "src" / "worker.ts").write_text("", encoding="utf-8")
        (dashboard / "worker" / "tsconfig.json").write_text(
            json.dumps({"files": ["src/worker.ts"]}), encoding="utf-8"
        )
        inputs, finding = scope_reporting.tsconfig_project_inputs(dashboard, {"path": "worker"}, 1)
        assert finding is None and inputs == {dashboard / "worker" / "src" / "worker.ts"}
        inputs, finding = scope_reporting.tsconfig_project_inputs(dashboard, {"bad": 1}, 0)
        assert finding is not None and inputs == set()
        inputs, finding = scope_reporting.tsconfig_project_inputs(
            dashboard, {"path": "tsconfig.missing.json"}, 2
        )
        assert finding is not None and inputs == set()

    def test_tsconfig_inputs(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        (dashboard / "src").mkdir(parents=True)
        (dashboard / "src" / "app.ts").write_text("", encoding="utf-8")
        (dashboard / "tsconfig.json").write_text(
            json.dumps({"references": [{"path": "tsconfig.app.json"}]}), encoding="utf-8"
        )
        (dashboard / "tsconfig.app.json").write_text(
            json.dumps({"files": ["src/app.ts"]}), encoding="utf-8"
        )
        projects, inputs = scope_reporting.tsconfig_inputs(dashboard)
        assert projects == 1 and inputs == 1
        (dashboard / "tsconfig.json").write_text(json.dumps({}), encoding="utf-8")
        with pytest.raises(scope_reporting.ScopeReportingError, match="zero TypeScript projects"):
            scope_reporting.tsconfig_inputs(dashboard)

    def test_config_input_files_glob(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        (dashboard / "src").mkdir(parents=True)
        (dashboard / "src" / "x.ts").write_text("", encoding="utf-8")
        found = scope_reporting.config_input_files(dashboard, {"include": ["src/*.ts"]})
        assert found == {dashboard / "src" / "x.ts"}

    def test_dashboard_test_scope_line(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        (dashboard / "src").mkdir(parents=True)
        (dashboard / "src" / "a.test.ts").write_text("", encoding="utf-8")
        (dashboard / "vitest.config.ts").write_text("", encoding="utf-8")
        line = scope_reporting.dashboard_test_scope_line(
            dashboard, scope_reporting.frontend_files(dashboard)
        )
        assert "dashboard-test" in line
        (dashboard / "vitest.config.ts").unlink()
        with pytest.raises(scope_reporting.ScopeReportingError, match=r"vitest\.config\.ts"):
            scope_reporting.dashboard_test_scope_line(
                dashboard, scope_reporting.frontend_files(dashboard)
            )
        (dashboard / "vitest.config.ts").write_text("", encoding="utf-8")
        (dashboard / "src" / "a.test.ts").unlink()
        with pytest.raises(scope_reporting.ScopeReportingError, match="zero test files"):
            scope_reporting.dashboard_test_scope_line(
                dashboard, scope_reporting.frontend_files(dashboard)
            )

    def test_dashboard_build_scope_line_error(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        dashboard = tmp_path / "dashboard"
        (dashboard / "panda.config.ts").write_text("x", encoding="utf-8")
        with pytest.raises(scope_reporting.ScopeReportingError, match="could not resolve"):
            scope_reporting.dashboard_build_scope_line(dashboard, 1, 1)

    def test_fixed_step_unknown_name(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        gate = scope.derive_scope(tmp_path)
        with pytest.raises(scope_reporting.ScopeReportingError, match="no scope contract"):
            scope_reporting.fixed_step_scope_line("bogus", tmp_path, gate)

    def test_generated_scope_line_nonzero(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        (tmp_path / "scripts" / "sync.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
        with pytest.raises(scope_reporting.ScopeReportingError, match="failed with exit 2"):
            scope_reporting.generated_scope_line(tmp_path, "x", tmp_path / "scripts" / "sync.py")

    def test_tsconfig_project_invalid_json(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        (dashboard / "src").mkdir(parents=True)
        (dashboard / "tsconfig.app.json").write_text("{bad", encoding="utf-8")
        inputs, finding = scope_reporting.tsconfig_project_inputs(
            dashboard, {"path": "tsconfig.app.json"}, 0
        )
        assert inputs == set() and finding is not None

    def test_dashboard_lint_scope_line_errors(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        dashboard.mkdir()
        with pytest.raises(
            scope_reporting.ScopeReportingError, match=r"eslint\.config\.js is missing"
        ):
            scope_reporting.dashboard_lint_scope_line(dashboard)
        (dashboard / "eslint.config.js").write_text("", encoding="utf-8")
        with (
            mock.patch.object(
                scope_reporting, "eslint_result_files", side_effect=scope.ScopeError("eslint boom")
            ),
            pytest.raises(scope_reporting.ScopeReportingError, match="eslint boom"),
        ):
            scope_reporting.dashboard_lint_scope_line(dashboard)

    def test_dashboard_scope_lines(self, tmp_path: Path) -> None:
        _valid_repository(tmp_path)
        dashboard = tmp_path / "dashboard"
        (dashboard / "package.json").write_text(
            json.dumps({"scripts": {"lint": "eslint ."}}), encoding="utf-8"
        )
        with pytest.raises(scope_reporting.ScopeReportingError, match="no 'test' script"):
            scope_reporting.dashboard_scope_line(tmp_path, "test")
        (dashboard / "package.json").write_text(
            json.dumps({"scripts": {"foo": "x"}}), encoding="utf-8"
        )
        (dashboard / "tsconfig.json").write_text(
            json.dumps({"references": [{"path": "tsconfig.app.json"}]}), encoding="utf-8"
        )
        (dashboard / "tsconfig.app.json").write_text(
            json.dumps({"files": ["src/app.ts"]}), encoding="utf-8"
        )
        (dashboard / "src" / "app.ts").write_text("", encoding="utf-8")
        with pytest.raises(
            scope_reporting.ScopeReportingError, match="unsupported dashboard quality"
        ):
            scope_reporting.dashboard_scope_line(tmp_path, "foo")

    def test_main_runs_as_module(self) -> None:
        with pytest.raises(SystemExit):
            runpy.run_path(str(scope_reporting.__file__), run_name="__main__")
