"""Record a landing the remote already performed -- the pull-request route.

:mod:`agents_remember.worktrees.modules.integrate` lands code by moving refs locally and then
records what it moved. When the code landed through a pull request AR moved nothing: ``gh pr merge``
did. Until this route existed, the contract's ``integration`` cell stayed ``not-started`` for every
PR landing -- and that is not cosmetic, because the cell is what ``worktree_cleanup`` requires
before it retires a branch and what the series abandon guard reads before it lets a master's
integration branch go. A PR-merged master therefore looked exactly like a master whose work had
never left it.

The caller supplies the landed commits; this module records them. Nothing is inferred after the
fact, because inferring means asking GitHub, and a guard that authorizes deletion must not depend
on the network being reachable.

On converted memory the record route commits no memory but still checks the landed memory commit
(MIK-R09 rule 3): the validator passes against the parent line the task last synced from, and a
leaf's history file is ``closed`` in it. A leaf's landed pair is judged by MIK-R52 rule 1 before
anything is recorded, in the dry run too: the validator as a leaf publication with the contract's
memory base commit as base, then the gate with the worklist recomputed over the two commits'
trees. A memory commit made before the repository was converted
carries no layout marker and is exempt; a converted memory line whose landing names no memory
commit is refused.

**Probe first (L09 review R1, finding 7).** The marker probe reads the memory line and the task's
memory base before anything reads the landed memory commit. On an unconverted line the landed
commit is only probed for the marker, and one Git cannot read is exempt as it was before this
master: the route records exactly as it did. On a converted line an unreadable commit refuses.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.worktrees.disabled_memory import (
    is_disabled_contract,
    opt_out_detail_for_contract,
    opt_out_refusal_result,
)
from agents_remember.worktrees.knowledge_gate import (
    GateProbeError,
    closed_out_memory,
    converted_memory,
    landing_gate_refusal,
    landing_history_refusal,
    leaf_cutover_refusal,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout_external import leaf_pair_refusal
from agents_remember.worktrees.modules.git import (
    branch_exists,
    is_ancestor,
    local_branch_ref,
    require_ancestor,
    require_git,
)
from agents_remember.worktrees.modules.landing_record import (
    LandedIntegration,
    record_landed_integration,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.series_closeout import landed_chain_refusal
from agents_remember.worktrees.services import LandingGateRequest
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

# The route label recorded in the contract's ``integration.strategy`` cell, so the cell says which
# route actually landed the work. ``worktree_integrate`` writes its own strategy there instead.
PR_STRATEGY = "pr"


def _landing_targets(contract: WorktreeContract) -> tuple[str, ...]:
    """The branches the recorded commit may legitimately have landed on.

    Both shapes occur. A master integrates into its recorded source branch (the super branch); a
    task whose branch was merged straight to the protected default bypasses super entirely, which
    is what happened to ``ar/260831_lifecycle-owned-completion-relay``. Checking only the recorded
    source branch would refuse to record exactly the landing that motivated this operation.
    """

    targets = [contract.code_source_branch]
    if branch_exists(contract.code_repo_path, "main") and "main" not in targets:
        targets.append("main")
    return tuple(
        target for target in targets if target and branch_exists(contract.code_repo_path, target)
    )


def _line_converted(contract: WorktreeContract, repository: Path) -> bool | str:
    """Whether the memory line or the task's memory base holds the marker, or why Git cannot say."""

    official = contract.memory_source_branch
    try:
        sides = (local_branch_ref(official) if official else "", contract.memory_base_commit)
        return converted_memory(repository, *sides)
    except RuntimeError as error:  # an unreadable probe, or an invalid official branch cell
        return str(error)


def _commit_converted(repository: Path, memory_commit: str) -> bool:
    """On an unconverted line: whether the landed commit holds the marker (unreadable: exempt)."""

    try:
        return converted_memory(repository, memory_commit)
    except GateProbeError:
        return False  # as before this master: an unconverted line's commit is never read


