"""Explicit normalized closeout inputs for behavioral fixtures."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.closeout_input import CloseoutCorrectedCall, EffectiveCloseoutInput
from agents_remember.models.lifecycles.mutation_evidence import (
    CloseoutMutationLeg,
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import CloseoutOperationInput
from agents_remember.worktrees.closeout_input import (
    normalize_closeout_input,
    raw_closeout_messages,
)
from agents_remember.worktrees.integration.closeout_operation_admission import (
    CloseoutOperationAdmission,
)
from agents_remember.worktrees.integration.closeout_recovery_projection import (
    derive_closeout_recovery_commits,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    start_or_observe_closeout_operation,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import load_contract


class MutationEvidenceRecorder:
    """Explicit unit-test authority that verifies every published Git transition."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.evidence: dict[CloseoutMutationLeg, GitMutationEvidence] = {}

    def __call__(self, phase: str, values) -> None:
        captured = dict(values)
        self.events.append((phase, captured))
        raw = captured.get("mutation_evidence")
        if raw is None:
            return
        current = GitMutationEvidence.model_validate(raw)
        previous = self.evidence.get(current.leg)
        if previous is None:
            assert current.state == "mutation-intent"
        else:
            allowed = {
                "mutation-intent": {"mutation-intent", "commit-proven"},
                "commit-proven": {"commit-proven"},
            }
            assert current.state in allowed[previous.state]
            assert current.before == previous.before
            if previous.expectedOutputTree is not None:
                assert current.expectedOutputTree == previous.expectedOutputTree
        self.evidence[current.leg] = current

    def assert_proven(self, *legs: CloseoutMutationLeg) -> None:
        assert set(self.evidence) == set(legs)
        assert all(self.evidence[leg].state == "commit-proven" for leg in legs)


def start_closeout_operation(
    operation_input: CloseoutOperationInput,
    **options,
):
    """Route a durable-input fixture through canonical raw, lease-bound admission."""
    effective = operation_input.effectiveInput
    return start_or_observe_closeout_operation(
        CloseoutOperationAdmission(
            config_path=operation_input.configPath,
            contract_path=Path(operation_input.contractPath),
            messages=raw_closeout_messages(
                code=_enabled_message(effective, "code"),
                memory=_enabled_message(effective, "memory"),
                ledger=_enabled_message(effective, "ledger"),
            ),
            approval_note=operation_input.approvalNote,
            gate_policy=operation_input.gatePolicy,
            corrected_call=CloseoutCorrectedCall(
                tool="worktree_closeout_apply",
                arguments={
                    "contract_path": operation_input.contractPath,
                    "intent_note": "<developer intent>",
                },
            ),
        ),
        load_contract(Path(operation_input.contractPath)),
        **options,
    )


def publish_closeout_finalization(runtime, contract) -> None:
    """Publish the exact proof production records before queue certification."""

    runtime.progress(
        "contract-finalization",
        {
            "approval_claimed": True,
            "recovery_commits": {
                "codeCommit": contract.code_commit,
                "memoryContentCommit": contract.memory_content_commit,
                "ledgerCommit": contract.ledger_commit,
            },
            "closeout_finalized_contract_sha256": closeout_contract_sha256(contract),
        },
    )


def _enabled_message(effective: EffectiveCloseoutInput, leg: CloseoutMutationLeg) -> str | None:
    return effective.message_for(leg) if effective.enabled(leg) else None


