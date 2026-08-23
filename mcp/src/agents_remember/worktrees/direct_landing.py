"""Direct landing: code-commit verification + memory commit + ledger row.

The direct landing is the branch-addressed counterpart of the worktree closeout
commit phase for sanctioned direct execution. Where the worktree path stages a
leaf worktree candidate, this operation binds the task-root series contract and
verifies the exact code commit on the series branch, then commits external-
memory content and prepends the code-to-memory ledger row with the same ledger
semantics as the worktree path. Input is normalized before the integration
authority lock. Apply records a durable direct-landing generation before the
first memory or ledger mutation.

The gate stays strictly pre-commit: pass the staged ``candidate_tree`` that the
owner already gated through the Dagger module's ``--source``/``--repository-bundle``
contract, and the landing verifies the branch HEAD tree equals it before any
memory or ledger commit. Commit-then-gate is the accepted-risk exception only
where the developer rules it.

The operation is policy-gated (``directExecutionEnabled``) and deliberately
synchronous: direct mode does not use the ``start_or_observe_operation`` detached
worker. The lane lock serializes execution; the canonical lifecycle journal owns
crash recovery across memory and ledger outputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.kernel.memory_ledger import LedgerError, load_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.closeout_input import CloseoutCorrectedCall, EffectiveCloseoutInput
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.operation import (
    GatePolicyRuleSnapshot,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.closeout_input import (
    corrected_closeout_arguments,
    normalize_closeout_input,
    raw_closeout_messages,
)
from agents_remember.worktrees.integration.configured_contract_authority import (
    reread_configured_contract,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_execution import (
    direct_landing_input,
    execute_or_require_direct_landing_recovery,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    DirectLandingRuntime,
    direct_landing_record,
    direct_landing_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_candidate import (
    lifecycle_operation_candidate,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    operation_state_fingerprint,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_lease import (
    contract_lifecycle_lease,
    require_lifecycle_operation_compatible,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    operation_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.integration.mutation_evidence import git_mutation_snapshot
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    is_ancestor,
    require_git,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


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
    ledger_commit_message: str | None = None
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
    """Refuse disabled direct execution before inspecting a contract address."""

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
            ledger=request.ledger_commit_message,
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
    with (
        contract_lifecycle_lease(contract),
        integration_authority_lock(config.coordination_root, contract.repo_name),
    ):
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
        require_lifecycle_operation_compatible(current, operation_kind="direct-landing")
        return _start_or_observe_direct_landing(
            config, current, request, effective_input, code_commit
        )


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
    """Read the external-memory repository and ledger facts for the landing."""
    if contract.memory_mode != "external":
        return {"memoryMode": contract.memory_mode}
    if contract.memory_repo_path is None or contract.ledger_path is None:
        raise DirectLandingError(
            "direct-landing-memory-authority-missing",
            "external-memory direct landing requires the configured memory "
            "repository and ledger path",
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
    _load_direct_ledger(contract.ledger_path)
    return {
        "memoryMode": "external",
        "memoryBranch": contract.memory_work_branch,
        "memoryHead": memory_head,
        "ledgerParsed": True,
    }


def _direct_landing_preview(
    contract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
    code_commit: str,
) -> dict[str, object]:
    _verify_code_commit(contract, code_commit, request.candidate_tree)
    memory = _memory_facts(contract)
    return {
        "ok": True,
        "operation": "direct_landing",
        "state": "would-land",
        "summary": "Direct landing preview: code commit verified; memory and ledger "
        "commits would be created.",
        "contractPath": contract.contract_path.as_posix(),
        "codeCommit": code_commit,
        "memoryContentCommit": "",
        "ledgerCommit": "",
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
        return git_mutation_snapshot(
            memory_repo,
            contract.worktree_group / "reports" / ".direct-admission.index",
        )
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
    contract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
    code_commit: str,
) -> dict[str, object]:
    if contract.memory_mode != "external":
        raise DirectLandingError(
            "direct-landing-memory-required",
            "direct landing currently requires external memory so the ledger row "
            "has a real mapping to commit; internal/disabled memory has no ledger",
        )
    if contract.memory_repo_path is None or contract.ledger_path is None:
        raise DirectLandingError(
            "direct-landing-memory-authority-missing",
            "external-memory direct landing requires the configured memory "
            "repository and ledger path",
        )
    candidate_tree = (request.candidate_tree or "").strip()
    if not candidate_tree:
        raise DirectLandingError(
            "direct-landing-candidate-tree-required",
            "direct landing apply requires the exact pre-commit gated candidate tree",
        )
    memory_repo = contract.memory_repo_path
    store = direct_landing_store(contract)
    current = store.read()
    code_tree = _verify_code_commit(contract, code_commit, candidate_tree)
    memory_before = _direct_memory_admission_snapshot(contract)
    _load_direct_ledger(contract.ledger_path)
    ledger_text = _read_direct_ledger_text(contract.ledger_path)
    operation_input = DirectLandingOperationInput(
        configPath=config.config_path.as_posix(),
        contractPath=contract.contract_path.as_posix(),
        effectiveInput=effective_input,
        approvalNote=request.intent_note.strip(),
        gatePolicy=_gate_policy_snapshot(config),
        codeCommit=code_commit,
        codeTree=code_tree,
        candidateTree=candidate_tree,
        memoryRepository=memory_repo.resolve().as_posix(),
        memoryBranch=contract.memory_work_branch,
        memoryRef=memory_before.headRef,
        memoryBefore=memory_before,
        ledgerPath=contract.ledger_path.resolve().as_posix(),
        ledgerBeforeText=ledger_text,
        ledgerBeforeSha256=_text_sha256(ledger_text),
    )
    candidate = lifecycle_operation_candidate(
        operation_input,
        candidate_state=operation_state_fingerprint(contract),
        candidate_tree=candidate_tree,
        integration_authority=None,
    )
    proposed = direct_landing_record(contract, operation_input, candidate)
    if current is not None:
        identity = _DirectRequestIdentity(
            config,
            contract,
            request,
            effective_input,
            code_commit,
            candidate_tree,
        )
        if _same_direct_request(current, identity):
            if current.status == "completed" and current.result is not None:
                return dict(current.result)
            return _direct_landing_observation(contract, current)
        cancelled_successor = (
            current.status == "cancelled" and current.cancellationEvidence is not None
        )
        completed_successor = _completed_direct_candidate_advanced(
            contract,
            current,
            code_commit=code_commit,
        )
        if not cancelled_successor and not completed_successor:
            raise DirectLandingError(
                "direct-landing-input-conflict",
                "changed direct-landing intent cannot amend the accepted generation; "
                "recover or safely dispose that generation first",
                expected={
                    "acceptedFingerprint": current.fingerprint,
                    "acceptedCodeCommit": direct_landing_input(current).codeCommit,
                },
                observed={
                    "candidateFingerprint": proposed.fingerprint,
                    "candidateCodeCommit": code_commit,
                },
            )
        record = store.replace_terminal(proposed)
        created = True
    else:
        record, created = store.create(proposed)
    if not created:
        raise RuntimeError("direct landing generation appeared during serialized admission")
    runtime = DirectLandingRuntime(contract, record)
    return execute_or_require_direct_landing_recovery(contract, runtime)


def _completed_direct_candidate_advanced(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    *,
    code_commit: str,
) -> bool:
    if record.status != "completed" or record.result is None:
        return False
    accepted = direct_landing_input(record)
    return code_commit != accepted.codeCommit and is_ancestor(
        contract.code_repo_path,
        accepted.codeCommit,
        code_commit,
    )


def _same_direct_request(
    record: LifecycleOperationRecord,
    identity: _DirectRequestIdentity,
) -> bool:
    accepted = direct_landing_input(record)
    expected = {
        "configPath": identity.config.config_path.as_posix(),
        "contractPath": identity.contract.contract_path.as_posix(),
        "effectiveInput": identity.effective_input,
        "approvalNote": identity.request.intent_note.strip(),
        "gatePolicy": _gate_policy_snapshot(identity.config),
        "codeCommit": identity.code_commit,
        "candidateTree": identity.candidate_tree,
    }
    return not any(getattr(accepted, field) != value for field, value in expected.items())


def _direct_landing_observation(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> dict[str, object]:
    projection = operation_projection(record, contract=contract).model_dump(
        mode="json", exclude_none=True
    )
    return {
        "ok": True,
        "operation": "direct_landing",
        "state": record.status,
        "summary": "The accepted direct-landing generation already exists; use its "
        "advertised task-addressed action.",
        "contractPath": record.contractPath,
        "dryRun": False,
        "lifecycleOperation": projection,
    }


def _load_direct_ledger(path: Path):
    try:
        return load_ledger(path)
    except (LedgerError, OSError) as exc:
        raise DirectLandingError(
            "direct-landing-ledger-invalid",
            "direct landing cannot parse the accepted ledger",
            observed=public_failure_evidence(
                stage="direct-ledger-read",
                side="ledger",
                name=path.name,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc


def _read_direct_ledger_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DirectLandingError(
            "direct-landing-ledger-unreadable",
            "direct landing cannot read the accepted ledger bytes",
            observed=public_failure_evidence(
                stage="direct-ledger-read",
                side="ledger",
                name=path.name,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc


def _gate_policy_snapshot(config: McpRuntimeConfig) -> list[GatePolicyRuleSnapshot]:
    return [
        GatePolicyRuleSnapshot(
            kind=rule.kind,
            delegatedRole=rule.delegated_role,
            requireReviewerVerdict=rule.require_reviewer_verdict,
        )
        for rule in config.orchestration.gate_policy.rules
    ]


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
