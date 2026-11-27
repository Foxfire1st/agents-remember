"""Derived SQLite FTS5 index (``ix_fts``) for deterministic keyword search over live records.

Built deterministically inside the existing derived knowledge index at index build time. Indexes
live records' ``title``, ``statement``, ``conditions``, and ``applicability`` using
``porter unicode61`` tokenization. ``history/`` is excluded, and retired invariants and closed
weaknesses are excluded from the live FTS table.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import apsw

DEFAULT_SEARCH_KINDS: Final[tuple[str, ...]] = ("invariant", "family", "weakness")
ALLOWED_SEARCH_KINDS: Final[frozenset[str]] = frozenset(
    {
        "invariant",
        "family",
        "weakness",
        "dependent",
        "decision",
        "incident",
        "assumption",
        "limitation",
        "failure_mode",
        "scenario",
        "diagnostic",
        "term",
    }
)
_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9_]+")
# BM25 column weights matching (record_id, kind, title, statement, conditions, applicability).
_BM25_WEIGHTS: Final = "0.0, 0.0, 3.0, 2.0, 1.2, 1.0"


@dataclass(frozen=True)
class FtsHit:
    """One ranked FTS5 match over live knowledge records."""

    record_id: str
    kind: str
    score: float
    bm25: float
    terms_matched: int
    terms_total: int
    phrase_matched: bool


def populate_fts(connection: apsw.Connection, parsed: Any) -> None:
    """Populate ``ix_fts`` deterministically from ``parsed.records`` (live records only)."""

    rows: list[tuple[str, str, str, str, str, str]] = []
    for record_id in sorted(parsed.records):
        indexed = parsed.records[record_id]
        record = indexed.record
        kind = str(record.record_kind)
        document = record.to_document()
        if _excluded_from_live_fts(kind, document):
            continue
        title, statement, conditions, applicability = _extract_fields(kind, document)
        rows.append((record_id, kind, title, statement, conditions, applicability))
    if rows:
        connection.executemany(
            "INSERT INTO ix_fts (record_id, kind, title, statement, conditions, applicability) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def search_fts(
    connection: apsw.Connection,
    query_text: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int | None = None,
) -> tuple[FtsHit, ...]:
    """Run deterministic FTS5 Porter + BM25 + phrase + distinct-term-coverage search."""

    terms = tokenize_query(query_text)
    if not terms:
        return ()
    selected_kinds = _normalize_kinds(kinds)
    if not selected_kinds:
        return ()

    placeholders = ", ".join("?" for _ in selected_kinds)
    or_query = " OR ".join(f'"{term}"' for term in terms)
    candidates = list(
        connection.execute(
            f"SELECT record_id, kind, bm25(ix_fts, {_BM25_WEIGHTS}) "
            f"FROM ix_fts WHERE ix_fts MATCH ? AND kind IN ({placeholders})",
            (or_query, *selected_kinds),
        )
    )
    if not candidates:
        return ()

    term_hits = _per_term_matches(connection, terms, selected_kinds, placeholders)
    phrase_hits = _phrase_matches(connection, terms, selected_kinds, placeholders)
    total_terms = len(terms)

    scored: list[FtsHit] = []
    for raw_id, raw_kind, raw_bm25 in candidates:
        record_id = str(raw_id)
        kind = str(raw_kind)
        bm25_mag = max(0.0, -float(raw_bm25))
        matched = sum(1 for hit_set in term_hits if record_id in hit_set)
        if matched == 0:
            matched = 1
        phrase = record_id in phrase_hits
        coverage = matched / total_terms
        combined = bm25_mag * (0.5 + 1.5 * coverage) + (8.0 * coverage) + (12.0 if phrase else 0.0)
        scored.append(
            FtsHit(
                record_id=record_id,
                kind=kind,
                score=round(combined, 4),
                bm25=round(bm25_mag, 4),
                terms_matched=matched,
                terms_total=total_terms,
                phrase_matched=phrase,
            )
        )
    scored.sort(
        key=lambda hit: (-hit.score, -int(hit.phrase_matched), -hit.terms_matched, hit.record_id)
    )
    if limit is not None and limit >= 0:
        scored = scored[:limit]
    return tuple(scored)


def tokenize_query(query_text: str) -> tuple[str, ...]:
    """Extract deduplicated safe query terms preserving original order."""

    raw = [match.group(0).lower() for match in _TOKEN_RE.finditer(query_text)]
    deduped = list(dict.fromkeys(raw))
    if not deduped:
        return ()
    meaningful = [token for token in deduped if len(token) > 1]
    return tuple(meaningful if meaningful else deduped)


def _normalize_kinds(kinds: Sequence[str] | None) -> tuple[str, ...]:
    if kinds is None:
        return DEFAULT_SEARCH_KINDS
    normalized = tuple(
        dict.fromkeys(
            kind.strip().lower()
            for kind in kinds
            if isinstance(kind, str) and kind.strip().lower() in ALLOWED_SEARCH_KINDS
        )
    )
    return normalized


def _per_term_matches(
    connection: apsw.Connection,
    terms: Sequence[str],
    kinds: Sequence[str],
    placeholders: str,
) -> list[set[str]]:
    results: list[set[str]] = []
    for term in terms:
        matched = {
            str(row[0])
            for row in connection.execute(
                f"SELECT record_id FROM ix_fts WHERE ix_fts MATCH ? AND kind IN ({placeholders})",
                (f'"{term}"', *kinds),
            )
        }
        results.append(matched)
    return results


def _phrase_matches(
    connection: apsw.Connection,
    terms: Sequence[str],
    kinds: Sequence[str],
    placeholders: str,
) -> set[str]:
    if len(terms) < 2:
        return set()
    phrase = '"' + " ".join(terms) + '"'
    return {
        str(row[0])
        for row in connection.execute(
            f"SELECT record_id FROM ix_fts WHERE ix_fts MATCH ? AND kind IN ({placeholders})",
            (phrase, *kinds),
        )
    }


def _excluded_from_live_fts(kind: str, document: Mapping[str, Any]) -> bool:
    if kind == "invariant" and document.get("status") == "retired":
        return True
    return kind == "weakness" and document.get("state") == "closed"


def _extract_fields(kind: str, document: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if kind == "invariant":
        return (
            _text(document.get("title")),
            _text(document.get("statement")),
            _join_strings(document.get("conditions")),
            _text(document.get("applicability")),
        )
    if kind == "family":
        return (
            _text(document.get("title")),
            _text(document.get("guarantee")),
            "",
            _join_strings(document.get("routes")),
        )
    if kind == "weakness":
        anchor = document.get("anchor")
        path = _text(anchor.get("path")) if isinstance(anchor, Mapping) else ""
        return (_text(document.get("kind")), _text(document.get("statement")), "", path)
    if kind == "dependent":
        confirmed = document.get("confirmed")
        confirmed_text = _text(confirmed.get("statement")) if isinstance(confirmed, Mapping) else ""
        deferral_parts = _dependent_deferral_text(document.get("deferrals"))
        doc_path = _text(document.get("document"))
        return (doc_path, confirmed_text, deferral_parts, doc_path)
    if kind == "decision":
        return (
            _text(document.get("title")),
            _text(document.get("context")),
            _decision_conditions(document),
            "",
        )
    return _facet_fields(document)


def _dependent_deferral_text(deferrals: Any) -> str:
    if not isinstance(deferrals, Iterable) or isinstance(deferrals, (str, bytes)):
        return ""
    parts: list[str] = []
    for item in deferrals:
        if not isinstance(item, Mapping):
            continue
        for key in ("passage", "reason"):
            value = _text(item.get(key))
            if value:
                parts.append(value)
        condition = item.get("condition")
        if isinstance(condition, Mapping):
            for key in ("task", "file", "note"):
                value = _text(condition.get(key))
                if value:
                    parts.append(value)
    return " ".join(parts)


def _decision_conditions(document: Mapping[str, Any]) -> str:
    parts: list[str] = []
    consequences = _join_strings(document.get("consequences"))
    if consequences:
        parts.append(consequences)
    alternatives = document.get("alternatives")
    if isinstance(alternatives, Iterable) and not isinstance(alternatives, (str, bytes)):
        for alt in alternatives:
            if not isinstance(alt, Mapping):
                continue
            for key in ("option", "reason", "reconsider_when"):
                value = _text(alt.get(key))
                if value:
                    parts.append(value)
    return " ".join(parts)


def _facet_fields(document: Mapping[str, Any]) -> tuple[str, str, str, str]:
    title = _text(document.get("title") or document.get("term"))
    statement = " ".join(
        piece
        for piece in (
            _text(document.get("statement")),
            _text(document.get("occurrence")),
            _text(document.get("proposition")),
            _text(document.get("limited")),
            _text(document.get("failure")),
            _text(document.get("situation")),
            _text(document.get("definition")),
            _text(document.get("cause")),
            _text(document.get("observable_effect")),
        )
        if piece
    )
    conditions = " ".join(
        piece
        for piece in (
            _text(document.get("condition")),
            _text(document.get("basis")),
            _text(document.get("boundary")),
            _text(document.get("preconditions")),
            _text(document.get("signal")),
            _text(document.get("interpretation")),
        )
        if piece
    )
    applicability = _text(document.get("applicability") or document.get("scope"))
    return (title, statement, conditions, applicability)


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _join_strings(value: Any) -> str:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return ""
    return " ".join(_text(item) for item in value if isinstance(item, str) and item.strip())


__all__ = [
    "ALLOWED_SEARCH_KINDS",
    "DEFAULT_SEARCH_KINDS",
    "FtsHit",
    "populate_fts",
    "search_fts",
    "tokenize_query",
]
