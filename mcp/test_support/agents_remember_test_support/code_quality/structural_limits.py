"""Enforce source-package structural limits (260731-EFA-L6 R8).

Limits over ``mcp/src/agents_remember`` are 100 lines per function, 15 public operations
per class, and 25 modules directly in one directory. Tests are outside this population.

Class surface counts distinct public names. Properties/setters, overload groups, and
singledispatch registrations count once. Module functions that mutate attributes of a
same-package receiver count against that receiver so relocating methods does not clear a
finding. Read-only protocol properties declare fields and do not count as operations.

``layers.toml`` may declare a directory-scoped sequencing deviation with an owner, date,
deleting leaf, and explicit limit names. A deviation can never name a class or module,
and becomes stale as soon as any named cap clears; stale or incomplete declarations fail.

Known false-positive boundaries:

1. Decorators do not extend function length; a function docstring does.
2. Nested-class methods and closures do not charge an enclosing class.
3. Private and dunder names are not public surface.
4. Body-less protocol properties are fields; protocol methods and properties with bodies
   remain operations.
5. Module functions that only read a receiver, or mutate an external type such as
   ``argparse.Namespace``, are not relocated methods.
6. Subpackages are measured independently rather than charged to their parent directory.

Known blind spot: inherited methods are not resolved across modules. Reviewers must reject
a wide class split only into mixins that it immediately recombines.

Failures report the complete offender list. Split a cohesive responsibility, make an
internal operation private, or subpackage a crowded directory; do not widen a cap or add
a construct-level exception.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

FUNCTION_LINE_LIMIT = 100
CLASS_PUBLIC_METHOD_LIMIT = 15
DIRECTORY_MODULE_LIMIT = 25

LAYERS_FILE = "layers.toml"
SEQUENCING_TABLE = "sequencing"
SEQUENCING_FIELDS = ("directory", "declared_on", "owner", "deleted_by")

# Closed vocabulary for the caps a sequencing deviation may name.
DIRECTORY_MODULES = "directory_modules"
CLASS_SURFACE = "class_surface"
SEQUENCING_LIMITS = (DIRECTORY_MODULES, CLASS_SURFACE)


class DeclarationError(RuntimeError):
    """``layers.toml`` says something a structural cap cannot act on."""


@dataclass(frozen=True)
class Offender:
    """One construct over one limit, with everything the remedy needs to find it."""

    path: str
    line: int
    name: str
    measured: int
    limit: int

    @property
    def location(self) -> str:
        """``path:line`` for a construct, bare ``path`` for a whole directory."""
        if self.line == 0:
            return self.path
        return f"{self.path}:{self.line}"

    @property
    def excess(self) -> int:
        return self.measured - self.limit


@dataclass(frozen=True)
class DirectoryDeviation:
    """A directory-scoped structural departure and the leaf required to clear it.

    ``layers.toml`` ``[sequencing.*]`` entries must provide a directory, declaration date,
    owner, deleting leaf, and one or more names from ``SEQUENCING_LIMITS``. The entry covers
    only those caps within that directory and becomes stale when any named cap clears. Class-
    and module-scoped deviations are not supported.
    """

    name: str
    directory: str
    declared_on: str
    owner: str
    deleted_by: str
    limits: tuple[str, ...]

    def covers(self, limit: str) -> bool:
        """Whether this deviation departs from ``limit``."""
        return limit in self.limits

    def contains(self, path: str) -> bool:
        """Whether a measured construct's display path lies inside the declared directory."""
        return path == self.directory or path.startswith(f"{self.directory}/")

    def describe(self) -> str:
        return (
            f"[{SEQUENCING_TABLE}.{self.name}] {self.directory}/ "
            f"over {', '.join(self.limits)} "
            f"(declared {self.declared_on} by {self.owner}, deleted by {self.deleted_by})"
        )


@dataclass(frozen=True)
class StaleDeviation:
    """A deviation and each declared cap the current tree now meets."""

    deviation: DirectoryDeviation
    cleared: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.deviation.describe()} -- no longer departs from {', '.join(self.cleared)}"


def python_sources(root: Path) -> list[Path]:
    """Every ``.py`` file under ``root``, in a stable order."""
    return sorted(root.rglob("*.py"))


