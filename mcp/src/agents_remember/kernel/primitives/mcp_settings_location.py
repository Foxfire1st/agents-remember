"""The tool-server authority file beside the user's role settings."""

from pathlib import Path

from agents_remember.kernel.primitives.user_settings_location import user_settings_path


def mcp_settings_path() -> Path:
    """Use the user's existing config directory for the separate MCP authority."""
    return user_settings_path().with_name("agents-remember-settings.json")
