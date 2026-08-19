"""Build worktree-start contracts and normalize their task leaf identity."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.tasks import TaskDocument, read_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.leaf_doc import (
    LeafLifecycleRestampPlan,
    plan_leaf_doc_lifecycle_restamp,
)
from agents_remember.worktrees.atomic_series_seal import require_series_accepting_leaves
from agents_remember.worktrees.integration_branch_authority import (
    ProposedWorkBranches,
    integration_surfaces,
    repository_default_branch,
    require_proposed_work_branches,
)
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    branch_commit,
    branch_exists,
    current_branch,
    head_commit,
    repository_identity,
    run_git,
)
from agents_remember.worktrees.modules.leaf_ref_start import (
    invalid_contract_request_result,
    invalid_leaf_ref_result,
    resolve_start_leaf_doc_id,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.scheduling_mode import (
    TERMINAL_SERIES_CLEANUP,
    commanded_sprint_masters,
    effective_execution_nature,
    resolve_scheduling_mode,
    sequential_lane_owner,
)
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
    worktree_group_for,
    write_contract,
)

MASTER_SERIES_BOOTSTRAP_OWNERSHIP = StoreOwnership(
    store="master-series-bootstrap",
    writers=("mcp",),
    compaction_owner=None,
    rationale=("Manager dispatch and first-leaf start share one structural bootstrap transaction."),
)


class _SeriesBootstrapRecord(BaseModel):
    """Crash-recoverable intent for the code+memory atomic branch bootstrap."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1.0"] = "1.0"
    contractPath: str = Field(min_length=1, max_length=4096)
    codeRepository: str = Field(min_length=1, max_length=4096)
    codeSourceBranch: str = Field(min_length=1, max_length=4096)
    codeWorkBranch: str = Field(min_length=1, max_length=4096)
    codeBaseCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryRepository: str = Field(default="", max_length=4096)
    memorySourceBranch: str = Field(default="", max_length=4096)
    memoryWorkBranch: str = Field(default="", max_length=4096)
    memoryBaseCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class _BootstrapRef:
    repository: Path
    branch: str
    commit: str
    source_branch: str
    source_commit: str


_BOOTSTRAP_REF_AUTHORITY = object()


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
        return branch_commit(memory_repo, memory_source_branch)
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


def _master_execution_nature(task_root: Path) -> str | None:
    """The master document's declared nature; a nature-less master reports ``None``.

    Nature-less masters are legal legacy state: the atomic-sequential default
    (L13-R1) resolves them to an effective atomic nature at the decision point,
    so reading the declared cell here must not raise.
    """

    task_path = task_root / "task.json"
    if not task_path.is_file():
        return None
    document = read_task_doc(task_path)
    if document.kind != "master":
        return None
    return document.executionNature


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
) -> WorktreeContract | WorktreeCommandResult:
    """Return or create the plane-owned contract for one canonical master document.

    The orchestrator must be able to create the manager seat before that manager starts the
    first leaf.  Contract and integration-branch identity therefore belong to the structural
    plane, not to the first worker request.  First-leaf start calls this same owner so both
    entry points remain one operation rather than competing bootstrap implementations.

    Under the atomic-sequential default (L13-R1: the commanding sprint has no
    executionGraph) at most one master is in flight: creating a series while another
    commanded master still owns the lane returns a blocked result payload (state
    ``sequential-lane-owned``) that orders the work — land the owner first — instead
    of refusing it outright.
    """

    _require_commanded_atomic_master(spec)
    lane_block = _sequential_lane_block(spec)
    if lane_block is not None:
        return lane_block

    if dry_run:
        existing = _existing_master_series_contract(spec)
        return existing if existing is not None else _new_master_series_contract(spec)

    # Manager dispatch and first-leaf start are two public entries into one bootstrap.  Hold the
    # same host-local/process-local lock across the second existence check, both branch creations,
    # and contract publication.  A concurrent loser therefore adopts the winner's completed edge;
    # it can never enter rollback and delete the winner's contract.
    with (
        integration_authority_lock(spec.coordination_root, spec.repo_name),
        exclusive_access(
            _master_series_bootstrap_lock_target(spec), MASTER_SERIES_BOOTSTRAP_OWNERSHIP
        ),
    ):
        _require_commanded_atomic_master(spec)
        lane_block = _sequential_lane_block(spec)
        if lane_block is not None:
            return lane_block
        recovering = _recover_master_series_bootstrap(spec)
        if recovering is not None:
            return recovering
        existing = _existing_master_series_contract(spec)
        if existing is not None:
            return existing
        contract = _new_master_series_contract(spec)
        integration_surfaces(contract)
        _publish_master_series_contract(spec, contract)
        return contract


