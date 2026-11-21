"""Pure same-generation lifecycle resume transition."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord


def requeued_same_generation(record: LifecycleOperationRecord) -> LifecycleOperationRecord:
    """Advance one retained generation without replacing immutable accepted intent."""
    mutations = dict(record.mutationEvidence)
    mutation_history = dict(record.mutationHistory)
    for leg, evidence in record.mutationEvidence.items():
        if evidence.state == "reconciled-unchanged":
            mutation_history[leg] = [*mutation_history.get(leg, []), evidence]
            mutations[leg] = evidence.model_copy(
                update={
                    "state": "pre-mutation",
                    "before": None,
                    "observed": None,
                    "expectedOutputTree": None,
                }
            )
    return record.model_copy(
        update={
            "status": "running",
            "phase": "direct-preflight",
            "attempt": record.attempt + 1,
            "finishedAt": None,
            "failure": None,
            "guidance": None,
            "cancelRequested": False,
            "mutationEvidence": mutations,
            "mutationHistory": mutation_history,
            "currentCommand": "recover exact accepted generation",
        }
    )
