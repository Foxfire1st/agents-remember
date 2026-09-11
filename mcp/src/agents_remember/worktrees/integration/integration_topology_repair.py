"""Explicit recovery for a topology edit that replaces deleted task owners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.integration.integration_branch_types import IntegrationSurface


def current_surfaces_for_publication(
    candidate: tuple[IntegrationSurface, ...],
    coordination_root: Path,
    overrides: Mapping[TaskDocumentRef, TaskDocument],
    read_current: Callable[[], tuple[IntegrationSurface, ...]],
) -> tuple[tuple[IntegrationSurface, ...], set[TaskDocumentRef]]:
    """Read current authority or prove the exact deleted-owner repair case."""

    try:
        return read_current(), set()
    except RuntimeError as error:
        _require_repairable_membership_error(error)
        repaired = _deleted_override_owners(coordination_root, overrides)
        _require_complete_owner_repair(repaired, overrides)
        return _surfaces_without_owners(candidate, repaired), repaired


def _require_repairable_membership_error(error: RuntimeError) -> None:
    cause = error.__cause__
    observed = (isinstance(cause, TaskDocumentRefError), getattr(cause, "status", None))
    if observed != (True, "task-execution-graph-membership-invalid"):
        raise error


def _deleted_override_owners(
    coordination_root: Path,
    overrides: Mapping[TaskDocumentRef, TaskDocument],
) -> set[TaskDocumentRef]:
    return {
        ref
        for ref in overrides
        if not (coordination_root / "tasks" / ref.repository / ref.path).is_file()
    }


def _require_complete_owner_repair(
    repaired: set[TaskDocumentRef],
    overrides: Mapping[TaskDocumentRef, TaskDocument],
) -> None:
    if (bool(repaired), len(repaired)) != (True, len(overrides)):
        raise RuntimeError("topology repair overrides do not exactly replace deleted owners")


def _surfaces_without_owners(
    candidate: tuple[IntegrationSurface, ...],
    repaired: set[TaskDocumentRef],
) -> tuple[IntegrationSurface, ...]:
    repaired_keys = {ref.key for ref in repaired}
    return tuple(surface for surface in candidate if surface.owner not in repaired_keys)