def _unnamed_memory_commit_refusal(converted: bool) -> str | None:
    """A converted memory line's landing must name its memory commit for the gate to check."""

    if not converted:
        return None
    return (
        "record_landing refused: the memory line is converted, so the landing names its landed "
        "memory commit (landed_memory_content_commit) for the mandatory invariant gate "
        "(MIK-R09) to check"
    )


def _knowledge_gate_refusal(
    contract: WorktreeContract, code_commit: str, memory_commit: str
) -> str | None:
    """MIK-R09 rule 3 over the landed memory commit; ``None`` for unconverted memory.

    A contract that carries the removed mode never reaches the gate's reads:
    the route entry refuses it with the opt-out refusal first (MIK-R50 R4a),
    and this function refuses it itself for a direct caller (MIK-R50 R4b).

    A leaf's landed pair is judged by MIK-R52 rule 1 (:func:`_leaf_pair_recorded_refusal`); every
    other contract keeps the validator and the closed check below.
    """

    repository = contract.memory_repo_path
    if contract.memory_mode != "external":
        return opt_out_detail_for_contract(contract, "worktree_record_landing")
    if repository is None:
        return None
    line = _line_converted(contract, repository)
    if isinstance(line, str):
        return line
    if not line and not (memory_commit and _commit_converted(repository, memory_commit)):
        # Unconverted memory: nothing more is read, exactly as before this master -- unless the
        # repository holds converted memory, when the cutover lock refuses (MIK-R09 rule 6). The
        # lock's verdict is the whole answer here: the landed commit is only probed for the
        # marker, never read (L09 review R1, finding 7).
        return leaf_cutover_refusal(contract, "record_landing")
    if not memory_commit:
        return _unnamed_memory_commit_refusal(line)
    return _judged_landing_refusal(contract, code_commit, memory_commit)


def _judged_landing_refusal(
    contract: WorktreeContract, code_commit: str, memory_commit: str
) -> str | None:
    """The gate judgment after the marker probe and naming checks pass.

    A leaf's landed pair is judged by MIK-R52 rule 1 (:func:`_leaf_pair_recorded_refusal`); a
    master's recorded landing runs the validator, the net staleness check, the landing chain
    proof and rule 5 (MIK-R52 rule 4, :func:`_master_recorded_refusal`).
    """

    if contract.kind == "leaf":
        return _leaf_pair_recorded_refusal(contract, code_commit, memory_commit)
    return _master_recorded_refusal(contract, code_commit, memory_commit)


def _master_recorded_refusal(
    contract: WorktreeContract, code_commit: str, memory_commit: str
) -> str | None:
    """Judge a master's recorded landing: validator, staleness, chain proof, rule 5 (rule 4).

    The validator runs as today; the net staleness check of MIK-R09 rule 4 takes the code base
    from the series contract's ``code_base_commit`` and judges at the contract's recorded code
    commit when the landed commit contains it, otherwise at the landed commit. The landing chain
    proof runs over the leaves that have landed, up to the landed commits rather than the branch
    tips. Rule 5 follows inside the gate. Unconverted lines keep the prior behavior (MIK-R46):
    the validator only.
    """

    repository = contract.memory_repo_path
    assert repository is not None
    request = _landing_request(contract, repository, code_commit, memory_commit)
    if isinstance(request, str):
        return request
    refusal = landing_gate_refusal(request)
    if refusal is not None:
        return refusal
    try:
        converted = converted_memory(repository, memory_commit, contract.memory_base_commit)
    except GateProbeError as error:
        return str(error)
    if not converted:
        return None
    return landed_chain_refusal(
        contract,
        code_tip=code_commit,
        memory_tip=memory_commit,
        require_all=False,
    )


def _judged_master_code(contract: WorktreeContract, code_commit: str) -> str:
    """The code commit a recorded master landing judges staleness at (MIK-R52 rule 4).

    The contract's recorded code commit when the landed commit contains it, otherwise the landed
    commit itself. A recorded commit Git cannot read refuses by name (MIK-R52 F01).
    """

    recorded = contract.integrated_code_commit or contract.code_commit
    if recorded and (
        code_commit == recorded
        or require_ancestor(
            contract.code_repo_path,
            recorded,
            code_commit,
            what=f"the recorded code commit {recorded}",
        )
    ):
        return recorded
    return code_commit


