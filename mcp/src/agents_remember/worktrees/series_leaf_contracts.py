"""Exact leaf document and enclosure membership for an atomic master's landing chain."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document import SubTaskRef
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


@dataclass(frozen=True)
class AtomicLeafDocuments:
    """The rows of an atomic master, split by what its closeout asks of them.

    ``refs`` holds the owned leaf document of every row that is not abandoned. An abandoned row
    is a finished record of a leaf that will not run: the closeout asks it for no file cell, no
    document and no number of its own, so it is known by its number alone, and a number that a
    row which is not abandoned also carries belongs to that row.
    """

    refs: dict[str, TaskDocumentRef]
    abandoned: frozenset[str]

    @property
    def rows(self) -> frozenset[str]:
        return self.abandoned | self.refs.keys()


def atomic_leaf_documents(
    series: WorktreeContract,
) -> AtomicLeafDocuments:
    topology = TaskDocumentTopology(series.coordination_root)
    master_ref = topology.canonical_ref(series.repo_name, series.task_root / "task.json")
    master = topology.resolve(master_ref)
    expected: dict[str, TaskDocumentRef] = {}
    abandoned: set[str] = set()
    paths: dict[str, str] = {}
    master_file = master.path.as_posix()
    for row in master.document.subTasks:
        if row.status == "abandoned":
            abandoned.add(row.number)
            continue
        if row.number in expected:
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                f"master {series.task_id!r} subtask row {row.number!r} in {master_file} repeats "
                "the number of an earlier row; give every row that is not abandoned a unique "
                "number (task_doc replace on the master) before closeout",
            )
        leaf = _row_leaf_document(series, topology, master, row)
        if leaf.ref.path in paths:
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                f"master {series.task_id!r} subtask rows {paths[leaf.ref.path]!r} and "
                f"{row.number!r} in {master_file} resolve to the same task document "
                f"{leaf.ref.path!r}; point each row at its own leaf document (task_doc "
                "set_subtask on the master) before closeout",
            )
        if (
            leaf.document.kind == "master"
            or leaf.document.id != row.number
            or topology.parent(leaf.ref) != master_ref
        ):
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                f"master {series.task_id!r} row {row.number!r} in {master_file} does not bind one "
                f"exact owned leaf: {leaf.ref.path!r} must be a leaf document with id "
                f"{row.number!r} whose parent is this master; repair that row's file cell or the "
                "leaf document's id/master before closeout",
            )
        expected[row.number] = leaf.ref
        paths[leaf.ref.path] = row.number
    if not master.document.subTasks:
        raise CloseoutQueueError(
            "atomic-series-leaf-task-set-invalid",
            f"master {series.task_id!r} ({master_file}) has no subtask rows, so there is no leaf "
            "to close out; add its leaf rows with task_doc set_subtask on the master before closeout",
        )
    return AtomicLeafDocuments(expected, frozenset(abandoned - expected.keys()))


def _row_leaf_document(
    series: WorktreeContract,
    topology: TaskDocumentTopology,
    master: ResolvedTaskDocument,
    row: SubTaskRef,
) -> ResolvedTaskDocument:
    """The leaf document that a row which is not abandoned names in its file cell."""

    master_file = master.path.as_posix()
    if not row.file:
        raise CloseoutQueueError(
            "atomic-series-leaf-task-set-invalid",
            f"master {series.task_id!r} subtask row {row.number!r} in {master_file} has no "
            "task-document file; give the row its exact leaf document in the file cell "
            "(task_doc set_subtask on the master) before closeout",
        )
    leaf_path = (master.path.parent / row.file).with_suffix(".json")
    try:
        return topology.resolve(topology.canonical_ref(series.repo_name, leaf_path))
    except TaskDocumentRefError as exc:
        raise TaskDocumentRefError(
            exc.status,
            f"master {series.task_id!r} subtask row {row.number!r} in {master_file} names a leaf "
            f"document the closeout cannot use: {exc}; restore that document or point the row's "
            "file cell at its leaf document (task_doc set_subtask on the master) before closeout",
        ) from exc


def exact_atomic_leaf_contracts(series: WorktreeContract) -> dict[str, WorktreeContract]:
    documents = atomic_leaf_documents(series)
    contracts: dict[str, WorktreeContract] = {}
    for path in sorted((series.task_root / "enclosures").glob("*/series-contract.md")):
        try:
            leaf = load_contract(path)
        except (ContractError, OSError) as exc:
            raise CloseoutQueueError(
                "atomic-series-leaf-contract-unreadable",
                f"master {series.task_id!r} cannot read enclosure {path}: {exc}; repair that file and retry closeout",
            ) from exc
        if (
            leaf.kind != "leaf"
            or not leaf.leaf_id
            or leaf.leaf_id in contracts
            or leaf.contract_path.resolve() != path.resolve()
        ):
            raise CloseoutQueueError(
                "atomic-series-leaf-contract-set-invalid",
                f"master {series.task_id!r} has an invalid, foreign or duplicate enclosure: {path}; repair its canonical leaf binding and retry closeout",
            )
        if leaf.leaf_id not in documents.rows:
            raise CloseoutQueueError(
                "atomic-series-leaf-contract-set-incomplete",
                f"master {series.task_id!r} has an enclosure {path} for leaf {leaf.leaf_id!r}, which is not a row of the master; remove that enclosure or add the row with task_doc set_subtask before closeout",
            )
        if leaf.leaf_id in documents.abandoned and leaf.integration_status == "completed":
            raise CloseoutQueueError(
                "atomic-series-abandoned-leaf-landed",
                f"master {series.task_id!r} row {leaf.leaf_id!r} is abandoned but enclosure {path} records completed integration; reconcile that row's status with its landing before closeout",
            )
        contracts[leaf.leaf_id] = leaf
    missing = documents.refs.keys() - contracts.keys()
    if missing:
        raise CloseoutQueueError(
            "atomic-series-leaf-contract-set-incomplete",
            f"master {series.task_id!r} closeout requires one exact enclosure for each non-abandoned row: missing={sorted(missing)!r}; complete those rows through their leaf lifecycle before closeout",
        )
    return {leaf_id: contracts[leaf_id] for leaf_id in sorted(documents.refs)}
