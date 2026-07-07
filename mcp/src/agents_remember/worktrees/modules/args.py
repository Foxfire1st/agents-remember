"""Typed cross-layer DTO for worktree domain operations.

`WorktreeArgs` replaces the loosely typed `argparse.Namespace` that previously
flowed from the MCP controllers and the worktree CLI into the worktree domain
functions. Every field carries a sensible default so each operation can build
just the subset it needs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal

from agents_remember.controlplane.gate_policy import DEFAULT_GATE_POLICY, GatePolicy
from agents_remember.worktrees.modules.models import WorktreeProviderSetupConfig


@dataclass(frozen=True)
class WorktreeArgs:
    """Inputs shared by the worktree controllers, CLI, and domain functions."""

    # Coordination / repository resolution
    code_repository_name: str | None = None
    code_repository_root: Path | None = None
    coordination_root: Path | None = None
    workspace_root: Path | None = None
    topology: Literal["internal", "external"] | None = None
    contract_path: Path | None = None
    task_name: str | None = None
    parent_task: str | None = None
    leaf_id: str | None = None

    # Start inputs
    worktree_name: str | None = None
    workflow_kind: str = "light-task"
    source_branch: str | None = None
    work_branch: str | None = None
    memory_mode: str | None = None
    memory_choice: str | None = None
    stale_base_choice: str | None = None
    memory_sync_choice: str | None = None
    custom_instruction: str | None = None
    lifecycle_id: str = ""

    # Provider setup
    skip_provider_setup: bool = False
    provider_setup_config: WorktreeProviderSetupConfig | None = None
    provider_timeout: int = 1800
    retry_provider_setup: bool = False

    # Lifecycle flags
    dry_run: bool = False
    strategy: str = "ff-only"
    approved: bool = False
    approval_note: str = ""
    force: bool = False
    teardown_providers: bool = True

    # Closeout / integrate commit messages
    code_commit_message: str = ""
    memory_commit_message: str = ""
    ledger_commit_message: str = ""

    # Gate enforcement policy
    gate_policy: GatePolicy = DEFAULT_GATE_POLICY

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> WorktreeArgs:
        """Build from an argparse Namespace, falling back to field defaults.

        Argparse subparsers only populate the arguments they declare, and tests
        construct partial namespaces, so missing attributes fall back to the
        dataclass default for that field.
        """
        overrides = {
            field.name: getattr(namespace, field.name)
            for field in fields(cls)
            if hasattr(namespace, field.name)
        }
        return replace(cls(), **overrides)
