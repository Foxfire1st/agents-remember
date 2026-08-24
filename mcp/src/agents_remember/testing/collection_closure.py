"""Collection-time statement routing for the direct-test closure."""

from __future__ import annotations

import ast
from collections.abc import Callable

from agents_remember.testing.python_source import (
    ImportBinding,
    SourceModule,
    has_effectful_target,
    is_docstring,
    is_type_checking_guard,
)
from agents_remember.testing.selection_contract import (
    ClosureRefusal,
    UnsafeEffectFamily,
)

ImportAnalyzer = Callable[
    [SourceModule, ast.Import | ast.ImportFrom, dict[str, ImportBinding]],
    ClosureRefusal | None,
]
ExpressionAnalyzer = Callable[
    [SourceModule, ast.expr, dict[str, ImportBinding]],
    ClosureRefusal | None,
]
UnsupportedBuilder = Callable[[SourceModule, ast.AST, str], ClosureRefusal]
UnsafeBuilder = Callable[
    [SourceModule, ast.AST, str, UnsafeEffectFamily],
    ClosureRefusal,
]


class CollectionClosure:
    """Route known collection syntax and fail closed on every other statement."""

    def __init__(
        self,
        analyze_import: ImportAnalyzer,
        analyze_expression: ExpressionAnalyzer,
        unsupported: UnsupportedBuilder,
        unsafe: UnsafeBuilder,
    ) -> None:
        self._analyze_import = analyze_import
        self._analyze_expression = analyze_expression
        self._unsupported = unsupported
        self._unsafe = unsafe
        self._handlers = {
            ast.Import: self._import,
            ast.ImportFrom: self._import,
            ast.FunctionDef: self._function,
            ast.AsyncFunctionDef: self._function,
            ast.ClassDef: self._class,
            ast.Expr: self._expression,
            ast.Assign: self._assignment,
            ast.AnnAssign: self._assignment,
            ast.If: self._if,
            ast.Pass: self._pass,
        }

    def analyze(self, module: SourceModule, statement: ast.stmt) -> ClosureRefusal | None:
        handler = self._handlers.get(type(statement))
        if handler is None:
            return self._unsupported(module, statement, type(statement).__name__)
        return handler(module, statement)

    def _import(self, module: SourceModule, statement: ast.stmt) -> ClosureRefusal | None:
        assert isinstance(statement, ast.Import | ast.ImportFrom)
        return self._analyze_import(module, statement, module.imports)

    def _function(self, module: SourceModule, statement: ast.stmt) -> ClosureRefusal | None:
        assert isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
        return self._expressions(
            module,
            (
                *statement.decorator_list,
                *statement.args.defaults,
                *(item for item in statement.args.kw_defaults if item is not None),
            ),
        )

    def _class(self, module: SourceModule, statement: ast.stmt) -> ClosureRefusal | None:
        assert isinstance(statement, ast.ClassDef)
        if refusal := self._expressions(
            module,
            (*statement.bases, *statement.decorator_list),
        ):
            return refusal
        for child in statement.body:
            if refusal := self._class_statement(module, child):
                return refusal
        return None

    def _class_statement(
        self,
        module: SourceModule,
        statement: ast.stmt,
    ) -> ClosureRefusal | None:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            return self._function(module, statement)
        if isinstance(statement, ast.Pass) or (
            isinstance(statement, ast.Expr) and is_docstring(statement)
        ):
            return None
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            return self._assignment(module, statement)
        return self._unsupported(module, statement, "executable class body")

    def _expression(self, module: SourceModule, statement: ast.stmt) -> ClosureRefusal | None:
        assert isinstance(statement, ast.Expr)
        if is_docstring(statement):
            return None
        return self._analyze_expression(module, statement.value, module.imports)

    def _assignment(self, module: SourceModule, statement: ast.stmt) -> ClosureRefusal | None:
        assert isinstance(statement, ast.Assign | ast.AnnAssign)
        value = statement.value
        if value is not None and (
            refusal := self._analyze_expression(module, value, module.imports)
        ):
            return refusal
        if has_effectful_target(statement):
            return self._unsafe(
                module,
                statement,
                "module-level assignment mutates attribute or subscript state",
                UnsafeEffectFamily.MUTABLE_GLOBAL_STATE,
            )
        return None

    def _if(self, module: SourceModule, statement: ast.stmt) -> ClosureRefusal | None:
        assert isinstance(statement, ast.If)
        if is_type_checking_guard(statement.test):
            return None
        return self._unsupported(module, statement, "runtime conditional")

    @staticmethod
    def _pass(module: SourceModule, statement: ast.stmt) -> None:
        del module, statement

    def _expressions(
        self,
        module: SourceModule,
        expressions: tuple[ast.expr, ...],
    ) -> ClosureRefusal | None:
        for expression in expressions:
            if refusal := self._analyze_expression(module, expression, module.imports):
                return refusal
        return None
