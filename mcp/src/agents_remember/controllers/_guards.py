"""Shared authority guards for MCP controllers.

These collapse the resolve-and-confine checks that every controller used to
copy verbatim. Keeping them in one place means the security boundary is
written, reviewed, and tested once -- "the copy you forget to update is the
vuln."
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
    """Resolve ``value`` and confine it to the coordinator root.

    Relative inputs resolve against ``coordination_root``. Raises
    :class:`AuthorityError` if the resolved path escapes that root.
    """
    path = Path(value)
    if not path.is_absolute():
        path = config.coordination_root / path
    path = path.resolve()
    if not path_is_relative_to(path, config.coordination_root):
        raise AuthorityError(f"{label} must stay inside coordination_root")
    return path
