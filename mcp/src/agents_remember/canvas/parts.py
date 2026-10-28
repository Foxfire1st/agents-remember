"""The page's parts and the claims nested in them (MIK-R104 rules 1, 4; MIK-R103 rule 2).

A published explanation page is made of *parts*: static ``data-part`` elements, each with an id the
page's own text carries. Every evidence mark inside a part belongs to that part -- to the innermost
one when parts nest -- and its value is the id of a claim in the revision's claim map
(:class:`~agents_remember.models.knowledge.review_evidence.EvidenceClaim`), never a basis reference.

The product derives the map; the explainer never writes it. Readiness checks it before a revision
becomes ready (:mod:`agents_remember.canvas.readiness`), and per-part staleness reads it afterwards
(MIK-R187 rule 8). One derivation serves both, so a page cannot be ready under one reading of its
parts and stale under another.

A page without parts is one part holding every claim: its staleness is whole-page. The implicit part
is named :data:`WHOLE_PAGE_PART` so the trusted list can name it, and no page may declare that id
itself.

Part ids are bounded here once -- at most :data:`MAX_PART_ID_LENGTH` characters from ``[a-z0-9-]``,
starting with a letter or digit -- because the same bound is enforced on the host's ``stale-parts``
message (:mod:`agents_remember.canvas.protocol`) and by readiness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

__all__ = [
    "MAX_PART_ID_LENGTH",
    "PARTS_ATTRIBUTE",
    "PART_ATTRIBUTE",
    "PART_TITLE_ATTRIBUTE",
    "WHOLE_PAGE_PART",
    "PartEntry",
    "PartMap",
    "derive_part_map",
    "part_id_problem",
]

PART_ATTRIBUTE = "data-part"
PART_TITLE_ATTRIBUTE = "data-part-title"
PARTS_ATTRIBUTE = "data-evidence-claim"
WHOLE_PAGE_PART = "page"
MAX_PART_ID_LENGTH = 64

_PART_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
#: Tags that never enclose anything, so they never open a part even when they carry the attribute.
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def part_id_problem(part_id: object) -> str | None:
    """Why one part id is not admissible, or ``None`` when it is.

    The one spelling of the bound: readiness, the protocol's ``stale-parts`` message and the part map
    all read it from here, so a page and a message cannot disagree about which ids exist.
    """

    if not isinstance(part_id, str) or not part_id:
        return "a part id is a non-empty string"
    if len(part_id) > MAX_PART_ID_LENGTH:
        return (
            f"the part id {part_id!r} is {len(part_id)} characters, beyond {MAX_PART_ID_LENGTH}: "
            f"shorten it to {MAX_PART_ID_LENGTH} characters or fewer in its data-part attribute"
        )
    if not _PART_ID.match(part_id):
        return (
            f"the part id {part_id!r} is not written from [a-z0-9-] starting with a letter or digit"
        )
    if part_id == WHOLE_PAGE_PART:
        return f"the part id {WHOLE_PAGE_PART!r} is reserved for a page that declares no parts"
    return None


@dataclass(frozen=True)
class PartEntry:
    """One part: its id, the title a reader sees in the trusted list, and its claims."""

    part_id: str
    title: str
    claims: tuple[str, ...] = ()

    def as_record(self) -> dict[str, object]:
        """The plain shape a manifest or a read route carries."""

        return {"part": self.part_id, "title": self.title, "claims": list(self.claims)}


@dataclass(frozen=True)
class PartMap:
    """Every part of one page in document order, with what the derivation refused."""

    parts: tuple[PartEntry, ...] = ()
    implicit: bool = False
    problems: tuple[str, ...] = ()
    claims_outside_parts: tuple[str, ...] = field(default=())

    def part_of(self, claim_id: str) -> str | None:
        """The innermost part one claim belongs to, or ``None`` when it belongs to none."""

        for entry in self.parts:
            if claim_id in entry.claims:
                return entry.part_id
        return None

    def claims(self) -> tuple[str, ...]:
        """Every claim id the map carries, in document order, each once."""

        seen: list[str] = []
        for entry in self.parts:
            for claim_id in entry.claims:
                if claim_id not in seen:
                    seen.append(claim_id)
        return tuple(seen)

    def as_record(self) -> dict[str, object]:
        """The plain shape the manifest stores beside the claim map."""

        return {
            "implicit": self.implicit,
            "parts": [{**entry.as_record(), "claims": list(entry.claims)} for entry in self.parts],
        }

    def titles(self) -> dict[str, str]:
        """Every part's title by its id."""

        return {entry.part_id: entry.title for entry in self.parts}


