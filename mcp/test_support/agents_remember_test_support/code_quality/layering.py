"""Package layering enforcement over ``layers.toml`` (260731-EFA-L9 R12).

The contract is one strict total order of top-level packages. A module in
package P may import package Q only when ``rank(Q) < rank(P)``, and no
package-pair cycle may exist. There is no baseline and no allowlist: the tree
either satisfies the declared order or the gate fails.

Packages carrying ``present = false`` are not scanned (their directories do
not exist yet); if the leaf named in ``arrives_in`` has already landed in git,
the stale flag itself fails the build.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from agents_remember.kernel.git_command import run_git


@dataclass(frozen=True)
class LayersContract:
    order: tuple[str, ...]
    ranks: dict[str, int]
    packages: dict[str, dict[str, object]]


@dataclass(frozen=True)
class ImportStatement:
    importer: str
    imported: str
    path: str
    line: int
    module: str


@dataclass(frozen=True)
class LayeringReport:
    violations: list[ImportStatement] = field(default_factory=list)
    cycles: list[tuple[str, str]] = field(default_factory=list)
    stale_present_flags: list[tuple[str, str]] = field(default_factory=list)
    undeclared_dirs: list[str] = field(default_factory=list)
    undeclared_imports: list[ImportStatement] = field(default_factory=list)
    scanned_modules: int = 0

    @property
    def ok(self) -> bool:
        return (
            not self.violations
            and not self.cycles
            and not self.stale_present_flags
            and not self.undeclared_dirs
            and not self.undeclared_imports
        )


def load_contract(layers_path: Path) -> LayersContract:
    with layers_path.open("rb") as handle:
        data = tomllib.load(handle)
    order = tuple(data["contract"]["order"])
    ranks = {name: index for index, name in enumerate(order)}
    packages = {name: dict(data.get("package", {}).get(name, {})) for name in order}
    return LayersContract(order=order, ranks=ranks, packages=packages)


def package_for(rel_path: Path, contract: LayersContract) -> str | None:
    parts = rel_path.parts
    if len(parts) == 1:
        root_modules = set(
            cast(list[str], contract.packages.get("errors", {}).get("root_modules", []))
        )
        return "errors" if rel_path.name in root_modules else None
    candidate = parts[0]
    if candidate not in contract.ranks:
        return None
    if not contract.packages[candidate].get("present", True):
        return None
    return candidate


def resolve_import_target(module: str, contract: LayersContract) -> str | None:
    """Map an imported module name to its top-level package, if declared."""
    parts = module.split(".")
    if parts[0] != "agents_remember":
        return None
    rest = parts[1:]
    if not rest:
        return None
    candidate = rest[0]
    if candidate in contract.ranks:
        if candidate == "errors":
            if len(rest) == 1:
                return "errors"
            return None
        return candidate
    return None


def imports_of(path: Path) -> list[tuple[int, str, tuple[str, ...]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name, ()))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = tuple(alias.name for alias in node.names)
            found.append((node.lineno, module, names))
    return found


def undeclared_dirs(source_root: Path, contract: LayersContract) -> list[str]:
    """Top-level Python source packages that ``layers.toml`` does not declare.

    A deleted package can leave an ignored ``__pycache__`` directory in a long-lived
    checkout.  That bytecode debris is not source architecture and must not make the
    master gate depend on checkout hygiene.  Any undeclared directory that still owns a
    Python source file remains a hard failure, including namespace-package layouts.
    """
    undeclared: list[str] = []
    for entry in sorted(source_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in contract.ranks:
            continue
        if entry.name in {"__pycache__", "package_data"} or entry.name.startswith("."):
            continue
        if not any(
            path.is_file() and "__pycache__" not in path.parts for path in entry.rglob("*.py")
        ):
            continue
        undeclared.append(entry.name)
    return undeclared


@dataclass
class _ScanContext:
    contract: LayersContract
    present_packages: set[str]
    violations: list[ImportStatement]
    undeclared_imports: list[ImportStatement]
    all_edges: dict[str, set[str]]


def _record_edge(statement: ImportStatement, context: _ScanContext) -> None:
    context.all_edges.setdefault(statement.importer, set()).add(statement.imported)
    if context.contract.ranks[statement.imported] >= context.contract.ranks[statement.importer]:
        context.violations.append(statement)


def _package_import_statements(
    importer: str,
    path: Path,
    lineno: int,
    names: tuple[str, ...],
    context: _ScanContext,
) -> tuple[list[ImportStatement], list[ImportStatement]]:
    """Resolve ``from agents_remember import X`` into edges or undeclared-package failures."""
    edges: list[ImportStatement] = []
    undeclared: list[ImportStatement] = []
    for name in names:
        if name == "*":
            continue
        if name not in context.contract.ranks:
            undeclared.append(
                ImportStatement(
                    importer=importer,
                    imported=name,
                    path=path.as_posix(),
                    line=lineno,
                    module=f"agents_remember.{name}",
                )
            )
            continue
        if name == importer or name not in context.present_packages:
            continue
        edges.append(
            ImportStatement(
                importer=importer,
                imported=name,
                path=path.as_posix(),
                line=lineno,
                module=f"agents_remember.{name}",
            )
        )
    return edges, undeclared


def _collect_violations(
    source_root: Path, contract: LayersContract
) -> tuple[list[ImportStatement], list[ImportStatement], int, dict[str, set[str]]]:
    violations: list[ImportStatement] = []
    undeclared_imports: list[ImportStatement] = []
    all_edges: dict[str, set[str]] = {}
    scanned = 0
    present_packages = {
        name for name in contract.order if contract.packages[name].get("present", True)
    }
    context = _ScanContext(
        contract=contract,
        present_packages=present_packages,
        violations=violations,
        undeclared_imports=undeclared_imports,
        all_edges=all_edges,
    )
    for path in sorted(source_root.rglob("*.py")):
        if "package_data" in path.parts:
            continue
        rel = path.relative_to(source_root)
        importer = package_for(rel, contract)
        if importer is None:
            continue
        scanned += 1
        for lineno, module, names in imports_of(path):
            if module == "agents_remember" and names:
                edges, found_undeclared = _package_import_statements(
                    importer, path, lineno, names, context
                )
                undeclared_imports.extend(found_undeclared)
                for statement in edges:
                    _record_edge(statement, context)
                continue
            imported = resolve_import_target(module, contract)
            if imported is None or imported == importer:
                continue
            if imported not in present_packages:
                continue
            _record_edge(
                ImportStatement(
                    importer=importer,
                    imported=imported,
                    path=path.as_posix(),
                    line=lineno,
                    module=module,
                ),
                context,
            )
    return violations, undeclared_imports, scanned, all_edges


def _collect_cycles(all_edges: dict[str, set[str]]) -> list[tuple[str, str]]:
    cycles: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for importer, targets in all_edges.items():
        for imported in targets:
            pair: tuple[str, str] = (
                (importer, imported) if importer <= imported else (imported, importer)
            )
            if pair in seen_pairs:
                continue
            if importer in all_edges.get(imported, set()):
                seen_pairs.add(pair)
                cycles.append((importer, imported))
    return cycles


def _collect_stale_flags(
    contract: LayersContract,
    project_root: Path,
) -> list[tuple[str, str]]:
    stale: list[tuple[str, str]] = []
    for name in contract.order:
        package = contract.packages[name]
        if package.get("present", True):
            continue
        arrives_in = package.get("arrives_in")
        if not arrives_in:
            continue
        if _leaf_landed(str(arrives_in), project_root):
            stale.append((name, str(arrives_in)))
    return stale


def build_report(
    source_root: Path,
    contract: LayersContract,
    project_root: Path,
) -> LayeringReport:
    violations, undeclared_imports, scanned, all_edges = _collect_violations(source_root, contract)
    return LayeringReport(
        violations=violations,
        cycles=_collect_cycles(all_edges),
        stale_present_flags=_collect_stale_flags(contract, project_root),
        undeclared_dirs=undeclared_dirs(source_root, contract),
        undeclared_imports=undeclared_imports,
        scanned_modules=scanned,
    )


def _leaf_landed(leaf_id: str, project_root: Path | None = None) -> bool:
    """Whether the named leaf already appears in the repository's commit history."""
    try:
        completed = run_git(
            project_root or Path.cwd(),
            ["log", "--oneline", "--grep", leaf_id, "-i"],
        )
    except (OSError, ValueError):
        return False
    return bool(completed.stdout.strip())