def _sequential_lane_block(spec: MasterSeriesContractSpec) -> WorktreeCommandResult | None:
    """Block a second in-flight master under the atomic-sequential default (L13-R1).

    A stored fact decides ownership: the other master's series contract exists with
    non-terminal cleanup. The blocked result orders the work — land the owner first —
    and always names a legal next operation.
    """

    topology = TaskDocumentTopology(spec.coordination_root)
    master_ref = topology.canonical_ref(spec.repo_name, spec.task_root / "task.json")
    sprint_ref = topology.parent(master_ref)
    if sprint_ref is None:
        # A standalone master is trivially sequential: no lane to contend for.
        return None
    # Fail closed (L13 review): if the commanding sprint cannot be resolved, the
    # lane question is unanswered and the bootstrap must not proceed — the
    # TaskDocumentRefError propagates as the typed refusal.
    mode = resolve_scheduling_mode(topology, sprint_ref)
    if mode.mode != "atomic-sequential":
        return None
    owner = sequential_lane_owner(topology, mode)
    if owner is None or owner.ref == master_ref:
        return None
    owner_contract = series_contract_path(owner.path.parent)
    return WorktreeCommandResult(
        2,
        {
            "state": "sequential-lane-owned",
            "laneOwner": owner.ref.key,
            "laneOwnerContractPath": owner_contract.as_posix(),
            "legalNextOperations": [
                f"complete master {owner.ref.key} and land its series "
                "(worktree_closeout_apply, then worktree_integrate)",
                "retire the owner series contract (worktree_cleanup or worktree_abandon): "
                f"{owner_contract.as_posix()}",
                "retry this master series bootstrap once the lane is free",
            ],
            "summary": (
                f"atomic-sequential sprint {sprint_ref.key} already has master "
                f"{owner.ref.key} in flight; one master fully integrates before the "
                "next master's series begins"
            ),
        },
    )


def _require_commanded_atomic_master(spec: MasterSeriesContractSpec) -> None:
    topology = TaskDocumentTopology(spec.coordination_root)
    try:
        master_ref = topology.canonical_ref(spec.repo_name, spec.task_root / "task.json")
        master = topology.resolve(master_ref)
        sprint_ref = topology.parent(master_ref)
        sprint = topology.resolve(sprint_ref) if sprint_ref is not None else None
        nature = effective_execution_nature(
            master.document, sprint.document if sprint is not None else None
        )
        if nature != "atomic":
            raise RuntimeError(
                f"series bootstrap requires an effective atomic master nature, got {nature!r}"
            )
        if sprint_ref is None or sprint is None:
            default_branch = repository_default_branch(spec.code_repo)
            if spec.protected_branch.removeprefix("refs/heads/") != default_branch:
                raise RuntimeError(
                    "standalone atomic series bootstrap must derive from the repository-default "
                    "branch"
                )
            return
        commanded = {item.ref for item in commanded_sprint_masters(topology, sprint)}
    except TaskDocumentRefError as exc:
        raise RuntimeError(
            f"cannot resolve atomic series bootstrap authority: {exc.status}: {exc}"
        ) from exc
    if master_ref not in commanded:
        raise RuntimeError("atomic series bootstrap task is not commanded by the sprint")
    if sprint.document.integrationBranch != spec.protected_branch:
        raise RuntimeError(
            "atomic series bootstrap source does not match the sprint integrationBranch"
        )


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
    if existing.cleanup in TERMINAL_SERIES_CLEANUP:
        # Stale terminal artifact (L13-R5b): it no longer owns the lane; the
        # caller's fresh bootstrap replaces it.
        return None
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
    expected_worktree_group = worktree_group_for(
        spec.coordination_root, spec.repo_name, spec.task_name
    )
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
            base_commit=branch_commit(spec.code_repo, source_branch),
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
    record = _bootstrap_record(spec, contract)
    path = _master_series_bootstrap_record_path(spec)
    atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
    _finish_master_series_bootstrap(spec, record)


