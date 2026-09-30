"""Entry classification and the knowledge-side changes (MIK-R08 definitions 4, 6 and 7).

**Classification.** Each K_B entry takes the first class that applies:

1. ``stale_at_base`` -- its ``blob`` differs from its path's blob at B, and at B either its range's
   content differs from the recorded ``content`` or its locator does not resolve;
2. ``moved_or_absent`` -- its path is absent at C or Git's rename detection renamed it, its symbol does
   not resolve uniquely at C, or its line range has no image at C;
3. ``touched`` -- a changed line of the B-to-C hunks hits its range on the B side or on the C side;
4. ``carried`` -- the C blob differs from its ``blob`` and its range content at C is identical;
5. ``untouched`` -- the C blob is its ``blob``.

One case the definitions leave open is closed conservatively: an entry that is not stale, not moved
and hit by no hunk, whose C blob differs from its recorded ``blob`` while its range content at C
still differs from the recorded ``content``, is classified ``touched`` -- the curator must look at
it -- rather than ``carried``, which would claim identical content. When the C blob *is* the
recorded ``blob`` the entry is ``untouched`` (definition 4 wins, architect ruling on review R1).

A ``moved_or_absent`` symbol entry carries the mechanical unique match of definition 6 when there is
one, labelled ``mechanical``.

A decision's ``reconsider_on`` anchor target is classified the same way (MIK-R14 rule 1,
:meth:`Classifier.classify_anchor`), as kind ``link``; it is not an entry and is never recorded
among the classified entries.

**Knowledge-side changes (definition 7).** An invariant of K_B changed when its record file differs,
when K_C holds an entry for it whose ID K_B does not (added), when an entry of K_B is absent from K_C
(retired), or when one of its entries was re-anchored: present on both sides with a different anchor
or source path, unless its K_B class already raised an item (``touched``, ``moved_or_absent``,
``stale_at_base``) or it is ``carried`` and only ``blob`` and line numbers moved. An entry that
changed invariant is retired from the one and added to the other. A family of K_B changed when its
record file differs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal

from agents_remember.application.knowledge_worklist.code import (
    CodeTrees,
    Hunk,
    Resolved,
    hits_new,
    hits_old,
)
from agents_remember.application.knowledge_worklist.knowledge import (
    KnowledgeSide,
    anchor_document,
)
from agents_remember.memory.knowledge_index.build import IndexedEntry
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.models.knowledge_files.sidecars import ProofEntry

__all__ = [
    "COVERING_CLASSES",
    "RAISING_CLASSES",
    "Classification",
    "Classifier",
    "EntryClass",
    "InvariantChange",
    "KnowledgeChanges",
    "knowledge_changes",
]

EntryClass = Literal["stale_at_base", "moved_or_absent", "touched", "carried", "untouched"]
EntryKind = Literal["realization", "proof", "link"]
RAISING_CLASSES: Final = frozenset({"touched", "moved_or_absent"})
COVERING_CLASSES: Final = frozenset({"touched", "moved_or_absent", "stale_at_base"})
ABSENT: Final = "absent"


@dataclass(frozen=True)
class Classification:
    """One K_B entry's class in this run, with the facts that decided it."""

    entry_id: str
    entry_kind: EntryKind
    invariant: str
    path: str
    entry_class: EntryClass
    anchor: Mapping[str, Any]
    base_blob: str | None
    base: Resolved | None
    candidate_blob: str | None
    candidate: Resolved | None
    hunks: tuple[Hunk, ...] = ()
    renamed_to: str | None = None
    unique_match: str | None = None

    def contents(self) -> list[str]:
        """The entry's range content identity at B and at C (``absent`` where it does not resolve)."""

        return [
            self.entry_id,
            ABSENT if self.base is None else self.base.content,
            ABSENT if self.candidate is None else self.candidate.content,
        ]

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "id": self.entry_id,
            "kind": self.entry_kind,
            "path": self.path,
            "class": self.entry_class,
            "anchor": dict(self.anchor),
            "base": _side(self.base_blob, self.base),
            "candidate": _side(self.candidate_blob, self.candidate),
            "hunks": [hunk.to_document() for hunk in self.hunks],
        }
        if self.renamed_to is not None:
            document["renamedTo"] = self.renamed_to
        if self.unique_match is not None:
            document["uniqueMatch"] = {"path": self.unique_match, "label": "mechanical"}
        return document


