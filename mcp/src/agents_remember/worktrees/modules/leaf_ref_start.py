"""Start-side adapter for canonical leaf-ref resolution."""

from __future__ import annotations

from agents_remember.worktrees.leaf_refs import (
    LEAF_REF_EXPECTED_FORM,
    LeafRefResolutionError,
    resolve_leaf_ref,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult


def resolve_start_leaf_doc_id(context, args: WorktreeArgs) -> str:
    assert args.worktree_name is not None
    return resolve_leaf_ref(
        context.coordination_root,
        context.code_repository_name,
        args.leaf_id or args.worktree_name,
        task_name=args.task_name,
        parent_task=args.parent_task,
    ).doc_id


def invalid_leaf_ref_result(error: LeafRefResolutionError) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": error.status,
            "summary": str(error),
            "expected": LEAF_REF_EXPECTED_FORM,
            "candidates": list(error.candidates),
        },
    )
