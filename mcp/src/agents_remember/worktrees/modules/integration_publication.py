"""Typed state carried from integration preflight into protected publication."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.enforcement import GateGuard
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class IntegratePreview:
    """The evaluated seam guard and the planned altitude-routed quality gate."""

    guard: GateGuard
    handover_warning: dict[str, object] | None
    quality_gate: dict[str, object]


@dataclass(frozen=True)
class IntegrationPublication:
    """Every preflight fact the irreversible publication must re-verify."""

    contract: WorktreeContract
    args: WorktreeArgs
    locked_args: WorktreeArgs
    sources: IntegrationSources
    commits: IntegratedCommits
    preflight_organizational_completion: bool
    quality_gate: dict[str, object]
    handover_warning: dict[str, object] | None
