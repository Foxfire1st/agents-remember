"""Configured application authority for exact task-owned operation journal locations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationKind,
    LifecycleOperationProjection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    LifecycleOperationLocationError,
    resolve_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    unreadable_contract_operation_projections,
)


@dataclass(frozen=True)
class LifecycleOperationPublicAddress:
    """Bound the task-facing operation identity used by unreadable refusals."""

    operation: str
    kind: LifecycleOperationKind
    generation: int | None = None


class LocationDecisionPayload(TypedDict):
    """One bounded public decision emitted by the canonical locator owner."""

    ok: Literal[False]
    state: str
    status: str
    summary: str
    detail: str
    expected: dict[str, object]
    observed: dict[str, object]
    nextAction: Literal["developer-decision"]
    developerDecisionRequired: Literal[True]
    decisionSurface: str


@dataclass(frozen=True)
class ContractReadOperationObservation:
    """Retained strict journals or the exact addressable-publication contradiction."""

    operations: tuple[LifecycleOperationProjection, ...]
    decision: LocationDecisionPayload | None


def configured_lifecycle_operation_location(
    config: McpRuntimeConfig,
    contract_path: str | Path,
) -> tuple[Path, LifecycleOperationLocation]:
    """Bind a confined contract path to exactly one configured repository task root."""

    confined = require_within_coordination(config, str(contract_path), "contract_path")
    return confined, resolve_lifecycle_operation_location(
        config.coordination_root,
        confined,
    )


def location_decision_payload(error: LifecycleOperationLocationError) -> LocationDecisionPayload:
    return {
        "ok": False,
        "state": error.status,
        "status": error.status,
        "summary": error.detail,
        "detail": error.detail,
        "expected": error.expected,
        "observed": error.observed,
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
        "decisionSurface": error.detail,
    }


def unreadable_status_operations(
    config: McpRuntimeConfig,
    status_result: dict[str, Any],
    contract_path: Path,
    read_failure: dict[str, object],
) -> list[LifecycleOperationProjection]:
    """Project strict journals through the immutable locator when contract bytes fail."""

    try:
        _, location = configured_lifecycle_operation_location(config, contract_path)
    except LifecycleOperationLocationError as error:
        status_result.update(location_decision_payload(error))
        return []
    observation = observe_contract_read_failure(
        location,
        read_failure,
    )
    if observation.decision is not None:
        status_result.update(observation.decision)
    return list(observation.operations)


def observe_contract_read_failure(
    location: LifecycleOperationLocation,
    read_failure: Mapping[str, object],
) -> ContractReadOperationObservation:
    """Prefer retained journals, then preserve proven initial-contract authority loss."""

    error_type = read_failure.get("errorType")
    name = read_failure.get("name")
    operations = tuple(
        unreadable_contract_operation_projections(
            location,
            error_type=error_type if isinstance(error_type, str) else "ContractError",
            name=name if isinstance(name, str) else location.contract_path.name,
        )
    )
    if operations:
        return ContractReadOperationObservation(operations, None)
    detail = "the addressable enclosure's proven initial contract is missing or unreadable"
    locator = location.locator
    manifest = location.manifest
    assert locator.state == "addressable"
    proven_initial = locator.provenInitialContractSha256
    assert proven_initial == locator.expectedInitialContractSha256
    observed_state = _contract_read_state(read_failure)
    decision: LocationDecisionPayload = {
        "ok": False,
        "state": "operation-contract-publication-lost",
        "status": "operation-contract-publication-lost",
        "summary": detail,
        "detail": detail,
        "expected": {
            "contractPath": location.contract_path.resolve(strict=False).as_posix(),
            "route": "locator -> root manifest -> root journal",
            "publicationState": locator.state,
            "locatorId": locator.locatorId,
            "publicationRequestId": locator.publicationRequestId,
            "bindingFingerprint": locator.bindingFingerprint,
            "expectedInitialContractSha256": locator.expectedInitialContractSha256,
            "provenInitialContractSha256": proven_initial,
            "manifestInitialContractSha256": manifest.initialContractSha256,
        },
        "observed": {
            "stage": "contract-read",
            "side": "contract",
            "name": name if isinstance(name, str) else location.contract_path.name,
            "errorType": error_type if isinstance(error_type, str) else "ContractError",
            "state": observed_state,
        },
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
        "decisionSurface": detail,
    }
    return ContractReadOperationObservation((), decision)


def _contract_read_state(read_failure: Mapping[str, object]) -> Literal["missing", "unreadable"]:
    observed = read_failure.get("observed")
    if isinstance(observed, Mapping) and observed.get("state") == "missing":
        return "missing"
    return "unreadable"


def configured_unreadable_operation_projections(
    config: McpRuntimeConfig,
    contract_path: Path,
    *,
    error_type: str,
    name: str,
) -> list[LifecycleOperationProjection]:
    """Resolve the configured locator before reading retained strict journals."""

    _, location = configured_lifecycle_operation_location(config, contract_path)
    return unreadable_contract_operation_projections(
        location,
        error_type=error_type,
        name=name,
    )


def primary_operation_projection(
    projections: list[LifecycleOperationProjection],
) -> LifecycleOperationProjection | None:
    """Select the same one-operation context surface without hiding read failures."""

    unreadable = [item for item in projections if item.status == "unreadable"]
    if unreadable:
        return min(unreadable, key=lambda item: item.kind)
    active = [
        item
        for item in projections
        if item.status in {"queued", "running", "input-required", "termination-required"}
    ]
    candidates = active or projections
    return (
        max(
            candidates,
            key=lambda item: (
                item.startedAt or item.finishedAt or "",
                item.generation or 0,
                item.kind,
            ),
        )
        if candidates
        else None
    )


def operation_address_projections(
    projections: list[LifecycleOperationProjection],
    address: LifecycleOperationPublicAddress,
) -> list[LifecycleOperationProjection]:
    """Select one kind while retaining an unreadable journal with unknown generation."""

    return [
        item
        for item in projections
        if item.kind == address.kind
        and (
            item.status == "unreadable"
            or address.generation is None
            or item.generation == address.generation
        )
    ]


def unreadable_operation_refusal(
    config: McpRuntimeConfig,
    contract_path: Path,
    address: LifecycleOperationPublicAddress,
    error: Exception,
) -> dict[str, Any]:
    """Translate one unreadable contract through retained locator-owned authority."""

    try:
        projections = configured_unreadable_operation_projections(
            config,
            contract_path,
            error_type=type(error).__name__,
            name=contract_path.name,
        )
    except LifecycleOperationLocationError as location_error:
        return {"operation": address.operation, **location_decision_payload(location_error)}
    matching = operation_address_projections(projections, address)
    projection = primary_operation_projection(matching)
    decision = projection.result if projection is not None else None
    if not isinstance(decision, dict) or "decisionSurface" not in decision:
        detail = "the canonical task contract is unreadable for this operation"
        decision = {
            "state": f"{address.kind}-contract-invalid",
            "developerDecisionRequired": True,
            "decisionSurface": detail,
            "nextAction": "developer-decision",
            "expected": {
                "contractPath": contract_path.as_posix(),
                "operationKind": address.kind,
                **({"generation": address.generation} if address.generation is not None else {}),
            },
            "observed": {
                "stage": "contract-read",
                "side": "contract",
                "name": contract_path.name,
                "errorType": type(error).__name__,
            },
        }
    result: dict[str, Any] = {
        "ok": False,
        "operation": address.operation,
        "state": "refused",
        "status": decision["state"],
        "detail": decision["decisionSurface"],
        **{key: decision[key] for key in ("nextAction", "expected", "observed")},
    }
    if decision.get("developerDecisionRequired") is True:
        result.update(
            {
                "developerDecisionRequired": True,
                "decisionSurface": decision["decisionSurface"],
            }
        )
    if projection is not None:
        result["lifecycleOperation"] = projection.model_dump(mode="json", exclude_none=True)
    return result
