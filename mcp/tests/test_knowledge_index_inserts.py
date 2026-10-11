"""No test or test-support module stages index rows by hand (MIK-R74 rule 3).

Rule 3 requires it statically: the shared fixture builder and every test module build
datasets by writing a text memory tree and calling :func:`build_index`. This test parses
every module under ``mcp/tests`` (476 files) and ``mcp/test_support`` (60 files) and
fails on any ``INSERT INTO`` (or ``REPLACE INTO``) literal naming an index table -- the
eight logical tables or any ``ix_*`` table -- that can execute.

One file is excepted, for the reason rule 3 states itself, and for nothing else:

- ``knowledge_conversion_test_support.py``: the one exception rule 3 names itself --
  it builds the legacy database for the conversion's reader.

The retired-writer detector's spelling vectors in
``test_knowledge_database_retired.py`` are NOT excepted: they are proved inert below.
Every index-table INSERT literal in that file must sit in a data structure (a vector
tuple/list), never as an argument to a call and never joined into one. Its module-level
``exec`` compiles production modules under test, never the vectors. If a vector ever
becomes executable, this census fails on it like any other live INSERT.

``DROP TABLE`` / ``DELETE FROM`` fault injection on tmp copies (staging an unreadable or
unrecorded file for a read's own unavailable/not-recorded paths) is not an INSERT and is
not flagged here; each use carries its own comment at the call site.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

LOGICAL_TABLES = frozenset(
    {
        "repository",
        "invariant",
        "invariant_revision",
        "family",
        "family_revision",
        "family_member",
        "source_anchor",
        "realization_claim",
    }
)

_STATEMENT = re.compile(r"\b(?:insert|replace)\s+into\s+", re.IGNORECASE)
_TABLE = re.compile(r"\b(?:insert|replace)\s+into\s+(?:main\s*\.\s*)?[\"'\[]?([A-Za-z_][\w$]*)", re.IGNORECASE)

EXCEPTED_FILES = {
    "knowledge_conversion_test_support.py": (
        "the one exception MIK-R74 rule 3 names: the legacy database for the conversion reader"
    ),
}

# Modules whose index-table INSERT literals are proved inert (data only, never called).
INERT_VECTOR_MODULES = {
    "test_knowledge_database_retired.py": (
        "writer-detector spelling vectors: bare strings in vector tuples, asserted by the "
        "module's own tests to trip the detector; never a call argument"
    ),
}


def _literals(tree: ast.AST):
    """Yield every string fragment any statement in the module can execute or name.

    F-strings yield one joined fragment with their interpolated values blanked: a
    dynamic ``f"INSERT INTO {table} ..."`` must not slip past the table matcher.
    """

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value, False
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    parts.append(part.value)
                else:
                    parts.append("\0")
            yield node.lineno, "".join(parts), True


def _index_tables(fragment: str, *, dynamic: bool) -> list[str]:
    """Name every index table an INSERT/REPLACE literal in the fragment stages into.

    A dynamic (f-string) statement whose table comes from interpolation reports
    ``dynamic-table``: the census cannot clear what it cannot name. A plain literal
    naming no index table reports nothing.
    """

    tables = []
    if not _STATEMENT.search(fragment):
        return tables
    for match in _TABLE.finditer(fragment):
        name = match.group(1).lower()
        if name in LOGICAL_TABLES or name.startswith("ix_"):
            tables.append(name)
    if not tables and dynamic:
        tables.append("dynamic-table")
    return tables


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Map every node to its parent, so a literal can prove it is never called."""

    mapping: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            mapping[child] = node
    return mapping


def _literal_has_index_insert(node: ast.AST) -> bool:
    """Whether a subtree names an index-table INSERT/REPLACE a literal could stage."""

    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if _index_tables(child.value, dynamic=False):
                return True
        elif isinstance(child, ast.JoinedStr):
            joined = "".join(
                part.value
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else "\0"
                for part in child.values
            )
            if _index_tables(joined, dynamic=True):
                return True
    return False


