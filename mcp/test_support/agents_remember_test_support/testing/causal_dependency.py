"""Source-derived exact-node dependencies for causal prerequisite suppression."""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agents_remember_test_support.testing.dependency_facts import RepositoryDependencyFacts


class CausalDependencyError(RuntimeError):
    """An exact owner/node dependency population cannot be proved from source."""


@dataclass(frozen=True)
class CausalNodeDependency:
    """One exact test node and its shortest source-level chain to an owner symbol."""

    node_id: str
    dependency_chain: tuple[str, ...]


@dataclass(frozen=True)
class _Function:
    selector: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    class_name: str | None


@dataclass(frozen=True)
class _OwnerBindings:
    direct_names: frozenset[str]
    module_aliases: dict[str, str]
    owner_module: str
    owner_symbol: str


def derive_causal_nodes(
    project_root: Path,
    *,
    owner: Path,
    owner_module: str,
    owner_symbol: str,
) -> tuple[CausalNodeDependency, ...]:
    """Find only exact tests with a source-level call chain to the owner symbol."""

    root = project_root.resolve()
    relative_owner = owner.relative_to(root) if owner.is_absolute() else owner
    return _derive_causal_nodes(root, relative_owner, owner_module, owner_symbol)


@lru_cache(maxsize=4)
def _derive_causal_nodes(
    root: Path,
    owner: Path,
    owner_module: str,
    owner_symbol: str,
) -> tuple[CausalNodeDependency, ...]:
    """Derive once per immutable candidate process and bounded owner contract."""

    facts = RepositoryDependencyFacts.build(root)
    if facts.parse_error is not None:
        raise CausalDependencyError(facts.parse_error)
    if facts.ambiguous_modules:
        raise CausalDependencyError(f"ambiguous module owners: {sorted(facts.ambiguous_modules)}")
    if facts.modules.get(owner) != owner_module:
        raise CausalDependencyError(
            f"{owner_module!r} is not uniquely owned by {owner.as_posix()!r}"
        )
    _validate_owner_symbol(root / owner, owner_symbol)
    dependencies: list[CausalNodeDependency] = []
    for test_path in facts.tests:
        tree = _parse(root / test_path)
        bindings = _owner_bindings(tree, owner_module, owner_symbol)
        if not bindings.direct_names and not bindings.module_aliases:
            continue
        functions = _functions(tree)
        for function in functions.values():
            if not _is_test(function):
                continue
            chain = _shortest_owner_chain(function, functions, bindings)
            if chain is None:
                continue
            node_id = f"{test_path.as_posix()}::{function.selector}"
            owner_ref = f"{owner.as_posix()}::{owner_symbol}"
            helper_refs = tuple(
                f"{test_path.as_posix()}::{selector}" for selector in reversed(chain[1:])
            )
            dependencies.append(
                CausalNodeDependency(
                    node_id=node_id,
                    dependency_chain=(owner_ref, *helper_refs, node_id),
                )
            )
    return tuple(sorted(dependencies, key=lambda item: item.node_id))


def _validate_owner_symbol(path: Path, symbol: str) -> None:
    tree = _parse(path)
    matches = [
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and item.name == symbol
    ]
    if len(matches) != 1:
        raise CausalDependencyError(
            f"owner symbol {path.as_posix()}::{symbol} does not resolve exactly once"
        )


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    except (OSError, SyntaxError, UnicodeError) as error:
        raise CausalDependencyError(
            f"cannot parse causal dependency source {path}: {error}"
        ) from error


def _owner_bindings(
    tree: ast.Module,
    owner_module: str,
    owner_symbol: str,
) -> _OwnerBindings:
    direct: set[str] = set()
    modules: dict[str, str] = {}
    for node in tree.body:
        direct.update(_direct_owner_names(node, owner_module, owner_symbol))
        modules.update(_owner_module_aliases(node, owner_module))
    return _OwnerBindings(frozenset(direct), modules, owner_module, owner_symbol)


def _direct_owner_names(
    node: ast.stmt,
    owner_module: str,
    owner_symbol: str,
) -> set[str]:
    if not isinstance(node, ast.ImportFrom) or node.level != 0:
        return set()
    if node.module != owner_module:
        return set()
    return {alias.asname or alias.name for alias in node.names if alias.name == owner_symbol}