def _master_series_bootstrap_record_path(spec: MasterSeriesContractSpec) -> Path:
    return _master_series_bootstrap_lock_target(spec).with_suffix(".json")


def _bootstrap_record(
    spec: MasterSeriesContractSpec,
    contract: WorktreeContract,
) -> _SeriesBootstrapRecord:
    code_identity = repository_identity(spec.code_repo)
    if code_identity is None:
        raise RuntimeError("cannot resolve code repository identity for series bootstrap")
    memory_repo = spec.memory_root if contract.memory_mode == "external" else None
    memory_identity = repository_identity(memory_repo)
    if memory_repo is not None and memory_identity is None:
        raise RuntimeError("cannot resolve memory repository identity for series bootstrap")
    return _SeriesBootstrapRecord(
        contractPath=contract.contract_path.resolve().as_posix(),
        codeRepository=code_identity.as_posix(),
        codeSourceBranch=contract.code_source_branch,
        codeWorkBranch=contract.code_work_branch,
        codeBaseCommit=contract.code_base_commit,
        memoryRepository=(memory_identity.as_posix() if memory_identity is not None else ""),
        memorySourceBranch=contract.memory_source_branch,
        memoryWorkBranch=contract.memory_work_branch,
        memoryBaseCommit=contract.memory_base_commit,
    )


def _recover_master_series_bootstrap(
    spec: MasterSeriesContractSpec,
) -> WorktreeContract | None:
    path = _master_series_bootstrap_record_path(spec)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        record = _SeriesBootstrapRecord.model_validate(payload)
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        raise RuntimeError(f"invalid master-series bootstrap record {path}: {exc}") from exc
    contract = _contract_from_bootstrap_record(spec, record)
    if contract.contract_path.is_file():
        existing = load_contract(contract.contract_path)
        if existing != contract:
            raise RuntimeError(
                "series bootstrap contract was published with different task or repository facts"
            )
        path.unlink(missing_ok=True)
        return existing
    if _bootstrap_ref_creation_started(spec, record):
        try:
            _require_commanded_atomic_master(spec)
            _require_current_bootstrap_sources(spec, record)
        except RuntimeError:
            _rollback_partial_bootstrap_refs(
                spec,
                record,
                authority=_BOOTSTRAP_REF_AUTHORITY,
            )
            path.unlink(missing_ok=True)
            return None
    return _finish_master_series_bootstrap(spec, record)


def _rollback_partial_bootstrap_refs(
    spec: MasterSeriesContractSpec,
    record: _SeriesBootstrapRecord,
    *,
    authority: object | None = None,
) -> None:
    if authority is not _BOOTSTRAP_REF_AUTHORITY:
        raise RuntimeError("series ref rollback requires the journaled bootstrap capability")
    targets = [(spec.code_repo, record.codeWorkBranch, record.codeBaseCommit)]
    if record.memoryRepository:
        if spec.memory_root is None:
            raise RuntimeError("series bootstrap record requires the external memory repository")
        targets.append((spec.memory_root, record.memoryWorkBranch, record.memoryBaseCommit))
    for repository, branch, expected in targets:
        if not branch_exists(repository, branch):
            continue
        result = run_git(
            repository,
            ["update-ref", "-d", f"refs/heads/{branch}", expected],
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"could not retire stale partial series ref {branch!r}: "
                f"{(result.stderr or result.stdout).strip()}"
            )


