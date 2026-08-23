"""Closed admission for every public current-contract mutation route."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from agents_remember.errors import (
    AuthorityError,
    ConfiguredContractAuthorityError,
    ConfiguredContractRereadError,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.worktrees.integration.configured_contract_authority import (
    require_configured_contract_repositories,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    LifecycleOperationLocationError,
    require_contract_matches_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

from .lifecycle_operation_location import (
    LifecycleOperationPublicAddress,
    configured_lifecycle_operation_location,
    observe_contract_read_failure,
    operation_address_projections,
    primary_operation_projection,
)

ConfiguredContractRefusalReason = Literal[
    "address-invalid",
    "location-invalid",
    "contract-unreadable",
    "authority-invalid",
]


@dataclass(frozen=True)
class ConfiguredContractAccepted:
    """One strict contract bound to its configured immutable enclosure location."""

    contract_path: Path
    contract: WorktreeContract
    location: LifecycleOperationLocation


@dataclass(frozen=True)
class ConfiguredContractRefused:
    """One finite admission refusal containing only bounded public evidence."""

    reason: ConfiguredContractRefusalReason
    status: str
    detail: str
    expected: dict[str, object]
    observed: dict[str, object]
    contract_path: Path | None = None
    location: LifecycleOperationLocation | None = None


ConfiguredContractAdmission = ConfiguredContractAccepted | ConfiguredContractRefused
ConfiguredContractOperationResult = TypeVar("ConfiguredContractOperationResult")


def admit_configured_contract(
    config: McpRuntimeConfig,
    contract_path: str | Path,
) -> ConfiguredContractAdmission:
    """Resolve, read, and cross-check one configured current task contract."""

    try:
        confined, location = configured_lifecycle_operation_location(config, contract_path)
    except AuthorityError as error:
        detail = "the configured contract address is outside coordination authority"
        return ConfiguredContractRefused(
            reason="address-invalid",
            status="configured-contract-address-invalid",
            detail=detail,
            expected={"contractAddress": "confined under coordinationRoot"},
            observed=public_failure_evidence(
                stage="contract-address",
                side="contract",
                name="contract_path",
                error_type=type(error).__name__,
                observed={"state": "invalid"},
            ),
        )
    except LifecycleOperationLocationError as error:
        return ConfiguredContractRefused(
            reason="location-invalid",
            status=error.status,
            detail=error.detail,
            expected=error.expected,
            observed=error.observed,
        )

    try:
        contract = load_contract(confined)
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        state = "missing" if isinstance(error, FileNotFoundError) else "unreadable"
        return ConfiguredContractRefused(
            reason="contract-unreadable",
            status="configured-contract-unreadable",
            detail="the canonical configured task contract is missing or unreadable",
            expected={
                "contractPath": confined.as_posix(),
                "route": "locator -> root manifest -> root journal",
            },
            observed=public_failure_evidence(
                stage="contract-read",
                side="contract",
                name=confined.name,
                error_type=type(error).__name__,
                observed={"state": state},
            ),
            contract_path=confined,
            location=location,
        )

    try:
        require_configured_contract_repositories(contract, config.config_path.as_posix())
    except ConfiguredContractAuthorityError as error:
        return configured_authority_refusal(
            ConfiguredContractAccepted(confined, contract, location), error
        )
    try:
        require_contract_matches_lifecycle_operation_location(contract, location)
    except LifecycleOperationLocationError as error:
        return ConfiguredContractRefused(
            reason="location-invalid",
            status=error.status,
            detail=error.detail,
            expected=error.expected,
            observed=error.observed,
            contract_path=confined,
            location=location,
        )
    return ConfiguredContractAccepted(confined, contract, location)


def configured_authority_refusal(
    accepted: ConfiguredContractAccepted,
    error: ConfiguredContractAuthorityError,
) -> ConfiguredContractRefused:
    """Classify a mutation-time configured-authority reread without raw detail."""

    detail = "the canonical task contract does not match configured repository authority"
    return ConfiguredContractRefused(
        reason="authority-invalid",
        status="configured-contract-authority-invalid",
        detail=detail,
        expected={
            "contractPath": accepted.contract_path.as_posix(),
            "repositoryAuthority": "configured",
        },
        observed=public_failure_evidence(
            stage="contract-authority",
            side=error.side,
            name=error.name,
            error_type=type(error).__name__,
            observed={"state": "mismatch"},
        ),
        contract_path=accepted.contract_path,
        location=accepted.location,
    )


def configured_contract_reread_refusal(
    accepted: ConfiguredContractAccepted,
    error: ConfiguredContractRereadError,
) -> ConfiguredContractRefused:
    """Classify one named mutation-time reread failure against prior admission."""

    return ConfiguredContractRefused(
        reason=cast(ConfiguredContractRefusalReason, error.reason),
        status=error.status,
        detail=error.detail,
        expected=error.expected,
        observed=error.observed,
        contract_path=accepted.contract_path,
        location=accepted.location,
    )


def execute_configured_contract_operation(
    accepted: ConfiguredContractAccepted,
    execute: Callable[[], ConfiguredContractOperationResult],
) -> ConfiguredContractOperationResult | ConfiguredContractRefused:
    """Execute one admitted operation and close only its typed reread failure."""

    try:
        return execute()
    except ConfiguredContractRereadError as error:
        return configured_contract_reread_refusal(accepted, error)


def project_configured_contract_refusal(
    refusal: ConfiguredContractRefused,
    *,
    operation: str,
    address: LifecycleOperationPublicAddress | None = None,
) -> dict[str, Any]:
    """Project one semantic refusal without re-reading or inferring authority."""

    if refusal.reason == "contract-unreadable":
        return _project_unreadable_contract(refusal, operation=operation, address=address)
    if refusal.reason == "location-invalid":
        return {
            "ok": False,
            "operation": operation,
            "state": refusal.status,
            "status": refusal.status,
            "summary": refusal.detail,
            "detail": refusal.detail,
            "expected": refusal.expected,
            "observed": refusal.observed,
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": refusal.detail,
        }
    status = refusal.status
    expected = dict(refusal.expected)
    observed = dict(refusal.observed)
    if refusal.reason == "authority-invalid" and address is not None:
        status = f"{address.kind}-contract-invalid"
        expected["operationKind"] = address.kind
        if address.generation is not None:
            expected["generation"] = address.generation
    return _developer_decision(
        operation=operation,
        status=status,
        detail=refusal.detail,
        expected=expected,
        observed=observed,
    )


def _project_unreadable_contract(
    refusal: ConfiguredContractRefused,
    *,
    operation: str,
    address: LifecycleOperationPublicAddress | None,
) -> dict[str, Any]:
    location = refusal.location
    assert location is not None
    observation = observe_contract_read_failure(location, refusal.observed)
    if address is None:
        if observation.decision is not None:
            return {"operation": operation, **observation.decision}
        result = _developer_decision(
            operation=operation,
            status="configured-contract-unreadable",
            detail=refusal.detail,
            expected=refusal.expected,
            observed=refusal.observed,
        )
        result["lifecycleOperations"] = [
            item.model_dump(mode="json", exclude_none=True) for item in observation.operations
        ]
        return result

    matching = operation_address_projections(list(observation.operations), address)
    projection = primary_operation_projection(list(matching))
    if projection is not None and isinstance(projection.result, dict):
        return _project_operation_decision(operation, projection)
    if not observation.operations and observation.decision is not None:
        return {"operation": operation, **observation.decision}
    detail = "the canonical task contract is unreadable for this operation"
    expected: dict[str, object] = {
        "contractPath": location.contract_path.as_posix(),
        "operationKind": address.kind,
    }
    if address.generation is not None:
        expected["generation"] = address.generation
    return _developer_decision(
        operation=operation,
        status=f"{address.kind}-contract-invalid",
        detail=detail,
        expected=expected,
        observed=refusal.observed,
    )


def _project_operation_decision(
    operation: str,
    projection: LifecycleOperationProjection,
) -> dict[str, Any]:
    decision = projection.result
    assert isinstance(decision, dict)
    result: dict[str, Any] = {
        "ok": False,
        "operation": operation,
        "state": "refused",
        "status": decision["state"],
        "detail": decision["decisionSurface"],
        **{key: decision[key] for key in ("nextAction", "expected", "observed")},
        "lifecycleOperation": projection.model_dump(mode="json", exclude_none=True),
    }
    if decision.get("developerDecisionRequired") is True:
        result.update(
            developerDecisionRequired=True,
            decisionSurface=decision["decisionSurface"],
        )
    return result


def _developer_decision(
    *,
    operation: str,
    status: str,
    detail: str,
    expected: dict[str, object],
    observed: dict[str, object],
) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "state": "refused",
        "status": status,
        "detail": detail,
        "expected": expected,
        "observed": observed,
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
        "decisionSurface": detail,
    }
