"""Pure tree-sitter syntax highlighting engine: capture precedence sweep and UTF-16 spans.

This module owns the deterministic, language-agnostic highlighting pipeline:
1. ``; inherits:`` directive parsing and ordered query bundle composition.
2. ``\\r\\n`` and lone ``\\r`` normalization to ``\\n`` on a copy (leaving raw caller text intact).
3. ``QueryCursor`` capture collection and ``O(M log M)`` precedence sweep handling
   ``@none`` suppression, narrower-node precedence, query/pattern order, dropping unstyled
   captures, and merging adjacent same-kind spans.
4. ``O(N)`` UTF-8 byte-offset to UTF-16 code-unit offset conversion for CodeMirror 6.

No language lists or suffix tables live here.
"""

from __future__ import annotations

import hashlib
import heapq
import re
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from tree_sitter import Language, Parser, Query, QueryCursor

from agents_remember.models.highlights import (
    HIGHLIGHT_MAX_BYTES,
    HIGHLIGHT_MAX_SPANS,
    HIGHLIGHT_PAYLOAD_VERSION,
    HighlightKind,
    SyntaxHighlights,
)

__all__ = [
    "HIGHLIGHT_VERSION",
    "classify_capture",
    "compose_highlight_queries",
    "compute_highlights",
    "normalize_newlines",
    "parse_inherits_directive",
]

HIGHLIGHT_VERSION: Final = "ar-highlights/1"

_INHERITS_RE: Final = re.compile(r"^\s*;\s*inherits\s*:\s*([a-zA-Z0-9_,\s-]+)\s*$")

# (HighlightKind | None, precedence rank).
# ``kind=None, rank=100`` is the absolute ``@none`` suppressor; ``kind=None, rank=80`` suppresses
# enclosing styled captures (such as JSON keys overriding ``@string``); ``rank=0`` is unstyled.
_CAPTURE_MAP: Final[dict[str, tuple[HighlightKind | None, int]]] = {
    "none": (None, 100),
    "string.special.key": (None, 80),
    "string.escape": ("string", 70),
    "escape": ("string", 70),
    "keyword": ("keyword", 65),
    "conditional": ("keyword", 65),
    "storageclass": ("keyword", 65),
    "import": ("keyword", 65),
    "comment": ("comment", 60),
    "string": ("string", 55),
    "character": ("string", 55),
    "number": ("number", 55),
    "float": ("number", 55),
    "boolean": ("number", 55),
    "constant.builtin": ("number", 55),
    "function": ("function", 50),
    "method": ("function", 50),
    "constructor": ("type", 52),
    "tag": ("tag", 50),
    "attribute": ("attribute", 50),
    "text.title": ("heading", 50),
    "markup.heading": ("heading", 50),
    "text.uri": ("function", 50),
    "markup.link.url": ("function", 50),
    "text.reference": ("type", 48),
    "markup.link": ("type", 48),
    "type": ("type", 45),
    "module": ("type", 45),
    "namespace": ("type", 45),
    "constant": ("number", 42),
    "label": ("keyword", 42),
    "text.literal": ("string", 40),
    "markup.raw": ("string", 40),
    "operator": ("operator", 35),
    "punctuation": ("punctuation", 30),
    "delimiter": ("punctuation", 30),
}


def classify_capture(name: str) -> tuple[HighlightKind | None, int]:
    """Map a tree-sitter capture name (walking dot prefixes) to ``(kind, precedence_rank)``.

    Returns ``(None, 0)`` for unstyled captures (such as ``@variable`` or ``@property``) so
    they are dropped before sweeping.
    """
    candidate = name.lstrip("@")
    while candidate:
        hit = _CAPTURE_MAP.get(candidate)
        if hit is not None:
            return hit
        dot = candidate.rfind(".")
        if dot < 0:
            break
        candidate = candidate[:dot]
    return (None, 0)


