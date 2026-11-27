"""Shared formatting, path normalization, statement trimming, and cursor helpers for query views."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from agents_remember.application.knowledge_currentness import Currentness
from agents_remember.application.knowledge_paging.threshold import response_tokens
from agents_remember.memory.knowledge_index import KnowledgeIndex, RecordStanding

STATEMENT_WORD_CAP: Final = 80
CURSOR_PREFIX: Final = "kq1."
_SENTENCE_ENDINGS: Final = (".", "!", "?")


def normalize_repo_path(path: str) -> str:
    """Normalize a repository-relative path without leading ``./`` or trailing ``/``."""

    cleaned = path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.strip("/")


def trim_statement(statement: str, record_id: str, *, max_words: int = STATEMENT_WORD_CAP) -> str:
    """Trim a statement longer than ``max_words`` words with an inline pointer to ``records``."""

    words = statement.split()
    if len(words) <= max_words:
        return " ".join(words)
    head = words[:max_words]
    cut_index = max_words
    for idx in range(max_words - 1, max(max_words - 21, 0), -1):
        if head[idx].endswith(_SENTENCE_ENDINGS):
            cut_index = idx + 1
            break
    trimmed = " ".join(head[:cut_index]).rstrip(",;:")
    return f"{trimmed} … (full: records {record_id})"


def writer_label(standing: RecordStanding | None) -> str:
    if standing is None or standing.writer is None:
        return "unknown"
    writer = standing.writer
    parts = [part for part in (writer.role, writer.model) if part and part != "unknown"]
    return "/".join(parts) if parts else "unknown"


def anchor_counts(
    invariant_id: str,
    currentness: Currentness | None,
    *,
    fallback_total: int = 0,
) -> dict[str, Any]:
    """Summarize anchor currentness counts for one invariant."""

    if currentness is None:
        return {
            "state": "unverifiable",
            "current": 0,
            "stale": 0,
            "unverifiable": fallback_total,
            "total": fallback_total,
        }
    inv_state = currentness.of(invariant_id)
    if inv_state is None:
        return {
            "state": "unverifiable",
            "current": 0,
            "stale": 0,
            "unverifiable": fallback_total,
            "total": fallback_total,
        }
    entries = inv_state.entries
    current_count = sum(1 for one in entries if one.state == "current")
    stale_count = sum(1 for one in entries if one.state in ("stale", "moved_or_absent"))
    unverifiable_count = sum(1 for one in entries if one.state == "unverifiable")
    return {
        "state": inv_state.state,
        "current": current_count,
        "stale": stale_count,
        "unverifiable": unverifiable_count,
        "total": len(entries),
    }


def standing_label(
    standing: RecordStanding | None,
    anchors: Mapping[str, Any] | None = None,
) -> str:
    state = "unknown" if standing is None or not standing.state else standing.state
    writer = writer_label(standing)
    if anchors is None:
        return f"{state}|{writer}"
    total = int(anchors.get("total", 0))
    current = int(anchors.get("current", 0))
    stale = int(anchors.get("stale", 0))
    unverifiable = int(anchors.get("unverifiable", 0))
    if total == 0:
        anchor_text = "0 anchors"
    elif unverifiable:
        anchor_text = f"{current}/{total} current, {unverifiable} unverifiable"
    elif stale:
        anchor_text = f"{current}/{total} current, {stale} stale"
    else:
        anchor_text = f"{current}/{total} current"
    return f"{state}|{writer}|{anchor_text}"


def resolve_indexed_paths(
    index: KnowledgeIndex,
    requested_files: Sequence[str],
    folder: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve user-supplied files/folder to uniform repo-relative paths present in the index.

    Returns ``(resolved_paths, unmatched_files)``. Exact normalized matches win; if a caller
    passes a shorter suffix (such as ``worktrees/modules/integrate.py``) that uniquely matches one
    indexed path, that canonical repo-relative path is used.
    """

    all_paths = sorted(index.live_entry_paths_under("").value)
    all_path_set = set(all_paths)
    matched: list[str] = []
    seen: set[str] = set()
    unmatched: list[str] = []

    for raw in requested_files:
        resolved, was_matched = _resolve_one_path(raw, all_paths, all_path_set)
        if resolved is None:
            continue
        if resolved not in seen:
            seen.add(resolved)
            matched.append(resolved)
        if not was_matched:
            unmatched.append(resolved)

    if folder is not None:
        norm_folder = normalize_repo_path(folder)
        for path in sorted(index.live_entry_paths_under(norm_folder).value):
            if path not in seen:
                seen.add(path)
                matched.append(path)

    return tuple(matched), tuple(unmatched)


