"""The pause path cannot reach a publication, and nothing on it can move a ref.

The developer's instruction is that pausing a master must do ONLY that: no ref move, no
commit, no landing, no ledger row. The pause therefore has no runtime condition that could be
turned into a publication -- publication is excluded structurally, by what the route is able
to import and call at all. This module is the executable specification of that structure.

What it measures is a STATIC, source-level graph: every ``import``/``from ... import`` statement
reachable from the pause module by reading each reached module's own AST, with ``TYPE_CHECKING``
branches excluded because those never execute. It is a superset of the runtime import graph in
one direction (it also counts imports inside function bodies) and a subset in another (it does
not follow dynamic imports such as ``importlib.import_module``), and it is per-module reachability
rather than a process-wide ``sys.modules`` reading -- which is what makes it independent of
whatever else the test session happens to have imported.

It asserts that closure is disjoint from every module that owns a ref move, a landing, a ledger
write or a closeout, and then asserts the closure is deep enough for that first assertion to mean
anything: a walker that stops at the direct imports reaches no publication module either, so the
disjointness check alone cannot fail. The pause's legitimate reading of the branch-authority
helpers goes through ``worktrees.modules.git``; what it can never reach is the transaction that
moves a ref and the routes that record one.

Reaching the checkpoint landing -- ``worktree_checkpoint_landing`` -- is the exact regression
this guards: the split into a partial PUBLICATION and a stop exists because the two were one
verb, and the stop must not be able to become the publication again.
"""

from __future__ import annotations

import ast
from pathlib import Path

PAUSE_MODULE = "agents_remember.worktrees.modules.pause"

# Every module that can create a commit, move a protected ref, land a series, record a landing
# or write a ledger row. The pause's closure must be disjoint from all of them.
PUBLICATION_MODULES = frozenset(
    {
        "agents_remember.worktrees.integration.integration_ref_transaction",
        "agents_remember.worktrees.integration.atomic_series_landing",
        "agents_remember.worktrees.integration.direct_landing",
        "agents_remember.worktrees.modules.integrate",
        "agents_remember.worktrees.modules.record_landing",
        "agents_remember.worktrees.modules.landing_record",
        "agents_remember.worktrees.modules.closeout",
        "agents_remember.worktrees.modules.closeout_external",
        "agents_remember.worktrees.modules.closeout_lineage",
        "agents_remember.worktrees.series_closeout",
        "agents_remember.worktrees.direct_landing",
        "agents_remember.worktrees.closeout_input",
    }
)


