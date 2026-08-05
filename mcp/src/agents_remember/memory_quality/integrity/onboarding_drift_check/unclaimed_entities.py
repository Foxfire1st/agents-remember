"""Rank source files that no entity-catalog fingerprint claims.

The entity catalog is intentionally not a tiling of the repository. Most unclaimed files are
ordinary modules and reporting every one would turn a useful review prompt into a census nobody
reads. This report therefore computes the complete set difference, then lists only unclaimed
Python modules that declare one of three facts already used as repository contracts:

* a versioned ``*_CONTRACT`` string such as ``ar-durable-store/1.0``;
* a ``StoreOwnership`` value, which names writer and compaction authority; or
* a ``*_SCHEMA`` / ``SCHEMA_VERSION`` string.

The ranking is lexical and deterministic: a versioned contract carrying authority comes first,
then other versioned contracts, then authority declarations, then schemas. Declaration counts
and the source path break ties. There are no weights, name-similarity guesses, path allowlists,
or gate verdicts in this module.

False-positive modes
--------------------
1. A schema or contract constant can describe a low-impact interchange format. The report says
   "review", not "create an entity", so the catalog owner decides whether the concept is real.
2. ``StoreOwnership`` is an exact source-level signal. A test fixture or example under a tracked
   Python path would rank even when it carries no production authority.

Known limit
-----------
Only Python declarations have a syntax parser here. TypeScript and other source files still count
in the complete unclaimed set, but they are not ranked until an equally explicit declaration
contract exists for those languages.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.memory_quality.integrity.onboarding_drift_check.entities import (
    parse_entity_fingerprint_rows,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    list_repo_sources,
)

VERSIONED_CONTRACT = re.compile(r"^ar-[a-z0-9-]+/(?:v)?\d+(?:\.\d+)*$")


@dataclass(frozen=True)
class UnclaimedEntitySource:
    """One meaningful unclaimed source and the declarations that ranked it."""

    path: str
    versioned_contracts: tuple[str, ...]
    authority_declarations: tuple[str, ...]
    schema_declarations: tuple[str, ...]

    @property
    def priority(self) -> str:
        if self.versioned_contracts and self.authority_declarations:
            return "contract + authority"
        if self.versioned_contracts:
            return "versioned contract"
        if self.authority_declarations:
            return "authority"
        return "schema"


@dataclass(frozen=True)
class UnclaimedEntityReport:
    """Complete coverage counts plus the ranked meaningful subset."""

    source_count: int
    claimed_source_count: int
    unclaimed_source_count: int
    ranked: tuple[UnclaimedEntitySource, ...]


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> tuple[str, ...]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return tuple(target.id for target in targets if isinstance(target, ast.Name))


def _assigned_value(node: ast.Assign | ast.AnnAssign) -> ast.expr | None:
    return node.value


def _call_name(value: ast.expr | None) -> str | None:
    if not isinstance(value, ast.Call):
        return None
    if isinstance(value.func, ast.Name):
        return value.func.id
    if isinstance(value.func, ast.Attribute):
        return value.func.attr
    return None


def declaration_signals(path: Path, relative_path: str) -> UnclaimedEntitySource | None:
    """Return the explicit contract/schema/authority facts declared by one Python module."""
    if path.suffix != ".py":
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
    contracts: list[str] = []
    authorities: list[str] = []
    schemas: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        names = _assigned_names(node)
        value = _assigned_value(node)
        if _call_name(value) == "StoreOwnership":
            authorities.extend(names)
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for name in names:
            if name.endswith("_CONTRACT") and VERSIONED_CONTRACT.fullmatch(value.value):
                contracts.append(f"{name}={value.value}")
            if name == "SCHEMA_VERSION" or name.endswith("_SCHEMA"):
                schemas.append(f"{name}={value.value}")
    if not contracts and not authorities and not schemas:
        return None
    return UnclaimedEntitySource(
        path=relative_path,
        versioned_contracts=tuple(sorted(contracts)),
        authority_declarations=tuple(sorted(authorities)),
        schema_declarations=tuple(sorted(schemas)),
    )


def _rank_key(source: UnclaimedEntitySource) -> tuple[int, int, int, int, str]:
    if source.versioned_contracts and source.authority_declarations:
        tier = 0
    elif source.versioned_contracts:
        tier = 1
    elif source.authority_declarations:
        tier = 2
    else:
        tier = 3
    return (
        tier,
        -len(source.versioned_contracts),
        -len(source.authority_declarations),
        -len(source.schema_declarations),
        source.path,
    )


def rank_unclaimed_entity_sources(
    repo_root: Path,
    entity_catalog: Path,
    *,
    source_inventory: list[str] | None = None,
) -> UnclaimedEntityReport:
    """Compute the real inventory/evidence set difference and rank its meaningful members."""
    inventory = set(
        source_inventory if source_inventory is not None else list_repo_sources(repo_root)
    )
    claimed = {
        source_path
        for row in parse_entity_fingerprint_rows(entity_catalog)
        for source_path in row.evidence_paths
    }
    unclaimed = inventory - claimed
    ranked = [
        signal
        for relative_path in sorted(unclaimed)
        if (repo_root / relative_path).is_file()
        if (signal := declaration_signals(repo_root / relative_path, relative_path)) is not None
    ]
    ranked.sort(key=_rank_key)
    return UnclaimedEntityReport(
        source_count=len(inventory),
        claimed_source_count=len(inventory & claimed),
        unclaimed_source_count=len(unclaimed),
        ranked=tuple(ranked),
    )
