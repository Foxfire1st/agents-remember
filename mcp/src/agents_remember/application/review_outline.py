"""The comparison's complete navigation tree, from its two derived indexes only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import apsw

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_record_changes import RecordChanges
from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndex,
    Record,
    text_uuid,
)
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
            return _outline(before, after)
    except (apsw.Error, IndexMismatchError) as error:
        return _unavailable(f"The comparison's memory indexes could not be read: {error}")


def _unavailable(detail: str) -> ReviewOutline:
    return ReviewOutline(state="unavailable", detail=detail[:20_000])


def _outline(before: KnowledgeIndex, after: KnowledgeIndex) -> ReviewOutline:
    sides = (
        _Side("before", before, dict(before.state.problems)),
        _Side("after", after, dict(after.state.problems)),
    )
    changes = RecordChanges()
    families: list[ReviewOutlineFamily] = []
    invariants: list[ReviewOutlineInvariant] = []
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
                    )
                )
            families.append(
                ReviewOutlineFamily(
                    selector_id=text_uuid("identity", identity),
                    id=identity,
                    title=str(record.document["title"]),
                    change=change,
                    members=tuple(members),
                )
            )
    return ReviewOutline(
        state="outlined",
        detail="Recorded meaning and membership from the comparison's two memory indexes.",
        families=tuple(families),
        invariants=tuple(invariants),
    )


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
