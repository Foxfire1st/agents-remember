"""Prove the exact current memory HEAD as a reusable certified content tree."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import (
    inspect_existing_git_preparation,
    run_git,
)
from agents_remember.kernel.git_preparation import ExistingGitPreparationBinding
from agents_remember.kernel.memory_ledger import MEMORY_CACHE_EXCLUDE
from agents_remember.models.lifecycles.evidence_dependencies import canonical_sha256
from agents_remember.models.lifecycles.preparation import ExistingMemoryPreparationProof
from agents_remember.worktrees.integration.closeout.certification.observation import refuse
from agents_remember.worktrees.modules.git import require_git


def observe_existing_memory_proof(
    root: Path, *, certified_tree: str
) -> ExistingMemoryPreparationProof | None:
    """Reuse clean content without inventing attribution for a newly accepted code commit."""
    status = run_git(
        root,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            ".",
            MEMORY_CACHE_EXCLUDE,
        ],
    )
    if status.returncode != 0:
        refuse("memory-reuse-status-unavailable", "actual logical memory status", status.returncode)
    if status.stdout:
        return None
    common = Path(require_git(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]))
    logical_ref = require_git(root, ["symbolic-ref", "HEAD"])
    head = require_git(root, ["rev-parse", "--verify", "HEAD"])
    head_tree = require_git(root, ["rev-parse", "--verify", f"{head}^{{tree}}"])
    binding = ExistingGitPreparationBinding(
        root=root,
        common_directory=common,
        logical_ref=logical_ref,
        commit=head,
        tree=head_tree,
        allow_memory_cache=True,
        memory_content_tree=certified_tree,
    )
    inspect_existing_git_preparation(binding)
    payload = {
        "schemaVersion": "existing-memory-preparation-proof/v1",
        "repositoryIdentity": common.as_posix(),
        "logicalHeadCommit": head,
        "logicalHeadTree": head_tree,
        "certifiedContentTree": certified_tree,
    }
    inspect_existing_git_preparation(binding)
    return ExistingMemoryPreparationProof.model_validate(
        {**payload, "proofDigest": canonical_sha256(payload)}
    )
