"""Library cursor/key authority: signed opaque tokens and identity digests (260718-CHATS-L2).

The one mint/verify boundary for :class:`LibraryListCursor`, :class:`LibraryReadCursor`,
:class:`LibraryConversationKey`, and :class:`NativeResumeTarget`. Every token is a
purpose-branded base64url JSON payload closed by an HMAC-SHA256 over the canonical payload, so a
caller cannot reorder, rebrand, or re-scope a token without failing verification. Possession of a
token is never authorization by itself: every list/read/open re-resolves the caller binding and the
service re-checks scope, purpose, and catalog generation on each call (design section 6.8).

The signing key is per-application and never persisted (minted by the dormant resolver factory).
A server restart therefore invalidates outstanding tokens honestly: verification fails closed with
``InvalidLibraryCursorError`` and the caller re-lists from native authority. No token content is
authoritative beyond the local operator posture it is issued to.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from typing import Literal

from agents_remember.serving.conversation.library.errors import (
    InvalidLibraryCursorError,
)
from agents_remember.serving.conversation.models import (
    ConversationLibraryScope,
    HarnessId,
    LibraryConversationKey,
    LibraryCursorBinding,
    LibraryKeyBinding,
    LibraryListCursor,
    LibraryReadCursor,
    NativeResumeTarget,
)

_SCHEMA_VERSION = 1
_MAC_BYTES = 32
TokenPurpose = Literal["library-list", "library-read"]


def _parse_envelope(encoded: str) -> dict[str, object]:
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidLibraryCursorError("token is not decodable") from exc
    if not isinstance(value, dict):
        raise InvalidLibraryCursorError("token payload is not an object")
    return value


def mint_signing_key() -> bytes:
    """One random per-application key; never derived from or written to any store."""

    return secrets.token_bytes(_MAC_BYTES)


class LibraryCursorAuthority:
    """Mints and verifies every library opaque token for exactly one app lifetime."""

    def __init__(self, signing_key: bytes) -> None:
        if len(signing_key) < _MAC_BYTES:
            raise ValueError("library cursor signing key must be at least 32 bytes")
        self._key = signing_key

    # -- identity digests -------------------------------------------------

    def identity_digest(
        self,
        harness_id: HarnessId,
        vendor_conversation_id: str,
        canonical_project_scope: str,
    ) -> str:
        """Server-issued stale-open check token, recomputed from native identity + key."""

        return "sha256:" + self._mac(
            "identity",
            {
                "harnessId": harness_id,
                "scope": canonical_project_scope,
                "vendor": vendor_conversation_id,
            },
        )

    @staticmethod
    def catalog_generation(signature: str) -> int:
        """Fold one native catalog signature into a positive wire generation integer.

        The generation is a content fingerprint of the observed native store: it changes exactly
        when the store observable changes, without a server-side counter or index.
        """

        digest = hashlib.sha256(signature.encode("utf-8")).digest()
        return int.from_bytes(digest[:7], "big") % (2**53 - 1) + 1

    # -- list/read cursors --------------------------------------------------

    def mint_list_cursor(
        self,
        scope: ConversationLibraryScope,
        *,
        catalog_generation: int,
        native_cursor: str | int,
    ) -> LibraryListCursor:
        payload = self._cursor_payload(
            scope,
            purpose="library-list",
            catalog_generation=catalog_generation,
            native_cursor=native_cursor,
        )
        return LibraryListCursor(f"{LibraryListCursor.token_prefix}{self._encode(payload)}")

    def mint_read_cursor(
        self,
        scope: ConversationLibraryScope,
        *,
        catalog_generation: int,
        native_cursor: str | int,
    ) -> LibraryReadCursor:
        payload = self._cursor_payload(
            scope,
            purpose="library-read",
            catalog_generation=catalog_generation,
            native_cursor=native_cursor,
        )
        return LibraryReadCursor(f"{LibraryReadCursor.token_prefix}{self._encode(payload)}")

    def verify_list_cursor(
        self,
        cursor: LibraryListCursor,
    ) -> tuple[LibraryCursorBinding, str | int]:
        return self._verify_cursor(
            cursor.root, LibraryListCursor.token_prefix, purpose="library-list"
        )

    def verify_read_cursor(
        self,
        cursor: LibraryReadCursor,
    ) -> tuple[LibraryCursorBinding, str | int]:
        return self._verify_cursor(
            cursor.root, LibraryReadCursor.token_prefix, purpose="library-read"
        )

    # -- conversation keys --------------------------------------------------

    def mint_conversation_key(
        self,
        scope: ConversationLibraryScope,
        *,
        vendor_conversation_id: str,
        identity_digest: str,
        catalog_generation: int,
    ) -> LibraryConversationKey:
        binding = LibraryKeyBinding(
            scope=scope,
            identity_digest=identity_digest,
            catalog_generation=catalog_generation,
            schema_version=_SCHEMA_VERSION,
        )
        payload = {
            "binding": binding.model_dump(mode="json", by_alias=True),
            "vendor": vendor_conversation_id,
        }
        return LibraryConversationKey(
            f"{LibraryConversationKey.token_prefix}{self._encode(payload)}"
        )

    def verify_conversation_key(
        self,
        key: LibraryConversationKey,
    ) -> tuple[LibraryKeyBinding, str]:
        payload = self._decode(key.root, LibraryConversationKey.token_prefix)
        vendor = payload.get("vendor")
        if not isinstance(vendor, str) or not vendor:
            raise InvalidLibraryCursorError("library conversation key carries no native identity")
        try:
            binding = LibraryKeyBinding.model_validate(payload.get("binding"))
        except ValueError as exc:
            raise InvalidLibraryCursorError(
                f"library conversation key binding is invalid: {exc}"
            ) from exc
        return binding, vendor

    # -- resume targets -----------------------------------------------------

    def mint_resume_target(
        self,
        scope: ConversationLibraryScope,
        *,
        vendor_conversation_id: str,
        identity_digest: str,
        catalog_generation: int,
        launch: Mapping[str, object],
    ) -> NativeResumeTarget:
        """Server-private exact resume target; never a public authorization grant."""

        payload = {
            "binding": LibraryKeyBinding(
                scope=scope,
                identity_digest=identity_digest,
                catalog_generation=catalog_generation,
                schema_version=_SCHEMA_VERSION,
            ).model_dump(mode="json", by_alias=True),
            "vendor": vendor_conversation_id,
            "launch": dict(launch),
        }
        return NativeResumeTarget(f"{NativeResumeTarget.token_prefix}{self._encode(payload)}")

    def verify_resume_target(
        self,
        target: NativeResumeTarget,
    ) -> tuple[LibraryKeyBinding, str, Mapping[str, object]]:
        payload = self._decode(target.root, NativeResumeTarget.token_prefix)
        vendor = payload.get("vendor")
        launch = payload.get("launch")
        if not isinstance(vendor, str) or not vendor:
            raise InvalidLibraryCursorError("resume target carries no native identity")
        if not isinstance(launch, Mapping) or not launch.get("kind"):
            raise InvalidLibraryCursorError("resume target carries no launch material")
        try:
            binding = LibraryKeyBinding.model_validate(payload.get("binding"))
        except ValueError as exc:
            raise InvalidLibraryCursorError(f"resume target binding is invalid: {exc}") from exc
        return binding, vendor, launch

    # -- internals ----------------------------------------------------------

    def _cursor_payload(
        self,
        scope: ConversationLibraryScope,
        *,
        purpose: TokenPurpose,
        catalog_generation: int,
        native_cursor: str | int,
    ) -> dict[str, object]:
        if isinstance(native_cursor, bool) or not isinstance(native_cursor, (str, int)):
            raise InvalidLibraryCursorError("native cursor position must be text or an integer")
        binding = LibraryCursorBinding(
            scope=scope,
            purpose=purpose,
            catalog_generation=catalog_generation,
            schema_version=_SCHEMA_VERSION,
        )
        return {
            "binding": binding.model_dump(mode="json", by_alias=True),
            "native": native_cursor,
        }

    def _verify_cursor(
        self,
        token: str,
        prefix: str,
        *,
        purpose: TokenPurpose,
    ) -> tuple[LibraryCursorBinding, str | int]:
        payload = self._decode(token, prefix)
        native = payload.get("native")
        if isinstance(native, bool) or not isinstance(native, (str, int)):
            raise InvalidLibraryCursorError("library cursor carries no native position")
        try:
            binding = LibraryCursorBinding.model_validate(payload.get("binding"))
        except ValueError as exc:
            raise InvalidLibraryCursorError(f"library cursor binding is invalid: {exc}") from exc
        if binding.purpose != purpose:
            raise InvalidLibraryCursorError("library cursor used for the wrong purpose")
        return binding, native

    def _encode(self, payload: Mapping[str, object]) -> str:
        body = dict(payload)
        body["v"] = _SCHEMA_VERSION
        body["mac"] = self._mac("token", body)
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    def _decode(self, token: str, prefix: str) -> Mapping[str, object]:
        if not token.startswith(prefix):
            raise InvalidLibraryCursorError("token used with the wrong purpose prefix")
        value = _parse_envelope(token[len(prefix) :])
        if value.get("v") != _SCHEMA_VERSION:
            raise InvalidLibraryCursorError("token schema version is unsupported")
        mac = value.pop("mac", None)
        if not isinstance(mac, str) or not hmac.compare_digest(mac, self._mac("token", value)):
            raise InvalidLibraryCursorError("token signature does not verify")
        return value

    def _mac(self, domain: str, payload: Mapping[str, object]) -> str:
        canonical = json.dumps(
            {"domain": domain, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(self._key, canonical, hashlib.sha256).hexdigest()


__all__ = ["LibraryCursorAuthority", "mint_signing_key"]
