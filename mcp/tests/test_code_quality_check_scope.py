from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agents_remember_test_support.code_quality import check
from agents_remember_test_support.code_quality import scope as quality_scope
from test_code_quality_check import (
    REPOSITORY_ROOT,
    run_git,
    write_sample_repository,
)


def _matches_pyright_exclude(path: Path, pattern: str) -> bool:
    """Return True when ``path`` or any ancestor directory matches a Pyright exclude glob."""
    posix = path.as_posix()
    normalized = pattern.rstrip("/")
    if normalized.startswith("**/"):
        tail = normalized[3:]
        if "/" not in tail:
            return any(fnmatch.fnmatchcase(part, tail) for part in path.parts)
        return fnmatch.fnmatchcase(posix, normalized) or fnmatch.fnmatchcase(posix, tail)
    candidates = [posix, *(parent.as_posix() for parent in path.parents if parent != Path("."))]
    return any(fnmatch.fnmatchcase(candidate, normalized) for candidate in candidates)


def _run_pyright_json(project_root: Path, *extra_args: str) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyright",
            "--project",
            ".",
            "--pythonpath",
            sys.executable,
            "--outputjson",
            *extra_args,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def _write_pyright_pyproject(
    project_root: Path,
    *,
    include: list[str],
    exclude: list[str],
) -> None:
    lines = [
        "[tool.pyright]",
        f"include = {json.dumps(include)}",
        f"exclude = {json.dumps(exclude)}",
        'pythonVersion = "3.14"',
        'typeCheckingMode = "basic"',
        "",
    ]
    (project_root / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")


def _summary_files_analyzed(payload: dict[str, object]) -> int:
    summary = payload.get("summary")
    assert isinstance(summary, dict)
    analyzed = summary.get("filesAnalyzed")
    assert isinstance(analyzed, int)
    return analyzed


def _diagnostic_relative_paths(payload: dict[str, object], project_root: Path) -> set[str]:
    diagnostics = payload.get("generalDiagnostics")
    assert isinstance(diagnostics, list)
    resolved_root = project_root.resolve()
    result: set[str] = set()
    for entry in diagnostics:
        assert isinstance(entry, dict)
        raw_file = entry.get("file")
        assert isinstance(raw_file, str)
        result.add(Path(raw_file).resolve().relative_to(resolved_root).as_posix())
    return result


class GateScopeDerivationTests(unittest.TestCase):
    def test_new_importable_package_requires_explicit_product_or_verification_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_sample_repository(Path(tmp))
            support = root / "support"
            support.mkdir()
            (support / "__init__.py").write_text("", encoding="utf-8")
            run_git(root, "add", "support/__init__.py")

            with self.assertRaises(check.ScopeError) as raised:
                check.derive_scope(root)

            self.assertIn("missing=['support']", str(raised.exception))

            pyproject = root / "pyproject.toml"
            pyproject.write_text(
                pyproject.read_text(encoding="utf-8").replace(
                    "verification_package_roots = []",
                    'verification_package_roots = ["support"]',
                ),
                encoding="utf-8",
            )
            scope = check.derive_scope(root)

            self.assertEqual(scope.coverage_paths, [Path("pkg")])
            self.assertIn(Path("support/__init__.py"), scope.lint_paths)
            self.assertIn(Path("support/__init__.py"), scope.type_paths)

    def test_package_authority_rejects_overlap_and_stale_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_sample_repository(Path(tmp))
            pyproject = root / "pyproject.toml"
            original = pyproject.read_text(encoding="utf-8")
            pyproject.write_text(
                original.replace(
                    "verification_package_roots = []",
                    'verification_package_roots = ["pkg"]',
                ),
                encoding="utf-8",
            )

            with self.assertRaises(check.ScopeError) as overlap:
                check.derive_scope(root)

            self.assertIn("both product and verification", str(overlap.exception))

            pyproject.write_text(
                original.replace(
                    "verification_package_roots = []",
                    'verification_package_roots = ["missing_support"]',
                ),
                encoding="utf-8",
            )

            with self.assertRaises(check.ScopeError) as stale:
                check.derive_scope(root)

            self.assertIn("stale=['missing_support']", str(stale.exception))


class PyrightScopeConfigurationTests(unittest.TestCase):
    def _repo_pyright_exclude(self) -> list[str]:
        _, data = quality_scope.read_pyproject(REPOSITORY_ROOT)
        pyright = quality_scope.toml_section(data, ("tool", "pyright"))
        exclude = pyright.get("exclude")
        assert isinstance(exclude, list)
        assert all(isinstance(entry, str) for entry in exclude)
        return list(exclude)

    def test_repo_pyright_config_excludes_nested_environments_and_keeps_tracked_python(
        self,
    ) -> None:
        quality_scope.validate_quality_config(REPOSITORY_ROOT)
        _, data = quality_scope.read_pyproject(REPOSITORY_ROOT)
        pyright = quality_scope.toml_section(data, ("tool", "pyright"))
        self.assertEqual(pyright.get("include"), ["."])

        exclude = self._repo_pyright_exclude()
        for required in (
            "**/__pycache__",
            "**/.cache",
            "**/.mypy_cache",
            "**/.pytest_cache",
            "**/.ruff_cache",
            "**/.venv",
            "**/*.egg-info",
            "**/build",
            "**/dist",
            "**/node_modules",
            "**/venv",
        ):
            self.assertIn(required, exclude)
        self.assertNotIn(".*", exclude)
        self.assertNotIn("**/.*", exclude)

        tracked = quality_scope.git_ls_files(REPOSITORY_ROOT, "*.py")
        self.assertTrue(
            any(path.parts[0] == ".dagger" for path in tracked),
            "expected tracked Python files under .dagger/",
        )
        dropped = [
            (path.as_posix(), pattern)
            for path in tracked
            for pattern in exclude
            if _matches_pyright_exclude(path, pattern)
        ]
        self.assertEqual(dropped, [])

    def test_pyright_cli_file_list_replaces_include_and_exclude_filters_listed_files(
        self,
    ) -> None:
        exclude = self._repo_pyright_exclude()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in (
                ("included_dir/inc_err.py", 'x: int = "bad_inc"\n'),
                ("other_dir/other_err.py", 'y: int = "bad_other"\n'),
                ("scripts/script_err.py", 's: int = "bad_script"\n'),
                ("mcp/.venv/lib/venv_err.py", 'v: int = "bad_venv"\n'),
                ("dashboard/node_modules/pkg/node_err.py", 'n: int = "bad_node"\n'),
            ):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")

            _write_pyright_pyproject(root, include=["included_dir"], exclude=exclude)

            # Fact 1: an explicit CLI file list replaces [tool.pyright] include.
            rc_other, payload_other = _run_pyright_json(root, "other_dir/other_err.py")
            self.assertEqual(rc_other, 1)
            self.assertEqual(_summary_files_analyzed(payload_other), 1)
            self.assertEqual(
                _diagnostic_relative_paths(payload_other, root),
                {"other_dir/other_err.py"},
            )

            # Fact 2: Pyright drops an explicitly listed file when an exclude pattern matches it,
            # while non-dot tracked paths stay checked under the repo's exclude list (Pyright
            # 1.1.414+ auto-excludes `**/.*` independently of `[tool.pyright] exclude`).
            rc_excluded, payload_excluded = _run_pyright_json(root, "mcp/.venv/lib/venv_err.py")
            self.assertEqual(rc_excluded, 0)
            self.assertEqual(_summary_files_analyzed(payload_excluded), 0)

            rc_listed, payload_listed = _run_pyright_json(
                root,
                "other_dir/other_err.py",
                "scripts/script_err.py",
            )
            self.assertEqual(rc_listed, 1)
            self.assertEqual(_summary_files_analyzed(payload_listed), 2)
            self.assertEqual(
                _diagnostic_relative_paths(payload_listed, root),
                {"other_dir/other_err.py", "scripts/script_err.py"},
            )

            # Bare run with include = ["."] skips nested .venv and node_modules while checking
            # checkout code outside excluded trees.
            _write_pyright_pyproject(root, include=["."], exclude=exclude)
            rc_bare, payload_bare = _run_pyright_json(root)
            self.assertEqual(rc_bare, 1)
            self.assertEqual(_summary_files_analyzed(payload_bare), 3)
            self.assertEqual(
                _diagnostic_relative_paths(payload_bare, root),
                {
                    "included_dir/inc_err.py",
                    "other_dir/other_err.py",
                    "scripts/script_err.py",
                },
            )

            # Hook/wrapper file-list invocation gives identical results under the old top-level
            # exclude list and the new nested exclude list on the same tracked file list.
            tracked_args = (
                "included_dir/inc_err.py",
                "other_dir/other_err.py",
                "scripts/script_err.py",
            )
            rc_after_list, payload_after_list = _run_pyright_json(root, *tracked_args)
            legacy_exclude = [
                "**/__pycache__",
                ".cache",
                ".pytest_cache",
                ".ruff_cache",
                ".venv",
                "build",
                "dist",
                "node_modules",
            ]
            _write_pyright_pyproject(root, include=["."], exclude=legacy_exclude)
            rc_before_list, payload_before_list = _run_pyright_json(root, *tracked_args)
            self.assertEqual(rc_after_list, rc_before_list)
            self.assertEqual(
                _summary_files_analyzed(payload_after_list),
                _summary_files_analyzed(payload_before_list),
            )
            self.assertEqual(
                _diagnostic_relative_paths(payload_after_list, root),
                _diagnostic_relative_paths(payload_before_list, root),
            )
