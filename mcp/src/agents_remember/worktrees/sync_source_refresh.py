"""Shared pre-lock source-upstream refresh for worktree synchronization."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_freshness import fetch_remote, upstream_ref
from agents_remember.models.worktree import SyncSource
from agents_remember.worktrees.integration.integration_branch_repository import (
    BranchAuthorityUnavailable,
)
from agents_remember.worktrees.sync_source_strategy import source_strategy
from agents_remember.worktrees.worktree_contract import WorktreeContract


def fetch_source_upstreams(
    contract: WorktreeContract, sync_source: SyncSource | None = None
) -> dict[str, object]:
    """Best-effort bounded fetch of each source branch's upstream remote.

    Offline or remote-less repositories degrade to reported state; sync proceeds
    from local facts pinned after repository integration authority is acquired.
    A repository-default sync fetches the default branches it will pull.
    """

    results: dict[str, object] = {}
    targets: list[tuple[str, Path, str]] = []
    strategy = source_strategy(sync_source)
    try:
        targets.append(("code", contract.code_repo_path, strategy.source_branch(contract, "code")))
        if contract.memory_mode == "external" and contract.memory_repo_path is not None:
            targets.append(
                ("memory", contract.memory_repo_path, strategy.source_branch(contract, "memory"))
            )
    except BranchAuthorityUnavailable as error:
        # The admission answers the unresolved default by name; the fetch only reports it.
        results["default"] = {"state": "unresolved", "error": str(error)}
    for side, repository, branch in targets:
        upstream = upstream_ref(repository, branch) if branch else None
        if upstream is None:
            results[side] = {"state": "no-upstream"}
            continue
        error = fetch_remote(repository, upstream.partition("/")[0])
        results[side] = (
            {"state": "fetched"} if error is None else {"state": "failed", "error": error}
        )
    return results
