"""Direct landing: exact code-commit verification and memory content publication.

The direct landing is the branch-addressed counterpart of the worktree closeout
commit phase for sanctioned direct execution. Where the worktree path stages a
leaf worktree candidate, this operation binds the task-root series contract and
verifies the exact code commit on the series branch, then commits external-
memory content with code attribution. Input is normalized before the integration
authority lock. Apply records a durable direct-landing generation before the
memory content commit.

This is specifically the delivery route for a leaf implemented without its own
worktree enclosure. It is not master/series closeout and it is not the ordinary
``worktree_integrate`` edge that lands an already closed master into its parent
branch. Those lifecycle edges do not become direct execution merely because
their contract kind is ``series``.

The gate stays strictly pre-commit: pass the staged ``candidate_tree`` that the
owner already gated through the Dagger module's ``--source``/``--repository-bundle``
contract, and the landing verifies the branch HEAD tree equals it before any
memory commit. Commit-then-gate is the accepted-risk exception only
where the developer rules it.

The operation is policy-gated (``directExecutionEnabled``) and deliberately
synchronous: direct mode does not use the ``start_or_observe_operation`` detached
worker. The lane lock serializes execution; the canonical lifecycle journal owns
crash recovery for the memory output.

On converted memory (the layout marker on either side) the mandatory invariant gate
(MIK-R09 rule 3) runs before admission: the worklist is recomputed and every item
decided against the leaf's history file, the validator runs over the exact candidate,
and any finding refuses. The leaf's history file is then closed in the candidate the
journal admits (MIK-R07 rule 7), and that exact tree is validated again before it is
admitted. Unconverted memory lands exactly as before.

The closing is kept until the generation is decided (L09 review R1, finding 3): an
input conflict, a failed create or a replayed generation restores the file in process,
and a generation that is created carries its own receipt, so its cancellation (or a
later landing, if the call ended before the journal accepted it) restores the file too;
another generation's request never replaces that receipt, and an unreadable receipt
refuses the landing by name (review R2). An
exact retry of an in-flight generation reaches the existing-generation check before
the gate: that generation was gated when it was admitted, over the inputs its
fingerprint binds.

A generation stays in flight only when something irreversible has happened or cannot be
ruled out (L26 rulings of 2026-10-05, findings 1 and 4). A refusal after admission that
published nothing (a refused publication, the stricter check finding the memory checkout
changed since it was judged) cancels its generation in the same call and restores the
closing, the ignore rule and the index; the same request then starts a fresh generation,
any number of times, and that generation judges the memory checkout as it is then.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.memory_cache import ignore_memory_cache
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.closeout.input import CloseoutCorrectedCall, EffectiveCloseoutInput
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.models.lifecycles.operation import (
    GatePolicyRuleSnapshot,
    LifecycleOperationRecord,
)
from agents_remember.models.memory_content_excludes import MEMORY_CONTENT_EXCLUDES
from agents_remember.worktrees.closeout_input import (
    corrected_closeout_arguments,
    normalize_closeout_input,
    raw_closeout_messages,
)
from agents_remember.worktrees.cutover_lock import CUTOVER_LOCK_CODE
from agents_remember.worktrees.integration.configured_contract_authority import (
    reread_configured_contract,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_execution import (
    execute_or_require_direct_landing_recovery,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    DirectLandingRuntime,
    direct_landing_record,
    direct_landing_store,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_recovery_state import (
    cancelled_unpublished,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_candidate import (
    LifecycleOperationCandidate,
    LifecycleOperationCandidateBinding,
    lifecycle_operation_candidate,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    operation_state_fingerprint,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    operation_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.integration.mutation_evidence import git_mutation_snapshot
from agents_remember.worktrees.knowledge_gate import (
    ClosingReceiptError,
    DirectGenerationState,
    HistoryClosing,
    checkout_memory_converted,
    close_owner_history,
    direct_gate_source,
    direct_gate_verdict,
    forget_direct_closing,
    keep_direct_closing,
    leaf_cutover_refusal,
    settle_direct_closing,
)
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    require_git,
    worktree_candidate_tree,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


@dataclass(frozen=True)
class DirectLandingRequest:
    """One branch-addressed direct landing of an exact series code commit.

    ``candidate_tree`` is the staged candidate tree the owner gated pre-commit
    through the Dagger ``--source``/``--repository-bundle`` contract; when given,
    the landing verifies the branch HEAD tree equals it before committing.
    """

    contract_path: str
    code_commit: str
    memory_commit_message: str | None = None
    intent_note: str = ""
    candidate_tree: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class _DirectRequestIdentity:
    config: McpRuntimeConfig
    contract: WorktreeContract
    request: DirectLandingRequest
    effective_input: EffectiveCloseoutInput
    code_commit: str
    candidate_tree: str


@dataclass(frozen=True)
class _JudgedHistoryClosing(HistoryClosing):
    """The closed candidate's exact Git state and reversible pre-admission preparation."""

    judged: GitMutationSnapshot
    ignore_before: HistoryClosing

    def restore(self) -> None:
        super().restore()
        self.ignore_before.restore()


