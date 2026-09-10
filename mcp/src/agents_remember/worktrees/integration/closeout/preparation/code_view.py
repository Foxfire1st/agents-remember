"""Reprove selected real code bytes while retaining the canonical logical memory pair."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import (
    admit_private_git_preparation,
    inspect_existing_git_preparation,
    inspect_git_preparation,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.models.certification.references import CertificateObjectReference
from agents_remember.models.lifecycles.evidence_dependencies import canonical_sha256
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.lifecycles.preparation import (
    CloseoutPreparationIntent,
    PreparedCloseoutOutput,
    build_prepared_closeout_output,
    require_prepared_output_matches_intent,
)
from agents_remember.models.lifecycles.prepared_memory import PreparedCodeExecutionView
from agents_remember.worktrees.integration.closeout.certification.execution import (
    CloseoutCertificationHandoff,
)
from agents_remember.worktrees.integration.closeout.certification.observation import refuse
from agents_remember.worktrees.integration.closeout.certification.selection import load_typed
from agents_remember.worktrees.integration.closeout.memory_candidate_pair import (
    resolve_memory_candidate_pair,
)
from agents_remember.worktrees.integration.closeout.preparation_selection import (
    require_preparation_logical_refs,
    selected_preparation_intents,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.modules.quality.certification_records import certificate_store
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .code_execution import observe_code_output, prepare_code_output
from .code_output import current_code_preparation
from .policy import observe_git_preparation_policy
from .private_execution import private_git_binding
from .selected import SelectedCloseoutPreparation


@dataclass(frozen=True)
class _PreparedCodeViewBuild:
    pair: MemoryCandidatePairIdentity
    operation_key: str
    generation: int
    intent: CloseoutPreparationIntent
    intent_reference: CertificateObjectReference
    output: PreparedCloseoutOutput
    output_reference: CertificateObjectReference
    raw: bytes


def observe_prepared_code_view(handoff: CloseoutCertificationHandoff) -> PreparedCodeExecutionView:
    """Read only this generation's named output; no creation, adoption or ledger fiction."""
    state = handoff.record.preparation
    if state is None or state.legs[0].output is None:
        refuse("prepared-code-view-unavailable", "journal-selected code output", state)
    intent = selected_preparation_intents(handoff.contract, handoff.record)[0]
    selected = current_code_preparation(
        SelectedCloseoutPreparation(handoff, intent, state.legs[0].intent)
    )
    objects = certificate_store(selected.handoff.contract.worktree_group)
    output = load_typed(objects, state.legs[0].output, PreparedCloseoutOutput)
    require_prepared_output_matches_intent(output, intent)
    raw = observe_code_output(selected)
    pair = resolve_memory_candidate_pair(selected.handoff.contract)
    view = _build_prepared_code_view(
        _PreparedCodeViewBuild(
            pair=pair,
            operation_key=selected.handoff.record.operationKey,
            generation=selected.handoff.record.generation,
            intent=intent,
            intent_reference=selected.reference,
            output=output,
            output_reference=state.legs[0].output,
            raw=raw,
        )
    )
    current_code_preparation(selected)
    return view


