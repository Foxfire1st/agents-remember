"""Direct landing: code-commit verification + memory commit + ledger row.

The direct landing is the branch-addressed counterpart of the worktree closeout
commit phase for sanctioned direct execution. Where the worktree path stages a
leaf worktree candidate, this operation binds the task-root series contract and
verifies the exact code commit on the series branch, then commits external-
memory content and prepends the code-to-memory ledger row with the same ledger
semantics as the worktree path. Input is normalized before the integration
authority lock; the synchronous memory/ledger durability seam remains separate.

The gate stays strictly pre-commit: pass the staged ``candidate_tree`` that the
owner already gated through the Dagger module's ``--source``/``--repository-bundle``
contract, and the landing verifies the branch HEAD tree equals it before any
memory or ledger commit. Commit-then-gate is the accepted-risk exception only
where the developer rules it.

The operation is policy-gated (``directExecutionEnabled``) and deliberately
synchronous: direct mode has no worktree group and does not use the
``start_or_observe_operation`` detached worker. It uses a lock-serialized synchronous
validate-then-mutate execution pattern. The lock prevents concurrent lane use only; it
provides neither rollback nor durable crash recovery across memory/ledger outputs.
L2-R11 and L5-R15 own that durability work.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.closeout_input import CloseoutCorrectedCall, EffectiveCloseoutInput
from agents_remember.worktrees.closeout_input import (
    corrected_closeout_arguments,
    normalize_closeout_input,
    raw_closeout_messages,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    commit_if_dirty,
    current_branch,
    ensure_git_identity,
    is_ancestor,
    require_git,
)
from agents_remember.worktrees.worktree_contract import ContractError, load_contract


class DirectLandingError(ValueError):
    """The direct landing request is malformed or violates the current facts."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(f"{status}: {detail}")


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


def direct_landing(
    config: McpRuntimeConfig,
    request: DirectLandingRequest,
) -> dict[str, object]:
    """Run the direct landing under the integration authority lock.

    Validates the effective message plan before lane authority or Git. The code
    commit itself is verified, never created: the developer has already
    committed the candidate on the series branch in direct mode.
    """
    if not config.direct_execution_enabled:
        raise DirectLandingError(
            "direct-landing-policy-disabled",
            "direct landing is disabled by policy; enable directExecutionEnabled "
            "in the MCP authority settings for sanctioned direct execution",
        )
    contract_path = require_within_coordination(config, request.contract_path, "contract_path")
    try:
        contract = load_contract(contract_path)
    except ContractError as exc:
        raise DirectLandingError(
            "direct-landing-contract-invalid",
            f"direct landing requires the task-root series contract: {exc}",
        ) from exc
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
    with integration_authority_lock(config.coordination_root, contract.repo_name):
        current = load_contract(contract_path)
        if current != contract:
            raise DirectLandingError(
                "direct-landing-contract-changed",
                "series contract changed before direct landing",
            )
        if request.dry_run:
            return _direct_landing_preview(current, request, effective_input, code_commit)
        return _direct_landing_apply(current, request, effective_input, code_commit)


def _verify_code_commit(contract, code_commit: str, candidate_tree: str | None) -> str:
    """Verify the exact code commit is the current series branch HEAD.

    When ``candidate_tree`` is given (the gated staged candidate), the branch
    HEAD tree must equal it: the Dagger ``--source``/``--repository-bundle``
    gate ran over that exact tree before this landing, so a moved branch after
    the gate is refused pre-commit.
    """
    series_head = branch_commit(contract.code_repo_path, contract.code_work_branch)
    if code_commit != series_head:
        raise DirectLandingError(
            "direct-landing-code-commit-mismatch",
            f"code commit {code_commit} is not the current series branch HEAD "
            f"({series_head}); direct landing verifies the branch HEAD commit, "
            "it does not create one",
        )
    committed_tree = require_git(contract.code_repo_path, ["rev-parse", f"{code_commit}^{{tree}}"])
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
        load_ledger(contract.ledger_path)
    except LedgerError as exc:
        raise DirectLandingError(
            "direct-landing-ledger-invalid", f"direct landing cannot read the ledger: {exc}"
        ) from exc
    return {
        "memoryMode": "external",
        "memoryBranch": contract.memory_work_branch,
        "memoryHead": branch_commit(contract.memory_repo_path, contract.memory_work_branch),
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


def _direct_landing_apply(
    contract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
    code_commit: str,
) -> dict[str, object]:
    _verify_code_commit(contract, code_commit, request.candidate_tree)
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
    memory_repo = contract.memory_repo_path
    ensure_git_identity(memory_repo)
    if contract.memory_work_branch and (current_branch(memory_repo) != contract.memory_work_branch):
        raise DirectLandingError(
            "direct-landing-memory-branch-mismatch",
            "the memory repository checkout is not on the series memory branch "
            f"({contract.memory_work_branch}); direct landing commits the ledger "
            "on the exact series memory branch",
        )

    # Memory content commit (only when dirty) -- same semantics as the worktree
    # external closeout path.
    memory_commit = commit_if_dirty(
        memory_repo,
        effective_input.message_for("memory"),
    )
    ledger = load_ledger(contract.ledger_path)
    existing = find_mapping(ledger, code_commit)
    if existing is not None and existing.memory_commit == memory_commit:
        ledger_commit = branch_commit(memory_repo, contract.memory_work_branch)
    else:
        if existing is not None:
            raise DirectLandingError(
                "direct-landing-ledger-conflict",
                f"ledger already maps code commit {code_commit} to "
                f"{existing.memory_commit}, not {memory_commit}; reconcile the "
                "memory branch before landing",
            )
        write_ledger(
            contract.ledger_path,
            prepend_mapping(ledger, code_commit, memory_commit),
        )
        require_git(memory_repo, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(
            memory_repo,
            effective_input.message_for("ledger"),
        )
    if not ledger_commit or not is_ancestor(memory_repo, memory_commit, ledger_commit):
        raise DirectLandingError(
            "direct-landing-ledger-unreachable",
            "the committed ledger row does not reach the memory content commit",
        )
    return {
        "ok": True,
        "operation": "direct_landing",
        "state": "landed",
        "summary": "Direct landing: code commit verified and the memory + ledger "
        "commits landed on the series memory branch.",
        "contractPath": contract.contract_path.as_posix(),
        "codeCommit": code_commit,
        "memoryContentCommit": memory_commit,
        "ledgerCommit": ledger_commit,
        "dryRun": False,
        "memory": {
            "memoryMode": "external",
            "memoryBranch": contract.memory_work_branch,
            "memoryHead": ledger_commit,
        },
        "effectiveInput": effective_input.model_dump(mode="json"),
    }
