"""Minimal synthetic candidate writer for sealed-cohort contract tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.testing.cohort_manifest import (
    COHORT_MANIFEST_PATH,
    COHORT_MANIFEST_SCHEMA,
    MAX_DIRECT_NODES,
    POLICY_VERSION,
)

_CONFIGURATION_PATHS = ("pyproject.toml", "mcp/tests/evidence-lifecycle.toml")


@dataclass(frozen=True)
class SyntheticCohortOptions:
    """Optional dependency/effect facts for one synthetic manifest."""

    local_imports: Mapping[str, Sequence[str]] = field(default_factory=dict)
    effects: Mapping[str, Sequence[str]] = field(default_factory=dict)
    effects_known: Mapping[str, bool] = field(default_factory=dict)
    closures: Mapping[str, Sequence[str]] = field(default_factory=dict)


def write_synthetic_direct_cohort(
    root: Path,
    nodes: Sequence[str],
    python_symbols: Mapping[str, Sequence[str]],
    options: SyntheticCohortOptions | None = None,
) -> None:
    """Write a content-sealed manifest for an explicit synthetic forcing case."""

    policy = options or SyntheticCohortOptions()
    lines = [
        f"schema_version = {json.dumps(COHORT_MANIFEST_SCHEMA)}",
        f"policy_version = {json.dumps(POLICY_VERSION)}",
        f"max_selection = {MAX_DIRECT_NODES}",
        "",
    ]
    for relative, symbols in python_symbols.items():
        payload = (root / relative).read_bytes()
        imports = policy.local_imports.get(relative, ())
        effect_families = policy.effects.get(relative, ())
        known = policy.effects_known.get(relative, True)
        lines.extend(
            (
                "[[python_file]]",
                f"path = {json.dumps(relative)}",
                f"sha256 = {json.dumps(hashlib.sha256(payload).hexdigest())}",
                f"symbols = {json.dumps(list(symbols))}",
                f"local_imports = {json.dumps(list(imports))}",
                f"effects_known = {json.dumps(known)}",
                f"effects = {json.dumps(list(effect_families))}",
                'purpose = "synthetic forcing-proof closure"',
                "",
            )
        )
    for relative in _CONFIGURATION_PATHS:
        payload = (root / relative).read_bytes()
        lines.extend(
            (
                "[[configuration]]",
                f"path = {json.dumps(relative)}",
                f"sha256 = {json.dumps(hashlib.sha256(payload).hexdigest())}",
                'purpose = "synthetic canonical configuration"',
                "",
            )
        )
    for node in nodes:
        closure = (
            tuple(
                f"{path}::{symbol}"
                for path, symbols in python_symbols.items()
                for symbol in symbols
            )
            if node not in policy.closures
            else tuple(policy.closures[node])
        )
        lines.extend(
            (
                "[[node]]",
                f"id = {json.dumps(node)}",
                f"closure = {json.dumps(list(closure))}",
                'rationale = "synthetic forcing-proof node"',
                "",
            )
        )
    path = root / COHORT_MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