def _owner_module_aliases(node: ast.stmt, owner_module: str) -> dict[str, str]:
    if isinstance(node, ast.Import):
        return {
            alias.asname or alias.name.split(".", maxsplit=1)[0]: (
                alias.name if alias.asname else alias.name.split(".", maxsplit=1)[0]
            )
            for alias in node.names
            if alias.name == owner_module
        }
    if not isinstance(node, ast.ImportFrom) or node.level != 0:
        return {}
    parent, _, leaf = owner_module.rpartition(".")
    if node.module != parent:
        return {}
    return {alias.asname or alias.name: owner_module for alias in node.names if alias.name == leaf}


def _functions(tree: ast.Module) -> dict[str, _Function]:
    result: dict[str, _Function] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = _Function(node.name, node, None)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    selector = f"{node.name}::{child.name}"
                    result[selector] = _Function(selector, child, node.name)
    return result


def _is_test(function: _Function) -> bool:
    return function.node.name.startswith("test_")


def _shortest_owner_chain(
    root: _Function,
    functions: dict[str, _Function],
    bindings: _OwnerBindings,
) -> tuple[str, ...] | None:
    fixtures = _module_fixture_roots(root, functions)
    pending: deque[tuple[str, tuple[str, ...]]] = deque([(root.selector, (root.selector,))])
    pending.extend((value, (root.selector, value)) for value in fixtures)
    seen: set[str] = set()
    while pending:
        selector, chain = pending.popleft()
        if selector in seen:
            continue
        seen.add(selector)
        function = functions[selector]
        if _calls_owner(function.node, bindings):
            return chain
        for called in _called_local(function, functions):
            if called not in seen:
                pending.append((called, (*chain, called)))
    return None


def _module_fixture_roots(
    function: _Function,
    functions: dict[str, _Function],
) -> tuple[str, ...]:
    names = {
        argument.arg
        for argument in (
            *function.node.args.posonlyargs,
            *function.node.args.args,
            *function.node.args.kwonlyargs,
        )
    }
    fixtures = {
        candidate.selector
        for candidate in functions.values()
        if (candidate.class_name is None or candidate.class_name == function.class_name)
        and _is_fixture(candidate.node)
        and (candidate.node.name in names or _is_autouse_fixture(candidate.node))
    }
    return tuple(sorted(fixtures))


def _calls_owner(node: ast.AST, bindings: _OwnerBindings) -> bool:
    target = f"{bindings.owner_module}.{bindings.owner_symbol}"
    for call in _direct_calls(node):
        if isinstance(call.func, ast.Name) and call.func.id in bindings.direct_names:
            return True
        qualified = _qualified_name(call.func)
        if not qualified:
            continue
        root, _, suffix = qualified.partition(".")
        if root in bindings.direct_names:
            return True
        module = bindings.module_aliases.get(root)
        if module is not None and f"{module}.{suffix}".rstrip(".") == target:
            return True
    return False


def _called_local(
    function: _Function,
    functions: dict[str, _Function],
) -> tuple[str, ...]:
    called: set[str] = set()
    for call in _direct_calls(function.node):
        if isinstance(call.func, ast.Name) and call.func.id in functions:
            called.add(call.func.id)
        elif (
            function.class_name is not None
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in {"self", "cls"}
        ):
            selector = f"{function.class_name}::{call.func.attr}"
            if selector in functions:
                called.add(selector)
    return tuple(sorted(called))


def _direct_calls(node: ast.AST) -> tuple[ast.Call, ...]:
    """Calls executed by this function body, excluding deferred nested definitions."""

    calls: list[ast.Call] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, candidate: ast.Call) -> None:
            calls.append(candidate)
            self.generic_visit(candidate)

        def visit_FunctionDef(self, candidate: ast.FunctionDef) -> None:
            if candidate is node:
                self.generic_visit(candidate)

        def visit_AsyncFunctionDef(self, candidate: ast.AsyncFunctionDef) -> None:
            if candidate is node:
                self.generic_visit(candidate)

        def visit_Lambda(self, candidate: ast.Lambda) -> None:
            del candidate

        def visit_ClassDef(self, candidate: ast.ClassDef) -> None:
            del candidate

    Visitor().visit(node)
    return tuple(calls)


def _qualified_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return _qualified_name(node)


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_decorator_name(value).endswith("fixture") for value in node.decorator_list)


def _is_autouse_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and _decorator_name(decorator).endswith("fixture")
        and any(
            keyword.arg == "autouse"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        )
        for decorator in node.decorator_list
    )
