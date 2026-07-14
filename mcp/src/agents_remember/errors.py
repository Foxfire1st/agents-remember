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


class HarnessControlError(AgentsRememberError):
    """The hosted harness control contract or exact-session identity was violated."""


class HarnessAdapterDisconnectedError(HarnessControlError):
    """A protocol adapter disconnected before or after a prompt might have been sent."""

    def __init__(
        self,
        detail: str,
        *,
        may_have_sent: bool,
        vendor_correlation_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.may_have_sent = may_have_sent
        self.vendor_correlation_id = vendor_correlation_id


class CodexAppServerError(HarnessControlError):
    """The pinned Codex app-server protocol or its configured contract was violated."""


class CodexAppServerRpcError(CodexAppServerError):
    """A correlated Codex JSON-RPC request returned an error response."""

    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"Codex app-server {method} failed ({code}): {message}")
        self.method = method
        self.code = code
