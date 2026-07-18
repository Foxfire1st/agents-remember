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


class RouteIndexCensusError(AgentsRememberError):
    """A validated repository could not provide one authoritative source census."""


class HarnessControlError(AgentsRememberError):
    """The hosted harness control contract or exact-session identity was violated."""


class HarnessControlClientError(HarnessControlError):
    """The serving process lost an exact-session IPC request before or after its first byte.

    ``may_have_sent`` is retry-safety evidence: callers may retry a pre-write failure, while a
    post-write failure must remain ``unknown`` until the same request id is reconciled.
    """

    def __init__(self, detail: str, *, may_have_sent: bool) -> None:
        super().__init__(detail)
        self.may_have_sent = may_have_sent


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


class HarnessAdapterBusyError(HarnessControlError):
    """An adapter's final write-boundary guard proved that zero operation bytes were sent.

    The common submission authority may move ``dispatching`` back to ``queued`` only for this
    typed error.  A generic exception cannot safely make that claim because vendor bytes might
    already have crossed the transport.
    """

    def __init__(self, detail: str, *, may_have_sent: bool = False) -> None:
        if may_have_sent:
            raise ValueError("HarnessAdapterBusyError must certify may_have_sent=False")
        super().__init__(detail)
        self.may_have_sent = False


class HarnessRequestConflictError(HarnessControlError):
    """A retained request id was reused with a different immutable source or payload."""


class HarnessBridgeEpochMismatchError(HarnessControlError):
    """A caller addressed lifecycle state from a replaced hosted runner generation."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"submission authority epoch changed (expected {expected!r}, actual {actual!r})"
        )
        self.expected = expected
        self.actual = actual


class CodexAppServerError(HarnessControlError):
    """The negotiated Codex app-server protocol or its configured contract was violated."""


class CodexAppServerRpcError(CodexAppServerError):
    """A correlated Codex JSON-RPC request returned an error response."""

    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"Codex app-server {method} failed ({code}): {message}")
        self.method = method
        self.code = code
