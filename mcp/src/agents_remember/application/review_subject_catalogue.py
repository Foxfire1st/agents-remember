"""The Intent Reviewer's subject catalogue: every recorded subject of both snapshots, labelled.

This module owns the entry half's enumeration, which :mod:`agents_remember.application.knowledge_review`
delegates to. It answers one question -- which invariant and family identities does the resolved
pair record -- from the two snapshots' own identity tables and from nothing else:

* **Both snapshots.** The before and after stores are each read through their own
  ``list_invariants``/``list_families`` operations, and the union is offered. A subject the before
  snapshot records and the candidate does not is ``before_only``: the knowledge history is
  append-only, so a retired subject is neither gone nor unreviewable, and listing it is what keeps
  the catalogue complete. One only the candidate records is ``after_only``.
* **No comparison.** Listing an identity never runs the shipped comparison for it. A catalogue read
  is four indexed identity listings, however many historical subjects the append-only pair holds,
  so displaying the task source never waits on comparing every historical subject first. What one
  subject's review renders is still the comparison's own answer when that subject alone is opened.
* **No silent drops.** Every recorded identity is listed with the presence above. A subject whose
  review cannot be composed is not removed here to imply a smaller population: opening it answers
  with the comparison's own typed refusal, which names the subject and its reason, while the other
  subjects and the task-context source review stay accessible.
* **Zero subjects is an empty catalogue**, which the caller renders as no entry beside the source
  inventory rather than as an invitation to name a subject.
"""

from __future__ import annotations

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    review_namespace,
)
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)
from agents_remember.models.knowledge.review import (
    ReviewEntry,
    ReviewSubjectKind,
)

__all__ = ["read_subject_catalogue"]

# One listed identity before it becomes an entry: the kind, the recorded id, the recorded label,
# and which side the label was read from. The label travels with its side so the union below can
# prefer the live side's wording without mixing two snapshots' spellings into one string.
_RecordedIdentity = tuple[ReviewSubjectKind, str, str]


def read_subject_catalogue(resolved: ReviewCandidateResolution) -> tuple[ReviewEntry, ...]:
    """List every invariant/family identity the resolved pair records, invariants before families.

    The namespace is read once from the candidate's own receipt and both snapshots are listed
    under it, so one catalogue read cannot offer its subjects under two different namespaces: the
    recorded one is what the catalogue and the review an entry opens both use. Within each kind
    the candidate side orders -- it is the live side a reader extends -- and the before side's
    retired subjects of that kind follow in the before snapshot's own order, so a retired
    invariant still sorts with the invariants rather than after the families.
    """

    namespace = review_namespace(resolved.repository_id, resolved.candidate_database)
    before_store = open_existing_knowledge_store(resolved.baseline_database, namespace)
    try:
        before = _side_identities(before_store)
    finally:
        before_store.close()
    after_store = open_existing_knowledge_store(resolved.candidate_database, namespace)
    try:
        after = _side_identities(after_store)
    finally:
        after_store.close()
    return _union(before, after)


def _side_identities(store: OpenedKnowledgeStore) -> tuple[_RecordedIdentity, ...]:
    """Every reviewable identity one snapshot records, invariants before families.

    Read through the store's own two list operations rather than through a query written here, so
    the identities offered are the ones the namespace records and not the ones a second reader of
    the same tables believes it finds.
    """

    invariants: tuple[_RecordedIdentity, ...] = tuple(
        ("invariant", invariant.invariant_id, invariant.display_label)
        for invariant in store.list_invariants()
    )
    families: tuple[_RecordedIdentity, ...] = tuple(
        ("family", family.family_id, family.display_label) for family in store.list_families()
    )
    return invariants + families


def _union(
    before: tuple[_RecordedIdentity, ...], after: tuple[_RecordedIdentity, ...]
) -> tuple[ReviewEntry, ...]:
    """Merge two snapshots' identities into labelled catalogue entries with their presence.

    The outer loop is the subject kind, so the catalogue is kind-grouped whatever each snapshot
    holds: every invariant (the candidate's first, then the before side's retired ones) precedes
    every family. The after side's label wins when both sides record the identity, because the
    candidate is the side a reader acts on; a retired subject keeps its before snapshot's own
    wording rather than receiving a label nothing recorded.
    """

    before_by_key: dict[tuple[ReviewSubjectKind, str], str] = {
        (kind, identity_id): label for kind, identity_id, label in before
    }
    after_by_key: dict[tuple[ReviewSubjectKind, str], str] = {
        (kind, identity_id): label for kind, identity_id, label in after
    }
    entries: list[ReviewEntry] = []
    for kind in ("invariant", "family"):
        entries.extend(_after_kind_rows(kind, after, before_by_key))
        entries.extend(_before_only_kind_rows(kind, before, after_by_key))
    return tuple(entries)


def _after_kind_rows(
    kind: ReviewSubjectKind,
    after: tuple[_RecordedIdentity, ...],
    before_by_key: dict[tuple[ReviewSubjectKind, str], str],
) -> list[ReviewEntry]:
    """The candidate's rows of one kind, each with the presence both snapshots give it."""

    return [
        ReviewEntry(
            selector_kind=kind,
            selector_id=identity_id,
            label=label,
            presence="both" if (kind, identity_id) in before_by_key else "after_only",
        )
        for row_kind, identity_id, label in after
        if row_kind == kind
    ]


def _before_only_kind_rows(
    kind: ReviewSubjectKind,
    before: tuple[_RecordedIdentity, ...],
    after_by_key: dict[tuple[ReviewSubjectKind, str], str],
) -> list[ReviewEntry]:
    """The baseline's retired rows of one kind, in the before snapshot's own order."""

    return [
        ReviewEntry(
            selector_kind=kind,
            selector_id=identity_id,
            label=label,
            presence="before_only",
        )
        for row_kind, identity_id, label in before
        if row_kind == kind and (kind, identity_id) not in after_by_key
    ]
