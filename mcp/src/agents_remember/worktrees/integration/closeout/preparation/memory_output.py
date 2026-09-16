"""Prepare exact post-certification memory content without publishing logical refs."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.git_command import read_git_commit_bytes
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_ledger import MEMORY_CACHE_EXCLUDE
from agents_remember.models.lifecycles.evidence_dependencies import canonical_sha256
from agents_remember.models.lifecycles.operation import CloseoutOperationInput
from agents_remember.models.lifecycles.preparation import (
    CloseoutPreparationIntent,
    ExistingMemoryPreparationProof,
    PreparedCloseoutOutput,
)
from agents_remember.worktrees.integration.closeout.certification.execution import (
    CloseoutCertificationHandoff,
)
from agents_remember.worktrees.integration.closeout.certification.observation import refuse
from agents_remember.worktrees.integration.closeout.certification.selection import load_typed
from agents_remember.worktrees.integration.closeout.preparation_selection import (
    select_preparation_intent,
    selected_preparation_intents,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.modules.quality.certification_records import certificate_store

from .code_output import select_code_preparation
from .memory_execution import current_prepared_memory_result
from .memory_port import PreparedMemoryCertificationResult
from .memory_reuse import observe_existing_memory_proof
from .output_selection import retain_prepared_output
from .policy import observe_git_preparation_policy
from .private_execution import observe_private_output, prepare_private_output
from .selected import SelectedCloseoutPreparation


@dataclass(frozen=True)
class PreparedMemoryOutputs:
    handoff: CloseoutCertificationHandoff
    code: SelectedCloseoutPreparation
    memory: SelectedCloseoutPreparation


@dataclass(frozen=True, kw_only=True)
class _MemoryIntentSelection:
    parent: str
    tree: str
    proof: ExistingMemoryPreparationProof | None
    enabled: bool


def _intent(
    result: PreparedMemoryCertificationResult,
    selection: _MemoryIntentSelection,
) -> CloseoutPreparationIntent:
    handoff = result.handoff
    contract = handoff.contract
    root = contract.memory_worktree
    if root is None or not isinstance(handoff.record.input, CloseoutOperationInput):
        refuse("prepared-memory-route", "external-memory closeout", root)
    effective = handoff.record.input.effectiveInput
    if selection.enabled and not effective.enabled("memory"):
        refuse("prepared-memory-write-disabled", "memory", effective)
    # Attribution is part of the exact object that certification prepares for publication.
    message = (
        render_memory_content_message(
            effective.message_for("memory"), result.candidate.codeView.codeCommit
        )
        if selection.enabled
        else None
    )
    policy = observe_git_preparation_policy(root)
    private = (
        contract.worktree_group
        / "preparation"
        / handoff.record.operationKey
        / f"generation-{handoff.record.generation}"
        / "memory-content"
    )
    payload = {
        "schemaVersion": "closeout-preparation-intent/v1",
        "operationKey": handoff.record.operationKey,
        "generation": handoff.record.generation,
        "contractPath": contract.contract_path.as_posix(),
        "contractSha256": closeout_contract_sha256(contract),
        "leg": "memory-content",
        "writeEnabled": selection.enabled,
        "repositoryIdentity": require_git(
            root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
        ),
        "logicalRoot": root.as_posix(),
        "logicalRef": f"refs/heads/{contract.memory_work_branch}",
        "expectedOldCommit": require_git(root, ["rev-parse", "--verify", "HEAD"]),
        "parentCommit": selection.parent,
        "admittedTree": selection.tree,
        "privateRoot": private.as_posix() if selection.enabled else None,
        "normalizedMessage": message,
        "hookPolicy": "ordinary",
        "gitConfigSha256": policy.git_config_sha256,
        "hooksSha256": policy.hooks_sha256,
        "frozenRun": handoff.selected.state.frozenRun.model_dump(mode="json"),
        "candidateAuthorities": handoff.selected.state.candidateAuthorities.model_dump(mode="json"),
        "prefixCertificates": [
            item.certificateReference.model_dump(mode="json")
            for item in handoff.selected.terminals[:4]
            if item.certificateReference is not None
        ],
        "gateFiveCertificate": result.certificate.model_dump(mode="json"),
        "existingMemoryProof": None
        if selection.proof is None
        else selection.proof.model_dump(mode="json"),
    }
    return CloseoutPreparationIntent.model_validate(
        {**payload, "intentDigest": canonical_sha256(payload)}
    )


def _selected(
    result: PreparedMemoryCertificationResult, intent: CloseoutPreparationIntent
) -> SelectedCloseoutPreparation:
    handoff = result.handoff
    objects = certificate_store(handoff.contract.worktree_group)
    intents = selected_preparation_intents(handoff.contract, handoff.record)
    for index, existing in enumerate(intents):
        if existing.leg == intent.leg:
            if existing != intent:
                refuse("prepared-memory-intent-moved", existing.intentDigest, intent.intentDigest)
            assert handoff.record.preparation is not None
            return SelectedCloseoutPreparation(
                handoff, intent, handoff.record.preparation.legs[index].intent
            )
    objects.publish(intent)
    reference = objects.reference("preparation-intent", intent.intentDigest)
    record = select_preparation_intent(handoff.contract, handoff.store, handoff.record, reference)
    return SelectedCloseoutPreparation(replace(handoff, record=record), intent, reference)


def _output(selected: SelectedCloseoutPreparation) -> PreparedCloseoutOutput:
    assert selected.handoff.record.preparation is not None
    state = next(
        item for item in selected.handoff.record.preparation.legs if item.leg == selected.intent.leg
    )
    if state.output is None:
        refuse("prepared-memory-output-missing", selected.intent.leg, None)
    return load_typed(
        certificate_store(selected.handoff.contract.worktree_group),
        state.output,
        PreparedCloseoutOutput,
    )


def _prepare(
    result: PreparedMemoryCertificationResult, intent: CloseoutPreparationIntent
) -> SelectedCloseoutPreparation:
    selected = _selected(result, intent)

    def reobserve(observed: SelectedCloseoutPreparation) -> SelectedCloseoutPreparation:
        actual = current_prepared_memory_result(replace(result, handoff=observed.handoff))
        observe_git_preparation_policy(Path(intent.logicalRoot)).require_intent(intent)
        originals = selected_preparation_intents(actual.handoff.contract, actual.handoff.record)
        if intent not in originals:
            refuse("prepared-memory-selection-moved", intent.intentDigest, originals)
        if intent.existingMemoryProof is not None:
            proof = observe_existing_memory_proof(
                Path(intent.logicalRoot),
                certified_tree=actual.candidate.memoryTree,
            )
            if proof != intent.existingMemoryProof:
                refuse("prepared-memory-reuse-moved", intent.existingMemoryProof, proof)
        return replace(observed, handoff=actual.handoff)

    selected = reobserve(selected)
    assert selected.handoff.record.preparation is not None
    state = next(
        item for item in selected.handoff.record.preparation.legs if item.leg == intent.leg
    )
    if state.output is not None:
        # Previously selected output is immutable. Physical reproof is still required.
        if intent.writeEnabled:
            observe_private_output(selected, reobserve=reobserve)
        else:
            raw = read_git_commit_bytes(Path(intent.logicalRoot), _output(selected).commit)
            if raw != base64.b64decode(_output(selected).rawCommitBase64, validate=True):
                refuse("prepared-memory-existing-output-moved", state.output, "raw bytes")
        return reobserve(selected)
    if intent.writeEnabled:
        selected, raw = prepare_private_output(selected, reobserve=reobserve)
    else:
        raw = read_git_commit_bytes(Path(intent.logicalRoot), intent.expectedOldCommit)
    selected, _, _ = retain_prepared_output(selected, raw, reobserve=reobserve)
    return selected


def prepare_memory_outputs(result: PreparedMemoryCertificationResult) -> PreparedMemoryOutputs:
    """Retain genuine code and memory outputs, leaving both logical branches untouched."""
    result = current_prepared_memory_result(result)
    code = select_code_preparation(result.handoff)
    root = result.handoff.contract.memory_worktree
    if root is None:
        refuse("prepared-memory-route", "external-memory closeout", None)
    head = require_git(root, ["rev-parse", "--verify", "HEAD"])
    proof = observe_existing_memory_proof(
        root,
        certified_tree=result.candidate.memoryTree,
    )
    memory_intent = _intent(
        result,
        _MemoryIntentSelection(
            parent=head,
            tree=result.candidate.memoryTree if proof is None else proof.logicalHeadTree,
            proof=proof,
            enabled=proof is None,
        ),
    )
    memory = _prepare(result, memory_intent)
    if memory.intent.writeEnabled:
        result = current_prepared_memory_result(replace(result, handoff=memory.handoff))
        require_git(root, ["add", "-A", "--", ".", MEMORY_CACHE_EXCLUDE])
        require_git(root, ["update-index", "--force-remove", "--", "memory.md"])
        actual_tree = require_git(root, ["write-tree"])
        if actual_tree != result.candidate.memoryTree:
            refuse("prepared-memory-index-moved", result.candidate.memoryTree, actual_tree)
        result = current_prepared_memory_result(result)
        memory = replace(memory, handoff=result.handoff)
    return PreparedMemoryOutputs(
        memory.handoff,
        replace(code, handoff=memory.handoff),
        memory,
    )
