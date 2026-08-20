"""Data contracts for task-derived integration branch authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology

IntegrationSurfaceKind = Literal["repository-default", "sprint-super", "atomic-integration"]
IntegrationTargetKind = Literal["sprint-super", "atomic-integration"]
IntegrationSurfaceSide = Literal["code", "memory"]


@dataclass(frozen=True)
class IntegrationSurface:
    side: IntegrationSurfaceSide
    kind: IntegrationSurfaceKind
    repository: Path
    branch: str
    owner: str


@dataclass(frozen=True)
class IntegrationTarget:
    side: IntegrationSurfaceSide
    kind: IntegrationTargetKind
    repository: Path
    branch: str
    owner: str


@dataclass(frozen=True)
class _RepositorySide:
    side: IntegrationSurfaceSide
    repository: Path
    worktree: Path
    source_branch: str
    work_branch: str


@dataclass(frozen=True)
class _BranchScope:
    coordination_root: Path
    repo_name: str
    task_root: Path
    sides: tuple[_RepositorySide, ...]


@dataclass(frozen=True)
class _MasterAuthority:
    topology: TaskDocumentTopology
    master_ref: TaskDocumentRef
    sprint_ref: TaskDocumentRef | None
    sprint_branch: str | None
    execution_nature: str | None


@dataclass(frozen=True)
class ProposedWorkBranches:
    coordination_root: Path
    repo_name: str
    task_root: Path
    code_repository: Path
    code_work_branch: str
    memory_repository: Path | None
    memory_work_branch: str


@dataclass(frozen=True)
class RepositoryCheckoutRequest:
    coordination_root: Path
    repo_name: str
    code_repository: Path
    memory_repository: Path | None
    checkout: Path
    side_name: IntegrationSurfaceSide
    operation: str
