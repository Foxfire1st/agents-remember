"""Start-side adapter for canonical leaf-ref resolution."""

from __future__ import annotations

from pathlib import Path

from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.leaf_refs import (
    LEAF_REF_EXPECTED_FORM,
    LeafRefResolutionError,
    resolve_leaf_ref,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.task_resolver import task_folder_name
from agents_remember.worktrees.worktree_contract import ContractError


def resolve_start_leaf_doc_id(context, args: WorktreeArgs) -> str:
    assert args.worktree_name is not None
    return resolve_leaf_ref(
        context.coordination_root,
        context.code_repository_name,
        args.leaf_id or args.worktree_name,
        task_name=args.task_name,
        parent_task=args.parent_task,
    ).doc_id


def start_input_edge_refusal(
    context, args: WorktreeArgs, field: str, expected: str | None
) -> WorktreeCommandResult:
    """Refuse an incorrect caller edge without publishing its proposed parent."""
    next_args = {
        "repo_id": context.code_repository_name,
        "task_name": args.task_name,
        "worktree_name": args.worktree_name,
        "leaf_id": args.leaf_id,
        "workflow_kind": args.workflow_kind,
        "dry_run": args.dry_run,
        "skip_provider_setup": args.skip_provider_setup,
        "memory_mode": args.memory_mode,
        "source_branch": args.source_branch,
        "parent_task": args.parent_task,
    }
    next_args[field] = expected
    code = (
        "start-source-branch-mismatch" if field == "source_branch" else "start-parent-task-mismatch"
    )
    return WorktreeCommandResult(
        2,
        {
            "state": code,
            "code": code,
            "summary": f"The requested {field} does not match the task-derived edge.",
            "expected": {field: expected},
            "observed": {field: getattr(args, field)},
            "nextTool": "worktree_start",
            "nextArgs": {key: value for key, value in next_args.items() if value is not None},
        },
    )


def start_parent_input_refusal(context, args: WorktreeArgs, task_root: Path):
    """The supplied parent is an assertion, never authority to author an edge."""
    if not args.parent_task or not (task_root / "task.json").is_file():
        return None
    # The public selector may name the leaf's immediate master itself, or
    # the commanding parent used to disambiguate a nested master namespace.
    if task_folder_name(args.parent_task) == task_root.name:
        return None
    topology = TaskDocumentTopology(context.coordination_root)
    master_ref = topology.canonical_ref(context.code_repository_name, task_root / "task.json")
    parent_ref = topology.parent(master_ref)
    expected = Path(parent_ref.path).parent.name if parent_ref is not None else None
    if task_folder_name(args.parent_task) == expected:
        return None
    return start_input_edge_refusal(context, args, "parent_task", expected)


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


def invalid_contract_request_result(error: ContractError) -> WorktreeCommandResult:
    """A ``worktree_start`` argument the contract vocabulary does not hold, as a refusal.

    ``workflow_kind`` and ``memory_mode`` arrive at the MCP signature as free ``str`` and are
    narrowed by ``_task_vocabulary`` before anything is written; the narrowing raises, and
    nothing between here and the ``@server.tool()`` handler catches a ``ContractError``. A
    caller who mistypes one gets a refusal that names the legal set, in the same
    ``WorktreeCommandResult`` shape as every other blocked start, rather than a traceback.
    """
    return WorktreeCommandResult(
        2,
        {
            "state": "invalid-request",
            "summary": f"worktree_start refused: {error}",
        },
    )
