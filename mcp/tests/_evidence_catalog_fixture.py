"""One builder for lifecycle-valid synthetic test-evidence catalogs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from agents_remember.testing.evidence_lifecycle import CATALOG_SCHEMA


def write_synthetic_evidence_catalog(
    root: Path,
    artifacts: Mapping[str, Sequence[str]],
) -> Path:
    """Write exact metadata for the governed artifacts a synthetic repo created."""

    if not artifacts:
        raise ValueError("a synthetic evidence catalog requires at least one artifact")
    lines = [
        f"schema_version = {json.dumps(CATALOG_SCHEMA)}",
        "large_fixture_bytes = 25000",
    ]
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
                f"consumers = {json.dumps(list(consumers))}",
            )
        )
    catalog = root / "mcp/tests/evidence-lifecycle.toml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return catalog
