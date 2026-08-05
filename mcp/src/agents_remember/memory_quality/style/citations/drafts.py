"""Describe migration declines and their next action.

Every decline code names one reason the citation cannot be converted without invention and
supplies the edit that clears it. All are tier-2 curator work except ``anchor_absent``:
an anchor found nowhere may mean the claim changed and can require tier-3 review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.memory_quality.style.citations import model, old_form, repair, work_order
from agents_remember.memory_quality.style.citations.editing import Site

TABLE_ROW = "table-row"
PROSE = "prose"

ANCHOR_ABSENT_FROM_ROW = "anchor_absent_from_row"
ANCHOR_CHOICE_NEEDED = "anchor_choice_needed"
CITATIONS_NOTE_DROPPED = "citations_note_dropped"
SOURCE_MISSING = "source_missing"
SOURCE_NOT_A_PATH = "source_not_a_path"
SOURCE_UNRESOLVABLE = "source_unresolvable"
SOURCE_HOLDS_NO_ANCHOR = "source_holds_no_anchor"
PROSE_PATH_UNKNOWN = "prose_path_unknown"
PROSE_ANCHOR_MISSING = "prose_anchor_missing"
PROSE_ANCHOR_NOT_IN_CITED_RANGE = "prose_anchor_not_in_cited_range"
ANCHOR_NOT_THE_SUBJECT = "anchor_not_the_subject"

ACTIONS = {
    ANCHOR_ABSENT_FROM_ROW: (
        "Read the cited file and name the construct this claim is about, then write it in "
        "the Anchor column. The row names no backticked identifier, no `#`-prefixed heading "
        "and no quoted literal anywhere, so there is nothing here to promote."
    ),
    ANCHOR_CHOICE_NEEDED: (
        "Name every construct the claim is about in the Anchor column -- that may be ALL "
        "of the candidates below. Anchors pool: a row may carry several, each verified against "
        "one of its ranges, so a claim about a pair or a quartet keeps every one. Do not "
        "pick a single anchor to satisfy the column; that silently drops the rest of the "
        "claim, which is the failure the pooling rule exists to prevent. Only a reader can "
        "say which candidates are the subject, and picking by position or by similarity is "
        "how a claim gets repointed at code that does not do what it says."
    ),
    repair.ANCHOR_AMBIGUOUS: (
        "Read the candidate locations below and cite the one the claim is about. The anchor "
        "occurs in more than one place and no verified range picks between them."
    ),
    repair.ANCHOR_ABSENT: (
        "Read the CLAIM, not the pointer. The anchor exists nowhere in the code tree, which "
        "usually means the behaviour changed rather than a number going stale."
    ),
    CITATIONS_NOTE_DROPPED: (
        "The Citations cell holds a note rather than a range and the new format has no "
        "column for it. Fold what it says into the Finding sentence or drop it, then give "
        "the row an anchor and a source."
    ),
    SOURCE_MISSING: (
        "The row cites lines with no file. Name the file in the Source column as "
        "`path:start-end`, or delete the row: a range with no path denotes nothing."
    ),
    SOURCE_NOT_A_PATH: (
        "A URL IS NOT EVIDENCE, IT IS A POINTER. This cell names a URL, or no path at all, "
        "and `path:start-end` cannot express either -- nothing offline can check a link and "
        "the gate must never fetch one. Two honest destinations, whichever fits this row: "
        "(1) re-anchor it as a THIRD-PARTY BEHAVIOUR claim, quoting the dependency's own "
        "source as a double-quoted literal and carrying a version pin checked against "
        "`pyproject.toml`; or (2) move the URL into the Finding prose, where a pointer "
        "belongs, and drop the citation. Do not invent a `path:start-end` spelling for a URL."
    ),
    SOURCE_UNRESOLVABLE: (
        "The cited path names no file in either tree. Find where the file went and cite it, "
        "or delete the row if the material is gone."
    ),
    SOURCE_HOLDS_NO_ANCHOR: (
        "The row cites a file that holds none of its anchors, so no range can be generated "
        "for it. Give that file its own anchor or drop it from the Source list."
    ),
    PROSE_PATH_UNKNOWN: (
        "The citation names no file and this card declares no `path` metadata to fall back "
        "on. Write the path explicitly: `cit:([<anchor>], <path>:<start>-<end>)`."
    ),
    PROSE_ANCHOR_MISSING: (
        "The range has no anchor beside it, so nothing says what those lines contain. Write "
        "`cit:([<anchor>], <path>:<start>-<end>)` naming the construct the sentence is about."
    ),
    ANCHOR_NOT_THE_SUBJECT: (
        "Pick a better anchor. The range this row already carries covers most of the file "
        "and its anchor is only MENTIONED inside that range, never declared there -- which "
        "usually means the anchor names something the claim refers to in passing rather "
        "than its subject. If the claim really is about the whole module, anchor it on the "
        "module-level construct that carries it; if it is about one part, anchor it there "
        "and cite that part."
    ),
    PROSE_ANCHOR_NOT_IN_CITED_RANGE: (
        "The anchor is NOT inside the range this citation gives. Read the message below "
        "before deciding what that means: if it names lines in the card's own file that DO "
        "carry the anchor, the range is stale and the path is right -- rewrite the range. "
        "The stale range is the common case by a wide margin, so do not go looking for "
        "another file first. If the anchor occurs nowhere in that file, the cause is NOT "
        "proven: Search the tree for the exact anchor. A unique exact-name sighting can show "
        "a move or wrong path; ambiguous sightings or a possible rename need Tier 2 reading; "
        "no exact sighting can mean deletion or a stale claim and needs Tier 2 or Tier 3 "
        "judgement. Never invent a replacement merely to make the citation pass."
    ),
}

TIERS = {repair.ANCHOR_ABSENT: work_order.DEVELOPER_TIER}


@dataclass(frozen=True)
class Subject:
    """One document and the source file its own metadata table says it is about."""

    document: Path
    relative: str
    path: str
    repository: str


@dataclass
class Draft:
    """One citation being migrated: where it is, what it states, and why it was refused."""

    subject: Subject
    line: int
    kind: str
    site: Site
    text: str
    anchors: tuple[model.Anchor, ...] = ()
    paths: tuple[str, ...] = ()
    hint: old_form.Span | None = None
    raw_span: old_form.Span | None = None
    candidates: tuple[model.Anchor, ...] = ()
    decline: work_order.Item | None = None

    def refuse(
        self, code: str, detail: str, anchor: str | None = None, *, parsed: bool = False
    ) -> None:
        """Record why this citation stays for a curator. ``parsed`` marks a refusal that
        came from LOCATING the anchor, so it moves when the extent layer does."""
        self.decline = work_order.Item(
            document=self.subject.relative,
            line=self.line,
            kind=self.kind,
            code=code,
            tier=TIERS.get(code, work_order.CURATOR_TIER),
            action=ACTIONS[code],
            message=detail,
            subject=self.text,
            anchor=anchor or "; ".join(one.written for one in self.anchors) or None,
            source="; ".join(self.paths) or None,
            parser_dependent=parsed,
        )


@dataclass
class TableDraft:
    """One superseded table: where its header is, its rows, and the marker a padded row uses.

    Each row is ``(draft, finding, evidence)``. A row with ``None`` for its draft is the
    table's empty state -- no anchor and no source, which is not a citation and is not gated.
    It is padded to the new width, never deleted. ``evidence`` is what a row that cannot be
    converted keeps in its Source column, verbatim.
    """

    subject: Subject
    header: int
    rows: list[tuple[Draft | None, str, str]]
    marker: str


@dataclass
class Result:
    """What one pass read, converted and declined."""

    documents: int = 0
    tables: int = 0
    rows: int = 0
    placeholders: int = 0
    converted_rows: int = 0
    converted_tables: int = 0
    converted_prose: int = 0
    converted_literal: int = 0
    converted_declaration: int = 0
    converted_occurrence: int = 0
    prose_seen: int = 0
    wrapped: int = 0
    written: int = 0
    remaining: int = 0
    hint_would_have_helped: int = 0
    declined: list[work_order.Item] = field(default_factory=list)
    subjects: dict[str, str] = field(default_factory=dict)
