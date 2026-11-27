"""Reclaim the old shared held-message files after their writers have restarted."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

from agents_remember.cli.paseo_role_recipients import _line_receipts, is_minted_agent_id
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_held_store import (
    held_seat_ref,
    preview_held_store_reconciliation,
    receipt_is_closed,
    reconcile_held_store,
)
from agents_remember.cli.role_launch_receipts import is_deferred_held_receipt
from agents_remember.controlplane.held_store_layout import product_held_dir
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel
from agents_remember.models.core import RunningServerPayload
from agents_remember.serving.build_info import process_serving_build


def reconcile_shared_held_stores(
    config: McpRuntimeConfig,
    *,
    dry_run: bool,
    servers: list[RunningServerPayload],
) -> dict[str, Any]:
    """Reconcile each exact shared store owner once, including an obsolete lock by itself."""
    workspace = config.workspace_root
    directory = product_held_dir(workspace)
    if not dry_run:
        refusal = _older_writer_refusal(servers)
        if refusal is not None:
            return {"state": "blocked", "path": str(directory), "reason": refusal}
    try:
        agent_ids = _shared_agent_ids(workspace, directory)
    except (OSError, AuthorityError) as error:
        return {"state": "blocked", "path": str(directory), "reason": str(error)}
    owners = _shared_owners(config, agent_ids) if agent_ids else {}
    rows = [
        _reconcile_agent(config, agent_id, owners.get(agent_id, ()), dry_run=dry_run)
        for agent_id in sorted(agent_ids)
    ]
    return {
        "state": "previewed" if dry_run else "reconciled",
        "path": str(directory),
        "stores": rows,
        "wouldRemoveStores" if dry_run else "removedStores": sum(
            row.get("store", False) for row in rows
        ),
        "wouldRemoveLocks" if dry_run else "removedLocks": sum(
            row.get("lock", False) for row in rows
        ),
        "preserved": sum(row["state"] == "preserved" for row in rows),
    }


def _older_writer_refusal(servers: list[RunningServerPayload]) -> str | None:
    digest = process_serving_build().source_digest
    if not digest:
        return "The serving build digest is unknown. Reconnect a current server before retrying."
    older = [
        f"{server.role} PID {server.pid}" for server in servers if server.loadedDigest != digest
    ]
    if older:
        return (
            f"Older or unverified writers remain: {', '.join(older)}. "
            "Restart or retire them before reconciling shared held-store locks."
        )
    return None


def _shared_agent_ids(workspace: Path, directory: Path) -> set[str]:
    try:
        confine_non_symlink_rel(workspace, directory.relative_to(workspace).as_posix())
    except FileNotFoundError:
        return set()
    return {agent_id for path in directory.iterdir() if (agent_id := _shared_agent_id(path.name))}


def _shared_agent_id(name: str) -> str | None:
    filename = name.removesuffix(".lock")
    if not filename.endswith(".json"):
        return None
    agent_id = filename.removesuffix(".json")
    return agent_id if is_minted_agent_id(agent_id) else None


type Receipt = tuple[Path, dict[str, Any]]


def _shared_owners(config: McpRuntimeConfig, agent_ids: set[str]) -> dict[str, list[Receipt]]:
    owners: dict[str, list[Receipt]] = {}
    for path, receipt in _receipts_in_workspace(config):
        agent_id = receipt.get("agentId")
        if isinstance(agent_id, str) and agent_id in agent_ids:
            owners.setdefault(agent_id, []).append((path, receipt))
    return owners


def _receipts_in_workspace(config: McpRuntimeConfig) -> Iterator[Receipt]:
    for path, receipt in _line_receipts(config):
        workspace = receipt.get("workspace")
        folder = workspace.get("path") if isinstance(workspace, dict) else None
        if isinstance(folder, str) and Path(folder) == config.workspace_root:
            yield path, receipt


def _reconcile_agent(
    config: McpRuntimeConfig,
    agent_id: str,
    owners: Sequence[Receipt],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if len(owners) != 1:
        reason = (
            "Matching launch records disagree." if owners else "No readable launch record matched."
        )
        return {"agentId": agent_id, "state": "preserved", "reason": reason}
    path, receipt = owners[0]
    if not receipt_is_closed(receipt) and is_deferred_held_receipt(receipt):
        return {
            "agentId": agent_id,
            "state": "preserved",
            "reason": "The held seat has not opened yet.",
        }
    try:
        ref = held_seat_ref(path, receipt)
        host_state = None if receipt_is_closed(receipt) else _host_closure(config, agent_id)
        operation = preview_held_store_reconciliation if dry_run else reconcile_held_store
        outcome = operation(ref, host_state=host_state)
    except (OSError, ValueError, HTTPException) as error:
        reason = str(error.detail) if isinstance(error, HTTPException) else str(error)
        return {"agentId": agent_id, "state": "preserved", "reason": reason}
    return {
        "agentId": agent_id,
        "state": "would-settle" if dry_run else "settled",
        "store": outcome.store_removed,
        "lock": outcome.lock_removed,
        "waitingMessages": outcome.message_count,
    }


def _host_closure(config: McpRuntimeConfig, agent_id: str) -> Literal["archived", "gone"]:
    reading = read_agent(config, agent_id)
    if not reading.reachable:
        raise AuthorityError(
            reading.unreachable_reason or "The host could not confirm that the seat closed."
        )
    if reading.archived:
        return "archived"
    if not reading.found:
        return "gone"
    raise AuthorityError("The host still has this seat.")
