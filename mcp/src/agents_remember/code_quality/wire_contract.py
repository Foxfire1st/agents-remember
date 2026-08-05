"""Reject post-``model_dump`` mutations that escape model validation (L6-R10).

A dumped mapping may be edited only when it is passed through ``model_validate`` before
leaving the function. A mutation that is returned, yielded, or merged into a response
without re-validation is an offender. Copies made with ``dict``, ``copy``/``deepcopy``,
unpacking, or mapping union retain taint.

The package-wide analysis discovers functions that return dumps, pass through a parameter,
or re-validate a mapping, then follows calls to those producers. This covers memoized
projection bodies and ``finalize_payload_tokens`` as well as inline dumps.

The one declared post-dump owner is ``served_state_tail``. Only
``payload.update(served_state_tail(...))`` is permitted; handwritten tail keys or another
builder remain offenders. Its route behavior is held by the served-state conformance
suite.

Known false-positive boundaries:

1. Dump-returning and pass-through functions are matched by name package-wide; an
   unrelated same-named call is over-tainted only if its result is also mutated and escapes.
2. Any ``model_dump`` attribute call is treated as Pydantic.
3. Re-validation is determined by textual line order, not control flow.
4. Only local-name mappings are tracked; nested attribute/element mappings are outside
   scope.

Blind spots: a dump passed into a callee and mutated there is not followed, and a mapping
assembled field-by-field without ``model_dump`` is not tainted.

Scope is ``mcp/src/agents_remember`` excluding ``package_data`` and tests. Failures list
every escape and direct the caller to declare the field on the model, re-validate the
edited mapping, or use the served-tail owner where that contract applies.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.code_quality.single_owner import Offender, package_modules, report

__all__ = [
    "SERVED_TAIL_BUILDER",
    "SERVED_TAIL_OWNER",
    "WIRE_DUMP_REMEDIATION",
    "Offender",
    "Producers",
    "dump_returning_names",
    "module_mutation_offenders",
    "post_dump_mutation_offenders",
    "report",
    "served_tail_merges",
    "validating_names",
]

WIRE_DUMP = "model_dump"
"""The pydantic call that turns a model into the dict the wire carries."""

VALIDATE = "model_validate"
"""The call that hands a dict back to a model. Between a mutation and the function's exit,
this is what makes the mutation an intermediate step instead of an escape."""

DICT_MUTATORS = frozenset({"update", "setdefault", "pop", "popitem", "clear"})
"""Dict methods that change the mapping in place."""

COPY_CALLS = frozenset({"copy", "deepcopy"})

SERVED_TAIL_OWNER = "serving/served_state.py"
SERVED_TAIL_BUILDER = "served_state_tail"
"""The single owner of the serve-time tail (L4). The only sanctioned way to add keys to an
already-dumped served body is to call this function; see the module docstring."""

WIRE_DUMP_REMEDIATION = (
    "declare the key on the response model and set it on the MODEL before the dump "
    "(the pattern mcp/tools/base.py uses for nextStep/supervisorBanner), or re-validate "
    "the edited dict through model_validate before returning it -- and if this is a "
    f"serve-time tail on a served body, call {SERVED_TAIL_OWNER}::{SERVED_TAIL_BUILDER}, "
    "which declares exactly the keys it may add"
)


@dataclass
class Taint:
    """Local names known to hold a dumped dict, or a container holding one."""

    dumps: set[str]
    holders: set[str]

    @classmethod
    def empty(cls) -> Taint:
        return cls(dumps=set(), holders=set())


@dataclass(frozen=True)
class Producers:
    """The two ways a call can hand back a dumped dict, discovered package-wide.

    ``returning`` always produces one (``_ProjectionBodyCache.body``). ``passthrough``
    produces one only when it was GIVEN one (``finalize_payload_tokens``), so it is
    resolved against the argument rather than the callee alone.
    """

    returning: frozenset[str] = frozenset()
    passthrough: frozenset[str] = frozenset()


NO_PRODUCERS = Producers()
"""The empty producer set: what a single-module caller sweeps with."""


def _is_dump_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == WIRE_DUMP
    )


def _callee_name(node: ast.Call) -> str | None:
    """The terminal name of a call target -- ``f``/``x.f``/``a.b.f`` all give ``f``."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_copy_of(node: ast.Call, taint: Taint, producers: Producers) -> bool:
    """``dict(x)``, ``x.copy()``, ``copy.deepcopy(x)`` -- a copy of a dump is still one."""
    if isinstance(node.func, ast.Name) and node.func.id == "dict" and node.args:
        return produces_dump(node.args[0], taint, producers)
    if isinstance(node.func, ast.Attribute) and node.func.attr in COPY_CALLS:
        if not node.args:
            return produces_dump(node.func.value, taint, producers)
        return produces_dump(node.args[0], taint, producers)
    return False


