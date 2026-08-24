"""Ambient-authorized application boundary for contract-owned closeout doors."""

from __future__ import annotations

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
    admit_configured_contract,
    execute_configured_contract_operation,
    project_configured_contract_refusal,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.door import CloseoutDoorRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.ambient_seat import AmbientSeatError, resolve_ambient_seat
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.worktrees.integration.closeout_door_control import (
    DoorActor,
)
from agents_remember.worktrees.integration.closeout_door_control import (
    closeout_door_tool as apply_closeout_door,
)
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError


def closeout_door_tool(
    config: McpRuntimeConfig,
    request: CloseoutDoorRequest,
) -> dict[str, object]:
    """Resolve one canonical caller and apply the door-source operation."""

    configured = admit_configured_contract(config, request.contract_path)
    if isinstance(configured, ConfiguredContractRefused):
        return _door_configured_refusal(request, configured)
    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    try:
        caller = resolve_ambient_seat(catalog)
    except AmbientSeatError as exc:
        if exc.status != "ambient-seat-unavailable":
            raise CloseoutQueueError(
                exc.status,
                "the hosted closeout-door caller identity could not be resolved",
            ) from exc
        execution = execute_configured_contract_operation(
            configured,
            lambda: apply_closeout_door(
                config,
                request,
                actor=_declared_actor(request),
                admitted_contract=configured.contract,
            ),
        )
        return (
            _door_configured_refusal(request, execution)
            if isinstance(execution, ConfiguredContractRefused)
            else execution
        )
    document = caller.binding_task_document_ref
    if document is None:
        raise CloseoutQueueError(
            "ambient-seat-unbound", "closeout door callers require a canonical task document"
        )
    _refuse_hosted_declared_conflict(request, caller.binding_role, document)
    execution = execute_configured_contract_operation(
        configured,
        lambda: apply_closeout_door(
            config,
            request,
            actor=DoorActor(role=caller.binding_role, task_document_ref=document),
            admitted_contract=configured.contract,
        ),
    )
    return (
        _door_configured_refusal(request, execution)
        if isinstance(execution, ConfiguredContractRefused)
        else execution
    )


def _declared_actor(request: CloseoutDoorRequest) -> DoorActor:
    declared = request.caller
    if declared is None:
        raise CloseoutQueueError(
            "closeout-door-caller-required",
            "ambient closeout door callers must declare caller (role + task_document_ref)",
        )
    return DoorActor(role=declared.role, task_document_ref=declared.task_document_ref)


def _refuse_hosted_declared_conflict(
    request: CloseoutDoorRequest,
    seat_role: str,
    seat_document: TaskDocumentRef,
) -> None:
    declared = request.caller
    if declared is not None and (
        declared.role != seat_role or declared.task_document_ref != seat_document
    ):
        raise CloseoutQueueError(
            "closeout-door-caller-conflict",
            "declared caller conflicts with the plane-injected hosted seat",
        )


def _door_configured_refusal(
    request: CloseoutDoorRequest,
    refusal: ConfiguredContractRefused,
) -> dict[str, object]:
    projected = project_configured_contract_refusal(refusal, operation="closeout_door")
    allowed = {
        "status",
        "detail",
        "expected",
        "observed",
        "nextAction",
        "developerDecisionRequired",
        "decisionSurface",
        "lifecycleOperation",
        "lifecycleOperations",
    }
    result: dict[str, object] = {key: value for key, value in projected.items() if key in allowed}
    result.update(
        {
            "ok": False,
            "operation": "closeout_door",
            "action": request.action,
            "state": "refused",
            "status": str(projected.get("status") or projected.get("state") or refusal.status),
            "detail": str(projected.get("detail") or refusal.detail),
            "summary": str(
                projected.get("summary")
                or projected.get("detail")
                or "configured closeout-door authority refused"
            ),
            "contractPath": (
                refusal.contract_path.as_posix()
                if refusal.contract_path is not None
                else request.contract_path
            ),
            "generation": None,
            "projectionEffects": [],
        }
    )
    return result


__all__ = ["CloseoutDoorRequest", "closeout_door_tool"]
