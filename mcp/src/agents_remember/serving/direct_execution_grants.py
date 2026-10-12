"""The developer's direct-execution grant control, behind the dashboard route.

Serving rank: this module never imports ``application`` or MCP surfaces (the
layering test forbids it). It reuses the same substrate readers the
``prove_task_unstarted`` census reads -- task documents, the enclosure
contract, lifecycle operation locations and journals, the terminal seat
catalog, and the operator-inbox turn reports -- and folds them the same way:
any start evidence, or any doubt about a source, refuses the change. Both
routes write the same record through the kernel store.
"""

from __future__ import annotations

import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import MemoryModeUnsupportedError
from agents_remember.kernel.direct_execution_grants import (
    ARCHITECT_GRANTER,
    DEVELOPER_GRANTER,
    DirectExecutionGrant,
    DirectGrantError,
    create_grant_file,
    grant_path_for,
    grant_removal_paths,
    grant_store_locked,
    read_grant,
    remove_matching_grant,
    utc_now_iso,
)
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    inspect_lifecycle_operation_locator,
    resolve_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.observation.projection import (
    unreadable_contract_operation_projections,
)
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

#: The seat roles whose presence proves a leaf started (same set as the census).
_LEAF_SEAT_ROLES = frozenset({"worker", "reviewer", "curator"})

#: What remains possible after a started-leaf refusal (same as the architect route).
_STARTED_REMAINDER = "Finish the leaf as it is, or abandon it and plan a new one"


class DeveloperGrantRequest(BaseModel):
    """The dashboard control's body: which leaf, and whether to give or take back."""

    model_config = ConfigDict(extra="forbid")

    repository: str
    path: str
    action: str


@dataclass(frozen=True)
class DeveloperGrantChange:
    """The outcome of the developer giving or taking back a grant."""

    changed: bool
    grant: DirectExecutionGrant | None
    detail: str


def _resolve_leaf(
    coordination_root: Path, ref: TaskDocumentRef
) -> tuple[Path, str, TaskDocument]:
    """The leaf's task root, id, and document for a canonical leaf reference."""
    try:
        resolved = TaskDocumentTopology(coordination_root).resolve(ref)
    except Exception as error:
        raise DirectGrantError(
            f"no canonical task document for {ref.repository}:{ref.path}: {error}",
            status="direct-grant-leaf-missing",
        ) from error
    if resolved.document.kind != "subTask":
        raise DirectGrantError(
            "a direct-execution grant is given for a leaf only; "
            f"{ref.repository}:{ref.path} is {resolved.document.kind}",
            status="direct-grant-leaf-only",
        )
    return resolved.path.parent, resolved.document.id, resolved.document


def _refresh_leaf_markdown(coordination_root: Path, ref: TaskDocumentRef) -> None:
    """Re-render the leaf's stored Markdown with its live grant (F12)."""

    task_root, _leaf_id, _leaf = _resolve_leaf(coordination_root, ref)
    try:
        document = read_task_doc(task_root / Path(ref.path).name)
    except (OSError, ValueError) as error:
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} was stored, "
            f"but its document cannot be re-rendered: {error}",
            status="direct-grant-unwritable",
        ) from error
    write_task_doc(task_root, document)


def _ref_key(ref: TaskDocumentRef | None) -> tuple[str, str] | None:
    if ref is None:
        return None
    return (ref.repository, ref.path)


def _task_labels(task_root: Path, leaf_id: str, leaf: TaskDocument) -> list[str]:
    """Start evidence from the canonical task documents alone."""
    labels = [
        *_leaf_doc_labels(leaf),
        *_master_row_labels(task_root, leaf_id),
    ]
    return labels


