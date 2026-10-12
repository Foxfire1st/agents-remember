"""Direct landing: exact code-commit verification or creation, and memory content publication.

The direct landing is the branch-addressed counterpart of the worktree closeout
commit phase for granted direct execution. Where the worktree path stages a
leaf worktree candidate, this operation binds the task-root series contract and
either verifies the exact code commit on the series branch or creates it from
the leaf's uncommitted changes in that checkout, then commits external-
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

The operation is grant-gated (a direct-execution grant per task) and deliberately
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

from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from agents_remember.controlplane.enforcement import CLOSEOUT_GATE_KIND
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.direct_execution_grants import (
    DirectGrantError,
    require_grant,
)
from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.kernel.memory_cache import ignore_memory_cache
from agents_remember.kernel.primitives.gate_policy import (
    DecisionRole,
    GatePolicyRule,
    make_gate_policy,
)
from agents_remember.kernel.primitives.gate_vocab import GateKind
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.closeout.input import (
    CloseoutCorrectedCall,
    CloseoutLegPlan,
    EffectiveCloseoutInput,
)
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.models.lifecycles.operation import (
    GatePolicyRuleSnapshot,
    LifecycleOperationRecord,
)
from agents_remember.models.memory_content_excludes import MEMORY_CONTENT_EXCLUDES
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.events import now_iso
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_input import (
    corrected_closeout_arguments,
    normalize_closeout_input,
    raw_closeout_messages,
    resolve_closeout_plan,
)
from agents_remember.worktrees.cutover_lock import CUTOVER_LOCK_CODE
from agents_remember.worktrees.disabled_memory import (
    OPT_OUT_STATUS,
    is_disabled_contract,
    opt_out_detail_for_contract,
)
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
    classify_direct_landing_recovery,
)
from agents_remember.worktrees.integration.lifecycle.generation import resume
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
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    git_mutation_snapshot,
    prove_git_commit,
)
from agents_remember.worktrees.knowledge_gate import (
    ClosingReceiptError,
    DirectGateVerdict,
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
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout import _validate_closeout_source_state
from agents_remember.worktrees.modules.closeout_lineage import SourceLineageRefusal
from agents_remember.worktrees.modules.git import (
    branch_commit,
    checkout_holding_branch,
    current_branch,
    ensure_git_identity,
    head_commit,
    require_git,
    stage_tree,
    worktree_candidate_tree,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


@dataclass(frozen=True)
class DirectLandingRequest:
    """One branch-addressed direct landing of an exact series code commit.

    ``leaf_task_document_ref`` names the leaf this landing lands: only a leaf
    with a direct-execution grant lands directly. ``code_commit`` is the exact
    series code commit to verify; ``None`` takes the uncommitted changes in the
    checkout holding the master's branch and creates the code commit on that
    branch. ``code_commit_message`` is the message of a created code commit; a
    call that verifies an existing commit gives none. ``candidate_tree`` is the
 staged candidate tree the owner gated pre-commit through the Dagger
 ``--source``/``--repository-bundle`` contract; when given, the landing
 verifies the branch HEAD tree equals it before committing.
    ``leaf_id`` names the leaf whose history file this landing judges and closes
    (MIK-R52 rule 8); without it the owner is inferred from the one open history
    file, as before.
 ``bound_leaf_id`` is set by the landing itself after it proves the named
 leaf belongs to the admitted series contract; callers leave it empty.
    """

    contract_path: str
    code_commit: str | None
    leaf_task_document_ref: TaskDocumentRef
    code_commit_message: str | None = None
    memory_commit_message: str | None = None
    intent_note: str = ""
    candidate_tree: str | None = None
    dry_run: bool = False
    leaf_id: str | None = None
    bound_leaf_id: str | None = None


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

    Validates the effective message plan before lane authority or Git. With an
    exact ``code_commit`` that commit is verified, never created; without one
    the uncommitted changes in the checkout holding the series branch become
    the created code commit, behind the same gates as any leaf closeout.
    """
    require_direct_grant(config, request.leaf_task_document_ref)
    bound_leaf_id = _require_bound_leaf(config, admitted_contract, request.leaf_task_document_ref)
    request = replace(request, bound_leaf_id=bound_leaf_id)
    if is_disabled_contract(admitted_contract):
        # A contract that carries the removed mode never lands directly
        # (MIK-R50 R4a): the apply refuses with the opt-out refusal, as the
        # preview does.
        raise DirectLandingError(
            OPT_OUT_STATUS,
            opt_out_detail_for_contract(admitted_contract, "direct_landing"),
        )
    validated_contract = _validated_source_contract(admitted_contract, dry_run=request.dry_run)
    return _direct_landing_after_policy(config, request, validated_contract)


def require_direct_grant(config: McpRuntimeConfig, ref: TaskDocumentRef) -> None:
    """Refuse the branch-addressed leaf route before inspecting its contract."""
    try:
        require_grant(config.coordination_root, ref)
    except DirectGrantError as error:
        status = (
            "direct-landing-leaf-without-grant"
            if error.status == "direct-grant-missing"
            else "direct-landing-grant-unreadable"
        )
        raise DirectLandingError(
            status,
            f"direct landing for {ref.repository}:{ref.path} is refused: {error.detail}",
        ) from error