def _leaf_pair_recorded_refusal(
    contract: WorktreeContract, code_commit: str, memory_commit: str
) -> str | None:
    """Judge a leaf's landed pair by MIK-R52 rule 1, then keep the closed check (rule 2).

    The memory commit is the landed one and the base is the contract's memory base commit: a leaf
    contract with no recorded memory base is refused by name, and the fallback to the landed
    commit's parents is gone. The code commit is the contract's recorded closeout code commit when
    the landed code commit is that commit or descends from it, else the landed code commit. The
    check that the leaf's latest history file is closed stays, through
    :func:`landing_history_refusal`.
    """

    repository = contract.memory_repo_path
    assert repository is not None
    base = contract.memory_base_commit
    if not base:
        return (
            "record_landing refused: the leaf contract records no memory base commit, so the "
            "landed pair cannot be judged (MIK-R52 rule 2); close the leaf out through the "
            "tools, which record the base the landed pair is judged against"
        )
    try:
        code = _judged_recorded_code(contract, code_commit)
        refusal = leaf_pair_refusal(
            contract, code_commit=code, memory_commit=memory_commit, bases=(base,),
            persist_worklist=False,
        )
    except (RuntimeError, subprocess.SubprocessError) as error:
        return str(error)
    if refusal is not None:
        return refusal
    return landing_history_refusal(
        LandingGateRequest(
            memory_repository=repository,
            memory_commit=memory_commit,
            memory_bases=(base,),
            code_repository=contract.code_repo_path,
            code_commit=code,
            leaf_owner=contract.leaf_id or contract.task_name,
        )
    )


def _judged_recorded_code(contract: WorktreeContract, code_commit: str) -> str:
    """The code commit rule 1 judges for a recorded leaf landing (MIK-R52 rule 2).

    The contract's recorded closeout code commit when the landed code commit is that commit or
    descends from it -- the landed squash or fast-forward still carries the judged trees --
    otherwise the landed code commit itself. A recorded commit Git cannot read refuses by name:
    a failed ancestry read never silently falls through to the landed commit (MIK-R52 F01).
    """

    recorded = contract.code_commit
    if recorded and (
        code_commit == recorded
        or require_ancestor(
            contract.code_repo_path,
            recorded,
            code_commit,
            what=f"the leaf's recorded closeout code commit {recorded}",
        )
    ):
        return recorded
    return code_commit


def _landing_request(
    contract: WorktreeContract, repository: Path, code_commit: str, memory_commit: str
) -> LandingGateRequest | str:
    """The gate's request over the landed memory commit, or why that commit cannot be read."""

    try:
        parents = require_git(repository, ["rev-list", "--parents", "-n", "1", memory_commit])
    except (RuntimeError, subprocess.SubprocessError) as error:
        return f"record_landing refused: the landed memory commit cannot be read: {error}"
    landed, *made_on = parents.split()
    # The comparison base is the parent line the task last synced from, so every record the task
    # introduced -- in however many of its own commits -- is judged new (MIK-R27, carried from L27).
    bases = (contract.memory_base_commit,) if contract.memory_base_commit else tuple(made_on)
    # Only a closeout the contract records freezes a history file (L09 review R1, finding 1): the
    # commit that closeout made is judged as the closeout judged it, with what it was made on
    # frozen. A commit made by any other hand -- a child of it, a merge made afterwards -- freezes
    # nothing, so every row of the leaf is checked against the landed tree (L37 review R5-1).
    recorded = landed in closed_out_memory(contract)
    try:
        code = _judged_master_code(contract, code_commit)
    except (RuntimeError, subprocess.SubprocessError) as error:
        return f"record_landing refused: the landed code commit cannot be read: {error}"
    return LandingGateRequest(
        memory_repository=repository,
        memory_commit=memory_commit,
        memory_bases=bases,
        code_repository=contract.code_repo_path,
        code_commit=code,
        # MIK-R52 rule 4: the recorded landing judges net staleness from the series contract's
        # code base, at the recorded code commit when the landed commit contains it.
        code_base=contract.code_base_commit or None,
        leaf_owner=(contract.leaf_id or contract.task_name) if contract.kind == "leaf" else None,
        frozen=tuple(made_on) if recorded else (),
        series_contract=contract.contract_path.as_posix(),
    )


