"""Apply candidate-bound no-impact decisions to onboarding body classifications."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from agents_remember.worktrees.modules.models import (
    RouteOverviewBodyClassification,
    SidecarBodyClassification,
)


@dataclass(frozen=True)
class OnboardingBodyGateEvidence:
    """Candidate evidence consumed together by one onboarding body gate."""

    memory_tree: Path | None = None
    memory_verified_commit: str = ""
    accepted_no_impact: frozenset[str] = frozenset()


def apply_sidecar_no_impact(
    classification: SidecarBodyClassification,
    accepted_sources: Collection[str],
) -> SidecarBodyClassification:
    stale, attested = _accept_unchanged(
        classification["stale"],
        classification["attested_no_impact"],
        accepted_sources,
    )
    return {
        "stale": stale,
        "untraced": classification["untraced"],
        "attested_no_impact": attested,
    }


def apply_route_no_impact(
    classification: RouteOverviewBodyClassification,
    accepted_routes: Collection[str],
) -> RouteOverviewBodyClassification:
    stale, attested = _accept_unchanged(
        classification["stale"],
        classification["attested_no_impact"],
        accepted_routes,
    )
    return {
        "stale": stale,
        "untraced": classification["untraced"],
        "attested_no_impact": attested,
        "stamped_without_body_review": classification["stamped_without_body_review"],
    }


def _accept_unchanged(
    stale: list[str],
    already_attested: list[str],
    accepted: Collection[str],
) -> tuple[list[str], list[str]]:
    accepted_set = set(accepted)
    newly_attested = [
        path for path in stale if path in accepted_set and path not in already_attested
    ]
    return (
        [path for path in stale if path not in accepted_set],
        [*already_attested, *newly_attested],
    )


__all__ = [
    "OnboardingBodyGateEvidence",
    "apply_route_no_impact",
    "apply_sidecar_no_impact",
]
