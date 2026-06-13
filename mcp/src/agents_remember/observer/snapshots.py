"""Structural file-surface readers for the projection (data surfaces 1, 5, 6).

3a reads only the surfaces the *named* state tree needs: provider current-state
(surface 1) and worktree enclosures (the contract, surface 6, plus the group
layout, surface 5). They reuse the existing producers' readers rather than
re-parsing -- ``providers.current_state`` and ``worktrees.worktree_contract``.
The analytical surfaces (ledger, drift, sidecars, route indexes, tool reports,
setup, tasks) join here in slice 3b.

These functions do the file I/O at the projection's call edge; the fold itself
(:mod:`agents_remember.observer.reducer`) stays pure.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.observer.projection import EnclosureNode, ProviderNode
from agents_remember.observer.timeutil import age_seconds
from agents_remember.providers.current_state import current_state_path
from agents_remember.worktrees.worktree_contract import ContractError, load_contract


def read_providers(config: McpRuntimeConfig, *, now: datetime) -> list[ProviderNode]:
    """Surface 1: the persisted provider current-state snapshot.

    Provider state is call-triggered and stale between calls, so the snapshot's
    age (``snapshotStaleSeconds``) is surfaced rather than pretending it is live.
    """
    payload = _read_json(current_state_path(config))
    if payload is None:
        return []
    checked_at = payload.get("checkedAt")
    stale = age_seconds(checked_at, now) if isinstance(checked_at, str) else None
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return []
    nodes: list[ProviderNode] = []
    for key, value in providers.items():
        if not isinstance(value, dict):
            continue
        ok = value.get("ok")
        nodes.append(
            ProviderNode(
                id=str(value.get("id", key)),
                state=str(value.get("state", "unknown")),
                ok=ok if isinstance(ok, bool) else None,
                watcherUp=bool(value.get("watcherUp", False)),
                indexingState=str(value.get("indexingState", "unknown")),
                snapshotStaleSeconds=stale,
            )
        )
    return nodes


def read_enclosures(coordination_root: Path) -> list[EnclosureNode]:
    """Surfaces 5/6: every worktree contract under ``tasks/<repo>/<task>/``.

    The contract lives in the durable task folder (design §1.1), so it outlives
    worktree cleanup -- an enclosure stays in the projection as the kanban record
    even after its worktree is reclaimed. A malformed contract is skipped, never
    fatal to the whole projection.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    nodes: list[EnclosureNode] = []
    for path in sorted(tasks_root.glob("*/*/contract.md")):
        node = _enclosure_from_contract(path)
        if node is not None:
            nodes.append(node)
    return nodes


def _enclosure_from_contract(path: Path) -> EnclosureNode | None:
    try:
        contract = load_contract(path)
    except (ContractError, OSError):
        return None
    return EnclosureNode(
        enclosure=contract.contract_path.as_posix(),
        taskId=contract.task_id,
        taskName=contract.task_name,
        repoName=contract.repo_name,
        lifecycleId=contract.lifecycle_id,
        worktreeGroup=contract.worktree_group.as_posix(),
        humanReviewStatus=contract.human_review_status,
        closeoutStatus=contract.closeout_status,
        integrationStatus=contract.integration_status,
        cleanup=contract.cleanup,
    )


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