def _leaf_doc_labels(leaf: TaskDocument) -> list[str]:
    blockers = [
        label
        for present, label in (
            (leaf.status != "planning", f"status={leaf.status}"),
            (leaf.lifecycleId is not None, "lifecycleId"),
            (bool(leaf.enclosures), "enclosures"),
            (bool(leaf.executionRegistrations), "executionRegistrations"),
            (leaf.routeReview is not None, "routeReview"),
        )
        if present
    ]
    blockers += [
        f"step:{step.id}:{step.status}"
        for step in leaf.steps
        if (step.status, step.disposition) != ("pending", None)
    ]
    blockers += [
        f"substep:{step.id}/{substep.id}:{substep.status}"
        for step in leaf.steps
        for substep in step.substeps
        if (substep.status, substep.disposition) != ("pending", None)
    ]
    if not blockers:
        return []
    return [f"task execution-progress ({', '.join(blockers)})"]


def _master_row_labels(task_root: Path, leaf_id: str) -> list[str]:
    try:
        master = read_task_doc(task_root / "task.json")
    except (OSError, ValueError) as error:
        return [f"master task document unreadable ({type(error).__name__})"]
    rows = [row for row in master.subTasks if row.number == leaf_id]
    if not rows:
        return [f"leaf {leaf_id} has no row on its master"]
    if rows[0].status != "planning":
        return [f"master row status={rows[0].status}"]
    return []


def _enclosure_labels(
    task_root: Path, leaf_id: str
) -> tuple[list[str], Any | None]:
    """Start evidence from the leaf enclosure contract; the contract when readable."""
    path = leaf_enclosure_path(task_root, leaf_id)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return [], None
    except OSError as error:
        return [f"leaf enclosure unreadable ({type(error).__name__})"], None
    if not stat.S_ISREG(mode):
        return ["leaf enclosure unreadable (not a regular file)"], None
    try:
        return [], load_contract(path)
    except MemoryModeUnsupportedError as error:
        return [f"leaf enclosure unreadable (removed mode {error.requested})"], None
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        return [f"leaf enclosure unreadable ({type(error).__name__})"], None


def _operation_labels(
    coordination_root: Path,
    contract_path: Path,
    contract_failure: str | None,
) -> list[str]:
    """Start evidence from the lifecycle locator and its journals."""
    locator = inspect_lifecycle_operation_locator(coordination_root, contract_path)
    if locator.state == "missing":
        return []
    if locator.state == "unreadable":
        return ["lifecycle locator unreadable"]
    if locator.state != "addressable":
        return [f"lifecycle locator {locator.state}"]
    try:
        location = resolve_lifecycle_operation_location(coordination_root, contract_path)
    except LifecycleOperationLocationError as error:
        return [f"lifecycle operation location unreadable ({error.status})"]
    projections = unreadable_contract_operation_projections(
        location,
        error_type=contract_failure or "ContractError",
        name=contract_path.name,
    )
    if not projections:
        return []
    kinds = ",".join(sorted({projection.kind for projection in projections}))
    return [f"lifecycle operations present ({kinds})"]


def _seat_labels(coordination_root: Path, ref: TaskDocumentRef) -> list[str]:
    """Start evidence from the terminal seat catalog."""
    path = terminal_catalog_path(coordination_root)
    if not path.is_file():
        return []
    try:
        rows = TerminalCatalog(path).list(include_terminated=True)
    except (OSError, ValueError, TypeError) as error:
        return [f"seat catalog unreadable ({type(error).__name__})"]
    wanted = (ref.repository, ref.path)
    matched = [
        f"{row.id}:{row.status}"
        for row in rows
        if row.binding_role in _LEAF_SEAT_ROLES
        and wanted
        in {_ref_key(row.task_document_ref), _ref_key(row.replacement_for_task_document_ref)}
    ]
    if not matched:
        return []
    return [f"leaf seats present ({','.join(sorted(matched))})"]


