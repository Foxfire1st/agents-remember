"""Typed error family for Agents Remember.

Every domain error subclasses :class:`AgentsRememberError`, which itself
subclasses :class:`ValueError` so existing ``except ValueError`` handlers and
the FastMCP error surface keep working unchanged. New code should raise (and
catch) the typed members of this family rather than bare ``ValueError`` /
``RuntimeError`` so the public surface has one coherent error contract.
"""

from __future__ import annotations


class AgentsRememberError(ValueError):
    """Base class for all Agents Remember domain errors."""


class AuthorityError(AgentsRememberError):
    """A path or repo argument violated the MCP authority settings.

    Raised when a caller names a repo that settings do not allow, or passes a
    path that escapes the coordinator root. Centralizing this means every
    controller reports the same boundary violation the same way.
    """
