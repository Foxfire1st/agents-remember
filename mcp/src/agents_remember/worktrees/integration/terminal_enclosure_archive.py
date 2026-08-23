"""L2 fail-closed boundary before L5 owns terminal enclosure archival."""

from __future__ import annotations

import hashlib

from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


def terminal_archive_required_result(
    contract: WorktreeContract,
    *,
    operation: str,
    dry_run: bool,
) -> WorktreeCommandResult:
    """Refuse before terminal mutation until L5 proves archive/readback/receipt."""

    try:
        location = require_matching_lifecycle_operation_location(contract)
    except LifecycleOperationLocationError as error:
        return WorktreeCommandResult(
            2,
            {
                "state": error.status,
                "status": error.status,
                "dryRun": dry_run,
                "summary": error.detail,
                "detail": error.detail,
                "expected": error.expected,
                "observed": error.observed,
                "nextAction": "developer-decision",
                "developerDecisionRequired": True,
                "decisionSurface": error.detail,
            },
        )
    lifecycle = location.lifecycle_directory
    try:
        canonical_files = [
            {
                "name": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in sorted(lifecycle.iterdir(), key=lambda item: item.name)
            if path.is_file() and not path.name.endswith(".lock")
        ]
    except OSError as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "terminal-archive-evidence-unreadable",
                "status": "terminal-archive-evidence-unreadable",
                "dryRun": dry_run,
                "summary": "canonical lifecycle evidence is unreadable before terminal cleanup",
                "detail": "canonical lifecycle evidence is unreadable before terminal cleanup",
                "expected": {
                    "lifecycleDirectory": lifecycle.as_posix(),
                    "terminalArchive": "external readback and locator receipt",
                },
                "observed": {"errorType": type(error).__name__},
                "nextAction": "developer-decision",
                "developerDecisionRequired": True,
                "decisionSurface": (
                    "restore exact canonical lifecycle evidence before terminal archival"
                ),
            },
        )
    detail = (
        f"{operation} is archive-blocked: L5 has not published and read back an external "
        "terminal archive plus locator receipt, so L2 must preserve the enclosure root"
    )
    return WorktreeCommandResult(
        2,
        {
            "state": "terminal-archive-required",
            "status": "terminal-archive-required",
            "dryRun": dry_run,
            "summary": detail,
            "detail": detail,
            "expected": {
                "locatorState": "terminal-archived",
                "archiveReadback": "proven",
                "terminalReceipt": "proven",
            },
            "observed": {
                "locatorState": location.locator.state,
                "locatorPath": location.locator_path.as_posix(),
                "manifestPath": location.manifest_path.as_posix(),
                "lifecycleDirectory": lifecycle.as_posix(),
                "canonicalFiles": canonical_files,
            },
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": (
                "terminal cleanup remains unavailable until L5 owns archive/readback/receipt"
            ),
        },
    )
