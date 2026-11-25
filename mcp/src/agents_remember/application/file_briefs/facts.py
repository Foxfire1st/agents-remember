"""What one memory tree says about a source file, collected into a :class:`~.model.FileBrief`.

A :class:`BriefSource` is one memory tree's derived index together with the code tree its anchors
are checked at. It reads every weakness, dependent and decision once, keeps each rule's knowledge
after its first lookup, and asks the product's currentness check once for every rule it shows, so a
full writing of every brief of a tree costs one pass over the index.

Which files get a brief: every source file at which a live rule is realized or proved, an
open weakness is anchored, a decision is linked, or a document stands. A file with only a card gets
none. Retired rules are left out of a brief and counted.

State and writer come from the same index as the records. A rule with an unknown writer
displays ``unknown``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.application.file_briefs.model import (
    BriefCounts,
    BriefHead,
    DecisionLine,
    FacetLine,
    FamilyBlock,
    FileBrief,
    OtherRule,
    Place,
    ProofLine,
    RuleBlock,
    RuleHead,
    Standing,
    StandingDocument,
    WeaknessLine,
)
from agents_remember.application.file_briefs.ordering import (
    Placement,
    RulePlaces,
    WeaknessSite,
    brief_order,
    place_weaknesses,
)
from agents_remember.application.file_briefs.titles import rule_title
from agents_remember.application.knowledge_currentness import (
    CodeTree,
    Currentness,
    invariant_currentness,
)
from agents_remember.application.knowledge_currentness.surface import CURRENTNESS_FAILURES
from agents_remember.application.record_standing_answers import compact_writer
from agents_remember.memory.knowledge_index import (
    Entry,
    InvariantKnowledge,
    KnowledgeIndex,
    RecordStanding,
)
from agents_remember.memory.knowledge_index.record_standing import UNKNOWN_WRITER
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT

_RETIRED: Final = "retired"
_FACET_TEXT: Final[Mapping[str, str]] = {
    "limitation": "limited",
    "failure_mode": "failure",
    "assumption": "proposition",
}
_PURPOSE: Final = re.compile(r"^#{2,4} Purpose[ \t]*\n(.*?)(?=^#{1,4} |\Z)", re.S | re.M)


@dataclass(frozen=True)
class CodeSide:
    """The code tree a brief's anchors are checked at, and how a reader names it."""

    tree: CodeTree | None
    commit: str | None
    state: str
    """``commit`` (a clean checkout), ``working-tree`` (captured edits) or ``unmeasured``."""


def anchor_symbol(anchor: Mapping[str, Any] | None) -> str:
    """How an anchor is named in a brief: its symbol, its lines, or the whole file."""

    locator = (anchor or {}).get("locator") or {}
    if locator.get("name"):
        return str(locator["name"])
    if locator.get("kind") == "line_range":
        return f"lines {locator.get('start')}-{locator.get('end')}"
    return "whole file"


