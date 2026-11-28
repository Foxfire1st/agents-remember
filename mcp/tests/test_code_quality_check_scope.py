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


def _matches_pyrefly_exclude(path: Path, pattern: str) -> bool:
    """Return True when ``path`` or any ancestor directory matches a Pyrefly exclude glob."""
    posix = path.as_posix()
    normalized = pattern.rstrip("/")
    if normalized.startswith("**/"):
        tail = normalized[3:]
        if "/" not in tail:
            return any(fnmatch.fnmatchcase(part, tail) for part in path.parts)
        return fnmatch.fnmatchcase(posix, normalized) or fnmatch.fnmatchcase(posix, tail)
    candidates = [posix, *(parent.as_posix() for parent in path.parents if parent != Path("."))]
    return any(fnmatch.fnmatchcase(candidate, normalized) for candidate in candidates)


def _run_pyrefly_json(project_root: Path, *extra_args: str) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyrefly",
            "check",
            "--force-exclude",
            "--python-interpreter-path",
            sys.executable,
            "--output-format",
            "json",
            *extra_args,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    raw = completed.stdout.strip()
    if not raw and completed.returncode == 0:
        return completed.returncode, {"errors": []}
    return completed.returncode, json.loads(raw)


def _write_pyrefly_pyproject(
    project_root: Path,
    *,
    includes: list[str],
    excludes: list[str],
) -> None:
    lines = [
        "[tool.pyrefly]",
        f"project-includes = {json.dumps(includes)}",
        f"project-excludes = {json.dumps(excludes)}",
        "disable-project-excludes-heuristics = true",
        'python-version = "3.14"',
        "",
    ]
    (project_root / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")


def _diagnostic_relative_paths(payload: dict[str, object], project_root: Path) -> set[str]:
    diagnostics = payload.get("errors")
    assert isinstance(diagnostics, list)
    resolved_root = project_root.resolve()
    result: set[str] = set()
    for entry in diagnostics:
        assert isinstance(entry, dict)
        if entry.get("severity") != "error":
            continue
        raw_file = entry.get("path")
        assert isinstance(raw_file, str)
        candidate = Path(raw_file)
        if not candidate.is_absolute():
            candidate = resolved_root / candidate
        result.add(candidate.resolve().relative_to(resolved_root).as_posix())
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


class PyreflyScopeConfigurationTests(unittest.TestCase):
    def _repo_pyrefly_exclude(self) -> list[str]:
        _, data = quality_scope.read_pyproject(REPOSITORY_ROOT)
        pyrefly = quality_scope.toml_section(data, ("tool", "pyrefly"))
        exclude = pyrefly.get("project-excludes")
        assert isinstance(exclude, list)
        assert all(isinstance(entry, str) for entry in exclude)
        return list(exclude)

    def test_repo_pyrefly_config_excludes_nested_environments_and_keeps_tracked_python(
        self,
    ) -> None:
        quality_scope.validate_quality_config(REPOSITORY_ROOT)
        _, data = quality_scope.read_pyproject(REPOSITORY_ROOT)
        pyrefly = quality_scope.toml_section(data, ("tool", "pyrefly"))
        self.assertEqual(pyrefly.get("project-includes"), [".", ".dagger"])
        self.assertIs(pyrefly.get("disable-project-excludes-heuristics"), True)

        exclude = self._repo_pyrefly_exclude()
        for required in (
            "**/.git",
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
            if _matches_pyrefly_exclude(path, pattern)
        ]
        self.assertEqual(dropped, [])

    def test_pyrefly_cli_file_list_and_force_exclude_match_project_scope(
        self,
    ) -> None:
        exclude = self._repo_pyrefly_exclude()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in (
                ("included_dir/inc_err.py", 'x: int = "bad_inc"\n'),
                ("other_dir/other_err.py", 'y: int = "bad_other"\n'),
                ("scripts/script_err.py", 's: int = "bad_script"\n'),
                (".dagger/src/dagger_err.py", 'd: int = "bad_dagger"\n'),
                ("mcp/.venv/lib/venv_err.py", 'v: int = "bad_venv"\n'),
                ("dashboard/node_modules/pkg/node_err.py", 'n: int = "bad_node"\n'),
            ):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")

            _write_pyrefly_pyproject(root, includes=["included_dir"], excludes=exclude)

            # Fact 1: an explicit CLI file list checks listed files outside project-includes.
            rc_other, payload_other = _run_pyrefly_json(root, "other_dir/other_err.py")
            self.assertEqual(rc_other, 1)
            self.assertEqual(
                _diagnostic_relative_paths(payload_other, root),
                {"other_dir/other_err.py"},
            )

            # Fact 2: `--force-exclude` drops explicitly listed files matching project-excludes,
            # while tracked paths including `.dagger/` stay checked.
            rc_excluded, payload_excluded = _run_pyrefly_json(root, "mcp/.venv/lib/venv_err.py")
            self.assertEqual(rc_excluded, 0)
            self.assertEqual(_diagnostic_relative_paths(payload_excluded, root), set())

            rc_listed, payload_listed = _run_pyrefly_json(
                root,
                "other_dir/other_err.py",
                "scripts/script_err.py",
                ".dagger/src/dagger_err.py",
            )
            self.assertEqual(rc_listed, 1)
            self.assertEqual(
                _diagnostic_relative_paths(payload_listed, root),
                {"other_dir/other_err.py", "scripts/script_err.py", ".dagger/src/dagger_err.py"},
            )

            # Bare run with project-includes = [".", ".dagger"] skips nested .venv and node_modules
            # while checking checkout code including `.dagger/`.
            _write_pyrefly_pyproject(root, includes=[".", ".dagger"], excludes=exclude)
            rc_bare, payload_bare = _run_pyrefly_json(root)
            self.assertEqual(rc_bare, 1)
            self.assertEqual(
                _diagnostic_relative_paths(payload_bare, root),
                {
                    "included_dir/inc_err.py",
                    "other_dir/other_err.py",
                    "scripts/script_err.py",
                    ".dagger/src/dagger_err.py",
                },
            )