def _resolve_one_path(
    raw: str, all_paths: Sequence[str], all_path_set: set[str]
) -> tuple[str | None, bool]:
    norm = normalize_repo_path(raw)
    if not norm:
        return None, True
    if norm in all_path_set:
        return norm, True
    suffix = f"/{norm}"
    candidates = [path for path in all_paths if path.endswith(suffix)]
    if len(candidates) == 1:
        return candidates[0], True
    return norm, False


def scope_digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def encode_query_cursor(view: str, offset: int, digest: str) -> str:
    return f"{CURSOR_PREFIX}{view}:{digest}:{offset}"


def decode_query_cursor(token: str | None, *, view: str, digest: str) -> int | str:
    """Decode a query cursor, returning the starting offset or an error detail string."""

    if token is None or not token.strip():
        return 0
    cleaned = token.strip()
    if not cleaned.startswith(CURSOR_PREFIX):
        return f"the continuation {token!r} is not a cursor minted by view={view!r}"
    parts = cleaned[len(CURSOR_PREFIX) :].split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return f"the continuation {token!r} is malformed"
    cursor_view, cursor_digest, raw_offset = parts
    if cursor_view != view:
        return f"the continuation was minted for view={cursor_view!r}, not view={view!r}"
    if cursor_digest != digest:
        return "the continuation does not match this memory tree or query scope; start a new read"
    return int(raw_offset)


def fit_slice_in_budget[T](
    items: Sequence[T],
    start: int,
    render: Callable[[Sequence[T], bool], Mapping[str, Any]],
    *,
    budget_tokens: int,
    max_items: int | None = None,
) -> tuple[list[T], int]:
    """Pack the longest prefix of ``items[start:]`` that fits inside ``budget_tokens``.

    Always emits at least one item when ``start < len(items)`` so pagination always advances.
    Returns ``(chosen_items, measured_tokens)``.
    """

    remaining = items[start:]
    if not remaining:
        return [], response_tokens(render((), False))
    cap = len(remaining) if max_items is None else min(len(remaining), max_items)
    lo, hi = 1, cap
    best_count = 1
    best_tokens = response_tokens(render(remaining[:1], cap > 1 or start + 1 < len(items)))
    if best_tokens > budget_tokens or cap == 1:
        return list(remaining[:1]), best_tokens
    while lo <= hi:
        mid = (lo + hi) // 2
        has_more = start + mid < len(items)
        tokens = response_tokens(render(remaining[:mid], has_more))
        if tokens <= budget_tokens:
            best_count = mid
            best_tokens = tokens
            lo = mid + 1
        else:
            hi = mid - 1
    return list(remaining[:best_count]), best_tokens


def estimate_total_pages[T](
    items: Sequence[T],
    render: Callable[[Sequence[T], bool], Mapping[str, Any]],
    *,
    budget_tokens: int,
    max_items: int | None = None,
) -> int:
    """Count exact pages needed for ``items`` under ``budget_tokens``."""

    if not items:
        return 1
    pages = 0
    cursor = 0
    while cursor < len(items):
        chosen, _ = fit_slice_in_budget(
            items, cursor, render, budget_tokens=budget_tokens, max_items=max_items
        )
        cursor += max(1, len(chosen))
        pages += 1
    return pages


__all__ = [
    "CURSOR_PREFIX",
    "STATEMENT_WORD_CAP",
    "anchor_counts",
    "decode_query_cursor",
    "encode_query_cursor",
    "estimate_total_pages",
    "fit_slice_in_budget",
    "normalize_repo_path",
    "resolve_indexed_paths",
    "scope_digest",
    "standing_label",
    "trim_statement",
    "writer_label",
]