def closeout_operation_input(
    contract,
    **values,
) -> CloseoutOperationInput:
    config_path = values.pop("config_path", None)
    code = values.pop("code", "close code candidate")
    memory = values.pop("memory", "close external memory")
    ledger = values.pop("ledger", "record code-to-memory mapping")
    approval_note = values.pop("approval_note", "developer approved this exact candidate")
    assert not values, f"unknown closeout operation fixture fields: {sorted(values)}"
    effective = normalize_closeout_input(
        contract,
        raw_closeout_messages(code=code, memory=memory, ledger=ledger),
        route="worktree",
        corrected_call=CloseoutCorrectedCall(
            tool="worktree_closeout_apply",
            arguments={"contract_path": contract.contract_path.as_posix()},
        ),
    )
    configured = config_path or (contract.code_repo_path.parent / "settings.json")
    return CloseoutOperationInput(
        configPath=Path(configured).as_posix(),
        contractPath=contract.contract_path.as_posix(),
        effectiveInput=effective,
        approvalNote=str(approval_note),
    )


def closeout_worktree_args(
    contract,
    *,
    code: str = "close code candidate",
    memory: str = "close external memory",
    ledger: str = "record code-to-memory mapping",
    **values,
) -> WorktreeArgs:
    effective = normalize_closeout_input(
        contract,
        raw_closeout_messages(code=code, memory=memory, ledger=ledger),
        route="worktree",
        corrected_call=CloseoutCorrectedCall(
            tool="worktree_closeout_apply",
            arguments={"contract_path": contract.contract_path.as_posix()},
        ),
    )
    return WorktreeArgs(
        contract_path=contract.contract_path,
        closeout_input=effective,
        **values,
    )


def with_mutation_intent(record, *, leg: CloseoutMutationLeg | None = None):
    """Publish a structurally valid fixture intent for one enabled leg."""
    selected = leg or next(iter(record.mutationEvidence))
    current = record.mutationEvidence[selected]
    before = GitMutationSnapshot(
        headRef="refs/heads/test-closeout",
        head="a" * 40,
        headTree="b" * 40,
        refLogFingerprint="f" * 64,
        indexTree="b" * 40,
        candidateTree="c" * 40,
        statusFingerprint="d" * 64,
    )
    intent = current.model_copy(
        update={
            "state": "mutation-intent",
            "before": before,
            "expectedOutputTree": "c" * 40,
        }
    )
    evidence = dict(record.mutationEvidence)
    evidence[selected] = intent
    return record.model_copy(update={"mutationEvidence": evidence})


def with_commit_proven(
    record,
    *,
    leg: CloseoutMutationLeg | None = None,
    commit: str | None = None,
):
    """Advance one enabled fixture leg through intent and durable proof."""
    selected = leg or next(iter(record.mutationEvidence))
    if record.mutationEvidence[selected].state == "pre-mutation":
        record = with_mutation_intent(record, leg=selected)
    current = record.mutationEvidence[selected]
    assert current.before is not None
    proof_commit = commit or "e" * 40
    observed = current.before.model_copy(
        update={
            "head": proof_commit,
            "headTree": current.expectedOutputTree,
            "refLogFingerprint": "1" * 64,
            "indexTree": current.expectedOutputTree,
            "candidateTree": current.expectedOutputTree,
            "statusFingerprint": "2" * 64,
        }
    )
    proven = current.model_copy(
        update={"state": "commit-proven", "observed": observed, "commit": proof_commit}
    )
    evidence = dict(record.mutationEvidence)
    evidence[selected] = proven
    updated = record.model_copy(
        update={"mutationEvidence": evidence, "irreversibleBoundaryEntered": True}
    )
    return updated.model_copy(update={"recoveryCommits": derive_closeout_recovery_commits(updated)})


def with_reconciled_unchanged(record, *, leg: CloseoutMutationLeg | None = None):
    """Advance one enabled fixture leg to an exact no-output reconciliation."""
    selected = leg or next(iter(record.mutationEvidence))
    if record.mutationEvidence[selected].state == "pre-mutation":
        record = with_mutation_intent(record, leg=selected)
    current = record.mutationEvidence[selected]
    assert current.before is not None
    reconciled = current.model_copy(
        update={"state": "reconciled-unchanged", "observed": current.before}
    )
    evidence = dict(record.mutationEvidence)
    evidence[selected] = reconciled
    return record.model_copy(update={"mutationEvidence": evidence})
