"""Shared protocol adapter primitives for Agents Remember MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.application.tool_response import complete_tool_response

# Not defined here: the roster is advertised wire vocabulary and its one definition lives
# in the `models` package, so a response model can read it without this adapter package
# being imported from below (see `models/tools/public_roster.py`). `__all__` below is what
# declares the re-export, because this is not an `__init__.py` (where ruff exempts the
# `X as X` idiom) and a bare import would read as unused.
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

TRANSPORT = "stdio"
RESERVED_TOOLS: tuple[str, ...] = ()

__all__ = ["PUBLIC_TOOLS", "RESERVED_TOOLS", "TRANSPORT"]


def _tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Convert one application result into its protocol-ready response."""
    return complete_tool_response(tool_name, payload)
