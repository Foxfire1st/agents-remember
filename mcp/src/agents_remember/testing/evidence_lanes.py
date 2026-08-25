"""Executable test-evidence categories and cadence routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest

from agents_remember.testing.evidence_lifecycle import (
    EvidenceCategory,
    EvidenceFidelity,
    EvidenceLifecycleError,
    load_evidence_inventory,
)


class EvidenceTrigger(StrEnum):
    """Why an evidence population is running."""

    AFFECTED = "affected"
    PROVIDER_BUMP = "provider-bump"
    SCHEDULED = "scheduled"
    MIGRATION_WINDOW = "migration-window"
    RELEASE = "release"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True)
class EvidenceLane:
    """One category's authority and default execution cadence."""

    category: EvidenceCategory
    marker: str | None
    authority: str
    fidelity: EvidenceFidelity
    expected_lifetime: str
    triggers: frozenset[EvidenceTrigger]


EVIDENCE_LANES = (
    EvidenceLane(
        EvidenceCategory.UNIT_REGRESSION,
        None,
        "owned product behavior",
        EvidenceFidelity.IN_PROCESS,
        "permanent while the owned behavior remains supported",
        frozenset({EvidenceTrigger.AFFECTED, EvidenceTrigger.RELEASE}),
    ),
    EvidenceLane(
        EvidenceCategory.PUBLIC_CONTRACT,
        "evidence_contract",
        "supported public port or wire contract",
        EvidenceFidelity.PUBLIC_BOUNDARY,
        "permanent while the public contract remains supported",
        frozenset({EvidenceTrigger.AFFECTED, EvidenceTrigger.RELEASE}),
    ),
    EvidenceLane(
        EvidenceCategory.INTEGRATION,
        "evidence_integration",
        "real composition or local process boundary",
        EvidenceFidelity.LOCAL_COMPOSITION,
        "permanent while the composed boundary remains supported",
        frozenset({EvidenceTrigger.AFFECTED, EvidenceTrigger.RELEASE}),
    ),
    EvidenceLane(
        EvidenceCategory.ARCHITECTURE_FITNESS,
        "fitness",
        "repository architecture contract",
        EvidenceFidelity.REPOSITORY_STRUCTURE,
        "permanent while the architecture rule remains active",
        frozenset({EvidenceTrigger.AFFECTED, EvidenceTrigger.RELEASE}),
    ),
    EvidenceLane(
        EvidenceCategory.PROVIDER_CONFORMANCE,
        "evidence_provider",
        "independent provider recording or specification",
        EvidenceFidelity.INDEPENDENT_BOUNDARY,
        "versioned to the supported provider or protocol shape",
        frozenset(
            {EvidenceTrigger.AFFECTED, EvidenceTrigger.PROVIDER_BUMP, EvidenceTrigger.RELEASE}
        ),
    ),
    EvidenceLane(
        EvidenceCategory.STRESS_DURABILITY,
        "evidence_stress",
        "bounded process, load, or historical sensitivity evidence",
        EvidenceFidelity.PROCESS_RACE,
        "permanent while the durability or race contract remains supported",
        frozenset({EvidenceTrigger.SCHEDULED, EvidenceTrigger.RELEASE}),
    ),
    EvidenceLane(
        EvidenceCategory.MIGRATION,
        "evidence_migration",
        "temporary migration owner named by the lifecycle catalog",
        EvidenceFidelity.TRANSITION_COMPARISON,
        "temporary until its executable replacement is verified",
        frozenset(
            {EvidenceTrigger.AFFECTED, EvidenceTrigger.MIGRATION_WINDOW, EvidenceTrigger.RELEASE}
        ),
    ),
    EvidenceLane(
        EvidenceCategory.DIAGNOSTIC,
        None,
        "non-accepting exact-node direct diagnostic route",
        EvidenceFidelity.EXACT_NODE_DIAGNOSTIC,
        "invocation-local and never durable acceptance evidence",
        frozenset({EvidenceTrigger.DIAGNOSTIC}),
    ),
)

