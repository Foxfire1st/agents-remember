"""Resolve caller-specific authority for completed lifecycle dispositions."""

from __future__ import annotations

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.ambient_seat import AmbientSeatError, resolve_ambient_seat
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.worktree_contract import WorktreeContract


class LifecycleCallerError(ValueError):
    """A caller identity is absent, contradictory, or unauthorized."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def resolve_lifecycle_caller(
    config: McpRuntimeConfig,
    declared: DeclaredCaller | None,
) -> DeclaredCaller | None:
    """Resolve a hosted seat, or an explicit ambient declaration when no seat exists."""

    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    try:
        seat = resolve_ambient_seat(catalog)
    except AmbientSeatError as exc:
        if exc.status != "ambient-seat-unavailable":
            raise LifecycleCallerError(exc.status, exc.detail) from exc
        return declared
    task_document_ref = seat.binding_task_document_ref
    if task_document_ref is None:
        raise LifecycleCallerError(
            "ambient-seat-unbound",
            "the hosted caller is not bound to a task document",
        )
    hosted = DeclaredCaller(
        role=seat.binding_role,
        task_document_ref=task_document_ref,
    )
    if declared is not None and declared != hosted:
        raise LifecycleCallerError(
            "lifecycle-control-caller-conflict",
            "declared caller conflicts with the plane-injected hosted seat",
        )
    return hosted


def completed_disposition_owner(
    contract: WorktreeContract,
) -> tuple[str, TaskDocumentRef]:
    """Return orchestrator@sprint, or architect@standalone-root, from live topology."""

    topology = TaskDocumentTopology(contract.coordination_root)
    master = topology.canonical_ref(
        contract.repo_name,
        contract.task_root / "task.json",
    )
    parent = topology.parent(master)
    return ("orchestrator", parent) if parent is not None else ("architect", master)


def completed_disposition_authorized(
    contract: WorktreeContract,
    caller: DeclaredCaller | None,
) -> bool:
    if caller is None:
        return False
    try:
        role, document = completed_disposition_owner(contract)
    except TaskDocumentRefError:
        return False
    return caller.role == role and caller.task_document_ref == document


def require_completed_disposition_authority(
    contract: WorktreeContract,
    caller: DeclaredCaller | None,
) -> None:
    try:
        role, document = completed_disposition_owner(contract)
    except TaskDocumentRefError as exc:
        raise LifecycleCallerError(
            "lifecycle-disposition-topology-invalid",
            f"cannot resolve completed disposition owner: {exc}",
        ) from exc
    if caller is None:
        raise LifecycleCallerError(
            "lifecycle-disposition-caller-required",
            "retire/supersede requires an explicit authorized caller",
        )
    if caller.role != role or caller.task_document_ref != document:
        raise LifecycleCallerError(
            "lifecycle-disposition-caller-unauthorized",
            f"retire/supersede requires {role}@{document.key}",
        )