def package_sources(root: Path) -> list[tuple[str, str]]:
    """``(display path, source text)`` for every module under ``root``, read once.

    The class cap needs two passes over the whole package -- one to learn what every class
    declares, one to measure -- and reading twice would double the cost of the check.
    """
    return [
        (_display(path, root), path.read_text(encoding="utf-8")) for path in python_sources(root)
    ]


def _display(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def source_span(node: ast.stmt) -> int:
    """Lines the statement spans, counting its own first line.

    ``end_lineno`` is typed optional because the node classes are shared with trees built
    by hand; every tree this module measures comes from ``ast.parse``, which always sets
    it. The fallback measures a one-line construct rather than raising, because a checker
    that crashes on a shape it did not expect is a checker that gets switched off.
    """
    end_lineno = node.end_lineno or node.lineno
    return end_lineno - node.lineno + 1


def function_definitions(tree: ast.Module) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every ``def`` and ``async def`` in the module, nested ones included."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def measure_functions(source: str, *, display_path: str) -> list[Offender]:
    """Every function in ``source``, measured. Callers filter; this reports all of them."""
    tree = ast.parse(source, filename=display_path)
    return [
        Offender(
            path=display_path,
            line=node.lineno,
            name=node.name,
            measured=source_span(node),
            limit=FUNCTION_LINE_LIMIT,
        )
        for node in function_definitions(tree)
    ]


def long_functions(root: Path, *, limit: int = FUNCTION_LINE_LIMIT) -> list[Offender]:
    """Functions under ``root`` longer than ``limit`` lines, longest first."""
    offenders: list[Offender] = []
    for path in python_sources(root):
        display = _display(path, root)
        source = path.read_text(encoding="utf-8")
        offenders.extend(
            measured
            for measured in measure_functions(source, display_path=display)
            if measured.measured > limit
        )
    return _worst_first(offenders)


def _nested_statements(statement: ast.stmt) -> list[ast.stmt]:
    """Statements one level inside ``statement``, exception handlers included."""
    nested: list[ast.stmt] = []
    for child in ast.iter_child_nodes(statement):
        if isinstance(child, ast.stmt):
            nested.append(child)
        elif isinstance(child, ast.ExceptHandler):
            nested.extend(child.body)
    return nested


def _class_body(node: ast.ClassDef) -> Iterator[ast.stmt]:
    """Every statement the class declares, including ones behind a class-body ``if``.

    The walk descends through ordinary statements so a platform-guarded or
    ``TYPE_CHECKING``-guarded declaration still counts, and stops at a nested ``class`` --
    whose members belong to that class -- and at a ``def``, whose body holds closures and
    locals rather than declarations.
    """
    pending = list(node.body)
    while pending:
        statement = pending.pop(0)
        yield statement
        if not isinstance(statement, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            pending.extend(_nested_statements(statement))


def method_definitions(node: ast.ClassDef) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every method the class declares, nested classes and closures excluded."""
    for statement in _class_body(node):
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            yield statement


def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the def is a ``typing.overload`` stub rather than a distinct method."""
    return any(_decorator_name(decorator) == "overload" for decorator in node.decorator_list)


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Name):
        return decorator.id
    return ""


def _name_of(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def is_protocol(node: ast.ClassDef) -> bool:
    """Whether the class declares a structural TYPE rather than an implementation."""
    return any(_name_of(base) == "Protocol" for base in node.bases)


def _is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the def has no body: only ``...``, optionally under a docstring."""
    statements = [
        statement
        for statement in node.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    return all(
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
        for statement in statements
    )


def declares_field(node: ast.ClassDef, method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the member declares a read-only FIELD rather than an operation.

    True for exactly one shape: a body-less ``@property`` inside a ``Protocol``. That shape
    is a record's field written the only way a COVARIANT protocol member can be written --
    ``status: str`` on a protocol is read-write and therefore invariant, so a row whose
    ``status`` is a narrow ``Literal[...]`` would fail to match it. See
    ``controlplane/seats.py``, which says so at length and is why this rule exists.

    The same record spelled as a dataclass or a pydantic model measures zero public methods,
    because its fields are annotations; 804 of this package's 1,010 classes are in exactly
    that position. Counting the protocol spelling at sixteen and the dataclass spelling at
    zero measures the spelling rather than the shape, and the cap is on OPERATIONS -- "this
    class has taken a second job". A declaration has no jobs.

    Deliberately narrow, so it cannot become a hiding place. A protocol's ``def`` members
    still count in full (``serving/harness_control_adapter.py``'s eleven-operation
    ``HarnessProtocolAdapter`` is exactly the fat interface this cap should see); a property
    with a real body still counts, on a protocol or anywhere else; and nothing outside a
    ``Protocol`` is folded at all.
    """
    return (
        is_protocol(node)
        and any(_decorator_name(decorator) == "property" for decorator in method.decorator_list)
        and _is_stub(method)
    )


def public_method_names(node: ast.ClassDef) -> set[str]:
    """The class's public surface: distinct non-underscore method names, overloads folded.

    Distinct *names* rather than distinct ``def`` nodes, because a property and its setter,
    a ``singledispatchmethod`` and its registrations, and an overload set are each one
    member of the surface spelled several times. Declared protocol fields are not surface
    at all -- see :func:`declares_field`.
    """
    return {
        method.name
        for method in method_definitions(node)
        if not method.name.startswith("_")
        and not _is_overload(method)
        and not declares_field(node, method)
    }


def _first_parameter(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.arg | None:
    positional = [*node.args.posonlyargs, *node.args.args]
    return positional[0] if positional else None


def _attributes_on(node: ast.AST, subject: str) -> tuple[set[str], set[str]]:
    """Attributes touched on the name ``subject``, and the subset assigned to."""
    touched: set[str] = set()
    assigned: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        if not (isinstance(child.value, ast.Name) and child.value.id == subject):
            continue
        touched.add(child.attr)
        if isinstance(child.ctx, ast.Store):
            assigned.add(child.attr)
    return touched, assigned


def declared_attribute_names(node: ast.ClassDef) -> set[str]:
    """Everything the class declares a member of itself: fields, methods, ``self.x`` stores.

    An unannotated relocated method matches this fingerprint. A class may declare more
    fields than the function touches; every touched field must still be declared here.
    """
    names: set[str] = set()
    for statement in _class_body(node):
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(statement.name)
            receiver = _first_parameter(statement)
            if receiver is not None:
                names |= _attributes_on(statement, receiver.arg)[1]
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names.add(statement.target.id)
        elif isinstance(statement, ast.Assign):
            names.update(target.id for target in statement.targets if isinstance(target, ast.Name))
    return names


@dataclass(frozen=True)
class BoundFunction:
    """A public module-level function that assigns to its first parameter's attributes.

    That is a method with the receiver spelled as a parameter. ``touched`` is every
    attribute it reads or writes on that parameter, which is what identifies the class when
    ``annotation`` is empty -- and relocation always leaves it empty, because an annotation
    would reintroduce the import the split was pretending to avoid.
    """

    name: str
    touched: frozenset[str]
    annotation: str


def _annotation_name(annotation: ast.expr | None) -> str:
    """The bare class name an annotation refers to, or ``""`` if it is not a plain name."""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.strip("\"' ")
    return ""


def bound_functions(tree: ast.Module) -> list[BoundFunction]:
    """The module's top-level functions that mutate whatever is passed as their first argument."""
    found: list[BoundFunction] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        parameter = _first_parameter(node)
        if node.name.startswith("_") or parameter is None:
            continue
        touched, assigned = _attributes_on(node, parameter.arg)
        if assigned:
            found.append(
                BoundFunction(
                    name=node.name,
                    touched=frozenset(touched),
                    annotation=_annotation_name(parameter.annotation),
                )
            )
    return found


def _classes_bound_by(
    function: BoundFunction, declared: Mapping[tuple[str, str], frozenset[str]]
) -> list[tuple[str, str]]:
    if function.annotation:
        return [key for key in declared if key[1] == function.annotation]
    return [key for key, attributes in declared.items() if function.touched <= attributes]


def relocated_surface(
    sources: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], set[str]]:
    """Module-level functions charged to the class they are a method of, by ``(path, name)``.

    A function matched to more than one class is charged to all of them: over-reporting a
    near-identical second class is a visible failure with an obvious fix (annotate the
    parameter, and the match becomes exact), whereas charging neither is a hole in the one
    place this rule exists.
    """
    trees = [(display, ast.parse(source, filename=display)) for display, source in sources]
    declared = {
        (display, node.name): frozenset(declared_attribute_names(node))
        for display, tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }
    charged: dict[tuple[str, str], set[str]] = {}
    for _display_path, tree in trees:
        for function in bound_functions(tree):
            for key in _classes_bound_by(function, declared):
                charged.setdefault(key, set()).add(function.name)
    return charged


def measure_classes(
    source: str,
    *,
    display_path: str,
    relocated: Mapping[tuple[str, str], set[str]] | None = None,
) -> list[Offender]:
    """Every class in ``source``, measured by public surface.

    ``relocated`` is :func:`relocated_surface`'s package-wide result; without it a class is
    measured on its own body alone, which is what a single-source caller can see. Counted
    as a union of NAMES, so a module-level function that shadows a method it delegates to
    costs one member rather than two.
    """
    charged = relocated or {}
    tree = ast.parse(source, filename=display_path)
    return [
        Offender(
            path=display_path,
            line=node.lineno,
            name=node.name,
            measured=len(public_method_names(node) | charged.get((display_path, node.name), set())),
            limit=CLASS_PUBLIC_METHOD_LIMIT,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]


def _all_wide_classes(root: Path, limit: int) -> list[Offender]:
    """Every class over ``limit``, before any deviation is applied."""
    sources = package_sources(root)
    relocated = relocated_surface(sources)
    offenders: list[Offender] = []
    for display, source in sources:
        offenders.extend(
            measured
            for measured in measure_classes(source, display_path=display, relocated=relocated)
            if measured.measured > limit
        )
    return offenders


def wide_classes(
    root: Path,
    *,
    deviations: Iterable[DirectoryDeviation] = (),
    limit: int = CLASS_PUBLIC_METHOD_LIMIT,
) -> list[Offender]:
    """Classes over ``limit`` that no declared deviation covers, widest first."""
    declared = [deviation for deviation in deviations if deviation.covers(CLASS_SURFACE)]
    return _worst_first(
        offender
        for offender in _all_wide_classes(root, limit)
        if not any(deviation.contains(offender.path) for deviation in declared)
    )


def module_counts(root: Path) -> dict[str, int]:
    """Modules directly in each directory under ``root``, keyed by posix relative path.

    Directly, not recursively: a sub-package is counted in its own right, so splitting a
    drawer into sub-packages is a real remedy rather than a way of moving the number.
    """
    counts: dict[str, int] = {}
    for path in python_sources(root):
        directory = _display(path.parent, root) if path.parent != root else "."
        counts[directory] = counts.get(directory, 0) + 1
    return counts


def crowded_directories(
    root: Path,
    *,
    deviations: Iterable[DirectoryDeviation] = (),
    limit: int = DIRECTORY_MODULE_LIMIT,
) -> list[Offender]:
    """Directories over ``limit`` modules that no declared deviation covers, worst first."""
    declared = {
        deviation.directory for deviation in deviations if deviation.covers(DIRECTORY_MODULES)
    }
    return _worst_first(
        Offender(path=directory, line=0, name=directory, measured=count, limit=limit)
        for directory, count in module_counts(root).items()
        if count > limit and directory not in declared
    )


def stale_deviations(
    root: Path,
    deviations: Iterable[DirectoryDeviation],
    *,
    module_limit: int = DIRECTORY_MODULE_LIMIT,
    class_limit: int = CLASS_PUBLIC_METHOD_LIMIT,
) -> list[StaleDeviation]:
    """Declared deviations, and the caps they no longer depart from.

    This is what stops the declaration turning into the allowlist R12 forbids. A deviation
    outlives a cap the moment the tree stops exceeding that cap, and from that moment the
    build fails until the line is narrowed or deleted. Checked per declared cap rather than
    per entry, so an entry covering two caps cannot survive on the strength of one.
    """
    counts = module_counts(root)
    wide = _all_wide_classes(root, class_limit)
    stale: list[StaleDeviation] = []
    for deviation in deviations:
        cleared = []
        if (
            deviation.covers(DIRECTORY_MODULES)
            and counts.get(deviation.directory, 0) <= module_limit
        ):
            cleared.append(DIRECTORY_MODULES)
        if deviation.covers(CLASS_SURFACE) and not any(
            deviation.contains(offender.path) for offender in wide
        ):
            cleared.append(CLASS_SURFACE)
        if cleared:
            stale.append(StaleDeviation(deviation=deviation, cleared=tuple(cleared)))
    return stale


def _worst_first(offenders: Iterable[Offender]) -> list[Offender]:
    return sorted(offenders, key=lambda offender: (-offender.measured, offender.location))


def read_directory_deviations(layers_path: Path) -> list[DirectoryDeviation]:
    """Directory deviations declared in ``layers.toml``'s ``[sequencing.*]`` tables.

    The shape, which is 260731-EFA-L6's own architecture contract rather than a second
    mechanism invented here::

        [sequencing.serving_package_size]
        directory = "serving/"
        limits = ["directory_modules", "class_surface"]
        declared_on = "2026-08-01"
        owner = "the developer (ruled knowingly, 260731-EFA-L6)"
        deleted_by = "260731-EFA-L12"
        statement = \"\"\"...\"\"\"

    A ``[sequencing.*]`` table with no ``directory`` key is about something else -- the
    section is the contract's general sequencing register -- and is passed over. One that
    names a directory but cannot say who owns it, when it was taken, which leaf deletes it,
    or which caps it departs from raises rather than being skipped: a deviation nobody can
    be held to is exactly the allowlist entry R12 forbids, and silently honouring it would
    be worse than none.

    A missing ``layers.toml`` means no deviation is declared, so every crowded directory
    and every wide class is an offender. That is the safe direction: the check fails loudly
    rather than passing on a file it could not find.
    """
    if not layers_path.is_file():
        return []
    with layers_path.open("rb") as handle:
        data = tomllib.load(handle)
    sequencing = data.get(SEQUENCING_TABLE, {})
    if not isinstance(sequencing, dict):
        raise DeclarationError(
            f"[{SEQUENCING_TABLE}] in {layers_path} is not a table of tables; the "
            "structural caps read one [sequencing.<name>] entry per deviation"
        )
    return [
        _deviation_from(name, entry, layers_path)
        for name, entry in sorted(sequencing.items())
        if isinstance(entry, dict) and "directory" in entry
    ]


def _deviation_from(name: str, entry: dict[str, object], layers_path: Path) -> DirectoryDeviation:
    values: dict[str, str] = {}
    for key in SEQUENCING_FIELDS:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DeclarationError(
                f"[{SEQUENCING_TABLE}.{name}] in {layers_path} names a directory but has "
                f"no non-empty '{key}'. All of {list(SEQUENCING_FIELDS)} are mandatory: a "
                "deviation with no owner, no date and no named deleter is a grandfather "
                "entry, which 260731-EFA-L6 R12 forbids."
            )
        values[key] = value.strip()
    return DirectoryDeviation(
        name=name,
        directory=values["directory"].strip("/"),
        declared_on=values["declared_on"],
        owner=values["owner"],
        deleted_by=values["deleted_by"],
        limits=_limits_from(name, entry.get("limits"), layers_path),
    )


def _limits_from(name: str, declared: object, layers_path: Path) -> tuple[str, ...]:
    """The caps one deviation departs from: a non-empty list drawn from a closed vocabulary.

    Mandatory, and refused when it names anything this module does not measure. An entry
    that cannot say WHICH cap it departs from excuses all of them, including caps written
    after it -- which is a standing exemption rather than a sequencing decision.
    """
    if not isinstance(declared, list) or not declared:
        raise DeclarationError(
            f"[{SEQUENCING_TABLE}.{name}] in {layers_path} names a directory but has no "
            f"non-empty 'limits'. Name the caps it departs from, drawn from "
            f"{list(SEQUENCING_LIMITS)}: a deviation that does not say what it excuses "
            "excuses everything, which 260731-EFA-L6 R12 forbids."
        )
    unknown = [value for value in declared if value not in SEQUENCING_LIMITS]
    if unknown:
        raise DeclarationError(
            f"[{SEQUENCING_TABLE}.{name}] in {layers_path} declares limits {unknown} that "
            f"no structural cap measures; the vocabulary is {list(SEQUENCING_LIMITS)}"
        )
    return tuple(dict.fromkeys(str(value) for value in declared))


def render_offenders(subject: str, offenders: Sequence[Offender], remedy: str) -> str:
    """Render the complete offender list plus the required remediation (R15)."""
    lines = [
        f"{len(offenders)} {subject} over the limit. There is no baseline, allowlist or "
        f"exemption for this check: {remedy}"
    ]
    lines.extend(
        f"  {offender.measured:>4} (limit {offender.limit}, +{offender.excess})  "
        f"{offender.location} {offender.name}"
        for offender in offenders
    )
    return "\n".join(lines)
