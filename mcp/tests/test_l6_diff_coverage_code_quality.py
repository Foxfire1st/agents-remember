"""L6 closeout coverage tests for code-quality gate internals."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _quality_admission import QUALITY_TEST_ADMISSION
from agents_remember_test_support.code_quality import (
    application_boundary,
    check,
    crap_calculator,
    scope,
    single_owner,
)
from agents_remember_test_support.code_quality.application_boundary import BoundaryContractError
from agents_remember_test_support.code_quality.scope import ScopeError


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", repo.as_posix(), *args],
        text=True,
        capture_output=True,
        check=True,
    )


def _valid_project(root: Path) -> None:
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
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.email=scope@agents-remember.invalid",
        "-c",
        "user.name=Scope Tests",
        "commit",
        "--quiet",
        "-m",
        "seed",
    )


class TestApplicationBoundary:
    def test_read_contract_errors(self, tmp_path: Path) -> None:
        layers = tmp_path / "layers.toml"
        with pytest.raises(BoundaryContractError, match="missing package contract"):
            application_boundary._read_contract(layers)
        layers.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(BoundaryContractError, match="no \\[package"):
            application_boundary._read_contract(layers)
        layers.write_text("[package]\nmodels = 1\n", encoding="utf-8")
        with pytest.raises(BoundaryContractError, match="invalid package declaration"):
            application_boundary._read_contract(layers)
        layers.write_text('[package.models]\nrank = "x"\n', encoding="utf-8")
        with pytest.raises(BoundaryContractError, match="no integer rank"):
            application_boundary._read_contract(layers)
        layers.write_text(
            "[package.models]\nrank = 0\n[package.application]\nrank = 1\n", encoding="utf-8"
        )
        with pytest.raises(BoundaryContractError, match="does not declare package 'mcp'"):
            application_boundary._read_contract(layers)
        layers.write_text(
            "[package.models]\nrank = 2\n[package.application]\nrank = 1\n"
            "[package.mcp]\nrank = 3\n",
            encoding="utf-8",
        )
        with pytest.raises(BoundaryContractError, match="must order models"):
            application_boundary._read_contract(layers)

    def test_read_contract_ok(self, tmp_path: Path) -> None:
        layers = tmp_path / "layers.toml"
        layers.write_text(
            "[package.models]\nrank = 0\n[package.application]\nrank = 1\n"
            "[package.mcp]\nrank = 2\n",
            encoding="utf-8",
        )
        contract = application_boundary._read_contract(layers)
        assert contract.ranks["models"] == 0 and contract.models_rank == 0

    def test_resolved_imports(self) -> None:
        imp = cast(ast.Import, ast.parse("import a.b, c").body[0])
        assert application_boundary._resolved_imports(imp, ["pkg"]) == ["a.b", "c"]
        imp_from = cast(ast.ImportFrom, ast.parse("from . import x").body[0])
        assert application_boundary._resolved_imports(imp_from, ["pkg", "sub"]) == ["pkg.x"]
        imp_from = cast(ast.ImportFrom, ast.parse("from ..sibling import y").body[0])
        assert application_boundary._resolved_imports(imp_from, ["pkg", "sub"]) == ["sibling.y"]

    def test_top_package_and_permitted(self) -> None:
        assert application_boundary._top_package("other.x") is None
        assert application_boundary._top_package("agents_remember") == ""
        assert application_boundary._top_package("agents_remember.models.x") == "models"
        contract = SimpleNamespace(
            ranks={"models": 0, "unknown": 5, "application": 1}, models_rank=0
        )
        typed = cast(application_boundary._LayerContract, contract)
        assert application_boundary._permitted("unknown", typed) is False
        assert application_boundary._permitted("models", typed) is True
        assert application_boundary._permitted("application", typed) is True

    def test_required_modules_errors(self, tmp_path: Path) -> None:
        root = tmp_path / "mcp"
        with pytest.raises(BoundaryContractError, match="missing MCP transport"):
            application_boundary._required_modules(root)
        (root / "mcp" / "tools").mkdir(parents=True)
        (root / "mcp" / "registration").mkdir(parents=True)
        with pytest.raises(BoundaryContractError, match="no Python modules"):
            application_boundary._required_modules(root)
        (root / "mcp" / "tools" / "x.py").write_text("", encoding="utf-8")
        (root / "mcp" / "registration" / "y.py").write_text("", encoding="utf-8")
        with pytest.raises(BoundaryContractError, match="missing MCP server startup"):
            application_boundary._required_modules(root)


class TestSingleOwner:
    def test_import_from_origin(self) -> None:
        node = ast.parse("from x import y").body[0]
        assert isinstance(node, ast.ImportFrom)
        assert single_owner._import_from_origin(node, "a.b") == "x"
        node = ast.parse("from . import y").body[0]
        assert isinstance(node, ast.ImportFrom)
        assert single_owner._import_from_origin(node, "agents_remember.tasks.leaf_doc") == (
            "agents_remember"
        )
        node = ast.parse("from ..z import y").body[0]
        assert isinstance(node, ast.ImportFrom)
        assert single_owner._import_from_origin(node, "agents_remember.tasks.leaf_doc") == "z"

    def test_task_writer_bindings(self) -> None:
        tree = ast.parse(
            "import agents_remember.tasks.store as store\n"
            "from agents_remember.tasks.store import write_task_doc as w, write_task_docs\n"
            "from agents_remember.tasks import write_task_doc as t\n"
            "from agents_remember.tasks import *\n"
        )
        writers, modules = single_owner._task_writer_bindings(tree, "m")
        assert writers["w"] == "write_task_doc"
        assert writers["t"] == "write_task_doc"
        assert "write_task_docs" in writers
        assert modules["store"] == "agents_remember.tasks.store"


class TestCheckRails:
    def test_run_fixed_checks(self, tmp_path: Path) -> None:
        config = SimpleNamespace(
            project_root=tmp_path,
            scope=SimpleNamespace(coverage_paths=[Path("mcp/src")]),
            progress=None,
            coverage_data=None,
        )
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({"files": {"a": {}}}), encoding="utf-8")
        calls: list[tuple[str, int]] = []

        def runner(name: str, command: list[str], cwd: Path, env: dict) -> SimpleNamespace:
            calls.append((name, 0 if name == "pytest" else 1))
            return SimpleNamespace(return_code=0 if name == "pytest" else 1)

        steps = [
            check.Step("pytest", ["pytest"]),
            check.Step("ruff", ["ruff"]),
        ]
        lines: list[str] = []
        with (
            mock.patch.object(check, "quality_steps", return_value=steps),
            mock.patch.object(check.scope_reporting, "fixed_step_scope_line", return_value="scope"),
            mock.patch.object(
                check.scope_reporting, "coverage_result_scope_line", return_value="cov"
            ),
        ):
            failed = check.run_fixed_checks(
                cast(check.CheckConfig, config),
                coverage_json,
                runner=cast(check.CommandRunner, runner),
                printer=lines.append,
            )
        assert failed == 1 and any("ruff FAIL" in line for line in lines)
        assert any("pytest PASS" in line for line in lines)

    def test_run_fixed_checks_coverage_report_failure(self, tmp_path: Path) -> None:
        config = SimpleNamespace(
            project_root=tmp_path,
            scope=SimpleNamespace(coverage_paths=[Path("mcp/src")]),
            progress=None,
            coverage_data=None,
        )
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text("{}", encoding="utf-8")
        steps = [check.Step("pytest", ["pytest"])]

        def runner(name: str, command: list[str], cwd: Path, env: dict) -> SimpleNamespace:
            return SimpleNamespace(return_code=0)

        lines: list[str] = []
        with (
            mock.patch.object(check, "quality_steps", return_value=steps),
            mock.patch.object(check.scope_reporting, "fixed_step_scope_line", return_value="scope"),
            mock.patch.object(
                check.scope_reporting,
                "coverage_result_scope_line",
                side_effect=ScopeError("bad coverage"),
            ),
        ):
            failed = check.run_fixed_checks(
                cast(check.CheckConfig, config),
                coverage_json,
                runner=cast(check.CommandRunner, runner),
                printer=lines.append,
            )
        assert failed == 1
        assert any("coverage result reporting failed" in line for line in lines)

    def test_run_crap_calculator_branches(self, tmp_path: Path) -> None:
        config = SimpleNamespace(
            project_root=tmp_path,
            scope=SimpleNamespace(coverage_paths=[Path("mcp")]),
            threshold=20.0,
            top=5,
        )
        lines: list[str] = []
        missing = tmp_path / "missing.json"
        with mock.patch.object(check.scope_reporting, "crap_scope_line", return_value="crap"):
            assert (
                check.run_crap_calculator(
                    cast(check.CheckConfig, config), missing, tmp_path, printer=lines.append
                )
                == 1
            )

        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text("{}", encoding="utf-8")
        with (
            mock.patch.object(
                crap_calculator, "calculate_scores", side_effect=RuntimeError("boom")
            ),
            mock.patch.object(check.scope_reporting, "crap_scope_line", return_value="crap"),
        ):
            assert (
                check.run_crap_calculator(
                    cast(check.CheckConfig, config), coverage_json, tmp_path, printer=lines.append
                )
                == 1
            )

        with (
            mock.patch.object(crap_calculator, "calculate_scores", return_value=[]),
            mock.patch.object(check.scope_reporting, "crap_scope_line", return_value="crap"),
        ):
            assert (
                check.run_crap_calculator(
                    cast(check.CheckConfig, config), coverage_json, tmp_path, printer=lines.append
                )
                == 1
            )

        score = SimpleNamespace(
            crap=19.0,
            complexity=5,
            coverage_ratio=0.5,
            path=Path("mcp/x.py"),
            start_line=1,
            function="f",
        )
        with (
            mock.patch.object(crap_calculator, "calculate_scores", return_value=[score]),
            mock.patch.object(crap_calculator, "render_table", return_value="table"),
            mock.patch.object(check.scope_reporting, "crap_scope_line", return_value="crap"),
        ):
            assert (
                check.run_crap_calculator(
                    cast(check.CheckConfig, config), coverage_json, tmp_path, printer=lines.append
                )
                == 0
            )

        score = SimpleNamespace(
            crap=25.0,
            complexity=5,
            coverage_ratio=0.5,
            path=Path("mcp/x.py"),
            start_line=1,
            function="f",
        )
        with (
            mock.patch.object(crap_calculator, "calculate_scores", return_value=[score]),
            mock.patch.object(crap_calculator, "render_table", return_value="table"),
            mock.patch.object(check.scope_reporting, "crap_scope_line", return_value="crap"),
        ):
            assert (
                check.run_crap_calculator(
                    cast(check.CheckConfig, config), coverage_json, tmp_path, printer=lines.append
                )
                == 1
            )

    def test_config_from_args_error(self, tmp_path: Path) -> None:
        _valid_project(tmp_path)
        args = argparse.Namespace(
            project_root=tmp_path,
            coverage_json=None,
            threshold=20.0,
            top=5,
            diff_base=None,
            diff_floor=100.0,
        )
        with (
            mock.patch.object(
                check.scope_reporting,
                "validate_invocation_environment",
                side_effect=check.scope_reporting.ScopeReportingError("bad env"),
            ),
            pytest.raises(ScopeError, match="bad env"),
        ):
            check.config_from_args(args, admission=QUALITY_TEST_ADMISSION)


class TestScopeModuleBranches:
    def test_git_untracked_files(self, tmp_path: Path) -> None:
        _valid_project(tmp_path)
        (tmp_path / "untracked.py").write_text("", encoding="utf-8")
        files = scope.git_untracked_files(tmp_path, [Path(".")])
        assert files == [Path("untracked.py")]
        with (
            mock.patch.object(scope.git_command, "run_git", side_effect=OSError("boom")),
            pytest.raises(ScopeError, match="could not enumerate"),
        ):
            scope.git_untracked_files(tmp_path, [Path(".")])
        failed = subprocess.CompletedProcess([], 128, stdout="", stderr="nope")
        with (
            mock.patch.object(scope.git_command, "run_git", return_value=failed),
            pytest.raises(ScopeError, match="exit 128"),
        ):
            scope.git_untracked_files(tmp_path, [Path(".")])

    def test_validate_pyright_venv(self, tmp_path: Path) -> None:
        scope.validate_pyright_venv(tmp_path, {}, tmp_path / "pyproject.toml")
        with pytest.raises(ScopeError, match="both be strings or both be absent"):
            scope.validate_pyright_venv(tmp_path, {"venvPath": "."}, tmp_path / "pyproject.toml")
        with pytest.raises(ScopeError, match="missing directory"):
            scope.validate_pyright_venv(
                tmp_path, {"venvPath": ".", "venv": ".venv"}, tmp_path / "pyproject.toml"
            )
        (tmp_path / ".venv").mkdir()
        scope.validate_pyright_venv(
            tmp_path, {"venvPath": ".", "venv": ".venv"}, tmp_path / "pyproject.toml"
        )

    def test_path_is_within(self) -> None:
        assert scope.path_is_within(Path("a/b.py"), Path(".")) is True
        assert scope.path_is_within(Path("a/b.py"), Path("a")) is True
        assert scope.path_is_within(Path("b.py"), Path("a")) is False

    def test_validate_quality_config_branches(self, tmp_path: Path) -> None:
        _valid_project(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            "\n".join(
                (
                    "[tool.ruff]",
                    "line-length = 100",
                    "[tool.pyright]",
                    'include = ["."]',
                    "[tool.coverage.run]",
                    "branch = false",
                    "[tool.pytest.ini_options]",
                    'testpaths = ["tests"]',
                    "",
                )
            ),
            encoding="utf-8",
        )
        with pytest.raises(ScopeError, match="branch must be true"):
            scope.validate_quality_config(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            "\n".join(
                (
                    "[tool.ruff]",
                    "line-length = 100",
                    "[tool.pyright]",
                    'venvPath = "."',
                    "[tool.coverage.run]",
                    "branch = true",
                    "[tool.pytest.ini_options]",
                    'testpaths = "bad"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        with pytest.raises(ScopeError, match="testpaths is missing or empty"):
            scope.validate_quality_config(tmp_path)

    def test_validate_quality_config_pyright_include(self, tmp_path: Path) -> None:
        _valid_project(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            "\n".join(
                (
                    "[tool.ruff]",
                    "line-length = 100",
                    "[tool.pyright]",
                    'venvPath = "."',
                    "[tool.coverage.run]",
                    "branch = true",
                    "[tool.pytest.ini_options]",
                    'testpaths = ["tests"]',
                    "",
                )
            ),
            encoding="utf-8",
        )
        with pytest.raises(ScopeError, match="include is missing or empty"):
            scope.validate_quality_config(tmp_path)

    def test_validate_quality_config_zero_test_files(self, tmp_path: Path) -> None:
        _valid_project(tmp_path)
        (tmp_path / "empty").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            "\n".join(
                (
                    "[tool.ruff]",
                    "line-length = 100",
                    "[tool.pyright]",
                    'include = ["."]',
                    "[tool.coverage.run]",
                    "branch = true",
                    "[tool.pytest.ini_options]",
                    'testpaths = ["empty"]',
                    "",
                )
            ),
            encoding="utf-8",
        )
        with pytest.raises(ScopeError, match="zero Python files"):
            scope.validate_quality_config(tmp_path)

    def test_config_string_array(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ts"
        path.write_text('include: ["a", "b"]\n', encoding="utf-8")
        assert scope.config_string_array(path, r"\binclude\s*:\s*", "Include") == ("a", "b")
        with pytest.raises(ScopeError, match="could not read"):
            scope.config_string_array(tmp_path / "missing.ts", "x", "Include")
        path.write_text("no array\n", encoding="utf-8")
        with pytest.raises(ScopeError, match="could not resolve"):
            scope.config_string_array(path, r"\binclude\s*:\s*", "Include")
        path.write_text("include: []\n", encoding="utf-8")
        with pytest.raises(ScopeError, match="resolves zero entries"):
            scope.config_string_array(path, r"\binclude\s*:\s*", "Include")

    def test_coverage_json_file_count(self, tmp_path: Path) -> None:
        path = tmp_path / "coverage.json"
        path.write_text("{bad", encoding="utf-8")
        with pytest.raises(ScopeError, match="could not read"):
            scope.coverage_json_file_count(path)
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(ScopeError, match="zero file records"):
            scope.coverage_json_file_count(path)
        path.write_text('{"files": {"a.py": {}}}', encoding="utf-8")
        assert scope.coverage_json_file_count(path) == 1

    def test_dashboard_build_inputs(self, tmp_path: Path) -> None:
        dashboard = tmp_path / "dashboard"
        dashboard.mkdir()
        (dashboard / "panda.config.ts").write_text('include: ["src/**/*.ts"]\n', encoding="utf-8")
        (dashboard / "vite.config.ts").write_text(
            'const BUILD_INPUT_FILES = ["src/main.ts"]\n', encoding="utf-8"
        )
        (dashboard / "src").mkdir()
        (dashboard / "src" / "main.ts").write_text("", encoding="utf-8")
        result = scope.dashboard_build_inputs(dashboard)
        assert result.vite_inputs == (dashboard / "src" / "main.ts",)
        (dashboard / "src" / "main.ts").unlink()
        with pytest.raises(ScopeError, match="names missing inputs"):
            scope.dashboard_build_inputs(dashboard)
