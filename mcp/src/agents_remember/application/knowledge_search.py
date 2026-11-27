"""Deterministic SQLite FTS5 keyword search view (``view="search"``) for ``knowledge_read``.

Queries the derived ``ix_fts`` table with Porter stemming, BM25 column weighting, phrase boost,
and distinct-term coverage (``termsMatched``). Defaults to ``kinds=("invariant", "family",
"weakness")``, supports explicit ``kinds`` expansion (excluding ``history/``), optional
``files``/``folder`` scope filtering, 80-word statement trimming, and pagination inside the
3,500-token per-page target.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from agents_remember.application.file_briefs.titles import rule_title
from agents_remember.application.knowledge_currentness import Currentness
from agents_remember.application.knowledge_query_formatting import (
    anchor_counts,
    decode_query_cursor,
    encode_query_cursor,
    fit_slice_in_budget,
    normalize_repo_path,
    resolve_indexed_paths,
    scope_digest,
    standing_label,
    trim_statement,
)
from agents_remember.application.knowledge_reader.paths import states_at
from agents_remember.application.knowledge_reader.selection import ReaderSelection
from agents_remember.memory.knowledge_index import (
    ALLOWED_SEARCH_KINDS,
    DEFAULT_SEARCH_KINDS,
    FtsHit,
    KnowledgeIndex,
    Record,
    RecordStanding,
)

DEFAULT_SEARCH_BUDGET_TOKENS: Final = 3350
_SEARCH_FILE_CAP: Final = 4
_MAX_ITEMS_PER_PAGE: Final = 20


@dataclass(frozen=True)
class SearchQuery:
    """Parameters for one ``view="search"`` read."""

    query: str
    kinds: tuple[str, ...] | None = None
    files: tuple[str, ...] = ()
    folder: str | None = None
    compact: bool = False
    continuation: str | None = None
    budget_tokens: int = DEFAULT_SEARCH_BUDGET_TOKENS


@dataclass(frozen=True)
class _HitFormatContext:
    index: KnowledgeIndex
    standings: Mapping[str, RecordStanding]
    currentness: Currentness | None
    compact: bool


def search_view(selection: ReaderSelection, query: SearchQuery) -> dict[str, Any]:
    """Run deterministic FTS5 search over live records, bounded inside ``query.budget_tokens``."""

    if not query.query or not query.query.strip():
        return {
            "refusalCode": "invalid-input",
            "refusalDetail": "view='search' requires a non-empty `query` string",
        }
    effective_kinds, bad_kinds = _validate_kinds(query.kinds)
    if bad_kinds:
        return {
            "refusalCode": "invalid-input",
            "refusalDetail": (
                f"view='search' does not support kinds {list(bad_kinds)} "
                f"(history is excluded; allowed: {sorted(ALLOWED_SEARCH_KINDS)})"
            ),
        }

    index = selection.index
    scope_paths: tuple[str, ...] = ()
    norm_folder = (
        normalize_repo_path(query.folder) if query.folder and query.folder.strip() else None
    )
    if query.files or norm_folder is not None:
        scope_paths, _ = resolve_indexed_paths(index, query.files, folder=norm_folder)
    scope_set = set(scope_paths)

    digest = scope_digest(
        {
            "tree": index.state.key,
            "query": query.query.strip(),
            "kinds": list(effective_kinds),
            "files": list(scope_paths),
            "folder": norm_folder,
            "compact": query.compact,
        }
    )
    start = decode_query_cursor(query.continuation, view="search", digest=digest)
    if isinstance(start, str):
        return {"refusalCode": "bad_continuation", "refusalDetail": start}

    raw_hits: tuple[FtsHit, ...] = index.search_fts(query.query, kinds=effective_kinds).value
    scoped_hits = _filter_hits_by_scope(index, raw_hits, scope_set, norm_folder)

    if start > 0 and start >= len(scoped_hits):
        return {
            "refusalCode": "bad_continuation",
            "refusalDetail": f"continuation offset {start} is past the {len(scoped_hits)} matches",
        }

    window = scoped_hits[start : start + 60]
    window_records = {
        hit.record_id: rec
        for hit in window
        if (rec := index.record(hit.record_id).value) is not None
    }
    window_invs = [rid for rid, rec in window_records.items() if rec.kind == "invariant"]
    currentness, currentness_problem = (
        states_at(selection, window_invs, []) if window_invs else (None, None)
    )
    standings = index.standings(list(window_records)).value if window_records else {}
    fmt_ctx = _HitFormatContext(
        index=index, standings=standings, currentness=currentness, compact=query.compact
    )
    formatted_window = [
        _format_hit(fmt_ctx, hit, window_records[hit.record_id])
        for hit in window
        if hit.record_id in window_records
    ]

    code_pairing = _code_pairing_block(selection, currentness_problem)

    def render_page(page_rows: Sequence[Any], has_more: bool) -> dict[str, Any]:
        remaining = max(0, len(scoped_hits) - start - len(page_rows))
        body: dict[str, Any] = {
            "query": query.query.strip(),
            "kinds": list(effective_kinds),
            "results": list(page_rows),
            "counts": {
                "total": len(scoped_hits),
                "returned": len(page_rows),
                "remaining": remaining,
                "offset": start,
            },
            "codePairing": code_pairing,
            "enumerationComplete": remaining == 0 and not has_more,
        }
        if scope_paths or norm_folder:
            body["scope"] = {
                **({"files": list(scope_paths)} if scope_paths else {}),
                **({"folder": norm_folder} if norm_folder else {}),
            }
        if remaining > 0:
            body["continuation"] = encode_query_cursor("search", start + len(page_rows), digest)
        return body

    chosen, _ = fit_slice_in_budget(
        formatted_window,
        0,
        render_page,
        budget_tokens=query.budget_tokens,
        max_items=_MAX_ITEMS_PER_PAGE,
    )
    has_more = start + len(chosen) < len(scoped_hits)
    return render_page(chosen, has_more)


def _validate_kinds(kinds: Sequence[str] | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if kinds is None or len(kinds) == 0:
        return DEFAULT_SEARCH_KINDS, ()
    valid: list[str] = []
    invalid: list[str] = []
    for raw in kinds:
        if not isinstance(raw, str):
            continue
        word = raw.strip().lower()
        if word == "all":
            return tuple(sorted(ALLOWED_SEARCH_KINDS)), ()
        if word in ALLOWED_SEARCH_KINDS:
            if word not in valid:
                valid.append(word)
        elif word:
            invalid.append(word)
    return tuple(valid), tuple(invalid)


def _filter_hits_by_scope(
    index: KnowledgeIndex,
    hits: Sequence[FtsHit],
    scope_set: set[str],
    norm_folder: str | None,
) -> list[FtsHit]:
    if not scope_set and norm_folder is None:
        return list(hits)
    filtered: list[FtsHit] = []
    for hit in hits:
        paths = _record_paths(index, hit.record_id, hit.kind)
        if any(_path_in_scope(p, scope_set, norm_folder) for p in paths):
            filtered.append(hit)
    return filtered


def _path_in_scope(path: str, scope_set: set[str], norm_folder: str | None) -> bool:
    if path in scope_set:
        return True
    if norm_folder is not None:
        return path == norm_folder or path.startswith(f"{norm_folder}/")
    return False


def _record_paths(index: KnowledgeIndex, record_id: str, kind: str) -> tuple[str, ...]:
    if kind == "invariant":
        knowledge = index.invariant(record_id).value
        return tuple(
            sorted(
                {normalize_repo_path(e.path) for e in (*knowledge.realizations, *knowledge.proofs)}
            )
        )
    if kind == "family":
        return _family_paths(index, record_id)
    rec = index.record(record_id).value
    return () if rec is None else _single_record_paths(rec, kind)


def _family_paths(index: KnowledgeIndex, record_id: str) -> tuple[str, ...]:
    fam = index.family(record_id).value
    paths = {normalize_repo_path(route) for route in fam.routes}
    for member in fam.members:
        inv = index.invariant(member).value
        paths.update(normalize_repo_path(entry.path) for entry in inv.realizations)
    return tuple(sorted(paths))


def _single_record_paths(record: Record, kind: str) -> tuple[str, ...]:
    if kind == "weakness":
        anchor = record.document.get("anchor")
        if isinstance(anchor, Mapping) and isinstance(anchor.get("path"), str):
            return (normalize_repo_path(anchor["path"]),)
    if kind == "dependent":
        doc = record.document.get("document")
        if isinstance(doc, str) and doc.strip():
            return (normalize_repo_path(doc),)
    return ()


def _format_hit(ctx: _HitFormatContext, hit: FtsHit, record: Record) -> dict[str, Any] | str:
    doc = record.document
    paths = _record_paths(ctx.index, record.id, record.kind)
    anchors = None
    if record.kind == "invariant":
        inv = ctx.index.invariant(record.id).value
        anchors = anchor_counts(
            record.id, ctx.currentness, fallback_total=len(inv.realizations) + len(inv.proofs)
        )
    standing_text = standing_label(ctx.standings.get(record.id), anchors)
    terms_text = (
        f"{hit.terms_matched}/{hit.terms_total}+phrase"
        if hit.phrase_matched
        else f"{hit.terms_matched}/{hit.terms_total}"
    )

    if ctx.compact:
        title = _compact_title(record)
        shown_paths = ", ".join(paths[:3]) + (f" (+{len(paths) - 3})" if len(paths) > 3 else "")
        return (
            f"{record.id} ({record.kind}, {terms_text}) [{standing_text}] {title} [{shown_paths}]"
        )

    primary_text = _primary_statement(record)
    row: dict[str, Any] = {
        "id": record.id,
        "kind": record.kind,
        "score": hit.score,
        "termsMatched": terms_text,
        "standing": standing_text,
        "statement": trim_statement(primary_text, record.id),
    }
    recorded_title = doc.get("title")
    if isinstance(recorded_title, str) and recorded_title.strip():
        row["title"] = " ".join(recorded_title.split())
    if paths:
        shown = list(paths[:_SEARCH_FILE_CAP])
        extra = len(paths) - len(shown)
        row["at"] = shown if extra == 0 else [*shown, f"+{extra} more"]
    return row


def _compact_title(record: Record) -> str:
    if record.kind == "invariant":
        return rule_title(record.document).text
    doc = record.document
    title = doc.get("title")
    if isinstance(title, str) and title.strip():
        return " ".join(title.split())
    return rule_title({"statement": _primary_statement(record)}).text


def _primary_statement(record: Record) -> str:
    doc = record.document
    for key in ("statement", "guarantee", "context", "occurrence", "proposition", "definition"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if record.kind == "dependent":
        confirmed = doc.get("confirmed")
        if isinstance(confirmed, Mapping) and isinstance(confirmed.get("statement"), str):
            return confirmed["statement"]
        return str(doc.get("document") or "")
    return ""


def _code_pairing_block(selection: ReaderSelection, problem: str | None) -> dict[str, Any]:
    if selection.code_tree is None:
        return {"paired": False, "reason": problem or "no code tree was resolved"}
    return {
        "paired": True,
        "codeTreeId": selection.code_tree.tree[:12],
        "repositoryRoot": selection.code_tree.repository.as_posix(),
    }


__all__ = ["DEFAULT_SEARCH_BUDGET_TOKENS", "SearchQuery", "search_view"]
