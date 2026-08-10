"""Deterministic public preview and content-digest transforms (260718-CHATS-L3).

The queue projection's ``redactedPreview`` is identification copy for the
authenticated caller's own cockpit row, never recovery authority: the transform
removes control characters, collapses whitespace, applies the repository
secret-redaction policy, and returns at most 96 grapheme-ish clusters plus
``previewTruncated``. The digest mirrors the submission authority's exact
payload-digest construction (text-only byte-identical; canonical asset identity
covered only when assets are present) so the daemon-held digest and the
authority's idempotence digest always agree for the same content.
"""

from __future__ import annotations

import json
import unicodedata
from hashlib import sha256

from agents_remember.kernel.primitives.tool_reports import redact_secrets
from agents_remember.models.conversations.control_wire import (
    AssetReference,
)

MAX_PREVIEW_CLUSTERS = 96

_ZWJ = "‍"
_VARIATION_SELECTORS = range(0xFE00, 0xFE10)


def payload_digest(text: str, assets: tuple[AssetReference, ...] = ()) -> str:
    """The authority's exact payload digest, ``sha256:``-prefixed for the wire."""

    if not assets:
        return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"
    canonical = json.dumps(
        {
            "text": text,
            "assets": [
                {
                    "assetId": asset.asset_id,
                    "mimeType": asset.mime_type,
                    "byteSize": asset.byte_size,
                    "sha256": asset.sha256,
                }
                for asset in sorted(assets, key=lambda item: item.asset_id)
            ],
        },
        separators=(",", ":"),
    )
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


def redacted_preview(text: str) -> tuple[str, bool]:
    """The deterministic public preview transform; returns (preview, truncated)."""

    cleaned = "".join(
        character if character in "\n\t" or ord(character) >= 32 else " " for character in text
    )
    collapsed = " ".join(cleaned.split())
    redacted = str(redact_secrets(collapsed))
    clusters = _clusters(redacted)
    if len(clusters) <= MAX_PREVIEW_CLUSTERS:
        return redacted, False
    return "".join(clusters[:MAX_PREVIEW_CLUSTERS]), True


def _clusters(text: str) -> list[str]:
    """Split into conservative grapheme-ish clusters (base + continuation).

    Continuation characters are Unicode combining marks, variation selectors,
    zero-width joiners, and any character following a joiner — the cut edge
    therefore never lands inside an emoji/accent cluster. This is a documented
    stdlib-only approximation of UAX #29 segmentation (the ``regex`` package
    is not a declared dependency); it is conservative for identification copy:
    it may merge two graphemes into one cluster, never split one.
    """

    clusters: list[str] = []
    for character in text:
        if not clusters:
            clusters.append(character)
            continue
        codepoint = ord(character)
        if (
            unicodedata.combining(character)
            or codepoint in _VARIATION_SELECTORS
            or character == _ZWJ
            or clusters[-1].endswith(_ZWJ)
        ):
            clusters[-1] += character
        else:
            clusters.append(character)
    return clusters


__all__ = ["MAX_PREVIEW_CLUSTERS", "payload_digest", "redacted_preview"]
