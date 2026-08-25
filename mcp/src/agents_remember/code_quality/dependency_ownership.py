"""Canonical test-consumer ownership for targeted selection and retry proof."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agents_remember.code_quality.scope import (
    ScopeError,
    git_ls_files,
    pytest_testpaths,
    top_level_packages,
)
from agents_remember.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    load_evidence_inventory,
)

GLOBAL_TEST_INPUTS = frozenset(
    {
        Path("pyproject.toml"),
        Path("mcp/tests/evidence-lifecycle.toml"),
    }
)


class SelectionReasonKind(StrEnum):
    """Stable reason vocabulary shared by reports and retry decisions."""

    CHANGED_TEST = "changed-test"
    IMPORT_CONSUMER = "import-consumer"
    DECLARED_CONSUMER = "declared-consumer"
    NAME_HEURISTIC = "name-heuristic"
    TEXT_HEURISTIC = "text-heuristic"
    PYTEST_GLOBAL = "pytest-global"
    SAFE_FULL = "safe-full"


@dataclass(frozen=True, order=True)
class SelectionReason:
    kind: SelectionReasonKind
    source: Path
    detail: str

    def render(self) -> str:
        return f"{self.kind.value}:{self.source.as_posix()}:{self.detail}"


@dataclass(frozen=True)
class OwnedTest:
    path: Path
    reasons: tuple[SelectionReason, ...]


@dataclass(frozen=True)
class TestImpact:
    """Affected test population and whether partial reuse is sound."""

    tests: tuple[Path, ...]
    ownership: tuple[OwnedTest, ...]
    complete: bool
    global_invalidation: bool
    fallback: SelectionReason | None = None

    def reasons_for(self, path: Path) -> tuple[SelectionReason, ...]:
        return next((item.reasons for item in self.ownership if item.path == path), ())


def import_roots_for(
    tracked_python: Sequence[Path], test_roots: Sequence[Path]
) -> tuple[Path, ...]:
    """Import roots for product packages plus non-package test support modules."""

    roots = {package.parent for package in top_level_packages(list(tracked_python))}
    roots.update(test_roots)
    return tuple(sorted(roots, key=lambda path: (-len(path.parts), path.as_posix())))


def module_for_path(path: Path, import_roots: Sequence[Path]) -> str | None:
    """Dotted module name under the nearest configured import root."""

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
        return ".".join(parts) or None
    return None


def dotted_ancestors(module: str) -> tuple[str, ...]:
    parts = module.split(".")
    return tuple(".".join(parts[:index]) for index in range(len(parts), 0, -1))


def resolve_relative_import(root_module: str, level: int, name: str | None) -> str | None:
    parts = root_module.split(".")
    if level > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    return ".".join(base if name is None else [*base, name])


def import_from_modules(node: ast.ImportFrom, root_module: str | None) -> set[str]:
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
    """Resolve imports, including string-declared pytest plugins in conftest."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise ScopeError(f"test ownership could not parse {path}: {error}") from error
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.update(dotted_ancestors(alias.name))
        elif isinstance(node, ast.ImportFrom):
            imports.update(import_from_modules(node, root_module))
    if path.name == "conftest.py":
        imports.update(_pytest_plugin_imports(tree))
    return imports


def _pytest_plugin_imports(tree: ast.AST) -> set[str]:
    plugins: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins" for target in targets
        ):
            continue
        value = node.value
        if value is None:
            continue
        for candidate in ast.walk(value):
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                plugins.update(dotted_ancestors(candidate.value))
    return plugins


def name_match_tests(test_roots: Sequence[Path], module: str, tracked: Sequence[Path]) -> set[Path]:
    """Test files matching a suffix of a dotted module identity."""

    parts = module.split(".")
    candidates = {f"test_{'_'.join(parts[-tail:])}.py" for tail in range(1, len(parts) + 1)}
    return {path for path in tracked if path.name in candidates and within_any(path, test_roots)}


