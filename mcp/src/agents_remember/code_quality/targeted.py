"""Deterministic change-set scoping for leaf-edge quality gates.

The leaf ladder (260731-EFA-L17) keeps mandatory leaf-edge checks mandatory but
points them at the leaf's own change set instead of the whole tree:

- ruff/ruff-format run over the changed Python files;
- pyright runs over the changed files PLUS the reverse-import closure, so a
  cross-file type break in an unchanged importer is still caught;
- pytest runs the derived test subset covering the touched modules;
- coverage, CRAP and radon are scoped to the changed production modules, and the
  changed-lines coverage floor still measures the leaf's own diff.

The test subset is derived deterministically on every run, with no maintained
map file and no per-leaf declaration for a manager to validate: a test file is
selected when its imports (resolved statically from its AST) reach a changed
module, when its name matches the module's dotted path
(``code_quality.check`` -> ``test_code_quality_check.py``), or when its source
contains the module's dotted path as a token (string-based wiring tests such as
the MCP registration suite reference builder module paths without importing
them). The full derivation is printed for review so nobody has to trust the
selection.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_remember.code_quality.scope import (
    GateScope,
    ScopeError,
    git_ls_files,
    pytest_testpaths,
    top_level_packages,
)
from agents_remember.kernel import git_command


@dataclass(frozen=True)
class TargetedScopeResult:
    """Everything a targeted run derives, kept explicit so it can be printed."""

    base_revision: str
    changed_paths: tuple[Path, ...]
    lint_paths: tuple[Path, ...]
    type_paths: tuple[Path, ...]
    coverage_paths: tuple[Path, ...]
    coverage_root_modules: tuple[str, ...]
    test_paths: tuple[Path, ...]
    reverse_import_closure: tuple[Path, ...]

    def to_gate_scope(self, full_scope: GateScope) -> GateScope:
        """The concrete path lists each rail receives, plus untracked exposure."""
        return GateScope(
            lint_paths=list(self.lint_paths),
            type_paths=list(self.type_paths),
            coverage_paths=list(self.coverage_paths),
            test_paths=list(self.test_paths),
            scope_roots=full_scope.scope_roots,
            untracked_paths=full_scope.untracked_paths,
        )


def _git(project_root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return git_command.run_git(project_root, ["-c", "core.quotePath=false", *arguments])
    except (OSError, subprocess.SubprocessError) as error:
        raise ScopeError(
            f"targeted scope could not run git (git {' '.join(arguments)}): {error}"
        ) from error


def changed_python_paths(project_root: Path, base_revision: str) -> list[Path]:
    """Repo-relative changed Python files against ``base_revision``.

    The comparison is base-to-working-tree, matching the diff-coverage rail: the
    targeted run certifies the bytes the leaf is about to push or commit. Deletions
    are filtered out because there is nothing left to lint or type-check.
    """
    completed = _git(
        project_root,
        [
            "diff",
            "-z",
            "--name-only",
            "--diff-filter=ACMR",
            base_revision,
            "--",
            "*.py",
        ],
    )
    if completed.returncode != 0:
        raise ScopeError(
            "targeted scope could not diff the change set against "
            f"{base_revision}: exit {completed.returncode}: {completed.stderr.strip()}"
        )
    return sorted(Path(entry) for entry in completed.stdout.split("\0") if entry)


def import_roots_for(tracked: list[Path]) -> tuple[Path, ...]:
    """Directories that must be importable for the tracked top-level packages.

    A file under one of these roots gets a dotted module name; files outside them
    (scripts, tests) are still linted and type-checked but have no import identity
    for closure or test mapping.
    """
    return tuple(
        sorted(
            {package.parent for package in top_level_packages(tracked)},
            key=lambda path: path.as_posix(),
        )
    )


def module_for_path(path: Path, import_roots: tuple[Path, ...]) -> str | None:
    """The dotted module name for ``path`` under the nearest import root."""
    for root in import_roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not relative.parts:
            return None
        parts = relative.with_suffix("").parts
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            return None
        return ".".join(parts)
    return None


def dotted_ancestors(module: str) -> tuple[str, ...]:
    """Every package prefix of ``module``, longest first.

    ``import a.b.c`` loads ``a`` and ``a.b`` as packages too, so an importer of
    ``a.b.c`` is also an importer of ``a.b`` and ``a``.
    """
    parts = module.split(".")
    return tuple(".".join(parts[:index]) for index in range(len(parts), 0, -1))


def resolve_relative_import(root_module: str, level: int, name: str | None) -> str | None:
    parts = root_module.split(".")
    if level > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    if name is None:
        return ".".join(base)
    return ".".join([*base, name])


def _import_from_modules(node: ast.ImportFrom, root_module: str | None) -> set[str]:
    modules: set[str] = set()
    if node.level > 0:
        if root_module is None:
            return modules
        resolved = resolve_relative_import(root_module, node.level, node.module)
        if resolved is None:
            return modules
        modules.update(dotted_ancestors(resolved))
        prefix = resolved
    elif node.module:
        modules.update(dotted_ancestors(node.module))
        prefix = node.module
    else:
        return modules
    for alias in node.names:
        if alias.name != "*":
            modules.add(f"{prefix}.{alias.name}")
    return modules


def file_imports(path: Path, root_module: str | None) -> set[str]:
    """Absolute module names ``path`` imports, resolved from its AST."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise ScopeError(f"targeted scope could not parse {path}: {error}") from error
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.update(dotted_ancestors(alias.name))
        elif isinstance(node, ast.ImportFrom):
            imports.update(_import_from_modules(node, root_module))
    return imports