def _call_argument_names(tree: ast.AST) -> set[str]:
    """Every name flowing into any call: bare arguments and subscripted containers."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
            for child in ast.walk(argument):
                if isinstance(child, ast.Name):
                    names.add(child.id)
    return names


def _assignment_targets(target: ast.AST) -> list[str]:
    """Names one assignment target binds: plain, tuple/list-nested or starred."""

    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        found = []
        for element in target.elts:
            found += _assignment_targets(element)
        return found
    if isinstance(target, ast.Starred):
        return _assignment_targets(target.value)
    return []


def _default_tainted_params(node: ast.AST) -> set[str]:
    """Parameters whose default value stages index rows."""

    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    parameters = (*node.args.args, *node.args.kwonlyargs)
    defaults = (*node.args.defaults, *node.args.kw_defaults)
    offset = len(parameters) - len(defaults)
    tainted = set()
    for parameter, default in zip(parameters[offset:], defaults, strict=True):
        if default is not None and _literal_has_index_insert(default):
            tainted.add(parameter.arg)
    return tainted


def _insert_bound_names(tree: ast.AST) -> set[str]:
    """Names bound to values holding an index-table INSERT literal.

    Plain and annotated assignments, loop variables over tainted containers, and
    default-valued parameters whose default stages rows: passing any of them to a
    call executes the staged rows as surely as passing the literal itself.
    """

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _literal_has_index_insert(node.value):
            for target in node.targets:
                bound.update(_assignment_targets(target))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _literal_has_index_insert(node.value):
                bound.update(_assignment_targets(node.target))
        else:
            bound.update(_default_tainted_params(node))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.For)
            and isinstance(node.iter, ast.Name)
            and node.iter.id in bound
        ):
            bound.update(_assignment_targets(node.target))
    return bound


def _executed(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    *,
    bound_names: set[str] | None = None,
    called_names: set[str] | None = None,
) -> bool:
    """Whether a string node can execute as (part of) a call argument.

    A literal sitting in untouched data is inert. One joined into an f-string or
    concatenated into a call argument executes with it -- and so does one assigned
    to a name, defaulted into a parameter, or held in a container that is later
    passed to a call by name or subscript. Only the bound/call sets make the
    indirect cases visible; without them an assigned vector reads as bare data.
    """

    bound_names = bound_names or set()
    called_names = called_names or set()
    current: ast.AST | None = node
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parent = parents.get(current)
        if parent is None:
            return False
        if isinstance(parent, ast.Call):
            return True
        if isinstance(parent, (ast.JoinedStr, ast.BinOp, ast.FormattedValue)):
            current = parent
            continue
        if isinstance(parent, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
            current = parent
            continue
        if isinstance(parent, ast.Assign):
            return any(
                target.id in bound_names and target.id in called_names
                for target in parent.targets
                if isinstance(target, ast.Name)
            )
        return False
    return False


def _scan_modules() -> list[Path]:
    """Every test and test-support module: the sealed 536-module population."""

    root = Path(__file__).resolve().parent
    support = root.parent / "test_support"
    return sorted(root.glob("*.py")) + sorted(support.rglob("*.py"))


def test_no_test_or_support_module_inserts_into_an_index_table() -> None:
    """Every executable INSERT/REPLACE INTO an index table lives in the exception."""

    offenders = []
    for path in _scan_modules():
        if path.name in EXCEPTED_FILES or path.name in INERT_VECTOR_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        seen: set[tuple[int, str]] = set()
        for lineno, fragment, dynamic in _literals(tree):
            for table in _index_tables(fragment, dynamic=dynamic):
                key = (lineno, table)
                if key not in seen:
                    seen.add(key)
                    offenders.append(f"{path.name}:{lineno}: stages rows into {table}")
    assert offenders == [], (
        "test/support modules stage index rows by hand outside the rule-3 exception "
        f"({', '.join(sorted(EXCEPTED_FILES))}):\n" + "\n".join(offenders)
    )


def test_retired_detector_vectors_are_inert_data_never_called() -> None:
    """The retired file's index-table INSERT literals never execute, directly or not."""

    for name in INERT_VECTOR_MODULES:
        for path in _scan_modules():
            if path.name != name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parents = _parents(tree)
            bound = _insert_bound_names(tree)
            called = _call_argument_names(tree)
            live = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    tables = _index_tables(node.value, dynamic=False)
                    if tables and _executed(node, parents, bound_names=bound, called_names=called):
                        live.append(f"{name}:{node.lineno}: executable staging into {tables}")
                elif isinstance(node, ast.JoinedStr):
                    joined = "".join(
                        part.value
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                        else "\0"
                        for part in node.values
                    )
                    tables = _index_tables(joined, dynamic=True)
                    if tables and _executed(node, parents, bound_names=bound, called_names=called):
                        live.append(f"{name}:{node.lineno}: executable staging into {tables} (joined)")
            assert live == [], (
                "writer-detector vectors became executable; they must stay bare data:\n"
                + "\n".join(live)
            )