def _side(blob: str | None, resolved: Resolved | None) -> dict[str, Any]:
    if blob is None:
        return {"blob": ABSENT}
    if resolved is None:
        return {"blob": blob, "resolved": False}
    return {**resolved.to_document(), "resolved": True}


@dataclass
class Classifier:
    """Classifies K_B entries against the code trees B and C; each entry is classified once."""

    code: CodeTrees
    base: KnowledgeSide
    renames: Mapping[str, str]
    _done: dict[str, Classification] = field(default_factory=dict)

    def classified(self) -> tuple[Classification, ...]:
        """Every entry classified so far, by scope or to decide a re-anchor."""

        return tuple(self._done.values())

    def classify(self, entry_id: str) -> Classification:
        found = self._done.get(entry_id)
        if found is None:
            found = self._classify(self.base.entries[entry_id])
            self._done[entry_id] = found
        return found

    def classify_anchor(self, key: str, anchor: Anchor) -> Classification:
        """A ``reconsider_on`` link's anchor, classified like an entry (MIK-R14 rule 1).

        It is not an entry: it is never recorded among the run's classified entries, and ``key``
        names the link. ``anchor.path`` is required on a link target.
        """

        assert anchor.path is not None  # a link's anchor target always names its path
        return self._classify_anchor(key, "link", "", anchor.path, anchor)

    def _classify(self, indexed: IndexedEntry) -> Classification:
        entry = indexed.entry
        kind: EntryKind = "proof" if isinstance(entry, ProofEntry) else "realization"
        return self._classify_anchor(entry.id, kind, entry.invariant, indexed.path, entry.anchor)

    def _classify_anchor(
        self, key: str, kind: EntryKind, invariant: str, path: str, anchor: Anchor
    ) -> Classification:
        locator = anchor.locator.to_document()
        base_blob = self.code.base().get(path)
        candidate_blob = self.code.candidate().get(path)
        at_base = self._resolve(path, locator, anchor.blob, base_blob)
        at_candidate = self._resolve(path, locator, anchor.blob, candidate_blob)
        facts = Classification(
            entry_id=key,
            entry_kind=kind,
            invariant=invariant,
            path=path,
            entry_class="untouched",
            anchor=anchor_document(anchor, path),
            base_blob=base_blob,
            base=at_base,
            candidate_blob=candidate_blob,
            candidate=at_candidate,
            renamed_to=self.renames.get(path),
        )
        if anchor.blob != base_blob and (at_base is None or at_base.content != anchor.content):
            return _with(facts, "stale_at_base")
        if candidate_blob is None or path in self.renames or at_candidate is None:
            match = (
                self.code.unique_binder(str(locator["name"]))
                if locator.get("kind") == "symbol"
                else None
            )
            return _with(facts, "moved_or_absent", unique_match=match)
        hits = self._hits(base_blob, candidate_blob, at_base, at_candidate)
        if hits is None or hits:
            return _with(facts, "touched", hunks=hits or ())
        if candidate_blob == anchor.blob:
            return facts  # the blob is unchanged: untouched, whatever the recorded content says
        if at_candidate.content != anchor.content:
            return _with(facts, "touched")
        return _with(facts, "carried")

    def _resolve(
        self, path: str, locator: Mapping[str, Any], recorded: str, blob: str | None
    ) -> Resolved | None:
        return None if blob is None else self.code.resolve(path, locator, recorded, blob)

    def _hits(
        self,
        base_blob: str | None,
        candidate_blob: str,
        at_base: Resolved | None,
        at_candidate: Resolved,
    ) -> tuple[Hunk, ...] | None:
        """The B-to-C hunks that hit the range; ``None`` when the pair is binary (a whole change)."""

        if base_blob is None or base_blob == candidate_blob:
            return ()
        hunks = self.code.hunks(base_blob, candidate_blob)
        if hunks is None:
            return None
        return tuple(
            hunk
            for hunk in hunks
            if (at_base is not None and hits_old(hunk, at_base.span))
            or hits_new(hunk, at_candidate.span)
        )