def name_match_tests(
    test_roots: list[Path],
    module: str,
    tracked: list[Path],
) -> set[Path]:
    """Test files whose basename matches a suffix of the dotted module path."""
    parts = module.split(".")
    candidates = {f"test_{'_'.join(parts[-tail:])}.py" for tail in range(1, len(parts) + 1)}
    matches: set[Path] = set()
    for path in tracked:
        if path.name not in candidates:
            continue
        if any(path.is_relative_to(root) for root in test_roots):
            matches.add(path)
    return matches


def _within_any(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _reverse_import_closure(
    changed: set[Path],
    changed_modules: list[str],
    modules: dict[Path, str],
    importers: dict[str, set[Path]],
) -> set[Path]:
    closure = set(changed)
    queue = list(changed_modules)
    while queue:
        module = queue.pop()
        for importer in sorted(importers.get(module, ())):
            if importer not in closure:
                closure.add(importer)
                importer_module = modules.get(importer)
                if importer_module is not None:
                    queue.append(importer_module)
    return closure


def _module_index(
    project_root: Path,
    tracked: list[Path],
    import_roots: tuple[Path, ...],
) -> tuple[dict[Path, str], dict[str, set[Path]]]:
    """Dotted module per tracked path, and every tracked file importing each module."""
    modules: dict[Path, str] = {}
    for path in tracked:
        module = module_for_path(path, import_roots)
        if module is not None:
            modules[path] = module
    importers: dict[str, set[Path]] = {}
    for path, imports in _imports_by_file(project_root, tracked, modules).items():
        for module in imports:
            importers.setdefault(module, set()).add(path)
    return modules, importers


def _imports_by_file(
    project_root: Path,
    tracked: list[Path],
    modules: dict[Path, str],
) -> dict[Path, set[str]]:
    imports: dict[Path, set[str]] = {}
    for path in tracked:
        imports[path] = file_imports(project_root / path, modules.get(path))
    return imports


def _coverage_root_modules(tracked: list[Path], import_roots: tuple[Path, ...]) -> tuple[str, ...]:
    root_modules: set[str] = set()
    for package in top_level_packages(tracked):
        module = module_for_path(package, import_roots)
        if module is not None:
            root_modules.add(module)
    return tuple(sorted(root_modules, key=str))


def _tests_for_changed_modules(
    project_root: Path,
    changed_modules: list[str],
    importers: dict[str, set[Path]],
    test_roots: list[Path],
    tracked: list[Path],
) -> dict[str, set[Path]]:
    tests_by_module: dict[str, set[Path]] = {}
    for module in changed_modules:
        tests = {path for path in importers.get(module, ()) if _within_any(path, test_roots)}
        tests |= name_match_tests(test_roots, module, tracked)
        tests |= _string_reference_tests(project_root, test_roots, module, tracked)
        tests_by_module[module] = tests
    return tests_by_module


def _string_reference_tests(
    project_root: Path,
    test_roots: list[Path],
    module: str,
    tracked: list[Path],
) -> set[Path]:
    """Test files whose source names the module path as a whole-word token."""
    pattern = re.compile(rf"\b{re.escape(module)}\b")
    matches: set[Path] = set()
    for path in tracked:
        if not _within_any(path, test_roots):
            continue
        try:
            text = (project_root / path).read_text(encoding="utf-8")
        except OSError as error:
            raise ScopeError(f"targeted scope could not read {path}: {error}") from error
        if pattern.search(text):
            matches.add(path)
    return matches


def derive_targeted_scope(project_root: Path, base_revision: str) -> TargetedScopeResult:
    """Derive the leaf change-set scope for a targeted quality run.

    Refuses a changed production module with no reachable test file: the targeted
    contract promises that the test subset covers every touched module, so an
    uncovered module is a refused gate, not a silently narrower run.
    """
    project_root = project_root.resolve()
    tracked = git_ls_files(project_root, "*.py")
    import_roots = import_roots_for(tracked)
    modules, importers = _module_index(project_root, tracked, import_roots)

    changed = changed_python_paths(project_root, base_revision)
    changed_set = set(changed)
    changed_modules = sorted({modules[path] for path in changed if path in modules}, key=str)
    closure = _reverse_import_closure(changed_set, changed_modules, modules, importers)

    test_roots = [Path(str(root)) for root in pytest_testpaths(project_root)]
    tests_by_module = _tests_for_changed_modules(
        project_root, changed_modules, importers, test_roots, tracked
    )
    uncovered = sorted((module for module, tests in tests_by_module.items() if not tests), key=str)
    if uncovered:
        rendered = ", ".join(uncovered)
        raise ScopeError(
            "targeted change set has changed production module(s) with no derived test "
            f"subset: {rendered}. Add a test importing each module or name-match a test "
            "file (test_<module>.py) so the leaf gate can certify the change."
        )

    test_paths = sorted(
        {path for path in changed if _within_any(path, test_roots)}
        | {path for tests in tests_by_module.values() for path in tests},
        key=lambda path: path.as_posix(),
    )
    coverage_paths = sorted(
        (path for path in changed if path in modules), key=lambda path: path.as_posix()
    )
    return TargetedScopeResult(
        base_revision=base_revision,
        changed_paths=tuple(changed),
        lint_paths=tuple(changed),
        type_paths=tuple(sorted(closure, key=lambda path: path.as_posix())),
        coverage_paths=tuple(coverage_paths),
        coverage_root_modules=_coverage_root_modules(tracked, import_roots),
        test_paths=tuple(test_paths),
        reverse_import_closure=tuple(
            sorted(closure - changed_set, key=lambda path: path.as_posix())
        ),
    )
