"""Authorized native library list/read orchestration (260718-CHATS-L2).

The service is the one boundary where every list/read re-authorizes: the caller's
server-resolved binding is checked against the token-bound scope, the canonical project scope is
re-derived narrow-only, the identity digest is recomputed from native identity, and the
harness's live production gate must report the feature ``supported`` before any native store is
touched. Cursors and keys are never authorization by themselves (design sections 6.8 and 9.3).

The port builder is injected by the dormant resolver factory so this module never imports the
factory (no import cycle); tests may inject their own builder for the same seam.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from agents_remember.errors import AuthorityError
from agents_remember.serving.conversation.library.errors import (
    InvalidLibraryCursorError,
    LibraryCapabilityError,
    StaleNativeIdentityError,
)
from agents_remember.serving.conversation.library.scope import canonical_library_scope
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    ConversationLibraryPage,
    ConversationLibraryScope,
    HarnessId,
    HistoricalConversationPage,
    LibraryConversationKey,
    LibraryKeyBinding,
    LibraryListCursor,
    LibraryReadCursor,
    NativeConversationRef,
    NativeResumeTarget,
)
from agents_remember.serving.conversation.runtime import ConversationRuntime

if TYPE_CHECKING:
    from agents_remember.serving.conversation.library.factories import LibraryShared


class LibraryPort(Protocol):
    """The dormant per-harness resolver shape this service orchestrates."""

    harness_id: HarnessId

    async def list(
        self,
        scope: ConversationLibraryScope,
        *,
        cursor: LibraryListCursor | None,
        limit: int,
    ) -> ConversationLibraryPage: ...

    async def read(
        self,
        ref: NativeConversationRef,
        *,
        before: LibraryReadCursor | None,
        limit: int,
    ) -> HistoricalConversationPage: ...

    async def resolve_resume_target(self, ref: NativeConversationRef) -> NativeResumeTarget: ...


PortBuilder = Callable[[str], LibraryPort]


class ConversationLibraryService:
    """List/read authorization + capability orchestration for the dormant library."""

    def __init__(
        self,
        *,
        runtime: ConversationRuntime,
        shared: LibraryShared,
        authorization: AuthorizationBinding,
        port_builder: PortBuilder,
    ) -> None:
        self._runtime = runtime
        self._shared = shared
        self._authorization = authorization
        self._port_builder = port_builder

    @property
    def shared(self) -> LibraryShared:
        return self._shared

    async def list(
        self,
        harness_id: HarnessId,
        requested_cwd: str | None,
        *,
        cursor: LibraryListCursor | None,
        limit: int,
    ) -> ConversationLibraryPage:
        scope = canonical_library_scope(
            self._authorization,
            harness_id,
            requested_cwd,
            workspace_root=self._runtime.scope.workspace_root,
        )
        capabilities = await self._shared.gates.history_capabilities(harness_id)
        feature = capabilities.list
        if feature.state != "supported":
            raise LibraryCapabilityError(feature.reason, capability_state=feature.state)
        port = self._port_builder(harness_id)
        if port.harness_id != harness_id:
            raise InvalidLibraryCursorError("resolver factory returned the wrong harness port")
        return await port.list(scope, cursor=cursor, limit=limit)

    def resolve_key(
        self,
        harness_id: HarnessId,
        key_token: str,
    ) -> tuple[ConversationLibraryScope, NativeConversationRef, LibraryKeyBinding]:
        """Re-authorize one opaque conversation key into its exact native ref.

        Fails closed on: malformed/foreign keys, cross-principal bindings, wrong harness, and a
        recomputed identity digest that no longer matches the key (stale row).
        """

        try:
            key = LibraryConversationKey(key_token)
        except ValueError as exc:
            raise InvalidLibraryCursorError(f"conversation key is malformed: {exc}") from exc
        binding, vendor = self._shared.cursor_authority.verify_conversation_key(key)
        scope = binding.scope
        if scope.authorization != self._authorization:
            raise AuthorityError(
                "library conversation key does not name the caller's operator/workspace"
            )
        if scope.harness_id != harness_id:
            raise InvalidLibraryCursorError(
                "library conversation key does not belong to this harness"
            )
        digest = self._shared.cursor_authority.identity_digest(
            harness_id, vendor, scope.canonical_project_scope
        )
        if digest != binding.identity_digest:
            raise StaleNativeIdentityError(
                "the native conversation identity changed; refresh the library row"
            )
        ref = NativeConversationRef(
            harness_id=harness_id,
            vendor_conversation_id=vendor,
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        return scope, ref, binding

    async def read(
        self,
        harness_id: HarnessId,
        key_token: str,
        *,
        before: LibraryReadCursor | None,
        limit: int,
    ) -> HistoricalConversationPage:
        _scope, ref, _binding = self.resolve_key(harness_id, key_token)
        capabilities = await self._shared.gates.history_capabilities(harness_id)
        feature = capabilities.read
        if feature.state != "supported":
            raise LibraryCapabilityError(feature.reason, capability_state=feature.state)
        port = self._port_builder(harness_id)
        if port.harness_id != harness_id:
            raise InvalidLibraryCursorError("resolver factory returned the wrong harness port")
        return await port.read(ref, before=before, limit=limit)


__all__ = ["ConversationLibraryService", "LibraryPort", "PortBuilder"]
