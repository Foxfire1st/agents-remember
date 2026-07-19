"""Shared normalization primitives for the dormant native libraries (260718-CHATS-L2).

Single source for the small helpers every harness resolver needs: text capping, native-only
provenance, required-field parsing, and vendor text-content extraction. One home so the three
resolvers cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.serving.conversation.library.errors import LibraryStoreError
from agents_remember.serving.conversation.models import ProvenanceEvidence

TEXT_BLOCK_CAP = 8192


def capped_text(text: str, cap: int = TEXT_BLOCK_CAP) -> str:
    """Bound one block's text with a visible truncation marker (resource guard)."""

    if len(text) <= cap:
        return text
    return text[:cap] + "\n…[truncated]"


def native_provenance(producer: str | None, origin: str) -> ProvenanceEvidence:
    """Native-history provenance: strength is always native-only, producer only when proven."""

    if producer is None:
        return ProvenanceEvidence(strength="native-only", origin=origin)
    return ProvenanceEvidence(strength="native-only", producer=producer, origin=origin)  # type: ignore[arg-type]


def required_field(payload: Mapping[str, object], key: str, *, source: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise LibraryStoreError(f"{source} response lacks {key}")
    return value


def first_text(payload: Mapping[str, object], *keys: str) -> str | None:
    """The first non-empty trimmed string among ``keys``, or ``None``."""

    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def text_content_parts(content: object) -> list[str]:
    """Extract the text segments of a vendor content field (string or typed block list)."""

    if isinstance(content, str):
        return [content] if content else []
    return _text_parts_from_blocks(content)


def _text_parts_from_blocks(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    return [text for entry in content if (text := _text_part(entry))]


def _text_part(entry: object) -> str:
    if not isinstance(entry, Mapping) or entry.get("type") != "text":
        return ""
    text = entry.get("text")
    return text if isinstance(text, str) else ""


__all__ = [
    "TEXT_BLOCK_CAP",
    "capped_text",
    "first_text",
    "native_provenance",
    "required_field",
    "text_content_parts",
]