def _finish_master_series_bootstrap(
    spec: MasterSeriesContractSpec,
    record: _SeriesBootstrapRecord,
) -> WorktreeContract:
    contract = _contract_from_bootstrap_record(spec, record)
    if not _bootstrap_ref_creation_started(spec, record):
        try:
            _require_commanded_atomic_master(spec)
            _require_current_bootstrap_sources(spec, record)
        except RuntimeError:
            _master_series_bootstrap_record_path(spec).unlink(missing_ok=True)
            raise
    _require_bootstrap_ref(
        _BootstrapRef(
            repository=spec.code_repo,
            branch=record.codeWorkBranch,
            commit=record.codeBaseCommit,
            source_branch=record.codeSourceBranch,
            source_commit=record.codeBaseCommit,
        ),
        authority=_BOOTSTRAP_REF_AUTHORITY,
    )
    if record.memoryRepository:
        if spec.memory_root is None:
            raise RuntimeError("series bootstrap record requires the external memory repository")
        _require_bootstrap_ref(
            _BootstrapRef(
                repository=spec.memory_root,
                branch=record.memoryWorkBranch,
                commit=record.memoryBaseCommit,
                source_branch=record.memorySourceBranch,
                source_commit=record.memoryBaseCommit,
            ),
            authority=_BOOTSTRAP_REF_AUTHORITY,
        )
    if contract.contract_path.is_file():
        existing = load_contract(contract.contract_path)
        if existing != contract:
            raise RuntimeError(
                "series bootstrap contract was published with different task or repository facts"
            )
    else:
        write_contract(contract.contract_path, contract)
    _master_series_bootstrap_record_path(spec).unlink(missing_ok=True)
    return contract


def _bootstrap_ref_creation_started(
    spec: MasterSeriesContractSpec,
    record: _SeriesBootstrapRecord,
) -> bool:
    if branch_exists(spec.code_repo, record.codeWorkBranch):
        return True
    return bool(
        record.memoryRepository
        and spec.memory_root is not None
        and branch_exists(spec.memory_root, record.memoryWorkBranch)
    )


def _require_current_bootstrap_sources(
    spec: MasterSeriesContractSpec,
    record: _SeriesBootstrapRecord,
) -> None:
    code_source = branch_commit(spec.code_repo, record.codeSourceBranch)
    if code_source != record.codeBaseCommit:
        raise RuntimeError(
            "series bootstrap code source moved before protected-ref creation; retry from "
            "fresh task authority"
        )
    if record.memoryRepository:
        if spec.memory_root is None:
            raise RuntimeError("series bootstrap record requires the external memory repository")
        memory_source = branch_commit(spec.memory_root, record.memorySourceBranch)
        if memory_source != record.memoryBaseCommit:
            raise RuntimeError(
                "series bootstrap memory source moved before protected-ref creation; retry "
                "from fresh task authority"
            )


def _require_bootstrap_ref(
    ref: _BootstrapRef,
    *,
    authority: object | None = None,
) -> None:
    if authority is not _BOOTSTRAP_REF_AUTHORITY:
        raise RuntimeError("series ref creation requires the journaled bootstrap capability")
    if branch_exists(ref.repository, ref.branch):
        found = branch_commit(ref.repository, ref.branch)
        if found != ref.commit:
            raise RuntimeError(
                f"series bootstrap ref {ref.branch!r} is {found}, expected journaled {ref.commit}"
            )
        return
    if not ref.source_branch or not ref.source_commit:
        raise RuntimeError("series ref creation requires the exact journaled source authority")
    result = run_git(
        ref.repository,
        ["update-ref", "--stdin"],
        input_text="\n".join(
            [
                "start",
                f"verify refs/heads/{ref.source_branch} {ref.source_commit}",
                f"create refs/heads/{ref.branch} {ref.commit}",
                "prepare",
                "commit",
                "",
            ]
        ),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not create journaled series ref {ref.branch!r}: "
            f"{(result.stderr or result.stdout).strip()}"
        )


