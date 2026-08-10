"""Tests for the leaf change-set-scoped quality derivation (260731-EFA-L17-R1/R5)."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality import check, diff_coverage, targeted
from agents_remember.code_quality.scope import ScopeError


def run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=True,
    )


def write_quality_config(root: Path) -> None:
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


def targeted_repository(root: Path) -> str:
    """A leaf-shaped repository: package, importer chain, and tests, at a baseline."""
    run_git(root, "init", "--quiet", "--initial-branch=main")
    write_quality_config(root)
    (root / "src/pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "scripts").mkdir()
    (root / "src/pkg/__init__.py").write_text("", encoding="utf-8")
    (root / "src/pkg/module.py").write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
    (root / "src/pkg/relative.py").write_text(
        "from .module import value\nRELATIVE = value()\n", encoding="utf-8"
    )
    (root / "src/pkg/deep.py").write_text("from ... import nope\nDEEP = nope\n", encoding="utf-8")
    (root / "src/pkg/common.py").write_text(
        "from pkg.module import value\nfrom pkg.extra import other\nCOMMON = value() + other\n",
        encoding="utf-8",
    )
    (root / "src/pkg/importer.py").write_text(
        "from pkg.module import value\nVALUE = value()\n", encoding="utf-8"
    )
    (root / "src/pkg/top.py").write_text(
        "from pkg.importer import VALUE\nTOP = VALUE\n", encoding="utf-8"
    )
    (root / "tests/test_module.py").write_text(
        "from pkg.module import value\n\ndef test_value() -> None:\n    assert value() == 1\n",
        encoding="utf-8",
    )
    (root / "tests/test_extra.py").write_text(
        "import os\n"
        "from . import sibling\n"
        "from pkg.module import *\n"
        "def test_nothing() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    (root / "scripts/sync.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "scripts/test_module.py").write_text("NAME_MATCH_DECOY = True\n", encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(
        root,
        "-c",
        "user.email=targeted@agents-remember.invalid",
        "-c",
        "user.name=Targeted Tests",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def coverage_json_for(root: Path, relative_paths: list[Path]) -> dict[str, object]:
    files: dict[str, object] = {}
    for relative in relative_paths:
        lines = (root / relative).read_text(encoding="utf-8").splitlines()
        executed = [
            number
            for number, line in enumerate(lines, start=1)
            if line.strip() and not line.strip().startswith("#")
        ]
        files[relative.as_posix()] = {
            "executed_lines": executed,
            "missing_lines": [],
            "executed_branches": [],
            "missing_branches": [],
        }
    return {"meta": {"format": 3, "branch_coverage": True}, "files": files}


def fake_runner(
    commands: list[list[str]],
    coverage_json: Path,
    root: Path,
    coverage_paths: list[Path],
) -> check.CommandRunner:
    def run(name: str, command: list[str], cwd: Path, env: Mapping[str, str]) -> check.StepResult:
        del cwd, env
        commands.append(command)
        if name == "pytest" and coverage_paths:
            coverage_json.write_text(
                json.dumps(coverage_json_for(root, coverage_paths)), encoding="utf-8"
            )
        return check.StepResult(name, 0, command)

    return run


class TargetedScopeDerivationTests(unittest.TestCase):
    def test_changed_files_closure_and_test_subset_are_derived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "src/pkg/module.py").write_text(
                "def value() -> int:\n    return 2\n", encoding="utf-8"
            )
            (root / "src/pkg/extra.py").write_text(
                "def other() -> int:\n    return 3\n", encoding="utf-8"
            )
            run_git(root, "add", "-A")

            derived = targeted.derive_targeted_scope(root, base)

            self.assertEqual(
                derived.changed_paths,
                (Path("src/pkg/extra.py"), Path("src/pkg/module.py")),
            )
            self.assertEqual(derived.lint_paths, derived.changed_paths)
            # Reverse-import closure: importer.py imports module.py, top.py imports
            # importer.py, common.py imports both changed modules, and the extra
            # module itself is in the closure.
            for path in (
                "src/pkg/importer.py",
                "src/pkg/top.py",
                "src/pkg/common.py",
                "src/pkg/extra.py",
            ):
                self.assertIn(Path(path), derived.type_paths)
            self.assertNotIn(Path("src/pkg/module.py"), derived.reverse_import_closure)
            self.assertEqual(
                derived.coverage_paths,
                (Path("src/pkg/extra.py"), Path("src/pkg/module.py")),
            )
            # test_module.py reaches module.py through imports; test_extra.py matches
            # the extra module by name even though it does not import it.
            self.assertEqual(
                derived.test_paths,
                (Path("tests/test_extra.py"), Path("tests/test_module.py")),
            )

    def test_internal_module_is_covered_through_its_public_import_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "src/pkg/internal.py").write_text(
                "def inner() -> int:\n    return 1\n", encoding="utf-8"
            )
            (root / "src/pkg/module.py").write_text(
                "from pkg.internal import inner\n\ndef value() -> int:\n    return inner()\n",
                encoding="utf-8",
            )
            (root / "tests/test_module.py").write_text(
                "from pkg.internal import inner\n"
                "from pkg.module import value\n"
                "\n"
                "def test_value() -> None:\n"
                "    assert value() == inner()\n",
                encoding="utf-8",
            )
            run_git(root, "add", "-A")

            derived = targeted.derive_targeted_scope(root, base)

            self.assertIn(Path("src/pkg/internal.py"), derived.changed_paths)
            # No test imports pkg.internal directly; the derived subset must
            # reach it through pkg.module and its importers.
            self.assertTrue(any(path.name == "test_module.py" for path in derived.test_paths))

    def test_changed_production_module_without_tests_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "src/pkg/naked.py").write_text(
                "def uncovered() -> int:\n    return 0\n", encoding="utf-8"
            )
            run_git(root, "add", "-A")

            with self.assertRaisesRegex(ScopeError, "pkg.naked"):
                targeted.derive_targeted_scope(root, base)

    def test_string_referenced_module_is_covered_by_wiring_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "src/pkg/wiring.py").write_text(
                "def wire() -> int:\n    return 1\n", encoding="utf-8"
            )
            (root / "tests/test_wiring_registration.py").write_text(
                'WIRING_PATH = "pkg.wiring"\n', encoding="utf-8"
            )
            run_git(root, "add", "-A")

            derived = targeted.derive_targeted_scope(root, base)

            self.assertEqual(
                derived.changed_paths,
                (
                    Path("src/pkg/wiring.py"),
                    Path("tests/test_wiring_registration.py"),
                ),
            )
            self.assertEqual(derived.test_paths, (Path("tests/test_wiring_registration.py"),))

    def test_tests_only_change_leaves_production_coverage_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "tests/test_module.py").write_text(
                "from pkg.module import value\n\n"
                "def test_value() -> None:\n"
                "    assert value() == 2\n",
                encoding="utf-8",
            )

            derived = targeted.derive_targeted_scope(root, base)

            self.assertEqual(derived.changed_paths, (Path("tests/test_module.py"),))
            self.assertEqual(derived.coverage_paths, ())
            self.assertEqual(derived.test_paths, (Path("tests/test_module.py"),))

    def test_no_python_changes_derives_an_empty_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "README.md").write_text("docs only\n", encoding="utf-8")

            derived = targeted.derive_targeted_scope(root, base)

            self.assertEqual(derived.changed_paths, ())
            self.assertEqual(derived.type_paths, ())

    def test_scripts_only_change_has_no_test_subset_or_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "scripts/sync.py").write_text("VALUE = 2\n", encoding="utf-8")

            derived = targeted.derive_targeted_scope(root, base)

            self.assertEqual(derived.changed_paths, (Path("scripts/sync.py"),))
            self.assertEqual(derived.coverage_paths, ())
            self.assertEqual(derived.test_paths, ())

    def test_module_for_path_returns_none_for_the_import_root_itself(self) -> None:
        self.assertIsNone(targeted.module_for_path(Path("src"), (Path("src"),)))
        self.assertIsNone(targeted.module_for_path(Path("__init__.py"), (Path("."),)))
        self.assertEqual(
            targeted.module_for_path(Path("src/pkg/__init__.py"), (Path("src"),)),
            "pkg",
        )

    def test_resolve_relative_import_edges(self) -> None:
        self.assertIsNone(targeted.resolve_relative_import("a.b", 3, None))
        self.assertEqual(targeted.resolve_relative_import("a.b", 1, None), "a.b")
        self.assertEqual(targeted.resolve_relative_import("a.b", 1, "x"), "a.b.x")

    def test_import_from_modules_edges(self) -> None:
        absolute = ast.parse("from x import y").body[0]
        assert isinstance(absolute, ast.ImportFrom)
        self.assertIn("x.y", targeted._import_from_modules(absolute, "pkg"))
        star = ast.parse("from x import *").body[0]
        assert isinstance(star, ast.ImportFrom)
        modules = targeted._import_from_modules(star, "pkg")
        self.assertNotIn("x.*", modules)
        relative_without_root = ast.parse("from . import y").body[0]
        assert isinstance(relative_without_root, ast.ImportFrom)
        self.assertEqual(targeted._import_from_modules(relative_without_root, None), set())
        bare = ast.ImportFrom(module=None, names=[], level=0)
        self.assertEqual(targeted._import_from_modules(bare, "pkg"), set())

    def test_file_imports_refuses_a_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.py"
            broken.write_text("def broken(:\n", encoding="utf-8")

            with self.assertRaisesRegex(ScopeError, "could not parse"):
                targeted.file_imports(broken, None)

    def test_changed_paths_refuses_an_unknown_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targeted_repository(root)

            with self.assertRaisesRegex(ScopeError, "could not diff"):
                targeted.changed_python_paths(root, "not-a-commit")

    def test_git_transport_failure_is_a_scope_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targeted_repository(root)
            with (
                mock.patch.object(targeted.git_command, "run_git", side_effect=OSError("boom")),
                self.assertRaisesRegex(ScopeError, "could not run git"),
            ):
                targeted._git(root, ["diff"])

    def test_string_reference_read_failure_is_a_scope_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targeted_repository(root)
            with (
                mock.patch.object(Path, "read_text", side_effect=OSError("unreadable")),
                self.assertRaisesRegex(ScopeError, "could not read"),
            ):
                targeted._string_reference_tests(
                    root,
                    [Path("tests")],
                    "pkg.module",
                    [Path("tests/test_module.py")],
                )

    def test_coverage_root_modules_skips_a_root_package_without_import_root(self) -> None:
        modules = targeted._coverage_root_modules([Path("__init__.py")], (Path("."),))
        self.assertEqual(modules, ())


class TargetedWrapperRunTests(unittest.TestCase):
    def test_targeted_run_prints_derivation_and_scopes_every_rail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "src/pkg/module.py").write_text(
                "def value() -> int:\n    return 2\n", encoding="utf-8"
            )
            run_git(root, "add", "-A")
            args = argparse.Namespace(
                project_root=root,
                coverage_json=root / "coverage.json",
                threshold=30.0,
                top=5,
                diff_base=base,
                diff_floor=100.0,
                targeted=True,
                memory_cap_bytes=None,
            )
            config = check.config_from_args(args)
            commands: list[list[str]] = []
            output: list[str] = []

            exit_code = check.run_quality_check(
                config,
                runner=fake_runner(
                    commands,
                    root / "coverage.json",
                    root,
                    [Path("src/pkg/module.py")],
                ),
                printer=output.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(any("targeted changed files (1)" in line for line in output))
            self.assertTrue(
                any(
                    "targeted reverse-import closure for pyright adds 5 file(s)" in line
                    for line in output
                )
            )
            self.assertTrue(any("targeted test subset (2 file(s))" in line for line in output))
            ruff = commands[0]
            self.assertEqual(ruff[2:], ["ruff", "check", "src/pkg/module.py"])
            file_size = commands[2]
            self.assertEqual(file_size[2], "agents_remember.code_quality.file_size")
            self.assertIn("src/pkg/module.py", file_size)
            pyright = commands[3]
            self.assertIn("--pythonpath", pyright)
            self.assertIn("src/pkg/module.py", pyright)
            self.assertIn("src/pkg/importer.py", pyright)
            self.assertIn("src/pkg/top.py", pyright)
            # Radon report rails consume the changed production module FILES, not
            # the pytest package roots: a module name would resolve to nothing at
            # the repo root and make the report rail vacuous.
            radon_cc = commands[4]
            self.assertEqual(radon_cc[4], "src/pkg/module.py")
            radon_mi = commands[5]
            self.assertEqual(radon_mi[4], "src/pkg/module.py")
            pytest = commands[6]
            self.assertIn("tests/test_module.py", pytest)
            self.assertIn("--cov=pkg", pytest)
            self.assertNotIn("--cov=src/pkg", pytest)
            # CRAP is scoped to the changed production module, not the whole package.
            crap_scope = next(line for line in output if line.startswith("scope: CRAP-Calculator"))
            self.assertIn("1 functions", crap_scope)

    def test_radon_analyzes_the_changed_module_in_a_real_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targeted_repository(root)
            (root / "src/pkg/module.py").write_text(
                "def value(flag: int) -> int:\n"
                "    if flag == 1:\n        return 1\n"
                "    if flag == 2:\n        return 2\n"
                "    if flag == 3:\n        return 3\n"
                "    if flag == 4:\n        return 4\n"
                "    if flag == 5:\n        return 5\n"
                "    return 0\n",
                encoding="utf-8",
            )
            run_git(root, "add", "-A")

            result_cc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "radon",
                    "cc",
                    "src/pkg/module.py",
                    "-s",
                    "-n",
                    "B",
                    "--order",
                    "SCORE",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result_cc.returncode, 0)
            self.assertIn("module.py", result_cc.stdout)
            self.assertIn("value", result_cc.stdout)

            result_mi = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "radon",
                    "mi",
                    "src/pkg/module.py",
                    "-s",
                    "-n",
                    "B",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result_mi.returncode, 0)
            # The wrapper's mi rail applies radon's rank-B display filter, which can
            # hide a small file; prove the rail consumed the file by rendering
            # without the filter.
            unfiltered_mi = subprocess.run(
                [sys.executable, "-m", "radon", "mi", "src/pkg/module.py", "-s"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(unfiltered_mi.returncode, 0)
            self.assertIn("module.py", unfiltered_mi.stdout)

    def test_no_python_changes_short_circuits_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            derived = targeted.derive_targeted_scope(root, base)
            full = check.derive_scope(root)
            config = check.CheckConfig(
                project_root=root,
                scope=derived.to_gate_scope(full),
                coverage_json=root / "coverage.json",
                threshold=30.0,
                top=5,
                targeted=True,
                targeted_base=diff_coverage.BaseResolution(base, "test"),
                targeted_scope=derived,
            )

            output: list[str] = []
            commands: list[list[str]] = []
            exit_code = check.run_quality_check(
                config,
                runner=fake_runner(commands, root / "coverage.json", root, []),
                printer=output.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(commands, [])
            self.assertTrue(any("no Python files changed" in line for line in output))
            self.assertTrue(any("result: quality-wrapper PASS" in line for line in output))

    def test_tests_only_run_marks_radon_crap_and_diff_as_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "tests/test_module.py").write_text(
                "from pkg.module import value\n\n"
                "def test_value() -> None:\n"
                "    assert value() == 2\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                project_root=root,
                coverage_json=root / "coverage.json",
                threshold=30.0,
                top=5,
                diff_base=base,
                diff_floor=100.0,
                targeted=True,
                memory_cap_bytes=None,
            )
            config = check.config_from_args(args)
            commands: list[list[str]] = []
            output: list[str] = []

            exit_code = check.run_quality_check(
                config,
                runner=fake_runner(commands, root / "coverage.json", root, []),
                printer=output.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                [command[2] for command in commands],
                ["ruff", "ruff", "agents_remember.code_quality.file_size", "pyright", "pytest"],
            )
            self.assertTrue(
                any("radon report and CRAP rails are not applicable" in line for line in output)
            )
            self.assertTrue(any("CRAP-Calculator PASS (not applicable)" in line for line in output))
            self.assertTrue(any("diff-coverage PASS (not applicable)" in line for line in output))
            self.assertFalse(any("## radon-cc" in line for line in output))

    def test_scripts_only_run_skips_pytest_and_the_coverage_rails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = targeted_repository(root)
            (root / "scripts/sync.py").write_text("VALUE = 2\n", encoding="utf-8")
            args = argparse.Namespace(
                project_root=root,
                coverage_json=root / "coverage.json",
                threshold=30.0,
                top=5,
                diff_base=base,
                diff_floor=100.0,
                targeted=True,
                memory_cap_bytes=None,
            )
            config = check.config_from_args(args)
            commands: list[list[str]] = []
            output: list[str] = []

            exit_code = check.run_quality_check(
                config,
                runner=fake_runner(commands, root / "coverage.json", root, []),
                printer=output.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                [command[2] for command in commands],
                ["ruff", "ruff", "agents_remember.code_quality.file_size", "pyright"],
            )
            self.assertTrue(
                any("radon report and CRAP rails are not applicable" in line for line in output)
            )
            self.assertTrue(any("pytest rail is not applicable" in line for line in output))

    def test_source_import_roots_resolves_files_to_the_package_import_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/pkg").mkdir(parents=True)
            (root / "src/pkg/__init__.py").write_text("", encoding="utf-8")
            (root / "src/pkg/module.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts/sync.py").write_text("VALUE = 1\n", encoding="utf-8")

            self.assertEqual(
                check.source_import_roots(root, [Path("src/pkg/module.py")]),
                [root / "src"],
            )
            self.assertEqual(
                check.source_import_roots(root, [Path("scripts/sync.py")]),
                [root / "scripts"],
            )

    def test_source_import_roots_keeps_a_root_package_at_the_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "__init__.py").write_text("", encoding="utf-8")

            self.assertEqual(
                check.source_import_roots(root, [Path("__init__.py")]),
                [root],
            )
