"""Fail-closed source-line ancestry resolved from task and enclosure identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_freshness import ahead_behind
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.worktree import (
    SourceLineageEdge,
    SourceLineageProjection,
    SourceLineageRecovery,
    SourceLineageRelation,
    SourceLineageSide,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.modules.git import (
    branch_exists,
    local_branch_ref,
    repository_identity,
)
from agents_remember.worktrees.task_resolver import leaf_enclosure_path, series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

SourceLineageRefusalStatus = Literal["source-lineage-stale", "source-lineage-unavailable"]


@dataclass(frozen=True)
class _EdgeInput:
    relation: SourceLineageRelation
    side: SourceLineageSide
    repo: Path | None
    source_branch: str
    descendant_branch: str
    contract_path: Path
    descendant_commit: str | None = None
    exact_descendant: bool = False


def source_lineage_for_task(
    coordination_root: Path, task_document_ref: TaskDocumentRef
) -> SourceLineageProjection | None:
    """Resolve the applicable lineage before a document-bound role seat is created.

    Sprint roles have no single master edge and therefore return ``None``. Master and leaf
    roles resolve their contracts from the canonical task document; no runtime or commit id
    is accepted from the caller.
    """

    topology = TaskDocumentTopology(coordination_root)
    resolved = topology.resolve(task_document_ref)
    altitude = topology.altitude(task_document_ref)
    if altitude == "sprint":
        return None
    if altitude == "master":
        if resolved.document.executionNature == "organizational":
            return _organizational_master_projection(topology, resolved)
        contract_path = series_contract_path(resolved.path.parent)
        missing_relation: SourceLineageRelation = "super-to-master"
    else:
        contract_path = leaf_enclosure_path(resolved.path.parent, resolved.document.id)
        missing_relation = "master-to-leaf"
    contract = _load_required_contract(contract_path)
    if contract is None:
        return _unavailable_contract_projection(contract_path, missing_relation)
    return source_lineage_for_contract(contract)


def parent_source_lineage(contract: WorktreeContract) -> SourceLineageProjection | None:
    """Prove a leaf's master contains its super before reopen/start mutates leaf state."""

    if contract.kind != "leaf":
        return None
    if contract.parent_contract_path is None:
        return _projection(_organizational_leaf_edges(contract, prestart=True))
    if not contract.parent_contract_path.exists():
        return _unavailable_contract_projection(contract.parent_contract_path, "super-to-master")
    parent = _load_required_contract(contract.parent_contract_path)
    if parent is None:
        return _unavailable_contract_projection(contract.parent_contract_path, "super-to-master")
    return _projection([*_series_edges(parent), *_prestart_leaf_edges(parent, contract)])


def source_lineage_for_contract(contract: WorktreeContract) -> SourceLineageProjection | None:
    """Prove every applicable super -> master -> leaf edge for one contract."""

    if contract.kind == "series":
        return _projection(_series_edges(contract))
    if contract.kind != "leaf":
        return None
    if contract.parent_contract_path is None:
        return _projection(_organizational_leaf_edges(contract, prestart=False))
    parent = _load_required_contract(contract.parent_contract_path)
    if parent is None:
        return _unavailable_contract_projection(contract.parent_contract_path, "super-to-master")
    edges = [*_series_edges(parent), *_leaf_edges(parent, contract)]
    return _projection(edges)


def lineage_refusal(
    projection: SourceLineageProjection | None,
) -> tuple[SourceLineageRefusalStatus, str] | None:
    """Return the public refusal status/detail when lineage is not current."""

    if projection is None or projection.state == "current":
        return None
    status = (
        "source-lineage-stale" if projection.state == "blocked" else "source-lineage-unavailable"
    )
    recovery = projection.recoveries[0].contractPath if projection.recoveries else None
    suffix = f" First run worktree_sync for {recovery}." if recovery else ""
    return status, f"{projection.summary}{suffix}"


