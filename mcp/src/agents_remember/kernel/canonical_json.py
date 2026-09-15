"""Canonical JSON encoding for content-addressed digests.

One encoder owns "the same logical value always produces the same bytes" across the tree.
It is a kernel primitive because it holds no feature: it decides only separators, key order,
Unicode and float policy, and it refuses the value classes whose encoding would not be
reproducible (NaN/Infinity, non-string mapping keys).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

# The one canonical encoding: UTF-8, sorted keys, compact separators, literal Unicode.
# ``allow_nan`` is off because NaN has no single textual spelling, so a digest over it
# would not identify the value it seals.
CANONICAL_JSON_KWARGS: dict[str, Any] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
    "allow_nan": False,
}


def canonical_json_bytes(value: Any) -> bytes:
    """Encode ``value`` as the canonical UTF-8 JSON byte string for digesting."""

    text = json.dumps(value, **CANONICAL_JSON_KWARGS)
    return text.encode("utf-8")


def sha256_digest(value: Any) -> str:
    """Return the lowercase hex SHA-256 over ``value``'s canonical encoding."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def prefixed_sha256_digest(value: Any) -> str:
    """Return the canonical ``sha256:<hex>`` form used in receipts and exports."""

    return f"sha256:{sha256_digest(value)}"


def decoded_json(text: str) -> Any:
    """Decode a stored canonical JSON text column, refusing duplicate keys.

    A duplicate key means the stored text is ambiguous about the value it holds, so it is a
    refusal rather than a last-one-wins decode.
    """

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise ValueError(f"duplicate JSON key: {key}")
            seen[key] = value
        return seen

    return json.loads(text, object_pairs_hook=reject_duplicates)


def require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    """Narrow a decoded JSON value to a mapping with a legible refusal."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value
