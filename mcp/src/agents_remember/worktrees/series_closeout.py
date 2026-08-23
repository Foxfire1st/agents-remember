"""Named-ref-only closeout facts for an atomic master series."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.kernel.memory_ledger import LedgerRow, find_mapping, parse_ledger_text
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import completion_blockers
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    branch_worktree_owners,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    is_ancestor,
    repository_identity,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.queue.closeout_queue import (
    CloseoutQueueError,
    _graph_context,
    now_iso,
)
from agents_remember.worktrees.queue.closeout_queue_state import initial_queue_state
from agents_remember.worktrees.queue.closeout_recovery import MemoryCloseoutOutcome
from agents_remember.worktrees.scheduling_mode import effective_execution_nature
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

T = TypeVar("T")


@dataclass(frozen=True)
class _AtomicLandingFacts:
    leaf_ref: TaskDocumentRef
    sprint_ref: TaskDocumentRef | None


def publish_closeout_under_authority(contract: WorktreeContract, publication: Callable[[], T]) -> T:
    """Hold the exact task/ref authority through closeout contract publication."""

    if contract.kind == "leaf":
        with integration_authority_lock(contract.coordination_root, contract.repo_name):
            return publication()
    if contract.kind != "series":
        raise RuntimeError("atomic series closeout authority requires a series contract")
    return _publish_atomic_series_edge(
        contract,
        publication,
        edge="closeout",
    )


def publish_series_integration_under_authority(
    contract: WorktreeContract,
    publication: Callable[[], T],
) -> T:
    """Hold the atomic blocker and exact task/ref authority through its one landing."""

    if contract.kind != "series":
        raise RuntimeError("atomic series integration authority requires a series contract")
    return _publish_atomic_series_edge(
        contract,
        publication,
        edge="integration",
    )


def _publish_atomic_series_edge(
    contract: WorktreeContract,
    publication: Callable[[], T],
    *,
    edge: str,
) -> T:
    topology = TaskDocumentTopology(contract.coordination_root)
    master_ref = topology.canonical_ref(contract.repo_name, contract.task_root / "task.json")
    _require_atomic_master_complete(topology, master_ref)
    sprint_ref = topology.parent(master_ref)

    def repository_publication() -> T:
        with integration_authority_lock(contract.coordination_root, contract.repo_name):
            _require_atomic_master_complete(topology, master_ref)
            _require_every_atomic_leaf_landed(contract)
            return publication()

    if sprint_ref is None:
        return repository_publication()
    sprint = topology.resolve(sprint_ref)
    if sprint.document.executionGraph is None:
        # Atomic-sequential default (L13-R1): no queue graph exists; the master
        # already owns the sequential lane through its live series contract.
        return repository_publication()
    graph = _graph_context(topology, sprint_ref)
    initial = initial_queue_state(sprint_ref, graph.revision, now_iso())

    def validate_and_publish(state):
        current_graph = _graph_context(topology, sprint_ref)
        if current_graph.revision != graph.revision:
            raise CloseoutQueueError(
                f"atomic-series-{edge}-graph-moved",
                f"atomic master {edge} graph changed before protected publication",
            )
        blocker = state.activeBlocker
        if blocker is None or blocker.master != master_ref:
            raise CloseoutQueueError(
                f"atomic-series-{edge}-blocker-required",
                f"atomic master {edge} requires its exact active sprint landing blocker",
            )
        candidates = [
            candidate.taskDocumentRef.key
            for candidate in state.candidates.values()
            if candidate.owningMaster == master_ref
        ]
        if candidates:
            raise CloseoutQueueError(
                f"atomic-series-{edge}-candidates-remain",
                f"atomic master {edge} requires every own leaf landing: {candidates!r}",
            )
        return repository_publication()

    return CloseoutQueueStore(contract.coordination_root, sprint_ref).inspect(
        initial, validate_and_publish
    )


def _require_every_atomic_leaf_landed(series: WorktreeContract) -> None:
    _exact_atomic_landing_chain(series)


def _exact_atomic_landing_chain(series: WorktreeContract) -> list[WorktreeContract]:
    expected, sprint_ref = _atomic_leaf_documents(series)
    contracts: dict[str, WorktreeContract] = {}
    for path in sorted((series.task_root / "enclosures").glob("*/series-contract.md")):
        leaf = load_contract(path)
        if (
            leaf.kind != "leaf"
            or not leaf.leaf_id
            or leaf.leaf_id in contracts
            or leaf.contract_path.resolve() != path.resolve()
        ):
            raise CloseoutQueueError(
                "atomic-series-leaf-contract-set-invalid",
                f"atomic series has an invalid or duplicate leaf enclosure: {path}",
            )
        contracts[leaf.leaf_id] = leaf
    if set(contracts) != set(expected):
        raise CloseoutQueueError(
            "atomic-series-leaf-contract-set-incomplete",
            "atomic series closeout requires one exact enclosure for every canonical leaf: "
            f"expected={sorted(expected)!r}, found={sorted(contracts)!r}",
        )
    return _require_exact_atomic_landing_chain(series, contracts, expected, sprint_ref)


def _require_exact_atomic_landing_chain(
    series: WorktreeContract,
    contracts: dict[str, WorktreeContract],
    expected: dict[str, TaskDocumentRef],
    sprint_ref: TaskDocumentRef | None,
) -> list[WorktreeContract]:
    current_code = series.code_base_commit
    current_memory = series.memory_base_commit if series.memory_mode == "external" else ""
    remaining = dict(contracts)
    ordered: list[WorktreeContract] = []
    while remaining:
        next_ids = [
            leaf_id
            for leaf_id, leaf in remaining.items()
            if leaf.code_base_commit == current_code
            and (series.memory_mode != "external" or leaf.memory_base_commit == current_memory)
        ]
        if len(next_ids) != 1:
            raise CloseoutQueueError(
                "atomic-series-leaf-chain-invalid",
                "atomic series leaves do not form one exact code-and-memory landing chain",
            )
        leaf_id = next_ids[0]
        leaf = remaining.pop(leaf_id)
        _require_atomic_leaf_landed(
            series,
            leaf,
            _AtomicLandingFacts(expected[leaf_id], sprint_ref),
        )
        ordered.append(leaf)
        current_code = leaf.integrated_code_commit
        if series.memory_mode == "external":
            current_memory = leaf.integrated_ledger_commit
    _require_atomic_chain_tips(series, current_code, current_memory)
    return ordered


def atomic_series_ledger_prefix(series: WorktreeContract) -> tuple[LedgerRow, ...]:
    """Return the exact newest-first rows contributed by the atomic leaf chain."""

    if series.kind != "series" or series.memory_mode != "external":
        raise RuntimeError("atomic series ledger prefix requires an external-memory series")
    ordered = _exact_atomic_landing_chain(series)
    return tuple(
        LedgerRow(leaf.integrated_code_commit, leaf.integrated_memory_content_commit)
        for leaf in reversed(ordered)
    )


def _require_atomic_chain_tips(
    series: WorktreeContract,
    code_commit: str,
    memory_commit: str,
) -> None:
    if branch_commit(series.code_repo_path, series.code_work_branch) != code_commit:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            "atomic series code ref contains history outside the exact leaf landing chain",
        )
    if series.memory_mode != "external":
        return
    if series.memory_repo_path is None or (
        branch_commit(series.memory_repo_path, series.memory_work_branch) != memory_commit
    ):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            "atomic series memory ref contains history outside the exact leaf landing chain",
        )


def _atomic_leaf_documents(
    series: WorktreeContract,
) -> tuple[dict[str, TaskDocumentRef], TaskDocumentRef | None]:
    topology = TaskDocumentTopology(series.coordination_root)
    master_ref = topology.canonical_ref(series.repo_name, series.task_root / "task.json")
    master = topology.resolve(master_ref)
    expected: dict[str, TaskDocumentRef] = {}
    paths: set[str] = set()
    for row in master.document.subTasks:
        if not row.file or row.number in expected:
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                "atomic series requires unique subtask rows with exact task-document files",
            )
        leaf_path = (master.path.parent / row.file).with_suffix(".json")
        leaf_ref = topology.canonical_ref(series.repo_name, leaf_path)
        if leaf_ref.path in paths:
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                "atomic series subtask rows resolve to a duplicate task document",
            )
        leaf = topology.resolve(leaf_ref)
        if (
            leaf.document.kind == "master"
            or leaf.document.id != row.number
            or topology.parent(leaf_ref) != master_ref
        ):
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                f"atomic series row {row.number!r} does not bind one exact owned leaf",
            )
        expected[row.number] = leaf_ref
        paths.add(leaf_ref.path)
    if not expected:
        raise CloseoutQueueError(
            "atomic-series-leaf-task-set-invalid",
            "atomic series closeout requires at least one exact owned leaf",
        )
    return expected, topology.parent(master_ref)


def _require_atomic_leaf_landed(
    series: WorktreeContract,
    leaf: WorktreeContract,
    facts: _AtomicLandingFacts,
) -> None:
    if not _atomic_leaf_code_matches(series, leaf, facts):
        raise CloseoutQueueError(
            "atomic-series-leaf-not-landed",
            f"atomic leaf {leaf.leaf_id!r} has not landed on the exact series code ref",
        )
    if series.memory_mode != "external":
        return
    if not _atomic_leaf_memory_matches(series, leaf):
        raise CloseoutQueueError(
            "atomic-series-leaf-memory-not-landed",
            f"atomic leaf {leaf.leaf_id!r} has not landed its exact external-memory pair",
        )
    assert series.memory_repo_path is not None
    code_commit = leaf.integrated_code_commit
    memory_commit = leaf.integrated_memory_content_commit
    ledger_commit = leaf.integrated_ledger_commit
    ledger = parse_ledger_text(
        require_git(series.memory_repo_path, ["show", f"{ledger_commit}:memory.md"])
    )
    mapping = find_mapping(ledger, code_commit)
    if mapping is None or mapping.memory_commit != memory_commit:
        raise CloseoutQueueError(
            "atomic-series-leaf-ledger-mapping-invalid",
            f"atomic leaf {leaf.leaf_id!r} has no exact code-to-memory mapping on the series ref",
        )


def _atomic_leaf_code_matches(
    series: WorktreeContract,
    leaf: WorktreeContract,
    facts: _AtomicLandingFacts,
) -> bool:
    code_commit = leaf.integrated_code_commit
    parent_path = leaf.parent_contract_path.resolve() if leaf.parent_contract_path else None
    queue_sprint = facts.sprint_ref.key if facts.sprint_ref is not None else ""
    queue_candidate = facts.leaf_ref.key if facts.sprint_ref is not None else ""
    found = (
        leaf.repo_name,
        leaf.coordination_root.resolve(),
        leaf.task_root.resolve(),
        leaf.contract_path.resolve(),
        parent_path,
        leaf.memory_mode,
        leaf.queue_candidate_task_document,
        leaf.queue_sprint_task_document,
        leaf.integration_status,
        leaf.code_source_branch,
        code_commit,
    )
    expected = (
        series.repo_name,
        series.coordination_root.resolve(),
        series.task_root.resolve(),
        leaf_enclosure_path(series.task_root, leaf.leaf_id).resolve(),
        series.contract_path.resolve(),
        series.memory_mode,
        queue_candidate,
        queue_sprint,
        "completed",
        series.code_work_branch,
        leaf.code_commit,
    )
    return (
        bool(code_commit)
        and found == expected
        and _same_repository(leaf.code_repo_path, series.code_repo_path)
        and is_ancestor(series.code_repo_path, leaf.code_base_commit, code_commit)
    )


def _atomic_leaf_memory_matches(
    series: WorktreeContract,
    leaf: WorktreeContract,
) -> bool:
    memory_commit = leaf.integrated_memory_content_commit
    ledger_commit = leaf.integrated_ledger_commit
    if leaf.memory_repo_path is None or series.memory_repo_path is None:
        return False
    found = (
        leaf.memory_mode,
        leaf.memory_source_branch,
        memory_commit,
        ledger_commit,
    )
    expected = (
        "external",
        series.memory_work_branch,
        leaf.memory_content_commit,
        leaf.ledger_commit,
    )
    return (
        bool(memory_commit and ledger_commit)
        and found == expected
        and _same_repository(leaf.memory_repo_path, series.memory_repo_path)
        and is_ancestor(series.memory_repo_path, leaf.memory_base_commit, memory_commit)
        and is_ancestor(series.memory_repo_path, leaf.memory_base_commit, ledger_commit)
        and is_ancestor(series.memory_repo_path, memory_commit, ledger_commit)
    )


def _same_repository(left: Path, right: Path) -> bool:
    left_identity = repository_identity(left)
    right_identity = repository_identity(right)
    return left_identity is not None and left_identity == right_identity


def _require_atomic_master_complete(
    topology: TaskDocumentTopology,
    master_ref: TaskDocumentRef,
) -> ResolvedTaskDocument:
    master = topology.resolve(master_ref)
    sprint_ref = topology.parent(master_ref)
    sprint = topology.resolve(sprint_ref) if sprint_ref is not None else None
    try:
        # L13-R5a: the effective nature — a nature-less legacy master executes
        # atomically under the default and closes out without migration.
        nature = effective_execution_nature(
            master.document, sprint.document if sprint is not None else None
        )
    except TaskDocumentRefError as exc:
        raise CloseoutQueueError(
            "atomic-series-closeout-task-invalid", f"{exc.status}: {exc}"
        ) from exc
    if nature != "atomic":
        raise CloseoutQueueError(
            "atomic-series-closeout-task-invalid",
            "series closeout requires the canonical atomic master task",
        )
    blockers = completion_blockers(master.document)
    if master.document.status != "Completed" or blockers:
        raise CloseoutQueueError(
            "atomic-series-closeout-master-incomplete",
            f"atomic master closeout requires exact completion facts: {blockers!r}",
        )
    return master


def refuse_series_workbench_commit(contract: WorktreeContract) -> None:
    """Refuse dirty checkouts that own the exact atomic integration refs."""

    if contract.kind == "leaf":
        return
    branches = [(contract.code_repo_path, contract.code_work_branch)]
    if contract.memory_mode == "external":
        if contract.memory_repo_path is None:
            raise RuntimeError("external-memory series closeout requires a memory repository")
        branches.append((contract.memory_repo_path, contract.memory_work_branch))
    for repository, branch in branches:
        for checkout in branch_worktree_owners(repository, branch):
            if worktree_dirty(checkout):
                raise RuntimeError(
                    "series/master closeout cannot create code, memory, or ledger commits on "
                    "its integration worktree; land all content through closed leaves first"
                )


def exact_series_memory_closeout(
    contract: WorktreeContract, code_commit: str
) -> MemoryCloseoutOutcome:
    """Read the exact atomic memory ref and prove its ledger maps the code ref."""

    if contract.memory_repo_path is None:
        raise RuntimeError("external-memory series closeout requires a memory repository")
    ledger_commit = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    ledger = parse_ledger_text(
        require_git(
            contract.memory_repo_path,
            ["show", f"{ledger_commit}:memory.md"],
        )
    )
    mapping = find_mapping(ledger, code_commit)
    if mapping is None:
        raise RuntimeError(
            "series/master closeout requires its existing ledger head to map the exact "
            "series code commit; integration branches are not closeout workbenches"
        )
    if not is_ancestor(
        contract.memory_repo_path,
        mapping.memory_commit,
        ledger_commit,
    ):
        raise RuntimeError(
            "series/master closeout ledger maps memory content that is not reachable "
            "from the exact series memory head"
        )
    return MemoryCloseoutOutcome(
        memory_commit=mapping.memory_commit,
        ledger_commit=ledger_commit,
    )