def direct_landing(
    config: McpRuntimeConfig,
    request: DirectLandingRequest,
    admitted_contract: WorktreeContract,
) -> dict[str, object]:
    """Run the direct landing under the integration authority lock.

    Validates the effective message plan before lane authority or Git. The code
    commit itself is verified, never created: the developer has already
    committed the candidate on the series branch in direct mode.
    """
    require_direct_landing_enabled(config)
    return _direct_landing_after_policy(config, request, admitted_contract)


def require_direct_landing_enabled(config: McpRuntimeConfig) -> None:
    """Refuse the branch-addressed leaf route before inspecting its contract."""

    if not config.direct_execution_enabled:
        raise DirectLandingError(
            "direct-landing-policy-disabled",
            "direct landing is disabled by policy; enable directExecutionEnabled "
            "in the MCP authority settings for sanctioned direct execution",
        )


def _direct_landing_after_policy(
    config: McpRuntimeConfig,
    request: DirectLandingRequest,
    admitted_contract: WorktreeContract,
) -> dict[str, object]:
    contract = admitted_contract
    contract_path = contract.contract_path
    if contract.kind != "series":
        raise DirectLandingError(
            "direct-landing-series-required",
            "direct landing binds the task-root series contract "
            f"(series-contract.md); {contract_path} is a {contract.kind} contract",
        )
    corrected_arguments = corrected_closeout_arguments(
        contract_path.as_posix(),
        code_commit="<exact series code commit>",
        intent_note="<developer intent>",
    )
    if request.dry_run:
        corrected_arguments["dry_run"] = True
    effective_input = normalize_closeout_input(
        contract,
        raw_closeout_messages(
            code=None,
            memory=request.memory_commit_message,
        ),
        route="direct-landing",
        corrected_call=CloseoutCorrectedCall(
            tool="direct_landing",
            arguments=corrected_arguments,
        ),
    )
    if not request.intent_note.strip():
        raise DirectLandingError(
            "direct-landing-intent-required",
            "direct landing requires a non-empty intent note (the commit approval)",
        )
    code_commit = request.code_commit.strip()
    if not code_commit:
        raise DirectLandingError(
            "direct-landing-code-commit-required",
            "direct landing requires the exact series code commit to verify",
        )
    current, _location = reread_configured_contract(
        contract,
        config.config_path.as_posix(),
    )
    if current != contract:
        raise DirectLandingError(
            "direct-landing-contract-changed",
            "series contract changed before direct landing",
        )
    if request.dry_run:
        return _direct_landing_preview(current, request, effective_input, code_commit)
    return _start_or_observe_direct_landing(config, current, request, effective_input, code_commit)


