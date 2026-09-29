"""Fixture censuses over the converted MIK-R22 fixture tree, for the MIK-R20 tests.

:func:`census_tree_files` extends the validator's converted fixture tree with a root route and four
more onboarding routes -- six routes in all -- and one census, ``wave-1``, whose inventory is taken
mechanically by :func:`build_inventory`. :func:`wave_one_files` adds the packet's conforming example:
42 claims (31 T, 4 F, 5 U, 2 P), seven per route, and every route ``migrated`` against one tree.
"""

from __future__ import annotations

import json
from typing import Any

from agents_remember.memory.knowledge_census import build_inventory
from agents_remember.memory_quality.knowledge_census import (
    baseline_path,
    claims_path,
    inventory_path,
    route_status_path,
)
from agents_remember.models.knowledge_files.census import CensusBaseline
from knowledge_validator_test_support import CODE_PATHS, encode, fixture_tree_files

CENSUS = "wave-1"
CODE_COMMIT = "c" * 40
MEMORY_COMMIT = "d" * 40
MIGRATED_TREE = "e" * 40
WORKTREES = "mcp/src/agents_remember/worktrees"
EXTRA_ROUTES = ("dashboard", "dashboard/src", "skills", "scripts")
ROUTES = (".", *EXTRA_ROUTES, WORKTREES)
EXTRA_SOURCES = {
    "dashboard/index.ts",
    "dashboard/src/app.ts",
    "skills/README.md",
    "scripts/sync.py",
    "unrouted.txt",
}
CENSUS_CODE_PATHS = frozenset(CODE_PATHS | EXTRA_SOURCES)
# One card per route: the artifact its claims are extracted from.
ROUTE_ARTIFACT = {
    ".": "onboarding/overview.md",
    "dashboard": "onboarding/dashboard/index.ts.md",
    "dashboard/src": "onboarding/dashboard/src/app.ts.md",
    "skills": "onboarding/skills/README.md.md",
    "scripts": "onboarding/scripts/sync.py.md",
    WORKTREES: "onboarding/mcp/src/agents_remember/worktrees/direct_landing.py.md",
}


def provenance(at: str = "2026-09-29T08:00:00+02:00", **owner: str) -> dict[str, str]:
    return {**(owner or {"wave": "260930-MIG-W1"}), "session": "curator-7", "at": at}


def assessment(verdict: str, *, at: str = "2026-09-29T08:00:00+02:00") -> dict[str, Any]:
    return {
        "verdict": verdict,
        "evidence": [{"targets": [{"kind": "external", "document": {"document": "code review"}}]}],
        "provenance": provenance(at),
    }


def claim(number: int, artifact: str, *verdicts: str, **fields: Any) -> dict[str, Any]:
    """A claim document; ``fields`` override ``kind``, ``applicability``, ``disposition`` and add
    ``records``."""

    return {
        "id": f"CLM-{number:06d}",
        "text": f"Legacy claim {number} holds.",
        "location": {"artifact": artifact, "lines": {"start": 3, "end": 4}},
        "kind": "current_behavior",
        "applicability": "assessable",
        "assessments": [assessment(verdict) for verdict in verdicts],
        "disposition": "pending",
        **fields,
    }


def claims_document(route: str, claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": "ar-census-claims/v1", "census": CENSUS, "route": route, "claims": claims}


def status_document(route: str, *entries: tuple[str, str]) -> dict[str, Any]:
    return {
        "schema": "ar-census-route/v1",
        "census": CENSUS,
        "route": route,
        "statuses": [
            {
                "status": status,
                "reason": f"{status} by wave 1",
                "tree": MIGRATED_TREE,
                "provenance": provenance(at),
            }
            for status, at in entries
        ],
    }


def census_tree_files() -> dict[str, bytes]:
    """The converted fixture tree with six onboarding routes and the pinned census ``wave-1``."""

    files = fixture_tree_files()
    for route in (".", *EXTRA_ROUTES):
        prefix = "onboarding" if route == "." else f"onboarding/{route}"
        files[f"{prefix}/overview.md"] = f"# {route}\n".encode()
    for route, artifact in ROUTE_ARTIFACT.items():
        files.setdefault(artifact, f"# {route} card\n".encode())
    baseline = CensusBaseline.model_validate(
        {
            "census": CENSUS,
            "code": {"commit": CODE_COMMIT},
            "memory": {"commit": MEMORY_COMMIT},
            "scope": [],
        }
    )
    inventory = build_inventory(CENSUS, code_paths=CENSUS_CODE_PATHS, memory_paths=files)
    files[baseline_path(CENSUS)] = encode(baseline.to_document())
    files[inventory_path(CENSUS)] = encode(inventory.to_document())
    return files


def wave_one_claims() -> dict[str, list[dict[str, Any]]]:
    """42 claims, seven per route: 31 T, 4 F, 5 U, 2 P."""

    verdicts = ["no_concern_found"] * 31 + ["concern_found"] * 4 + ["unresolved"] * 5 + [""] * 2
    by_route: dict[str, list[dict[str, Any]]] = {route: [] for route in ROUTES}
    for number, verdict in enumerate(verdicts, start=1):
        route = ROUTES[(number - 1) % len(ROUTES)]
        disposition = "discarded_false" if verdict == "concern_found" else "kept_as_prose"
        kind = "accepted_invariant" if number % 2 else "current_behavior"
        chosen = (verdict,) if verdict else ()
        by_route[route].append(
            claim(number, ROUTE_ARTIFACT[route], *chosen, kind=kind, disposition=disposition)
        )
    return by_route


def wave_one_files() -> dict[str, bytes]:
    files = census_tree_files()
    for route, claims in wave_one_claims().items():
        files[claims_path(CENSUS, route)] = encode(claims_document(route, claims))
        files[route_status_path(CENSUS, route)] = encode(
            status_document(
                route,
                ("in_progress", "2026-09-29T08:00:00+02:00"),
                ("migrated", "2026-09-29T09:00:00+02:00"),
            )
        )
    return files


def json_of(files: dict[str, bytes], path: str) -> dict[str, Any]:
    return json.loads(files[path])