def require_current_source_lineage(
    contract: WorktreeContract, *, operation: str
) -> SourceLineageProjection | None:
    """Refuse a lifecycle boundary unless its full task-derived ancestry is current."""
    projection = source_lineage_for_contract(contract)
    refusal = lineage_refusal(projection)
    if refusal is None:
        return projection
    status, detail = refusal
    raise RuntimeError(
        f"{operation} requires current transitive source lineage ({status}): {detail}"
    )


def lineage_block_payload(projection: SourceLineageProjection) -> dict[str, object]:
    """Build one consistent fail-closed payload for worktree lifecycle entry points."""

    payload: dict[str, object] = {
        "state": "blocked",
        "summary": projection.summary,
        "source_lineage": projection.model_dump(by_alias=True),
    }
    if projection.recoveries:
        recovery = projection.recoveries[0]
        payload.update(
            {
                "nextOperation": "sync_source_lineage",
                "nextTool": recovery.tool,
                "nextArgs": recovery.args,
            }
        )
    return payload


def _load_required_contract(path: Path) -> WorktreeContract | None:
    if not path.is_file():
        return None
    try:
        return load_contract(path)
    except (ContractError, OSError):
        return None


def _unavailable_contract_projection(
    path: Path, relation: SourceLineageRelation
) -> SourceLineageProjection:
    edge = SourceLineageEdge(
        relation=relation,
        side="code",
        state="unavailable",
        sourceBranch="",
        descendantBranch="",
        contractPath=path.as_posix(),
        syncContractPath=path.as_posix(),
        detail="the task-bound enclosure contract is absent or unreadable",
    )
    return _projection([edge])


def _organizational_master_projection(
    topology: TaskDocumentTopology,
    master: ResolvedTaskDocument,
) -> SourceLineageProjection:
    """Validate the graph-owned super without inventing a master branch edge."""

    try:
        sprint_ref = topology.parent(master.ref)
        if sprint_ref is None:
            raise RuntimeError("organizational master is not commanded by a sprint")
        sprint = topology.resolve(sprint_ref)
        commanded = topology.validate_execution_topology(sprint_ref)
        if not any(item.ref == master.ref for item in commanded):
            raise RuntimeError("organizational master is absent from the sprint execution graph")
        if not sprint.document.integrationBranch:
            raise RuntimeError("commanding sprint does not declare integrationBranch")
    except (RuntimeError, TaskDocumentRefError) as exc:
        return _unavailable_topology_projection(master.path, str(exc))
    return _projection([])


def _organizational_leaf_edges(
    contract: WorktreeContract,
    *,
    prestart: bool,
) -> list[SourceLineageEdge]:
    """Resolve the direct sprint-super -> organizational leaf edge from task authority."""

    branch, detail = _organizational_source_branch(contract)
    if detail is not None:
        return [
            SourceLineageEdge(
                relation="super-to-leaf",
                side="code",
                state="unavailable",
                sourceBranch=contract.code_source_branch,
                descendantBranch=contract.code_work_branch,
                contractPath=contract.contract_path.as_posix(),
                syncContractPath=contract.contract_path.as_posix(),
                detail=detail,
            )
        ]
    assert branch is not None
    edges = [
        _organizational_edge(
            _EdgeInput(
                "super-to-leaf",
                "code",
                contract.code_repo_path,
                contract.code_source_branch,
                contract.code_work_branch,
                contract.contract_path,
                contract.code_base_commit if prestart else None,
                prestart,
            ),
            expected_source_branch=branch,
        )
    ]
    if contract.memory_mode == "external":
        edges.append(
            _organizational_edge(
                _EdgeInput(
                    "super-to-leaf",
                    "memory",
                    contract.memory_repo_path,
                    contract.memory_source_branch,
                    contract.memory_work_branch,
                    contract.contract_path,
                    contract.memory_base_commit if prestart else None,
                    prestart,
                ),
                expected_source_branch=branch,
            )
        )
    return edges


