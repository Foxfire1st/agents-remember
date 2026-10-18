"""Select original private preparation objects through the existing lifecycle CAS."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.lifecycles.operation import (
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.preparation import (
    CloseoutPreparationIntent,
    PreparedCloseoutOutput,
    require_prepared_output_matches_intent,
)
from agents_remember.worktrees.integration.closeout.certification.observation import refuse
from agents_remember.worktrees.integration.closeout.certification.selection import load_typed
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
