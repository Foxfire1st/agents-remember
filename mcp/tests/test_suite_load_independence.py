"""Source guards against load-dependent timing and process-state leaks."""

from __future__ import annotations

import ast
import asyncio
import io
import tokenize
from collections.abc import Sequence
from pathlib import Path

import pytest
from agents_remember_test_support.testing import waits
from agents_remember_test_support.testing.global_state import PROCESS_MUTABLE_STATES
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

ROOT = Path(__file__).resolve().parents[2]
CLOCKS = {"time", "monotonic", "perf_counter"}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _number(node: ast.AST) -> float | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    return None


def _short(node: ast.AST) -> bool:
    number = _number(node)
    return number is not None and 0 < number < HANG_GUARD_SECONDS


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    while node in parents:
        node = parents[node]
        yield node


def _has_clock(node: ast.AST | None, names: frozenset[str] | set[str] = frozenset()) -> bool:
    if node is None:
        return False
    return any(
        (isinstance(item, ast.Call) and _name(item.func) in CLOCKS)
        or (isinstance(item, ast.Name) and item.id in names)
        for item in ast.walk(node)
    )


def _clock_names(function: ast.AST) -> set[str]:
    names: set[str] = set()
    assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
    ]
    while True:
        previous = names.copy()
        for node in assignments:
            if _has_clock(node.value, names):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names.update(
                    item.id
                    for target in targets
                    for item in ast.walk(target)
                    if isinstance(item, ast.Name)
                )
        if names == previous:
            return names


class _TimingScan:
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.tree = ast.parse(source, filename=str(path))
        self.parents = {
            child: node for node in ast.walk(self.tree) for child in ast.iter_child_nodes(node)
        }
        self.markers = {
            token.start[0]
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type == tokenize.COMMENT
            and "load-independent:" in token.string
            and token.string.split("load-independent:", 1)[1].strip()
        }
        self.clock_names: dict[ast.AST, set[str]] = {
            node: _clock_names(node)
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.findings: list[str] = []

    def report(self, node: ast.expr | ast.stmt | ast.arg, pattern: str, rule: int = 5) -> None:
        if node.lineno not in self.markers and node.lineno - 1 not in self.markers:
            self.findings.append(
                f"{self.path}:{node.lineno}: rule {rule}: {pattern}; "
                "use the hang guard, a condition wait or fake clock, "
                "or add load-independent: <reason>"
            )

    def call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg in {"timeout", "timeout_s", "timeout_seconds"} and _short(keyword.value):
                self.report(keyword.value, f"short {keyword.arg}")
        if (
            _name(node.func) in {"join", "wait", "wait_for", "result"}
            and node.args
            and _short(node.args[-1])
        ):
            self.report(node.args[-1], f"short positional {_name(node.func)} bound")
        self.sleep(node)
        if _name(node.func) in {
            "assertLess",
            "assertLessEqual",
            "assertGreater",
            "assertGreaterEqual",
        }:
            self.comparison(node, node.args[:2])

    def sleep(self, node: ast.Call) -> None:
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"time", "asyncio"}
            and node.func.attr == "sleep"
            and node.args
            and (_number(node.args[0]) or 0) > 0
        ):
            return
        chain = list(_ancestors(node, self.parents))
        in_while = any(isinstance(item, ast.While) for item in chain)
        in_range = any(
            isinstance(item, (ast.For, ast.AsyncFor))
            and isinstance(item.iter, ast.Call)
            and _name(item.iter.func) == "range"
            for item in chain
        )
        if not in_while or in_range:
            self.report(node, "fixed sleep without a condition", 12)

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        arguments = [*node.args.posonlyargs, *node.args.args]
        defaults: list[tuple[ast.arg, ast.expr | None]] = (
            list(zip(arguments[-len(node.args.defaults) :], node.args.defaults, strict=True))
            if node.args.defaults
            else []
        )
        defaults.extend(zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True))
        for argument, default in defaults:
            if default is not None and "timeout" in argument.arg.lower() and _short(default):
                self.report(default, "short timeout default")

    def assignment(self, node: ast.Assign | ast.AnnAssign) -> None:
        if self.parents.get(node) is not self.tree or node.value is None or not _short(node.value):
            return
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(
            isinstance(target, ast.Name)
            and any(
                word in target.id.upper() for word in ("TIMEOUT", "WATCHDOG", "DEADLINE", "WAIT")
            )
            for target in targets
        ):
            self.report(node, "short module wait constant")

    def deadline(self, node: ast.BinOp) -> None:
        if not isinstance(node.op, ast.Add):
            return
        for clock, bound in ((node.left, node.right), (node.right, node.left)):
            number = _number(bound)
            if _has_clock(clock) and number is not None and number < HANG_GUARD_SECONDS:
                self.report(node, "short clock deadline")

    def comparison(self, node: ast.Call | ast.Compare, operands: Sequence[ast.AST]) -> None:
        function = next(
            (item for item in _ancestors(node, self.parents) if item in self.clock_names), None
        )
        names = self.clock_names.get(function, set()) if function is not None else set()
        if any(_number(item) is not None for item in operands) and any(
            _has_clock(item, names) for item in operands
        ):
            self.report(node, "elapsed real clock compared with a number", 11)

    def run(self) -> list[str]:
        handlers = {
            ast.Call: self.call,
            ast.FunctionDef: self.function,
            ast.AsyncFunctionDef: self.function,
            ast.Assign: self.assignment,
            ast.AnnAssign: self.assignment,
            ast.BinOp: self.deadline,
        }
        for node in ast.walk(self.tree):
            handler = handlers.get(type(node))
            if handler is not None:
                handler(node)
            elif isinstance(node, ast.Compare):
                self.comparison(node, [node.left, *node.comparators])
        return self.findings