def _require_bound_leaf(
    config: McpRuntimeConfig, contract: WorktreeContract, ref: TaskDocumentRef
) -> str:
    """The id of the named leaf when it belongs to the admitted series contract.

    Proves repository, immediate master and series contract are the same
    authority: the reference resolves to a canonical subTask, its task folder
    is the contract's task root, and the master's document lists the leaf.
    A grant for a foreign repository, another master, or another task's leaf
    is refused before any durable effect, naming the leaf and the contract.
    """
    try:
        resolved = TaskDocumentTopology(config.coordination_root).resolve(ref)
    except Exception as error:
        raise DirectLandingError(
            "direct-landing-leaf-missing",
            f"direct landing for {ref.repository}:{ref.path} is refused: "
            f"no canonical task document resolves ({error}); land the leaf "
            "bound to this series contract",
        ) from error
    doc = resolved.document
    if doc.kind != "subTask":
        raise DirectLandingError(
            "direct-landing-leaf-only",
            f"direct landing for {ref.repository}:{ref.path} is refused: "
            f"{ref.repository}:{ref.path} is {doc.kind}, not a leaf; land the "
            "leaf bound to this series contract",
        )
    task_root = resolved.path.parent.resolve(strict=False)
    if task_root != contract.task_root.resolve(strict=False):
        raise DirectLandingError(
            "direct-landing-foreign-leaf",
            f"direct landing for {ref.repository}:{ref.path} is refused: the leaf "
            f"lives in {resolved.path.parent.as_posix()}, not in this series "
            f"contract's task ({contract.task_root.as_posix()}); land the leaf "
            "bound to this series contract",
        )
    try:
        master = read_task_doc(task_root / "task.json")
    except (OSError, ValueError) as error:
        raise DirectLandingError(
            "direct-landing-master-unreadable",
            f"direct landing for {ref.repository}:{ref.path} is refused: its "
            f"master document cannot be read ({error}); repair the task and retry",
        ) from error
    if master.kind != "master" or all(row.number != doc.id for row in master.subTasks):
        raise DirectLandingError(
            "direct-landing-foreign-leaf",
            f"direct landing for {ref.repository}:{ref.path} is refused: no master "
            f"row owns leaf {doc.id} in {task_root.as_posix()}; land the leaf "
            "bound to this series contract",
        )
    return doc.id


