"""What the source of one production module shows about SQLite databases.

This is the reader behind the static half of the MIK-R26 retirement guard. The lists, the tests and
the statement of what the check guarantees are in ``mcp/tests/test_knowledge_database_retired.py``;
:func:`read_module` reads one parsed module for them and returns a :class:`Reading`:

* every way the module reaches ``apsw`` or ``sqlite3``: the constructor calls with the function
  that makes them, each form that hides a constructor, each other attribute it names, and each
  import of a module name computed at run time;
* every string the module spells, and every string it builds from constants (``+``, f-strings,
  ``join`` over a literal sequence, a name bound once to such a string), with a part computed at
  run time kept as the placeholder ``_computed_``;
* the SQL statements among those strings that write a row, attach a database or copy one, read
  with and without their SQL comments and statement by statement;
* every name the module takes from another module, so that the callers of a helper can be pinned.

It reads source only. A value that exists at run time alone (a path, a name assembled with ``%``
or ``str.format``, code run from a string, a module taken from ``sys.modules`` or ``globals()``)
is outside what it can see. The run-time guard in ``database_retirement_guard`` sees the
connections and the file operations among those when a test executes them, never a statement.
"""

from __future__ import annotations

import ast
import functools
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

# --- strings: what a module spells, and what it builds from constants ---------------------------------

_COMPUTED = "_computed_"
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
# A table as ``UPDATE`` may name it: bare, quoted or bracketed, behind a schema, with an alias or an
# index choice.
_NAME = r'(?:\w+|"[^"]+"|\[[^\]]+\]|`[^`]+`)'
_TABLE = rf"(?:{_NAME}\s*\.\s*)?{_NAME}(?:\s+AS\s+\w+)?(?:\s+(?:NOT\s+INDEXED|INDEXED\s+BY\s+\w+))?"
_ROW_WRITE = (
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|DELETE\s+FROM"
    rf"|UPDATE\s+(?:OR\s+\w+\s+)?{_TABLE}\s+SET"
)
# ``WITH [RECURSIVE] name [(columns)] AS [[NOT] MATERIALIZED] (`` and whatever precedes the write.
_WITH = (
    r"WITH\s+(?:RECURSIVE\s+)?\w+\s*(?:\([^)]*\)\s*)?AS\s+(?:(?:NOT\s+)?MATERIALIZED\s+)?\(.*?\b"
)
_WRITE_STATEMENT = re.compile(
    rf"^\s*(?:(?:{_WITH})?(?:{_ROW_WRITE})|VACUUM\s+(?:\w+\s+)?INTO|DROP\s+TABLE"
    r"|CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\b.*?\bAS\s+SELECT"
    r"|PRAGMA\s+(?:\w+\s*\.\s*)?writable_schema)\b",
    re.IGNORECASE | re.DOTALL,
)
# After ``ATTACH``: ``DATABASE``, a parameter, a quoted literal, a parenthesis or a function call
# before ``AS``, or one bare word before ``AS`` and the schema name.
_ATTACH_STATEMENT = re.compile(
    r"^\s*ATTACH\b\s*(?:DATABASE\b|[?:@$'\"]|(?:\w+\s*)?\(.*\)\s*AS\b|\w+\s+AS\s+\w+\s*$)",
    re.IGNORECASE | re.DOTALL,
)
_ATTACH_DATABASE = re.compile(r"\bATTACH\s+DATABASE\b", re.IGNORECASE)
_COPY_STATEMENT = re.compile(r"\bVACUUM\s+(\w+\s+)?INTO\b", re.IGNORECASE)
DATABASE_FILE_NAME = re.compile(r"\.sqlite|\.db3?\b", re.IGNORECASE)