def _contract_from_bootstrap_record(
    spec: MasterSeriesContractSpec,
    record: _SeriesBootstrapRecord,
) -> WorktreeContract:
    code_identity = repository_identity(spec.code_repo)
    memory_identity = repository_identity(spec.memory_root)
    if code_identity is None or code_identity.as_posix() != record.codeRepository:
        raise RuntimeError("series bootstrap code repository identity changed")
    if record.contractPath != series_contract_path(spec.task_root).resolve().as_posix():
        raise RuntimeError("series bootstrap contract path changed")
    expected_work_branch = f"ar/{slugify(spec.task_name)}"
    if (
        record.codeSourceBranch != spec.protected_branch
        or record.codeWorkBranch != expected_work_branch
    ):
        raise RuntimeError("series bootstrap branch authority changed after journal publication")
    expected_memory_mode = memory_mode_for_repository(spec.code_repo, spec.memory_root)
    expected_external = expected_memory_mode == "external"
    if bool(record.memoryRepository) != expected_external:
        raise RuntimeError("series bootstrap memory edge changed after journal publication")
    if record.memoryRepository:
        if memory_identity is None or memory_identity.as_posix() != record.memoryRepository:
            raise RuntimeError("series bootstrap memory repository identity changed")
        if (
            record.memorySourceBranch != spec.protected_branch
            or record.memoryWorkBranch != expected_work_branch
        ):
            raise RuntimeError(
                "series bootstrap memory branch authority changed after journal publication"
            )
    elif record.memorySourceBranch or record.memoryWorkBranch or record.memoryBaseCommit:
        raise RuntimeError("disabled-memory series bootstrap journal carries a memory edge")
    return default_series_contract(
        ContractTask(
            name=spec.task_name,
            repo_name=spec.repo_name,
            coordination_root=spec.coordination_root,
            workflow_kind=spec.workflow_kind,
            memory_mode=expected_memory_mode,
            parent_task_name=spec.parent_task_name,
        ),
        code=RepoBranchPlan(
            repo_path=spec.code_repo,
            source_branch=record.codeSourceBranch,
            work_branch=record.codeWorkBranch,
            base_commit=record.codeBaseCommit,
        ),
        memory=_memory_plan(
            spec.memory_root if record.memoryRepository else None,
            source_branch=record.memorySourceBranch,
            work_branch=record.memoryWorkBranch,
            base_commit=record.memoryBaseCommit,
        ),
        task_root=spec.task_root,
    )


def _declared_integration_source_branch(context, task_root: Path) -> str:
    """Resolve a sprint super, or the PR-gated root for a standalone atomic master."""

    topology = TaskDocumentTopology(context.coordination_root)
    try:
        master_ref = topology.canonical_ref(context.code_repository_name, task_root / "task.json")
        parent_ref = topology.parent(master_ref)
        if parent_ref is None:
            # L13-R5e: a nature-less standalone master is atomic by default. An explicit
            # organizational standalone master already refused in TaskDocumentTopology.parent
            # (task-document-parent-missing), one frame up.
            return repository_default_branch(context.code_repository_root)
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
) -> WorktreeContract | WorktreeCommandResult | None:
    if not args.task_name:
        return None
    task_root = resolve_active_task_root(
        context.coordination_root,
        context.code_repository_name,
        args.task_name,
        parent_task=args.parent_task,
    )
    nature = _master_execution_nature(task_root)
    if nature is None and not _task_root_has_master_artifact(task_root):
        # No master document governs this task root: the leaf starts ungoverned.
        return None
    if _is_graph_organizational(context, task_root, nature):
        # Organizational semantics exist only under an authored graph (L13-R1).
        # A terminal stale series artifact no longer owns anything and is ignored
        # (L13-R5b); the start result reports its staleSeriesArtifact fact.
        stale = series_contract_path(task_root)
        if stale.exists():
            try:
                terminal = load_contract(stale).cleanup in TERMINAL_SERIES_CLEANUP
            except ContractError:
                terminal = False
            if not terminal:
                raise RuntimeError("organizational master must not carry an atomic series contract")
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
    series = ensure_master_series_contract(
        MasterSeriesContractSpec(
            coordination_root=context.coordination_root,
            repo_name=context.code_repository_name,
            code_repo=repo,
            memory_root=configured_memory,
            task_root=task_root,
            task_name=args.task_name,
            parent_task_name=args.parent_task or "",
            workflow_kind=args.workflow_kind,
            protected_branch=_declared_integration_source_branch(context, task_root),
        ),
        dry_run=args.dry_run,
    )
    if isinstance(series, WorktreeCommandResult):
        return series
    require_series_accepting_leaves(series, operation="atomic leaf start")
    return series


