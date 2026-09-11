"""Shared pre-lock source-upstream refresh for worktree synchronization."""

from __future__ import annotations

from agents_remember.kernel.git_freshness import fetch_remote, upstream_ref
from agents_remember.worktrees.worktree_contract import WorktreeContract


def fetch_source_upstreams(contract: WorktreeContract) -> dict[str, object]:
    """Best-effort bounded fetch of each source branch's upstream remote.

    Offline or remote-less repositories degrade to reported state; sync proceeds
    from local facts pinned after repository integration authority is acquired.
    """

    results: dict[str, object] = {}
    targets = [("code", contract.code_repo_path, contract.code_source_branch)]
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        targets.append(("memory", contract.memory_repo_path, contract.memory_source_branch))
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