def _with(facts: Classification, entry_class: EntryClass, **changes: Any) -> Classification:
    return replace(facts, entry_class=entry_class, **changes)


@dataclass
class InvariantChange:
    """What changed for one K_B invariant on the knowledge side."""

    record_changed: bool = False
    added: list[dict[str, Any]] = field(default_factory=list)
    retired: list[dict[str, Any]] = field(default_factory=list)
    reanchored: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.record_changed or bool(self.added or self.retired or self.reanchored)


@dataclass(frozen=True)
class KnowledgeChanges:
    invariants: Mapping[str, InvariantChange]
    families: frozenset[str]

    def changed_invariants(self) -> frozenset[str]:
        return frozenset(key for key, change in self.invariants.items() if change.changed)


def _entry_fact(indexed: IndexedEntry) -> dict[str, Any]:
    return {"id": indexed.entry.id, "anchor": anchor_document(indexed.entry.anchor, indexed.path)}


def _only_mechanical(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Whether two anchors differ only in ``blob`` and line numbers (the writer's carry-forward)."""

    if before["path"] != after["path"] or before["content"] != after["content"]:
        return False
    old, new = before["locator"], after["locator"]
    if old.get("kind") != new.get("kind"):
        return False
    ignored = {"start", "end"} if old.get("kind") == "line_range" else set()
    return {k: v for k, v in old.items() if k not in ignored} == {
        k: v for k, v in new.items() if k not in ignored
    }


def knowledge_changes(
    base: KnowledgeSide, candidate: KnowledgeSide, classifier: Classifier
) -> KnowledgeChanges:
    """Definition 7 between K_B and K_C."""

    changes = {
        invariant: InvariantChange(
            record_changed=base.record_file(invariant) != candidate.record_file(invariant)
        )
        for invariant in base.invariants
    }

    def note(invariant: str, kind: str, fact: dict[str, Any]) -> None:
        change = changes.get(invariant)
        if change is not None:  # an invariant absent from K_B raises nothing (rule 6)
            getattr(change, kind).append(fact)

    for entry_id, after in sorted(candidate.entries.items()):
        before = base.entries.get(entry_id)
        if before is None:
            note(after.entry.invariant, "added", _entry_fact(after))
        elif before.entry.invariant != after.entry.invariant:
            note(before.entry.invariant, "retired", _entry_fact(before))
            note(after.entry.invariant, "added", _entry_fact(after))
        else:
            _reanchor(before, after, classifier, note)
    for entry_id, before in sorted(base.entries.items()):
        if entry_id not in candidate.entries:
            note(before.entry.invariant, "retired", _entry_fact(before))
    families = frozenset(
        family
        for family in base.families
        if base.record_file(family) != candidate.record_file(family)
    )
    return KnowledgeChanges(invariants=changes, families=families)


def _reanchor(before: IndexedEntry, after: IndexedEntry, classifier: Classifier, note: Any) -> None:
    old = anchor_document(before.entry.anchor, before.path)
    new = anchor_document(after.entry.anchor, after.path)
    if old == new:
        return
    entry_class = classifier.classify(before.entry.id).entry_class
    if entry_class in COVERING_CLASSES:
        return
    if entry_class == "carried" and _only_mechanical(old, new):
        return
    note(
        before.entry.invariant,
        "reanchored",
        {"id": before.entry.id, "before": old, "after": new, "baseClass": entry_class},
    )
