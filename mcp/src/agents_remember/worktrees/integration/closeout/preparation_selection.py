"""Select original private preparation objects through the existing lifecycle CAS."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from agents_remember.kernel.git_command import read_git_commit_bytes
from agents_remember.models.certification.references import CertificateObjectReference
from agents_remember.models.lifecycles.evidence_dependencies import canonical_sha256
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.preparation import (
    CloseoutPreparationIntent,
    PreparedCloseoutOutput,
    build_prepared_closeout_output,
    require_prepared_output_matches_intent,
)
from agents_remember.models.lifecycles.preparation_state import (
    OperationPreparationState,
    PreparationCommand,
    PreparationCommandTerminal,
    PreparedCodeRetention,
    SelectedPreparation,
)
from agents_remember.worktrees.integration.closeout.certification.observation import refuse
from agents_remember.worktrees.integration.closeout.certification.selection import (
    LoadedCertificationSelection,
    load_typed,
    require_selected_certification,
)
from agents_remember.worktrees.integration.lifecycle.certification_observation import (
    observe_certification_publication,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.modules.quality.certification_records import certificate_store
from agents_remember.worktrees.worktree_contract import WorktreeContract


def selected_preparation_intents(
    contract: WorktreeContract, record: LifecycleOperationRecord
) -> tuple[CloseoutPreparationIntent, ...]:
    """Reopen only the selected exact objects, without discovering private outputs."""
    state = record.preparation
    if state is None:
        return ()
    objects = certificate_store(contract.worktree_group)
    intents: list[CloseoutPreparationIntent] = []
    for selected in state.legs:
        intent = load_typed(objects, selected.intent, CloseoutPreparationIntent)
        _require_intent_owner(contract, record, intent)
        if intent.leg != selected.leg or (not intent.writeEnabled and selected.commands):
            refuse("preparation-intent-selection-mismatch", intent.leg, selected.leg)
        if selected.output is not None:
            output = load_typed(objects, selected.output, PreparedCloseoutOutput)
            require_prepared_output_matches_intent(output, intent)
            if intent.writeEnabled and len(selected.commands) != 3:
                refuse("preparation-command-proof-missing", "original commit command", selected)
        intents.append(intent)
    return tuple(intents)


def selected_prepared_code_history_commits(
    contract: WorktreeContract, record: LifecycleOperationRecord
) -> tuple[str, ...]:
    """Return code commits from this selected preparation and its explicit predecessors.

    A successor may prepare a sibling code output while reusing memory evidence from an
    earlier generation.  The certification predecessor links are the only history allowed
    to retain that earlier proof; this never searches the repository for other commits.
    """

    commits: list[str] = []
    current = record
    for _ in range(256):
        state = current.preparation
        if state is not None and state.legs:
            leg = state.legs[0]
            if leg.leg != "code":
                refuse("prepared-code-history-prefix-invalid", "selected code leg", leg.leg)
            if leg.output is not None:
                intents = selected_preparation_intents(contract, current)
                if not intents or intents[0].leg != "code":
                    refuse("prepared-code-history-intent-invalid", "selected code intent", intents)
                require_preparation_logical_refs(contract, current, intents)
                output = load_typed(
                    certificate_store(contract.worktree_group), leg.output, PreparedCloseoutOutput
                )
                require_prepared_output_matches_intent(output, intents[0])
                if current.candidateTree != output.tree:
                    refuse(
                        "prepared-code-history-candidate-mismatch",
                        current.candidateTree,
                        output.tree,
                    )
                commits.append(output.commit)

        selected = current.certification
        predecessor = None if selected is None else selected.predecessor
        if predecessor is None:
            break
        if predecessor.generation != current.generation - 1:
            refuse(
                "selected-predecessor-generation",
                current.generation - 1,
                predecessor.generation,
            )
        archive = operation_record_path(contract.worktree_group, "closeout").with_name(
            f"closeout-operation.generation-{predecessor.generation}.json"
        )
        original = LifecycleOperationStore(archive).read()
        if original is None or (
            original.operationKey != predecessor.operationKey
            or original.generation != predecessor.generation
            or original.fingerprint != current.predecessorFingerprint
            or original.successorFingerprint != current.fingerprint
        ):
            refuse("selected-predecessor-journal-mismatch", predecessor, original)
        current = original
    else:
        refuse("selected-history-capacity", 256, record.generation)
    return tuple(dict.fromkeys(commits))


def select_preparation_intent(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    observed: LifecycleOperationRecord,
    reference: CertificateObjectReference,
) -> LifecycleOperationRecord:
    """Select an already stored, actually admitted intent before any private command."""
    selected = require_selected_certification(contract, observed)
    intent = load_typed(
        certificate_store(contract.worktree_group), reference, CloseoutPreparationIntent
    )
    _require_intent_owner(contract, observed, intent)
    _require_preparation_certificates(selected, intent)
    require_preparation_logical_refs(contract, observed, (intent,))
    prior = observed.preparation
    if prior is not None and any(item.intent == reference for item in prior.legs):
        return _select(store, observed, prior)
    legs = (() if prior is None else prior.legs) + (
        SelectedPreparation(leg=intent.leg, intent=reference),
    )
    state = OperationPreparationState(
        operationKey=observed.operationKey, generation=observed.generation, legs=legs
    )
    return _select(store, observed, state)


def _require_preparation_certificates(
    selected: LoadedCertificationSelection, intent: CloseoutPreparationIntent
) -> None:
    """Require the exact code prefix and, for memory legs, the selected fifth certificate."""
    certificates = tuple(item.certificate for item in selected.terminals[:4])
    if len(certificates) != 4 or any(item is None for item in certificates):
        refuse(
            "preparation-code-prefix-incomplete", "four original code certificates", certificates
        )
    prefix = tuple(item.certificate for item in selected.state.terminals[:4])
    if intent.prefixCertificates != prefix:
        refuse("preparation-code-prefix-mismatch", prefix, intent.prefixCertificates)
    gate_five = selected.state.terminals[4].certificate if len(selected.terminals) == 5 else None
    if intent.leg != "code" and (gate_five is None or gate_five != intent.gateFiveCertificate):
        refuse("preparation-memory-certificate-missing", gate_five, intent.gateFiveCertificate)


def begin_preparation_command(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    observed: LifecycleOperationRecord,
    command: PreparationCommand,
) -> LifecycleOperationRecord:
    """Record a start exactly once. Returning successfully is required before launch."""
    intents = selected_preparation_intents(contract, observed)
    selected = _last(observed)
    if not intents[-1].writeEnabled:
        refuse("preparation-command-disabled", "existing code readback only", command.kind)
    if command.terminal is not None or any(item.kind == command.kind for item in selected.commands):
        refuse("preparation-command-already-started", "named-output readback only", command.kind)
    require_preparation_logical_refs(contract, observed, intents)
    changed = selected.model_copy(update={"commands": (*selected.commands, command)})
    return _replace_last(store, observed, changed)


def observe_preparation_command(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    observed: LifecycleOperationRecord,
    command: PreparationCommand,
    terminal: PreparationCommandTerminal,
) -> LifecycleOperationRecord:
    """Retain the actual command outcome even after cancellation; grant no new start."""
    selected_preparation_intents(contract, observed)
    selected = _last(observed)
    if not selected.commands or selected.commands[-1] != command or command.terminal is not None:
        refuse("preparation-command-observation-mismatch", "exact unobserved command", command)
    finished = command.model_copy(update={"terminal": terminal})
    changed = selected.model_copy(update={"commands": (*selected.commands[:-1], finished)})
    return _replace_last(store, observed, changed)


def select_prepared_output(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    observed: LifecycleOperationRecord,
    reference: CertificateObjectReference,
) -> LifecycleOperationRecord:
    """Select a producer's physically verified output; this does not publish any task ref.

    The preparation producer must complete exact named-checkout/raw-object readback
    before this call. Here the original stored bytes and command relation are reopened.
    """
    intents = selected_preparation_intents(contract, observed)
    selected = _last(observed)
    output = load_typed(
        certificate_store(contract.worktree_group), reference, PreparedCloseoutOutput
    )
    require_prepared_output_matches_intent(output, intents[-1])
    if output.intent != selected.intent or (
        intents[-1].writeEnabled and len(selected.commands) != 3
    ):
        refuse("preparation-output-command-mismatch", selected.intent, output.intent)
    require_preparation_logical_refs(contract, observed, intents)
    return _replace_last(store, observed, selected.model_copy(update={"output": reference}))


def require_preparation_logical_refs(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    intents: tuple[CloseoutPreparationIntent, ...] | None = None,
) -> dict[str, str]:
    """Observe unchanged protected logical roots/refs separately from private Git work."""
    selected = selected_preparation_intents(contract, record) if intents is None else intents
    facts: dict[str, str] = {}
    for intent in selected:
        _require_intent_owner(contract, record, intent)
        root = Path(intent.logicalRoot)
        if root.resolve() != root or not root.is_dir():
            refuse("preparation-logical-root-unsafe", intent.logicalRoot, root.as_posix())
        observed = {
            "repositoryIdentity": require_git(
                root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
            ),
            "logicalRef": require_git(root, ["symbolic-ref", "HEAD"]),
            "head": require_git(root, ["rev-parse", "HEAD"]),
            "ref": require_git(root, ["rev-parse", "--verify", intent.logicalRef]),
        }
        expected = {
            "repositoryIdentity": intent.repositoryIdentity,
            "logicalRef": intent.logicalRef,
            "head": intent.expectedOldCommit,
            "ref": intent.expectedOldCommit,
        }
        if observed != expected:
            refuse("preparation-logical-refs-changed", expected, observed)
        for key, value in observed.items():
            facts[f"preparation:{intent.leg}:{key}"] = value
        facts[f"preparation:{intent.leg}:intent"] = intent.intentDigest
        facts[f"preparation:{intent.leg}:logicalRoot"] = intent.logicalRoot
    return facts


def _require_intent_owner(
    contract: WorktreeContract, record: LifecycleOperationRecord, intent: CloseoutPreparationIntent
) -> None:
    selected = record.certification
    root = contract.code_worktree if intent.leg == "code" else contract.memory_worktree
    if (
        selected is None
        or record.operationKind != "closeout"
        or (intent.operationKey, intent.generation) != (record.operationKey, record.generation)
        or record.contractPath != contract.contract_path.as_posix()
        or intent.contractPath != record.contractPath
        or intent.frozenRun != selected.frozenRun
        or intent.candidateAuthorities != selected.candidateAuthorities
        or root is None
        or intent.logicalRoot != root.as_posix()
    ):
        refuse("preparation-intent-owner-mismatch", record.operationKey, intent.operationKey)


def _last(record: LifecycleOperationRecord) -> SelectedPreparation:
    if record.preparation is None:
        refuse("preparation-intent-missing", "separately selected intent", None)
    return record.preparation.legs[-1]


def _replace_last(
    store: LifecycleOperationStore,
    observed: LifecycleOperationRecord,
    selected: SelectedPreparation,
) -> LifecycleOperationRecord:
    if observed.preparation is None:
        refuse("preparation-intent-missing", "separately selected intent", None)
    state = observed.preparation.model_copy(
        update={"legs": (*observed.preparation.legs[:-1], selected)}
    )
    return _select(store, observed, state)


def _select(
    store: LifecycleOperationStore,
    observed: LifecycleOperationRecord,
    state: OperationPreparationState,
) -> LifecycleOperationRecord:
    observed = observe_certification_publication(store, observed)
    record, matched = store.update_if_current(
        observed, lambda current: current.model_copy(update={"preparation": state})
    )
    if not matched:
        refuse("preparation-selection-stale", observed.recordRevision, record.recordRevision)
    return record


def retain_prepared_code_for_successor(
    contract: WorktreeContract,
    predecessor: LifecycleOperationRecord,
    successor: LifecycleOperationRecord,
) -> LifecycleOperationRecord:
    """Rebind one proved private code output to a memory-only successor.

    The predecessor's intent/output stay addressable in the certificate store.  A
    new intent/output wrapper is required because generation ownership is part of
    both objects; the raw Git commit and its byte-derived identity are carried
    unchanged.  No command is started here.
    """

    predecessor_leg, source_intent, source_output, raw = _source_code_output(contract, predecessor)
    source_output_ref = predecessor_leg.output
    if source_output_ref is None:
        refuse(
            "prepared-code-retention-output-missing",
            "selected predecessor code output",
            predecessor_leg,
        )
    if (
        predecessor.candidateTree != source_output.tree
        or successor.candidateTree != source_output.tree
    ):
        refuse(
            "prepared-code-retention-candidate-moved",
            source_output.tree,
            (predecessor.candidateTree, successor.candidateTree),
        )
    prefix = _successor_code_prefix(successor)
    successor_intent = _rebound_code_intent(contract, successor, source_intent, prefix)
    objects = certificate_store(contract.worktree_group)
    objects.publish(successor_intent)
    successor_intent_ref = objects.reference("preparation-intent", successor_intent.intentDigest)
    successor_output, successor_output_ref = _rebound_code_output(
        objects, raw, source_output, successor_intent_ref
    )
    retained_leg = predecessor_leg.model_copy(
        update={"intent": successor_intent_ref, "output": successor_output_ref}
    )
    state = OperationPreparationState(
        operationKey=successor.operationKey,
        generation=successor.generation,
        legs=(retained_leg,),
    )
    retention = PreparedCodeRetention(
        predecessorOperationKey=predecessor.operationKey,
        predecessorGeneration=predecessor.generation,
        predecessorFingerprint=predecessor.fingerprint,
        sourceIntent=predecessor_leg.intent,
        sourceOutput=source_output_ref,
        successorIntent=successor_intent_ref,
        successorOutput=successor_output_ref,
        commit=successor_output.commit,
        tree=successor_output.tree,
        committerDate=successor_output.committerDate,
        rawCommitSha256=successor_output.rawCommitSha256,
    )
    return successor.model_copy(update={"preparation": state, "preparedCodeRetention": retention})


def _source_code_output(
    contract: WorktreeContract,
    predecessor: LifecycleOperationRecord,
) -> tuple[SelectedPreparation, CloseoutPreparationIntent, PreparedCloseoutOutput, bytes]:
    state = predecessor.preparation
    if state is None:
        refuse("prepared-code-retention-missing", "selected predecessor code preparation", None)
    if len(state.legs) != 1 or state.legs[0].leg != "code":
        refuse("prepared-code-retention-prefix-unsupported", "one selected code leg", state)
    leg = state.legs[0]
    if (
        leg.output is None
        or len(leg.commands) != 3
        or any(
            command.terminal is None or command.terminal.outcome != "succeeded"
            for command in leg.commands
        )
    ):
        refuse("prepared-code-retention-output-missing", "completed selected code output", leg)
    if any(evidence.state != "pre-mutation" for evidence in predecessor.mutationEvidence.values()):
        refuse(
            "prepared-code-retention-protected-output",
            "all mutation legs pre-mutation",
            predecessor.mutationEvidence,
        )
    intents = selected_preparation_intents(contract, predecessor)
    if (
        len(intents) != 1
        or intents[0].leg != "code"
        or intents[0].intentDigest != leg.intent.semanticDigest
    ):
        refuse("prepared-code-retention-intent-moved", leg.intent, intents)
    require_preparation_logical_refs(contract, predecessor, intents)
    objects = certificate_store(contract.worktree_group)
    output = load_typed(objects, leg.output, PreparedCloseoutOutput)
    require_prepared_output_matches_intent(output, intents[0])
    raw = read_git_commit_bytes(Path(intents[0].logicalRoot), output.commit)
    if (
        raw != base64.b64decode(output.rawCommitBase64, validate=True)
        or hashlib.sha256(raw).hexdigest() != output.rawCommitSha256
    ):
        refuse("prepared-code-retention-bytes-moved", output.outputDigest, "raw commit")
    return leg, intents[0], output, raw


def _successor_code_prefix(
    successor: LifecycleOperationRecord,
) -> tuple[CertificateObjectReference, ...]:
    selected = successor.certification
    if selected is None:
        refuse("prepared-code-retention-certification-missing", "new selected certification", None)
    prefix = tuple(item.certificate for item in selected.terminals[:4])
    if len(prefix) != 4 or any(item is None for item in prefix):
        refuse(
            "prepared-code-retention-prefix-incomplete", "four selected green certificates", prefix
        )
    return tuple(item for item in prefix if item is not None)


def _rebound_code_intent(
    contract: WorktreeContract,
    successor: LifecycleOperationRecord,
    source: CloseoutPreparationIntent,
    prefix: tuple[CertificateObjectReference, ...],
) -> CloseoutPreparationIntent:
    operation_input = successor.input
    if not isinstance(
        operation_input, CloseoutOperationInput
    ) or not operation_input.effectiveInput.enabled("code"):
        refuse(
            "prepared-code-retention-code-disabled", "enabled code closeout leg", operation_input
        )
    if operation_input.effectiveInput.message_for("code") != source.normalizedMessage:
        refuse(
            "prepared-code-retention-code-intent-moved",
            source.normalizedMessage,
            operation_input.effectiveInput.message_for("code"),
        )
    if successor.certification is None:  # narrowed for type checkers; prefix already proved it
        refuse("prepared-code-retention-certification-missing", "new selected certification", None)
    payload = source.model_dump(mode="json")
    payload.update(
        {
            "operationKey": successor.operationKey,
            "generation": successor.generation,
            "contractSha256": closeout_contract_sha256(contract),
            "frozenRun": successor.certification.frozenRun.model_dump(mode="json"),
            "candidateAuthorities": successor.certification.candidateAuthorities.model_dump(
                mode="json"
            ),
            "prefixCertificates": [item.model_dump(mode="json") for item in prefix],
        }
    )
    payload.pop("intentDigest", None)
    return CloseoutPreparationIntent.model_validate(
        {**payload, "intentDigest": canonical_sha256(payload)}
    )


def _rebound_code_output(
    objects,
    raw: bytes,
    source: PreparedCloseoutOutput,
    intent: CertificateObjectReference,
) -> tuple[PreparedCloseoutOutput, CertificateObjectReference]:
    output = build_prepared_closeout_output(raw, intent, disposition=source.disposition)
    if (output.commit, output.tree, output.committerDate, output.rawCommitSha256) != (
        source.commit,
        source.tree,
        source.committerDate,
        source.rawCommitSha256,
    ):
        refuse("prepared-code-retention-identity-moved", source, output)
    objects.publish(output)
    return output, objects.reference("prepared-output", output.outputDigest)


def require_retained_prepared_code_current(
    record: LifecycleOperationRecord,
    intent: CloseoutPreparationIntent,
    output: PreparedCloseoutOutput,
) -> None:
    """Reprove the successor binding before any private output read or reuse."""

    retention = record.preparedCodeRetention
    state = record.preparation
    if retention is None or state is None or not state.legs or state.legs[0].leg != "code":
        refuse("prepared-code-retention-missing", "code-first successor retention proof", retention)
    leg = state.legs[0]
    if (
        leg.intent != retention.successorIntent
        or leg.output != retention.successorOutput
        or output.commit != retention.commit
        or output.tree != retention.tree
        or output.committerDate != retention.committerDate
        or output.rawCommitSha256 != retention.rawCommitSha256
    ):
        refuse("prepared-code-retention-binding-moved", retention, (leg, output))
    require_prepared_output_matches_intent(output, intent)
    raw = read_git_commit_bytes(Path(intent.logicalRoot), output.commit)
    if base64.b64decode(output.rawCommitBase64, validate=True) != raw:
        refuse("prepared-code-retention-bytes-moved", output.outputDigest, "raw commit")