def _folded(node: ast.AST, constants: Mapping[str, str]) -> str | None:
    """The string ``node`` builds, a part computed at run time written as ``_computed_``.

    ``None`` when ``node`` is not a string built from at least one constant.
    """

    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(_part(value, constants) for value in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _sum(node, constants)
    return _joined(node, constants)


def _sum(node: ast.BinOp, constants: Mapping[str, str]) -> str | None:
    left, right = _folded(node.left, constants), _folded(node.right, constants)
    if left is None and right is None:
        return None
    return (_COMPUTED if left is None else left) + (_COMPUTED if right is None else right)


def _joined(node: ast.AST, constants: Mapping[str, str]) -> str | None:
    """``separator.join(...)`` over a literal list or tuple."""

    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    separator, items = node.func.value, node.args[0] if len(node.args) == 1 else None
    if node.func.attr != "join" or not isinstance(items, ast.List | ast.Tuple):
        return None
    if not (isinstance(separator, ast.Constant) and isinstance(separator.value, str)):
        return None
    return separator.value.join(_part(item, constants) for item in items.elts)


def _part(node: ast.AST, constants: Mapping[str, str]) -> str:
    folded = _folded(node.value if isinstance(node, ast.FormattedValue) else node, constants)
    return _COMPUTED if folded is None else folded


def _bound_once(tree: ast.Module) -> dict[str, ast.expr]:
    """The value of each name a module binds exactly once, by a plain assignment."""

    bindings = Counter(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    )
    bindings.update(node.arg for node in ast.walk(tree) if isinstance(node, ast.arg))
    bindings.update(
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    )
    values: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            bound, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            bound, value = node.target, node.value
        else:
            continue
        if isinstance(bound, ast.Name) and value is not None and bindings[bound.id] == 1:
            values[bound.id] = value
    return values


def _constants(tree: ast.Module) -> dict[str, str]:
    """The names a module binds exactly once, to a string built from constants alone."""

    values = _bound_once(tree)
    constants: dict[str, str] = {}
    while True:  # a constant may be built from another one
        found = {
            name: text
            for name, value in values.items()
            if (text := _folded(value, constants)) is not None and _COMPUTED not in text
        }
        if found == constants:
            return constants
        constants = found


def _texts(tree: ast.Module, constants: Mapping[str, str]) -> tuple[str, ...]:
    """Every string a module spells or builds from constants, outside its docstrings."""

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    texts = {
        folded
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp | ast.Call)
        and id(node) not in docstrings
        and (folded := _folded(node, constants)) is not None
    }
    return tuple(sorted(texts))


def _statements(text: str) -> list[str]:
    """``text``, and each SQL statement it may hold, read with and without its SQL comments."""

    forms = {text, _SQL_COMMENT.sub(" ", text)}
    return [text, *(statement for form in sorted(forms) for statement in form.split(";"))]


def _whole_strings(tree: ast.Module) -> list[str]:
    """Every string constant of a module as it stands, docstrings included."""

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


# --- the SQLite modules: every way a module reaches them ----------------------------------------------

DATABASE_MODULES = ("apsw", "sqlite3")
_CONSTRUCTOR_NAMES = {"Connection", "connect"}
_IMPORTERS = {"import_module", "__import__"}


def _annotations(tree: ast.Module) -> list[ast.expr]:
    """Every type annotation of a module: a type named there constructs nothing."""

    found: list[ast.expr | None] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg | ast.AnnAssign):
            found.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append(node.returns)
    return [annotation for annotation in found if annotation is not None]