def parse_inherits_directive(query_text: str) -> tuple[str, ...]:
    """Extract parent language ids from leading ``; inherits: a, b`` comments."""
    parents: list[str] = []
    for line in query_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(";"):
            break
        match = _INHERITS_RE.match(stripped)
        if match is None:
            continue
        for item in match.group(1).split(","):
            parent = item.strip()
            if parent and parent not in parents:
                parents.append(parent)
    return tuple(parents)


def compose_highlight_queries(
    lang_id: str,
    own_queries: Sequence[str],
    resolve_parent: Callable[[str], Sequence[str]],
) -> tuple[str, ...]:
    """Expand ``; inherits:`` directives from base to derived with cycle protection."""
    ordered: list[str] = []
    seen_texts: set[str] = set()
    visiting: set[str] = {lang_id}

    def _visit(texts: Sequence[str]) -> None:
        for text in texts:
            for parent_id in parse_inherits_directive(text):
                if parent_id in visiting:
                    continue
                visiting.add(parent_id)
                _visit(resolve_parent(parent_id))
        for text in texts:
            if text not in seen_texts:
                seen_texts.add(text)
                ordered.append(text)

    _visit(own_queries)
    return tuple(ordered)


def normalize_newlines(text: str) -> str:
    """Return a copy of ``text`` with ``\\r\\n`` and lone ``\\r`` normalized to ``\\n``."""
    if "\r" not in text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class _CaptureSpan:
    start: int
    end: int
    kind: HighlightKind | None
    priority: tuple[int, int, int, int, int]


def _collect_capture_spans(
    tree_root: object,
    queries: Sequence[Query],
    byte_length: int,
) -> list[_CaptureSpan]:
    spans: list[_CaptureSpan] = []
    for query_index, query in enumerate(queries):
        cursor = QueryCursor(query)
        for pattern_index, captures in cursor.matches(tree_root):  # type: ignore[arg-type]
            for capture_name, nodes in captures.items():
                kind, rank = classify_capture(capture_name)
                if kind is None and rank == 0:
                    continue
                is_none = 1 if rank >= 100 else 0
                for node in nodes:
                    start = node.start_byte
                    end = node.end_byte
                    if start < 0 or end <= start or end > byte_length:
                        continue
                    # Higher tuple wins: @none suppressor > narrower span > capture rank > query/pattern order.
                    priority = (is_none, -(end - start), rank, query_index, pattern_index)
                    spans.append(_CaptureSpan(start=start, end=end, kind=kind, priority=priority))
    return spans


def _apply_events_at(
    events: Sequence[tuple[int, int, int]],
    idx: int,
    raw_spans: Sequence[_CaptureSpan],
    active: list[bool],
    heap: list[tuple[tuple[int, int, int, int, int], int]],
) -> int:
    pos = events[idx][0]
    total = len(events)
    while idx < total and events[idx][0] == pos:
        _, kind_flag, span_id = events[idx]
        if kind_flag == 1:
            active[span_id] = True
            p = raw_spans[span_id].priority
            heapq.heappush(heap, ((-p[0], -p[1], -p[2], -p[3], -p[4]), span_id))
        else:
            active[span_id] = False
        idx += 1
    return idx


def _append_merged(
    merged: list[tuple[int, int, HighlightKind]],
    start: int,
    end: int,
    kind: HighlightKind,
) -> None:
    if merged and merged[-1][1] == start and merged[-1][2] == kind:
        merged[-1] = (merged[-1][0], end, kind)
    else:
        merged.append((start, end, kind))


def _sweep_byte_spans(raw_spans: Sequence[_CaptureSpan]) -> list[tuple[int, int, HighlightKind]]:
    """Resolve overlapping captures into disjoint, merged ``(start_byte, end_byte, kind)`` spans."""
    if not raw_spans:
        return []
    events: list[tuple[int, int, int]] = []
    for span_id, span in enumerate(raw_spans):
        events.append((span.start, 1, span_id))
        events.append((span.end, 0, span_id))
    events.sort(key=lambda item: (item[0], item[1]))

    active: list[bool] = [False] * len(raw_spans)
    heap: list[tuple[tuple[int, int, int, int, int], int]] = []
    merged: list[tuple[int, int, HighlightKind]] = []
    idx = 0
    total = len(events)
    while idx < total:
        pos = events[idx][0]
        idx = _apply_events_at(events, idx, raw_spans, active, heap)
        if idx >= total:
            break
        next_pos = events[idx][0]
        while heap and not active[heap[0][1]]:
            heapq.heappop(heap)
        if next_pos > pos and heap:
            winner = raw_spans[heap[0][1]].kind
            if winner is not None:
                _append_merged(merged, pos, next_pos, winner)
    return merged


