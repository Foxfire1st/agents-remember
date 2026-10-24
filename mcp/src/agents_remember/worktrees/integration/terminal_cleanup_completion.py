"""The completion record a finished cleanup leaves for the generations it outlived (MIK-R207).

An archive is published *before* the destructive outputs, so its existence is not completion
evidence; the live address answers that question with the sanctioned cleanup progression. A
generation a successor replaced cannot be asked the same way -- its contract dies with its
worktree group -- so the only surviving completion evidence is a record written by the operation
that completed. This module writes that record where the terminal archive lives and reads it back
for the sweep, so a predecessor's ended generation is proved by completion, not by a folder that
merely stopped existing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.lifecycles.enclosure import TerminalCleanupOperation
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    terminal_enclosure_archive_paths,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

SUFFIX = ".cleanup-complete.json"
"""The record sits beside the terminal archive of the same publication request."""

SCHEMA = "terminal-cleanup-completion/1.0"


def completion_record_path(coordination_root: Path, publication_request_id: str) -> Path:
    """Where one generation's completion record lives, given its publication request."""

    archive, _receipt = terminal_enclosure_archive_paths(coordination_root, publication_request_id)
    return archive.with_suffix(SUFFIX)


def write_completion_record(
    contract: WorktreeContract,
    completed: WorktreeContract,
    *,
    operation: TerminalCleanupOperation,
) -> str:
    """Record that ``completed`` is the finished form of ``contract``; return its path.

    Written by the operation that completed, after its contract is published. A generation whose
    address no longer carries a terminal locator has nothing to record: it never reached the
    progression the record attests to.
    """

    try:
        observed = inspect_lifecycle_operation_locator(
            contract.coordination_root,
            contract.contract_path,
        )
    except Exception:
        return ""
    locator = observed.locator
    if locator is None:
        return ""
    record = outside_scratch.recorded_root(completed)
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "state": "cleanup-completed",
        "publicationRequestId": locator.publicationRequestId,
        "cleanupOperation": operation,
        "contractPath": contract.contract_path.resolve(strict=False).as_posix(),
        "outsideScratch": (
            {
                "path": record.root.as_posix(),
                "generation": record.generation,
                "markerSha256": record.marker_sha256,
            }
            if record is not None
            else None
        ),
    }
    path = completion_record_path(contract.coordination_root, locator.publicationRequestId)
    try:
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        return ""
    return path.as_posix()


def read_completion_record(
    coordination_root: Path,
    publication_request_id: str,
) -> dict[str, Any] | None:
    """The completion record for one publication request, or ``None`` when there is none."""

    path = completion_record_path(coordination_root, publication_request_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schemaVersion") != SCHEMA or payload.get("state") != "cleanup-completed":
        return None
    if payload.get("publicationRequestId") != publication_request_id:
        return None
    return payload


def completion_agrees(
    record: dict[str, Any],
    *,
    publication_request_id: str,
    operation: str,
    contract_path: str,
) -> bool:
    """Whether one record attests exactly this generation's completion."""

    return (
        record.get("publicationRequestId") == publication_request_id
        and record.get("cleanupOperation") == operation
        and record.get("contractPath") == contract_path
    )
