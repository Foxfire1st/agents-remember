"""Base-defect witness for ICR-R31 (no candidate code): what the base review payload can say about families.

Builds the same leaf enclosure the leaf's cases build -- a real worktree, a real contract, two real
knowledge datasets -- authors the same *successor family revision* and memberships through the
public store operations, and then asks the base composition (``read_knowledge_review``) the question
the accepted reviewer asks: which recorded families does the selected invariant belong to, what is
each selected family revision's own authored guarantee, and which exact members does it record.

It reports, as measurements rather than assertions:

* whether the served payload carries any family-context value at all;
* which family/membership facts the payload's own collections do carry (the R08 relationship union),
  and whether a guarantee text or a roster is obtainable from them;
* whether the payload's page-bound union items carry the successor family revision and the membership
  that cites it.

Run against the base checkout with that checkout's own ``mcp/src`` on the path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from agents_remember.application.knowledge_review import read_knowledge_review
from agents_remember.memory.knowledge import families, memberships
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.result import (
    FamilyMemberRequest,
    FamilyRevisionRequest,
    RevisionDraft,
    RevisionRequest,
)
from test_knowledge_review_source_endpoints import _place_datasets, build_endpoint_fixture

GUARANTEE = "The retry budget and the batch obligation hold together under the revised member."
MEMBER_STATEMENT = "Apply every admitted candidate change as one sealed batch."


def main(directory: Path) -> None:
    """Author one family movement and report what the base composition publishes about it."""

    endpoints = build_endpoint_fixture(directory / "endpoints")
    diff = endpoints.diff
    fixture = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        member_successor = str(uuid4())
        created = store.create_revision(
            RevisionRequest(
                repository_id=diff.repository_id,
                revision=RevisionDraft(
                    revision_id=member_successor,
                    invariant_id=fixture.batch_invariant_id,
                    display_version="v2",
                    statement=MEMBER_STATEMENT,
                    applicability="Every admitted candidate write in this repository namespace.",
                    predecessors=(fixture.batch_revision_id,),
                    provenance=fixture.authorship,
                ),
            )
        )
        assert created.state == "created", created.refusal
        successor = str(uuid4())
        authored = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=diff.repository_id,
                revision=FamilyRevisionDraft(
                    family_id=fixture.direct_family.family_id,
                    revision_id=successor,
                    display_version="v1",
                    joint_guarantee=GUARANTEE,
                    predecessors=(fixture.direct_family.revision_id,),
                    provenance=fixture.authorship,
                ),
            ),
        )
        assert authored.state == "created", authored.refusal
        for member in (diff.subject_revision_id, member_successor):
            written = memberships.create_family_member(
                store,
                FamilyMemberRequest(
                    repository_id=diff.repository_id,
                    member=FamilyMemberDraft(
                        member_id=str(uuid4()),
                        family_revision_id=successor,
                        invariant_revision_id=member,
                        provenance=fixture.authorship,
                    ),
                ),
            )
            assert written.state == "created", written.refusal
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)

    result = read_knowledge_review(endpoints.config, endpoints.request())
    print("review state                     :", result.state)
    payload = result.payload
    assert payload is not None
    fields = set(type(payload).model_fields)
    print("payload carries family context   :", "family_context" in fields)
    print("payload fields                   :", ",".join(sorted(fields)))
    movements = [
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind == "membership"
    ]
    print("membership movements in payload   :", len(movements))
    for movement in movements:
        print(
            "  movement                        :",
            movement.record_id,
            movement.transition,
            "before=",
            [
                (side.relationship_id, side.revision_id, side.member_revision_id)
                for side in movement.before
            ],
            "after=",
            None
            if movement.after is None
            else (
                movement.after.relationship_id,
                movement.after.revision_id,
                movement.after.member_revision_id,
            ),
        )
    carries_guarantee = any(
        "guarantee" in movement.model_dump_json() or GUARANTEE in movement.model_dump_json()
        for movement in movements
    )
    print("movement carries a guarantee text :", carries_guarantee)
    member_revisions = {
        side.member_revision_id
        for movement in movements
        for side in (*movement.before, *([] if movement.after is None else [movement.after]))
    }
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        recorded_roster = memberships.list_members(store, successor)
    finally:
        store.close()
    print("distinct member revisions shown   :", len(member_revisions))
    print("successor family revision shown   :", successor in payload.model_dump_json())
    print("successor member revision shown   :", member_successor in payload.model_dump_json())
    print("guarantee text shown anywhere     :", GUARANTEE in payload.model_dump_json())
    print("member statement shown anywhere   :", MEMBER_STATEMENT in payload.model_dump_json())
    print("successor roster the store records:", len(recorded_roster.members), "membership(s)")
    print(
        "roster memberships the payload shows:",
        sum(
            1
            for movement in movements
            for side in (*movement.before, *([] if movement.after is None else [movement.after]))
            if side.revision_id == successor
        ),
    )


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/home/firefox/projects/ar-coordination/temp/l31-base/scratch"))