class _PartWalker(HTMLParser):
    """One pass over the page's own document: parts, their nesting, and the marks inside them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # Each open part is (id, title, [claim ids]). The stack is the nesting.
        self._open: list[tuple[str, str, list[str]]] = []
        # Full stack of open non-void HTML elements: (tag_name, opened_part_or_none).
        self._elements: list[tuple[str, tuple[str, str, list[str]] | None]] = []
        self.parts: list[PartEntry] = []
        self.open_order: list[str] = []
        self.problems: list[str] = []
        self.outside: list[str] = []
        self.saw_part = False
        self._titles: dict[str, str] = {}

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(_tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # A self-closing element opens its part, records any same-element claim, and closes at once.
        opened = self._start(tag, attrs)
        if opened:
            self._close(tag)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        """Read one start tag; answer whether it pushed a non-void element to close later."""

        tag_name = tag.lower()
        values = {name.lower(): value for name, value in attrs if value is not None}
        part_entry = self._open_part(values)
        claim = values.get(PARTS_ATTRIBUTE)
        if claim is not None:
            claim = claim.strip()
            if self._open:
                self._open[-1][2].append(claim)
            else:
                self.outside.append(claim)
        if tag_name in _VOID:
            if part_entry is not None:
                self._finish_part(self._open.pop())
            return False
        self._elements.append((tag_name, part_entry))
        return True

    def _open_part(self, values: dict[str, str]) -> tuple[str, str, list[str]] | None:
        part_id = values.get(PART_ATTRIBUTE)
        if part_id is None:
            return None
        self.saw_part = True
        part_id = part_id.strip()
        title = (values.get(PART_TITLE_ATTRIBUTE) or "").strip() or part_id
        problem = part_id_problem(part_id)
        if problem:
            self.problems.append(problem)
        if part_id in self._titles:
            self.problems.append(f"the part id {part_id!r} is declared twice")
        self._titles[part_id] = title
        self.open_order.append(part_id)
        entry: tuple[str, str, list[str]] = (part_id, title, [])
        self._open.append(entry)
        return entry

    def _finish_part(self, entry: tuple[str, str, list[str]]) -> None:
        part_id, title, claims = entry
        self.parts.append(PartEntry(part_id, title, tuple(dict.fromkeys(claims))))

    def _close(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in _VOID:
            return
        for index in range(len(self._elements) - 1, -1, -1):
            if self._elements[index][0] == tag_name:
                while len(self._elements) > index:
                    _closed_tag, closed_part = self._elements.pop()
                    if closed_part is not None and self._open:
                        self._finish_part(self._open.pop())
                return

    def handle_endtag(self, tag: str) -> None:
        self._close(tag)

    def close(self) -> None:
        super().close()
        # A page that never closes a part still has it; document order puts the outermost last.
        while self._elements:
            _closed_tag, closed_part = self._elements.pop()
            if closed_part is not None and self._open:
                self._finish_part(self._open.pop())
        while self._open:
            self._finish_part(self._open.pop())


def derive_part_map(document: str) -> PartMap:
    """Derive one page's part map from its own document text.

    The map is document-ordered, innermost nesting resolved, and a claim belongs to exactly one part:
    the innermost part that contains it. A page with no ``data-part`` element is one implicit part
    holding every claim. The problems are the three readiness checks' raw material: an inadmissible or
    repeated part id, and a claim that lies outside every part on a page that declares parts.
    """

    walker = _PartWalker()
    walker.feed(document)
    walker.close()
    problems = list(walker.problems)
    if not walker.saw_part:
        claims = tuple(dict.fromkeys(walker.outside))
        return PartMap(
            parts=(PartEntry(WHOLE_PAGE_PART, "the page", claims),),
            implicit=True,
            problems=tuple(problems),
        )
    ordered = _document_order(walker.parts, walker.open_order)
    if walker.outside:
        problems.append(
            "the page marks claim(s) outside every part: " + ", ".join(sorted(set(walker.outside)))
        )
    _check_cross_part_claims(ordered, problems)
    return PartMap(
        parts=ordered,
        implicit=False,
        problems=tuple(problems),
        claims_outside_parts=tuple(dict.fromkeys(walker.outside)),
    )


def _check_cross_part_claims(ordered: tuple[PartEntry, ...], problems: list[str]) -> None:
    seen_claims: dict[str, str] = {}
    for entry in ordered:
        for claim_id in entry.claims:
            owner = seen_claims.get(claim_id)
            if owner is not None and owner != entry.part_id:
                problems.append(
                    f"claim {claim_id!r} belongs to both part {owner!r} and part "
                    f"{entry.part_id!r}; every claim belongs to one part only"
                )
            else:
                seen_claims[claim_id] = entry.part_id


def _document_order(parts: list[PartEntry], open_order: list[str]) -> tuple[PartEntry, ...]:
    """One entry per id, in the order the page opens them.

    The walker appends a part when it closes, so a nested part arrives before its parent; the open
    order is what the page actually reads as. A part that never closed is still in the open order.
    """

    by_id: dict[str, PartEntry] = {}
    for entry in parts:
        by_id.setdefault(entry.part_id, entry)
    return tuple(by_id[part_id] for part_id in open_order if part_id in by_id)
