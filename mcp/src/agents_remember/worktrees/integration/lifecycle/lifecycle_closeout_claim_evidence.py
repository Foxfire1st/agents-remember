"""Small immutable evidence helpers shared by closeout claim generation changes."""

from __future__ import annotations

from agents_remember.models.lifecycles.door import (
    CloseoutDoorGeneration,
    DoorPublicationEvidence,
)
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    LifecycleOperationRecord,
)


def closeout_preview_args(operation_input: CloseoutOperationInput) -> dict[str, object]:
    args: dict[str, object] = {"contract_path": operation_input.contractPath}
    for leg, field in (
        ("code", "code_commit_message"),
        ("memory", "memory_commit_message"),
        ("ledger", "ledger_commit_message"),
    ):
        accepted = getattr(operation_input.effectiveInput, leg)
        if accepted.state == "enabled":
            args[field] = accepted.message
    return args


def claimed_predecessor_for_waiting_successor(
    current: LifecycleOperationRecord,
    successor: CloseoutDoorGeneration | None,
) -> DoorPublicationEvidence | None:
    """Select the one claimed lineage cell moved to history by cancel publication."""

    if successor is None or successor.disposition != "waiting":
        return None
    matches = [
        publication
        for publication in current.doorPublicationHistory
        if publication.state == "proven"
        and publication.generation.disposition == "claimed"
        and publication.generation.generationId == successor.predecessorGenerationId
    ]
    return matches[0] if len(matches) == 1 else None


__all__ = ["claimed_predecessor_for_waiting_successor", "closeout_preview_args"]
