"""Behavioral store and retained-integration invariants for lifecycle journals."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.lifecycle_operation_worker import OperationRuntime
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees.integration.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle_operations import (
    cancel_operation,
    start_or_observe_operation,
)
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.worktree_contract import WorktreeContract
from closeout_fixture_test_support import selected_fixture
from closeout_input_test_support import (
    closeout_operation_input,
    start_closeout_operation,
    with_commit_proven,
    with_mutation_intent,
)
from pydantic import ValidationError
from test_closeout_queue import MASTER_A
from test_lifecycle_operations import _contract, _integration_ready


def _closeout_store(root: Path) -> tuple[WorktreeContract, LifecycleOperationStore]:
    contract = _contract(root)
    start_closeout_operation(closeout_operation_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    return contract, store


def _external_store(root: Path) -> LifecycleOperationStore:
    fixture = selected_fixture(root, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    operation_input = closeout_operation_input(contract, config_path=fixture.config_path)
    start_closeout_operation(operation_input, launcher=lambda *_: None)
    return LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))


def _snapshot(seed: str) -> GitMutationSnapshot:
    return GitMutationSnapshot(
        headRef="refs/heads/test-closeout",
        head=seed * 40,
        headTree="b" * 40,
        refLogFingerprint=seed * 64,
        indexTree="b" * 40,
        candidateTree="c" * 40,
        statusFingerprint="d" * 64,
    )


def test_store_reads_one_schema_and_revalidates_every_update(tmp_path: Path) -> None:
    _contract_value, store = _closeout_store(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["agentSelectedJobId"] = "forbidden"
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid lifecycle operation record"):
        store.read()

    _contract_value, store = _closeout_store(tmp_path / "invalid-status")
    with pytest.raises(ValidationError):
        store.update(lambda record: record.model_copy(update={"status": "willy-nilly"}))


def test_recovery_claim_is_single_writer_and_preserves_durable_input(tmp_path: Path) -> None:
    _contract_value, store = _closeout_store(tmp_path)
    store.update(
        lambda record: record.model_copy(
            update={"status": "failed", "phase": "failed", "finishedAt": "2026-08-22T00:00Z"}
        )
    )

    def requeue(record):
        return record.model_copy(
            update={"status": "queued", "phase": "queued", "attempt": record.attempt + 1}
        )

    first, claimed = store.replace_for_recovery(requeue, expected_attempt=1)
    observed, duplicated = store.replace_for_recovery(requeue, expected_attempt=1)
    assert first.attempt == observed.attempt == 2
    assert claimed is True and duplicated is False
    with pytest.raises(RuntimeError, match="cannot change durable input"):
        store.replace_for_recovery(
            lambda record: record.model_copy(
                update={"input": record.input.model_copy(update={"approvalNote": "changed"})}
            ),
            expected_attempt=2,
        )


def test_store_replacement_and_recovery_require_valid_terminal_identity(tmp_path: Path) -> None:
    _contract_value, store = _closeout_store(tmp_path)
    current = store.read()
    assert current is not None
    with pytest.raises(RuntimeError, match="active lifecycle operation"):
        store.replace_terminal(current)
    with pytest.raises(RuntimeError, match="cannot change its fingerprint"):
        store.replace_for_recovery(
            lambda record: record.model_copy(update={"fingerprint": "c" * 64}),
            expected_attempt=1,
        )

    store.update(
        lambda record: record.model_copy(
            update={"status": "failed", "phase": "failed", "finishedAt": "2026-08-22T00:00Z"}
        )
    )
    candidate = current.model_copy(update={"fingerprint": "d" * 64, "operationKey": "e" * 64})
    with pytest.raises(RuntimeError, match="cannot change taskId"):
        store.replace_terminal(candidate.model_copy(update={"taskId": "different"}))
    assert store.replace_terminal(candidate).attempt == 2


def test_store_refuses_recovery_after_truthful_commit_proof(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    (contract.code_worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    start_closeout_operation(closeout_operation_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(with_mutation_intent)
    store.update(
        lambda record: with_commit_proven(record).model_copy(
            update={"status": "failed", "phase": "failed"}
        )
    )
    with pytest.raises(RuntimeError, match="past its boundary"):
        store.replace_for_recovery(lambda record: record, expected_attempt=1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("taskName", "other", "cannot change taskName"),
        ("operationKey", "f" * 64, "cannot change its durable input"),
        ("status", "completed", "invalid lifecycle operation transition"),
    ],
)
def test_store_refuses_immutable_identity_and_status_transitions(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    _contract_value, store = _closeout_store(tmp_path)
    with pytest.raises(RuntimeError, match=message):
        store.update(lambda record: record.model_copy(update={field: value}))


def test_store_refuses_claim_boundary_and_ambiguous_cancellation(tmp_path: Path) -> None:
    _contract_value, claimed_store = _closeout_store(tmp_path / "claim")
    claimed_store.update(lambda record: record.model_copy(update={"approvalClaimed": True}))
    with pytest.raises(RuntimeError, match="cannot become unclaimed"):
        claimed_store.update(lambda record: record.model_copy(update={"approvalClaimed": False}))

    contract = _contract(tmp_path / "ambiguous")
    (contract.code_worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    start_closeout_operation(closeout_operation_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(with_mutation_intent)
    with pytest.raises(RuntimeError, match="ambiguous Git intent"):
        store.update(
            lambda record: record.model_copy(update={"status": "cancelled", "phase": "cancelled"})
        )


def test_store_refuses_clearing_or_cancelling_commit_boundary(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    (contract.code_worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    start_closeout_operation(closeout_operation_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(with_mutation_intent)
    store.update(with_commit_proven)
    with pytest.raises(ValidationError, match="must be derived from commit-proven evidence"):
        store.update(
            lambda record: record.model_copy(update={"irreversibleBoundaryEntered": False})
        )
    with pytest.raises(RuntimeError, match="cancellation is refused"):
        cancel_operation(contract.contract_path, "closeout")


def test_integrate_boundary_cannot_be_cleared_or_cancelled(tmp_path: Path) -> None:
    contract = _integration_ready(_contract(tmp_path))
    operation_input = IntegrateOperationInput(
        configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
        contractPath=contract.contract_path.as_posix(),
    )
    start_or_observe_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
    runtime = OperationRuntime(store)
    runtime.start()
    runtime.progress("source-merge", {"irreversible_boundary": True})

    with pytest.raises(RuntimeError, match="irreversible boundary cannot be cleared"):
        store.update(
            lambda record: record.model_copy(update={"irreversibleBoundaryEntered": False})
        )
    with pytest.raises(RuntimeError, match="integrate has entered its irreversible boundary"):
        cancel_operation(contract.contract_path, "integrate")


@pytest.mark.parametrize(
    "mutation",
    [
        "repository",
        "state",
        "before",
        "observed",
        "expected-tree",
    ],
)
def test_store_checks_mutation_evidence_monotonicity(tmp_path: Path, mutation: str) -> None:
    store = _external_store(tmp_path)
    current = store.read()
    assert current is not None
    leg = "code"
    if mutation == "repository":
        with pytest.raises(RuntimeError, match="evidence identity is immutable"):
            store.update(
                lambda record: record.model_copy(
                    update={
                        "mutationEvidence": {
                            **record.mutationEvidence,
                            leg: record.mutationEvidence[leg].model_copy(
                                update={"repository": "/different"}
                            ),
                        }
                    }
                )
            )
        return
    store.update(with_mutation_intent)
    if mutation == "state":
        store.update(
            lambda record: record.model_copy(
                update={
                    "mutationEvidence": {
                        **record.mutationEvidence,
                        leg: record.mutationEvidence[leg].model_copy(
                            update={
                                "state": "reconciled-unchanged",
                                "observed": record.mutationEvidence[leg].before,
                            }
                        ),
                    }
                }
            )
        )
    elif mutation == "observed":
        store.update(lambda record: _with_observed_intent(record, _snapshot("4")))
    expected = {
        "state": "invalid closeout mutation evidence transition",
        "before": "pre-command Git evidence is immutable",
        "observed": "observed Git evidence is immutable",
        "expected-tree": "expected output tree is immutable",
    }[mutation]
    with pytest.raises(RuntimeError, match=expected):
        store.update(
            lambda record: _replace_evidence(
                record,
                leg,
                _changed_evidence(record.mutationEvidence[leg], mutation),
            )
        )


def _replace_evidence(record, leg: str, evidence):
    return record.model_copy(
        update={"mutationEvidence": {**record.mutationEvidence, leg: evidence}}
    )


def _with_observed_intent(record, observed: GitMutationSnapshot):
    return _replace_evidence(
        record,
        "code",
        record.mutationEvidence["code"].model_copy(update={"observed": observed}),
    )


def _changed_evidence(evidence, mutation: str):
    if mutation == "state":
        return evidence.model_copy(update={"state": "mutation-intent", "observed": _snapshot("9")})
    if mutation == "before":
        return evidence.model_copy(update={"before": _snapshot("3")})
    if mutation == "observed":
        return evidence.model_copy(update={"observed": _snapshot("5")})
    return evidence.model_copy(update={"expectedOutputTree": "6" * 40})


def test_commit_change_is_preempted_by_model_and_recovery_fill_only(tmp_path: Path) -> None:
    store = _external_store(tmp_path)
    store.update(with_mutation_intent)
    store.update(with_commit_proven)
    with pytest.raises(ValidationError, match="contradicts commit-proven evidence"):
        store.update(lambda record: _changed_proof(record, include_recovery=False))
    with pytest.raises(RuntimeError, match="can only fill empty cells"):
        store.update(lambda record: _changed_proof(record, include_recovery=True))


def _changed_proof(record, *, include_recovery: bool):
    current = record.mutationEvidence["code"]
    assert current.observed is not None
    evidence = current.model_copy(
        update={
            "observed": current.observed.model_copy(
                update={"head": "9" * 40, "refLogFingerprint": "8" * 64}
            ),
            "commit": "9" * 40,
        }
    )
    updates: dict[str, object] = {"mutationEvidence": {**record.mutationEvidence, "code": evidence}}
    if include_recovery:
        assert record.recoveryCommits is not None
        updates["recoveryCommits"] = record.recoveryCommits.model_copy(
            update={"codeCommit": "9" * 40}
        )
    return record.model_copy(update=updates)


def test_finalization_hash_transition_is_phase_bound_and_immutable(tmp_path: Path) -> None:
    contract, store = _closeout_store(tmp_path / "wrong-phase")
    operation_input = closeout_operation_input(contract)
    finalized = replace(
        contract,
        approved_for_commit=True,
        commit_approval_note=operation_input.approvalNote,
        human_review_status="approved",
        closeout_status="completed",
        code_commit=head_commit(contract.code_worktree),
    )
    runtime = OperationRuntime(store)
    runtime.start()
    expected_hash = closeout_contract_sha256(finalized)
    evidence = {
        "recovery_commits": {"codeCommit": finalized.code_commit},
        "closeout_finalized_contract_sha256": expected_hash,
    }
    with pytest.raises(RuntimeError, match="claimed approval and complete recovery"):
        store.update(
            lambda record: record.model_copy(
                update={
                    "phase": "contract-finalization",
                    "recoveryCommits": LifecycleOperationRecoveryCommits(
                        codeCommit=finalized.code_commit
                    ),
                    "closeoutFinalizedContractSha256": expected_hash,
                }
            )
        )
    runtime.progress("approval-claim", {"approval_claimed": True})
    with pytest.raises(RuntimeError, match="introduced at contract-finalization"):
        runtime.progress("quality", evidence)
    runtime.progress("contract-finalization", evidence)
    persisted = store.read()
    assert persisted is not None and persisted.recoveryCommits is not None
    assert persisted.phase == "contract-finalization"
    assert persisted.closeoutFinalizedContractSha256 == expected_hash
    assert persisted.recoveryCommits.model_dump() == {
        "codeCommit": finalized.code_commit,
        "memoryContentCommit": "",
        "ledgerCommit": "",
    }
    finalized_bytes = store.path.read_bytes()
    with pytest.raises(RuntimeError) as raised:
        runtime.progress(
            "quality",
            {"current_command": "refuse phase advancement after finalization"},
        )
    assert str(raised.value) == (
        "closeout finalized contract SHA-256 requires claimed approval and complete "
        "recovery commits"
    )
    after_refusal = store.read()
    assert after_refusal is not None
    assert after_refusal == persisted
    assert store.path.read_bytes() == finalized_bytes
    assert after_refusal.phase == "contract-finalization"
    assert after_refusal.closeoutFinalizedContractSha256 == expected_hash
    assert after_refusal.recoveryCommits == persisted.recoveryCommits
    with pytest.raises(RuntimeError, match="is immutable"):
        store.update(
            lambda record: record.model_copy(update={"closeoutFinalizedContractSha256": "9" * 64})
        )


def test_external_finalization_requires_complete_recovery_tuple(tmp_path: Path) -> None:
    store = _external_store(tmp_path)
    runtime = OperationRuntime(store)
    runtime.start()
    runtime.progress("approval-claim", {"approval_claimed": True})

    with pytest.raises(RuntimeError, match="claimed approval and complete recovery"):
        store.update(
            lambda record: record.model_copy(
                update={
                    "phase": "contract-finalization",
                    "recoveryCommits": LifecycleOperationRecoveryCommits(codeCommit="7" * 40),
                    "closeoutFinalizedContractSha256": "8" * 64,
                }
            )
        )


def test_completed_integration_retains_its_exact_parameters(tmp_path: Path) -> None:
    contract = _integration_ready(_contract(tmp_path))
    first = IntegrateOperationInput(
        configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
        contractPath=contract.contract_path.as_posix(),
        strategy="ff-only",
    )
    start_or_observe_operation(first, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
    runtime = OperationRuntime(store)
    runtime.start()
    runtime.finish({"state": "integrated"}, ok=True)

    with pytest.raises(RuntimeError, match="already completed task state"):
        start_or_observe_operation(
            first.model_copy(update={"strategy": "replay"}),
            launcher=lambda *_: None,
        )
