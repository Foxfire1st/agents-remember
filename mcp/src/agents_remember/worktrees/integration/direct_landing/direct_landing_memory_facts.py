"""Read-only external-memory ref evidence for direct landing previews."""

from __future__ import annotations

from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.modules.git import branch_commit


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
