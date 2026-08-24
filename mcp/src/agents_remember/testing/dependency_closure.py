"""Fail-closed static dependency/effect closure for direct pytest diagnostics."""

from __future__ import annotations

import ast
from pathlib import Path

from agents_remember.testing.collection_closure import CollectionClosure
from agents_remember.testing.python_source import (
    CandidatePythonGraph,
    ExecutableVisitor,
    ImportBinding,
    SourceModule,
    decorator_name,
    is_autouse_fixture,
    is_fixture,
    qualified_name,
    root_name,
    unique_named_function,
)
from agents_remember.testing.selection_contract import (
    ClosureRefusal,
    DependencyObservation,
    DirectRefusalCode,
    ResolvedDependencyClosure,
    ResolvedTestTarget,
    UnsafeEffectFamily,
)
from agents_remember.testing.unsafe_effects import (
    DYNAMIC_CALLS,
    UNSAFE_QUALIFIED_CALLS,
    is_allowed_external_import,
    is_safe_call,
    unsafe_family_reason,
    unsafe_import_family,
)


class DependencyClosureAnalyzer:
    """Resolve one exact request through imports, helpers, fixtures, and effects."""

    def __init__(self, candidate_root: Path) -> None:
        self._root = candidate_root
        self._graph = CandidatePythonGraph(candidate_root)
        self._import_scanned: set[Path] = set()
        self._call_scanned: set[tuple[Path, str]] = set()
        self._observations: list[DependencyObservation] = []
        self._collection = CollectionClosure(
            self._analyze_import,
            self._analyze_expression,
            self._unsupported_collection,
            self._unsafe,
        )

    def analyze(
        self,
        targets: tuple[ResolvedTestTarget, ...],
    ) -> ResolvedDependencyClosure | ClosureRefusal:
        """Return a complete closure or the first stable refusal in source order."""

        for target in targets:
            module = self._graph.load_module(target.path)
            if isinstance(module, ClosureRefusal):
                return module
            if refusal := self._analyze_module_import_time(module):
                return refusal
            if refusal := self._analyze_selected_target(module, target):
                return refusal
        return ResolvedDependencyClosure(
            paths=tuple(sorted(self._graph.closure_paths)),
            observations=tuple(self._observations),
        )

    def _analyze_module_import_time(self, module: SourceModule) -> ClosureRefusal | None:
        if module.relative in self._import_scanned:
            return None
        self._import_scanned.add(module.relative)
        for statement in module.tree.body:
            refusal = self._collection.analyze(module, statement)
            if refusal is not None:
                return refusal
        return None

    def _analyze_selected_target(
        self,
        module: SourceModule,
        target: ResolvedTestTarget,
    ) -> ClosureRefusal | None:
        function = self._exact_function(module, target)
        if function is None:
            return self._refusal(
                DirectRefusalCode.TARGET_AMBIGUOUS,
                module.relative,
                target.line,
                target.function_name,
                "resolved node changed while building its dependency closure",
            )
        if isinstance(function, ast.AsyncFunctionDef):
            return self._refusal(
                DirectRefusalCode.UNSUPPORTED_TARGET,
                module.relative,
                function.lineno,
                function.name,
                "async pytest nodes are outside the first bounded direct cohort",
            )
        if target.class_name is not None and (
            refusal := self._analyze_class_lifecycle(module, target)
        ):
            return refusal
        if refusal := self._analyze_autouse_fixtures(module):
            return refusal
        if refusal := self._analyze_fixture_parameters(module, function, target.class_name):
            return refusal
        return self._analyze_function(module, function, fixture=False)

    def _analyze_class_lifecycle(
        self,
        module: SourceModule,
        target: ResolvedTestTarget,
    ) -> ClosureRefusal | None:
        assert target.class_name is not None
        class_node = self._exact_class(module, target.class_name)
        if class_node is None:
            return self._refusal(
                DirectRefusalCode.TARGET_AMBIGUOUS,
                module.relative,
                target.line,
                target.class_name,
                "test class did not resolve uniquely",
            )
        for lifecycle_name in ("setUpClass", "setUp", "tearDown", "tearDownClass"):
            lifecycle = unique_named_function(class_node.body, lifecycle_name)
            if lifecycle is not None and (
                refusal := self._analyze_function(module, lifecycle, fixture=False)
            ):
                return refusal
        return None

    def _analyze_autouse_fixtures(self, module: SourceModule) -> ClosureRefusal | None:
        for functions in module.functions.values():
            for function in functions:
                if is_autouse_fixture(function) and (
                    refusal := self._analyze_function(module, function, fixture=True)
                ):
                    return refusal
        return None

    def _analyze_fixture_parameters(
        self,
        module: SourceModule,
        function: ast.FunctionDef,
        class_name: str | None,
    ) -> ClosureRefusal | None:
        ignored = {"self", "cls"} if class_name is not None else set()
        names = [argument.arg for argument in (*function.args.posonlyargs, *function.args.args)]
        for name in names:
            if name in ignored:
                continue
            candidates = [item for item in module.functions.get(name, []) if is_fixture(item)]
            if len(candidates) != 1:
                return self._refusal(
                    DirectRefusalCode.UNSUPPORTED_FIXTURE,
                    module.relative,
                    function.lineno,
                    name,
                    "fixture dependency is absent, ambiguous, builtin, or conftest-owned",
                )
            if refusal := self._analyze_function(module, candidates[0], fixture=True):
                return refusal
        return None

    def _analyze_function(
        self,
        module: SourceModule,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        fixture: bool,
    ) -> ClosureRefusal | None:
        key = (module.relative, function.name)
        if key in self._call_scanned:
            return None
        self._call_scanned.add(key)
        if isinstance(function, ast.AsyncFunctionDef):
            return self._refusal(
                DirectRefusalCode.UNSUPPORTED_FIXTURE
                if fixture
                else DirectRefusalCode.UNRESOLVED_DEPENDENCY,
                module.relative,
                function.lineno,
                function.name,
                "async helper and fixture execution is outside the first direct cohort",
            )
        bindings = dict(module.imports)
        visitor = ExecutableVisitor()
        for statement in function.body:
            visitor.visit(statement)
        facts = visitor.facts
        refusal = self._function_facts_refusal(module, function, facts, bindings)
        if refusal is not None:
            return refusal
        self._observations.append(
            self._observation(
                module,
                function,
                function.name,
                "resolved helper/fixture body",
            )
        )
        return None

    def _function_facts_refusal(
        self,
        module: SourceModule,
        function: ast.FunctionDef,
        facts,
        bindings: dict[str, ImportBinding],
    ) -> ClosureRefusal | None:
        if facts.dynamic_nodes:
            node = facts.dynamic_nodes[0]
            return self._refusal(
                DirectRefusalCode.DYNAMIC_DEPENDENCY,
                module.relative,
                getattr(node, "lineno", function.lineno),
                function.name,
                "nested dynamic declarations cannot be resolved safely",
            )
        if facts.mutations:
            return self._unsafe(
                module,
                facts.mutations[0],
                "helper mutates attribute, subscript, global, nonlocal, or deleted state",
                UnsafeEffectFamily.MUTABLE_GLOBAL_STATE,
            )
        for imported in facts.imports:
            if refusal := self._analyze_import(module, imported, bindings):
                return refusal
        for call in facts.calls:
            if refusal := self._analyze_call(module, call, bindings):
                return refusal
        return None

    def _analyze_expression(
        self,
        module: SourceModule,
        expression: ast.expr,
        bindings: dict[str, ImportBinding],
    ) -> ClosureRefusal | None:
        visitor = ExecutableVisitor()
        visitor.visit(expression)
        if visitor.facts.dynamic_nodes:
            node = visitor.facts.dynamic_nodes[0]
            return self._refusal(
                DirectRefusalCode.DYNAMIC_DEPENDENCY,
                module.relative,
                getattr(node, "lineno", 1),
                type(node).__name__,
                "dynamic collection expression cannot be resolved safely",
            )
        if visitor.facts.mutations:
            return self._unsafe(
                module,
                visitor.facts.mutations[0],
                "collection expression mutates global state",
                UnsafeEffectFamily.MUTABLE_GLOBAL_STATE,
            )
        for imported in visitor.facts.imports:
            if refusal := self._analyze_import(module, imported, bindings):
                return refusal
        for call in visitor.facts.calls:
            if refusal := self._analyze_call(module, call, bindings):
                return refusal
        return None

    def _analyze_import(
        self,
        module: SourceModule,
        node: ast.Import | ast.ImportFrom,
        bindings: dict[str, ImportBinding],
    ) -> ClosureRefusal | None:
        for alias, binding in self._graph.import_bindings(module, node):
            bindings[alias] = binding
            if family := unsafe_import_family(binding.module):
                return self._unsafe(
                    module,
                    node,
                    unsafe_family_reason(family),
                    family,
                    symbol=binding.qualified,
                )
            local = self._graph.local_module_path(binding.module)
            if local is None:
                if not is_allowed_external_import(binding.module):
                    return self._refusal(
                        DirectRefusalCode.UNRESOLVED_DEPENDENCY,
                        module.relative,
                        node.lineno,
                        binding.qualified,
                        "external import is not in the closed allowed import model",
                    )
                self._observations.append(
                    self._observation(
                        module,
                        node,
                        binding.qualified,
                        "known import-time-safe external dependency",
                    )
                )
                continue
            imported = self._graph.load_module(local)
            if isinstance(imported, ClosureRefusal):
                return imported
            if refusal := self._analyze_module_import_time(imported):
                return refusal
            self._observations.append(
                self._observation(
                    module,
                    node,
                    binding.qualified,
                    f"resolved local import to {local.as_posix()}",
                )
            )
        return None

    def _analyze_call(
        self,
        module: SourceModule,
        call: ast.Call,
        bindings: dict[str, ImportBinding],
    ) -> ClosureRefusal | None:
        qualified = qualified_name(call.func, bindings)
        if refusal := self._call_policy_refusal(module, call, qualified):
            return refusal
        method_name = call.func.attr if isinstance(call.func, ast.Attribute) else None
        if is_safe_call(qualified, method_name=method_name):
            return None
        if isinstance(call.func, ast.Name):
            handled, result = self._named_call(module, call, bindings)
            if handled:
                return result
        if isinstance(call.func, ast.Attribute):
            handled, result = self._attribute_call(module, call, bindings)
            if handled:
                return result
        return self._refusal(
            DirectRefusalCode.UNRESOLVED_DEPENDENCY,
            module.relative,
            call.lineno,
            qualified or ast.unparse(call.func),
            "call target is outside the closed positive effect model",
        )

    def _call_policy_refusal(
        self,
        module: SourceModule,
        call: ast.Call,
        qualified: str,
    ) -> ClosureRefusal | None:
        if qualified in DYNAMIC_CALLS:
            return self._refusal(
                DirectRefusalCode.DYNAMIC_DEPENDENCY,
                module.relative,
                call.lineno,
                qualified,
                "dynamic call targets are not eligible for direct diagnostics",
            )
        family = UNSAFE_QUALIFIED_CALLS.get(qualified)
        if family is None and (
            qualified.startswith("os.environ.") or qualified.startswith("sys.path.")
        ):
            family = UnsafeEffectFamily.MUTABLE_GLOBAL_STATE
        if family is None:
            return None
        return self._unsafe(
            module,
            call,
            unsafe_family_reason(family),
            family,
            symbol=qualified,
        )

    def _named_call(
        self,
        module: SourceModule,
        call: ast.Call,
        bindings: dict[str, ImportBinding],
    ) -> tuple[bool, ClosureRefusal | None]:
        assert isinstance(call.func, ast.Name)
        name = call.func.id
        local_functions = module.functions.get(name, [])
        if len(local_functions) == 1:
            return True, self._analyze_function(module, local_functions[0], fixture=False)
        if len(local_functions) > 1:
            return True, self._ambiguous_call(module, call, name)
        binding = bindings.get(name)
        if binding is not None and binding.symbol is not None:
            return True, self._analyze_imported_symbol(module, call, binding)
        local_classes = module.classes.get(name, [])
        if len(local_classes) == 1:
            return True, self._analyze_constructor(module, call, local_classes[0])
        if len(local_classes) > 1:
            return True, self._ambiguous_call(module, call, name)
        return False, None

    def _attribute_call(
        self,
        module: SourceModule,
        call: ast.Call,
        bindings: dict[str, ImportBinding],
    ) -> tuple[bool, ClosureRefusal | None]:
        assert isinstance(call.func, ast.Attribute)
        call_root_name = root_name(call.func)
        binding = bindings.get(call_root_name) if call_root_name else None
        if binding is None:
            return False, None
        imported_module = self._graph.local_module_path(binding.module)
        if imported_module is None:
            return False, None
        symbol = call.func.attr if binding.symbol is None else binding.symbol
        return True, self._analyze_local_symbol(module, call, imported_module, symbol)

    def _analyze_imported_symbol(
        self,
        caller: SourceModule,
        call: ast.Call,
        binding: ImportBinding,
    ) -> ClosureRefusal | None:
        path = self._graph.local_module_path(binding.module)
        if path is None or binding.symbol is None:
            return self._refusal(
                DirectRefusalCode.UNRESOLVED_DEPENDENCY,
                caller.relative,
                call.lineno,
                binding.qualified,
                "imported call target has no candidate-owned implementation",
            )
        return self._analyze_local_symbol(caller, call, path, binding.symbol)

    def _analyze_local_symbol(
        self,
        caller: SourceModule,
        call: ast.Call,
        path: Path,
        symbol: str,
    ) -> ClosureRefusal | None:
        imported = self._graph.load_module(path)
        if isinstance(imported, ClosureRefusal):
            return imported
        if refusal := self._analyze_module_import_time(imported):
            return refusal
        functions = imported.functions.get(symbol, [])
        if len(functions) == 1:
            return self._analyze_function(imported, functions[0], fixture=False)
        classes = imported.classes.get(symbol, [])
        if len(classes) == 1:
            return self._analyze_constructor(imported, call, classes[0])
        return self._refusal(
            DirectRefusalCode.UNRESOLVED_DEPENDENCY,
            caller.relative,
            call.lineno,
            f"{imported.name}.{symbol}",
            "candidate-owned imported symbol did not resolve uniquely",
        )

    def _analyze_constructor(
        self,
        module: SourceModule,
        call: ast.Call,
        class_node: ast.ClassDef,
    ) -> ClosureRefusal | None:
        constructor = unique_named_function(class_node.body, "__init__")
        if constructor is not None:
            return self._analyze_function(module, constructor, fixture=False)
        if any(decorator_name(item).endswith("dataclass") for item in class_node.decorator_list):
            return None
        if not class_node.bases:
            return None
        return self._refusal(
            DirectRefusalCode.UNRESOLVED_DEPENDENCY,
            module.relative,
            call.lineno,
            class_node.name,
            "class constructor behavior is inherited or generated outside the supported model",
        )

    @staticmethod
    def _exact_class(module: SourceModule, name: str) -> ast.ClassDef | None:
        candidates = module.classes.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    def _exact_function(
        self,
        module: SourceModule,
        target: ResolvedTestTarget,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        if target.class_name is None:
            candidates = module.functions.get(target.function_name, [])
        else:
            class_node = self._exact_class(module, target.class_name)
            candidates = (
                []
                if class_node is None
                else [
                    item
                    for item in class_node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name == target.function_name
                ]
            )
        return candidates[0] if len(candidates) == 1 else None

    def _unsupported_collection(
        self,
        module: SourceModule,
        node: ast.AST,
        detail: str,
    ) -> ClosureRefusal:
        return self._refusal(
            DirectRefusalCode.UNSUPPORTED_COLLECTION,
            module.relative,
            getattr(node, "lineno", 1),
            type(node).__name__,
            f"collection-time behavior is unsupported: {detail}",
        )

    def _ambiguous_call(
        self,
        module: SourceModule,
        call: ast.Call,
        name: str,
    ) -> ClosureRefusal:
        return self._refusal(
            DirectRefusalCode.UNRESOLVED_DEPENDENCY,
            module.relative,
            call.lineno,
            name,
            "call target has multiple candidate-owned definitions",
        )

    def _unsafe(
        self,
        module: SourceModule,
        node: ast.AST,
        detail: str,
        family: UnsafeEffectFamily,
        *,
        symbol: str | None = None,
    ) -> ClosureRefusal:
        observation = self._observation(
            module,
            node,
            symbol or type(node).__name__,
            detail,
            family,
        )
        return ClosureRefusal(
            DirectRefusalCode.UNSAFE_EFFECT,
            f"{family.value}: {detail}",
            observation,
        )

    def _refusal(
        self,
        code: DirectRefusalCode,
        path: Path,
        line: int,
        symbol: str,
        detail: str,
    ) -> ClosureRefusal:
        observation = DependencyObservation(path.as_posix(), line, symbol, detail)
        return ClosureRefusal(code, detail, observation)

    @staticmethod
    def _observation(
        module: SourceModule,
        node: ast.AST,
        symbol: str,
        detail: str,
        family: UnsafeEffectFamily | None = None,
    ) -> DependencyObservation:
        return DependencyObservation(
            module.relative.as_posix(),
            getattr(node, "lineno", 1),
            symbol,
            detail,
            family,
        )
