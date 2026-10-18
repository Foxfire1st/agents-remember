"""Inline onboarding block extraction and classification for drift detection."""

from __future__ import annotations

from agents_remember.kernel.coordination_context_resolver import (
    clean_scalar,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    COMMON_BLOCK_DELIMITERS,
    INLINE_END_MARKER,
    INLINE_START_MARKER,
    InlineBlock,
)


def line_bounds(text: str, index: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return start, end


def expand_inline_bounds(source_text: str, start_index: int, end_index: int) -> tuple[int, int]:
    start_line_start, _ = line_bounds(source_text, start_index)
    previous_line_end = start_line_start - 1
    previous_line_start = source_text.rfind("\n", 0, max(previous_line_end, 0)) + 1
    previous_line = (
        source_text[previous_line_start:previous_line_end].strip() if start_line_start > 0 else ""
    )
    block_start = start_line_start
    expected_end = COMMON_BLOCK_DELIMITERS.get(previous_line)
    if expected_end:
        block_start = previous_line_start

    _, end_line_end = line_bounds(source_text, end_index)
    block_end = end_line_end
    if expected_end and end_line_end < len(source_text):
        next_line_start = end_line_end + 1
        next_line_end = source_text.find("\n", next_line_start)
        if next_line_end == -1:
            next_line_end = len(source_text)
        next_line = source_text[next_line_start:next_line_end].strip()
        if next_line == expected_end:
            block_end = next_line_end
    if block_end < len(source_text) and source_text[block_end : block_end + 1] == "\n":
        block_end += 1
    return block_start, block_end


def extract_inline_onboarding_block(source_text: str) -> InlineBlock | None:
    start_index = source_text.find(INLINE_START_MARKER)
    end_index = source_text.find(INLINE_END_MARKER)
    if start_index == -1 or end_index == -1 or end_index < start_index:
        return None

    block_start, block_end = expand_inline_bounds(source_text, start_index, end_index)
    raw_text = source_text[block_start:block_end]
    metadata: dict[str, str] = {}
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {INLINE_START_MARKER, INLINE_END_MARKER}:
            continue
        if stripped.startswith(
            ("/*", "*/", "<!--", "-->", '"""', "'''", "=begin", "=end", "{-", "-}", "(*", "*)")
        ):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = clean_scalar(value)
    return InlineBlock(raw_text=raw_text, metadata=metadata)
