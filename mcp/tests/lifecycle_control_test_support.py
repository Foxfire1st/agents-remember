"""Public lifecycle-control helpers for tests that own a current generation."""

from pathlib import Path

from agents_remember.models.lifecycles.operation import (
    LifecycleOperationKind,
    LifecycleOperationProjection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    LifecycleControlAction,
    LifecycleControlCommand,
    control_operation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.worktree_contract import load_contract


def control_current_generation(
    contract_path: Path,
    kind: LifecycleOperationKind,
    action: LifecycleControlAction,
    *,
    intent_note: str = "exercise exact lifecycle generation",
) -> LifecycleOperationProjection:
    contract = load_contract(contract_path)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, kind))
    record = store.read()
    assert record is not None
    return control_operation(
        LifecycleControlCommand(
            admitted_contract=contract,
            admitted_location=require_matching_lifecycle_operation_location(contract),
            configured_authority=record.input.configPath,
            kind=kind,
            action=action,
            expected_generation=record.generation,
            intent_note=intent_note,
        )
    )


def cancel_current_generation(
    contract_path: Path,
    kind: LifecycleOperationKind,
) -> LifecycleOperationProjection:
    return control_current_generation(contract_path, kind, "cancel")
