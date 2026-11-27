"""Batch full-text record lookup (``view="records"``) for ``knowledge_read``.

Returns full verbatim statements, conditions, exclusions, standing, realizations, proofs, and
families for up to 25 requested record IDs, paginated at up to 20 records per page inside the
5,000-token ``knowledge_read`` answer bound. Never trims statements. Whenever ``stale > 0``,
names the stale anchor paths and states inline (bounded list + total stale count).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Final

from agents_remember.application.file_briefs.titles import rule_title
from agents_remember.application.knowledge_currentness import Currentness
from agents_remember.application.knowledge_query_formatting import (
    anchor_counts,
    decode_query_cursor,
    encode_query_cursor,
    fit_slice_in_budget,
    normalize_repo_path,
    scope_digest,
    standing_label,
)
from agents_remember.application.knowledge_reader.paths import states_at
from agents_remember.application.knowledge_reader.selection import ReaderSelection
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex, Record, RecordStanding

MAX_BATCH_RECORD_IDS: Final = 25
MAX_RECORDS_PER_PAGE: Final = 20
DEFAULT_RECORDS_BUDGET_TOKENS: Final = 4650
STALE_ANCHOR_INLINE_CAP: Final = 10


def records_view(
    selection: ReaderSelection,
    record_ids: Sequence[str],
    *,
    continuation: str | None = None,
    budget_tokens: int = DEFAULT_RECORDS_BUDGET_TOKENS,
) -> dict[str, Any]:
    """Batch full-text lookup for ``record_ids`` without trimming statements."""

    cleaned_ids = _clean_ids(record_ids)
    if not cleaned_ids:
        return {
            "refusalCode": "invalid-input",
            "refusalDetail": "view='records' requires a non-empty `recordIds` list (up to 25 IDs)",
        }
    if len(cleaned_ids) > MAX_BATCH_RECORD_IDS:
        return {
            "refusalCode": "invalid-input",
            "refusalDetail": (
                f"view='records' accepts at most {MAX_BATCH_RECORD_IDS} recordIds per request "
                f"(got {len(cleaned_ids)}); split into batches of at most {MAX_BATCH_RECORD_IDS}"
            ),
        }

    digest = scope_digest({"tree": selection.index.state.key, "ids": list(cleaned_ids)})
    start = decode_query_cursor(continuation, view="records", digest=digest)
    if isinstance(start, str):
        return {"refusalCode": "bad_continuation", "refusalDetail": start}
    if start >= len(cleaned_ids):
        return {
            "refusalCode": "bad_continuation",
            "refusalDetail": f"continuation offset {start} is past the {len(cleaned_ids)} requested IDs",
        }

    index = selection.index
    found_records: dict[str, Record] = {}
    missing_ids: list[str] = []
    for rid in cleaned_ids:
        rec = index.record(rid).value
        if rec is None:
            missing_ids.append(rid)
        else:
            found_records[rid] = rec

    inv_ids = [rid for rid, rec in found_records.items() if rec.kind == "invariant"]
    currentness, currentness_problem = (
        states_at(selection, inv_ids, []) if inv_ids else (None, None)
    )
    standings = index.standings(list(found_records)).value if found_records else {}

    ordered_items = [
        _format_record(
            index,
            found_records[rid],
            standings.get(rid),
            currentness,
        )
        for rid in cleaned_ids
        if rid in found_records
    ]

    code_pairing = _code_pairing_block(selection, currentness_problem)

    def render_page(page_items: Sequence[dict[str, Any]], has_more: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "records": list(page_items),
            "counts": {
                "requested": len(cleaned_ids),
                "found": len(ordered_items),
                "returned": len(page_items),
                "remaining": max(0, len(ordered_items) - start - len(page_items)),
                "missing": len(missing_ids),
            },
            "codePairing": code_pairing,
            "enumerationComplete": not has_more,
        }
        if missing_ids:
            body["missing"] = missing_ids
        if has_more:
            body["continuation"] = encode_query_cursor("records", start + len(page_items), digest)
        return body

    chosen, _ = fit_slice_in_budget(
        ordered_items,
        start,
        render_page,
        budget_tokens=budget_tokens,
        max_items=MAX_RECORDS_PER_PAGE,
    )
    has_more = start + len(chosen) < len(ordered_items)
    return render_page(chosen, has_more)


def _clean_ids(record_ids: Sequence[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in record_ids:
        if not isinstance(raw, str):
            continue
        rid = raw.strip()
        if rid and rid not in seen:
            seen.add(rid)
            cleaned.append(rid)
    return tuple(cleaned)


def _format_record(
    index: KnowledgeIndex,
    record: Record,
    standing: RecordStanding | None,
    currentness: Currentness | None,
) -> dict[str, Any]:
    if record.kind == "invariant":
        return _format_invariant(index, record, standing, currentness)
    row: dict[str, Any] = {
        "id": record.id,
        "kind": record.kind,
        "standing": standing_label(standing),
    }
    title = record.document.get("title")
    if isinstance(title, str) and title.strip():
        row["title"] = " ".join(title.split())
    row.update(_kind_fields(index, record))
    return row


def _kind_fields(index: KnowledgeIndex, record: Record) -> dict[str, Any]:
    doc = record.document
    if record.kind == "family":
        knowledge = index.family(record.id).value
        fields: dict[str, Any] = {
            "guarantee": str(doc.get("guarantee") or ""),
            "members": list(knowledge.members),
        }
        if knowledge.routes:
            fields["routes"] = list(knowledge.routes)
        return fields
    if record.kind == "weakness":
        return _weakness_fields(doc)
    if record.kind == "dependent":
        return _dependent_fields(doc)
    if record.kind == "decision":
        return _decision_fields(doc)
    return {
        key: doc[key]
        for key in ("statement", "applicability", "conditions", "exclusions")
        if key in doc and doc[key] is not None
    }


def _weakness_fields(doc: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "weaknessKind": doc.get("kind"),
        "state": doc.get("state"),
        "statement": str(doc.get("statement") or ""),
    }
    anchor = doc.get("anchor")
    if isinstance(anchor, Mapping) and isinstance(anchor.get("path"), str):
        fields["path"] = normalize_repo_path(anchor["path"])
    return fields


def _dependent_fields(doc: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {"document": normalize_repo_path(str(doc.get("document") or ""))}
    confirmed = doc.get("confirmed")
    if isinstance(confirmed, Mapping):
        fields["confirmed"] = dict(confirmed)
    if doc.get("deferrals"):
        fields["deferrals"] = list(doc["deferrals"])
    return fields


def _decision_fields(doc: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "status": doc.get("status"),
        "context": str(doc.get("context") or ""),
    }
    if doc.get("consequences"):
        fields["consequences"] = list(doc["consequences"])
    if doc.get("alternatives"):
        fields["alternatives"] = list(doc["alternatives"])
    return fields


def _format_invariant(
    index: KnowledgeIndex,
    record: Record,
    standing: RecordStanding | None,
    currentness: Currentness | None,
) -> dict[str, Any]:
    doc = record.document
    knowledge = index.invariant(record.id).value
    anchors = anchor_counts(
        record.id,
        currentness,
        fallback_total=len(knowledge.realizations) + len(knowledge.proofs),
    )
    row: dict[str, Any] = {
        "id": record.id,
        "kind": "invariant",
        "standing": standing_label(standing, anchors),
        "statement": str(doc.get("statement") or ""),
    }
    title = doc.get("title")
    if isinstance(title, str) and title.strip():
        row["title"] = " ".join(title.split())
    if doc.get("applicability"):
        row["applicability"] = doc["applicability"]
    row["conditions"] = list(doc.get("conditions") or ())
    row["exclusions"] = list(doc.get("exclusions") or ())
    row["realizedIn"] = _entry_paths(knowledge.realizations)
    row["proofs"] = _entry_paths(knowledge.proofs)
    row["families"] = _family_refs(index, knowledge.families)

    if anchors["stale"] > 0 and currentness is not None:
        stale_entries = _stale_anchors(record.id, currentness)
        row["staleAnchors"] = {
            "count": anchors["stale"],
            "entries": stale_entries[:STALE_ANCHOR_INLINE_CAP],
        }
    return row


def _entry_paths(entries: Sequence[Entry]) -> list[str]:
    by_path: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        path = normalize_repo_path(entry.path)
        symbol = _entry_symbol(entry)
        if symbol and symbol not in by_path[path]:
            by_path[path].append(symbol)
        else:
            by_path.setdefault(path, [])
    result: list[str] = []
    for path in sorted(by_path):
        symbols = by_path[path]
        result.append(f"{path}::{', '.join(symbols)}" if symbols else path)
    return result


def _entry_symbol(entry: Entry) -> str | None:
    anchor = entry.document.get("anchor")
    if not isinstance(anchor, Mapping):
        return None
    locator = anchor.get("locator")
    if isinstance(locator, Mapping) and locator.get("kind") == "symbol":
        value = locator.get("name") or locator.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _family_refs(index: KnowledgeIndex, family_ids: Sequence[str]) -> list[str]:
    refs: list[str] = []
    for fam_id in sorted(family_ids):
        rec = index.record(fam_id).value
        if rec is None:
            refs.append(fam_id)
            continue
        title = rule_title(rec.document).text
        refs.append(f"{fam_id}: {title}" if title else fam_id)
    return refs


def _stale_anchors(invariant_id: str, currentness: Currentness) -> list[dict[str, Any]]:
    inv_state = currentness.of(invariant_id)
    if inv_state is None:
        return []
    stale: list[dict[str, Any]] = []
    for entry in inv_state.entries:
        if entry.state in ("stale", "moved_or_absent"):
            item: dict[str, Any] = {
                "id": entry.entry_id,
                "kind": entry.kind,
                "path": normalize_repo_path(entry.path),
                "state": entry.state,
            }
            if entry.reason:
                item["reason"] = entry.reason
            stale.append(item)
    return stale


def _code_pairing_block(selection: ReaderSelection, problem: str | None) -> dict[str, Any]:
    if selection.code_tree is None:
        return {"paired": False, "reason": problem or "no code tree was resolved"}
    return {
        "paired": True,
        "codeTreeId": selection.code_tree.tree[:12],
        "repositoryRoot": selection.code_tree.repository.as_posix(),
    }


__all__ = [
    "DEFAULT_RECORDS_BUDGET_TOKENS",
    "MAX_BATCH_RECORD_IDS",
    "MAX_RECORDS_PER_PAGE",
    "STALE_ANCHOR_INLINE_CAP",
    "records_view",
]
