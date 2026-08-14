"""Build worktree-start contracts and normalize their task leaf identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.leaf_doc import (
    LeafLifecycleRestampPlan,
    plan_leaf_doc_lifecycle_restamp,
)
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    branch_exists,
    current_branch,
    head_commit,
    repository_identity,
    require_git,
    run_git,
)
from agents_remember.worktrees.modules.leaf_ref_start import (
    invalid_contract_request_result,
    invalid_leaf_ref_result,
    resolve_start_leaf_doc_id,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.task_resolver import (
    leaf_enclosure_path,
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

MASTER_SERIES_BOOTSTRAP_OWNERSHIP = StoreOwnership(
    store="master-series-bootstrap",
    writers=("mcp",),
    compaction_owner=None,
    rationale=("Manager dispatch and first-leaf start share one structural bootstrap transaction."),
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


def _create_series_branch(repo: Path, branch: str, source: str, *, dry_run: bool) -> bool:
    """Create one new series branch from its declared source, never adopt an orphan."""

    if not branch_exists(repo, source):
        raise RuntimeError(f"declared series source branch does not exist: {source}")
    if branch_exists(repo, branch):
        raise RuntimeError(f"series branch exists without its task-bound contract: {branch}")
    if dry_run:
        return False
    require_git(repo, ["branch", branch, source])
    return True


def _remove_created_branch(repo: Path, branch: str) -> None:
    require_git(repo, ["branch", "-D", branch])


def memory_mode_for_repository(code_repo: Path, memory_root: Path | None) -> str:
    """Derive the contract vocabulary from the configured repository topology."""

    if memory_root is None:
        return "disabled"
    if memory_root.resolve() == (code_repo / "ar-memory").resolve():
        return "internal"
    return "external"


@dataclass(frozen=True)
class MasterSeriesContractSpec:
    """All task/config-derived identity needed to materialize one master edge."""

    coordination_root: Path
    repo_name: str
    code_repo: Path
    memory_root: Path | None
    task_root: Path
    task_name: str
    parent_task_name: str
    protected_branch: str
    workflow_kind: str = "light-task"


def ensure_master_series_contract(
    spec: MasterSeriesContractSpec,
    *,
    dry_run: bool = False,
) -> WorktreeContract:
    """Return or create the plane-owned contract for one canonical master document.

    The orchestrator must be able to create the manager seat before that manager starts the
    first leaf.  Contract and integration-branch identity therefore belong to the structural
    plane, not to the first worker request.  First-leaf start calls this same owner so both
    entry points remain one operation rather than competing bootstrap implementations.
    """

    if dry_run:
        existing = _existing_master_series_contract(spec)
        return existing if existing is not None else _new_master_series_contract(spec)

    # Manager dispatch and first-leaf start are two public entries into one bootstrap.  Hold the
    # same host-local/process-local lock across the second existence check, both branch creations,
    # and contract publication.  A concurrent loser therefore adopts the winner's completed edge;
    # it can never enter rollback and delete the winner's contract.
    with exclusive_access(
        _master_series_bootstrap_lock_target(spec), MASTER_SERIES_BOOTSTRAP_OWNERSHIP
    ):
        existing = _existing_master_series_contract(spec)
        if existing is not None:
            return existing
        contract = _new_master_series_contract(spec)
        _publish_master_series_contract(spec, contract)
        return contract


def _master_series_bootstrap_lock_target(spec: MasterSeriesContractSpec) -> Path:
    """Stable non-task-artifact target whose sibling lock serializes one master bootstrap."""

    return (
        spec.coordination_root
        / "logs"
        / "worktree-series-bootstrap"
        / slugify(spec.repo_name)
        / slugify(spec.task_name)
    )


def _existing_master_series_contract(
    spec: MasterSeriesContractSpec,
) -> WorktreeContract | None:
    path = series_contract_path(spec.task_root)
    if not path.exists():
        return None
    try:
        existing = load_contract(path)
    except ContractError as exc:
        raise RuntimeError(f"parent task contract is not readable: {path}") from exc
    if existing.kind != "series":
        raise RuntimeError(f"parent task contract is not a series contract: {path}")
    if not all(
        (
            _same_master_task_edge(existing, spec, path),
            _same_master_repository_edge(existing, spec),
            _same_master_branch_edge(existing, spec),
        )
    ):
        raise RuntimeError(
            "existing master series contract does not match the commanding sprint's "
            "declared integrationBranch and repository memory edge"
        )
    return existing


def _same_master_task_edge(
    existing: WorktreeContract, spec: MasterSeriesContractSpec, path: Path
) -> bool:
    expected_task_artifact = spec.task_root / "task.md"
    expected_worktree_group = spec.task_root / "enclosures"
    return all(
        (
            existing.task_id == slugify(spec.task_name).upper(),
            existing.task_name == spec.task_name,
            existing.repo_name == spec.repo_name,
            existing.workflow_kind == spec.workflow_kind,
            existing.coordination_root.resolve() == spec.coordination_root.resolve(),
            existing.task_root.resolve() == spec.task_root.resolve(),
            existing.contract_path.resolve() == path.resolve(),
            existing.task_artifact.resolve() == expected_task_artifact.resolve(),
            existing.worktree_group.resolve() == expected_worktree_group.resolve(),
            existing.parent_task_name == spec.parent_task_name,
            existing.parent_contract_path is None,
            existing.leaf_id == "",
            existing.lifecycle_id == "",
        )
    )


def _same_master_repository_edge(
    existing: WorktreeContract, spec: MasterSeriesContractSpec
) -> bool:
    expected_memory_mode = memory_mode_for_repository(spec.code_repo, spec.memory_root)
    expected_memory_repo = spec.memory_root if expected_memory_mode == "external" else None
    return all(
        (
            _same_repository_root(existing.code_repo_path, spec.code_repo),
            _same_repository_root(existing.code_worktree, spec.code_repo),
            existing.memory_mode == expected_memory_mode,
            _same_optional_repository_root(existing.memory_repo_path, expected_memory_repo),
            _same_series_memory_edge(
                existing.ledger_path,
                existing.memory_repo_path,
                existing.memory_worktree,
            ),
        )
    )


def _same_master_branch_edge(existing: WorktreeContract, spec: MasterSeriesContractSpec) -> bool:
    expected_branch = f"ar/{slugify(spec.task_name)}"
    external_memory = existing.memory_mode == "external"
    return all(
        (
            existing.code_source_branch == spec.protected_branch,
            existing.code_work_branch == expected_branch,
            existing.memory_source_branch == (spec.protected_branch if external_memory else ""),
            existing.memory_work_branch == (expected_branch if external_memory else ""),
        )
    )


def _repository_root(path: Path | None) -> Path | None:
    if path is None or not path.is_dir():
        return None
    result = run_git(path, ["rev-parse", "--show-toplevel"])
    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        return None
    root = Path(output).resolve()
    return root if path.resolve() == root else None


def _same_repository_root(left: Path | None, right: Path | None) -> bool:
    if _repository_root(left) is None or _repository_root(right) is None:
        return False
    left_identity = repository_identity(left)
    right_identity = repository_identity(right)
    return left_identity is not None and left_identity == right_identity


def _same_optional_repository_root(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _same_repository_root(left, right)


def _same_series_memory_edge(
    ledger: Path | None,
    memory_repo: Path | None,
    memory_worktree: Path | None,
) -> bool:
    if ledger is None or memory_repo is None:
        return ledger is None and memory_repo is None and memory_worktree is None
    repository_root = _repository_root(memory_repo)
    if repository_root is None:
        return False
    authority_root = repository_root
    if memory_worktree is not None:
        worktree_root = _repository_root(memory_worktree)
        if worktree_root is None or not _same_repository_root(memory_worktree, memory_repo):
            return False
        authority_root = worktree_root
    return ledger.resolve() == (authority_root / "memory.md").resolve()


def _new_master_series_contract(spec: MasterSeriesContractSpec) -> WorktreeContract:
    if not _task_root_has_master_artifact(spec.task_root):
        raise RuntimeError(f"master task document is missing from task root: {spec.task_root}")
    source_branch = spec.protected_branch
    integration_branch = f"ar/{slugify(spec.task_name)}"
    _validate_new_code_series_branch(spec.code_repo, source_branch, integration_branch)
    memory_mode = memory_mode_for_repository(spec.code_repo, spec.memory_root)
    external_memory = spec.memory_root if memory_mode == "external" else None
    memory_source_branch = _external_memory_value(memory_mode, source_branch)
    memory_work_branch = _external_memory_value(memory_mode, integration_branch)
    _validate_new_memory_series_branch(external_memory, memory_source_branch, memory_work_branch)
    return default_series_contract(
        ContractTask(
            name=spec.task_name,
            repo_name=spec.repo_name,
            coordination_root=spec.coordination_root,
            workflow_kind=spec.workflow_kind,
            memory_mode=memory_mode,
            parent_task_name=spec.parent_task_name,
        ),
        code=RepoBranchPlan(
            repo_path=spec.code_repo,
            source_branch=source_branch,
            work_branch=integration_branch,
            base_commit=head_commit(spec.code_repo, source_branch),
        ),
        memory=_memory_plan(
            external_memory,
            source_branch=memory_source_branch,
            work_branch=memory_work_branch,
            base_commit=memory_base_for_source(external_memory, memory_source_branch),
        ),
        task_root=spec.task_root,
    )


def _validate_new_code_series_branch(repo: Path, source: str, target: str) -> None:
    if source == target:
        raise RuntimeError(
            "master-series protected branch equals its integration branch; "
            "the commanding sprint must declare a distinct super integration branch"
        )
    if not source or not branch_exists(repo, source):
        raise RuntimeError(f"declared series source branch does not exist: {source}")
    if branch_exists(repo, target):
        raise RuntimeError(f"series branch exists without its task-bound contract: {target}")


def _validate_new_memory_series_branch(repo: Path | None, source: str, target: str) -> None:
    if repo is None or not (repo / ".git").exists() or not source or not target:
        return
    if not branch_exists(repo, source):
        raise RuntimeError(f"declared memory series source branch does not exist: {source}")
    if branch_exists(repo, target):
        raise RuntimeError(f"memory series branch exists without its task-bound contract: {target}")


def _publish_master_series_contract(
    spec: MasterSeriesContractSpec, contract: WorktreeContract
) -> None:
    external_memory = spec.memory_root if contract.memory_mode == "external" else None
    code_created = False
    memory_created = False
    try:
        code_created = _create_series_branch(
            spec.code_repo,
            contract.code_work_branch,
            contract.code_source_branch,
            dry_run=False,
        )
        if external_memory is not None:
            memory_created = _create_series_branch(
                external_memory,
                contract.memory_work_branch,
                contract.memory_source_branch,
                dry_run=False,
            )
        write_contract(contract.contract_path, contract)
    except Exception:
        contract.contract_path.unlink(missing_ok=True)
        if memory_created and external_memory is not None:
            _remove_created_branch(external_memory, contract.memory_work_branch)
        if code_created:
            _remove_created_branch(spec.code_repo, contract.code_work_branch)
        raise


def _declared_super_branch(context, task_root: Path) -> str:
    """Resolve the master parent sprint's exact integration branch from task identity."""

    topology = TaskDocumentTopology(context.coordination_root)
    try:
        master_ref = topology.canonical_ref(context.code_repository_name, task_root / "task.json")
        parent_ref = topology.parent(master_ref)
        if parent_ref is None:
            raise RuntimeError("master task has no commanding sprint document")
        parent = topology.resolve(parent_ref)
    except TaskDocumentRefError as exc:
        raise RuntimeError(f"cannot resolve the commanding sprint document: {exc}") from exc
    branch = parent.document.integrationBranch
    if not branch:
        raise RuntimeError(
            "commanding sprint task document must declare integrationBranch before "
            "manager dispatch or first-leaf start"
        )
    return branch


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
    if not series_contract_path(task_root).exists() and not _task_root_has_master_artifact(
        task_root
    ):
        return None
    repo = context.code_repository_root
    integration_branch = f"ar/{slugify(args.task_name)}"
    leaf_branch = args.work_branch or f"ar/{args.worktree_name}"
    if leaf_branch == integration_branch:
        raise RuntimeError(
            "master-series leaf work branch would equal the integration branch; "
            "choose a distinct worktree_name or work_branch"
        )
    configured_memory = (
        _start_memory_repo(context, memory_mode)
        if memory_mode == "external"
        else (repo / "ar-memory" if memory_mode == "internal" else None)
    )
    return ensure_master_series_contract(
        MasterSeriesContractSpec(
            coordination_root=context.coordination_root,
            repo_name=context.code_repository_name,
            code_repo=repo,
            memory_root=configured_memory,
            task_root=task_root,
            task_name=args.task_name,
            parent_task_name=args.parent_task or "",
            workflow_kind=args.workflow_kind,
            protected_branch=_declared_super_branch(context, task_root),
        ),
        dry_run=args.dry_run,
    )


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


