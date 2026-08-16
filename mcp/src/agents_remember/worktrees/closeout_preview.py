"""Response-only closeout policy for leaf and atomic-series candidates."""

from __future__ import annotations

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import WorktreeContract


def proposed_closeout_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    code_dirty: bool,
    memory_would_commit: bool,
    code_quality_gate: dict[str, object],
) -> dict[str, object]:
    """Describe only the writes the matching closeout apply path can perform."""
    if contract.kind == "series":
        return {
            "code": {
                "would_commit": False,
                "message": "",
                "ref": f"refs/heads/{contract.code_work_branch}",
                "strict_code_quality_before_commit": False,
            },
            "memory": {
                "would_commit": False,
                "message": "",
                "ref": (
                    f"refs/heads/{contract.memory_work_branch}"
                    if contract.memory_mode == "external"
                    else ""
                ),
                "metadata_refresh_after_code_commit": False,
                "entity_fingerprint_refresh_after_code_commit": False,
                "route_refresh_after_code_commit": False,
                "memory_quality_check_before_commit": False,
            },
            "ledger": {
                "would_update": False,
                "message": "",
                "path": contract.ledger_path.as_posix() if contract.ledger_path else "",
            },
        }
    ledger_message = (
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: <code_commit> -> <memory_commit>"
    )
    return {
        "code": {
            "would_commit": code_dirty,
            "message": args.code_commit_message,
            "worktree": contract.code_worktree.as_posix(),
            "strict_code_quality_before_commit": bool(code_quality_gate["required"]),
        },
        "memory": {
            "would_commit": memory_would_commit,
            "message": args.memory_commit_message,
            "worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
            "metadata_refresh_after_code_commit": contract.memory_mode == "external",
            "entity_fingerprint_refresh_after_code_commit": contract.memory_mode == "external",
            "route_refresh_after_code_commit": contract.memory_mode == "external",
            "memory_quality_check_before_commit": contract.memory_mode == "external",
        },
        "ledger": {
            "would_update": contract.memory_mode == "external",
            "message": ledger_message,
            "path": contract.ledger_path.as_posix() if contract.ledger_path else "",
        },
    }


def closeout_summary(contract: WorktreeContract) -> str:
    if contract.kind == "series":
        return (
            "Closeout preview only; no commits were created. Series/master closeout reads the "
            "exact named atomic code and memory refs, verifies the existing ledger mapping, and "
            "records those already-landed commits without using an integration branch as a "
            "workbench or rerunning acceptance. The full wrapper runs once at the later "
            "Dagger-container master integration gate."
        )
    return (
        "Closeout preview only; no commits were created. For an external-memory leaf, the "
        "working-tree memory-quality preflight runs before staging or any code-quality "
        "subprocess, so a structurally invalid entity catalog or broken citation aborts before "
        "Pyright or pytest. The staging step and its two refusals belong only to the leaf "
        "change-set-scoped quality gate: when a leaf would commit and this checkout carries the "
        "quality wrapper, closeout refuses a non-task checkout or unresolved conflicts; "
        "otherwise it stages the whole task worktree, runs its configured fast hook once, "
        "restages any hook edits, and runs the leaf's targeted contract over exactly what it "
        "will commit. The code commit bypasses hooks so nothing restarts after the wrapper's "
        "pytest-final phase. After the code commit, external-memory leaf closeout refreshes "
        "onboarding and entity metadata plus route overviews and indexes, reruns memory quality "
        "without the preflight's temporary base provenance, and only then commits memory and "
        "ledger. A refusal commits nothing; a refused code gate may leave the disposable task "
        "worktree staged because its retry resets and restages it."
    )


def closeout_order(contract: WorktreeContract) -> list[str]:
    if contract.kind == "series":
        order = ["read-exact-series-code-ref"]
        if contract.memory_mode == "external":
            order.extend(
                [
                    "read-exact-series-memory-ref",
                    "verify-existing-ledger-maps-exact-series-commits",
                ]
            )
        return [*order, "record-existing-series-commits-in-contract"]
    return [
        "run-working-tree-memory-quality-preflight-before-code-quality",
        "refuse-if-gate-would-run-and-code-checkout-is-not-the-tasks-own-worktree",
        "refuse-if-gate-would-run-and-code-worktree-has-unresolved-merge-conflicts",
        "reset-and-stage-whole-task-worktree-if-gate-would-run",
        "run-configured-pre-commit-hook-once-and-restage-hook-edits",
        "run-strict-code-quality-over-that-staged-content",
        "commit-exactly-certified-code-index-without-rerunning-hooks",
        "refresh-onboarding-metadata-and-entity-fingerprints",
        "refresh-route-overview-metadata-and-indexes",
        "run-post-refresh-memory-quality-check",
        "commit-memory-content",
        "update-ledger",
        "commit-ledger",
        "update-contract",
    ]
