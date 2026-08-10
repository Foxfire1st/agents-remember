"""Installed package identity (moved from the mcp package root).

Every layer above kernel may name the server/version without importing the mcp
package.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

SERVER_NAME = "agents-remember"

try:
    # Single source of truth: the installed package metadata (mcp/pyproject.toml).
    SERVER_VERSION = version("agents-remember-mcp")
except PackageNotFoundError:  # running from a source checkout without an install
    SERVER_VERSION = "3.0.0rc6"
