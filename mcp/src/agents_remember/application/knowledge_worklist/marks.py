"""The two worklist kinds the marks raise: a weakness whose code changed, and a changed document.

**MIK-R118 rule 7.** When a task changes the code a weakness is anchored to, its worklist holds one
row for that weakness: *does it still stand?* The row is answered by a ``record`` history row about
the weakness -- ``changed`` when the Curator moved the anchor with the code or closed it, the record
file differing in K_C. A weakness is never a finding and never refuses anything.

**MIK-R124 rule 5.** A task that changes a document that has a dependent record owes one item for
it, and only the confirmed record answers it: the writer's every write of a dependent is a
confirmation at the task's tip, so the ``changed`` ``record`` row about the dependent is the
answer. A ``no_impact`` row does not answer it.

* kinds: ``weakness_anchor`` (subject ``WKN-…``) and ``changed_document`` (subject ``DPD-…``);
* facts: the anchored file and the anchor's recorded content for a weakness; the document, its
  revision and who last confirmed it for a dependent;
* satisfying row: the leaf's ``record`` row about the record (MIK-R07);
* owner: MIK-R118 / MIK-R124.

The package ``__init__`` imports this module, so both kinds are registered whenever the worklist is.
An item is raised from the memory *base*: a weakness or dependent that this task itself records is
new, and raises nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
    subject_row,
)
from agents_remember.models.knowledge_files.ids import id_pattern
from agents_remember.models.knowledge_files.records import DependentRecord, WeaknessRecord

__all__ = ["changed_document_items", "mark_items", "weakness_anchor_items"]

WEAKNESS_ITEM_KIND: Final = "weakness_anchor"
DOCUMENT_ITEM_KIND: Final = "changed_document"

register_item_kind(
    ItemKind(
        name=WEAKNESS_ITEM_KIND,
        subject="weakness (its record ID)",
        subject_pattern=id_pattern("WKN"),
        facts=("path", "kind", "anchorContent", "question"),
        satisfying_row=(
            "the leaf's record row about the weakness (MIK-R07): changed when the record file "
            "differs in K_C -- the anchor moved with the code, or the weakness was closed -- "
            "and no_impact when it still stands where it stood"
        ),
        row_lookup=subject_row("record"),
        owner="MIK-R118",
        advisory=True,
    )
)
register_item_kind(
    ItemKind(
        name=DOCUMENT_ITEM_KIND,
        subject="document (its dependent record ID)",
        subject_pattern=id_pattern("DPD"),
        facts=("document", "revision", "confirmedTask", "confirmedCommit"),
        satisfying_row=(
            "the leaf's record row about the dependent, at the confirmed revision: the writer's "
            "every write of a dependent is a confirmation at the task's tip, so a changed row "
            "answers; a no_impact row does not (MIK-R124 rule 5)"
        ),
        row_lookup=subject_row("record"),
        owner="MIK-R124",
    )
)


def weakness_anchor_items(
    base: KnowledgeSide, changed_paths: Iterable[str]
) -> list[dict[str, Any]]:
    """One ``weakness_anchor`` item per *open* base weakness whose anchored file this task changed.

    A closed weakness is history: its cause is gone, so a change to the file it once sat in raises
    no fresh maintenance obligation (MIK-R118 rule 7).
    """

    changed = set(changed_paths)
    items = []
    for record_id in sorted(base.parsed.records):
        record = base.parsed.records[record_id].record
        if not isinstance(record, WeaknessRecord) or record.state != "open":
            continue
        path = record.anchor.path or ""
        if path not in changed:
            continue
        items.append(
            {
                "id": item_id(WEAKNESS_ITEM_KIND, record.id, [record.revision, path]),
                "kind": WEAKNESS_ITEM_KIND,
                "subject": record.id,
                "facts": {
                    "path": path,
                    "kind": record.kind,
                    "anchorContent": record.anchor.content,
                    "question": "does it still stand",
                },
            }
        )
    return items


def changed_document_items(
    base: KnowledgeSide,
    changed_paths: Iterable[str],
    *,
    candidate_files: Mapping[str, str] | None = None,
    renamed: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One ``changed_document`` item per base dependent whose document this task changed.

    ``candidate_files`` is C's own file map. A document C no longer holds is gone: its dependent
    record is removed with it, so no obligation is raised about the removed subject (MIK-R124
    rule 5). A document renamed from its recorded path is still the same document, so the item
    names the path it now stands at and the record is confirmed there -- identity follows the
    document, not the old path. Without ``candidate_files`` every recorded document is treated as
    still present, which is the pre-deletion behaviour of the caller that has no code side.
    """

    changed = set(changed_paths)
    moves = dict(renamed or {})
    items = []
    for record_id in sorted(base.parsed.records):
        record = base.parsed.records[record_id].record
        if not isinstance(record, DependentRecord):
            continue
        document = record.document
        if candidate_files is not None and document not in candidate_files:
            document = moves.get(document, "")
            if not document:
                continue
        if document not in changed:
            continue
        items.append(
            {
                "id": item_id(DOCUMENT_ITEM_KIND, record.id, [record.revision, document]),
                "kind": DOCUMENT_ITEM_KIND,
                "subject": record.id,
                "facts": {
                    "document": document,
                    "recordedDocument": record.document,
                    "revision": record.revision,
                    "confirmedTask": record.confirmed.task,
                    "confirmedCommit": record.confirmed.commit,
                },
            }
        )
    return items


def mark_items(
    base: KnowledgeSide,
    changed_paths: Iterable[str],
    *,
    candidate_files: Mapping[str, str] | None = None,
    renamed: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Both mark kinds' items for one run, each raised once."""

    return [
        *weakness_anchor_items(base, changed_paths),
        *changed_document_items(
            base, changed_paths, candidate_files=candidate_files, renamed=renamed
        ),
    ]