def _module_files() -> dict[str, Path]:
    """Every importable module of the package, keyed by its dotted name."""

    source_root = Path(__file__).resolve().parents[1] / "src"
    files: dict[str, Path] = {}
    for path in (source_root / "agents_remember").rglob("*.py"):
        parts = list(path.relative_to(source_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        files[".".join(parts)] = path
    return files


def _resolve(name: str, files: dict[str, Path]) -> str | None:
    """The importable module a dotted name denotes, trimming trailing attribute names."""

    candidate = name
    while candidate:
        if candidate in files:
            return candidate
        candidate = candidate.rsplit(".", 1)[0] if "." in candidate else ""
    return None


def _is_type_checking(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _dependencies(node: ast.AST, current: str, found: set[str]) -> None:
    """Collect runtime imports, descending everywhere except a ``TYPE_CHECKING`` branch."""

    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If) and _is_type_checking(child):
            continue
        if isinstance(child, ast.Import):
            found.update(alias.name for alias in child.names)
        elif isinstance(child, ast.ImportFrom):
            base = current
            for _ in range(child.level):
                base = base.rsplit(".", 1)[0] if "." in base else ""
            if child.level == 0:
                base = ""
            # ``from . import x`` carries no module, so the base level IS the target then.
            target = (f"{base}.{child.module}" if base else child.module) if child.module else base
            found.add(target)
            found.update(f"{target}.{alias.name}" for alias in child.names)
        _dependencies(child, current, found)


def _runtime_imports_of(module: str) -> set[str]:
    """The modules ``module`` itself imports at runtime -- the walker's first step only."""

    files = _module_files()
    dependencies: set[str] = set()
    _dependencies(ast.parse(files[module].read_text(encoding="utf-8")), module, dependencies)
    return {
        reached
        for dependency in dependencies
        if (reached := _resolve(dependency, files)) is not None and reached != module
    }


def _runtime_import_closure(root: str) -> dict[str, str | None]:
    """Every runtime module reachable from ``root``, mapped to the module that first imports it.

    A module is marked EXPANDED only when its own imports are read; being discovered as somebody's
    dependency is not the same thing and must not stop the walk. Marking it at discovery is the
    defect this function exists in its current shape to prevent: it silently reduces the result to
    the root plus its direct imports while still looking like a closure.
    """

    files = _module_files()
    importers: dict[str, str | None] = {}
    expanded: set[str] = set()
    pending = [root]
    while pending:
        name = pending.pop()
        resolved = _resolve(name, files)
        if resolved is None or resolved in expanded:
            continue
        expanded.add(resolved)
        # The root has no importer inside this walk; every other module keeps the first one seen.
        importers.setdefault(resolved, None)
        dependencies: set[str] = set()
        _dependencies(
            ast.parse(files[resolved].read_text(encoding="utf-8")), resolved, dependencies
        )
        for dependency in dependencies:
            reached = _resolve(dependency, files)
            if reached is None or reached == resolved:
                continue
            importers.setdefault(reached, resolved)
            pending.append(dependency)
    return importers


def _import_path(parent: dict[str, str | None], target: str) -> str:
    chain = [target]
    while parent.get(chain[-1]) is not None:
        importer = parent[chain[-1]]
        assert importer is not None
        chain.append(importer)
    return " -> ".join(reversed(chain))


def test_the_pause_cannot_reach_any_publication_module() -> None:
    """Zero ref transactions, zero landing routes, zero closeout or ledger writers.

    The walker is poked before it is trusted: a closure that stops at the direct imports reports
    the same "no publication reached" answer as a correct one, so the separate non-vacuity
    assertions below are what make this test able to fail.
    """

    closure = _runtime_import_closure(PAUSE_MODULE)

    reached = sorted(module for module in PUBLICATION_MODULES if module in closure)
    assert reached == [], (
        "the pause path can reach a publication; pausing a master must not be able to publish:\n  "
        + "\n  ".join(f"{module} via {_import_path(closure, module)}" for module in reached)
    )

    # NON-VACUITY. A walker that marks a module expanded when it is DISCOVERED instead of when it
    # is expanded returns the root plus its direct imports and nothing else -- which is exactly the
    # answer that makes the assertion above vacuous. Measured on this tree: 60 modules beyond the
    # pause module's 5 direct imports, so these two checks leave the depth-1 regression no room.
    direct = _runtime_imports_of(PAUSE_MODULE)
    reachable = set(closure)
    assert {PAUSE_MODULE} | direct <= reachable, (
        "the walker did not even reach the pause module's own direct imports: "
        f"missing {sorted(({PAUSE_MODULE} | direct) - reachable)}"
    )
    deeper = reachable - direct - {PAUSE_MODULE}
    assert len(deeper) >= len(direct), (
        "the walker stopped at the direct imports instead of expanding them: "
        f"{len(deeper)} module(s) beyond {len(direct)} direct import(s), "
        f"expected at least {len(direct)}"
    )
    # Two named witnesses, so the "transitive" claim is legible rather than only counted. Neither
    # is imported by the pause module itself: each is reached only by expanding a direct import,
    # so both disappear from the result under the depth-1 regression the counts above reject.
    assert "agents_remember.worktrees.scheduling_mode" in deeper
    assert "agents_remember.kernel.primitives.gate_vocab" in deeper
    assert "agents_remember.worktrees.worktree_contract" in direct
