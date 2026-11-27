"""Enroll limited holds and deliver due recipients through the ordinary ordered claim."""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from agents_remember.cli.held_reset_adoption import adopt_held_resets
from agents_remember.cli.role_held_messages import apply_drain_to_receipt, drain_held
from agents_remember.cli.role_launch_receipts import _read_receipt
from agents_remember.controlplane.held_reset_index import HeldResetIndex, ResetTarget
from agents_remember.controlplane.held_store_layout import (
    _read_store_file,
    _store_endpoints,
    hold_message,
    note_held_receipt,
    store_path_for,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.serving.held_reset_scheduler import ResetAttempt, ResetDeliveryPorts

if TYPE_CHECKING:
    from agents_remember.controlplane.held_reset_index import ResetEntry
    from agents_remember.controlplane.held_store_layout import HeldMessage
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

logger = logging.getLogger(__name__)


def hold_at_receipt(
    config: McpRuntimeConfig,
    receipt_path: Path,
    workspace_path: str | Path,
    agent_id: str,
    message: HeldMessage,
) -> dict[str, Any]:
    """Take a native hold, publishing a known reset before putting its row on disk."""

    def schedule(until: str) -> None:
        target = ResetTarget(
            agent_id, receipt_path, Path(workspace_path), datetime.fromisoformat(until)
        )
        HeldResetIndex(config.coordination_root).schedule(target)

    taken = hold_message(workspace_path, agent_id, message, before_commit=schedule)
    note_held_receipt(config, receipt_path)
    return taken


def drain_reset_entry(config: McpRuntimeConfig, entry: ResetEntry) -> ResetAttempt:
    """Try the indexed receipt once, with the same checks and acknowledgement as other drains."""
    target = entry.target
    try:
        receipt = _read_receipt(target.receipt_path)
    except HTTPException:
        logger.exception("Could not read the reset receipt for agent %s", target.agent_id)
        return ResetAttempt(attempted=False)
    if receipt is None or _store_endpoints(receipt) != (
        str(target.workspace_path),
        target.agent_id,
    ):
        return ResetAttempt(attempted=False, gone=True)
    try:
        outcome = drain_held(config, target.receipt_path, receipt)
        if outcome.get("outcome") in {
            "delivered",
            "opened",
            "recipient-gone",
            "seat-lost",
            "partially-delivered",
        }:
            apply_drain_to_receipt(target.receipt_path, outcome)
    except (OSError, ValueError, HTTPException):
        logger.exception(
            "Could not deliver waiting messages for agent %s at reset", target.agent_id
        )
        return ResetAttempt(attempted=True)
    return ResetAttempt(attempted=True, delivered=outcome.get("outcome") in {"delivered", "opened"})


def finish_reset_entry(
    index: HeldResetIndex, entry: ResetEntry, result: ResetAttempt, retry_at: datetime
) -> None:
    """Reclaim only a proved empty wait, in the same exclusion as a new admission."""
    target = entry.target
    if result.gone:
        index.finish(entry, None)
        return
    path = store_path_for(target.workspace_path, target.agent_id)
    try:
        with exclusive_file_lock(path, "held-message store"):
            store = _read_store_file(path, target.agent_id)
            pending = store["messages"] or store.get("pendingLines")
            index.finish(entry, retry_at if pending else None)
    except (OSError, ValueError):
        logger.exception("Could not confirm waiting messages for agent %s", target.agent_id)
        index.finish(entry, retry_at)


def _attempt(
    config: McpRuntimeConfig, index: HeldResetIndex, entry: ResetEntry, retry_at: datetime
) -> ResetAttempt:
    result = drain_reset_entry(config, entry)
    finish_reset_entry(index, entry, result, retry_at)
    return result


def reset_delivery_ports(config: McpRuntimeConfig) -> ResetDeliveryPorts:
    return ResetDeliveryPorts(partial(adopt_held_resets, config), partial(_attempt, config))