def _passes_through_a_dump(node: ast.Call, taint: Taint, producers: Producers) -> bool:
    """A pass-through called on a dump returns that dump -- see :func:`returns_a_parameter`."""
    if _callee_name(node) not in producers.passthrough:
        return False
    return any(produces_dump(argument, taint, producers) for argument in node.args)


def produces_dump(node: ast.expr | None, taint: Taint, producers: Producers) -> bool:
    """Whether this expression evaluates to a dict that came out of a model."""
    if isinstance(node, ast.Call):
        return (
            _is_dump_call(node)
            or _is_copy_of(node, taint, producers)
            or (_callee_name(node) in producers.returning)
            or _passes_through_a_dump(node, taint, producers)
        )
    if isinstance(node, ast.Name):
        return node.id in taint.dumps
    return _wrapper_produces_dump(node, taint, producers)


def _wrapper_produces_dump(node: ast.expr | None, taint: Taint, producers: Producers) -> bool:
    """The forms that wrap another expression: a container index, a spread, a merge."""
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Name) and node.value.id in taint.holders
    if isinstance(node, ast.Dict):
        # A ``None`` key is the ``**x`` spread; the value beside it is what got spread.
        return any(
            key is None and produces_dump(value, taint, producers)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return produces_dump(node.left, taint, producers) or produces_dump(
            node.right, taint, producers
        )
    return False


def _holds_dump(node: ast.expr | None, taint: Taint, producers: Producers) -> bool:
    """A tuple/list literal with a dump inside it -- how ``_ProjectionBodyCache`` memoizes."""
    if not isinstance(node, ast.List | ast.Tuple):
        return False
    return any(produces_dump(element, taint, producers) for element in node.elts)


def _bindings(scope: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    """``(target, value)`` for every single-target assignment in this scope."""
    bound: list[tuple[ast.expr, ast.expr]] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            bound.append((node.targets[0], node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            bound.append((node.target, node.value))
    return bound


def scope_taint(scope: ast.AST, producers: Producers) -> Taint:
    """Local names holding a dumped dict, to a fixed point over the scope's assignments.

    Iterated rather than read in source order so a name bound from another name binds
    whichever way round they were written; over-approximating can only report more.
    """
    taint = Taint.empty()
    bound = _bindings(scope)
    # Terminates on its own: taint only ever grows, and it is bounded by the number of
    # names bound in the scope.
    grew = True
    while grew:
        grew = False
        for target, value in bound:
            if not isinstance(target, ast.Name):
                continue
            if target.id not in taint.dumps and produces_dump(value, taint, producers):
                taint.dumps.add(target.id)
                grew = True
            if target.id not in taint.holders and _holds_dump(value, taint, producers):
                taint.holders.add(target.id)
                grew = True
    return taint


def returns_dump(scope: ast.AST, producers: Producers) -> bool:
    """Whether this function hands a dumped dict back to its caller."""
    taint = scope_taint(scope, producers)
    return any(
        produces_dump(node.value, taint, producers)
        for node in ast.walk(scope)
        if isinstance(node, ast.Return)
    )


def returns_a_parameter(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether this function hands one of its own arguments back.

    A pass-through carries taint through itself: ``finalize_payload_tokens(payload)`` in
    ``models/tokens.py`` stamps the token fields onto the dict it was given and returns
    that same dict, so a dump goes in and the same dump comes out. Without this the wire
    choke point in ``mcp/tools/base.py`` -- the single most important function this rule
    watches -- binds its payload from a call the analysis reads as opaque, and every
    mutation below that line is invisible. It was invisible, until a planted violation
    there was not reported and the gap was traced back to here.
    """
    arguments = function.args
    params = {arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
    return any(
        isinstance(node.value, ast.Name) and node.value.id in params
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
    )


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def dump_returning_names(trees: Mapping[str, ast.AST]) -> Producers:
    """Functions that hand a dumped dict back to their caller, to a fixed point.

    This is what sees through the function boundary, in the two forms the package has.
    ``_ProjectionBodyCache.body`` always returns the memoized dump, so
    ``_projection_body_cache.body(snapshot)`` is a producer at every call site.
    ``finalize_payload_tokens`` returns whatever it was handed, so it carries a dump
    through itself -- that one is what connects the dump to the mutations below it at the
    ``mcp/tools/base.py`` wire choke point. Both are matched by name; see false-positive
    mode 1 in the module docstring.
    """
    passthrough = frozenset(
        function.name
        for tree in trees.values()
        for function in _functions(tree)
        if returns_a_parameter(function)
    )
    producers = Producers(passthrough=passthrough)
    for _ in range(len(trees) + 1):
        found = frozenset(
            function.name
            for tree in trees.values()
            for function in _functions(tree)
            if returns_dump(function, producers)
        )
        if found <= producers.returning:
            break
        producers = Producers(returning=producers.returning | found, passthrough=passthrough)
    return producers


def validating_names(trees: Mapping[str, ast.AST]) -> frozenset[str]:
    """Names of functions that hand a parameter to ``model_validate``.

    ``application/task_doc_tools.py`` re-validates through a local ``_validate(data)``
    rather than inline; without this its nine-site round-trip would read as an escape.
    """
    names: set[str] = set()
    for tree in trees.values():
        for function in _functions(tree):
            params = {arg.arg for arg in function.args.args}
            if any(_validates_one_of(call, params) for call in _calls(function)):
                names.add(function.name)
    return frozenset(names)


def _calls(scope: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(scope) if isinstance(node, ast.Call)]


def _validates_one_of(call: ast.Call, names: set[str]) -> bool:
    if _callee_name(call) != VALIDATE:
        return False
    return any(isinstance(arg, ast.Name) and arg.id in names for arg in call.args)


def _mutation(node: ast.AST, taint: Taint) -> tuple[str, int, str] | None:
    """``(name, line, how)`` when this statement changes a tainted dict in place."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            name = _subscript_name(target)
            if name in taint.dumps:
                return (str(name), node.lineno, "assigns a new key")
    if isinstance(node, ast.AugAssign):
        name = _subscript_name(node.target)
        if isinstance(node.target, ast.Name) and node.target.id in taint.dumps:
            return (node.target.id, node.lineno, "merges in place")
        if name in taint.dumps:
            return (str(name), node.lineno, "assigns a new key")
    if isinstance(node, ast.Delete):
        for target in node.targets:
            name = _subscript_name(target)
            if name in taint.dumps:
                return (str(name), node.lineno, "deletes a key")
    return _method_mutation(node, taint)


def _subscript_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
        return target.value.id
    return None


def _method_mutation(node: ast.AST, taint: Taint) -> tuple[str, int, str] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    receiver = node.func.value
    if node.func.attr not in DICT_MUTATORS or not isinstance(receiver, ast.Name):
        return None
    if receiver.id not in taint.dumps:
        return None
    return (receiver.id, node.lineno, f"{node.func.attr}() on the dumped dict")


def _is_owner_merge(node: ast.AST) -> bool:
    """``x.update(served_state_tail(...))`` -- the one sanctioned serve-time tail merge."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "update":
        return False
    return any(
        isinstance(arg, ast.Call) and _callee_name(arg) == SERVED_TAIL_BUILDER for arg in node.args
    )


def _validated_after(scope: ast.AST, name: str, line: int, validators: frozenset[str]) -> bool:
    """Whether ``name`` is handed back to a model below ``line``."""
    for call in _calls(scope):
        if call.lineno <= line:
            continue
        callee = _callee_name(call)
        if callee != VALIDATE and callee not in validators:
            continue
        if any(isinstance(arg, ast.Name) and arg.id == name for arg in call.args):
            return True
    return False


def module_mutation_offenders(
    tree: ast.AST,
    module: str,
    *,
    producers: Producers = NO_PRODUCERS,
    validators: frozenset[str] = frozenset(),
) -> list[Offender]:
    """Every dumped dict mutated and then let out of its function, in one parsed module."""
    offenders: list[Offender] = []
    for function in _functions(tree):
        taint = scope_taint(function, producers)
        if not taint.dumps:
            continue
        offenders.extend(_function_offenders(function, module, taint, validators))
    return sorted(offenders, key=lambda offender: (offender.line, offender.form))


def _function_offenders(
    function: ast.AST,
    module: str,
    taint: Taint,
    validators: frozenset[str],
) -> list[Offender]:
    offenders: list[Offender] = []
    for node in ast.walk(function):
        if _is_owner_merge(node):
            continue
        found = _mutation(node, taint)
        if found is None:
            continue
        name, line, how = found
        if _validated_after(function, name, line, validators):
            continue
        offenders.append(
            Offender(module, line, "post-dump mutation", f"{how} on {name!r} after model_dump")
        )
    return offenders


def _parse_package(package_root: Path) -> dict[str, ast.AST]:
    trees: Mapping[str, ast.AST] = {}
    for path in package_modules(package_root):
        module = path.relative_to(package_root).as_posix()
        trees[module] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return trees


def post_dump_mutation_offenders(package_root: Path) -> list[Offender]:
    """Every place the package changes a payload after its model stopped describing it."""
    trees = _parse_package(package_root)
    producers = dump_returning_names(trees)
    validators = validating_names(trees)
    offenders: list[Offender] = []
    for module, tree in trees.items():
        offenders.extend(
            module_mutation_offenders(tree, module, producers=producers, validators=validators)
        )
    return offenders


def served_tail_merges(package_root: Path) -> list[Offender]:
    """Where the sanctioned serve-time tail owner is actually called.

    Reported so the permission cannot rot into a dead string: the suite asserts this is
    non-empty, so an owner nobody calls is a failure rather than a quiet exemption.
    """
    found: list[Offender] = []
    for module, tree in _parse_package(package_root).items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_owner_merge(node):
                continue
            found.append(
                Offender(
                    module,
                    node.lineno,
                    "served tail",
                    f"merges the declared tail from {SERVED_TAIL_BUILDER}()",
                )
            )
    return sorted(found, key=lambda offender: (offender.module, offender.line))