def _build_byte_to_utf16_table(normalized: str, byte_length: int) -> array[int]:
    """Build an ``O(N)`` lookup mapping every UTF-8 byte boundary to its UTF-16 code-unit offset."""
    table = array("I", [0]) * (byte_length + 1)
    byte_pos = 0
    utf16_pos = 0
    for ch in normalized:
        codepoint = ord(ch)
        if codepoint < 0x80:
            b_len = 1
            u16_len = 1
        elif codepoint < 0x800:
            b_len = 2
            u16_len = 1
        elif codepoint < 0x10000:
            b_len = 3
            u16_len = 1
        else:
            b_len = 4
            u16_len = 2
        for step in range(b_len):
            table[byte_pos + step] = utf16_pos
        byte_pos += b_len
        utf16_pos += u16_len
    table[byte_length] = utf16_pos
    return table


def _pack_utf16_payload(
    byte_spans: Sequence[tuple[int, int, HighlightKind]],
    normalized: str,
    byte_length: int,
    *,
    language_id: str,
    version_tag: str,
) -> SyntaxHighlights | None:
    if len(byte_spans) > HIGHLIGHT_MAX_SPANS:
        return None
    if not byte_spans:
        return None
    ascii_only = normalized.isascii()
    utf16_table = None if ascii_only else _build_byte_to_utf16_table(normalized, byte_length)

    kind_to_index: dict[HighlightKind, int] = {}
    kinds: list[HighlightKind] = []
    packed: list[int] = []
    for start_b, end_b, kind in byte_spans:
        start_u16 = start_b if utf16_table is None else utf16_table[start_b]
        end_u16 = end_b if utf16_table is None else utf16_table[end_b]
        if end_u16 <= start_u16:
            continue
        kind_index = kind_to_index.get(kind)
        if kind_index is None:
            kind_index = len(kinds)
            kind_to_index[kind] = kind_index
            kinds.append(kind)
        packed.extend((start_u16, end_u16, kind_index))
    if not packed:
        return None
    digest_hash = hashlib.sha256()
    digest_hash.update(f"{HIGHLIGHT_VERSION}:{language_id}:{version_tag}:".encode())
    digest_hash.update(",".join(kinds).encode("ascii"))
    digest_hash.update(b":")
    digest_hash.update(",".join(str(v) for v in packed).encode("ascii"))
    return SyntaxHighlights(
        version=HIGHLIGHT_PAYLOAD_VERSION,
        digest=digest_hash.hexdigest()[:16],
        kinds=tuple(kinds),
        spans=tuple(packed),
    )


def compute_highlights(
    language: Language,
    queries: Sequence[Query],
    text: str,
    *,
    language_id: str,
    version_tag: str,
) -> SyntaxHighlights | None:
    """Parse ``text`` (on a ``\\n``-normalized copy) and return packed UTF-16 highlights."""
    if not text or not queries:
        return None
    normalized = normalize_newlines(text)
    raw_bytes = normalized.encode("utf-8")
    if not raw_bytes or len(raw_bytes) > HIGHLIGHT_MAX_BYTES:
        return None
    tree = Parser(language).parse(raw_bytes)
    raw_spans = _collect_capture_spans(tree.root_node, queries, len(raw_bytes))
    byte_spans = _sweep_byte_spans(raw_spans)
    return _pack_utf16_payload(
        byte_spans,
        normalized,
        len(raw_bytes),
        language_id=language_id,
        version_tag=version_tag,
    )