def _require_knowledge_gate(
    contract: WorktreeContract, code_commit: str, memory_commit: str
) -> None:
    refusal = _knowledge_gate_refusal(contract, code_commit, memory_commit)
    if refusal is not None:
        raise RuntimeError(refusal)


def _identity_payload(contract: WorktreeContract) -> dict[str, str]:
    return {
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "lifecycleId": contract.lifecycle_id,
        "contractPath": contract.contract_path.as_posix(),
    }


def record_landing_result(args: WorktreeArgs) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("recording a landing requires explicit developer approval")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    if is_disabled_contract(contract):
        # A contract that carries the removed mode never records a landing (MIK-R50 R4a).
        return opt_out_refusal_result(contract, "worktree_record_landing")
    payload = _identity_payload(contract)
    # A checkpointed contract has already recorded a landing, so it is "already recorded" too --
    # and it must be, because the full-record path below writes ``completed`` plus
    # ``cleanup="pending"``, which is exactly what ``worktree_cleanup`` requires. Letting the pull
    # request route take that path would silently revoke the checkpoint's guarantee that a series
    # landed while still open never becomes reclaimable. Completion stays on ``worktree_integrate``,
    # which reaches it only once the series is genuinely terminal.
    if contract.integration_status in {"completed", "checkpointed"}:
        checkpointed = contract.integration_status == "checkpointed"
        return WorktreeCommandResult(
            0,
            {
                **payload,
                "state": "already-recorded",
                "integrationStrategy": contract.integration_strategy,
                "landedCodeCommit": contract.integrated_code_commit,
                "summary": (
                    "This contract already records a checkpointed integration "
                    f"(strategy {contract.integration_strategy!r}). The series is still open and "
                    "cleanup is deliberately not pending, so the pull-request route has nothing "
                    "to record; the series completes through worktree_integrate."
                    if checkpointed
                    else "This contract already records a completed integration "
                    f"(strategy {contract.integration_strategy!r}); nothing to record."
                ),
            },
        )
    commit = args.landed_code_commit.strip()
    if not commit:
        raise RuntimeError(
            "record_landing requires landed_code_commit: the commit the pull request landed on "
            "the protected branch"
        )
    targets = _landing_targets(contract)
    if not targets:
        raise RuntimeError(
            "record_landing refused: neither the recorded source branch "
            f"{contract.code_source_branch!r} nor a local default branch exists, so the landing "
            "cannot be verified"
        )
    if not any(is_ancestor(contract.code_repo_path, commit, target) for target in targets):
        raise RuntimeError(
            f"record_landing refused: {commit} is not reachable from any landing target "
            f"({', '.join(targets)}). Pull the protected branch locally after the merge, then "
            "record the commit it landed."
        )
    claimed = list(targets)
    _require_knowledge_gate(contract, commit, args.landed_memory_content_commit.strip())
    if args.dry_run:
        return WorktreeCommandResult(
            0,
            {
                **payload,
                "state": "would-record",
                "landedCodeCommit": commit,
                "landingTargets": claimed,
                "summary": f"Would record {commit} as landed on {', '.join(targets)}.",
            },
        )
    updated = record_landed_integration(
        contract,
        landed=LandedIntegration(
            strategy=PR_STRATEGY,
            code_commit=commit,
            memory_content_commit=args.landed_memory_content_commit.strip(),
        ),
    )
    return WorktreeCommandResult(
        0,
        {
            **_identity_payload(updated),
            "state": "recorded",
            "integrationStrategy": updated.integration_strategy,
            "landedCodeCommit": commit,
            "landingTargets": claimed,
            "summary": (
                f"Recorded integration of {commit} on {', '.join(targets)} via the pull-request "
                "route. Cleanup and the series abandon guard now read this work as landed."
            ),
        },
    )