class _DatabaseUse(ast.NodeVisitor):
    """How one module reaches the SQLite modules: its constructor calls and every hidden form."""

    def __init__(self, tree: ast.Module, constants: Mapping[str, str]) -> None:
        self.found: list[tuple[str, ast.Call]] = []
        self.aliases: list[str] = []
        self.attributes: set[str] = set()
        self.computed_imports: list[int] = []
        self._constants = constants
        self._bound: dict[str, str] = {}  # local name -> the dotted path an import bound it to
        self._importers = set(_IMPORTERS)
        self._stack: list[str] = []
        self._called: set[int] = set()
        self._annotated = {
            id(inner) for annotation in _annotations(tree) for inner in ast.walk(annotation)
        }
        # The nodes that are the left side of an attribute access: the inside of a longer chain.
        self._inner = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self._bind_import(node)
            elif isinstance(node, ast.ImportFrom):
                self._bind_from(node)
        self.visit(tree)

    def _bind_import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in DATABASE_MODULES:
                # ``import apsw.ext`` binds ``apsw``; ``import apsw.ext as e`` binds the submodule.
                self._bound[alias.asname or root] = alias.name if alias.asname else root

    def _bind_from(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name in _IMPORTERS:
                self._importers.add(alias.asname or alias.name)
            if module.split(".")[0] not in DATABASE_MODULES:
                continue
            if alias.name == "*" or alias.name in _CONSTRUCTOR_NAMES:
                self.aliases.append(f"from {module} import {alias.name}")
            else:
                self._bound[alias.asname or alias.name] = f"{module}.{alias.name}"

    def _resolved(self, node: ast.expr) -> str | None:
        """The dotted path under a SQLite module that ``node`` names, when it names one."""

        if isinstance(node, ast.Name):
            return self._bound.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolved(node.value)
            return None if base is None else f"{base}.{node.attr}"
        return None

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        path = self._resolved(node.func)
        if path is not None and path.rsplit(".", 1)[-1] in _CONSTRUCTOR_NAMES:
            self.found.append((self._stack[0] if self._stack else "<module>", node))
            self._called.add(id(node.func))
        self._hidden(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        path = None if id(node) in self._inner else self._resolved(node)
        if path is not None and id(node) not in self._annotated and id(node) not in self._called:
            if path.rsplit(".", 1)[-1] in _CONSTRUCTOR_NAMES:
                # ``opener = apsw.Connection``: stored, passed or subclassed, it is an alias.
                self.aliases.append(f"{path} named without a call")
            else:
                self.attributes.add(path)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if (
            node.id in self._bound
            and isinstance(node.ctx, ast.Load)
            and id(node) not in self._inner
            and id(node) not in self._annotated
        ):
            # ``getattr(apsw, name)``, ``vars(apsw)``, ``opener = apsw``: no attribute to check.
            self.aliases.append(f"{self._bound[node.id]} used as an object, as {node.id}")

    def _hidden(self, node: ast.Call) -> None:
        """A constructor or a SQLite module reached by a name given as a string."""

        function = node.func
        name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")
        values = [*node.args, *(keyword.value for keyword in node.keywords)]
        folded = [_folded(value, self._constants) for value in values]
        literal = [text for text in folded if text is not None and _COMPUTED not in text]
        if name == "getattr" and len(literal) == 1 and literal[0] in _CONSTRUCTOR_NAMES:
            self.aliases.append(f"getattr(..., {literal[0]!r})")
        if name in self._importers and values:
            if any(text.split(".")[0] in DATABASE_MODULES for text in literal):
                self.aliases.append(f"{name}({literal[0]!r})")
            if folded[0] is None or _COMPUTED in folded[0]:
                self.computed_imports.append(node.lineno)


@dataclass(frozen=True)
class Reading:
    """What the source of one production module shows the static check."""

    constructors: tuple[tuple[str, ast.Call], ...]
    aliases: tuple[str, ...]
    attributes: frozenset[str]
    computed_imports: tuple[int, ...]
    foreign_names: frozenset[str]
    texts: tuple[str, ...]
    writes: tuple[str, ...]
    attaches: tuple[str, ...]
    copies: tuple[str, ...]


def _backups(tree: ast.Module) -> list[str]:
    return [
        f"line {node.lineno}: .backup("
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "backup"
    ]


def _bound_here(tree: ast.Module) -> set[str]:
    """The names a module binds itself: assigned, defined, a parameter or a caught error."""

    bound = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    bound.update(node.arg for node in ast.walk(tree) if isinstance(node, ast.arg))
    named = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler
    bound.update(
        getattr(node, "name", None) or "" for node in ast.walk(tree) if isinstance(node, named)
    )
    return bound


def _foreign_names(tree: ast.Module) -> frozenset[str]:
    """Every name a module takes from another module.

    A name it imports, each part of a module path it imports, an attribute it reads off an imported
    module, and a name it reads without binding it (what a ``*`` import brings).
    """

    imported: set[str] = set()  # the local names an import binds
    taken: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            taken.update((node.module or "").split("."))
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                taken.update(alias.name.split("."))
                imported.add(alias.asname or alias.name.split(".")[0])
    local = _bound_here(tree) | imported
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            root: ast.expr = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in imported:
                taken.add(node.attr)
        elif isinstance(node, ast.Name) and node.id not in local:
            taken.add(node.id)
    return frozenset(taken)


@functools.cache
def read_module(tree: ast.Module) -> Reading:
    constants = _constants(tree)
    use = _DatabaseUse(tree, constants)
    texts = _texts(tree, constants)
    # A whole string, docstrings included, is matched as the checks always matched it; each
    # statement inside a string that is not a docstring is matched as well.
    whole = _whole_strings(tree)
    statements = [statement for text in texts for statement in _statements(text)]
    return Reading(
        constructors=tuple(use.found),
        aliases=tuple(use.aliases),
        attributes=frozenset(use.attributes),
        computed_imports=tuple(use.computed_imports),
        foreign_names=_foreign_names(tree),
        texts=texts,
        writes=tuple(
            statement.strip().split("(")[0][:60]
            for statement in (*whole, *statements)
            if _WRITE_STATEMENT.match(statement)
        ),
        attaches=(
            *(text.strip()[:60] for text in whole if _ATTACH_DATABASE.search(text)),
            *(text.strip()[:60] for text in statements if _ATTACH_STATEMENT.match(text)),
        ),
        copies=(
            *_backups(tree),
            *(text[:60] for text in (*whole, *texts) if _COPY_STATEMENT.search(text)),
        ),
    )
