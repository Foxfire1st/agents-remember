"""Repository and coordination-root authority guards shared by upper layers.

These are configuration-boundary checks, not application use cases. Keeping the
resolve-and-confine rules in the kernel gives serving and application one real
lower owner for the security decision.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.errors import AuthorityError
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope, path_is_relative_to


def require_repo(config: McpRuntimeConfig, repo_id: str) -> RepositoryScope:
    """Return the repository scope for ``repo_id`` or raise :class:`AuthorityError`."""
    try:
        return config.repositories[repo_id]
    except KeyError as error:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise AuthorityError(
            f"repo_id {repo_id!r} is not allowed by MCP settings; allowed: {allowed}"
        ) from error


def require_within_coordination(config: McpRuntimeConfig, value: str, label: str) -> Path:
    """Resolve ``value`` and confine it to the coordinator root."""
    path = Path(value)
    if not path.is_absolute():
        path = config.coordination_root / path
    path = path.resolve()
    if not path_is_relative_to(path, config.coordination_root):
        raise AuthorityError(f"{label} must stay inside coordination_root")
    return path
