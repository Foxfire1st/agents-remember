"""Build worktree-start contracts and normalize their task leaf identity."""

from __future__ import annotations

from pathlib import Path

from agents_remember.tasks import read_task_doc
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    branch_exists,
    current_branch,
    head_commit,
    require_git,
)
from agents_remember.worktrees.modules.leaf_ref_start import (
    invalid_contract_request_result,
    invalid_leaf_ref_result,
    resolve_start_leaf_doc_id,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.task_resolver import (
    resolve_active_task_root,
    series_contract_path,
    slugify,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    default_series_contract,
    load_contract,
    write_contract,
)


def _start_memory_repo(context, memory_mode: str):
    if memory_mode != "external":
        return None
    return context.coordination_root / "memory-repos" / f"ar-{context.code_repository_name}"


def _memory_base_commit(memory_repo) -> str:
    if memory_repo is None:
        return ""
    if not memory_repo.exists() or not (memory_repo / ".git").exists():
        return ""
    return head_commit(memory_repo)


def memory_base_for_source(memory_repo, memory_source_branch: str) -> str:
    """Return the tip of the memory source branch a worktree is created from."""

    if memory_repo is None or not memory_source_branch:
        return _memory_base_commit(memory_repo)
    if not memory_repo.exists() or not (memory_repo / ".git").exists():
        return ""
    if branch_exists(memory_repo, memory_source_branch):
        return head_commit(memory_repo, memory_source_branch)
    return _memory_base_commit(memory_repo)


def _external_memory_value(memory_mode: str, value: str) -> str:
    return value if memory_mode == "external" else ""


def _memory_plan(
    memory_repo: Path | None,
    *,
    source_branch: str,
    work_branch: str,
    base_commit: str,
) -> RepoBranchPlan | None:
    """The memory side's branch plan, or ``None`` when this task has no memory repository.

    Absence is the whole state: without a repo path there is no memory branch, no memory base
    and no ledger, so a plan whose repo path is missing is not a plan.
    """
    if memory_repo is None:
        return None
    return RepoBranchPlan(
        repo_path=memory_repo,
        source_branch=source_branch,
        work_branch=work_branch,
        base_commit=base_commit,
    )


def _task_root_has_master_artifact(task_root: Path) -> bool:
    json_path = task_root / "task.json"
    if json_path.exists():
        return read_task_doc(json_path).kind == "master"
    markdown_path = task_root / "task.md"
    if not markdown_path.exists():
        return False
    try:
        head = markdown_path.read_text(encoding="utf-8")[:1000]
    except OSError:
        return False
    return "**Type:** Master" in head


def _ensure_branch(repo: Path, branch: str, source: str, *, dry_run: bool) -> None:
    if branch_exists(repo, branch) or dry_run:
        return
    require_git(repo, ["branch", branch, source])


def _parent_series_contract(
    context, args: WorktreeArgs, memory_mode: str
) -> WorktreeContract | None:
    if not args.task_name:
        return None
    task_root = resolve_active_task_root(
        context.coordination_root,
        context.code_repository_name,
        args.task_name,
        parent_task=args.parent_task,
    )
    path = series_contract_path(task_root)
    if not path.exists():
        if not _task_root_has_master_artifact(task_root):
            return None
        repo = context.code_repository_root
        protected_branch = args.source_branch or current_branch(repo)
        integration_branch = f"ar/{slugify(args.task_name)}"
        leaf_branch = args.work_branch or f"ar/{args.worktree_name}"
        if leaf_branch == integration_branch:
            raise RuntimeError(
                "master-series leaf work branch would equal the integration branch; "
                "choose a distinct worktree_name or work_branch"
            )
        _ensure_branch(repo, integration_branch, protected_branch, dry_run=args.dry_run)
        memory_repo = _start_memory_repo(context, memory_mode)
        memory_source_branch = _external_memory_value(memory_mode, protected_branch)
        memory_work_branch = _external_memory_value(memory_mode, integration_branch)
        if (
            memory_repo is not None
            and (memory_repo / ".git").exists()
            and memory_source_branch
            and memory_work_branch
        ):
            _ensure_branch(
                memory_repo,
                memory_work_branch,
                memory_source_branch,
                dry_run=args.dry_run,
            )
        contract = default_series_contract(
            ContractTask(
                name=args.task_name,
                repo_name=context.code_repository_name,
                coordination_root=context.coordination_root,
                workflow_kind=args.workflow_kind,
                memory_mode=memory_mode,
                parent_task_name=args.parent_task or "",
            ),
            code=RepoBranchPlan(
                repo_path=repo,
                source_branch=protected_branch,
                work_branch=integration_branch,
                base_commit=head_commit(repo, protected_branch),
            ),
            memory=_memory_plan(
                memory_repo,
                source_branch=memory_source_branch,
                work_branch=memory_work_branch,
                base_commit=memory_base_for_source(memory_repo, memory_source_branch),
            ),
            task_root=task_root,
        )
        if not args.dry_run:
            write_contract(contract.contract_path, contract)
        return contract
    try:
        contract = load_contract(path)
    except ContractError as exc:
        raise RuntimeError(f"parent task contract is not readable: {path}") from exc
    if contract.kind != "series":
        raise RuntimeError(f"parent task contract is not a series contract: {path}")
    return contract


def build_start_contract(context, args: WorktreeArgs) -> WorktreeContract | WorktreeCommandResult:
    """The contract a start would create, or the refusal that says why it cannot.

    Both are returned rather than raised, because `worktree_start`'s handler has no `except`
    for either. `LeafRefResolutionError` is always a bad *argument*: an unresolvable leaf ref.
    `ContractError` is NOT always an argument fault, and the docstring must not claim it is --
    the `except` wraps the whole call, so besides the intended case (a `workflow_kind` or
    `memory_mode` the contract vocabulary does not hold) it also catches a `ContractError`
    raised by the `write_contract` inside `_parent_series_contract`, which is a write-validation
    failure of the PARENT series contract rather than anything this caller passed. That path is
    still reported honestly -- `validate_contract` names the offending cell and the file -- so
    it is returned rather than re-raised on purpose; what would be wrong is describing it as a
    caller mistake when the caller may have supplied nothing at fault.
    """
    try:
        return _build_start_contract(context, args)
    except LeafRefResolutionError as exc:
        return invalid_leaf_ref_result(exc)
    except ContractError as exc:
        return invalid_contract_request_result(exc)


def _build_start_contract(context, args: WorktreeArgs) -> WorktreeContract:
    assert args.task_name is not None
    assert args.worktree_name is not None
    leaf_id = resolve_start_leaf_doc_id(context, args)
    repo = context.code_repository_root
    memory_mode = args.memory_mode or context.memory_mode
    parent_series = _parent_series_contract(context, args, memory_mode)
    source_branch = args.source_branch or (
        parent_series.code_work_branch if parent_series is not None else current_branch(repo)
    )
    work_branch = args.work_branch or f"ar/{args.worktree_name}"
    if args.dry_run and parent_series is not None and not branch_exists(repo, source_branch):
        base_commit = parent_series.code_base_commit
    else:
        base_commit = head_commit(repo, source_branch)
    memory_repo = _start_memory_repo(context, memory_mode)
    memory_source_branch = _external_memory_value(memory_mode, source_branch)
    if (
        args.dry_run
        and parent_series is not None
        and memory_repo is not None
        and memory_source_branch
        and not branch_exists(memory_repo, memory_source_branch)
    ):
        memory_base = parent_series.memory_base_commit
    else:
        memory_base = memory_base_for_source(memory_repo, memory_source_branch)
    return default_contract(
        ContractTask(
            name=args.task_name,
            repo_name=context.code_repository_name,
            coordination_root=context.coordination_root,
            workflow_kind=args.workflow_kind,
            memory_mode=memory_mode,
            parent_task_name=parent_series.task_name if parent_series is not None else "",
            parent_contract_path=parent_series.contract_path if parent_series is not None else None,
        ),
        leaf=LeafIdentity(
            worktree_name=args.worktree_name, leaf_id=leaf_id, lifecycle_id=args.lifecycle_id
        ),
        code=RepoBranchPlan(
            repo_path=repo,
            source_branch=source_branch,
            work_branch=work_branch,
            base_commit=base_commit,
        ),
        memory=_memory_plan(
            memory_repo,
            source_branch=memory_source_branch,
            work_branch=_external_memory_value(memory_mode, work_branch),
            base_commit=memory_base,
        ),
    )
