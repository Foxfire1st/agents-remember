"""MIK-R53 rule 8's ``touched_record`` item kind: changed decisions and facet records.

**Registration (MIK-R08 rule 1).**

* kind: ``touched_record``;
* subject: the record's ID (a decision, incident, assumption, limitation, failure mode,
  scenario, diagnostic or term);
* facts: the record's revision and status on each side (``baseRevision``, ``revision``,
  ``baseStatus``, ``status``);
* satisfying row: the leaf's ``record`` row about it at the candidate revision -- ``changed``
  exactly when the revision is higher than in the base, ``retired`` exactly when the status
  became ``retired``, otherwise ``no_impact``;
* owner: MIK-R53.

The package ``__init__`` imports this module, so the kind is registered whenever the worklist is.

**Raised.** Every such record of the memory base whose JSON or Markdown file differs in the
candidate, by exact bytes. A record that is new in the candidate raises nothing, and neither does
a difference that is only the ``status: under_reconsideration`` the writer set for the leaf's
own ``raise`` row. A record absent from the candidate raises nothing: a facet is retired, never
deleted, and a deletion is structural work other rules own.
"""

from __future__ import annotations

import json
from typing import Any, Final

from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
    subject_row,
)
from agents_remember.models.knowledge_files.history import (
    RECORD_ROW_PREFIXES,
    RECORD_ROW_SUBJECT_PATTERN,
    ReconsiderationRow,
)

__all__ = ["TOUCHED_RECORD_KIND", "TOUCHED_RECORD_PREFIXES", "touched_record_items"]

ITEM_KIND: Final = "touched_record"
TOUCHED_RECORD_PREFIXES: Final = RECORD_ROW_PREFIXES
# The spelling of the writer's mark, canonical in
# ``application/knowledge_writer/reconsideration.py``; repeated here so this module stays
# dependency-free of the writer.
_UNDER_RECONSIDERATION: Final = "under_reconsideration"
_SUBJECT_PATTERN: Final = RECORD_ROW_SUBJECT_PATTERN

TOUCHED_RECORD_KIND: Final = register_item_kind(
    ItemKind(
        name=ITEM_KIND,
        subject="decision or facet record (its record ID)",
        subject_pattern=_SUBJECT_PATTERN,
        facts=("baseRevision", "revision", "baseStatus", "status"),
        satisfying_row=(
            "the leaf's record row with this subject at the candidate revision "
            "(facts.revision): retired exactly when the status became retired, at any "
            "revision; changed exactly when the revision is higher than in the base "
            "(facts.baseRevision) and the status is not newly retired; otherwise "
            "no_impact (MIK-R53 rule 8)"
        ),
        row_lookup=subject_row("record"),
        owner="MIK-R53",
    )
)


def _markdown_path(json_path: str) -> str:
    return f"{json_path.removesuffix('.json')}.md"


def _leaf_raise_row(candidate: KnowledgeSide, record_id: str, owner: str | None) -> bool:
    """Whether the current owner's open history files hold the leaf's ``raise`` row about ``record_id``.

    Only the current leaf's own open raise exempts: a foreign owner's row, or a row in a
    closed file, never hides another change (MIK-R53 rule 8 exception). The merged per-owner
    aggregate is not consulted: a closed attempt's raise survives the merge into a later open
    unrelated attempt, and must not exempt there.
    """

    if owner is None:
        return False
    prefix = f"reconsider:{record_id}#"
    for history in candidate.parsed.history_files.values():
        if history.owner_id != owner or history.closed:
            continue
        for row in history.rows:
            if (
                isinstance(row, ReconsiderationRow)
                and row.disposition == "raise"
                and row.subject.startswith(prefix)
            ):
                return True
    return False


def _raise_mark_only(base_raw: bytes, candidate_raw: bytes) -> bool:
    """Whether the JSON differs only by the writer's ``under_reconsideration`` mark."""

    try:
        base = json.loads(base_raw)
        edited = json.loads(candidate_raw)
    except ValueError:
        return False
    if not isinstance(base, dict) or not isinstance(edited, dict):
        return False
    if set(base) != set(edited):
        return False
    if edited.get("status") != _UNDER_RECONSIDERATION:
        return False
    return all(base[key] == edited[key] for key in base if key != "status")


def touched_record_items(
    base: KnowledgeSide, candidate: KnowledgeSide, owner: str | None = None
) -> list[dict[str, Any]]:
    """One ``touched_record`` item per base decision/facet record whose files differ in K_C.

    ``owner`` is the current leaf's ID: only its own open ``raise`` row exempts a status-only
    mark change. Without it no raise exempts (fail-closed).
    """

    subjects = sorted(
        record_id
        for record_id, indexed in base.parsed.records.items()
        if indexed.record.id.split("-", 1)[0] in TOUCHED_RECORD_PREFIXES
    )
    items = []
    for record_id in subjects:
        base_indexed = base.parsed.records[record_id]
        found = candidate.parsed.records.get(record_id)
        if found is None:
            continue  # a deletion is structural work other rules own
        base_located = base.record_file(record_id)
        candidate_located = candidate.record_file(record_id)
        if base_located is None or candidate_located is None:  # pragma: no cover
            continue
        base_path, base_raw = base_located
        candidate_path, candidate_raw = candidate_located
        base_md = base.files.get(_markdown_path(base_path))
        candidate_md = candidate.files.get(_markdown_path(candidate_path))
        if base_raw == candidate_raw and base_md == candidate_md:
            continue
        if (
            base_md == candidate_md
            and _raise_mark_only(base_raw, candidate_raw)
            and _leaf_raise_row(candidate, record_id, owner)
        ):
            continue  # the leaf's own open raise mark for its raise row raises nothing
        base_record = base_indexed.record
        record = found.record
        items.append(
            {
                "id": item_id(ITEM_KIND, record_id, [base_record.revision, record.revision]),
                "kind": ITEM_KIND,
                "subject": record_id,
                "facts": {
                    "baseRevision": base_record.revision,
                    "revision": record.revision,
                    "baseStatus": base_record.status,
                    "status": record.status,
                },
            }
        )
    return items
