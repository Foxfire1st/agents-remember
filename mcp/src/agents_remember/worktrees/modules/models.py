from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

# Payload lists that scale with transported history (committed-range closeouts
# can carry hundreds of paths) are exposed as count + sample capped at this
# size; the full lists stay internal to the plans.
PATH_SAMPLE_LIMIT = 30


@dataclass(frozen=True)
class WorktreeCommandResult:
    returncode: int
    payload: dict[str, object]


@dataclass(frozen=True)
class WorktreeProviderSetupConfig:
    coordination_root: Path
    settings_path: Path
    seed_source_coordination_root: Path | None = None
    # True when settings_path is a temporary file whose lifetime must extend
    # into the background setup thread; the launcher then owns the unlink and
    # the controller must skip its own cleanup for a "starting" result.
    unlink_settings_after_setup: bool = False


class OnboardingRefreshPlan(TypedDict):
    """Sidecar onboarding refresh plan for a set of changed source files.

    ``missing`` and ``unsupported`` block closeout and are scoped to
    working-tree paths; ``unonboarded`` collects committed-range paths without
    existing onboarding — reported, never blocking, so transported history does
    not force whole-repository onboarding.
    """

    required: list[dict[str, str]]
    missing: list[str]
    unsupported: list[str]
    unonboarded: list[str]


class RouteOverviewRefreshPlan(TypedDict):
    """Route-overview verification-metadata refresh plan."""

    required: list[dict[str, str]]
    missing_metadata: list[str]


class SidecarBodyClassification(TypedDict):
    """Body/history classification of changed-source sidecars in the memory tree."""

    stale: list[str]
    untraced: list[str]
    attested_no_impact: list[str]


class RouteOverviewBodyClassification(TypedDict):
    """Body/history classification of route overviews matched by changed paths.

    Routes that are the nearest governor of a changed source path are
    domain-evident and classify as stale / untraced / attested_no_impact like
    sidecars; overviews matched only as ancestors are reported under
    ``stamped_without_body_review`` instead of gating closeout.
    """

    stale: list[str]
    untraced: list[str]
    attested_no_impact: list[str]
    stamped_without_body_review: list[str]


class EntityFingerprintRow(TypedDict):
    """One parsed row of the entity fingerprint catalog table."""

    line_index: int
    entity: str
    algorithm: str
    fingerprint: str
    evidence_paths: list[str]


class EntityFingerprintRequiredItem(TypedDict):
    """An entity whose fingerprint must be refreshed for the changed paths."""

    entity: str
    onboarding_file: str
    evidence_paths: list[str]
    affected_paths: list[str]


class EntityFingerprintRefreshPlan(TypedDict):
    """Entity fingerprint refresh plan: refreshable rows plus unsupported rows."""

    required: list[EntityFingerprintRequiredItem]
    unsupported: list[dict[str, str]]
