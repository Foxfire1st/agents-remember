"""Execute one selected private code or memory-content command suffix."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_preparation import (
    PrivateGitPreparationBinding,
)
from agents_remember.models.lifecycles.preparation import CloseoutPreparationIntent
from agents_remember.worktrees.integration.closeout.certification.observation import refuse


def private_git_binding(intent: CloseoutPreparationIntent) -> PrivateGitPreparationBinding:
    """Convert one exact write-enabled intent through the single preparation binding owner."""
    if not intent.writeEnabled or intent.privateRoot is None or intent.normalizedMessage is None:
        refuse("private-preparation-write-disabled", "write-enabled private intent", intent.leg)
    return PrivateGitPreparationBinding(
        logical_root=Path(intent.logicalRoot),
        logical_ref=intent.logicalRef,
        expected_logical_commit=intent.expectedOldCommit,
        common_directory=Path(intent.repositoryIdentity),
        private_root=Path(intent.privateRoot),
        parent_commit=intent.parentCommit,
        admitted_tree=intent.admittedTree,
        message=intent.normalizedMessage,
        hook_policy=intent.hookPolicy,
        operation_key=intent.operationKey,
        generation=intent.generation,
        intent_digest=intent.intentDigest,
    )
