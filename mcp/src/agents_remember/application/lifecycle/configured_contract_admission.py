"""Closed admission for every public current-contract mutation route."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from agents_remember.application.memory_mode_refusal import (
    memory_mode_refusal_evidence,
    removed_memory_mode_fields_from_evidence,
)
from agents_remember.errors import (
    AuthorityError,
    ConfiguredContractAuthorityError,
    ConfiguredContractRereadError,
    MemoryModeUnsupportedError,
)
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.enclosure import LifecycleEnclosureLocator
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.worktrees.integration.configured_contract_authority import (
    require_configured_contract_repositories,
    require_configured_terminal_contract_repositories,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    TerminalCleanupContractAuthority,
    terminal_cleanup_contract_authority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    LifecycleOperationLocationError,
    inspect_lifecycle_operation_locator,
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
    "memory-mode-unsupported",
    "authority-invalid",
]


@dataclass(frozen=True)
class ConfiguredContractAccepted:
    """One strict contract bound to its configured immutable enclosure location."""

    contract_path: Path
    contract: WorktreeContract
    location: LifecycleOperationLocation


@dataclass(frozen=True)
class TerminalConfiguredContractAccepted:
    """One terminal locator bound to archived and surviving configured contract truth."""

    contract_path: Path
    contract: WorktreeContract
    locator: LifecycleEnclosureLocator
    authority: TerminalCleanupContractAuthority


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
TerminalConfiguredContractAdmission = (
    ConfiguredContractAccepted | TerminalConfiguredContractAccepted | ConfiguredContractRefused
)


def admit_configured_contract(
    config: McpRuntimeConfig,
    contract_path: str | Path,
    *,
    require_candidate_identity: bool = True,
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

    read = _read_confined_contract(confined, location)
    if isinstance(read, ConfiguredContractRefused):
        return read
    contract = read

    try:
        require_configured_contract_repositories(
            contract,
            config.config_path.as_posix(),
            require_candidate_identity=require_candidate_identity,
        )
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


def admit_configured_terminal_contract(
    config: McpRuntimeConfig,
    contract_path: str | Path,
) -> TerminalConfiguredContractAdmission:
    """Admit live authority normally or one exact terminal archive for final retry/status."""

    try:
        confined = require_within_coordination(config, str(contract_path), "contract_path")
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
    observation = inspect_lifecycle_operation_locator(
        config.coordination_root,
        confined,
    )
    if observation.state != "terminal-archived":
        return admit_configured_contract(config, confined)
    assert observation.locator is not None
    read = _read_terminal_surviving_contract(confined)
    if isinstance(read, ConfiguredContractRefused):
        return read
    contract = read
    try:
        authority = terminal_cleanup_contract_authority(
            config.coordination_root,
            contract,
            observation.locator,
        )
    except LifecycleOperationLocationError as error:
        return ConfiguredContractRefused(
            reason="location-invalid",
            status=error.status,
            detail=error.detail,
            expected=error.expected,
            observed=error.observed,
            contract_path=confined,
        )
    try:
        require_configured_terminal_contract_repositories(
            authority.archived_contract,
            config.config_path.as_posix(),
        )
    except ConfiguredContractAuthorityError as error:
        detail = "the archived terminal contract does not match configured repository authority"
        return ConfiguredContractRefused(
            reason="authority-invalid",
            status="terminal-archive-configured-authority-invalid",
            detail=detail,
            expected={
                "contractPath": confined.as_posix(),
                "repositoryAuthority": "configured",
            },
            observed=public_failure_evidence(
                stage="terminal-contract-authority",
                side=error.side,
                name=error.name,
                error_type=type(error).__name__,
                observed={"state": "mismatch"},
            ),
            contract_path=confined,
        )
    return TerminalConfiguredContractAccepted(
        contract_path=confined,
        contract=contract,
        locator=observation.locator,
        authority=authority,
    )


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


def _read_confined_contract(
    confined: Path,
    location: LifecycleOperationLocation,
) -> WorktreeContract | ConfiguredContractRefused:
    """Read one confined current-task contract, refusing with the shape the failure deserves.

    The removed memory mode is refused first and by its own reason. The generic clause below
    would report it as merely unreadable and drop the recorded value, the supported set and the
    route -- three facts the developer cannot reconstruct from "unreadable".
    """

    try:
        return load_contract(confined)
    except MemoryModeUnsupportedError as error:
        return _removed_memory_mode_refusal(error, confined)
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


def _read_terminal_surviving_contract(
    confined: Path,
) -> WorktreeContract | ConfiguredContractRefused:
    """Read the surviving contract of a terminal archive, on the same two-shape boundary."""

    try:
        return load_contract(confined)
    except MemoryModeUnsupportedError as error:
        return _removed_memory_mode_refusal(error, confined)
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        return ConfiguredContractRefused(
            reason="location-invalid",
            status="terminal-archive-contract-unreadable",
            detail="terminal retry requires readable surviving contract truth",
            expected={
                "contractPath": confined.as_posix(),
                "route": "terminal locator -> exact archive/receipt -> surviving contract",
            },
            observed=public_failure_evidence(
                stage="terminal-contract-read",
                side="contract",
                name=confined.name,
                error_type=type(error).__name__,
                observed={"state": "missing" if not confined.exists() else "unreadable"},
            ),
            contract_path=confined,
        )


def _removed_memory_mode_refusal(
    error: MemoryModeUnsupportedError,
    confined: Path,
) -> ConfiguredContractRefused:
    """Report a configured contract that records the removed memory mode.

    The admission boundary used to fold this into ``contract-unreadable``, which named the file
    but not the value, the supported set or the route -- the three facts packet R3 asks a
    developer to receive. It keeps its own reason so the projection publishes the refusal's
    typed status rather than an unreadable-contract status.
    """
    expected: dict[str, object] = {
        "contractPath": confined.as_posix(),
        "supported": list(error.supported),
        "route": "locator -> root manifest -> root journal",
    }
    if error.artifact is not None:
        expected["artifact"] = error.artifact
    return ConfiguredContractRefused(
        reason="memory-mode-unsupported",
        status=error.status,
        detail=error.detail,
        expected=expected,
        observed=public_failure_evidence(
            stage="contract-read",
            side="contract",
            name=confined.name,
            error_type=type(error).__name__,
            observed=memory_mode_refusal_evidence(error),
        ),
        contract_path=confined,
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


def execute_configured_contract_operation[ConfiguredContractOperationResult](
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

    if refusal.reason == "memory-mode-unsupported":
        # The refusal already carries the value, the supported set and the route, so it takes the
        # same developer-decision shape as every other complete refusal rather than the
        # unreadable-contract projection, which would drop all three. The three are also lifted
        # to the payload's top level, where the developer reads them without walking the
        # evidence block.
        decision = _developer_decision(
            operation=operation,
            status=refusal.status,
            detail=refusal.detail,
            expected=refusal.expected,
            observed=refusal.observed,
        )
        for key, value in (
            removed_memory_mode_fields_from_evidence(refusal.observed) or {}
        ).items():
            if key in {"requested", "supported", "remedies", "artifact"}:
                decision[key] = value
        return decision
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
