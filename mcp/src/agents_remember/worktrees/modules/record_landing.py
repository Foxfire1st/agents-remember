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
"""

from __future__ import annotations

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_exists, is_ancestor
from agents_remember.worktrees.modules.landing_record import (
    LandedIntegration,
    record_landed_integration,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
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
