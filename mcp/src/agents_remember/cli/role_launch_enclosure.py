"""Prepare launch folders under the worktree enclosure's lifecycle authority."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from agents_remember.cli.role_held_store import leaf_workspace_allows_creation
from agents_remember.models.role_identity import LEAF_ROLES
from agents_remember.worktrees.integration.lifecycle.lifecycle_location_reservation import (
    lifecycle_location_reservation,
)
from agents_remember.worktrees.role_reports import (
    COLLECTED_REPORT_DIRECTORY,
    ROLE_REPORT_DIRECTORY,
)
from agents_remember.worktrees.worktree_contract import load_contract

if TYPE_CHECKING:
    from agents_remember.application.role_launch_context import RoleLaunchContext


class EnclosurePublicationRefusal(HTTPException):
    """The enclosure generation used by the compiled handover can no longer admit a seat."""

    def __init__(self, repo_id: str, contract_path: Path) -> None:
        super().__init__(
            status_code=409,
            detail=(
                "The role handover was prepared for an enclosure generation that is no longer "
                "active. Read "
                f"worktree_status(repo_id={repo_id!r}, contract_path={str(contract_path)!r}) "
                "before starting the role again."
            ),
        )


@contextmanager
def publication_scope(coordination_root: Path, workspace: dict[str, str]) -> Iterator[None]:
    """Admit writes to this exact live generation while the terminal locator cannot advance."""
    raw_path = workspace.get("contractPath")
    if raw_path is None:
        yield
        return
    contract_path = Path(raw_path)
    # Terminal publication and a successor reservation both use this same address.
    with lifecycle_location_reservation(coordination_root, contract_path):
        contract = load_contract(contract_path)
        if not (
            contract.coordination_root.resolve() == coordination_root.resolve()
            and workspace.get("lifecycleId", "") == contract.lifecycle_id
            and Path(workspace["path"]) == contract.worktree_group.resolve()
            and leaf_workspace_allows_creation({"workspace": workspace}, contract=contract)
        ):
            raise EnclosurePublicationRefusal(contract.repo_name, contract_path)
        yield


def prepare_role_folders(
    context: RoleLaunchContext, workspace: dict[str, str], request_id: uuid.UUID
) -> None:
    root = Path(workspace["path"])
    if context.role not in LEAF_ROLES:
        execution = root / ".agents-remember" / "role-executions" / str(request_id)
        local = execution / ROLE_REPORT_DIRECTORY
        canonical = (
            (context.effective_task.path.parent / COLLECTED_REPORT_DIRECTORY)
            if context.effective_task
            else root / ".agents-remember" / "reports"
        )
        workspace.update(taskReportAccessRoot=local.as_posix(), taskReportRoot=canonical.as_posix())
        scratch = execution / "scratch"
    else:
        workspace["taskReportAccessRoot"] = (
            root / ROLE_REPORT_DIRECTORY / context.role / str(request_id)
        ).as_posix()
        scratch = root / "scratch" / context.role / str(request_id)
    workspace["taskReportRoot"] = (
        Path(workspace["taskReportRoot"]) / context.role / str(request_id)
    ).as_posix()
    for path in (Path(workspace["taskReportAccessRoot"]), scratch):
        if any(
            parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(root)
        ) or not path.resolve().is_relative_to(root):
            raise ValueError(f"Role folder {path} must be a real directory inside {root}.")
        path.mkdir(parents=True, exist_ok=True)
    workspace["scratchRoot"] = scratch.as_posix()