def _verify_code_commit(contract, code_commit: str, candidate_tree: str | None) -> str:
    """Verify the exact code commit is the current series branch HEAD.

    When ``candidate_tree`` is given (the gated staged candidate), the branch
    HEAD tree must equal it: the Dagger ``--source``/``--repository-bundle``
    gate ran over that exact tree before this landing, so a moved branch after
    the gate is refused pre-commit.
    """
    try:
        series_head = branch_commit(contract.code_repo_path, contract.code_work_branch)
    except RuntimeError as exc:
        raise DirectLandingError(
            "direct-landing-code-git-unreadable",
            "direct landing cannot read the accepted code ref",
            observed=public_failure_evidence(
                stage="direct-code-proof",
                side="code",
                name=contract.code_work_branch,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc
    if code_commit != series_head:
        raise DirectLandingError(
            "direct-landing-code-commit-mismatch",
            f"code commit {code_commit} is not the current series branch HEAD "
            f"({series_head}); direct landing verifies the branch HEAD commit, "
            "it does not create one",
        )
    try:
        committed_tree = require_git(
            contract.code_repo_path, ["rev-parse", f"{code_commit}^{{tree}}"]
        )
    except RuntimeError as exc:
        raise DirectLandingError(
            "direct-landing-code-git-unreadable",
            "direct landing cannot read the accepted code tree",
            observed=public_failure_evidence(
                stage="direct-code-proof",
                side="code",
                name=contract.code_work_branch,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc
    if not committed_tree:
        raise DirectLandingError(
            "direct-landing-code-commit-invalid",
            f"cannot resolve the tree of code commit {code_commit}",
        )
    if candidate_tree and committed_tree != candidate_tree:
        raise DirectLandingError(
            "direct-landing-candidate-tree-moved",
            "the series branch HEAD tree moved after the staged candidate was "
            "gated: the Dagger --source/--repository-bundle gate certified "
            f"{candidate_tree}, the branch now carries {committed_tree}; "
            "re-gate the new candidate before landing",
        )
    return committed_tree


def _memory_facts(contract) -> dict[str, object]:
    """Read the external-memory repository and ref facts for the landing."""
    if contract.memory_mode != "external":
        return {"memoryMode": contract.memory_mode}
    if contract.memory_repo_path is None:
        raise DirectLandingError(
            "direct-landing-memory-authority-missing",
            "external-memory direct landing requires the configured memory repository",
        )
    try:
        memory_head = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    except (OSError, RuntimeError) as exc:
        raise DirectLandingError(
            "direct-landing-memory-evidence-unreadable",
            "direct landing cannot read the accepted memory ref",
            observed=public_failure_evidence(
                stage="direct-memory-proof",
                side="memory",
                name=contract.memory_work_branch,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc
    return {
        "memoryMode": "external",
        "memoryBranch": contract.memory_work_branch,
        "memoryHead": memory_head,
    }


def _direct_landing_preview(
    contract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
    code_commit: str,
) -> dict[str, object]:
    _verify_code_commit(contract, code_commit, request.candidate_tree)
    memory = _memory_facts(contract)
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        _direct_gate_owner(contract, code_commit)  # the preview refuses exactly as the apply
    return {
        "ok": True,
        "operation": "direct_landing",
        "state": "would-land",
        "summary": "Direct landing preview: code commit verified; memory content "
        "would be published or reused.",
        "contractPath": contract.contract_path.as_posix(),
        "codeCommit": code_commit,
        "memoryContentCommit": "",
        "dryRun": True,
        "memory": memory,
        "effectiveInput": effective_input.model_dump(mode="json"),
    }


def _direct_memory_admission_snapshot(contract: WorktreeContract):
    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    try:
        observed_branch = current_branch(memory_repo)
    except RuntimeError as exc:
        raise DirectLandingError(
            "direct-landing-memory-git-unreadable",
            "direct landing cannot read the accepted memory branch",
            observed=public_failure_evidence(
                stage="direct-memory-admission",
                side="memory",
                name=contract.memory_work_branch,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc
    if contract.memory_work_branch and observed_branch != contract.memory_work_branch:
        raise DirectLandingError(
            "direct-landing-memory-branch-mismatch",
            "the memory repository checkout is not on the accepted memory branch",
            expected={"branch": contract.memory_work_branch},
            observed={"branch": observed_branch},
        )
    try:
        if require_git(
            memory_repo,
            ["--no-optional-locks", "status", "--porcelain", "--", ".", ":(exclude)memory.md"],
        ):
            ignore_memory_cache(memory_repo)
        return _retained_memory_snapshot(contract, ".direct-admission.index")
    except (OSError, RuntimeError) as exc:
        raise DirectLandingError(
            "direct-landing-memory-git-unreadable",
            "direct landing cannot capture the accepted memory Git state",
            observed=public_failure_evidence(
                stage="direct-memory-admission",
                side="memory",
                name="git-state",
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc


def _start_or_observe_direct_landing(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
    code_commit: str,
) -> dict[str, object]:
    if contract.memory_mode != "external":
        raise DirectLandingError(
            "direct-landing-memory-required",
            "direct landing currently requires an external memory repository",
        )
    if contract.memory_repo_path is None:
        raise DirectLandingError(
            "direct-landing-memory-authority-missing",
            "external-memory direct landing requires the configured memory repository",
        )
    candidate_tree = (request.candidate_tree or "").strip()
    if not candidate_tree:
        raise DirectLandingError(
            "direct-landing-candidate-tree-required",
            "direct landing apply requires the exact pre-commit gated candidate tree",
        )
    store = direct_landing_store(contract)
    identity = _DirectRequestIdentity(
        config,
        contract,
        request,
        effective_input,
        code_commit,
        candidate_tree,
    )
    current_contract, _location = reread_configured_contract(
        contract,
        config.config_path.as_posix(),
    )
    if current_contract != contract:
        raise DirectLandingError(
            "direct-landing-contract-changed",
            "series contract changed before direct landing admission",
        )
    current = store.read()
    _settle_kept_closing(contract, current)
    if _in_flight_retry(current, identity):
        assert current is not None
        record, created = current, False
    else:
        operation_input, candidate, closing = _prepare_direct_landing_candidate(identity)
        record, created = _admit_direct_landing(
            contract, store, (operation_input, candidate), closing
        )
    if record.status == "completed" and record.result is not None:
        _settle_kept_closing(contract, record)
        return _with_lifecycle_operation(dict(record.result), contract, record)
    if not created and record.status != "running":
        return _direct_landing_observation(contract, record)
    runtime = DirectLandingRuntime(contract, record)
    result = execute_or_require_direct_landing_recovery(contract, runtime)
    completed = runtime.store.read() or runtime.record
    _settle_kept_closing(contract, completed)
    return _with_lifecycle_operation(result, contract, completed)


def _generation_state(record: LifecycleOperationRecord) -> DirectGenerationState:
    if record.status == "completed":
        return "landed"
    return "cancelled" if record.status == "cancelled" else "in-flight"


def _settle_kept_closing(
    contract: WorktreeContract, record: LifecycleOperationRecord | None
) -> None:
    """Decide the kept closings against the series' current generation (``settle_direct_closing``).

    A receipt that cannot be read refuses the landing by name (L09 review R2-5).
    """

    current = None if record is None else record.fingerprint
    state = "cancelled" if record is None else _generation_state(record)
    try:
        settle_direct_closing(contract, current=current, state=state)
    except ClosingReceiptError as exc:
        raise DirectLandingError("direct-landing-closing-receipt-unreadable", str(exc)) from exc


def _in_flight_retry(
    current: LifecycleOperationRecord | None, identity: _DirectRequestIdentity
) -> bool:
    """Whether this request exactly retries the series' in-flight direct-landing generation."""

    if current is None or current.operationKind != "direct-landing":
        return False
    if current.status in {"completed", "cancelled"}:
        return False
    accepted = current.input
    return isinstance(accepted, DirectLandingOperationInput) and _same_request(accepted, identity)


def _same_request(accepted: DirectLandingOperationInput, identity: _DirectRequestIdentity) -> bool:
    return (
        accepted.contractPath,
        accepted.codeCommit,
        accepted.candidateTree,
        accepted.effectiveInput,
        accepted.approvalNote,
    ) == (
        identity.contract.contract_path.as_posix(),
        identity.code_commit,
        identity.candidate_tree,
        identity.effective_input,
        identity.request.intent_note.strip(),
    )


def _admit_direct_landing(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    prepared: tuple[DirectLandingOperationInput, LifecycleOperationCandidate],
    closing: HistoryClosing | None,
) -> tuple[LifecycleOperationRecord, bool]:
    """Create or replay the generation; only a generation created now keeps the closing."""

    if closing is None:
        return _create_direct_landing(contract, store, prepared)
    fingerprint = prepared[1].fingerprint
    kept = False
    try:
        kept = keep_direct_closing(
            contract,
            closing,
            fingerprint,
            closing.ignore_before if isinstance(closing, _JudgedHistoryClosing) else None,
        )
        record, created = _create_direct_landing(contract, store, prepared)
    except ClosingReceiptError as exc:  # the closing could not be recorded: nothing is admitted
        _undo_closing(contract, closing, None)
        raise DirectLandingError("direct-landing-closing-receipt-unwritable", str(exc)) from exc
    except BaseException:
        _undo_closing(contract, closing, fingerprint if kept else None)
        raise
    if not created:
        _undo_closing(contract, closing, fingerprint if kept else None)
    return record, created


def _undo_closing(contract: WorktreeContract, closing: HistoryClosing, kept: str | None) -> None:
    """Restore the closing in process, and drop the receipt this call kept for it (only that one)."""

    closing.restore()
    if kept is not None:
        forget_direct_closing(contract, kept)


def _with_lifecycle_operation(
    result: dict[str, object],
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> dict[str, object]:
    """Expose the same durable generation on first completion and exact retry."""

    return {
        **result,
        "lifecycleOperation": operation_projection(
            record,
            contract=load_contract(contract.contract_path),
        ).model_dump(mode="json", exclude_none=True),
    }


def _prepare_direct_landing_candidate(
    identity: _DirectRequestIdentity,
) -> tuple[DirectLandingOperationInput, LifecycleOperationCandidate, HistoryClosing | None]:
    """The exact operation input and candidate, and the gate's closing of the leaf's file.

    An exact in-flight retry reuses the journal's already-judged input instead of preparing again.
    The snapshot the generation is admitted on must be the tree the gate judged: the closed tree
    when it closed the leaf's file, and the memory head's own tree when the gate found the landing
    to be a replay that commits nothing.
    """
    contract = identity.contract
    code_tree = _verify_code_commit(contract, identity.code_commit, identity.candidate_tree)
    closing = _close_gated_leaf(contract, identity.code_commit)
    replayed = _replayed_head_tree(contract) if closing is None else None
    try:
        operation_input, candidate = _operation_candidate(identity, code_tree)
        judged = closing.judged if isinstance(closing, _JudgedHistoryClosing) else None
        if (judged is not None and operation_input.memoryBefore != judged) or (
            replayed is not None and operation_input.memoryBefore.candidateTree != replayed
        ):
            raise DirectLandingError(
                "direct-landing-memory-candidate-changed",
                "memory worktree or index changed while the invariant gate ran; nothing was "
                "admitted or committed, retry to judge the current tree",
                expected=(
                    judged.model_dump(mode="json") if judged else {"candidateTree": replayed}
                ),
                observed=operation_input.memoryBefore.model_dump(mode="json"),
            )
    except BaseException:
        if closing is not None:
            closing.restore()
        raise
    return operation_input, candidate, closing


def _replayed_head_tree(contract: WorktreeContract) -> str | None:
    """The memory head's tree when converted memory was gated as a replay, else ``None``.

    On converted memory the gate leaves no closing only when the landing is a replay that commits
    nothing (``direct_gate_source`` found the head already paired with this code commit and holding
    the candidate tree). The generation must then be admitted on that very tree: a later edit would
    otherwise be committed by the replay without ever being judged.
    """

    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    if not checkout_memory_converted(memory_repo):
        return None
    return require_git(memory_repo, ["rev-parse", "HEAD^{tree}"])


def _operation_candidate(
    identity: _DirectRequestIdentity, code_tree: str
) -> tuple[DirectLandingOperationInput, LifecycleOperationCandidate]:
    config = identity.config
    contract = identity.contract
    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    memory_before = _direct_memory_admission_snapshot(contract)
    operation_input = DirectLandingOperationInput(
        configPath=config.config_path.as_posix(),
        contractPath=contract.contract_path.as_posix(),
        effectiveInput=identity.effective_input,
        approvalNote=identity.request.intent_note.strip(),
        gatePolicy=_gate_policy_snapshot(config),
        codeCommit=identity.code_commit,
        codeTree=code_tree,
        candidateTree=identity.candidate_tree,
        memoryRepository=memory_repo.resolve().as_posix(),
        memoryBranch=contract.memory_work_branch,
        memoryRef=memory_before.headRef,
        memoryBefore=memory_before,
    )
    candidate = lifecycle_operation_candidate(
        LifecycleOperationCandidateBinding(
            operation_input=operation_input,
            candidate_state=operation_state_fingerprint(contract),
            candidate_tree=identity.candidate_tree,
        )
    )
    return operation_input, candidate


def _memory_content_tree(contract: WorktreeContract) -> str:
    """The exact tree the memory-content commit would record (its own exclusions applied)."""

    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    try:
        return worktree_candidate_tree(
            memory_repo,
            contract.worktree_group / "reports" / ".direct-gate.index",
            exclude_paths=MEMORY_CONTENT_EXCLUDES,
        )
    except (OSError, RuntimeError) as exc:
        raise DirectLandingError(
            "direct-landing-knowledge-gate-refused",
            f"the mandatory invariant gate (MIK-R09) cannot capture the memory candidate: {exc}",
        ) from exc


def _retained_memory_snapshot(contract: WorktreeContract, index_name: str) -> GitMutationSnapshot:
    """Retain the candidate objects and bind the same tree to the disposable Git snapshot."""
    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    retained_tree = _memory_content_tree(contract)
    snapshot = git_mutation_snapshot(
        memory_repo,
        contract.worktree_group / "reports" / index_name,
        memory_cache=True,
    )
    if snapshot.candidateTree != retained_tree:
        raise DirectLandingError(
            "direct-landing-memory-candidate-changed",
            "memory changed while its exact candidate tree was retained; nothing lands",
        )
    return snapshot


def _direct_gate_owner(contract: WorktreeContract, code_commit: str) -> str | None:
    """MIK-R09 rule 3 over the candidate; the leaf whose history file this landing closes.

    ``None`` for unconverted memory, which is probed before anything is captured or written; the
    cutover lock refuses it instead once the repository holds converted memory (MIK-R09 rule 6).
    """

    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    try:
        converted = checkout_memory_converted(memory_repo)
    except RuntimeError as exc:
        raise DirectLandingError("direct-landing-knowledge-gate-refused", str(exc)) from exc
    if not converted:
        locked = leaf_cutover_refusal(contract, "direct landing")
        if locked is not None:
            raise DirectLandingError(f"direct-landing-{CUTOVER_LOCK_CODE}", locked)
        return None
    verdict = direct_gate_verdict(
        contract, code_commit=code_commit, memory_tree=_memory_content_tree(contract)
    )
    if verdict.refusal is not None:
        raise DirectLandingError("direct-landing-knowledge-gate-refused", verdict.refusal)
    return verdict.owner if verdict.applies else None


def _close_gated_leaf(contract: WorktreeContract, code_commit: str) -> HistoryClosing | None:
    """Select an exact history source, prepare the final closed tree, then judge that tree once.

    ``None`` for unconverted memory (nothing is written) and for a replay that commits nothing (the
    head already holds this code commit's memory output; see :func:`_replayed_head_tree`). A refusal
    after the closing restores the file's bytes, so a refused landing leaves the memory checkout as
    it found it.
    """

    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    try:
        converted = checkout_memory_converted(memory_repo)
    except RuntimeError as exc:
        raise DirectLandingError("direct-landing-knowledge-gate-refused", str(exc)) from exc
    if not converted:
        _direct_gate_owner(contract, code_commit)
        return None
    ignore = memory_repo / ".gitignore"
    ignore_previous = ignore.read_bytes() if ignore.is_file() else None
    ignore_before = HistoryClosing(ignore, ignore_previous, ignore_previous)
    closing = None
    try:
        selected = direct_gate_source(
            contract, code_commit=code_commit, memory_tree=_memory_content_tree(contract)
        )
        if selected.refusal is not None:
            raise DirectLandingError("direct-landing-knowledge-gate-refused", selected.refusal)
        if not selected.applies:
            return None
        assert selected.source is not None and selected.owner is not None
        ignore_memory_cache(memory_repo)
        ignore_before = HistoryClosing(ignore, ignore_previous, ignore.read_bytes())
        closing = close_owner_history(memory_repo, selected.owner)
        if closing.path.relative_to(memory_repo).as_posix() != selected.source.history_path:
            raise DirectLandingError(
                "direct-landing-knowledge-gate-refused",
                "the selected open history is not the leaf's latest history source; nothing lands",
            )
        judged = _retained_memory_snapshot(contract, ".direct-judged.index")
        verdict = direct_gate_verdict(
            contract,
            code_commit=code_commit,
            memory_tree=judged.candidateTree,
            source=selected.source,
        )
        if verdict.refusal is not None:
            raise DirectLandingError("direct-landing-knowledge-gate-refused", verdict.refusal)
        refusal = memory_commit_refusal(
            memory_repository=memory_repo,
            candidate_tree=judged.candidateTree,
            bases=(require_git(memory_repo, ["rev-parse", "HEAD"]),),
            paired_code=PairedCode(repository=Path(contract.code_repo_path), commit=code_commit),
            leaf_publication=True,
        )
        if refusal is not None:
            raise DirectLandingError("direct-landing-knowledge-validation-refused", refusal)
        return _JudgedHistoryClosing(
            closing.path, closing.previous, closing.written, judged, ignore_before
        )
    except BaseException:
        if closing is not None:
            closing.restore()
        ignore_before.restore()
        raise


def _create_direct_landing(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    prepared: tuple[DirectLandingOperationInput, LifecycleOperationCandidate],
) -> tuple[LifecycleOperationRecord, bool]:
    """Create the exact journal generation, or replay/replace a terminal one.

    A fresh direct landing is admitted by the request itself: the exact series
    contract, the branch HEAD commit and its tree, the gated candidate tree and
    the effective commit messages. It does not consult, claim or publish a
    closeout door, so there is no waiting-door predecessor to satisfy.
    """

    operation_input, candidate = prepared
    queued = direct_landing_record(contract, operation_input, candidate)
    current = store.read()
    if current is None:
        return store.create(queued)
    if cancelled_unpublished(current):
        # Its own refusal cancelled it before anything was published and restored its closing:
        # the next request, the same one included, starts a fresh generation instead of
        # observing the cancelled one. No other terminal generation is replaced by its own
        # fingerprint: a completed one is replayed, one cancelled by control is observed.
        return store.replace_terminal(queued), True
    if current.fingerprint == queued.fingerprint:
        return current, False
    if current.status in {"completed", "cancelled"} and current.generationDisposition in {
        "cancelled",
        "superseded",
    }:
        return store.replace_terminal(queued), True
    raise DirectLandingError(
        "direct-landing-input-conflict",
        "a fresh direct landing requires one terminal retained generation; "
        "retry, recover, cancel or supersede the accepted one first",
        expected={
            "acceptedFingerprint": current.fingerprint,
            "acceptedDisposition": current.generationDisposition,
        },
        observed={"candidateFingerprint": queued.fingerprint},
    )


def _direct_landing_observation(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> dict[str, object]:
    projection = operation_projection(record, contract=contract).model_dump(
        mode="json", exclude_none=True
    )
    return {
        "ok": False,
        "operation": "direct_landing",
        "state": "refused",
        "status": "direct-landing-operation-action-required",
        "summary": "The accepted direct-landing generation already exists; use its "
        "advertised task-addressed action.",
        "contractPath": record.contractPath,
        "dryRun": False,
        "lifecycleOperation": projection,
    }


def _gate_policy_snapshot(config: McpRuntimeConfig) -> list[GatePolicyRuleSnapshot]:
    return [
        GatePolicyRuleSnapshot(
            kind=rule.kind,
            delegatedRole=rule.delegated_role,
            requireReviewerVerdict=rule.require_reviewer_verdict,
        )
        for rule in config.orchestration.gate_policy.rules
    ]
