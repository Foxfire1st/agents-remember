"""The comparison's complete navigation tree, from its two derived indexes only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import apsw

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_meaning_effects import outline_meanings
from agents_remember.application.review_record_changes import RecordChanges
from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndex,
    Record,
    text_uuid,
)
from agents_remember.models.knowledge.review_meaning_effects import MeaningRecordKind
from agents_remember.models.knowledge.review_outline import (
    ReviewOutline,
    ReviewOutlineFamily,
    ReviewOutlineInvariant,
    ReviewOutlineMember,
)


@dataclass(frozen=True)
class _Side:
    name: str
    index: KnowledgeIndex
    problems: Mapping[str, str]
    detail: str | None = None


def read_review_outline(resolved: ReviewCandidateResolution) -> ReviewOutline:
    """Enumerate meaning and membership without opening source, a roster, or a subject review."""

    if resolved.trees is None:
        return _unavailable("This comparison has no converted memory trees to outline.")
    try:
        with (
            KnowledgeIndex(resolved.baseline_database) as before,
            KnowledgeIndex(resolved.candidate_database) as after,
        ):
            return _outline(before, after, resolved.leaf_id)
    except (apsw.Error, IndexMismatchError) as error:
        return _unavailable(f"The comparison's memory indexes could not be read: {error}")


def _unavailable(detail: str) -> ReviewOutline:
    return ReviewOutline(state="unavailable", detail=detail[:20_000])


def _outline(before: KnowledgeIndex, after: KnowledgeIndex, owner: str) -> ReviewOutline:
    sides = (
        _Side("before", before, dict(before.state.problems)),
        _Side("after", after, dict(after.state.problems)),
    )
    changes = RecordChanges()
    families: list[ReviewOutlineFamily] = []
    invariants: list[ReviewOutlineInvariant] = []
    # One owner-scoped history read serves every row's recorded effects (MIK-R43 rule 9):
    # the same values the reading area's block shows, from the after tree's index.
    # The baseline resolves the rare UUID the after map misses, exactly as the
    # selected answer does (MIK-R43 rule 1: retired and after-absent records).
    meanings = outline_meanings(
        after, owner, _meaning_subjects(before, after), identity_index=before
    )
    for kind in ("family", "invariant"):
        records = [{r.id: r for r in side.index.records_of_kind(kind).value} for side in sides]
        for side, held in zip(sides, records, strict=True):
            changes.records.update(
                ((side.name, identity), record) for identity, record in held.items()
            )
        old, new = records
        # Candidate order, followed by identities only the baseline holds, just as in the catalogue.
        for identity in dict.fromkeys((*new, *old)):
            record = new.get(identity) or old[identity]
            if kind == "invariant":
                invariants.append(
                    ReviewOutlineInvariant(
                        selector_id=text_uuid("identity", identity),
                        id=identity,
                        summary=_summary(str(record.document["statement"])),
                        change=changes.intent(sides, identity).change,
                        meaning=meanings[identity],
                    )
                )
                continue
            change, _ = changes.guarantee(sides, identity)
            members: list[ReviewOutlineMember] = []
            old_members = _members(old.get(identity))
            new_members = _members(new.get(identity))
            for member in dict.fromkeys((*new_members, *old_members)):
                fact = changes.membership(sides, identity, member)
                if fact.state == "unknown":
                    return _unavailable("; ".join(fact.reasons))
                members.append(
                    ReviewOutlineMember(
                        selector_id=text_uuid("identity", member),
                        id=member,
                        membership=fact.membership,
                        meaning=meanings[member],
                    )
                )
            families.append(
                ReviewOutlineFamily(
                    selector_id=text_uuid("identity", identity),
                    id=identity,
                    title=str(record.document["title"]),
                    change=change,
                    members=tuple(members),
                    meaning=meanings[identity],
                )
            )
    return ReviewOutline(
        state="outlined",
        detail="Recorded meaning and membership from the comparison's two memory indexes.",
        families=tuple(families),
        invariants=tuple(invariants),
    )


def _meaning_subjects(
    before: KnowledgeIndex, after: KnowledgeIndex
) -> list[tuple[str, MeaningRecordKind]]:
    """Every record the outline lists, with its kind, in outline order.

    Candidate order first, then identities only the baseline holds -- the same
    population the outline below enumerates, so every row finds its meaning.
    """

    subjects: list[tuple[str, MeaningRecordKind]] = []
    seen: set[str] = set()
    for kind in ("family", "invariant"):
        for index in (after, before):
            for record in index.records_of_kind(kind).value:
                if record.id in seen:
                    continue
                seen.add(record.id)
                subjects.append((record.id, "family" if kind == "family" else "invariant"))
                if kind == "family":
                    for member in _members(record):
                        if member not in seen:
                            seen.add(member)
                            subjects.append((member, "invariant"))
    return subjects


def _members(record: Record | None) -> tuple[str, ...]:
    if record is None or record.status == "retired":
        return ()
    return tuple(record.document.get("members", ()))


def _summary(statement: str) -> str:
    """At most 120 characters, preserving complete words at the cut."""

    if len(statement) <= 120:
        return statement
    prefix = statement[:120]
    if prefix[-1].isspace() or statement[120].isspace():
        return prefix.rstrip()
    return prefix.rsplit(maxsplit=1)[0] if any(character.isspace() for character in prefix) else ""
