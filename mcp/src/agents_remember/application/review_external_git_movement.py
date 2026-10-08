"""Read raw Git ancestry against the retained review tree record (ICR-R23).

The boundary compares recorded candidate/base commits where they exist. An uncommitted candidate
contains a tree pin without an observed work-branch head; that ancestry channel is not measured.
No current checkout supplies missing historical proof. Recorded code and memory bases remain exact,
and changed ancestry is reported beside the unsupported raw-operation recovery routes.
The boundary reads Git and retained JSON only; it never changes a checkout or records a comparison.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_final_output_receipt import (
    require_comparison_repositories,
    select_review_comparison,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review_external_movement import (
    ExternalGitMovement,
    ExternalGitTransition,
    GitMovementChannel,
    GitMovementEvidence,
    GitTransitionReconciliation,
)
from agents_remember.models.knowledge.review_final_output_receipt import tree_comparison_digest
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    is_ancestor,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "GIT_TRANSITION_SUPPORT",
    "GitTransitionSupport",
    "external_git_movement",
    "external_git_movement_for_contract",
    "external_git_movement_result_block",
    "render_git_transition_support",
    "supported_recovery",
]

_MovementState = Literal["current", "stale", "not-measured", "unavailable"]

# The recovery a boundary that could not read the generation at all names, which is a different
# step from a successor generation: there is nothing to succeed until the record can be read.
_GENERATION_UNREADABLE_RECOVERY = (
    "restore the readable tree comparison this leaf published under "
    "<task_root>/notes/reports/review-comparisons, then read the boundary again; no identity was "
    "compared, so no movement is claimed"
)

# The recovery a boundary names when a declared identity could not be compared at all and no shape
# was observed: the branch, checkout or repository it could not read is what a person acts on. It is
# read by ``_recovery_actions`` exactly when a report observed no transition, so a report can never
# publish an empty recovery clause.
_UNCOMPARED_RECOVERY = (
    "restore the checkout, branch or repository the boundary could not read, then read the boundary "
    "again; where the recorded identity itself no longer exists, record a successor tree comparison from "
    "the history the branch does hold. Nothing about a channel that was not compared is claimed here"
)


def _recovery_actions(transitions: Sequence[ExternalGitTransition]) -> str:
    """The recovery of the shapes a report observed, taken from the matrix and never restated.

    A row whose ``recovery_action`` changes changes every report that observed that shape, so this
    derivation keeps the report and the documented matrix the same answer. A report that observed no
    shape carries the uncompared recovery, because "nothing was compared" is still an action.
    """

    if not transitions:
        return _UNCOMPARED_RECOVERY
    return "; ".join(
        f"{transition}: {supported_recovery(transition).recovery_action}"
        for transition in transitions
    )


# The declared identities one boundary compares, and the label each carries in this module's own
# measurement. The names are the thing that moved rather than the command that moved it.
_CODE_CHANNEL: GitMovementChannel = "code-work-branch"
_SOURCE_CHANNEL: GitMovementChannel = "declared-source-branch"
_MEMORY_CHANNEL: GitMovementChannel = "memory-work-branch"


@dataclass(frozen=True)
class GitTransitionSupport:
    """One row of the support matrix: a transition, what it looks like, and what is done about it.

    ``measured_signature`` is what an ancestry check can actually observe, in Git's own terms, and it
    is the field that keeps this matrix honest: a row whose signature is not observable by this module
    says so instead of being folded into a neighbour. ``reconciliation`` is ``supported`` only for the
    transitions whose recovery this system performs, and ``recovery_action`` names the step either
    way, so ``unsupported`` is never a dead end -- it is the instruction a reader needs.
    """

    transition: ExternalGitTransition
    measured_signature: str
    state: _MovementState
    reconciliation: GitTransitionReconciliation
    recovery_action: str


# The support matrix, and the module's central deliverable: one row per shape, rendered verbatim into
# ``docs/reference/worktrees-c09.md`` by :func:`render_git_transition_support` and asserted against that
# file by the cases, so the documented matrix and the one a reader is told about cannot be two tables.
GIT_TRANSITION_SUPPORT: tuple[GitTransitionSupport, ...] = (
    GitTransitionSupport(
        transition="unchanged",
        measured_signature=(
            "every declared identity is still exactly the identity the comparison recorded: the "
            "candidate commit, the code-base commit and the memory work branch's base are all at "
            "their recorded values, so nothing was observed to move"
        ),
        state="current",
        reconciliation="supported",
        recovery_action=(
            "none required for the declared identities; this boundary compared all of them and found "
            "each still exactly where the reviewed tree comparison recorded it, so no shape was observed "
            "to reconcile"
        ),
    ),
    GitTransitionSupport(
        transition="ordinary-append",
        measured_signature=(
            "the recorded candidate commit is still an ancestor of the branch tip and the tip has "
            "moved past it: the branch advanced without replacing anything the comparison recorded"
        ),
        state="current",
        reconciliation="supported",
        recovery_action=(
            "none required for the recorded identities; the branch moved forward from the recorded candidate "
            "commit without replacing it, which is the ordinary shape of work continuing under an "
            "already-recorded tree comparison"
        ),
    ),
    GitTransitionSupport(
        transition="rebase",
        measured_signature=(
            "the recorded candidate commit is a readable commit object that is not an ancestor of "
            "the branch tip, so the branch was rewritten"
        ),
        state="stale",
        reconciliation="unsupported",
        recovery_action=(
            "open a new live review from the rebased tip to record the exact successor tree comparison; "
            "the previous comparison remains inspectable, and this system does not replay or reverse a rebase"
        ),
    ),
    GitTransitionSupport(
        transition="cherry-pick",
        measured_signature=(
            "the branch tip differs from the recorded candidate commit while the recorded candidate commit is still an "
            "ancestor, or the code and memory trees differ while both commits still resolve"
        ),
        state="current",
        reconciliation="unsupported",
        recovery_action=(
            "record a successor tree comparison from the advanced tip -- the pick is never identified "
            "from an ancestry check alone, and no record that already exists measures it: the "
            "managed-sync rebinding exists only once a sync has carried the official line and "
            "resolved a pair, the reopen channel reports the recorded comparison's availability "
            "rather than the pick, and this boundary's own state stays 'current'. A sync that "
            "carries nothing resolves no pair and records no rebinding, so it is not a measurement "
            "of the pick either"
        ),
    ),
    GitTransitionSupport(
        transition="revert",
        measured_signature=(
            "the branch advances by exactly the commits that undo earlier ones: the recorded candidate commit "
            "stays an ancestor and no ancestry check can tell the undo from any other new commit"
        ),
        state="current",
        reconciliation="unsupported",
        recovery_action=(
            "record a successor tree comparison from the branch as it now stands; a revert is never "
            "inferred from an ancestry check, and the code tree and memory candidate tree this boundary "
            "does not compare are the existing owners' measurements to take"
        ),
    ),
    GitTransitionSupport(
        transition="branch-switch",
        measured_signature=(
            "the code worktree is not on the branch the contract declared -- a detached HEAD or "
            "another branch -- so the work-branch comparison cannot be taken at all"
        ),
        state="not-measured",
        reconciliation="unsupported",
        recovery_action=(
            "return the worktree to its declared work branch, then read the boundary again; this "
            "system never checks a branch out on a reader's behalf and never mutates a checkout"
        ),
    ),
)

# The transition names in matrix order, for the callers that need to name a row without repeating the
# table: the mapping is derived, so a row added above is reachable by name below with no second list.
_SUPPORT_BY_TRANSITION: Mapping[str, GitTransitionSupport] = {
    row.transition: row for row in GIT_TRANSITION_SUPPORT
}


def supported_recovery(transition: str) -> GitTransitionSupport:
    """The matrix row for one transition, or a named error when the matrix has none.

    The lookup exists so a caller names a transition rather than restating its verdict, and it refuses
    an unknown name instead of answering with a default: a report that silently treated an
    unclassified transition as reconciled would be exactly the over-claim this matrix exists to
    prevent.
    """

    support = _SUPPORT_BY_TRANSITION.get(transition)
    if support is None:
        raise KeyError(
            f"no support-matrix row records the transition {transition!r}; the matrix in "
            "agents_remember.application.review_external_git_movement is the authority on which "
            "transitions are documented"
        )
    return support


def unsupported_transitions() -> tuple[str, ...]:
    """Every transition this system does not reconcile, in matrix order.

    The value a report repeats to its reader. It is derived from the matrix rather than written out,
    so a row that changes verdict changes every report that publishes it.
    """

    return tuple(
        f"{row.transition}:{row.reconciliation}"
        for row in GIT_TRANSITION_SUPPORT
        if row.reconciliation == "unsupported"
    )


def render_git_transition_support() -> str:
    """The matrix as the Markdown table ``docs/reference/worktrees-c09.md`` publishes.

    Rendered from :data:`GIT_TRANSITION_SUPPORT` rather than transcribed, so the documented matrix
    cannot drift from the one this module reports against; the framing paragraph is the document's.
    """

    header = (
        "| Transition | Measured signature | Boundary state | Reconciliation | Recovery |\n"
        "| --- | --- | --- | --- | --- |"
    )
    rows = [
        f"| `{row.transition}` | {_cell(row.measured_signature)} | `{row.state}` | **{row.reconciliation}** | {_cell(row.recovery_action)} |"
        for row in GIT_TRANSITION_SUPPORT
    ]
    return "\n".join([header, *rows])


def _cell(text: str) -> str:
    """One table cell: a literal pipe or backslash is escaped so the rendered row keeps its columns."""

    return text.replace("\\", "\\\\").replace("|", "\\|")


@dataclass(frozen=True)
class _Finding:
    """One channel's ancestry check: what was asked, and what the repository answered.

    ``state`` is the difference this module refuses to flatten: a channel whose repository or declared
    branch could not be read is ``unavailable``, never ``current`` (which would claim a comparison
    nobody took). ``branch_left`` is the *sub-fact* that earns the ``branch-switch`` shape, carried
    separately on purpose: a worktree on its declared branch whose recorded object is simply gone is
    also ``unavailable``, and deriving the switch from "could not be compared" would name a checkout
    nobody performed.
    """

    channel: GitMovementChannel
    state: GitMovementEvidence
    declared: str
    recorded: str
    observed: str | None
    detail: str
    branch_left: bool = False


@dataclass(frozen=True)
class _BranchTip:
    """A branch tip read from a repository, or the reason there is none to read."""

    commit: str | None
    detail: str = ""


def external_git_movement(
    resolved: ReviewCandidateResolution,
    contract: WorktreeContract | None = None,
) -> ExternalGitMovement | None:
    """The review entry point: what the repository shows about the generation this leaf published.

    ``None`` is the answer whenever there is no boundary to measure -- a resolution naming no enclosure,
    a record reopened from a closed leaf, a contract that is not a leaf or declares no work branch, or a
    leaf that published nothing -- and the live review read must never fail because a *measurement* was
    unavailable. A generation that exists and cannot be used (unreadable bytes, or a tie of two
    generations claiming one index) is **not** one of those: it is ``unavailable`` with the selection's
    own detail, because "never reviewed" and "the review record is corrupt" are different facts.

    ``contract`` is the enclosure contract the caller already resolved, when it has one; passing it
    avoids resolving the same record twice. A caller that passes nothing gets the resolution's own.
    """

    enclosure = contract if contract is not None else resolved.contract
    if (
        enclosure is None
        or resolved.closed_leaf is not None
        or (resolved.trees is not None and not resolved.trees.live)
    ):
        return None
    return external_git_movement_for_contract(enclosure)


@dataclass(frozen=True)
class _BoundaryAbsence:
    """Why no boundary could be measured here, in the words of the arm that established it.

    The review read (which reports the absence as ``None``) and the closeout/integration block (which
    publishes it) read the **same** branch: one sentence over every cause would say that *something*
    applies without saying which.
    """

    detail: str


def _measure_contract(contract: WorktreeContract) -> ExternalGitMovement | _BoundaryAbsence:
    """The measurement, or the exact cause that says why there is none.

    What the contract *declares* is decided here -- a contract that is not a leaf enclosure, and a leaf
    that names no work branch, are two different causes and each gets its own sentence -- and what the
    generation store *holds* for it is decided next door.
    """

    if contract.kind != "leaf":
        return _BoundaryAbsence(
            f"this contract records kind {contract.kind!r} rather than a leaf enclosure, so no leaf's "
            "published tree comparison resolves under it"
        )
    if not contract.code_work_branch:
        return _BoundaryAbsence(
            "this contract declares no work branch, so there is no declared work-branch head to "
            "compare against the repository"
        )
    return _measure_generation(contract)


def _measure_generation(contract: WorktreeContract) -> ExternalGitMovement | _BoundaryAbsence:
    """Read the leaf's published generation and compare it, or name the store's own absence.

    Both absences travel in the store's own words: ``unavailable`` with the selection's detail when a
    generation is held and unusable, and the ``no-generation`` detail when nothing was published.
    """

    try:
        selection = select_review_comparison(contract.task_root, contract.leaf_id)
        if selection.state in {"unreadable", "ambiguous", "legacy-limit"}:
            return _unavailable(contract, selection.detail)
        if selection.state != "selected" or selection.comparison is None:
            return _BoundaryAbsence(selection.detail)
        manifest = selection.comparison
        require_comparison_repositories(contract, manifest)
    except (KnowledgeStorageError, OSError, RuntimeError, ValueError) as error:
        return _unavailable(contract, str(error) or error.__class__.__name__)
    try:
        findings = _findings(contract, manifest)
    except (KnowledgeStorageError, OSError, RuntimeError, ValueError) as error:
        return _unavailable(contract, str(error) or error.__class__.__name__)
    return _report(contract, manifest, findings)


def external_git_movement_for_contract(contract: WorktreeContract) -> ExternalGitMovement | None:
    """The contract-only half of the boundary measurement, for the closeout and integration owners.

    That measurement needs nothing from a review resolution -- the generation store and the repositories
    the contract names are the whole input -- so there is one implementation of the comparison and the
    closeout owner cannot measure a different thing than the review read does. ``None`` is "no boundary
    was measured"; which cause applies is the answer :func:`_measure_contract` already holds, and a
    caller that must *publish* the absence uses :func:`external_git_movement_result_block`.
    """

    measured = _measure_contract(contract)
    return measured if isinstance(measured, ExternalGitMovement) else None


def external_git_movement_result_block(
    contract: WorktreeContract, payload: dict[str, Any]
) -> dict[str, Any]:
    """Carry the measured boundary on a closeout or integration result (ICR-R23@v1).

    This is the entry point those tools call, and it **never refuses anything**: an absent boundary, an
    unreadable record and an unreadable worktree are each reported as a ``state`` rather than raised,
    so a completed (or previewed) closeout is returned unchanged and no review obligation becomes a
    gate on the Git transaction the statement describes. The closeout door's own source-lineage checks
    remain that transaction's only checks; this rides beside them, and a failed operation carries
    nothing because there is no finalised work for a statement to be about.
    """

    if not payload.get("ok"):
        return payload
    try:
        measured = _measure_contract(contract)
    except (KnowledgeStorageError, OSError, RuntimeError, ValueError) as error:
        payload["external_git_movement"] = _absence_block(
            "unavailable",
            f"the boundary could not be measured for this result: {type(error).__name__}: {error}",
        )
        return payload
    if isinstance(measured, _BoundaryAbsence):
        # The cause is the one the measurement established, not a disjunction over every cause:
        # "never reviewed", "not a leaf enclosure" and "no declared work branch" are three facts a
        # reader acts on differently.
        payload["external_git_movement"] = _absence_block(
            "not-measured",
            f"no boundary was measured for this result: {measured.detail}",
        )
        return payload
    payload["external_git_movement"] = measured.model_dump(mode="json")
    return payload


def _absence_block(state: _MovementState, detail: str) -> dict[str, Any]:
    """The typed absence a result carries when no measurement could be made.

    It repeats ``unsupported_transitions()`` exactly as a measured value does: the routes this system
    does not reconcile are a fact about the system, not about one leaf, so a reader of a closeout
    result sees them whether or not the boundary measured.
    """

    return {
        "state": state,
        "detail": detail,
        "unsupported": list(unsupported_transitions()),
    }


def _unavailable(contract: WorktreeContract, detail: str) -> ExternalGitMovement:
    """The state a generation that could not be read earns: no generation identity is claimed."""

    reason = (
        f"the retained comparison cannot provide an exact tree-bound boundary measurement, so no declared identity was "
        f"compared -- no boundary measurement exists to report ({detail})"
    )
    return ExternalGitMovement(
        binding_state="unavailable",
        reason=reason,
        statement=f"{reason}; {_GENERATION_UNREADABLE_RECOVERY}",
        movement_version="ar-review-external-movement/v2",
        comparison=None,
        reviewed_binding_digest=None,
        declared_work_branch=contract.code_work_branch,
        successor_action=_GENERATION_UNREADABLE_RECOVERY,
        unsupported=unsupported_transitions(),
        generation_readable=False,
    )


def _report(
    contract: WorktreeContract,
    manifest: ReviewTreeComparisonRecord,
    findings: Sequence[_Finding],
) -> ExternalGitMovement:
    """Compose the report from the findings, taking its state from what was measured."""

    state = _state(findings)
    transitions = _shapes(findings, state=state)
    reason = _reason(findings) if state == "not-measured" else None
    recovery = _recovery_actions(transitions)
    return ExternalGitMovement(
        binding_state=state,
        transitions=transitions,
        transition_evidence=tuple((finding.channel, finding.state) for finding in findings),
        moved_identities=tuple(
            _moved_identity(finding) for finding in findings if finding.state == "replaced"
        ),
        reason=reason,
        statement=_statement(state, findings, reason, transitions, recovery),
        movement_version="ar-review-external-movement/v2",
        comparison=manifest,
        reviewed_binding_digest=tree_comparison_digest(manifest),
        declared_work_branch=contract.code_work_branch,
        observed_code_work_branch_head=_observed(findings, _CODE_CHANNEL),
        observed_declared_source_branch_head=_observed(findings, _SOURCE_CHANNEL),
        observed_memory_work_branch_head=_observed(findings, _MEMORY_CHANNEL),
        successor_action=recovery,
        unsupported=unsupported_transitions(),
    )


def _shapes(
    findings: Sequence[_Finding], *, state: _MovementState
) -> tuple[ExternalGitTransition, ...]:
    """The shapes a report observed, in channel order and deduplicated.

    ``unchanged`` -- and nothing else -- is published exactly when every declared identity was compared
    and none moved: the *measured absence* of a shape, so the commonest state of all is never labelled
    with an event that did not happen. An empty tuple is the other honest answer, for a boundary that
    observed no shape because something could not be compared.
    """

    observed: list[ExternalGitTransition] = []
    for finding in findings:
        shape = _transition(finding)
        if shape is not None and shape not in observed:
            observed.append(shape)
    if state == "current" and not observed:
        return ("unchanged",)
    return tuple(observed)


def _state(findings: Sequence[_Finding]) -> _MovementState:
    """The report's one state, from what was measured rather than from a count of findings.

    A replaced identity is the movement. Otherwise ``current`` requires that **every** declared channel
    was actually compared: claiming "these identities still stand" while one channel reports
    ``unavailable`` would claim coverage it did not have. A boundary that replaced nothing and could
    not compare something reports ``not-measured`` with that reason, and the channels it did compare
    stay named in ``transition_evidence``.
    """

    if any(finding.state == "replaced" for finding in findings):
        return "stale"
    if all(finding.state != "unavailable" for finding in findings):
        return "current"
    return "not-measured"


def _findings(
    contract: WorktreeContract, manifest: ReviewTreeComparisonRecord
) -> tuple[_Finding, ...]:
    """Ask every channel the contract declares, in a fixed order.

    The order is the report's, not a priority: a reader is told about the work branch, then the source
    branch it was forked from, then the memory line. A contract that declares no memory side asks two
    questions rather than reporting a third answer it never measured.
    """

    candidate = manifest.code_candidate
    if candidate.commit is None:
        work = _unreadable(
            _CODE_CHANNEL,
            declared=contract.code_work_branch,
            recorded=candidate.tree,
            detail="the retained uncommitted candidate records a tree pin, not an observed work-branch head; work-head ancestry cannot be measured",
            branch_left=_text(lambda: current_branch(contract.code_worktree))
            != contract.code_work_branch,
        )
    else:
        work = _work_branch_finding(contract, recorded=candidate.commit)
    base = manifest.code_base
    source = (
        _source_finding(contract, recorded=base.commit)
        if base.commit is not None
        else _unreadable(
            _SOURCE_CHANNEL,
            declared=contract.code_source_branch,
            recorded=base.tree,
            detail="the retained code base carries no commit for an ancestry check",
        )
    )
    findings = [work, source]
    memory_base = manifest.memory_base
    memory = (
        _memory_finding(contract, recorded=memory_base.commit)
        if memory_base.commit is not None
        else _unreadable(
            _MEMORY_CHANNEL,
            declared=contract.memory_work_branch,
            recorded=memory_base.tree,
            detail="the retained memory base carries no commit for an ancestry check",
        )
    )
    if memory is not None:
        findings.append(memory)
    return tuple(findings)


def _work_branch_finding(contract: WorktreeContract, *, recorded: str) -> _Finding:
    """The code work branch: is the worktree on it, and does its tip still descend from the record?"""

    branch = contract.code_work_branch
    checked_out = _text(lambda: current_branch(contract.code_worktree))
    if checked_out != branch:
        shown = checked_out or "no branch (a detached HEAD)"
        return _unreadable(
            _CODE_CHANNEL,
            declared=branch,
            recorded=recorded,
            detail=(
                f"the code worktree is on {shown}, not on the declared work branch {branch}, so the "
                "recorded candidate commit was not compared against it"
            ),
            branch_left=True,
        )
    return _ancestry(
        _CODE_CHANNEL,
        repository=contract.code_worktree,
        declared=branch,
        recorded=recorded,
        tip=_tip(contract.code_worktree, branch),
    )


def _source_finding(contract: WorktreeContract, *, recorded: str) -> _Finding:
    """The declared source branch: is the code-base commit the capture used still in its history?"""

    branch = contract.code_source_branch
    return _ancestry(
        _SOURCE_CHANNEL,
        repository=contract.code_repo_path,
        declared=branch,
        recorded=recorded,
        tip=_tip(contract.code_repo_path, branch),
    )


def _memory_finding(contract: WorktreeContract, *, recorded: str) -> _Finding | None:
    """The memory work branch, or nothing when this contract declares no memory side."""

    branch = contract.memory_work_branch
    repository = contract.memory_repo_path
    if not branch or repository is None or not recorded:
        return None
    return _ancestry(
        _MEMORY_CHANNEL,
        repository=repository,
        declared=branch,
        recorded=recorded,
        tip=_tip(repository, branch),
    )


def _ancestry(
    channel: GitMovementChannel,
    *,
    repository: Path,
    declared: str,
    recorded: str,
    tip: _BranchTip,
) -> _Finding:
    """One channel's answer, decided only from reads that succeeded.

    ``advanced`` is measured rather than assumed: the recorded identity is still an ancestor, so the
    branch moved forward without replacing it. ``unavailable`` is the answer whenever either side
    could not be resolved, and it is never folded into ``current``.
    """

    if tip.commit is None:
        return _unreadable(
            channel,
            declared=declared,
            recorded=recorded,
            detail=f"{declared} could not be resolved in {repository}: {tip.detail}",
        )
    if not _object_readable(repository, recorded):
        return _unreadable(
            channel,
            declared=declared,
            recorded=recorded,
            detail=(
                f"the recorded identity {recorded} is not a readable commit object in {repository}, "
                "so whether it still leads to the branch tip cannot be measured"
            ),
        )
    if recorded == tip.commit:
        state: GitMovementEvidence = "current"
        detail = f"{declared} is still at the recorded identity {recorded}"
    elif _related(repository, recorded, tip.commit):
        state = "advanced"
        detail = (
            f"{declared} advanced from the recorded identity {recorded} to {tip.commit} without "
            "replacing it"
        )
    else:
        state = "replaced"
        detail = (
            f"{declared} is at {tip.commit} and the recorded identity {recorded} is no longer in its "
            "history, so the branch was rewritten"
        )
    return _Finding(
        channel=channel,
        state=state,
        declared=declared,
        recorded=recorded,
        observed=tip.commit,
        detail=detail,
    )


def _unreadable(
    channel: GitMovementChannel,
    *,
    declared: str,
    recorded: str,
    detail: str,
    branch_left: bool = False,
) -> _Finding:
    """The finding for a channel that could not be compared, with no observed value at all."""

    return _Finding(
        channel=channel,
        state="unavailable",
        declared=declared,
        recorded=recorded,
        observed=None,
        detail=detail,
        branch_left=branch_left,
    )


def _tip(repository: Path, branch: str) -> _BranchTip:
    """One branch tip, or the reason it does not resolve -- a refused read is not an absence."""

    try:
        return _BranchTip(commit=branch_commit(repository, branch))
    except (OSError, RuntimeError) as error:
        return _BranchTip(commit=None, detail=str(error) or error.__class__.__name__)


def _object_readable(repository: Path, commit: str) -> bool:
    """Whether one exact commit object exists here, asked so a missing object is not an error."""

    try:
        return is_ancestor(repository, commit, commit)
    except (OSError, RuntimeError):
        return False


def _related(repository: Path, recorded: str, tip: str) -> bool:
    """Whether the recorded identity still leads to the tip; any read failure is not a relationship."""

    try:
        return is_ancestor(repository, recorded, tip)
    except (OSError, RuntimeError):
        return False


def _text(reader: Callable[[], str]) -> str:
    """One optionally-failing Git read, as text; an unreadable checkout answers the empty string."""

    try:
        return reader()
    except (OSError, RuntimeError):
        return ""


def _observed(findings: Sequence[_Finding], channel: GitMovementChannel) -> str | None:
    """The branch tip one channel actually read, or ``None`` when it read none."""

    for finding in findings:
        if finding.channel == channel:
            return finding.observed
    return None


def _transition(finding: _Finding) -> ExternalGitTransition | None:
    """Which shape one finding is, in the vocabulary the matrix and the report share.

    The switch is read from the *branch* sub-fact, never from "this channel could not be compared", so
    a recorded object that is simply gone is never reported as a checkout nobody performed. A replaced
    history is the rebase; an advance is the ordinary append; a channel still standing exactly at its
    record is **no shape at all**, and ``_shapes`` turns "nothing on any channel" into ``unchanged``.
    """

    if finding.branch_left:
        return "branch-switch"
    if finding.state == "replaced":
        return "rebase"
    if finding.state == "advanced":
        return "ordinary-append"
    return None


def _moved_identity(finding: _Finding) -> str:
    """The replaced identity, spelled ``channel:identity`` for the value that is no longer in place."""

    return f"{finding.channel}:{finding.recorded}"


def _reason(findings: Sequence[_Finding]) -> str:
    """Why nothing could be compared, in the boundary's own words.

    Every unreadable channel is named with its own reason, because the action a reader takes differs by
    channel: restoring a checkout and restoring a repository are different acts.
    """

    return "; ".join(
        f"{finding.channel} was not compared: {finding.detail}"
        for finding in findings
        if finding.state == "unavailable"
    )


def _statement(
    state: _MovementState,
    findings: Sequence[_Finding],
    reason: str | None,
    transitions: Sequence[ExternalGitTransition],
    recovery: str,
) -> str:
    """The one sentence this report publishes, taking its clauses from the measurements.

    No state borrows another's sentence: agreement is reachable only from ``current`` and says which
    shape was observed -- ``unchanged`` has its own clause, never an advance -- the movement sentence
    names exactly the identities replaced, and the absence sentence carries the reason and recovery
    instead of an agreement clause.
    """

    if state == "not-measured":
        return (
            "this boundary did not compare every declared identity of the reviewed tree comparison: "
            f"{reason}. {_compared_clause(findings)}The reviewed tree comparison is kept exactly where it "
            f"is and stays inspectable; {recovery}"
        )
    if state == "current":
        return _agreement_statement(findings, transitions, recovery)
    return _movement_statement(findings, recovery)


def _compared_clause(findings: Sequence[_Finding]) -> str:
    """What an absence did compare, stated only when something was compared.

    A boundary that could not compare one channel has usually compared the others, and a sentence
    saying it compared *nothing* would be false in exactly the state this vocabulary reports. Both
    halves are therefore spelled from the findings themselves.
    """

    measured = "; ".join(finding.detail for finding in findings if finding.state != "unavailable")
    if not measured:
        return "No declared identity was compared, so nothing is claimed about them. "
    return f"the identities it did compare are reported beside this value: {measured}. "


def _agreement_statement(
    findings: Sequence[_Finding],
    transitions: Sequence[ExternalGitTransition],
    recovery: str,
) -> str:
    """The agreement sentence, which claims only the shape the boundary actually observed.

    A state in which nothing moved says so: calling a branch "advanced" while it stands at its recorded
    head would turn the store's own claim into an event nobody observed. The channels named are exactly
    the ones compared -- ``current`` requires all of them -- so no clause about an uncompared channel
    belongs here; an absence is the ``not-measured`` state's reason.
    """

    measured = "; ".join(finding.detail for finding in findings if finding.state != "unavailable")
    if tuple(transitions) == ("unchanged",):
        return (
            "no raw Git operation moved a declared identity of the reviewed tree comparison, and this "
            f"boundary observed no transition: every declared identity still stands exactly where "
            f"the comparison recorded it -- {measured}. {recovery}"
        )
    return (
        "no raw Git operation replaced a declared identity of the reviewed tree comparison: a declared "
        f"branch advanced from its recorded identity without replacing it -- {measured}. {recovery}"
    )


def _movement_statement(findings: Sequence[_Finding], recovery: str) -> str:
    """The movement sentence: the replaced channels, then the channels that still stood."""

    replaced = "; ".join(
        _replacement_clause(finding) for finding in findings if finding.state == "replaced"
    )
    untouched = "; ".join(finding.detail for finding in findings if finding.state == "current")
    suffix = f"{untouched}; " if untouched else ""
    return (
        "a raw Git operation replaced a declared identity of the reviewed tree comparison without a "
        f"managed sync: {replaced}. {suffix}{recovery}"
    )


def _replacement_clause(finding: _Finding) -> str:
    """One replaced channel, stated from the values the check read rather than from a diagnosis."""

    if finding.channel == _CODE_CHANNEL:
        tip = finding.observed or "no readable tip"
        return (
            f"the code work branch {finding.declared} is at {tip} and the head the review captured "
            f"({finding.recorded}) is no longer in its history"
        )
    return (
        f"{finding.declared} is at {finding.observed} and the recorded base {finding.recorded} is no "
        "longer in its history"
    )
