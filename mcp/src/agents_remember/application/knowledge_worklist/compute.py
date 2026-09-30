"""One worklist run: the change inventory, the one-pass scope, the items and the digest (MIK-R08).

The run is a pure function of its inputs -- the code trees B and C, the knowledge trees K_B and K_C,
and the leaf's ``knowledgeMaintenanceScope`` -- so identical inputs give identical items and an
identical digest (rule 6). It reads the changed paths from the landed ICR change inventory
(:func:`tree_difference_observation`, ICR-R02) and the renames from Git's rename detection as the
landed inventory owner measures it (:func:`git_rename_inference`, ICR-R08), then:

1. classifies every K_B entry at a changed path (every K_B entry when the leaf sets
   ``knowledgeMaintenanceScope``);
2. computes the knowledge-side changes between K_B and K_C;
3. reaches every K_B family that contains an invariant with a ``touched_invariant`` item, or whose
   record changed;
4. classifies every K_B entry of those families' members -- once. What step 4 finds raises items but
   reaches no further family, and a ``stale_invariant`` raises ``reached_family`` for its families
   without classifying their members unless step 3 already reached them;
5. reconciles the leaf's declared ``expectedKnowledgeEffects`` against its history rows in K_C
   (MIK-R11, :mod:`.planned_effects`): every invariant and family item is marked ``planned`` or
   ``unplanned``, and every declaration no row delivers raises ``planned_untouched``;
6. evaluates the family route conditions (MIK-R06, :mod:`.route_conditions`) of every reached
   family, and ``route_path_absent`` of every family with a route this leaf's range killed;
7. marks every changed hunk **linked** or **unexplained** (definition 8; a delete-only hunk has no
   changed line at C, so only a K_B entry's range at B links it, and an insertion-only hunk has no
   changed line at B, so only a K_C entry's range at C links it) and raises MIK-R10's
   ``unexplained_hunk`` / ``unexplained_file`` item for every unlinked hunk and non-text change
   (:mod:`.unexplained`), with its path's coverage. Its items join step 5's sort; the planning marks
   do not apply to them.
8. evaluates every K_B decision's ``reconsider_on`` links (MIK-R14, :mod:`.reconsideration`) and
   raises a ``reconsideration_candidate`` for each alternative whose linked target changed.

An input that cannot be read makes the run ``incomplete`` naming it, with no items (rule 4);
nothing is a verdict (Exclusions).
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_worklist.classify import (
    ABSENT,
    COVERING_CLASSES,
    RAISING_CLASSES,
    Classification,
    Classifier,
    InvariantChange,
    KnowledgeChanges,
    knowledge_changes,
)
from agents_remember.application.knowledge_worklist.code import (
    CodeReadError,
    CodeTrees,
    Hunk,
    change_hunks,
    hits_new,
    hits_old,
)
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.planned_effects import (
    Declaration,
    reconcile_planned_effects,
)
from agents_remember.application.knowledge_worklist.reconsideration import (
    ReconsiderationInputs,
    reconsideration_candidates,
)
from agents_remember.application.knowledge_worklist.registry import item_id, kinds_document
from agents_remember.application.knowledge_worklist.route_conditions import (
    ITEM_KIND as ROUTE_CONDITION_KIND,
)
from agents_remember.application.knowledge_worklist.route_conditions import (
    RouteInputs,
    family_route_conditions,
)
from agents_remember.application.knowledge_worklist.unexplained import (
    RouteCoverage,
    UnexplainedSides,
    unexplained_items,
)
from agents_remember.application.review_rename_inference import git_rename_inference
from agents_remember.application.review_source_inventory import (
    byte_form,
    tree_difference_observation,
)
from agents_remember.kernel.canonical_json import prefixed_sha256_digest
from agents_remember.memory.knowledge.tree_observation import TreeChange, TreePaths, TreeSide

__all__ = [
    "WORKLIST_SCHEMA",
    "Incomplete",
    "Item",
    "WorklistInputs",
    "compute_worklist",
    "git_failure",
    "incomplete_worklist",
    "non_text_linked",
    "worklist_digest",
]

WORKLIST_SCHEMA: Final = "knowledge-worklist/v1"


@dataclass(frozen=True)
class Incomplete:
    """An input the run could not read (rule 4): which one, and why."""

    input: str
    detail: str

    def to_document(self) -> dict[str, str]:
        return {"input": self.input, "detail": self.detail}


class WorklistIncomplete(Exception):
    def __init__(self, missing: Incomplete) -> None:
        super().__init__(missing.detail)
        self.missing = missing


@dataclass(frozen=True)
class Item:
    """One worklist item: its registered kind, its subject, its facts and its identities.

    ``extra`` holds a registrant's further top-level fields (MIK-R06's ``satisfiedBy``).
    """

    kind: str
    subject: str
    facts: Mapping[str, Any]
    identities: Any
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return item_id(self.kind, self.subject, self.identities)

    def to_document(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "facts": self.facts,
            **self.extra,
        }


@dataclass(frozen=True)
class WorklistInputs:
    """What one run reads: the code trees, the two knowledge sides, the scope flag, the pairing."""

    code: CodeTrees
    base: KnowledgeSide
    candidate: KnowledgeSide
    pairing: Mapping[str, Any]
    maintenance_scope: bool = False
    owner: str | None = None
    expected_effects: tuple[Declaration, ...] | None = None
    """The leaf's ``expectedKnowledgeEffects`` (MIK-R11); ``None`` when it declares none."""
    coverage: RouteCoverage | None = None
    """K_B's onboarding routes and census statuses (MIK-R10 coverage); ``None``: every route pending."""
    coordination_root: Path | None = None
    """Where requirement endpoints' owning tasks live (MIK-R14); ``None`` resolves none."""