def test_the_retired_detector_module_has_no_execute_call_to_feed() -> None:
    """Name/subscript passing is impossible where no execute call exists at all.

    Beyond literal positions, the detector module must contain zero execute or
    executemany calls: open-then-close probes are its only database use, so a later
    execute(vectors[0]) fails here even before the taint predicate sees it.
    """

    root = Path(__file__).resolve().parent
    path = root / "test_knowledge_database_retired.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"execute", "executemany"}:
            calls.append(f"{path.name}:{node.lineno}: {func.attr} call")
        elif isinstance(func, ast.Name) and func.id in {"execute", "executemany"}:
            calls.append(f"{path.name}:{node.lineno}: {func.id} call")
    assert calls == [], (
        "the writer-detector module gained an execute call; its vectors are data only:\n"
        + "\n".join(calls)
    )


def test_assigned_and_subscripted_vectors_count_as_executed() -> None:
    """The Reviewer's two probes, pure AST with no SQL run: both must read True.

    The probe sources are assembled at runtime so this file's own census never sees
    a contiguous staging statement in its literals.
    """

    staged = "INSERT " + "INTO family_member VALUES (1)"
    probes = {
        "assigned": f'sql = "{staged}"\nconnection.execute(sql)\n',
        "subscripted": f'vectors = ["{staged}"]\nconnection.execute(vectors[0])\n',
    }
    for name, source in probes.items():
        tree = ast.parse(source)
        parents = _parents(tree)
        bound = _insert_bound_names(tree)
        called = _call_argument_names(tree)
        assert bound, f"{name}: assignment taints nothing"
        assert called, f"{name}: call names empty"
        flagged = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _index_tables(node.value, dynamic=False)
            and _executed(node, parents, bound_names=bound, called_names=called)
        ]
        assert flagged, f"{name}: an executed vector reads as inert data"
    bare = ast.parse(f'vectors = ["{staged}"]\n')
    parents = _parents(bare)
    quiet = [
        node
        for node in ast.walk(bare)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _index_tables(node.value, dynamic=False)
        and _executed(
            node,
            parents,
            bound_names=_insert_bound_names(bare),
            called_names=_call_argument_names(bare),
        )
    ]
    assert quiet == [], "untouched data must stay inert"


def test_the_census_exception_is_exactly_the_stated_one() -> None:
    """The exception cannot silently grow: it must still trip the scan."""

    for name in EXCEPTED_FILES:
        for path in _scan_modules():
            if path.name != name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            hits = [
                table
                for _, fragment, dynamic in _literals(tree)
                for table in _index_tables(fragment, dynamic=dynamic)
            ]
            assert hits, (
                f"{name} is excepted but no longer stages rows into an index table; "
                "remove it from EXCEPTED_FILES instead of carrying a dead exception"
            )
