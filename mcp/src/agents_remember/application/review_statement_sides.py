"""Pane 1's recorded content: one comparison item's two statement sides and its field rows.

**Scope.** This module owns exactly three projections of a comparison's own items -- the statement
operand each side recorded, the essential conditions each side recorded, and the mechanical field
rows the comparison itself reported. It reads a :class:`KnowledgeDiffItem` the shipped comparison
already produced and renders it into the vocabulary :mod:`agents_remember.models.knowledge.review`
declares; it selects nothing, compares nothing, re-reads no snapshot and stores nothing.

**Every side's state is read, never inferred from an empty string.** A side the comparison holds no
record for is ``absent`` and says so; a side whose record exists and published no statement is
``unresolved`` and says so. The two are different facts about two different snapshots, and text that
happened to be empty could not tell them apart, so neither ever becomes an empty operand a reader
could take for a recorded empty document (ICR-R06@v1, Failure And Recovery Behavior).

**A field row's ``None`` is a statement about absence, and that is why a structured value carries a
reason.** ``ReviewFieldChange`` declares that ``None`` on either value *is* the recorded fact that
the field was absent on that side. A record can also carry a field whose value is structured rather
than text -- ``provenance`` is the shipped example, and it is one of the nine fields the comparison
projects and compares. Reporting that value as ``None`` would state an absence the snapshot does not
hold, and on a field the comparison reports as *changed* it would state two of them at once; so each
side is carried as the pane's own text projection of the value it really holds -- canonical compact
JSON, clearly marked as a rendering -- and the two sides of a changed structured field stay as
distinguishable as the comparison says they are. This is not decoration: it is the difference
between "nothing was recorded here" and "something was recorded here, and here it is as text", which
ICR-R06@v1's Required Behavior keeps distinct.

**Why this is its own module.** The review adapter is over the repository's soft file-size rail, and
ICR-R06@v1's Scope asks a leaf that touches a responsibility inside it to move that responsibility
out before adding behavior. The statement-side contract is that responsibility here: one
implementation, called by :mod:`agents_remember.application.knowledge_review`, which stays the
adapter that resolves, calls and assembles.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.diff import KnowledgeDiffItem
from agents_remember.models.knowledge.read import ReadItem
from agents_remember.models.knowledge.review import ReviewFieldChange, ReviewSideContent

__all__ = [
    "STRUCTURED_VALUE_LEAD",
    "STRUCTURED_VALUE_TAIL",
    "field_changes",
    "field_text",
    "read_side",
    "side_conditions",
    "side_content",
    "structured_value_text",
]

# What a field row shows where a record really carries a value and the value is not text. It is
# carried in the value slot because that slot's ``None`` already means "the field was absent there"
# (``ReviewFieldChange``'s own contract), so a rendering of the value is the only thing left that
# states the truth without inventing an absence. The note brackets the projection because the slot
# holds text and a reader must be able to tell the pane's rendering of a value from a value an author
# wrote.
STRUCTURED_VALUE_LEAD = "<recorded as a structured value, rendered as compact JSON: "
STRUCTURED_VALUE_TAIL = ">"
# Appended when a projection does not fit the room a field row has. The projection is then visibly
# incomplete rather than silently shortened, and a value larger than its row can carry never raises
# on the way to a reader.
STRUCTURED_VALUE_TRUNCATION = "…<truncated>"


def structured_value_text(value: Mapping[str, object]) -> str:
    """One structured value's canonical text projection, marked as this pane's rendering of it.

    Canonical because the two sides of a changed field are read against each other: one key order
    (``sort_keys``), one separator set, and a non-text leaf rendered by its own ``str`` so the
    projection is a pure function of the stored value. Every token in it is the value's own -- this
    invents no content and resolves no reference. The projection is bounded by the row's own declared
    limit, and a cut projection says so.
    """

    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    room = PROSE_MAX_LENGTH - len(STRUCTURED_VALUE_LEAD) - len(STRUCTURED_VALUE_TAIL)
    if len(rendered) > room:
        rendered = rendered[: room - len(STRUCTURED_VALUE_TRUNCATION)] + STRUCTURED_VALUE_TRUNCATION
    return f"{STRUCTURED_VALUE_LEAD}{rendered}{STRUCTURED_VALUE_TAIL}"


def side_content(item: KnowledgeDiffItem | None, side: str) -> ReviewSideContent:
    """One side's statement operand, or the named state that says why there is none.

    Three outcomes and no fourth: the side holds no record for the reviewed subject (``absent``, and
    an addition or a removal is exactly this), the side holds the record and published no statement
    (``unresolved``, which is not an empty statement), or the side holds the recorded statement
    (``present``, carrying the text itself). ``side`` is ``"before"`` or ``"after"`` and is the only
    thing that decides which operand of the item is read.
    """

    read_item = read_side(item, side)
    if read_item is None:
        return ReviewSideContent(
            state="absent",
            language="text",
            detail=f"the {side} snapshot selected no record for the reviewed subject",
        )
    if read_item.statement is None:
        return ReviewSideContent(
            state="unresolved",
            language="text",
            detail=(
                f"the {side} snapshot holds the record but published no statement for it; the "
                "operand is unresolved rather than an empty statement"
            ),
        )
    return ReviewSideContent(
        state="present",
        text=read_item.statement,
        language="text",
        detail=f"the {side} snapshot's recorded statement for this revision",
    )


def side_conditions(item: KnowledgeDiffItem | None, side: str) -> tuple[str, ...]:
    """The essential conditions one side recorded, empty exactly when that side recorded none."""

    read_item = read_side(item, side)
    return () if read_item is None else tuple(read_item.essential_conditions)


def read_side(item: KnowledgeDiffItem | None, side: str) -> ReadItem | None:
    """The item's own payload for one side, or ``None`` when that side holds no record."""

    if item is None:
        return None
    return item.before if side == "before" else item.after


def field_changes(items: Sequence[KnowledgeDiffItem]) -> tuple[ReviewFieldChange, ...]:
    """Every field transition the comparison reported, one row per field, in the page's order.

    The roster is the comparison's own ``changed_fields`` and nothing else: this function neither
    widens it with fields that did not move nor drops a transition because one of its two values is
    absent. A one-sided *record* reports no field rows at all, and that is the comparison's own rule
    rather than an omission here -- it reports such a record through its coverage, because nine
    field rows would state nine differences where there is one absence.
    """

    return tuple(
        ReviewFieldChange(
            item_id=item.item_id,
            item_kind=item.kind,
            field=name,
            before_value=field_text(item.before, name),
            after_value=field_text(item.after, name),
        )
        for item in items
        for name in item.changed_fields
    )


def field_text(item: ReadItem | None, name: str) -> str | None:
    """One field's recorded value as text, or ``None`` exactly when the field was absent there.

    ``None`` is reserved for the two real absences -- the side holds no record at all, or the record
    it holds carries no value for this field -- because that is the fact ``ReviewFieldChange``'s own
    contract reads it as. A structured value is therefore *not* reported as ``None``: each side
    carries :func:`structured_value_text` of its own value, so a present value is never displayed as
    an absent one and two sides the comparison calls different never read as the same text. A tuple
    field joins into the text a reader can compare; an empty tuple is the empty string it is, which is
    a present-but-empty value and not an absence.
    """

    if item is None:
        return None
    value = getattr(item, name, None)
    if value is None:
        return None
    if isinstance(value, Mapping):
        return structured_value_text(value)
    if isinstance(value, tuple):
        return "; ".join(str(part) for part in value)
    return str(value)