def _is_graph_organizational(context, task_root: Path, nature: str | None) -> bool:
    """Whether the master takes the organizational lane (only under an authored graph)."""

    if nature != "organizational":
        return False
    sprint = _commanding_sprint_document(context, task_root)
    return sprint is not None and sprint.executionGraph is not None


def _commanding_sprint_document(context, task_root: Path) -> TaskDocument | None:
    """The document of the sprint commanding this master, if topology resolves one."""

    topology = TaskDocumentTopology(context.coordination_root)
    try:
        master_ref = topology.canonical_ref(context.code_repository_name, task_root / "task.json")
        sprint_ref = topology.parent(master_ref)
        if sprint_ref is None:
            return None
        return topology.resolve(sprint_ref).document
    except TaskDocumentRefError:
        return None


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


def _start_source_branch(
    context,
    args: WorktreeArgs,
    task_root: Path,
    parent_series: WorktreeContract | None,
    repo: Path,
) -> str:
    master_nature = _master_execution_nature(task_root)
    expected_source = (
        parent_series.code_work_branch
        if parent_series is not None
        else _declared_integration_source_branch(context, task_root)
        if master_nature == "organizational"
        else current_branch(repo)
    )
    if args.source_branch and args.source_branch.removeprefix("refs/heads/") != expected_source:
        raise RuntimeError(
            "leaf source branch does not match its task-derived organizational or atomic parent"
        )
    return expected_source


def _start_code_base(
    repo: Path,
    source_branch: str,
    args: WorktreeArgs,
    parent_series: WorktreeContract | None,
) -> str:
    if args.dry_run and parent_series is not None and not branch_exists(repo, source_branch):
        return parent_series.code_base_commit
    return branch_commit(repo, source_branch)


def _start_memory_base(
    memory_repo: Path | None,
    memory_source_branch: str,
    args: WorktreeArgs,
    parent_series: WorktreeContract | None,
) -> str:
    if (
        args.dry_run
        and parent_series is not None
        and memory_repo is not None
        and memory_source_branch
        and not branch_exists(memory_repo, memory_source_branch)
    ):
        return parent_series.memory_base_commit
    return memory_base_for_source(memory_repo, memory_source_branch)


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
    memory_repo = _start_memory_repo(context, memory_mode)
    work_branch = args.work_branch or f"ar/{args.worktree_name}"
    require_proposed_work_branches(
        ProposedWorkBranches(
            coordination_root=context.coordination_root,
            repo_name=context.code_repository_name,
            task_root=task_root,
            code_repository=repo,
            code_work_branch=work_branch,
            memory_repository=memory_repo if memory_mode == "external" else None,
            memory_work_branch=_external_memory_value(memory_mode, work_branch),
        )
    )
    parent_series = _parent_series_contract(context, args, memory_mode)
    if isinstance(parent_series, WorktreeCommandResult):
        # Blocked start (e.g. the atomic-sequential lane is owned by another master).
        return parent_series
    source_branch = _start_source_branch(context, args, task_root, parent_series, repo)
    base_commit = _start_code_base(repo, source_branch, args, parent_series)
    memory_source_branch = _external_memory_value(memory_mode, source_branch)
    memory_base = _start_memory_base(
        memory_repo,
        memory_source_branch,
        args,
        parent_series,
    )
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