@dataclass
class BriefSource:
    """One memory tree's index and the code tree it is measured at, read once per writing."""

    index: KnowledgeIndex
    memory_root: Path
    code: CodeSide
    _knowledge: dict[str, InvariantKnowledge] = field(default_factory=dict)
    _standings: dict[str, RecordStanding] = field(default_factory=dict)
    _currentness: dict[str, Any] = field(default_factory=dict)
    _measured: set[str] = field(default_factory=set)
    _maps: _TreeMaps | None = None

    # -- the tree's maps, read once -------------------------------------------------------------

    @property
    def maps(self) -> _TreeMaps:
        if self._maps is None:
            self._maps = _TreeMaps.read(self.index)
        return self._maps

    @property
    def memory_tree(self) -> str:
        return self.index.state.key

    def knowledge(self, invariant: str) -> InvariantKnowledge:
        if invariant not in self._knowledge:
            self._knowledge[invariant] = self.index.invariant(invariant).value
        return self._knowledge[invariant]

    def live(self, record_id: str) -> bool:
        record = self.index.record(record_id).value
        return record is not None and record.status != _RETIRED

    # -- which files get a brief ----------------------------------------------------------------

    def brief_paths(self) -> list[str]:
        """Every source file a live rule, an open weakness, a decision or a document stands at."""

        paths: set[str] = set(self.maps.weaknesses) | set(self.maps.dependents)
        paths |= set(self.maps.decisions_at)
        for path in self.index.live_entry_paths_under(".").value:
            paths.add(path)
        return sorted(paths)

    # -- currentness, asked once per rule ------------------------------------------------------

    def measure(self, invariants: Iterable[str]) -> None:
        """Read standing and measure currentness in batches for rules not yet measured."""

        wanted = sorted(set(invariants) - self._measured)
        if not wanted:
            return
        self._standings.update(self.index.standings(wanted).value)
        self._measured.update(wanted)
        if self.code.tree is None:
            return
        try:
            measured: Currentness | None = invariant_currentness(self.code.tree, self.index, wanted)
        except CURRENTNESS_FAILURES:
            measured = None
        if measured is None or measured.problem is not None:
            return
        for one in measured.invariants:
            for entry in one.entries:
                self._currentness[entry.entry_id] = entry.state == "current"

    def measure_every_rule(self) -> None:
        """Measure every live rule of the tree in one call, ahead of a full writing."""

        self.measure(
            record.id
            for record in self.index.records_of_kind("invariant").value
            if record.status != _RETIRED
        )

    def measure_paths(self, paths: Iterable[str]) -> None:
        """Measure the rules of every file in ``paths`` in one call, ahead of writing their briefs."""

        rules: set[str] = set()
        for path in paths:
            at = self.index.entries_at_path(path).value
            rules.update(one.invariant for one in (*at.realizations, *at.proofs))
        self.measure(rules)

    def entry_current(self, entry_id: str) -> bool | None:
        return self._currentness.get(entry_id)

    # -- one brief -----------------------------------------------------------------------------

    def assemble(self, path: str) -> FileBrief | None:
        """The brief of ``path``, or ``None`` when the memory records nothing a brief shows there."""

        at = self.index.entries_at_path(path).value
        entries = (*at.realizations, *at.proofs)
        own, retired = self._own_and_retired(entries)
        weaknesses = self.maps.weaknesses.get(path, ())
        documents = self.maps.dependents.get(path, ())
        decisions_here = self.maps.decisions_at.get(path, ())
        if not (own or weaknesses or documents or decisions_here):
            return None
        self.measure(own)
        places = [self._rule_places(rule, entries) for rule in own]
        placement = place_weaknesses(places, [_site(one) for one in weaknesses])
        by_id = {one["id"]: one for one in weaknesses}
        rules = self._rule_blocks(path, places, placement, by_id)
        families = self._families(own)
        counts = BriefCounts(
            rules=len(rules),
            weaknesses=len(weaknesses),
            documents=len(documents),
            families=len(families),
            retired=len(retired),
        )
        return FileBrief(
            head=self._brief_head(path, counts),
            purpose=self._purpose(path),
            rules=rules,
            loose_weaknesses=tuple(_weakness_line(by_id[one]) for one in placement.loose),
            loose_decisions=self._loose_decisions(decisions_here, rules),
            documents=_standing_documents(documents),
            families=families,
            retired_left_out=len(retired),
        )

    def _own_and_retired(self, entries: Sequence[Entry]) -> tuple[list[str], list[str]]:
        """The live rules of the entries and the retired ones, each sorted by ID.

        A test file's own rules are the rules it proves: its entries are proofs.
        """

        own = sorted({one.invariant for one in entries if self.live(one.invariant)})
        retired = sorted({one.invariant for one in entries if not self.live(one.invariant)})
        return own, retired

    def _rule_blocks(
        self,
        path: str,
        places: Sequence[RulePlaces],
        placement: Placement,
        by_id: Mapping[str, Mapping[str, Any]],
    ) -> tuple[RuleBlock, ...]:
        return tuple(
            self._rule_block(
                one,
                path,
                standing=[by_id[w] for w in placement.standing[one.head.id]],
                named=placement.named[one.head.id],
            )
            for one in brief_order(places)
        )

    def _brief_head(self, path: str, counts: BriefCounts) -> BriefHead:
        return BriefHead(
            path=path,
            memory_tree=self.memory_tree,
            code_commit=self.code.commit,
            code_state=self.code.state,
            card_path=self._card(path),
            counts=counts,
        )

    def _loose_decisions(
        self, decisions_here: Iterable[tuple[str, str]], rules: Iterable[RuleBlock]
    ) -> tuple[DecisionLine, ...]:
        """The decisions linked to the file that no rule block already shows."""

        tied = {decision.id for rule in rules for decision in rule.decisions}
        return tuple(
            self._decision_line(decision_id, relation)
            for decision_id, relation in decisions_here
            if decision_id not in tied
        )

    # -- the parts -----------------------------------------------------------------------------

    def _rule_places(self, invariant: str, entries: Sequence[Entry]) -> RulePlaces:
        mine = [one for one in entries if one.invariant == invariant]
        record = self.index.record(invariant).value
        document = {} if record is None else record.document
        states = [self.entry_current(one.id) for one in mine]
        measured = all(state is not None for state in states) and bool(states)
        standing = self._standings[invariant]
        assert standing.state is not None  # Only live invariants reach a rule block.
        writer = "unknown" if standing.writer == UNKNOWN_WRITER else compact_writer(standing.writer)
        head = RuleHead(
            id=invariant,
            title=rule_title(document),
            standing=Standing(state=standing.state, writer=writer),
            proofs=len(self.knowledge(invariant).proofs),
            anchors_current=sum(1 for state in states if state),
            anchors_total=len(mine),
            anchors_measured=measured,
        )
        symbols = tuple(anchor_symbol(one.document.get("anchor")) for one in mine)
        return RulePlaces(head=head, symbols=symbols)

    def _rule_block(
        self,
        places: RulePlaces,
        path: str,
        *,
        standing: Sequence[Mapping[str, Any]],
        named: Sequence[str],
    ) -> RuleBlock:
        invariant = places.head.id
        knowledge = self.knowledge(invariant)
        document = {} if knowledge.record is None else knowledge.record.document
        here, elsewhere = self._here_and_elsewhere(knowledge, path)
        linked = list(knowledge.linked_from)
        return RuleBlock(
            head=places.head,
            statement=str(document.get("statement") or ""),
            applicability=document.get("applicability"),
            conditions=tuple(document.get("conditions") or ()),
            exclusions=tuple(document.get("exclusions") or ()),
            here=here,
            elsewhere=elsewhere,
            proofs=_proof_lines(knowledge),
            decisions=self._linked_decisions(linked),
            weaknesses=tuple(_weakness_line(one) for one in standing),
            weaknesses_named=tuple(named),
            facets=self._linked_facets(linked),
            families=tuple(one for one in knowledge.families if self.live(one)),
            record_path=knowledge.record.path if knowledge.record is not None else "",
        )

    def _here_and_elsewhere(
        self, knowledge: InvariantKnowledge, path: str
    ) -> tuple[tuple[Place, ...], tuple[Place, ...]]:
        """The rule's places in ``path`` (measured) and its realizations in other files."""

        here = tuple(
            self._place(one)
            for one in (*knowledge.realizations, *knowledge.proofs)
            if one.path == path
        )
        elsewhere = tuple(
            self._place(one, measured=False) for one in knowledge.realizations if one.path != path
        )
        return here, elsewhere

    def _linked_decisions(self, linked: Sequence[Any]) -> tuple[DecisionLine, ...]:
        return tuple(
            self._decision_line(decision, ", ".join(relations))
            for decision, relations in _relations_by_source(linked, "decision").items()
        )

    def _linked_facets(self, linked: Sequence[Any]) -> tuple[FacetLine, ...]:
        return tuple(
            facet
            for facet in (
                self._facet_line(link.source, link.source_kind, link.relation)
                for link in linked
                if link.source_kind in _FACET_TEXT
            )
            if facet is not None
        )

    def _place(self, entry: Entry, *, measured: bool = True) -> Place:
        return Place(
            path=entry.path,
            symbol=anchor_symbol(entry.document.get("anchor")),
            role=entry.document.get("role"),
            rationale=entry.document.get("rationale"),
            current=self.entry_current(entry.id) if measured else None,
        )

    def _decision_line(self, decision_id: str, relation: str) -> DecisionLine:
        record = self.index.record(decision_id).value
        alternatives = [] if record is None else record.document.get("alternatives") or []
        return DecisionLine(
            id=decision_id,
            relation=relation,
            chosen=tuple(
                str(one.get("option")) for one in alternatives if one.get("status") == "chosen"
            ),
            rejected=tuple(
                (str(one.get("option")), str(one.get("reason") or ""))
                for one in alternatives
                if one.get("status") == "rejected"
            ),
            record_path="" if record is None else record.path,
        )

    def _facet_line(self, record_id: str, kind: str, relation: str) -> FacetLine | None:
        record = self.index.record(record_id).value
        if record is None or record.status == _RETIRED:
            return None
        return FacetLine(
            id=record_id,
            kind=kind,
            relation=relation,
            text=str(record.document.get(_FACET_TEXT[kind]) or ""),
            record_path=record.path,
        )

    def _families(self, own: Sequence[str]) -> tuple[FamilyBlock, ...]:
        found = sorted({one for rule in own for one in self.knowledge(rule).families})
        blocks = (self._family_block(family_id, own) for family_id in found)
        return tuple(block for block in blocks if block is not None)

    def _family_block(self, family_id: str, own: Sequence[str]) -> FamilyBlock | None:
        """A live family of the file's rules, with its rules here and its other live rules."""

        knowledge = self.index.family(family_id).value
        record = knowledge.record
        if record is None or record.status == _RETIRED:
            return None
        return FamilyBlock(
            id=family_id,
            title=str(record.document.get("title") or family_id),
            guarantee=str(record.document.get("guarantee") or ""),
            here=tuple(one for one in knowledge.members if one in own),
            others=tuple(
                self._other_rule(member)
                for member in knowledge.members
                if member not in own and self.live(member)
            ),
        )

    def _other_rule(self, member: str) -> OtherRule:
        member_knowledge = self.knowledge(member)
        document = {} if member_knowledge.record is None else member_knowledge.record.document
        return OtherRule(
            id=member,
            title=rule_title(document),
            statement=str(document.get("statement") or ""),
            paths=tuple(sorted({one.path for one in member_knowledge.realizations})),
        )

    def _card(self, path: str) -> str | None:
        card = f"{ONBOARDING_ROOT}/{path}.md"
        return card if (self.memory_root / card).is_file() else None

    def _purpose(self, path: str) -> str | None:
        card = self._card(path)
        if card is None:
            return None
        try:
            text = (self.memory_root / card).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        match = _PURPOSE.search(text)
        return None if match is None else match.group(1).strip() or None