def _organizational_source_branch(
    contract: WorktreeContract,
) -> tuple[str | None, str | None]:
    topology = TaskDocumentTopology(contract.coordination_root)
    try:
        master_ref = topology.canonical_ref(contract.repo_name, contract.task_root / "task.json")
        master = topology.resolve(master_ref)
        if master.document.executionNature != "organizational":
            raise RuntimeError(
                "a leaf without a parent series must belong to an organizational master"
            )
        sprint_ref = topology.parent(master_ref)
        if sprint_ref is None:
            raise RuntimeError("organizational master is not commanded by a sprint")
        sprint = topology.resolve(sprint_ref)
        commanded = topology.validate_execution_topology(sprint_ref)
        if not any(item.ref == master_ref for item in commanded):
            raise RuntimeError("organizational master is absent from the sprint execution graph")
        branch = sprint.document.integrationBranch
        if not branch:
            raise RuntimeError("commanding sprint does not declare integrationBranch")
    except (RuntimeError, TaskDocumentRefError) as exc:
        return None, str(exc)
    return branch.removeprefix("refs/heads/"), None


def _organizational_edge(
    edge: _EdgeInput,
    *,
    expected_source_branch: str,
) -> SourceLineageEdge:
    if edge.source_branch.removeprefix("refs/heads/") != expected_source_branch:
        return SourceLineageEdge(
            relation=edge.relation,
            side=edge.side,
            state="unavailable",
            sourceBranch=edge.source_branch,
            descendantBranch=edge.descendant_branch,
            contractPath=edge.contract_path.as_posix(),
            syncContractPath=edge.contract_path.as_posix(),
            detail="the organizational leaf source does not match its sprint integrationBranch",
        )
    return _edge(edge)


def _unavailable_topology_projection(path: Path, detail: str) -> SourceLineageProjection:
    return _projection(
        [
            SourceLineageEdge(
                relation="super-to-master",
                side="code",
                state="unavailable",
                sourceBranch="",
                descendantBranch="",
                contractPath=path.as_posix(),
                syncContractPath=path.as_posix(),
                detail=detail,
            )
        ]
    )


def _series_edges(contract: WorktreeContract) -> list[SourceLineageEdge]:
    edges = [
        _edge(
            _EdgeInput(
                "super-to-master",
                "code",
                contract.code_repo_path,
                contract.code_source_branch,
                contract.code_work_branch,
                contract.contract_path,
            )
        )
    ]
    if contract.memory_mode == "external":
        edges.append(
            _edge(
                _EdgeInput(
                    "super-to-master",
                    "memory",
                    contract.memory_repo_path,
                    contract.memory_source_branch,
                    contract.memory_work_branch,
                    contract.contract_path,
                )
            )
        )
    return edges


def _leaf_edges(parent: WorktreeContract, leaf: WorktreeContract) -> list[SourceLineageEdge]:
    edges = [
        _linked_edge(
            _EdgeInput(
                "master-to-leaf",
                "code",
                leaf.code_repo_path,
                leaf.code_source_branch,
                leaf.code_work_branch,
                leaf.contract_path,
            ),
            expected_repo=parent.code_repo_path,
            expected_source_branch=parent.code_work_branch,
        )
    ]
    if parent.memory_mode == "external" or leaf.memory_mode == "external":
        edges.append(
            _linked_edge(
                _EdgeInput(
                    "master-to-leaf",
                    "memory",
                    leaf.memory_repo_path,
                    leaf.memory_source_branch,
                    leaf.memory_work_branch,
                    leaf.contract_path,
                ),
                expected_repo=parent.memory_repo_path,
                expected_source_branch=parent.memory_work_branch,
            )
        )
    return edges


def _prestart_leaf_edges(
    parent: WorktreeContract, leaf: WorktreeContract
) -> list[SourceLineageEdge]:
    edges = [
        _linked_edge(
            _EdgeInput(
                "master-to-leaf",
                "code",
                leaf.code_repo_path,
                leaf.code_source_branch,
                leaf.code_work_branch,
                leaf.contract_path,
                leaf.code_base_commit,
                True,
            ),
            expected_repo=parent.code_repo_path,
            expected_source_branch=parent.code_work_branch,
        )
    ]
    if parent.memory_mode == "external" or leaf.memory_mode == "external":
        edges.append(
            _linked_edge(
                _EdgeInput(
                    "master-to-leaf",
                    "memory",
                    leaf.memory_repo_path,
                    leaf.memory_source_branch,
                    leaf.memory_work_branch,
                    leaf.contract_path,
                    leaf.memory_base_commit,
                    True,
                ),
                expected_repo=parent.memory_repo_path,
                expected_source_branch=parent.memory_work_branch,
            )
        )
    return edges


