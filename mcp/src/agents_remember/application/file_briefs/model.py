"""The parts of one file's brief, in the order the brief shows them.

A brief is what the memory says about one source file: the file's own rules with everything that
formed, proves or burdens them, the weaknesses and decisions at the file that belong to no rule,
the documents that stand on the file, and the families its rules belong to. These classes hold the
parts; :mod:`.facts` fills them from one memory tree and :mod:`.render_brief` and
:mod:`.short_answer` spell them. Nothing here reads or writes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleTitle:
    """A rule's short name: its recorded ``title``, or the first words of its statement."""

    text: str
    given: bool
    """``False`` when no title is recorded and the text is the statement's first words."""


@dataclass(frozen=True)
class Standing:
    """A rule's state and the writer of its revision, as the records hold them."""

    state: str
    writer: str


@dataclass(frozen=True)
class Place:
    """One place a rule is realized: a file and the symbol or lines of the anchor."""

    path: str
    symbol: str
    role: str | None
    rationale: str | None
    current: bool | None
    """Whether the currentness check finds the anchor current; ``None`` when it was not measured."""


@dataclass(frozen=True)
class ProofLine:
    """One test recorded as proving a rule."""

    path: str
    symbol: str
    facet: str | None


@dataclass(frozen=True)
class DecisionLine:
    """A decision with what it chose and what it rejected, and the reason of each rejection."""

    id: str
    relation: str
    chosen: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    record_path: str


@dataclass(frozen=True)
class WeaknessLine:
    """An open weakness of the code: its kind, the symbol it sits at and its one sentence."""

    id: str
    kind: str
    symbol: str
    statement: str
    record_path: str


@dataclass(frozen=True)
class FacetLine:
    """A limitation, failure mode or assumption whose record links to a rule."""

    id: str
    kind: str
    relation: str
    text: str
    record_path: str


@dataclass(frozen=True)
class RuleHead:
    """The one line of a rule's block that names it and counts its proof and anchors."""

    id: str
    title: RuleTitle
    standing: Standing
    proofs: int
    anchors_current: int
    anchors_total: int
    anchors_measured: bool

    @property
    def moved(self) -> bool:
        """An anchor in this file that the currentness check no longer finds current."""

        return self.anchors_measured and self.anchors_current < self.anchors_total


@dataclass(frozen=True)
class RuleBlock:
    """One of the file's own rules with everything the memory links to it."""

    head: RuleHead
    statement: str
    applicability: str | None
    conditions: tuple[str, ...]
    exclusions: tuple[str, ...]
    here: tuple[Place, ...]
    elsewhere: tuple[Place, ...]
    proofs: tuple[ProofLine, ...]
    decisions: tuple[DecisionLine, ...]
    weaknesses: tuple[WeaknessLine, ...]
    weaknesses_named: tuple[str, ...]
    """Weaknesses placed with an earlier rule realized at the same symbol, named here by id."""
    facets: tuple[FacetLine, ...]
    families: tuple[str, ...]
    record_path: str


@dataclass(frozen=True)
class OtherRule:
    """A family's rule that is not one of this file's own, with the files it lives in."""

    id: str
    title: RuleTitle
    statement: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class FamilyBlock:
    """A family one of the file's rules belongs to, with its other rules."""

    id: str
    title: str
    guarantee: str
    here: tuple[str, ...]
    others: tuple[OtherRule, ...]


@dataclass(frozen=True)
class StandingDocument:
    """A document that stands on the file: a change of the file owes it a confirmation."""

    id: str
    document: str
    record_path: str


@dataclass(frozen=True)
class BriefCounts:
    rules: int
    weaknesses: int
    documents: int
    families: int
    retired: int


@dataclass(frozen=True)
class BriefHead:
    """What the brief was made from and what it holds."""

    path: str
    memory_tree: str
    code_commit: str | None
    code_state: str
    card_path: str | None
    counts: BriefCounts


@dataclass(frozen=True)
class FileBrief:
    """Everything the memory says about one source file, in the brief's order."""

    head: BriefHead
    purpose: str | None
    rules: tuple[RuleBlock, ...]
    loose_weaknesses: tuple[WeaknessLine, ...]
    loose_decisions: tuple[DecisionLine, ...]
    documents: tuple[StandingDocument, ...]
    families: tuple[FamilyBlock, ...]
    retired_left_out: int


__all__ = [
    "BriefCounts",
    "BriefHead",
    "DecisionLine",
    "FacetLine",
    "FamilyBlock",
    "FileBrief",
    "OtherRule",
    "Place",
    "ProofLine",
    "RuleBlock",
    "RuleHead",
    "RuleTitle",
    "Standing",
    "StandingDocument",
    "WeaknessLine",
]
