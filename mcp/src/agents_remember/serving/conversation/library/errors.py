"""Typed errors for the native conversation library leaf (260718-CHATS-L2).

Every error subclasses the :mod:`agents_remember.errors` family so existing
``except ValueError`` handlers keep working, and the library routes map each
member to one precise HTTP status (the reviewer O4 obligation: no raw 500 for
routine refusals). The leaf keeps its own module instead of editing the shared
``errors.py`` so the parallel L1 leaf never collides with this file.
"""

from __future__ import annotations

from agents_remember.errors import AgentsRememberError, AuthorityError


class ConversationLibraryError(AgentsRememberError):
    """Base class for native conversation library failures."""


class UnknownLibraryHarnessError(ConversationLibraryError):
    """The path harness id is not one of the normalized native harnesses."""


class LibraryScopeError(AuthorityError):
    """A requested cwd escaped the caller's canonical allowed project scope."""


class InvalidLibraryCursorError(ConversationLibraryError):
    """A list/read cursor or conversation key failed purpose, signature, or shape checks."""


class CatalogGenerationError(ConversationLibraryError):
    """A cursor/key names a native catalog generation that no longer matches."""


class StaleNativeIdentityError(ConversationLibraryError):
    """The expected native identity digest no longer matches the native store."""


class UnknownNativeConversationError(ConversationLibraryError):
    """A validly keyed native conversation is absent from the native store."""


class LibraryCapabilityError(ConversationLibraryError):
    """A harness library capability is disabled (unavailable or unverified).

    Carries the exact capability state so the route can render fail-closed copy;
    the feature stays visible and never claims invented parity.
    """

    def __init__(self, detail: str, *, capability_state: str) -> None:
        super().__init__(detail)
        self.capability_state = capability_state


class LibraryStoreError(ConversationLibraryError):
    """The native store or locked helper could not serve an authorized request."""


class OpenRequestConflictError(ConversationLibraryError):
    """An open requestId was retained with a different immutable fingerprint."""


class UnknownOpenRequestError(ConversationLibraryError):
    """No open operation exists for the caller's requestId (and none is derivable)."""


class OpenLedgerFullError(ConversationLibraryError):
    """The bounded open ledger is full of live operations; retry after settlement."""


__all__ = [
    "CatalogGenerationError",
    "ConversationLibraryError",
    "InvalidLibraryCursorError",
    "LibraryCapabilityError",
    "LibraryScopeError",
    "LibraryStoreError",
    "OpenLedgerFullError",
    "OpenRequestConflictError",
    "StaleNativeIdentityError",
    "UnknownLibraryHarnessError",
    "UnknownNativeConversationError",
    "UnknownOpenRequestError",
]