def _linked_edge(
    edge: _EdgeInput,
    *,
    expected_repo: Path | None,
    expected_source_branch: str,
) -> SourceLineageEdge:
    if not _same_repo(edge.repo, expected_repo) or edge.source_branch != expected_source_branch:
        return SourceLineageEdge(
            relation=edge.relation,
            side=edge.side,
            state="unavailable",
            sourceBranch=edge.source_branch,
            descendantBranch=edge.descendant_branch,
            contractPath=edge.contract_path.as_posix(),
            syncContractPath=edge.contract_path.as_posix(),
            detail="the leaf contract does not name its parent master's repository and work branch",
        )
    return _edge(edge)


def _same_repo(left: Path | None, right: Path | None) -> bool:
    left_identity = repository_identity(left)
    right_identity = repository_identity(right)
    return left_identity is not None and left_identity == right_identity


def _edge(edge: _EdgeInput) -> SourceLineageEdge:
    detail: str | None = None
    counts: tuple[int, int] | None = None
    if edge.repo is None or not edge.repo.is_dir():
        detail = "the repository recorded by the contract is unavailable"
    elif not edge.source_branch or not edge.descendant_branch:
        detail = "the contract does not name both branches for this lineage edge"
    elif not branch_exists(edge.repo, edge.source_branch) or (
        edge.descendant_commit is None and not branch_exists(edge.repo, edge.descendant_branch)
    ):
        detail = "one or both recorded branches are absent"
    else:
        counts = ahead_behind(
            edge.repo,
            edge.descendant_commit or local_branch_ref(edge.descendant_branch),
            local_branch_ref(edge.source_branch),
        )
        if counts is None:
            detail = "Git could not compare the recorded branches"
    ahead, behind = counts if counts is not None else (None, None)
    if counts is None:
        state = "unavailable"
    elif edge.exact_descendant:
        state = "current" if counts == (0, 0) else "behind" if ahead == 0 else "diverged"
    else:
        state = "current" if behind == 0 else "behind" if ahead == 0 else "diverged"
    return SourceLineageEdge(
        relation=edge.relation,
        side=edge.side,
        state=state,
        sourceBranch=edge.source_branch,
        descendantBranch=edge.descendant_branch,
        ahead=ahead,
        behind=behind,
        contractPath=edge.contract_path.as_posix(),
        syncContractPath=edge.contract_path.as_posix(),
        detail=detail,
    )


def _projection(edges: list[SourceLineageEdge]) -> SourceLineageProjection:
    unavailable = any(edge.state == "unavailable" for edge in edges)
    stale = any(edge.state in {"behind", "diverged"} for edge in edges)
    state = "unavailable" if unavailable else "blocked" if stale else "current"
    recoveries: list[SourceLineageRecovery] = []
    seen: set[str] = set()
    for edge in edges:
        path = edge.syncContractPath
        if edge.state not in {"behind", "diverged"} or path in seen:
            continue
        seen.add(path)
        recoveries.append(
            SourceLineageRecovery(
                contractPath=path,
                args={"contract_path": path, "dry_run": True},
            )
        )
    summary = (
        "Source lineage is current across every applicable code and external-memory edge."
        if state == "current"
        else "Source lineage is stale; sync the ordered parent edges before task-bound work continues."
        if state == "blocked"
        else "Source lineage could not be proven; task-bound seats fail closed until contract and branch evidence is restored."
    )
    return SourceLineageProjection(
        state=state,
        summary=summary,
        edges=edges,
        recoveries=recoveries,
    )