@dataclass(frozen=True)
class _TreeMaps:
    """The records a brief needs by file, read once per tree."""

    weaknesses: Mapping[str, tuple[Mapping[str, Any], ...]]
    dependents: Mapping[str, tuple[Mapping[str, Any], ...]]
    decisions_at: Mapping[str, tuple[tuple[str, str], ...]]

    @classmethod
    def read(cls, index: KnowledgeIndex) -> _TreeMaps:
        return cls(
            weaknesses=_open_weaknesses(index),
            dependents=_standing_dependents(index),
            decisions_at=_decisions_at(index),
        )


def _open_weaknesses(index: KnowledgeIndex) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Every open weakness with an anchored file, by that file, in ID order."""

    weaknesses: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in index.records_of_kind("weakness").value:
        document = record.document
        anchor = document.get("anchor") or {}
        if document.get("state") != "open" or not anchor.get("path"):
            continue
        weaknesses[str(anchor["path"])].append({**document, "path": record.path})
    return {key: tuple(sorted(value, key=_by_id)) for key, value in weaknesses.items()}


def _standing_dependents(index: KnowledgeIndex) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Every live dependent by each file it stands on, in document and ID order."""

    dependents: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in index.records_of_kind("dependent").value:
        if record.status == _RETIRED:
            continue
        for path in _standing_paths(record.document):
            dependents[path].append({**record.document, "path": record.path})
    return {
        key: tuple(sorted(value, key=lambda one: (str(one.get("document")), _by_id(one))))
        for key, value in dependents.items()
    }


