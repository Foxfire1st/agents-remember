"""Agents Remember MCP server package."""

from importlib.metadata import PackageNotFoundError, version

SERVER_NAME = "agents-remember"

try:
    # Single source of truth: the installed package metadata (mcp/pyproject.toml).
    SERVER_VERSION = version("agents-remember-mcp")
except PackageNotFoundError:  # running from a source checkout without an install
    SERVER_VERSION = "2.9.0"