def render(report: LayeringReport) -> str:
    lines = [f"layering: scanned {report.scanned_modules} modules"]
    for name in report.undeclared_dirs:
        lines.append(
            f"layering undeclared package directory: {name!r} is not declared in layers.toml"
        )
    for statement in report.undeclared_imports:
        lines.append(
            f"layering undeclared package import: {statement.importer} -> "
            f"{statement.imported} ({statement.path}:{statement.line} {statement.module})"
        )
    for violation in report.violations:
        lines.append(
            f"layering violation: {violation.importer} -> {violation.imported} "
            f"({violation.path}:{violation.line} {violation.module})"
        )
    for importer, imported in report.cycles:
        lines.append(f"layering cycle: {importer} <-> {imported}")
    for name, leaf in report.stale_present_flags:
        lines.append(
            f"layering stale flag: package {name!r} is still present=false but its "
            f"arrives_in leaf {leaf} has landed"
        )
    return "\n".join(lines)


def check_layering(project_root: Path) -> LayeringReport:
    layers_path = project_root / "layers.toml"
    if not layers_path.exists():
        raise FileNotFoundError(f"layers.toml not found under {project_root}")
    source_root = project_root / "mcp/src/agents_remember"
    contract = load_contract(layers_path)
    return build_report(source_root, contract, project_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce layers.toml package order.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing layers.toml and mcp/src.",
    )
    args = parser.parse_args(argv)
    try:
        report = check_layering(args.project_root)
    except FileNotFoundError as error:
        print(f"layering: {error}", file=sys.stderr)
        return 1
    print(render(report))
    if report.ok:
        print("layering: PASS")
        return 0
    print("layering: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
