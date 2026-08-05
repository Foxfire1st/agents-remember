"""Enforce the MCP/application transport boundary (L6-R7).

All modules under ``mcp/tools`` and ``mcp/registration`` plus ``mcp/server.py`` may import
only MCP peers, application operations, models, errors, and kernel primitives. Every
Python module under ``serving`` is checked in reverse and may not import application or
MCP adapter surfaces. Missing or empty owned surfaces fail instead of yielding a vacuous
zero.

Absolute, relative, and ``TYPE_CHECKING`` imports are dependencies and are all scanned.
``mcp.config`` is outside the adapter surface because its runtime-config move belongs to
L9.

False-positive boundaries: test helpers placed inside production adapter packages are
reported, and type-only domain imports are still boundary edges. Dynamic imports are the
known blind spot; plugin loading must live in an application operation so the transport
retains a static application-only edge.
"""

from __future__ import annotations

import ast
import tomllib
from dataclasses import dataclass
from pathlib import Path


class BoundaryContractError(RuntimeError):
    """The declared package order cannot define the application boundary."""


MCP_ADAPTER_SURFACES = (
    "agents_remember.mcp.tools",
    "agents_remember.mcp.registration",
    "agents_remember.mcp.server",
)


@dataclass(frozen=True)
class BoundaryViolation:
    """One import that crosses the MCP/application boundary."""

    module: str
    line: int
    imported_package: str
    statement: str
    remediation: str = "route the use case through agents_remember.application"

    def __str__(self) -> str:
        return (
            f"{self.module}:{self.line} imports domain package "
            f"{self.imported_package!r}: {self.statement}; {self.remediation}"
        )


@dataclass(frozen=True)
class _LayerContract:
    ranks: dict[str, int]
    models_rank: int


def _read_contract(layers_path: Path) -> _LayerContract:
    if not layers_path.is_file():
        raise BoundaryContractError(f"missing package contract: {layers_path}")
    with layers_path.open("rb") as handle:
        data = tomllib.load(handle)
    package_table = data.get("package")
    if not isinstance(package_table, dict):
        raise BoundaryContractError(f"{layers_path} has no [package.*] declarations")
    ranks: dict[str, int] = {}
    for name, declaration in package_table.items():
        if not isinstance(name, str) or not isinstance(declaration, dict):
            raise BoundaryContractError(f"invalid package declaration in {layers_path}")
        rank = declaration.get("rank")
        if not isinstance(rank, int):
            raise BoundaryContractError(f"[package.{name}] has no integer rank")
        ranks[name] = rank
    for required in ("models", "application", "mcp"):
        if required not in ranks:
            raise BoundaryContractError(f"{layers_path} does not declare package {required!r}")
    if not ranks["models"] < ranks["application"] < ranks["mcp"]:
        raise BoundaryContractError(
            "layers.toml must order models below application below mcp "
            "before the MCP boundary can be enforced"
        )
    return _LayerContract(ranks=ranks, models_rank=ranks["models"])


def _resolved_imports(node: ast.Import | ast.ImportFrom, module_parts: list[str]) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        base = node.module or ""
    else:
        keep = len(module_parts) - node.level
        prefix = module_parts[: max(keep, 0)]
        suffix = node.module.split(".") if node.module else []
        base = ".".join([*prefix, *suffix])
    if not base:
        return [base]
    return [f"{base}.{alias.name}" for alias in node.names]


def _top_package(imported: str) -> str | None:
    parts = imported.split(".")
    if not parts or parts[0] != "agents_remember":
        return None
    return parts[1] if len(parts) > 1 else ""


def _permitted(package: str, contract: _LayerContract) -> bool:
    rank = contract.ranks.get(package)
    if rank is None:
        return False
    return rank <= contract.models_rank or package in {"application", "mcp"}


def _required_modules(package_root: Path) -> list[Path]:
    modules: list[Path] = []
    for relative in (Path("mcp/tools"), Path("mcp/registration")):
        root = package_root / relative
        if not root.is_dir():
            raise BoundaryContractError(f"missing MCP transport package: {root}")
        subtree = sorted(root.rglob("*.py"))
        if not subtree:
            raise BoundaryContractError(f"MCP transport package contains no Python modules: {root}")
        modules.extend(subtree)
    server = package_root / "mcp" / "server.py"
    if not server.is_file():
        raise BoundaryContractError(f"missing MCP server startup module: {server}")
    modules.append(server)
    return modules


def _serving_modules(package_root: Path) -> list[Path]:
    root = package_root / "serving"
    if not root.is_dir():
        raise BoundaryContractError(f"missing serving package: {root}")
    modules = sorted(root.rglob("*.py"))
    if not modules:
        raise BoundaryContractError(f"serving package contains no Python modules: {root}")
    return modules


def _module_imports(path: Path, package_root: Path) -> tuple[str, str, ast.Module]:
    module = path.relative_to(package_root).with_suffix("").as_posix()
    source = path.read_text(encoding="utf-8")
    return module, source, ast.parse(source, filename=str(path))


def _transport_violations(
    package_root: Path,
    modules: list[Path],
    contract: _LayerContract,
) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    for path in modules:
        module, source, tree = _module_imports(path, package_root)
        module_parts = ["agents_remember", *module.split("/")]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for imported in _resolved_imports(node, module_parts):
                package = _top_package(imported)
                if package is None or _permitted(package, contract):
                    continue
                statement = ast.get_source_segment(source, node)
                violations.append(
                    BoundaryViolation(
                        module=f"{module}.py",
                        line=node.lineno,
                        imported_package=package or "<package root>",
                        statement=statement or imported,
                    )
                )
    return violations


def _reverse_serving_violations(
    package_root: Path,
    modules: list[Path],
) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    for path in modules:
        module, source, tree = _module_imports(path, package_root)
        module_parts = ["agents_remember", *module.split("/")]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for imported in _resolved_imports(node, module_parts):
                imports_application = (
                    imported == "agents_remember.application"
                    or imported.startswith("agents_remember.application.")
                )
                imports_mcp_adapter = any(
                    imported == surface or imported.startswith(f"{surface}.")
                    for surface in MCP_ADAPTER_SURFACES
                )
                if not imports_application and not imports_mcp_adapter:
                    continue
                statement = ast.get_source_segment(source, node)
                violations.append(
                    BoundaryViolation(
                        module=f"{module}.py",
                        line=node.lineno,
                        imported_package="application" if imports_application else "mcp",
                        statement=statement or imported,
                        remediation=(
                            "move shared vocabulary to agents_remember.models or call a "
                            "serving/lower-ranked domain owner; serving must not import "
                            "application or MCP adapters"
                        ),
                    )
                )
    return violations


def application_boundary_violations(
    package_root: Path,
    layers_path: Path,
) -> list[BoundaryViolation]:
    """Return every MCP transport bypass and reverse serving edge in stable source order."""
    contract = _read_contract(layers_path)
    violations = _transport_violations(package_root, _required_modules(package_root), contract)
    violations.extend(_reverse_serving_violations(package_root, _serving_modules(package_root)))
    return sorted(
        violations,
        key=lambda item: (item.module, item.line, item.imported_package, item.statement),
    )