def _report_labels(config: McpRuntimeConfig, ref: TaskDocumentRef) -> list[str]:
    """Start evidence from the operator-inbox turn reports."""
    store = OperatorInboxStore(observer_root(config))
    path = store.log_path()
    if not path.is_file():
        return []
    try:
        entries = store.current().values()
    except (OSError, ValueError, TypeError) as error:
        return [f"turn reports unreadable ({type(error).__name__})"]
    wanted = (ref.repository, ref.path)
    matched = [
        entry.id
        for entry in entries
        if entry.messageKind == "turn-report"
        and entry.senderRole in _LEAF_SEAT_ROLES
        and wanted in {_ref_key(entry.subjectTaskDocumentRef), _ref_key(entry.taskDocumentRef)}
    ]
    if not matched:
        return []
    return [f"leaf turn reports present ({','.join(sorted(matched))})"]


def refuse_started_leaf(
    config: McpRuntimeConfig, coordination_root: Path, ref: TaskDocumentRef
) -> None:
    """Refuse a grant change for a started leaf, naming the evidence of the start."""
    task_root, leaf_id, leaf = _resolve_leaf(coordination_root, ref)
    labels = _task_labels(task_root, leaf_id, leaf)
    enclosure_labels, contract = _enclosure_labels(task_root, leaf_id)
    labels += enclosure_labels
    contract_failure = next(
        (
            label
            for label in enclosure_labels
            if label.startswith("leaf enclosure unreadable")
        ),
        None,
    )
    labels += _operation_labels(
        coordination_root,
        leaf_enclosure_path(task_root, leaf_id),
        contract_failure,
    )
    labels += _seat_labels(coordination_root, ref)
    labels += _report_labels(config, ref)
    if contract is not None:
        labels.append("leaf enclosure present")
    if not labels:
        return
    raise DirectGrantError(
        f"the grant for {ref.repository}:{ref.path} is refused: the leaf has started "
        f"({'; '.join(labels)}). {_STARTED_REMAINDER}",
        status="direct-grant-leaf-started",
    )


def give_grant_developer(
    config: McpRuntimeConfig, ref: TaskDocumentRef
) -> DeveloperGrantChange:
    """Give a leaf its grant from the dashboard control before work starts.

    The same lock-then-exclusive-create discipline as the architect writer:
    the read and proof hold the store lock, the publication is atomic, and a
    leaf that started between proof and publication does not keep the grant.
    """
    coordination_root = Path(config.coordination_root)
    address = f"{ref.repository}:{ref.path}"
    for _attempt in (0, 1):
        with grant_store_locked(coordination_root):
            _resolve_leaf(coordination_root, ref)
            existing = read_grant(coordination_root, ref)
            if existing is not None:
                return DeveloperGrantChange(
                    changed=False,
                    grant=existing,
                    detail=(
                        f"{address} already has its direct-execution grant "
                        f"({existing.describe()}); nothing changed"
                    ),
                )
            refuse_started_leaf(config, coordination_root, ref)
        grant = DirectExecutionGrant(
            leaf=ref, granted_by=DEVELOPER_GRANTER, granted_at=utc_now_iso()
        )
        try:
            create_grant_file(grant_path_for(coordination_root, ref), grant)
        except FileExistsError:
            existing = read_grant(coordination_root, ref)
            if existing is not None:
                return DeveloperGrantChange(
                    changed=False,
                    grant=existing,
                    detail=(
                        f"{address} already has its direct-execution grant "
                        f"({existing.describe()}); nothing changed"
                    ),
                )
            continue
        except OSError as error:
            raise DirectGrantError(
                f"the direct-execution grant for {address} cannot be written: {error}",
                status="direct-grant-unwritable",
            ) from error
        try:
            refuse_started_leaf(config, coordination_root, ref)
        except DirectGrantError:
            remove_matching_grant(coordination_root, ref, grant)
            raise
        _refresh_leaf_markdown(coordination_root, ref)
        return DeveloperGrantChange(
            changed=True,
            grant=grant,
            detail=f"{address} has its direct-execution grant ({grant.describe()})",
        )
    raise DirectGrantError(
        f"the direct-execution grant for {address} cannot be written: "
        "the grant file changed under this give",
        status="direct-grant-unwritable",
    )


