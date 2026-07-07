"""Validate leaf refs at server write boundaries before persistence."""

from __future__ import annotations

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.worktrees.leaf_refs import resolve_leaf_ref


def repo_scope_for_leaf_key(config: McpRuntimeConfig, leaf_key: str) -> str | None:
    """Use a configured repo only for unqualified refs; qualified refs carry their repo."""

    if len([part for part in leaf_key.split("/") if part]) == 3:
        return None
    return next(iter(config.repositories)) if len(config.repositories) == 1 else None


def resolve_catalog_leaf_key(config: McpRuntimeConfig, leaf_key: str) -> str:
    """Normalize a terminal catalog leaf key to ``repo/master/doc-id``."""

    return resolve_leaf_ref(
        config.coordination_root,
        repo_scope_for_leaf_key(config, leaf_key),
        leaf_key,
    ).qualified_id
