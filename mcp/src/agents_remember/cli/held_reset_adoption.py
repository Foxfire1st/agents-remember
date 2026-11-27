"""Enroll saved, provider-limited waits at dashboard startup."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from agents_remember.cli.role_launch_receipts import _read_receipt
from agents_remember.controlplane.held_reset_index import ResetTarget
from agents_remember.controlplane.held_store_discovery import ReceiptWalk, receipt_roots
from agents_remember.controlplane.held_store_layout import _store_endpoints, read_waiting_rows

if TYPE_CHECKING:
    from agents_remember.controlplane.held_reset_index import HeldResetIndex
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

_ADOPTION_READS = 32


def _existing_wait(path: Path) -> ResetTarget | None:
    try:
        receipt = _read_receipt(path)
    except HTTPException:
        return None
    endpoints = _store_endpoints(receipt) if receipt is not None else None
    if endpoints is None:
        return None
    folder, agent = endpoints
    rows, complete = read_waiting_rows(folder, agent)
    if not complete:
        return None
    resets = [
        datetime.fromisoformat(row["limitedUntil"])
        for row in rows
        if row["limitedUntil"] is not None
    ]
    if not resets:
        return None
    return ResetTarget(agent, path, Path(folder), min(resets))


def adopt_held_resets(config: McpRuntimeConfig, index: HeldResetIndex) -> None:
    """Walk live receipts once on startup. Existing index generations win."""
    for root in receipt_roots(config.coordination_root):
        walk = ReceiptWalk(root)
        try:
            while not walk.done:
                index.enroll(
                    [
                        target
                        for key in walk.take(_ADOPTION_READS)
                        if (target := _existing_wait(Path(key))) is not None
                    ]
                )
        finally:
            walk.close()