def within_any(path: Path, roots: Sequence[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def is_test_module(path: Path) -> bool:
    return path.suffix == ".py" and (
        path.name.startswith("test_") or path.name.endswith("_test.py")
    )


class DependencyOwnershipGraph:
    """One immutable consumer graph for production, support, plugins, and fixtures."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.tracked = tuple(sorted(git_ls_files(self.project_root), key=Path.as_posix))
        self.tracked_set = frozenset(self.tracked)
        self.test_roots = tuple(pytest_testpaths(self.project_root))
        self.python_paths = tuple(
            path
            for path in self.tracked
            if path.suffix == ".py" and (self.project_root / path).is_file()
        )
        self.tests = tuple(
            path
            for path in self.python_paths
            if is_test_module(path) and within_any(path, self.test_roots)
        )
        self.product_import_roots = import_roots_for(self.python_paths, ())
        self.import_roots = import_roots_for(self.python_paths, self.test_roots)
        self.product_modules, _product_ambiguity = _module_index(
            self.python_paths,
            self.product_import_roots,
        )
        self.modules, self.ambiguous_modules = _module_index(self.python_paths, self.import_roots)
        self.importers, self.parse_error = _importer_index(
            self.project_root,
            self.python_paths,
            self.modules,
        )
        self.declared_consumers, self.catalog_error = _declared_consumers(self.project_root)

    def resolve(self, changed: Sequence[Path]) -> TestImpact:
        """Resolve affected tests or select all with an explicit incomplete-ownership reason."""

        changed_paths = tuple(sorted(set(changed), key=Path.as_posix))
        if not changed_paths:
            return TestImpact((), (), True, False)
        if refusal := self._graph_refusal(changed_paths[0]):
            return self._safe_full(refusal.source, refusal.detail)
        return self._resolved_impact(changed_paths)

    def _resolved_impact(self, changed_paths: Sequence[Path]) -> TestImpact:
        reasons: dict[Path, set[SelectionReason]] = {}
        global_sources: list[SelectionReason] = []
        for path in changed_paths:
            decision = self._resolve_one(path)
            if isinstance(decision, SelectionReason):
                if decision.kind is SelectionReasonKind.SAFE_FULL:
                    return self._safe_full(path, decision.detail)
                global_sources.append(decision)
                continue
            for test, owned_reasons in decision.items():
                reasons.setdefault(test, set()).update(owned_reasons)
        if global_sources:
            for test in self.tests:
                reasons.setdefault(test, set()).update(global_sources)
        ownership = tuple(
            OwnedTest(path, tuple(sorted(owned_reasons)))
            for path, owned_reasons in sorted(reasons.items(), key=lambda item: item[0].as_posix())
        )
        return TestImpact(
            tests=tuple(item.path for item in ownership),
            ownership=ownership,
            complete=True,
            global_invalidation=bool(global_sources),
        )

    def _graph_refusal(self, source: Path) -> SelectionReason | None:
        if self.catalog_error is not None:
            detail = f"lifecycle-catalog-invalid:{self.catalog_error}"
        elif self.parse_error is not None:
            detail = f"import-graph-invalid:{self.parse_error}"
        elif self.ambiguous_modules:
            modules = ",".join(sorted(self.ambiguous_modules))
            detail = f"ambiguous-module-identity:{modules}"
        else:
            return None
        return SelectionReason(SelectionReasonKind.SAFE_FULL, source, detail)

    def _resolve_one(
        self,
        path: Path,
    ) -> dict[Path, set[SelectionReason]] | SelectionReason:
        global_reason = self._global_reason(path)
        if global_reason is not None:
            return global_reason
        if within_any(path, self.test_roots):
            return self._test_tree_consumers(path)
        return self._repository_consumers(path)

    def _global_reason(self, path: Path) -> SelectionReason | None:
        if path in GLOBAL_TEST_INPUTS:
            return SelectionReason(SelectionReasonKind.PYTEST_GLOBAL, path, "global-test-config")
        if path.name == "conftest.py" and within_any(path, self.test_roots):
            return SelectionReason(SelectionReasonKind.PYTEST_GLOBAL, path, "conftest-plugin-root")
        return None

    def _repository_consumers(
        self,
        path: Path,
    ) -> dict[Path, set[SelectionReason]] | SelectionReason:
        declared = self.declared_consumers.get(path)
        if declared is not None:
            return self._owned(declared, SelectionReasonKind.DECLARED_CONSUMER, path, "catalog")
        if path.suffix == ".py":
            return self._python_consumers(path)
        if _irrelevant_to_python_tests(path):
            return {}
        return SelectionReason(SelectionReasonKind.SAFE_FULL, path, "unowned-test-input")

    def _test_tree_consumers(
        self,
        path: Path,
    ) -> dict[Path, set[SelectionReason]] | SelectionReason:
        if is_test_module(path):
            if path not in self.tracked_set or not (self.project_root / path).is_file():
                return SelectionReason(SelectionReasonKind.SAFE_FULL, path, "deleted-test-module")
            return self._owned({path}, SelectionReasonKind.CHANGED_TEST, path, "self")
        consumers = set(self.declared_consumers.get(path, ()))
        module = module_for_path(path, self.import_roots) if path.suffix == ".py" else None
        if module is not None:
            consumers.update(self._importing_tests(module))
        if not consumers:
            return SelectionReason(SelectionReasonKind.SAFE_FULL, path, "unowned-test-support")
        owned = self._owned(
            self.declared_consumers.get(path, ()),
            SelectionReasonKind.DECLARED_CONSUMER,
            path,
            "catalog",
        )
        if module is not None:
            _merge_owned(
                owned,
                self._owned(
                    self._importing_tests(module),
                    SelectionReasonKind.IMPORT_CONSUMER,
                    path,
                    module,
                ),
            )
        return owned

    def _python_consumers(
        self,
        path: Path,
    ) -> dict[Path, set[SelectionReason]] | SelectionReason:
        module = self.modules.get(path) or module_for_path(path, self.import_roots)
        owned: dict[Path, set[SelectionReason]] = {}
        if module is not None:
            _merge_owned(
                owned,
                self._owned(
                    self._importing_tests(module),
                    SelectionReasonKind.IMPORT_CONSUMER,
                    path,
                    module,
                ),
            )
            _merge_owned(
                owned,
                self._owned(
                    name_match_tests(self.test_roots, module, self.tests),
                    SelectionReasonKind.NAME_HEURISTIC,
                    path,
                    module,
                ),
            )
        text_tokens = [path.as_posix()]
        if module is not None:
            text_tokens.append(module)
        _merge_owned(
            owned,
            self._owned(
                self._text_reference_tests(text_tokens),
                SelectionReasonKind.TEXT_HEURISTIC,
                path,
                "|".join(text_tokens),
            ),
        )
        if not owned:
            return SelectionReason(SelectionReasonKind.SAFE_FULL, path, "unowned-python-change")
        return owned

    def _importing_tests(self, module: str) -> set[Path]:
        importers = transitive_importers(module, self.modules, self.importers)
        if any(path.name == "conftest.py" for path in importers):
            return set(self.tests)
        return {path for path in importers if path in self.tests}

    def _text_reference_tests(self, tokens: Iterable[str]) -> set[Path]:
        patterns = [re.compile(rf"(?<![\w.]){re.escape(token)}(?![\w.])") for token in tokens]
        matches: set[Path] = set()
        for path in self.tests:
            try:
                text = (self.project_root / path).read_text(encoding="utf-8")
            except OSError as error:
                raise ScopeError(f"test ownership could not read {path}: {error}") from error
            if any(pattern.search(text) for pattern in patterns):
                matches.add(path)
        return matches

    @staticmethod
    def _owned(
        consumers: Iterable[Path],
        kind: SelectionReasonKind,
        source: Path,
        detail: str,
    ) -> dict[Path, set[SelectionReason]]:
        reason = SelectionReason(kind, source, detail)
        return {path: {reason} for path in consumers}

    def _safe_full(self, source: Path, detail: str) -> TestImpact:
        fallback = SelectionReason(SelectionReasonKind.SAFE_FULL, source, detail)
        ownership = tuple(OwnedTest(path, (fallback,)) for path in self.tests)
        return TestImpact(
            tests=self.tests,
            ownership=ownership,
            complete=False,
            global_invalidation=True,
            fallback=fallback,
        )


def transitive_importers(
    module: str,
    modules: dict[Path, str],
    importers: dict[str, set[Path]],
) -> set[Path]:
    """Cycle-safe reverse import closure."""

    reachable: set[Path] = set()
    seen_modules = {module}
    queue = [module]
    while queue:
        current = queue.pop()
        for importer in importers.get(current, ()):
            if importer in reachable:
                continue
            reachable.add(importer)
            importer_module = modules.get(importer)
            if importer_module is not None and importer_module not in seen_modules:
                seen_modules.add(importer_module)
                queue.append(importer_module)
    return reachable


def reverse_import_closure(
    changed: set[Path],
    changed_modules: Sequence[str],
    modules: dict[Path, str],
    importers: dict[str, set[Path]],
) -> set[Path]:
    closure = set(changed)
    for module in changed_modules:
        closure.update(transitive_importers(module, modules, importers))
    return closure


def coverage_root_modules(tracked: Sequence[Path], import_roots: Sequence[Path]) -> tuple[str, ...]:
    root_modules = {
        module
        for package in top_level_packages(list(tracked))
        if (module := module_for_path(package, import_roots)) is not None
    }
    return tuple(sorted(root_modules))


def _module_index(
    tracked: Sequence[Path],
    import_roots: Sequence[Path],
) -> tuple[dict[Path, str], frozenset[str]]:
    modules: dict[Path, str] = {}
    owners: dict[str, list[Path]] = {}
    for path in tracked:
        module = module_for_path(path, import_roots)
        if module is None:
            continue
        modules[path] = module
        owners.setdefault(module, []).append(path)
    ambiguous = frozenset(module for module, paths in owners.items() if len(paths) > 1)
    return modules, ambiguous


def _importer_index(
    project_root: Path,
    tracked: Sequence[Path],
    modules: dict[Path, str],
) -> tuple[dict[str, set[Path]], str | None]:
    importers: dict[str, set[Path]] = {}
    try:
        for path in tracked:
            for module in file_imports(project_root / path, modules.get(path)):
                importers.setdefault(module, set()).add(path)
    except ScopeError as error:
        return {}, str(error)
    return importers, None


def _declared_consumers(project_root: Path) -> tuple[dict[Path, set[Path]], str | None]:
    try:
        inventory = load_evidence_inventory(project_root)
    except EvidenceLifecycleError as error:
        return {}, str(error)
    declared: dict[Path, set[Path]] = {}
    for artifact in inventory.artifacts:
        declared.setdefault(Path(artifact.path), set()).update(
            Path(path) for path in artifact.consumers
        )
    return declared, None


def _merge_owned(
    target: dict[Path, set[SelectionReason]],
    source: dict[Path, set[SelectionReason]],
) -> None:
    for path, reasons in source.items():
        target.setdefault(path, set()).update(reasons)


def _irrelevant_to_python_tests(path: Path) -> bool:
    return path.parts[:1] in {("docs",), ("notes",), ("dashboard",)} or path.suffix in {
        ".md",
        ".txt",
        ".rst",
    }
