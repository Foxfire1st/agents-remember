"""Typed public recovery guidance for lifecycle successor journal cuts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agents_remember.models.lifecycles.operation import (
    LifecycleOperationInput,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.successor import LifecycleSuccessorPublicationIntent
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    operation_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    LifecycleSuccessorConflict,
    successor_publication_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


@dataclass(frozen=True)
class AcceptedSuccessorReplay:
    """Validated caller evidence and authorities for one accepted successor WAL."""

    operation_input: LifecycleOperationInput
    candidate_fingerprint: str
    dry_run: bool
    complete_publications: Callable[
        [WorktreeContract, LifecycleOperationStore, LifecycleOperationRecord],
        LifecycleOperationRecord,
    ]
    prove_publications: Callable[[WorktreeContract, LifecycleOperationRecord], None]
    launch_worker: Callable[[WorktreeContract, LifecycleOperationRecord], None]


def resume_accepted_revision_successor(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    intent: LifecycleSuccessorPublicationIntent,
    replay: AcceptedSuccessorReplay,
) -> LifecycleOperationProjection:
    """Converge the one accepted N+1 WAL before generic predecessor legality."""

    accepted = intent.successor
    if (
        replay.operation_input != accepted.input
        or replay.candidate_fingerprint != accepted.fingerprint
    ):
        raise LifecycleControlError(
            "lifecycle-successor-already-accepted",
            "this terminal generation already accepted one distinct successor",
            expected={
                "generation": accepted.generation,
                "fingerprint": accepted.fingerprint,
            },
            observed={
                "generation": intent.predecessor.generation + 1,
                "fingerprint": replay.candidate_fingerprint,
            },
            next_action="recover",
            next_args={
                "contract_path": contract.contract_path.as_posix(),
                "operation_kind": "closeout",
                "action": "recover",
                "expected_generation": accepted.generation,
                "intent_note": "<developer intent>",
                "dry_run": False,
            },
            next_tool="worktree_operation_control",
        )
    if replay.dry_run:
        return operation_projection(accepted, contract=contract)
    successor = store.complete_successor_publication()
    successor = replay.complete_publications(contract, store, successor)
    current_contract = load_contract(contract.contract_path)
    replay.prove_publications(current_contract, successor)
    replay.launch_worker(current_contract, successor)
    return operation_projection(store.read() or successor, contract=current_contract)


def accept_revision_successor(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    successor: LifecycleOperationRecord,
) -> LifecycleOperationRecord:
    """Atomically accept one immutable successor or classify the existing WAL."""

    try:
        return store.replace_terminal(successor)
    except LifecycleSuccessorConflict as exc:
        accepted = exc.intent.successor
        raise LifecycleControlError(
            "lifecycle-successor-already-accepted",
            "this terminal generation already accepted one distinct successor",
            expected={
                "generation": accepted.generation,
                "fingerprint": accepted.fingerprint,
            },
            observed={
                "generation": successor.generation,
                "fingerprint": exc.requested,
            },
            next_action="recover",
            next_args={
                "contract_path": contract.contract_path.as_posix(),
                "operation_kind": "closeout",
                "action": "recover",
                "expected_generation": accepted.generation,
                "intent_note": "<developer intent>",
                "dry_run": False,
            },
            next_tool="worktree_operation_control",
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise successor_publication_interrupted(store, exc) from exc


def successor_publication_interrupted(
    store: LifecycleOperationStore,
    error: Exception,
) -> LifecycleControlError:
    """Classify an interrupted N to N+1 journal transaction from durable bytes."""

    intent = store.read_successor_intent()
    current = store.read()
    if intent is None:
        return LifecycleControlError(
            "lifecycle-successor-publication-interrupted",
            "successor acceptance did not reach its durable journal intent",
            expected={"successorIntent": "durable-before-replacement"},
            observed={
                "generation": current.generation if current is not None else 0,
                "failure": public_failure_evidence(
                    stage="successor-intent-publication",
                    side="journal",
                    name=successor_publication_path(store.path).name,
                    error_type=type(error).__name__,
                    observed={"state": "interrupted"},
                ),
            },
            next_action="revise",
        )
    successor = intent.successor
    allowed = {
        "predecessorGeneration": intent.predecessor.generation,
        "successorGeneration": successor.generation,
        "successorFingerprint": successor.fingerprint,
    }
    observed = {
        "currentGeneration": current.generation if current is not None else 0,
        "currentFingerprint": current.fingerprint if current is not None else "",
        "failure": public_failure_evidence(
            stage="successor-record-publication",
            side="journal",
            name=store.path.name,
            error_type=type(error).__name__,
            observed={"state": "interrupted"},
        ),
    }
    exact_current = current is not None and current.fingerprint in {
        intent.predecessor.fingerprint,
        successor.fingerprint,
    }
    return LifecycleControlError(
        (
            "lifecycle-successor-publication-interrupted"
            if exact_current
            else "lifecycle-successor-publication-conflict"
        ),
        (
            "the accepted successor publication can resume from its exact journal intent"
            if exact_current
            else "current lifecycle bytes contradict the accepted successor publication"
        ),
        expected=allowed,
        observed=observed,
        next_action="recover" if exact_current else "developer-decision",
        next_tool="worktree_operation_control" if exact_current else None,
        next_args=(
            {
                "contract_path": successor.contractPath,
                "operation_kind": successor.operationKind,
                "action": "recover",
                "expected_generation": successor.generation,
                "intent_note": "<developer intent>",
                "dry_run": False,
            }
            if exact_current
            else None
        ),
    )
