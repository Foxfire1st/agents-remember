"""One builder for lifecycle-valid synthetic test-evidence catalogs."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from agents_remember_test_support.testing.evidence_lifecycle import CATALOG_SCHEMA


def write_synthetic_evidence_catalog(
    root: Path,
    artifacts: Mapping[str, Sequence[str]],
) -> Path:
    """Write exact metadata for the governed artifacts a synthetic repo created."""

    if not artifacts:
        raise ValueError("a synthetic evidence catalog requires at least one artifact")
    _write_observable_consumers(root, artifacts)
    lines = [
        f"schema_version = {json.dumps(CATALOG_SCHEMA)}",
        "large_fixture_bytes = 25000",
    ]
    first_path, first_consumers = next(iter(sorted(artifacts.items())))
    evidence_node = _first_test_node(root, first_consumers)
    lines.extend(
        (
            "",
            "[[contract]]",
            'id = "synthetic-test-evidence"',
            f"owner = {json.dumps(first_path)}",
            f"evidence_node = {json.dumps(evidence_node)}",
        )
    )
    for path, consumers in sorted(artifacts.items()):
        if not consumers:
            raise ValueError(f"synthetic artifact {path!r} requires a consumer")
        kind = "shared-support" if path.endswith(".py") else "fixture"
        lines.extend(
            (
                "",
                "[[artifact]]",
                f"path = {json.dumps(path)}",
                f"kind = {json.dumps(kind)}",
                'authority = "internal-canonical"',
                'owner = "synthetic-test-evidence"',
                'category = "unit-regression"',
                'fidelity = "in-process"',
                'cadence = "affected"',
                'source_version_or_generator = "repository test builder"',
                'introduced_by = "synthetic-test"',
                'lifetime = "permanent"',
                'permanence_rationale = "Required input to the synthetic contract."',
                'replacement_contract = "contract:synthetic-test-evidence"',
                'consumer_scope = "exact"',
                f"consumers = {json.dumps(list(consumers))}",
            )
        )
    catalog = root / "mcp/tests/evidence-lifecycle.toml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return catalog


def _write_observable_consumers(
    root: Path,
    artifacts: Mapping[str, Sequence[str]],
) -> None:
    """Make synthetic catalog consumers independently observable from their source."""

    by_consumer: dict[str, set[str]] = {}
    for artifact, consumers in artifacts.items():
        for consumer in consumers:
            by_consumer.setdefault(consumer, set()).add(artifact)
    for consumer, owned_artifacts in sorted(by_consumer.items()):
        path = root / consumer
        source = path.read_text(encoding="utf-8")
        rendered = repr(tuple(sorted(owned_artifacts)))
        path.write_text(
            source
            + "\nfrom pathlib import Path as _EvidencePath\n"
            + f"_AR_EVIDENCE_INPUTS = {rendered}\n"
            + "\n"
            + "def _read_ar_evidence_inputs() -> tuple[bytes, ...]:\n"
            + "    return tuple(_EvidencePath(value).read_bytes() for value in "
            + "_AR_EVIDENCE_INPUTS)\n",
            encoding="utf-8",
        )


def _first_test_node(root: Path, consumers: Sequence[str]) -> str:
    for consumer in consumers:
        path = root / consumer
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                return f"{consumer}::{node.name}"
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        child.name.startswith("test_")
                    ):
                        return f"{consumer}::{node.name}::{child.name}"
    raise ValueError("synthetic evidence requires one existing executable consumer test node")
