"""Prepare and summarize the quality run's derived knowledge inputs, not gate judgments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_gate import GateResult
from agents_remember.application.knowledge_proofs import invariants_without_proof
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.route_index import RouteIndexBuildResult, build_missing_route_indexes
from agents_remember.memory_quality.curator_checklist import WithoutProof
from agents_remember.memory_quality.knowledge_worklist_section import worklist_summary


def build_route_indexes(
    *,
    code_root: Path,
    onboarding_root: Path,
    repository: str,
    storage: StorageSettings,
) -> RouteIndexBuildResult:
    """Build absent derived caches through their owner; keep existing stale bytes untouched."""
    try:
        return build_missing_route_indexes(
            code_root=code_root,
            onboarding_root=onboarding_root,
            repository=repository,
            storage=storage,
        )
    except FileNotFoundError as error:
        if not code_root.is_dir() or onboarding_root.exists():
            raise
        return RouteIndexBuildResult(
            routes=0,
            written=0,
            unchanged=0,
            indexes=[],
            stale_indexes=[],
            build_errors=[{"path": onboarding_root.as_posix(), "error": str(error)}],
        )


def without_proof(memory_root: Path, coordination_root: Path | None = None) -> WithoutProof | None:
    """The informational proof population of a converted tree, not an actionable finding."""
    coverage = invariants_without_proof(memory_root, coordination_root=coordination_root)
    if coverage is None:
        return None
    return WithoutProof(
        rows=tuple(row.as_row() for row in coverage.unproven), problem=coverage.problem
    )


def knowledge_briefs(
    worklist: tuple[dict[str, Any], str | None] | None, gate: GateResult | None
) -> dict[str, object]:
    """Summaries of the already computed worklist and gate, where they apply."""
    briefs: dict[str, object] = {}
    if worklist is not None:
        briefs["knowledgeWorklist"] = worklist_summary(*worklist)
    if gate is not None:
        briefs["knowledgeGate"] = gate.brief()
    return briefs
