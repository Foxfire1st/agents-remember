"""Validate leaf refs at server write boundaries before persistence."""

from __future__ import annotations

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.serving.seat_binding import role_suffixed_leaf_base
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError, resolve_leaf_ref


def repo_scope_for_leaf_key(config: McpRuntimeConfig, leaf_key: str) -> str | None:
    """Use a configured repo only for unqualified refs; qualified refs carry their repo."""

    if len([part for part in leaf_key.split("/") if part]) == 3:
        return None
    return next(iter(config.repositories)) if len(config.repositories) == 1 else None


def resolve_catalog_leaf_key(config: McpRuntimeConfig, leaf_key: str) -> str:
    """Normalize a terminal catalog leaf key to ``repo/master/doc-id``."""

    repo_scope = repo_scope_for_leaf_key(config, leaf_key)
    try:
        return resolve_leaf_ref(config.coordination_root, repo_scope, leaf_key).qualified_id
    except LeafRefResolutionError as original:
        suffixed = role_suffixed_leaf_base(leaf_key)
        if suffixed is None:
            raise
        base, role = suffixed
        try:
            canonical = resolve_leaf_ref(
                config.coordination_root,
                repo_scope_for_leaf_key(config, base),
                base,
            ).qualified_id
        except LeafRefResolutionError:
            raise original from None
        raise LeafRefResolutionError(
            leaf_key,
            repo_name=repo_scope,
            reason="role-suffixed",
            candidates=(canonical,),
            guidance=(
                f"role-suffixed leaf refs are unsupported; use leaf_key={canonical!r} "
                f"with role={role!r}"
            ),
        ) from original