def _decisions_at(index: KnowledgeIndex) -> dict[str, tuple[tuple[str, str], ...]]:
    """Every decision's ``(id, relation)`` pairs by each file its links point at, sorted."""

    decisions: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for record in index.records_of_kind("decision").value:
        for link in record.document.get("links") or ():
            target = link.get("target")
            if not (isinstance(target, Mapping) and target.get("path")):
                continue
            pair = (record.id, str(link.get("relation")))
            if pair not in decisions[str(target["path"])]:
                decisions[str(target["path"])].append(pair)
    return {key: tuple(sorted(value)) for key, value in decisions.items()}


def _relations_by_source(links: Iterable[Any], kind: str) -> dict[str, list[str]]:
    """Each linking record of ``kind`` once, with every relation it holds to the rule."""

    found: dict[str, list[str]] = {}
    for link in links:
        if link.source_kind == kind and link.relation not in found.setdefault(link.source, []):
            found[link.source].append(link.relation)
    return found


def _by_id(document: Mapping[str, Any]) -> str:
    return str(document.get("id"))


def _standing_paths(document: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    for link in document.get("stands_on") or ():
        if link.get("kind") == "file" and link.get("path"):
            found.add(str(link["path"]))
        elif link.get("kind") == "anchor" and (link.get("anchor") or {}).get("path"):
            found.add(str(link["anchor"]["path"]))
    return found


def _proof_lines(knowledge: InvariantKnowledge) -> tuple[ProofLine, ...]:
    return tuple(
        ProofLine(
            path=one.path,
            symbol=anchor_symbol(one.document.get("anchor")),
            facet=one.document.get("facet"),
        )
        for one in knowledge.proofs
    )


def _standing_documents(documents: Iterable[Mapping[str, Any]]) -> tuple[StandingDocument, ...]:
    return tuple(
        StandingDocument(id=one["id"], document=one["document"], record_path=one["path"])
        for one in documents
    )


def _site(document: Mapping[str, Any]) -> WeaknessSite:
    burdens = tuple(
        str(link.get("target"))
        for link in document.get("links") or ()
        if link.get("relation") == "burdens" and isinstance(link.get("target"), str)
    )
    return WeaknessSite(
        id=str(document["id"]), symbol=anchor_symbol(document.get("anchor")), burdens=burdens
    )


def _weakness_line(document: Mapping[str, Any]) -> WeaknessLine:
    return WeaknessLine(
        id=str(document["id"]),
        kind=str(document.get("kind")),
        symbol=anchor_symbol(document.get("anchor")),
        statement=str(document.get("statement") or ""),
        record_path=str(document.get("path") or ""),
    )


__all__ = ["BriefSource", "CodeSide", "anchor_symbol"]
