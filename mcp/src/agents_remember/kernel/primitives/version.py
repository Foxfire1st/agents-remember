"""Installed package identity (moved from the mcp package root).

Every layer above kernel may name the server/version without importing the mcp
package.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

SERVER_NAME = "agents-remember"


def _resolve_server_version() -> str:
    """Return installed metadata, or the release identity for a source checkout."""
    try:
        # Single source of truth: the installed package metadata (mcp/pyproject.toml).
        return version("agents-remember-mcp")
    except PackageNotFoundError:  # running from a source checkout without an install
        return "3.0.0rc7"


SERVER_VERSION = _resolve_server_version()