def worklist_digest(state: str, items: Iterable[Mapping[str, Any]], missing: Any) -> str:
    return prefixed_sha256_digest({"state": state, "items": list(items), "incomplete": missing})


def incomplete_worklist(
    missing: Incomplete, *, owner: str | None, pairing: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The one representation of unreadable input: ``incomplete``, naming it, with no items."""

    return {
        "schema": WORKLIST_SCHEMA,
        "owner": owner,
        "state": "incomplete",
        "incomplete": [missing.to_document()],
        "pairing": dict(pairing or {}),
        "items": [],
        "kinds": kinds_document(),
        "digest": worklist_digest("incomplete", [], [missing.to_document()]),
    }


def compute_worklist(inputs: WorklistInputs) -> dict[str, Any]:
    """Run the worklist over ``inputs`` and return its ``knowledge-worklist/v1`` document."""

    try:
        return _Run(inputs).document()
    except WorklistIncomplete as error:
        return incomplete_worklist(error.missing, owner=inputs.owner, pairing=inputs.pairing)
    except CodeReadError as error:
        return incomplete_worklist(
            Incomplete("C", str(error)), owner=inputs.owner, pairing=inputs.pairing
        )
    except subprocess.SubprocessError as error:
        return incomplete_worklist(git_failure(error), owner=inputs.owner, pairing=inputs.pairing)


def _changes(inputs: WorklistInputs) -> tuple[tuple[TreeChange, ...], int, dict[str, str]]:
    root = str(inputs.code.repository)
    before = TreeSide(tree_id=inputs.code.base_tree, root=root)
    after = TreeSide(tree_id=inputs.code.candidate_tree, root=root)
    observed = tree_difference_observation(before, after)
    if not observed.available:
        raise WorklistIncomplete(Incomplete("B/C change inventory", observed.detail))
    if observed.partial:
        raise WorklistIncomplete(Incomplete("B/C change inventory", _partial_detail(observed)))
    renames = git_rename_inference(before, after)
    if not renames.available:
        raise WorklistIncomplete(Incomplete("B/C renames", renames.detail))
    renamed = {pair.before_path: pair.after_path for pair in renames.pairs}
    return tuple(observed.entries), len(observed.unrepresentable), renamed


def _partial_detail(observed: TreePaths) -> str:
    """Name what a partial inventory could not report whole (rule 4: never a silent fallback)."""

    unrepresentable = [byte_form(change.path) for change in observed.unrepresentable]
    unclassified = [change.path for change in observed.entries if change.content == "unknown"]
    parts = [observed.detail or "the change inventory is partial"]
    if unrepresentable:
        parts.append(f"paths that are not valid text: {', '.join(sorted(unrepresentable))}")
    if unclassified:
        parts.append(f"paths without a content classification: {', '.join(sorted(unclassified))}")
    return "; ".join(parts)


@dataclass
class _Run:
    inputs: WorklistInputs
    classified: dict[str, Classification] = field(default_factory=dict)

    def document(self) -> dict[str, Any]:
        inputs = self.inputs
        changes, unrepresentable, renamed = _changes(inputs)
        changed_paths = {change.path for change in changes}
        classifier = Classifier(inputs.code, inputs.base, renamed)
        knowledge, touched, reached, stale = self._scope(classifier, changed_paths)
        family_items = self._family_items(reached, touched, stale, knowledge)
        items = [
            *(
                self._touched_item(invariant, knowledge.invariants[invariant])
                for invariant in touched
            ),
            *(self._stale_item(invariant) for invariant in stale),
            *family_items,
            *self._route_items({item.subject for item in family_items}, renamed),
        ]
        planned = reconcile_planned_effects(
            inputs.expected_effects, inputs.base, inputs.candidate, inputs.owner
        )
        linkage = self._linkage(changes, renamed)
        unexplained = unexplained_items(
            linkage,
            UnexplainedSides(
                inputs.code, inputs.base, inputs.candidate, inputs.coverage, inputs.owner
            ),
        )
        reconsideration = reconsideration_candidates(
            ReconsiderationInputs(
                inputs.base,
                inputs.candidate,
                classifier.classify_anchor,
                inputs.owner,
                inputs.coordination_root,
            )
        )
        rendered = sorted(
            [
                *(planned.mark(item.to_document()) for item in items),
                *planned.items,
                *unexplained.items,
                *reconsideration.items,
            ],
            key=lambda item: (item["kind"], item["subject"]),
        )
        return {
            "schema": WORKLIST_SCHEMA,
            "owner": inputs.owner,
            "state": "complete",
            "incomplete": [],
            "pairing": dict(inputs.pairing),
            "scope": self._scope_document(changed_paths, unrepresentable, reached),
            "entries": self._entries_document(),
            "changes": linkage,
            "plannedEffects": planned.summary(),
            "unexplained": unexplained.summary(),
            "reconsideration": reconsideration.summary,
            "items": rendered,
            "kinds": kinds_document(),
            "digest": worklist_digest("complete", rendered, []),
        }

    def _scope(
        self, classifier: Classifier, changed_paths: set[str]
    ) -> tuple[KnowledgeChanges, list[str], set[str], list[str]]:
        """Steps 1-4: classify, compare the knowledge sides, reach families, classify members."""

        base = self.inputs.base
        self._classify(classifier, self._first_entries(changed_paths))
        knowledge = knowledge_changes(base, self.inputs.candidate, classifier)
        # A re-anchored entry whose class covers it (definition 7) raises that class's item, so an
        # entry classified only to decide the re-anchor joins the classified set when it does.
        self._classify(
            classifier,
            sorted(
                one.entry_id
                for one in classifier.classified()
                if one.entry_class in COVERING_CLASSES
            ),
        )
        reached = self._reached(self._touched(knowledge), knowledge)
        members = sorted(
            {member for family in reached for member in self._members(family)}
            & set(base.invariants)
        )
        self._classify(
            classifier,
            [entry for member in members for entry in base.entries_by_invariant.get(member, ())],
        )
        return knowledge, self._touched(knowledge), reached, self._stale()

    def _first_entries(self, changed_paths: set[str]) -> list[str]:
        """Step 1's entries: those at a changed path, or every K_B entry in maintenance scope."""

        base = self.inputs.base
        if self.inputs.maintenance_scope:
            return sorted(base.entries)
        return sorted(
            entry for path in changed_paths for entry in base.entries_by_path.get(path, ())
        )

    def _stale(self) -> list[str]:
        return sorted(
            {
                one.invariant
                for one in self.classified.values()
                if one.entry_class == "stale_at_base"
            }
        )

    def _scope_document(
        self, changed_paths: set[str], unrepresentable: int, reached: set[str]
    ) -> dict[str, Any]:
        return {
            "knowledgeMaintenanceScope": self.inputs.maintenance_scope,
            "changedPaths": len(changed_paths),
            "unrepresentablePaths": unrepresentable,
            "classifiedEntries": len(self.classified),
            "classes": _class_counts(self.classified.values()),
            "reachedFamilies": sorted(reached),
        }

    def _entries_document(self) -> list[dict[str, Any]]:
        return [
            {
                "id": one.entry_id,
                "invariant": one.invariant,
                "path": one.path,
                "class": one.entry_class,
            }
            for one in sorted(self.classified.values(), key=lambda one: one.entry_id)
        ]

    # -- scope ---------------------------------------------------------------------------------

    def _classify(self, classifier: Classifier, entries: Iterable[str]) -> None:
        for entry in entries:
            if entry not in self.classified:
                self.classified[entry] = classifier.classify(entry)

    def _touched(self, knowledge: KnowledgeChanges) -> list[str]:
        raised = {
            one.invariant for one in self.classified.values() if one.entry_class in RAISING_CLASSES
        }
        raised |= knowledge.changed_invariants()
        return sorted(raised & set(self.inputs.base.invariants))

    def _members(self, family: str) -> set[str]:
        members: set[str] = set()
        for side in (self.inputs.base, self.inputs.candidate):
            record = side.families.get(family)
            if record is not None:
                members.update(record.members)
        return members

    def _reached(self, touched: list[str], knowledge: KnowledgeChanges) -> set[str]:
        """Step 3: K_B families containing a touched invariant, or whose record changed."""

        touched_set = set(touched)
        return {
            family
            for family in self.inputs.base.families
            if self._members(family) & touched_set or family in knowledge.families
        }

    # -- items ---------------------------------------------------------------------------------

    def _entries_of(self, invariant: str) -> list[Classification]:
        return sorted(
            (one for one in self.classified.values() if one.invariant == invariant),
            key=lambda one: one.entry_id,
        )

    def _touched_item(self, invariant: str, change: InvariantChange) -> Item:
        base, candidate = self.inputs.base, self.inputs.candidate
        entries = self._entries_of(invariant)
        facts = {
            "entries": [one.to_document() for one in entries],
            "added": change.added,
            "retired": change.retired,
            "reanchored": change.reanchored,
            "record": {
                "changed": change.record_changed,
                "baseRevision": base.revision(invariant),
                "candidateRevision": candidate.revision(invariant),
            },
            "context": {
                "families": sorted(
                    {
                        *base.families_of.get(invariant, ()),
                        *candidate.families_of.get(invariant, ()),
                    }
                ),
                "linkedFrom": list(candidate.linked_from.get(invariant, ())),
            },
        }
        identities = {
            "entries": [one.contents() for one in entries if one.entry_class in RAISING_CLASSES],
            "added": [[fact["id"], fact["anchor"]["content"]] for fact in change.added],
            "retired": [[fact["id"], fact["anchor"]["content"]] for fact in change.retired],
            "reanchored": [
                [fact["id"], fact["before"]["content"], fact["after"]["content"]]
                for fact in change.reanchored
            ],
            "record": [base.revision(invariant), candidate.revision(invariant)]
            if change.record_changed
            else None,
        }
        return Item("touched_invariant", invariant, facts, identities)

    def _stale_item(self, invariant: str) -> Item:
        stale = [one for one in self._entries_of(invariant) if one.entry_class == "stale_at_base"]
        facts = {
            "entries": [
                {
                    "id": one.entry_id,
                    "path": one.path,
                    "entryBlob": one.anchor["blob"],
                    "baseBlob": one.base_blob or ABSENT,
                    "recordedContent": one.anchor["content"],
                    "baseContent": ABSENT if one.base is None else one.base.content,
                }
                for one in stale
            ]
        }
        identities = [
            [fact["id"], fact["recordedContent"], fact["baseContent"]] for fact in facts["entries"]
        ]
        return Item("stale_invariant", invariant, facts, identities)

    def _family_items(
        self,
        reached: set[str],
        touched: list[str],
        stale: list[str],
        knowledge: KnowledgeChanges,
    ) -> list[Item]:
        base = self.inputs.base
        reasons: dict[str, set[str]] = {family: set() for family in reached}
        for family in reached:
            members = self._members(family)
            reasons[family].update(f"touched:{one}" for one in touched if one in members)
            if family in knowledge.families:
                reasons[family].add("record-changed")
        for invariant in stale:
            for family in base.families_of.get(invariant, ()):
                reasons.setdefault(family, set()).add(f"stale:{invariant}")
        return [self._family_item(family, sorted(why)) for family, why in sorted(reasons.items())]

    def _family_item(self, family: str, why: list[str]) -> Item:
        base, candidate = self.inputs.base, self.inputs.candidate
        members = [
            {"id": member, "base": base.revision(member), "candidate": candidate.revision(member)}
            for member in sorted(self._members(family))
        ]
        routes = {
            "base": list(base.families[family].routes),
            "candidate": (
                list(candidate.families[family].routes) if family in candidate.families else None
            ),
        }
        facts = {"members": members, "routes": routes, "reachedBy": why}
        identities = {
            side: [
                {"id": one["id"], "revision": one[side]} for one in members if one[side] is not None
            ]
            for side in ("base", "candidate")
        }
        return Item("reached_family", family, facts, identities)

    def _route_items(self, reached: set[str], renamed: Mapping[str, str]) -> list[Item]:
        """MIK-R06: the route conditions of the reached families and of every dead route."""

        inputs = self.inputs
        return [
            Item(
                ROUTE_CONDITION_KIND,
                condition.subject,
                condition.facts,
                condition.identities,
                {"satisfiedBy": condition.satisfied_by},
            )
            for condition in family_route_conditions(
                RouteInputs(
                    base=inputs.base,
                    candidate=inputs.candidate,
                    code_paths=inputs.code.candidate(),
                    base_code_paths=inputs.code.base(),
                    renamed=renamed,
                    owner=inputs.owner,
                ),
                reached,
            )
        ]

    # -- gate linkage (definition 8) -----------------------------------------------------------

    def _linkage(
        self, changes: tuple[TreeChange, ...], renamed: Mapping[str, str]
    ) -> list[dict[str, Any]]:
        return [self._change(change, renamed.get(change.path)) for change in changes]

    def _change(self, change: TreeChange, renamed_to: str | None) -> dict[str, Any]:
        code = self.inputs.code
        path = change.path
        base_blob, candidate_blob = code.base().get(path), code.candidate().get(path)
        document: dict[str, Any] = {
            "path": path,
            "status": change.status,
            "content": change.content,
            "modeChange": change.mode_change,
        }
        if renamed_to is not None:
            document["renamedTo"] = renamed_to
        hunks = self._path_hunks(change, base_blob, candidate_blob)
        if hunks is None or change.mode_change:
            # Non-text changes, and the mode fact of a mode change (definition 8), are linked at
            # file level; a text change's hunks are linked below all the same.
            document["fileLevel"] = {"linked": self._file_covered(path)}
        if hunks is None:
            return document
        base_spans = self._spans(self.inputs.base, path, base_blob)
        candidate_spans = self._spans(self.inputs.candidate, path, candidate_blob)
        document["hunks"] = [
            {**hunk.to_document(), "linked": _linked(hunk, base_spans, candidate_spans)}
            for hunk in hunks
        ]
        return document

    def _path_hunks(
        self, change: TreeChange, base_blob: str | None, candidate_blob: str | None
    ) -> tuple[Hunk, ...] | None:
        return change_hunks(self.inputs.code, change, base_blob, candidate_blob)

    def _spans(self, side: KnowledgeSide, path: str, blob: str | None) -> list[tuple[int, int]]:
        if blob is None:
            return []
        spans: list[tuple[int, int]] = []
        for entry_id in side.entries_by_path.get(path, ()):
            anchor = side.entries[entry_id].entry.anchor
            resolved = self.inputs.code.resolve(
                path, anchor.locator.to_document(), anchor.blob, blob
            )
            if resolved is not None:
                spans.append(resolved.span)
        return spans

    def _file_covered(self, path: str) -> bool:
        return non_text_linked(
            side.entries[entry_id].entry.anchor.locator.kind
            for side in (self.inputs.base, self.inputs.candidate)
            for entry_id in side.entries_by_path.get(path, ())
        )


def non_text_linked(locator_kinds: Iterable[str]) -> bool:
    """Whether a non-text change is linked (definition 8): a ``file``-locator entry covers its path.

    ``locator_kinds`` are the locator kinds of every entry either knowledge side records at the path.
    """

    return any(kind == "file" for kind in locator_kinds)


def _linked(
    hunk: Hunk, base_spans: Iterable[tuple[int, int]], candidate_spans: Iterable[tuple[int, int]]
) -> bool:
    """Definition 8: the hunk's *changed lines* intersect an entry's range on their own side.

    A side with no changed line links nothing there, symmetrically: a delete-only hunk changes no
    line at C, so only a K_B range at B links it (MIK-R10 rule 4); an insertion-only hunk changes
    no line at B, so only a K_C range at C links it (MIK-R09, carried from the L10 review, N3).
    """

    return (hunk.old_count > 0 and any(hits_old(hunk, span) for span in base_spans)) or (
        hunk.new_count > 0 and any(hits_new(hunk, span) for span in candidate_spans)
    )


def git_failure(error: BaseException) -> Incomplete:
    """A Git call that failed or timed out (``SubprocessError``) is unreadable input, named (rule 4)."""

    return Incomplete("git", f"a Git call failed or timed out ({type(error).__name__}: {error})")


def _class_counts(classified: Iterable[Classification]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for one in classified:
        counts[one.entry_class] = counts.get(one.entry_class, 0) + 1
    return dict(sorted(counts.items()))