def observe_selected_prepared_code_view(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    store: LifecycleOperationStore,
    pair: MemoryCandidatePairIdentity,
) -> PreparedCodeExecutionView:
    """Reprove a selected code output without requiring a live worker lease.

    Public memory preparation may run after a closeout worker has returned
    ``input-required``. It reuses the same selected-intent/output owners and the
    same kernel Git observers as the running-worker path, while retaining the
    logical pair supplied by the caller.
    """

    state = record.preparation
    if state is None or not state.legs or state.legs[0].output is None:
        refuse("prepared-code-view-unavailable", "journal-selected code output", state)
    intents = selected_preparation_intents(contract, record)
    if not intents or intents[0].leg != "code":
        refuse("prepared-code-view-unavailable", "selected code intent", intents)
    require_preparation_logical_refs(contract, record, intents)
    intent = intents[0]
    selected = state.legs[0]
    output_reference = selected.output
    if output_reference is None:
        refuse("prepared-code-view-unavailable", "journal-selected code output", selected)
    objects = certificate_store(contract.worktree_group)
    output = load_typed(objects, output_reference, PreparedCloseoutOutput)
    require_prepared_output_matches_intent(output, intent)

    if output.disposition == "existing":
        raw = inspect_existing_git_preparation(
            Path(intent.logicalRoot),
            common_directory=Path(intent.repositoryIdentity),
            logical_ref=intent.logicalRef,
            commit=intent.expectedOldCommit,
            tree=intent.admittedTree,
        )
        _require_selected_code_current(contract, record, store, selected, intent)
    else:
        binding = private_git_binding(intent)

        def authorize(actual: object) -> None:
            _require_selected_code_current(contract, record, store, selected, intent)
            if actual != binding:
                raise GitPreparationError("selected private code binding moved")
            observe_git_preparation_policy(binding.private_root).require_intent(intent)

        capability = admit_private_git_preparation(binding, authorize=authorize)
        observation = inspect_git_preparation(capability)
        if observation.state != "committed" or observation.raw_commit is None:
            raise GitPreparationError("selected private code output is not committed")
        raw = observation.raw_commit
        _require_selected_code_current(contract, record, store, selected, intent)

    return _build_prepared_code_view(
        _PreparedCodeViewBuild(
            pair=pair,
            operation_key=record.operationKey,
            generation=record.generation,
            intent=intent,
            intent_reference=selected.intent,
            output=output,
            output_reference=output_reference,
            raw=raw,
        )
    )


def _require_selected_code_current(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    store: LifecycleOperationStore,
    selected,
    intent,
) -> None:
    current = store.read()
    if current is None or (current.operationKey, current.generation) != (
        record.operationKey,
        record.generation,
    ):
        raise GitPreparationError("selected prepared code operation moved")
    current_intents = selected_preparation_intents(contract, current)
    if (
        not current_intents
        or current_intents[0] != intent
        or current.preparation is None
        or current.preparation.legs[0] != selected
    ):
        raise GitPreparationError("selected prepared code output moved")


def _build_prepared_code_view(
    build: _PreparedCodeViewBuild,
) -> PreparedCodeExecutionView:
    actual = build_prepared_closeout_output(
        build.raw,
        build.intent_reference,
        disposition=build.output.disposition,
    )
    if actual != build.output:
        refuse("prepared-code-view-output-moved", build.output.outputDigest, actual.outputDigest)
    physical_root = (
        build.intent.logicalRoot
        if build.output.disposition == "existing"
        else build.intent.privateRoot
    )
    if physical_root is None:
        refuse(
            "prepared-code-view-private-root-missing", "selected private code root", build.intent
        )
    payload = {
        "schemaVersion": "prepared-code-execution-view/v1",
        "operationKey": build.operation_key,
        "generation": build.generation,
        "logicalPair": build.pair.model_dump(mode="json"),
        "preparationIntent": build.intent_reference.model_dump(mode="json"),
        "preparedOutput": build.output_reference.model_dump(mode="json"),
        "physicalCodeRoot": physical_root,
        "repositoryIdentity": build.intent.repositoryIdentity,
        "codeCommit": build.output.commit,
        "codeTree": build.output.tree,
        "disposition": build.output.disposition,
    }
    return PreparedCodeExecutionView.model_validate(
        {**payload, "viewDigest": canonical_sha256(payload)}
    )


def prepare_code_view(
    handoff: CloseoutCertificationHandoff,
) -> tuple[CloseoutCertificationHandoff, PreparedCodeExecutionView]:
    """Finish allowed private commands and return a newly proved physical execution view."""
    selected, _, _ = prepare_code_output(handoff)
    return selected.handoff, observe_prepared_code_view(selected.handoff)