def revoke_grant_developer(
    config: McpRuntimeConfig, ref: TaskDocumentRef
) -> DeveloperGrantChange:
    """Take back a leaf's grant from the dashboard control before the start.

    The read and proof hold the store lock and the removal is one atomic
    unlink, serial-consistent with concurrent gives. A leaf that started
    between the proof and the removal keeps its grant: the exact removed
    record is restored and the revoke is refused (F08).
    """
    coordination_root = Path(config.coordination_root)
    address = f"{ref.repository}:{ref.path}"
    with grant_store_locked(coordination_root):
        _resolve_leaf(coordination_root, ref)
        existing = read_grant(coordination_root, ref)
        if existing is None:
            return DeveloperGrantChange(
                changed=False,
                grant=None,
                detail=f"{address} has no direct-execution grant; nothing changed",
            )
        refuse_started_leaf(config, coordination_root, ref)
    removed = False
    for path in grant_removal_paths(coordination_root, ref):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError as error:
            raise DirectGrantError(
                f"the direct-execution grant for {address} cannot be removed: {error}",
                status="direct-grant-unwritable",
            ) from error
    if not removed:
        return DeveloperGrantChange(
            changed=False,
            grant=None,
            detail=f"{address} has no direct-execution grant; nothing changed",
        )
    try:
        refuse_started_leaf(config, coordination_root, ref)
    except DirectGrantError:
        _restore_removed_grant(coordination_root, ref, existing)
        raise
    _refresh_leaf_markdown(coordination_root, ref)
    return DeveloperGrantChange(
        changed=True,
        grant=None,
        detail=f"the direct-execution grant for {address} was taken back",
    )


def _restore_removed_grant(
    coordination_root: Path, ref: TaskDocumentRef, grant: DirectExecutionGrant
) -> None:
    """Restore exactly the grant a revoke removed, for a post-removal refusal.

    The exclusive create never clobbers a concurrent writer's grant: when
    another giver won the race, theirs stands and the revoke still refuses.
    """

    with suppress(FileExistsError, OSError):
        create_grant_file(grant_path_for(coordination_root, ref), grant)


def developer_grant_tool(
    config: McpRuntimeConfig, repository: str, path: str, action: str
) -> dict[str, Any]:
    """Give or take back one leaf's grant as the dashboard control's route."""
    ref = TaskDocumentRef(repository=repository, path=path)
    if action not in ("give", "revoke"):
        return {
            "ok": False,
            "operation": "direct_execution_grant",
            "state": "refused",
            "status": "direct-grant-action-unknown",
            "detail": f"direct_execution_grant action must be 'give' or 'revoke', not {action!r}",
            "nextAction": "correct-request",
            "changed": False,
            "grant": None,
        }
    try:
        change = (
            give_grant_developer(config, ref)
            if action == "give"
            else revoke_grant_developer(config, ref)
        )
    except DirectGrantError as error:
        return {
            "ok": False,
            "operation": "direct_execution_grant",
            "state": "refused",
            "status": error.status,
            "detail": error.detail,
            "nextAction": "correct-request",
            "changed": False,
            "grant": None,
        }
    if action == "give":
        state = "granted" if change.changed else "unchanged"
    else:
        state = "revoked" if change.changed else "unchanged"
    return {
        "ok": True,
        "operation": "direct_execution_grant",
        "state": state,
        "summary": change.detail,
        "changed": change.changed,
        "grant": change.grant.model_dump() if change.grant is not None else None,
    }


__all__ = [
    "ARCHITECT_GRANTER",
    "DeveloperGrantChange",
    "DeveloperGrantRequest",
    "developer_grant_tool",
    "give_grant_developer",
    "refuse_started_leaf",
    "revoke_grant_developer",
]
