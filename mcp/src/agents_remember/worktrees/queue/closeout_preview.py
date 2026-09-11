"""Response-only closeout policy for leaf and atomic-series candidates."""

from __future__ import annotations

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import WorktreeContract


def proposed_closeout_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    code_dirty: bool,
    memory_would_commit: bool,
) -> dict[str, object]:
    """Describe only the Git writes the matching closeout apply path can perform."""
    effective = args.closeout_input
    if effective is None:
        raise RuntimeError("closeout preview requires normalized effective input")
    if contract.kind == "series":
        return {
            "code": {
                "would_commit": False,
                "intent": effective.code.model_dump(mode="json"),
                "ref": f"refs/heads/{contract.code_work_branch}",
            },
            "memory": {
                "would_commit": False,
                "intent": effective.memory.model_dump(mode="json"),
                "ref": (
                    f"refs/heads/{contract.memory_work_branch}"
                    if contract.memory_mode == "external"
                    else ""
                ),
                "metadata_refresh_after_code_commit": False,
                "entity_fingerprint_refresh_after_code_commit": False,
                "route_refresh_after_code_commit": False,
            },
            "ledger": {
                "would_update": False,
                "intent": effective.ledger.model_dump(mode="json"),
                "path": contract.ledger_path.as_posix() if contract.ledger_path else "",
            },
        }
    proposed: dict[str, object] = {
        "code": {
            "would_commit": code_dirty,
            "intent": effective.code.model_dump(mode="json"),
            "worktree": contract.code_worktree.as_posix(),
        },
        "memory": {
            "would_commit": memory_would_commit,
            "intent": effective.memory.model_dump(mode="json"),
            "worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
            "metadata_refresh_after_code_commit": contract.memory_mode == "external",
            "entity_fingerprint_refresh_after_code_commit": contract.memory_mode == "external",
            "route_refresh_after_code_commit": contract.memory_mode == "external",
        },
        "ledger": {
            "would_update": contract.memory_mode == "external",
            "intent": effective.ledger.model_dump(mode="json"),
            "path": contract.ledger_path.as_posix() if contract.ledger_path else "",
        },
    }
    for leg in ("code", "memory", "ledger"):
        if effective.enabled(leg):
            entry = proposed[leg]
            assert isinstance(entry, dict)
            entry["message"] = effective.message_for(leg)
    return proposed


def closeout_summary(contract: WorktreeContract) -> str:
    if contract.kind == "series":
        return (
            "Closeout preview only; no commits were created. Series/master closeout reads the "
            "exact named atomic code and memory refs, verifies the existing ledger mapping, and "
            "records those already-landed commits."
        )
    return (
        "Closeout preview only; no commits were created. The transaction records the accepted "
        "code commit, refreshes and commits external memory when configured, prepends the exact "
        "code-to-memory mapping to the ledger, then updates the contract."
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
        "validate-current-code-candidate-and-source-refs",
        "commit-code-tree",
        "refresh-onboarding-metadata-and-entity-fingerprints",
        "refresh-route-overview-metadata-and-indexes",
        "commit-memory-content",
        "update-ledger",
        "commit-ledger",
        "update-contract",
    ]
