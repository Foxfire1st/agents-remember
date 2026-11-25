"""Use the locator publisher's reservation across operations on an enclosure path."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

from agents_remember.controlplane.durable_store import exclusive_access
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    _LOCATION_OWNERSHIP,
    lifecycle_operation_locator_path,
)


def lifecycle_location_reservation(
    coordination_root: Path, contract_path: Path
) -> AbstractContextManager[None]:
    """Reserve the same stable address that worktree start uses before creating a generation."""

    return exclusive_access(
        lifecycle_operation_locator_path(coordination_root, contract_path), _LOCATION_OWNERSHIP
    )
