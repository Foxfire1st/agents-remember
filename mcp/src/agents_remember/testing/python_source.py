"""Candidate-owned Python source graph and executed-syntax primitives."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.testing.selection_contract import (
    ClosureRefusal,
    DependencyObservation,
    DirectRefusalCode,
)


@dataclass(frozen=True)
class ImportBinding:
    module: str
    symbol: str | None = None

    @property
    def qualified(self) -> str:
        return self.module if self.symbol is None else f"{self.module}.{self.symbol}"


@dataclass
class SourceModule:
    relative: Path
    name: str
    source: str
    tree: ast.Module
    imports: dict[str, ImportBinding] = field(default_factory=dict)
    functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = field(default_factory=dict)
    classes: dict[str, list[ast.ClassDef]] = field(default_factory=dict)


@dataclass
class ExecutableFacts:
    calls: list[ast.Call] = field(default_factory=list)
    imports: list[ast.Import | ast.ImportFrom] = field(default_factory=list)
    mutations: list[ast.AST] = field(default_factory=list)
    dynamic_nodes: list[ast.AST] = field(default_factory=list)


class ExecutableVisitor(ast.NodeVisitor):
    """Collect executed syntax while excluding nested declarations."""

    def __init__(self) -> None:
        self.facts = ExecutableFacts()

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        self.facts.calls.append(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.facts.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.facts.imports.append(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.facts.mutations.append(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.facts.mutations.append(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Attribute | ast.Subscript):
            self.facts.mutations.append(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Attribute | ast.Subscript):
            self.facts.mutations.append(node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(isinstance(target, ast.Attribute | ast.Subscript) for target in node.targets):
            self.facts.mutations.append(node)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        self.facts.mutations.append(node)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.facts.dynamic_nodes.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.facts.dynamic_nodes.append(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.facts.dynamic_nodes.append(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.facts.dynamic_nodes.append(node)


class CandidatePythonGraph:
    """Resolve and parse candidate-owned modules without importing them."""

    def __init__(self, candidate_root: Path) -> None:
        self.root = candidate_root
        self.modules: dict[Path, SourceModule] = {}
        self.closure_paths: set[str] = set()

    def load_module(self, relative: Path) -> SourceModule | ClosureRefusal:
        if relative in self.modules:
            return self.modules[relative]
        path = self.root / relative
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as error:
            observation = DependencyObservation(
                relative.as_posix(),
                1,
                relative.as_posix(),
                f"Python dependency cannot be parsed: {error}",
            )
            return ClosureRefusal(
                DirectRefusalCode.UNRESOLVED_DEPENDENCY,
                observation.detail,
                observation,
            )
        module = SourceModule(
            relative=relative,
            name=self.module_name(relative),
            source=source,
            tree=tree,
        )
        for statement in tree.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                module.functions.setdefault(statement.name, []).append(statement)
            elif isinstance(statement, ast.ClassDef):
                module.classes.setdefault(statement.name, []).append(statement)
        self.modules[relative] = module
        self.closure_paths.add(relative.as_posix())
        return module

    def local_module_path(self, module: str) -> Path | None:
        candidates: list[Path] = []
        if module == "agents_remember" or module.startswith("agents_remember."):
            candidates.append(Path("mcp/src") / Path(*module.split(".")))
        elif module and "." not in module:
            candidates.append(Path("mcp/tests") / module)
        for base in candidates:
            file_candidate = base.with_suffix(".py")
            if (self.root / file_candidate).is_file():
                return file_candidate
            package_candidate = base / "__init__.py"
            if (self.root / package_candidate).is_file():
                return package_candidate
        return None

    def import_bindings(
        self,
        module: SourceModule,
        node: ast.Import | ast.ImportFrom,
    ) -> list[tuple[str, ImportBinding]]:
        if isinstance(node, ast.Import):
            return [
                (alias.asname or alias.name.split(".")[0], ImportBinding(alias.name))
                for alias in node.names
            ]
        imported_module = self.absolute_import_module(module, node)
        bindings: list[tuple[str, ImportBinding]] = []
        for alias in node.names:
            local_submodule = f"{imported_module}.{alias.name}" if imported_module else alias.name
            binding = (
                ImportBinding(local_submodule)
                if self.local_module_path(local_submodule) is not None
                else ImportBinding(imported_module, alias.name)
            )
            bindings.append((alias.asname or alias.name, binding))
        return bindings

    @staticmethod
    def absolute_import_module(module: SourceModule, node: ast.ImportFrom) -> str:
        if node.level == 0:
            return node.module or ""
        parts = module.name.split(".")
        if module.relative.name != "__init__.py":
            parts = parts[:-1]
        keep = max(len(parts) - (node.level - 1), 0)
        suffix = node.module.split(".") if node.module else []
        return ".".join((*parts[:keep], *suffix))

    @staticmethod
    def module_name(relative: Path) -> str:
        if relative.is_relative_to(Path("mcp/src")):
            path = relative.relative_to("mcp/src")
        elif relative.is_relative_to(Path("mcp/tests")):
            path = relative.relative_to("mcp/tests")
        else:
            path = relative
        parts = list(path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)


def qualified_name(node: ast.expr, bindings: dict[str, ImportBinding]) -> str:
    if isinstance(node, ast.Name):
        binding = bindings.get(node.id)
        return binding.qualified if binding is not None else node.id
    if isinstance(node, ast.Attribute):
        prefix = qualified_name(node.value, bindings)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def root_name(node: ast.Attribute) -> str | None:
    value: ast.expr = node
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


def unique_named_function(
    statements: list[ast.stmt],
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    candidates = [
        item
        for item in statements
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name
    ]
    return candidates[0] if len(candidates) == 1 else None


def decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def is_fixture(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(decorator_name(item).endswith("fixture") for item in function.decorator_list)


def is_autouse_fixture(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in function.decorator_list:
        if not isinstance(decorator, ast.Call) or not decorator_name(decorator.func).endswith(
            "fixture"
        ):
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "autouse" and isinstance(keyword.value, ast.Constant):
                return keyword.value.value is True
    return False


def is_docstring(statement: ast.Expr) -> bool:
    return isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)


def is_type_checking_guard(test: ast.expr) -> bool:
    return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"


def has_effectful_target(statement: ast.Assign | ast.AnnAssign) -> bool:
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return any(isinstance(target, ast.Attribute | ast.Subscript) for target in targets)
