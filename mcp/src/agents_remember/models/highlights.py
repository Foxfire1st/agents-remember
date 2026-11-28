"""Compact UTF-16 syntax highlight payload shared across code-serving surfaces.

Every server-side code viewer emits one immutable :class:`SyntaxHighlights` object alongside
its text (or ``None`` for binary, media, oversized, or unhighlighted files). Offsets are
UTF-16 code-unit indices into the ``\\n``-normalized text so CodeMirror 6 applies them
directly as ``Decoration.mark`` ranges without re-parsing in the browser.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "HIGHLIGHT_MAX_BYTES",
    "HIGHLIGHT_MAX_SPANS",
    "HIGHLIGHT_PAYLOAD_VERSION",
    "HighlightKind",
    "SyntaxHighlights",
]

HIGHLIGHT_PAYLOAD_VERSION: Final = 1
HIGHLIGHT_MAX_BYTES: Final = 512 * 1024
HIGHLIGHT_MAX_SPANS: Final = 20_000

HighlightKind = Literal[
    "keyword",
    "string",
    "comment",
    "number",
    "function",
    "type",
    "operator",
    "punctuation",
    "tag",
    "attribute",
    "heading",
]


class SyntaxHighlights(BaseModel):
    """Flat, immutable UTF-16 highlight span payload.

    ``spans`` packs triples ``(start_utf16, end_utf16, kind_index)`` in ascending non-overlapping
    order where each ``kind_index`` indexes ``kinds``. ``digest`` identifies the computed span
    sequence so viewers can skip re-building decorations across unchanged polls.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=HIGHLIGHT_PAYLOAD_VERSION, ge=1)
    digest: str = Field(min_length=1, max_length=64)
    kinds: tuple[HighlightKind, ...] = ()
    spans: tuple[int, ...] = ()

    @model_validator(mode="after")
    def _validate_span_triples(self) -> SyntaxHighlights:
        if len(self.spans) % 3 != 0:
            raise ValueError("spans must contain flat (start, end, kind_index) triples")
        kind_count = len(self.kinds)
        prev_end = 0
        for index in range(0, len(self.spans), 3):
            start = self.spans[index]
            end = self.spans[index + 1]
            kind_index = self.spans[index + 2]
            if start < prev_end or end <= start:
                raise ValueError(
                    f"span triple at {index // 3} must be strictly ordered and non-empty"
                )
            if kind_index < 0 or kind_index >= kind_count:
                raise ValueError(
                    f"span triple at {index // 3} references out-of-range kind {kind_index}"
                )
            prev_end = end
        return self

    def to_wire(self) -> dict[str, object]:
        """Plain JSON-serializable dict for ``JSONResponse`` serving handlers."""
        return {
            "version": self.version,
            "digest": self.digest,
            "kinds": list(self.kinds),
            "spans": list(self.spans),
        }