def _start_restamp_block(plan: LeafLifecycleRestampPlan, *, dry_run: bool) -> WorktreeCommandResult:
    assert plan.doc_path is not None
    return WorktreeCommandResult(
        2,
        {
            "state": "task-steps-blocked",
            "dryRun": dry_run,
            "taskDocument": plan.doc_path.as_posix(),
            "lifecycleId": plan.lifecycle_id,
            "blockers": [blocker.model_dump() for blocker in plan.blockers],
            "summary": (
                "Worktree start refused before creating its enclosure because changing the "
                "leaf lifecycle would republish terminal status with unresolved work units."
            ),
        },
    )


def _start_will_restamp(task_root: Path, leaf_id: str) -> bool:
    contract_path = leaf_enclosure_path(task_root, leaf_id)
    if not contract_path.exists():
        return True
    existing = load_contract(contract_path)
    return existing.cleanup in ("abandoned", "reopened")


def _start_restamp_preflight(
    args: WorktreeArgs, leaf_id: str, task_root: Path
) -> WorktreeCommandResult | None:
    """Refuse a false-terminal restamp before contract construction can mutate state."""
    if not args.lifecycle_id or not _start_will_restamp(task_root, leaf_id):
        return None
    restamp = plan_leaf_doc_lifecycle_restamp(task_root, leaf_id, args.lifecycle_id)
    if not restamp.blockers:
        return None
    return _start_restamp_block(restamp, dry_run=args.dry_run)


def _build_start_contract(context, args: WorktreeArgs) -> WorktreeContract | WorktreeCommandResult:
    assert args.task_name is not None
    assert args.worktree_name is not None
    leaf_id = resolve_start_leaf_doc_id(context, args)
    task_root = resolve_active_task_root(
        context.coordination_root,
        context.code_repository_name,
        args.task_name,
        parent_task=args.parent_task,
    )
    restamp_block = _start_restamp_preflight(args, leaf_id, task_root)
    if restamp_block is not None:
        return restamp_block
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