def _direct_effective_input(
    contract: WorktreeContract,
    request: DirectLandingRequest,
    creating: bool,
    corrected_arguments: dict[str, object],
) -> EffectiveCloseoutInput:
    """The normalized closeout input for the direct landing route.

    Verification keeps the route's verified-existing plan: the code commit is
    judged as an existing object, so the code leg stays not-applicable. A
    create enables the code leg: the route creates the series code commit
    under the required up-front message, and the admitted generation carries
    the code mutation intent through to commit proof, marking the
    irreversible boundary before the memory leg (F01). The create message is
    still required up front (``direct-landing-code-message-required``).
    """

    if creating and not (request.code_commit_message or "").strip():
        raise DirectLandingError(
            "direct-landing-code-message-required",
            "direct landing without a code commit requires code_commit_message: "
            "the message of the code commit it creates",
        )
    plan = resolve_closeout_plan(contract, route="direct-landing")
    if creating:
        plan = plan.model_copy(
            update={
                "code": CloseoutLegPlan(
                    state="enabled",
                    reason="direct landing creates the series code commit",
                )
            }
        )
    return normalize_closeout_input(
        contract,
        raw_closeout_messages(
            code=(request.code_commit_message or "").strip() if creating else None,
            memory=request.memory_commit_message,
        ),
        route="direct-landing",
        corrected_call=CloseoutCorrectedCall(
            tool="direct_landing",
            arguments=corrected_arguments,
        ),
        resolved_plan=plan,
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
    creating = request.code_commit is None
    effective_input = _direct_effective_input(contract, request, creating, corrected_arguments)
    if not request.intent_note.strip():
        raise DirectLandingError(
            "direct-landing-intent-required",
            "direct landing requires a non-empty intent note (the commit approval)",
        )
    if creating:
        if request.dry_run:
            return _direct_create_preview(
                _reread_series_contract(config, contract), request, effective_input
            )
        if not (request.candidate_tree or "").strip():
            raise DirectLandingError(
                "direct-landing-candidate-tree-required",
                "direct landing apply that creates the code commit requires the exact "
                "previewed candidate tree: preview first, then apply the tree it named; "
                "nothing was committed",
            )
        return _apply_create_landing(
            config, _reread_series_contract(config, contract), request, effective_input
        )
    else:
        code_commit = (request.code_commit or "").strip()
        if not code_commit:
            raise DirectLandingError(
                "direct-landing-code-commit-required",
                "direct landing requires the exact series code commit to verify",
            )
    current = _reread_series_contract(config, contract)
    if request.dry_run:
        return _direct_landing_preview(current, request, effective_input, code_commit)
    return _start_or_observe_direct_landing(
        config, current, request, effective_input, code_commit, review_claimed=creating
    )


def _reread_series_contract(config: McpRuntimeConfig, contract: WorktreeContract):
    """Reread the configured contract, refusing a series contract that moved."""
    current, _location = reread_configured_contract(
        contract,
        config.config_path.as_posix(),
    )
    if current != contract:
        raise DirectLandingError(
            "direct-landing-contract-changed",
            "series contract changed before direct landing",
        )
    return current


_DIRECT_CODE_CANDIDATE_INDEX = ".direct-code-candidate.index"
_DIRECT_CODE_MUTATION_INDEX = ".direct-code-mutation.index"

_UNFINISHED_CHECKOUT_MARKERS = (
    ("MERGE_HEAD", "a merge"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase"),
)


def _validated_source_contract(
    contract: WorktreeContract, *, dry_run: bool
) -> WorktreeContract:
    """Run the ordinary closeout source-lineage check on the direct route (F01).

    The direct route judges the same super -> master -> leaf chain the
    enclosure closeout proves, healing a plain fast-forward the same way and
    refusing a moved source with the same lineage words. There is no policy
    difference left to rule: preview and apply both carry this check.
    """
    try:
        return _validate_closeout_source_state(contract, dry_run=dry_run)
    except SourceLineageRefusal as exc:
        raise DirectLandingError(
            "direct-landing-source-lineage-refused",
            f"direct landing refuses on moved source lineage: {exc}",
        ) from exc


@dataclass(frozen=True)
class DirectPrecodeCheck:
    """One ordinary closeout pre-code check and its direct-create disposition."""

    id: str
    ordinary_check: str
    direct_disposition: str
    detail: str


DIRECT_CREATE_PRECODE_CENSUS: tuple[DirectPrecodeCheck, ...] = (
    DirectPrecodeCheck(
        id="entry-opt-out",
        ordinary_check="refuse contracts carrying the removed memory mode",
        direct_disposition="same",
        detail="is_disabled_contract refuses at entry and in preview, like the ordinary route",
    ),
    DirectPrecodeCheck(
        id="source-lineage",
        ordinary_check="prove super -> master -> leaf source lineage, heal a plain fast-forward",
        direct_disposition="same",
        detail="_validate_closeout_source_state runs first in preview and apply; a moved source refuses",
    ),
    DirectPrecodeCheck(
        id="plan-contract-identity",
        ordinary_check="bind the normalized input to the route plan and contract",
        direct_disposition="same",
        detail="_direct_effective_input normalizes through resolve_closeout_plan for direct-landing",
    ),
    DirectPrecodeCheck(
        id="commit-messages",
        ordinary_check="require a message for every enabled leg",
        direct_disposition="same",
        detail="memory message via normalize; the create code message is required up front and enables the code leg",
    ),
    DirectPrecodeCheck(
        id="previewed-tree-equality",
        ordinary_check="assert the previewed candidate tree still holds",
        direct_disposition="same",
        detail="a stale or omitted tree refuses with branch, files and index unchanged",
    ),
    DirectPrecodeCheck(
        id="branch-checkout",
        ordinary_check="require a checkout holding the work branch in a finished state",
        direct_disposition="same",
        detail="_checkout_holding_branch plus _refuse_unfinished_checkout, shared helper",
    ),
    DirectPrecodeCheck(
        id="review-claim",
        ordinary_check="claim the review gate before judging",
        direct_disposition="same",
        detail="_claim_direct_review_gate with the ambient gate policy",
    ),
    DirectPrecodeCheck(
        id="invariant-knowledge-judgment",
        ordinary_check="run invariant/knowledge validation over the exact trees before any publication",
        direct_disposition="same",
        detail="the pair judgment runs over the exact code and memory trees before admission",
    ),
    DirectPrecodeCheck(
        id="generation-admission",
        ordinary_check="persist the operation generation before any branch moves",
        direct_disposition="same",
        detail="_admit_direct_landing carries code input/message/tree/ref, pre-state and memory intent",
    ),
    DirectPrecodeCheck(
        id="code-leg-intent-proof",
        ordinary_check="journal per-leg mutation intent and proof, enter the irreversible boundary",
        direct_disposition="same",
        detail="enabled code leg: intent begun before the move, commit-proven after; the flag derives",
    ),
    DirectPrecodeCheck(
        id="closeout-door",
        ordinary_check="claim and prove the closeout door before launch and recovery",
        direct_disposition="ruled-out",
        detail="the direct route never claims doors (admitted L94-THE-TWO-CHECKS "
        "ruling): no two landings act on the same lines at once by rule 11 (a "
        "second direct leaf is refused naming the first; an enclosure leaf is "
        "refused its integration while the direct leaf's changes stand), and a "
        "recovery acts only on its own operation by the admitted generation's "
        "fingerprint and key (a recovery asked for another operation's record "
        "is refused with lifecycle-generation-changed)",
    ),
    DirectPrecodeCheck(
        id="task-intent-binding",
        ordinary_check="bind the generation to a leaf task intent",
        direct_disposition="ruled-out",
        detail="series-addressed delivery binds leafTaskDocumentRef on the input "
        "instead of a task intent (admitted L94-THE-TWO-CHECKS ruling): the "
        "record of a direct landing names its leaf, and a direct landing "
        "without a bound leaf is refused",
    ),
    DirectPrecodeCheck(
        id="grant-and-bound-leaf",
        ordinary_check="no ordinary analog: enclosure authority covers it",
        direct_disposition="direct-only",
        detail="require_direct_grant plus bound-leaf resolution replace door authority at entry",
    ),
)


def _checkout_holding_branch(contract: WorktreeContract) -> Path | None:
    """The checkout whose HEAD is the master's branch, if any holds it."""

    return checkout_holding_branch(contract.code_repo_path, contract.code_work_branch)


def _refuse_unfinished_checkout(checkout: Path) -> None:
    """Refuse before anything is staged: an unfinished action or unmerged paths."""
    for name, action in _UNFINISHED_CHECKOUT_MARKERS:
        marker = require_git(checkout, ["rev-parse", "--path-format=absolute", "--git-path", name])
        if Path(marker).exists():
            raise DirectLandingError(
                "direct-landing-checkout-unfinished",
                f"{marker} exists: {action} is unfinished in this checkout, "
                "so nothing was staged or committed; finish or abort it and rerun",
            )
    unmerged = require_git(checkout, ["diff", "--name-only", "--diff-filter=U"]).splitlines()
    if unmerged:
        raise DirectLandingError(
            "direct-landing-checkout-unmerged",
            f"the checkout has {len(unmerged)} unmerged path(s), so a merge, rebase, "
            "cherry-pick or revert is still in progress with its conflicts unresolved; "
            "nothing was staged or committed",
        )


def _direct_code_candidate(contract: WorktreeContract, checkout: Path) -> tuple[str, list[str]]:
    """The exact tree the checkout would commit, and every path it holds.

    Non-mutating: the tree is captured through a private index, exactly as
    ``git add -A`` over ``HEAD`` would commit it. What this lists is what a
    create commit records; a change that is not the leaf's is taken out of the
    checkout before the preview.
    """
    tree = worktree_candidate_tree(
        checkout, contract.worktree_group / "reports" / _DIRECT_CODE_CANDIDATE_INDEX
    )
    paths = require_git(checkout, ["ls-tree", "-r", "--name-only", tree]).splitlines()
    return tree, paths


def _claim_direct_review_gate(
    contract: WorktreeContract,
    policy: list[GatePolicyRuleSnapshot],
) -> None:
    """Claim the closeout approval before the direct route publishes anything.

    This is the review record of rule 9: the same gate the enclosure closeout
    claims before its code commit, over the same snapshot policy the operation
    carries. A lifecycle with no gate of this kind stays governed by the
    existing approval channel, exactly as on the enclosure route.
    """
    if not contract.lifecycle_id:
        return
    gate_policy = make_gate_policy(
        [
            GatePolicyRule(
                cast(GateKind, item.kind),
                cast(DecisionRole | None, item.delegatedRole),
                item.requireReviewerVerdict,
            )
            for item in policy
        ]
    )
    store = GateStore(observer_logs_root(contract.coordination_root))
    guard = store.claim_approval(
        contract.lifecycle_id,
        kind=CLOSEOUT_GATE_KIND,
        now=now_iso(),
        policy=gate_policy,
        operation_key=None,
    )
    if not guard.permitted:
        raise DirectLandingError(
            "direct-landing-review-refused",
            f"direct landing review record refuses: {guard.reason}",
        )


def _in_flight_proven_code_commit(
    contract: WorktreeContract,
    effective_input: EffectiveCloseoutInput,
    head: str,
    leaf_ref: TaskDocumentRef,
) -> str | None:
    """The proven code commit when this create call only completes a generation.

    The same call again after the code commit exists must complete the memory
    commit and the ledger row without creating a second code commit and without
    spending a second review claim. The generation must belong to the same
    named leaf: a proven commit for another leaf never completes this call.
    """
    record = direct_landing_store(contract).read()
    if record is None or record.operationKind != "direct-landing":
        return None
    if record.status in ("completed", "cancelled"):
        return None
    accepted = record.input
    if not isinstance(accepted, DirectLandingOperationInput):
        return None
    if (
        accepted.codeCommit != head
        or accepted.effectiveInput != effective_input
        or accepted.leafTaskDocumentRef != leaf_ref
    ):
        return None
    return accepted.codeCommit


def _provisional_code_commit(checkout: Path, tree: str, head: str, message: str) -> str:
    """The code commit object for the exact tree, on no ref yet.

    Built with the repository's own signing configuration, like the shared
    publication primitive builds it. The object is dangling until the branch
    moves to it, so any refusal before the move publishes nothing and leaves
    files and index exactly as found.
    """
    try:
        signing = require_git(checkout, ["config", "--type=bool", "--get", "commit.gpgsign"])
    except RuntimeError:
        signing = ""
    sign = ["-S"] if signing.strip() == "true" else []
    text = message if message.endswith("\n") else message + "\n"
    written = run_git(
        checkout,
        ["commit-tree", tree, "-p", head, *sign],
        GitRunnerOptions(input_text=text),
    )
    commit = written.stdout.strip()
    if written.returncode != 0 or not commit:
        raise DirectLandingError(
            "direct-landing-code-publication-refused",
            "the code commit object was not created: "
            f"{written.stderr.strip() or 'git commit-tree failed'}; nothing was committed",
        )
    return commit


def _publish_provisional_code_commit(
    checkout: Path,
    before: GitMutationSnapshot,
    provisional: str,
    tree: str,
) -> None:
    """Move the admitted branch to the judged commit, then prove and stage it.

    The move is one atomic compare-and-swap from the admitted tip: a branch
    that moved after admission refuses with branch, files and index exactly as
    found. Afterwards the index records the tree, so the landed files read
    clean. This is the irreversible boundary: everything before it refuses
    without publishing; everything after it completes the admitted pair.
    """
    head_ref = require_git(checkout, ["symbolic-ref", "--quiet", "HEAD"]).strip()
    if head_ref != before.headRef:
        raise DirectLandingError(
            "direct-landing-candidate-tree-moved",
            f"HEAD names {head_ref or 'no branch (it is detached)'}, not the branch "
            f"{before.headRef} this landing admitted; nothing was committed",
        )
    tip = head_commit(checkout)
    if tip != before.head:
        raise DirectLandingError(
            "direct-landing-candidate-tree-moved",
            f"the branch moved from {before.head} to {tip or 'nothing'} after this "
            "landing admitted it; preview again before landing, nothing was committed",
        )
    moved = run_git(checkout, ["update-ref", before.headRef, provisional, tip])
    if moved.returncode != 0:
        raise DirectLandingError(
            "direct-landing-code-publication-refused",
            f"the branch refused the judged commit: {moved.stderr.strip() or moved}; "
            "nothing was committed",
        )
    if head_commit(checkout) != provisional:
        rollback = run_git(checkout, ["update-ref", before.headRef, tip, provisional])
        raise DirectLandingError(
            "direct-landing-code-publication-refused",
            "the branch did not hold the judged commit after the move "
            f"(take-back {rollback.returncode}); inspect the checkout before retrying",
        )
    if require_git(checkout, ["rev-parse", f"{provisional}^{{tree}}"]) != tree:
        raise DirectLandingError(
            "direct-landing-code-tree-mismatch",
            "the judged commit does not hold the judged candidate tree; "
            "nothing else was committed",
        )
    stage_tree(checkout, tree)


def _resume_retry_if_needed(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
) -> LifecycleOperationRecord | None:
    """The record an exact same-call retry executes, or ``None`` to observe it.

    A running generation executes as-is. An input-required generation resumes
    exactly like the recover control resumes it, unless its classification
    needs a developer decision: genuinely ambiguous states keep the explicit
    refusal and the advertised recover action. Anything else is observed, not
    executed.
    """
    if record.status == "running":
        return record
    if record.status != "input-required":
        return None
    classification = classify_direct_landing_recovery(contract, record)
    if classification.state == "developer-decision":
        raise DirectLandingError(
            classification.status,
            classification.detail,
            expected=classification.expected,
            observed=classification.observed,
        )
    requeued, changed = store.resume_generation(
        resume.requeued_same_generation, expected_generation=record.generation
    )
    if not changed:
        return None
    return requeued


def _retry_create_landing(  # noqa: PLR0913, PLR0917 -- the retry compares the whole request identity
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
    store: LifecycleOperationStore,
    current: LifecycleOperationRecord,
    tree: str,
) -> dict[str, object] | None:
    """Complete the admitted pair when this call exactly retries it, else ``None``.

    A running generation executes as-is; a mechanically recoverable
    input-required one resumes exactly like the recover control resumes it.
    Anything else falls through to a fresh create, which refuses on a
    conflicting in-flight generation.
    """
    retry_identity = _DirectRequestIdentity(
        config, contract, request, effective_input, current.input.codeCommit, tree
    ) if isinstance(current.input, DirectLandingOperationInput) else None
    if retry_identity is None or not _in_flight_retry(current, retry_identity):
        return None
    resumed = _resume_retry_if_needed(contract, store, current)
    if resumed is None:
        return _direct_landing_observation(contract, current)
    runtime = DirectLandingRuntime(contract, resumed)
    result = execute_or_require_direct_landing_recovery(contract, runtime)
    completed = runtime.store.read() or runtime.record
    _settle_kept_closing(contract, completed)
    return _with_lifecycle_operation(result, contract, completed)


def _apply_create_landing(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
) -> dict[str, object]:
    """Create apply behind the pre-code gates: judge, admit, publish, prove, land.

    The pre-code-check census for a created commit, in order: grant and bound
    leaf (at entry), the ordinary super -> master -> leaf source-lineage proof
    (a moved source refuses with the lineage words, like the enclosure
    closeout; no policy difference remains), series authority, intent and
    create message, the exact previewed tree and its equality with the
    checkout, a checkout holding the branch in a finished state, the review
    claim, the invariant/knowledge judgment over the exact code and memory
    trees, and the admitted generation with the history closing. Only then
    does the branch move to the judged commit; the move is compare-and-swapped
    to the admitted tip, parent-linked to the admitted pre-state, and the
    index staged before the memory leg. The admitted generation persists the
    code commit, tree, ref and pre-state before the move, and a later cut
    re-proves that output live against the branch. A repeated same call
    completes the admitted pair without a second code commit; a publish
    refusal before any code output cancels the unpublished generation,
    restores the closing, and preserves files and index.
    """
    checkout = _checkout_holding_branch(contract)
    if checkout is None:
        raise DirectLandingError(
            "direct-landing-no-branch-checkout",
            f"no checkout holds the master's branch {contract.code_work_branch}; "
            "check it out and rerun the direct landing",
        )
    _refuse_unfinished_checkout(checkout)
    tree, _paths = _direct_code_candidate(contract, checkout)
    asserted_tree = (request.candidate_tree or "").strip()
    if asserted_tree != tree:
        raise DirectLandingError(
            "direct-landing-candidate-tree-moved",
            "the checkout no longer matches the previewed candidate tree: "
            f"the preview named {asserted_tree}, the checkout now holds {tree}; "
            "preview again before landing",
        )
    head = head_commit(checkout)
    store = direct_landing_store(contract)
    current = store.read()
    if current is not None and current.operationKind == "direct-landing":
        retried = _retry_create_landing(
            config, contract, request, effective_input, store, current, tree
        )
        if retried is not None:
            return retried
    if tree == require_git(checkout, ["rev-parse", "HEAD^{tree}"]):
        proven = _in_flight_proven_code_commit(
            contract, effective_input, head, request.leaf_task_document_ref
        )
        if proven is not None:
            return _start_or_observe_direct_landing(
                config,
                contract,
                replace(request, code_commit=proven),
                effective_input,
                proven,
                review_claimed=True,
            )
    _claim_direct_review_gate(contract, _gate_policy_snapshot(config))
    before = git_mutation_snapshot(
        checkout, contract.worktree_group / "reports" / _DIRECT_CODE_MUTATION_INDEX
    )
    ensure_git_identity(checkout)
    message = (request.code_commit_message or "").strip()
    provisional = _provisional_code_commit(checkout, tree, head, message)
    identity = _DirectRequestIdentity(config, contract, request, effective_input, provisional, tree)
    closing = _close_gated_leaf(
        contract, provisional, request.bound_leaf_id,
        enforce_owner=not _in_flight_conflict_pending(current),
    )
    operation_input, candidate = _operation_candidate(
        identity, tree, code_commit_message=message, code_before=before
    )
    judged = closing.judged if isinstance(closing, _JudgedHistoryClosing) else None
    replayed = _replayed_head_tree(contract) if closing is None else None
    if (judged is not None and operation_input.memoryBefore != judged) or (
        replayed is not None and operation_input.memoryBefore.candidateTree != replayed
    ):
        if closing is not None:
            closing.restore()
        raise DirectLandingError(
            "direct-landing-memory-candidate-changed",
            "memory worktree or index changed while the invariant gate ran; nothing was "
            "admitted or committed, retry to judge the current tree",
        )
    record, _created = _admit_direct_landing(contract, store, (operation_input, candidate), closing)
    runtime = DirectLandingRuntime(contract, record)
    # The sealed per-leg code intent, journaled before publication (F01): the
    # admitted generation carries the code mutation intent through to commit
    # proof, marking the irreversible boundary before the memory leg.
    code_args = WorktreeArgs(
        contract_path=contract.contract_path,
        closeout_input=effective_input,
        operation_key=record.operationKey,
        operation_progress=runtime.progress,
        recovery_commits=record.recoveryCommits,
    )
    code_intent = begin_git_mutation(
        code_args, leg="code", repository=checkout, expected_output_tree=tree
    )
    try:
        _publish_provisional_code_commit(checkout, before, provisional, tree)
    except DirectLandingError as error:
        runtime.cancel_unpublished(status=error.status, detail=error.detail)
        raise
    # The sealed per-leg code proof: the branch names the admitted commit
    # holding the judged tree under the admitted parent. This journals the
    # commit-proven cell and enters the irreversible boundary, so the same
    # generation completes the pair without another code commit (F01/F04).
    prove_git_commit(code_args, code_intent, repository=checkout, commit=provisional)
    runtime.progress(
        "code-commit",
        {
            "current_command": (
                f"code commit {provisional} published on {before.headRef}: "
                "the admitted pair completes without another code commit"
            ),
        },
    )
    result = execute_or_require_direct_landing_recovery(contract, runtime)
    completed = runtime.store.read() or runtime.record
    _settle_kept_closing(contract, completed)
    return _with_lifecycle_operation(result, contract, completed)


def _direct_create_preview(
    contract: WorktreeContract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
) -> dict[str, object]:
    """Preview the commit the apply would create: every path it would hold."""
    if is_disabled_contract(contract):
        raise DirectLandingError(
            OPT_OUT_STATUS,
            opt_out_detail_for_contract(contract, "direct_landing"),
        )
    checkout = _checkout_holding_branch(contract)
    if checkout is None:
        raise DirectLandingError(
            "direct-landing-no-branch-checkout",
            f"no checkout holds the master's branch {contract.code_work_branch}; "
            "check it out and rerun the direct landing",
        )
    _refuse_unfinished_checkout(checkout)
    tree, paths = _direct_code_candidate(contract, checkout)
    asserted_tree = (request.candidate_tree or "").strip()
    if asserted_tree and asserted_tree != tree:
        raise DirectLandingError(
            "direct-landing-candidate-tree-moved",
            "the checkout no longer matches the previewed candidate tree: "
            f"the preview named {asserted_tree}, the checkout now holds {tree}; "
            "preview again before landing",
        )
    memory = _memory_facts(contract)
    return {
        "ok": True,
        "operation": "direct_landing",
        "state": "would-land",
        "summary": "Direct landing preview: the apply would create the code commit "
        "with exactly the listed paths, then publish memory content.",
        "contractPath": contract.contract_path.as_posix(),
        "codeCommit": "",
        "codeCommitMessage": (request.code_commit_message or "").strip(),
        "candidateTree": tree,
        "candidatePaths": paths,
        "memoryContentCommit": "",
        "dryRun": True,
        "memory": memory,
        "effectiveInput": effective_input.model_dump(mode="json"),
    }


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
    if is_disabled_contract(contract):
        # The preview refuses exactly as the apply does (MIK-R50 R4c).
        raise DirectLandingError(
            OPT_OUT_STATUS,
            opt_out_detail_for_contract(contract, "direct_landing"),
        )
    _verify_code_commit(contract, code_commit, request.candidate_tree)
    memory = _memory_facts(contract)
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        _direct_gate_owner(
            contract,
            code_commit,
            leaf_id=request.bound_leaf_id or request.leaf_id,
        )  # the preview refuses exactly as the apply
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


def _start_or_observe_direct_landing(  # noqa: PLR0913 -- review_claimed rides the create/verify split
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    request: DirectLandingRequest,
    effective_input: EffectiveCloseoutInput,
    code_commit: str,
    *,
    review_claimed: bool = False,
) -> dict[str, object]:
    if is_disabled_contract(contract):
        # No direct landing lands without its memory side (MIK-R50 R4b).
        raise DirectLandingError(
            OPT_OUT_STATUS,
            opt_out_detail_for_contract(contract, "direct_landing"),
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
        if not review_claimed:
            _claim_direct_review_gate(contract, _gate_policy_snapshot(config))
        operation_input, candidate, closing = _prepare_direct_landing_candidate(
            identity, enforce_owner=not _in_flight_conflict_pending(current)
        )
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
    # MIK-R52 rule 8: the named leaf is part of the request identity. A call naming another leaf
    # is not a retry of the in-flight generation, whose kept closing belongs to the admitted leaf.
    return (
        accepted.contractPath,
        accepted.leafTaskDocumentRef,
        accepted.codeCommit,
        accepted.candidateTree,
        accepted.effectiveInput,
        accepted.approvalNote,
        accepted.leafId,
    ) == (
        identity.contract.contract_path.as_posix(),
        identity.request.leaf_task_document_ref,
        identity.code_commit,
        identity.candidate_tree,
        identity.effective_input,
        identity.request.intent_note.strip(),
        identity.request.leaf_id,
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


def _in_flight_conflict_pending(record: LifecycleOperationRecord | None) -> bool:
    """Whether a live generation may refuse this request as a conflict first.

    A running or input-required generation owns the next refusal: the admit's
    ``direct-landing-input-conflict`` names a request that differs from it. The
    foreign-history check below must not pre-empt that refusal, so callers skip
    it while such a generation is live. A terminal generation decides nothing
    anymore, so a new request is judged on its own history again.
    """

    return record is not None and record.status in {"running", "input-required"}


def _prepare_direct_landing_candidate(
    identity: _DirectRequestIdentity, *, enforce_owner: bool = True
) -> tuple[DirectLandingOperationInput, LifecycleOperationCandidate, HistoryClosing | None]:
    """The exact operation input and candidate, and the gate's closing of the leaf's file.

    An exact in-flight retry reuses the journal's already-judged input instead of preparing again.
    The snapshot the generation is admitted on must be the tree the gate judged: the closed tree
    when it closed the leaf's file, and the memory head's own tree when the gate found the landing
    to be a replay that commits nothing.
    """
    contract = identity.contract
    code_tree = _verify_code_commit(contract, identity.code_commit, identity.candidate_tree)
    closing = _close_gated_leaf(
        contract,
        identity.code_commit,
        identity.request.bound_leaf_id or identity.request.leaf_id,
        enforce_owner=enforce_owner,
    )
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
    identity: _DirectRequestIdentity,
    code_tree: str,
    *,
    code_commit_message: str = "",
    code_before: GitMutationSnapshot | None = None,
) -> tuple[DirectLandingOperationInput, LifecycleOperationCandidate]:
    config = identity.config
    contract = identity.contract
    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    memory_before = _direct_memory_admission_snapshot(contract)
    operation_input = DirectLandingOperationInput(
        configPath=config.config_path.as_posix(),
        contractPath=contract.contract_path.as_posix(),
        leafTaskDocumentRef=identity.request.leaf_task_document_ref,
        effectiveInput=identity.effective_input,
        approvalNote=identity.request.intent_note.strip(),
        gatePolicy=_gate_policy_snapshot(config),
        codeCommit=identity.code_commit,
        codeTree=code_tree,
        candidateTree=identity.candidate_tree,
        codeCommitMessage=code_commit_message,
        codeBefore=code_before,
        memoryRepository=memory_repo.resolve().as_posix(),
        memoryBranch=contract.memory_work_branch,
        memoryRef=memory_before.headRef,
        memoryBefore=memory_before,
        leafId=identity.request.leaf_id,
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


def _direct_gate_owner(
    contract: WorktreeContract, code_commit: str, *, leaf_id: str | None = None
) -> str | None:
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
        contract,
        code_commit=code_commit,
        memory_tree=_memory_content_tree(contract),
        leaf_id=leaf_id,
    )
    if verdict.refusal is not None:
        raise DirectLandingError("direct-landing-knowledge-gate-refused", verdict.refusal)
    return verdict.owner if verdict.applies else None


def _refuse_foreign_history_owner(selected: DirectGateVerdict, leaf_id: str | None) -> None:
    """Refuse when the gate's open history belongs to another leaf than the named one."""
    if leaf_id is not None and selected.owner is not None and selected.owner != leaf_id:
        raise DirectLandingError(
            "direct-landing-history-owner-mismatch",
            f"the selected open history belongs to leaf {selected.owner}, not to the "
            f"named leaf {leaf_id}; nothing lands",
        )


def _select_gated_history(
    contract: WorktreeContract,
    code_commit: str,
    leaf_id: str | None,
    *,
    enforce_owner: bool,
) -> DirectGateVerdict:
    """Select the gate's open history source, refusing a foreign leaf's file when judging anew.

    ``leaf_id`` reaches the gate only while owner enforcement is on: with a
    live generation pending (``enforce_owner=False``) the in-flight conflict
    refuses first as ``direct-landing-input-conflict``, exactly as before the
    leaf-aware gate, so a second request can never slip past the conflict by
    way of a gate refusal.
    """

    selected = direct_gate_source(
        contract,
        code_commit=code_commit,
        memory_tree=_memory_content_tree(contract),
        leaf_id=leaf_id if enforce_owner else None,
    )
    if selected.refusal is not None:
        raise DirectLandingError("direct-landing-knowledge-gate-refused", selected.refusal)
    if enforce_owner:
        _refuse_foreign_history_owner(selected, leaf_id)
    return selected


def _close_gated_leaf(
    contract: WorktreeContract,
    code_commit: str,
    leaf_id: str | None = None,
    *,
    enforce_owner: bool = True,
) -> HistoryClosing | None:
    """Select an exact history source, prepare the final closed tree, then judge that tree once.

    ``None`` for unconverted memory (nothing is written) and for a replay that commits nothing (the
    head already holds this code commit's memory output; see :func:`_replayed_head_tree`). A refusal
    after the closing restores the file's bytes, so a refused landing leaves the memory checkout as
    it found it.

    ``leaf_id`` names the leaf the request lands; when given with ``enforce_owner``, a gate verdict
    for another leaf's open history refuses instead of closing a foreign file. Callers pass
    ``enforce_owner=False`` while a live generation may refuse first as
    ``direct-landing-input-conflict`` (see :func:`_in_flight_conflict_pending`).
    """

    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    try:
        converted = checkout_memory_converted(memory_repo)
    except RuntimeError as exc:
        raise DirectLandingError("direct-landing-knowledge-gate-refused", str(exc)) from exc
    if not converted:
        _direct_gate_owner(contract, code_commit, leaf_id=leaf_id)
        return None
    ignore = memory_repo / ".gitignore"
    ignore_previous = ignore.read_bytes() if ignore.is_file() else None
    ignore_before = HistoryClosing(ignore, ignore_previous, ignore_previous)
    closing = None
    try:
        selected = _select_gated_history(contract, code_commit, leaf_id, enforce_owner=enforce_owner)
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
            leaf_id=leaf_id,
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
        "retired",
        "superseded",
    }:
        # A retired direct landing is replaced, not observed: after its owner
        # retires it for audit, the next direct_landing call starts a fresh
        # generation instead of refusing input-conflict (ruling e55c0093).
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