LANE_BY_CATEGORY = {lane.category: lane for lane in EVIDENCE_LANES}
LANE_BY_MARKER = {lane.marker: lane for lane in EVIDENCE_LANES if lane.marker is not None}
PROVIDER_GATE_MARKERS = frozenset(
    {
        "ar_run_pi_rpc_smoke",
        "ar_run_control_plane_installed",
        "ar_run_control_installed",
        "ar_run_evidence_installed",
        "ar_claude_stream_smoke",
        "ar_codex_app_server_live_smoke",
        "ar_codex_app_server_live_conformance",
        "agents_remember_real_mcp_config",
    }
)


def validate_lane_registry(lanes: tuple[EvidenceLane, ...] = EVIDENCE_LANES) -> None:
    """Refuse incomplete or ambiguous executable lane configuration."""

    categories = [lane.category for lane in lanes]
    if len(categories) != len(set(categories)):
        raise pytest.UsageError("evidence lane categories must be unique")
    missing = set(EvidenceCategory) - set(categories)
    if missing:
        raise pytest.UsageError(
            f"evidence lane registry is missing categories {sorted(item.value for item in missing)}"
        )
    markers = [lane.marker for lane in lanes if lane.marker is not None]
    if len(markers) != len(set(markers)):
        raise pytest.UsageError("evidence lane markers must be unique")


def expression_for(trigger: EvidenceTrigger) -> str | None:
    """Pytest marker expression for one non-overlapping cadence trigger."""

    if trigger is EvidenceTrigger.AFFECTED:
        return "not evidence_stress"
    if trigger is EvidenceTrigger.PROVIDER_BUMP:
        return "evidence_provider"
    if trigger is EvidenceTrigger.SCHEDULED:
        return "evidence_stress"
    if trigger is EvidenceTrigger.MIGRATION_WINDOW:
        return "evidence_migration"
    if trigger is EvidenceTrigger.DIAGNOSTIC:
        raise ValueError("diagnostic evidence uses exact-node selection, not a marker expression")
    return None


def category_for_item(item: pytest.Item) -> EvidenceCategory:
    """Resolve exactly one category, using ordinary regression as the default."""

    explicit = {name for name in LANE_BY_MARKER if item.get_closest_marker(name) is not None}
    if len(explicit) > 1:
        raise pytest.UsageError(
            f"{item.nodeid}: conflicting evidence categories {sorted(explicit)}"
        )
    provider_gates = {
        name for name in PROVIDER_GATE_MARKERS if item.get_closest_marker(name) is not None
    }
    if provider_gates:
        if explicit and explicit != {"evidence_provider"}:
            raise pytest.UsageError(
                f"{item.nodeid}: provider-gated evidence conflicts with {sorted(explicit)}"
            )
        return EvidenceCategory.PROVIDER_CONFORMANCE
    if explicit:
        return LANE_BY_MARKER[explicit.pop()].category
    return EvidenceCategory.UNIT_REGRESSION


def pytest_configure(config: pytest.Config) -> None:
    """Register category markers from the canonical lane registry itself."""

    validate_lane_registry()
    for lane in EVIDENCE_LANES:
        if lane.marker is not None:
            config.addinivalue_line("markers", f"{lane.marker}: {lane.authority}")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Validate category ownership once and expose it in every report record."""

    provider_consumers = _provider_consumers(Path(str(config.rootpath)))
    for item in items:
        try:
            relative = Path(item.path).resolve().relative_to(Path(str(config.rootpath)).resolve())
        except ValueError as error:
            raise pytest.UsageError(
                f"test item is outside the candidate root: {item.nodeid}"
            ) from error
        if relative.as_posix() in provider_consumers:
            item.add_marker("evidence_provider")
        category = category_for_item(item)
        item.user_properties.append(("arEvidenceCategory", category.value))


def _provider_consumers(project_root: Path) -> frozenset[str]:
    try:
        inventory = load_evidence_inventory(project_root)
    except EvidenceLifecycleError as error:
        raise pytest.UsageError(str(error)) from error
    return frozenset(
        consumer
        for artifact in inventory.artifacts
        if artifact.category is EvidenceCategory.PROVIDER_CONFORMANCE
        for consumer in artifact.consumers
    )