def timing_violations(path: Path, source: str) -> list[str]:
    return _TimingScan(path, source).run()


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name] = item.name
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_qualified_name(node.value, aliases)}.{node.attr}"
    return ""


def _empty_collection(value: ast.AST, aliases: dict[str, str]) -> bool:
    if isinstance(value, ast.Dict):
        return not value.keys
    if isinstance(value, (ast.List, ast.Set)):
        return not value.elts
    if not isinstance(value, ast.Call):
        return False
    factory = _qualified_name(value.func, aliases)
    if factory in {
        "dict",
        "list",
        "set",
        "collections.deque",
        "collections.OrderedDict",
        "weakref.WeakSet",
        "weakref.WeakKeyDictionary",
        "weakref.WeakValueDictionary",
    }:
        return not value.args and not value.keywords
    return factory == "collections.defaultdict" and len(value.args) <= 1 and not value.keywords


class _ProcessStateScan:
    def __init__(self, path: Path, source: str) -> None:
        self.tree = ast.parse(source, filename=str(path))
        self.parents = {
            child: node for node in ast.walk(self.tree) for child in ast.iter_child_nodes(node)
        }
        parts = path.with_suffix("").parts
        self.module = ".".join(parts[parts.index("agents_remember") :]).removesuffix(".__init__")
        self.aliases = _import_aliases(self.tree)
        self.classes = {node.name for node in ast.walk(self.tree) if isinstance(node, ast.ClassDef)}
        self.found: dict[str, int] = {}

    def scope(self, node: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
        return [
            item
            for item in _ancestors(node, self.parents)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]

    def record(self, name: str, node: ast.stmt) -> None:
        self.found.setdefault(f"{self.module}.{name}", node.lineno)

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            function = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _qualified_name(function, self.aliases) in {
                "functools.cache",
                "functools.lru_cache",
            }:
                enclosing = list(reversed(self.scope(node)))
                name = ".".join([*(item.name for item in enclosing), node.name])
                self.record(name, node)

    def assignment(self, node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and not self.scope(node)
                and node.value is not None
                and _empty_collection(node.value, self.aliases)
            ):
                self.record(target.id, node)
            elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                self.class_assignment(target.value.id, target.attr, node)

    def class_assignment(self, owner: str, attribute: str, node: ast.stmt) -> None:
        if owner == "cls":
            enclosing = next(
                (item for item in self.scope(node) if isinstance(item, ast.ClassDef)), None
            )
            if enclosing is not None:
                owner = enclosing.name
        if owner in self.classes:
            self.record(f"{owner}.{attribute}", node)

    def run(self) -> dict[str, int]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Global):
                for name in node.names:
                    self.record(name, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                self.assignment(node)
        return self.found


def process_state_names(path: Path, source: str) -> dict[str, int]:
    """Find the syntactic process-state population governed by the closed register."""
    return _ProcessStateScan(path, source).run()


def test_product_process_state_has_an_exact_register():
    found = {}
    for path in sorted((ROOT / "mcp/src/agents_remember").rglob("*.py")):
        names = process_state_names(path.relative_to(ROOT), path.read_text("utf-8"))
        found.update({name: (path.relative_to(ROOT), line) for name, line in names.items()})
    registered = {row.name for row in PROCESS_MUTABLE_STATES}
    findings = [
        f"{path}:{line}: rule 4/13: unregistered process state {name}"
        for name, (path, line) in found.items()
        if name not in registered
    ]
    findings.extend(
        f"mcp/src/{row.module.replace('.', '/')}.py:1: rule 4/13: stale process state row {row.name}"
        for row in PROCESS_MUTABLE_STATES
        if row.name not in found
    )
    assert len(registered) == len(PROCESS_MUTABLE_STATES), "duplicate process-state register row"
    assert not findings, "\n".join(findings)


def test_python_waits_are_load_independent():
    findings = []
    for directory in (ROOT / "mcp/tests", ROOT / "mcp/test_support"):
        for path in sorted(directory.rglob("*.py")):
            if path.name != "waits.py":
                findings.extend(timing_violations(path.relative_to(ROOT), path.read_text("utf-8")))
    assert not findings, "\n".join(findings)


@pytest.mark.parametrize(
    "source",
    [
        "f(timeout=1)",
        "thread.join(0.25)",
        "WAIT_SECONDS = 5",
        "def f(timeout_seconds=2): pass",
        "deadline = time.monotonic() + 3",
        "time.sleep(0.01)",
        "for x in range(2):\n while True:\n  time.sleep(0.1)",
        "def f():\n start=time.time()\n elapsed=time.time()-start\n assert elapsed < 3",
    ],
)
def test_timing_scan_reports_patterns(source):
    findings = timing_violations(Path("fixture.py"), source)
    assert findings
    assert all("fixture.py:" in finding and "rule" in finding for finding in findings)
    marked = source.splitlines()
    for line in {int(finding.split(":")[1]) for finding in findings}:
        marked[line - 1] += " # load-independent: deliberately exercised scanner fixture"
    assert not timing_violations(Path("fixture.py"), "\n".join(marked))


def test_timing_scan_accepts_condition_poll_and_reasoned_marker():
    assert not timing_violations(Path("fixture.py"), "while not ready():\n time.sleep(0.01)")
    assert not timing_violations(
        Path("fixture.py"), "f(timeout=1) # load-independent: timeout is expected"
    )
    assert timing_violations(Path("fixture.py"), 'reason="load-independent: ignored"\nf(timeout=1)')


@pytest.mark.parametrize(
    "source, names",
    [
        ("a={}\nb=[]\nc=set()", {"a", "b", "c"}),
        ("from collections import defaultdict as dd\na=dd(list)", {"a"}),
        ("import weakref as w\na=w.WeakValueDictionary()", {"a"}),
        ("def f():\n global a\n a=None", {"a"}),
        (
            "class Registry:\n @classmethod\n def f(cls):\n  cls.instance=None\nRegistry.other=None",
            {"Registry.instance", "Registry.other"},
        ),
        ("from functools import cache as cached\n@cached\ndef f(): pass", {"f"}),
        ("import functools as ft\n@ft.lru_cache(maxsize=1)\ndef f(): pass", {"f"}),
        ("def f():\n a={}\n return a", set()),
    ],
)
def test_process_state_scan_finds_the_registered_patterns(source, names):
    found = process_state_names(Path("mcp/src/agents_remember/fixture.py"), source)
    assert set(found) == {f"agents_remember.fixture.{name}" for name in names}


def test_wait_helpers_name_the_unreached_condition(monkeypatch):
    monkeypatch.setattr(waits, "HANG_GUARD_SECONDS", 0.0)
    waits.wait_until(lambda: True, "ready sync condition")
    asyncio.run(waits.async_wait_until(lambda: True, "ready async condition"))
    with pytest.raises(AssertionError, match="missing sync signal"):
        waits.wait_until(lambda: False, "missing sync signal")
    with pytest.raises(AssertionError, match="missing async signal"):
        asyncio.run(waits.async_wait_until(lambda: False, "missing async signal"))
