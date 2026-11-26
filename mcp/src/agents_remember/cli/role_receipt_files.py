"""Atomic creation and history publication for launch receipt files."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.cli.role_held_store import (
    HeldSeatRef,
    HostClosure,
    held_seat_ref,
    leaf_workspace_allows_creation,
    receipt_is_closed,
    settle_held_store,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.models.role_identity import LEAF_ROLES
from agents_remember.worktrees.role_reports import close_execution_role_folders

RECEIPT_SCHEMA = "ar-role-execution/v1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_receipt(path: Path) -> dict[str, Any] | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:
        raise HTTPException(
            status_code=409,
            detail="The saved role execution receipt cannot be inspected; reconcile it before launching.",
        ) from error
    if not stat.S_ISREG(mode):
        raise HTTPException(
            status_code=409,
            detail="The role execution receipt is not a regular file; refusing a new launch.",
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Start can archive the receipt between inspection and an unlocked snapshot read.
        return None
    except (OSError, ValueError, RecursionError) as error:
        # Bytes that are not UTF-8 or not JSON, a number too long to convert (all ValueError),
        # and a nesting beyond the parser's depth are one answer: the record cannot be read.
        raise HTTPException(
            status_code=409,
            detail="The role execution receipt is unreadable; reconcile it before launching.",
        ) from error
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise HTTPException(
            status_code=409,
            detail="The role execution receipt is malformed; reconcile it before launching.",
        )
    return receipt


def read_enumerated_receipt(path: Path) -> dict[str, Any] | None:
    """One file met while enumerating a report folder that also holds other JSON documents.

    Message attachments and other JSON documents are filed under the same report folders as
    launch receipts, so an enumeration cannot assume every ``*.json`` there is one. A file whose
    text parses as JSON but is not an object carrying this line's receipt schema is not a launch
    record and answers ``None``. A file that cannot be read at all keeps the ordinary refusal: it
    may be a receipt that was written part-way, and no seat may be retired over a guess.
    """

    try:
        return _read_receipt(path)
    except HTTPException:
        if _names_another_document(path):
            return None
        raise


def _names_another_document(path: Path) -> bool:
    """Whether the file's JSON is something other than a receipt of this line."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return False
    return not (isinstance(value, dict) and value.get("schema") == RECEIPT_SCHEMA)


def _replace_receipt_bytes(path: Path, receipt: dict[str, Any]) -> None:
    """Replace the current record atomically while its receipt lock is held."""
    temporary = _write_receipt_aside(path, receipt)
    try:
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _create_receipt(path: Path, receipt: dict[str, Any]) -> bool:
    """Create a receipt only if none exists at its address; say whether this call created it.

    The complete file appears under its name in one step, and that step fails when the name is
    taken. Of two processes that launch the same selection at the same moment, one creates the
    receipt and the other is told that it did not.
    """

    with exclusive_file_lock(path, "role execution receipt"):
        return _install_initial_receipt(path, receipt)


def _install_initial_receipt(path: Path, receipt: dict[str, Any]) -> bool:
    """Install one immutable initial receipt by name, with no overwrite."""
    temporary = _write_receipt_aside(path, receipt)
    try:
        os.link(temporary, path, follow_symlinks=False)
    except FileExistsError:
        return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _leaf_before_create(path: Path, receipt: dict[str, Any], *, opening_held: bool = False) -> bool:
    from agents_remember.cli.role_launch_receipts import (  # noqa: PLC0415
        _same_request_receipt,
        _write_receipt,
    )

    with exclusive_file_lock(path, "role execution receipt"):
        current = _same_request_receipt(path, receipt)
        if receipt_is_closed(current) or not leaf_workspace_allows_creation(current):
            return False
        current["heldOpenPending" if opening_held else "leafCreateEntered"] = True
        _write_receipt(path, current)
        return True


def _require_live_launch_scope(receipt: dict[str, Any]) -> None:
    if receipt.get("role") in LEAF_ROLES and not leaf_workspace_allows_creation(receipt):
        raise HTTPException(
            status_code=409, detail="The saved launch belongs to a terminal enclosure."
        )


def _write_receipt_aside(path: Path, receipt: dict[str, Any]) -> Path:
    """Write the receipt to a synced temporary file beside its address."""

    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Path(temporary)


def _archive_receipt(
    path: Path, receipt: dict[str, Any], *, host_state: HostClosure | None = None
) -> None:
    from agents_remember.cli.role_launch_receipts import _same_request_receipt  # noqa: PLC0415

    with exclusive_file_lock(path, "role execution receipt"):
        current = _same_request_receipt(path, receipt)
        if isinstance(current.get("agentId"), str) and (
            receipt_is_closed(current) or host_state is not None
        ):
            settle_held_store(held_seat_ref(path, receipt), host_state=host_state)
        _move_receipt_to_history(path, _same_request_receipt(path, current))


def _predecessor_held_ref(
    path: Path, receipt: dict[str, Any], launch_call: dict[str, Any]
) -> HeldSeatRef | None:
    """Bind the history receipt before the launch call archives its exact agent."""
    predecessor = receipt.get("predecessorReports")
    request_id = predecessor.get("requestId") if isinstance(predecessor, dict) else None
    agent_id = launch_call.get("archiveAgentId")
    if not isinstance(agent_id, str) or not isinstance(request_id, str) or not request_id:
        return None
    if Path(request_id).name != request_id:
        return None
    history = path.parent / "history" / f"{request_id}.json"
    prior = _read_receipt(history)
    if prior is not None and (prior.get("requestId"), prior.get("agentId")) == (
        request_id,
        agent_id,
    ):
        return held_seat_ref(history, prior)
    return None


def _move_receipt_to_history(path: Path, receipt: dict[str, Any]) -> None:
    from agents_remember.cli.role_launch_receipts import _write_receipt  # noqa: PLC0415

    request_id = receipt.get("requestId")
    if not isinstance(request_id, str):
        raise ValueError(
            "The prior role execution receipt has no request identity and cannot be archived safely."
        )
    predecessor = receipt.get("predecessorReports")
    if receipt.get("predecessorSettled") and isinstance(predecessor, dict):
        close_execution_role_folders(predecessor)
    history = path.parent / "history" / f"{request_id}.json"
    if history.exists():
        raise ValueError("A prior role execution archive already has this request identity.")
    history.parent.mkdir(parents=True, exist_ok=True)
    if "reportCollectionFiles" in receipt:
        # History preserves the collected evidence/truth, not an inactive reuse cache.
        receipt.pop("reportCollectionFiles")
        _write_receipt(path, receipt)
    path.replace(history)
