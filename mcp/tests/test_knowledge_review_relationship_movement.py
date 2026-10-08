"""The reviewer's relationship union and display over synthetic index-shaped unit rows.

The fixture keeps meaningful reader cases that a text tree cannot author, such as multi-revision
lineage. It calls the actual composition with explicit test-only row inputs; public resolution is
covered by the converted-tree suite. The scalar governing-route retirement case uses that real tree
fixture and verifies the family route sets remain available through their existing reader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import apsw
import pytest
from agents_remember.application.knowledge_review import read_complete_knowledge_review
from agents_remember.application.review_governing_route import governing_route_movements
from agents_remember.application.review_recorded_relationships import RecordedSnapshot
from agents_remember.memory.knowledge_index import KnowledgeIndex, text_uuid
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.read import FamilyIdentitySeed, InvariantIdentitySeed
from agents_remember.models.knowledge.source import FileLocator
from agents_remember.models.knowledge_files import canonical_text
from anchor_fixture_models import GitBlobIdentity, RealizationClaimDraft, SourceAnchorDraft
from diff_scope_test_support import (
    SUCCESSOR_PATH,
    _claim_row_digest,
    _git,
)
from knowledge_rows_test_support import (
    FamilyMemberRequest,
    FamilyRevisionRequest,
    NewAnchor,
    RealizationClaimRequest,
    RemoveFamilyMemberRequest,
    RemoveRealizationClaimRequest,
    RevisionDraft,
    RevisionRequest,
    RowStore,
    families,
    memberships,
    open_knowledge_store,
    realizations,
)
from read_scope_test_support import (
    ABSENT_PATH,
    AUXILIARY_PATH,
    BASE_LABEL,
    DIRECT_FAMILY_GUARANTEE,
    INTEGRATION_PATH,
    SYNCHRONIZATION_PATH,
)
from test_knowledge_review_source_endpoints import (
    EndpointFixture,
    _place_datasets,
    build_endpoint_fixture,
    compose_endpoint_review,
)
from test_review_git_trees import FAMILY, _resolve, build_world

pytestmark = pytest.mark.evidence_unit

# The candidate's own content for the moved realization's new path. It is deliberately a near-copy of
# the baseline file the withdrawn claim recorded, because that is what makes the source movement a
# *rename* to Git's own similarity detection -- and therefore what makes the packet's rename clause
# measurable rather than asserted.
RENAMED_PATH_TEXT = "# synchronization\nshared retry budget\npropagated\nmoved by the candidate\n"

# One more candidate-only path, carrying the authored merge revision's realization.
MERGE_PATH = "src/retry_merge.py"
MERGE_PATH_TEXT = "# retry merge\nrecords both predecessors' budget\n"

# The identities of the governing-route read's own in-memory snapshots.
ROUTE_REPOSITORY = "repository"
ROUTE_FAMILY = text_uuid("identity", FAMILY)
ROUTE_INVARIANT = text_uuid("identity", "INV-R00001")


@dataclass(frozen=True)
class ClaimDraft:
    """One realization to author: its claim, the revision it cites, its path and its recorded blob."""

    claim_id: str
    revision_id: str
    path: str
    blob: str


@dataclass(frozen=True)
class MovementFixture:
    """One live enclosure whose two datasets record every movement these cases read back."""

    endpoints: EndpointFixture
    merge_revision_id: str
    merge_claim_id: str
    new_family_revision_id: str
    removed_member_id: str
    added_member_id: str

    @property
    def diff(self):
        return self.endpoints.diff


@pytest.fixture(scope="module")
def movement_fixture(tmp_path_factory: pytest.TempPathFactory) -> MovementFixture:
    """Build the enclosure and record every movement through the store's own operations."""

    return build_movement_fixture(tmp_path_factory.mktemp("movement"))


def build_movement_fixture(directory: Path) -> MovementFixture:
    """Populate synthetic index-shaped rows and the real source trees their anchors name.

    The row fixture keeps lineage and movement reader cases the text format cannot author. It is
    test-only input to the actual composition, and exercises no retired production writer.
    """

    endpoints = build_endpoint_fixture(directory / "endpoints")
    diff = endpoints.diff
    worktree = endpoints.worktree
    # The source rename: the path the withdrawn claim recorded is gone and the path the candidate's
    # claim records holds a near-copy of it, so Git's own similarity detection has a rename to report.
    (worktree / SUCCESSOR_PATH).write_text(RENAMED_PATH_TEXT, encoding="utf-8")
    (worktree / MERGE_PATH).write_text(MERGE_PATH_TEXT, encoding="utf-8")
    # The withdrawn association's own file is deleted from the candidate tree as well, which is the
    # boundary the packet names: a deleted source with a retained invariant, displayed with the link
    # the candidate no longer records rather than disappearing with the file.
    (worktree / AUXILIARY_PATH).unlink()
    successor_blob = _git(worktree, ["hash-object", SUCCESSOR_PATH])
    merge_blob = _git(worktree, ["hash-object", MERGE_PATH])
    merge_revision_id = str(uuid4())
    merge_claim_id = str(uuid4())

    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        remove_claim(store, diff.added_claim_id)
        remove_claim(store, diff.before.fixture.auxiliary.claim_id)
        # The merge revision is authored before the claim that cites it: a relation names an exact
        # revision in this namespace, so the store refuses the claim otherwise -- which is the write
        # path's own ordering rule rather than a fixture detail.
        author_revision(store, diff, merge_revision_id, predecessors=_merge_predecessors(diff))
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=merge_claim_id,
                revision_id=merge_revision_id,
                path=MERGE_PATH,
                blob=merge_blob,
            ),
        )
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=str(uuid4()),
                revision_id=diff.revised_revision_id,
                path=SUCCESSOR_PATH,
                blob=successor_blob,
            ),
        )
        family_revision_id, removed_member_id, added_member_id = _reassign_family(store, diff)
    finally:
        store.close()

    _place_datasets(diff, endpoints.contract)
    return MovementFixture(
        endpoints=endpoints,
        merge_revision_id=merge_revision_id,
        merge_claim_id=merge_claim_id,
        new_family_revision_id=family_revision_id,
        removed_member_id=removed_member_id,
        added_member_id=added_member_id,
    )


def _merge_predecessors(diff) -> tuple[str, ...]:
    """The retry identity's two revisions the authored merge names together.

    Both must be revisions *of the reviewed invariant*: the write path refuses a predecessor from
    another identity ("predecessors must stay within one invariant"), which is why this names the
    retry identity's own base and subject revisions rather than any two revisions of the fixture.
    """

    return (diff.subject_revision_id, diff.before.fixture.base_revision_id)


def author_claim(
    store: RowStore,
    diff,
    draft: ClaimDraft,
) -> None:
    """Author one realization claim at one path, recording the blob the candidate tree really holds."""

    created = realizations.create_realization_claim(
        store,
        RealizationClaimRequest(
            repository_id=store.repository_id,
            claim=RealizationClaimDraft(
                claim_id=draft.claim_id,
                invariant_revision_id=draft.revision_id,
                role="enforcement",
                rationale="The candidate records where the moved obligation is realized.",
            ),
            anchor=NewAnchor(
                anchor=SourceAnchorDraft(
                    anchor_id=UUID(str(uuid4())),
                    path=draft.path,
                    source_identity=GitBlobIdentity(object_id=draft.blob),
                    locator=FileLocator(),
                )
            ),
            provenance=diff.before.fixture.authorship,
        ),
    )
    assert created.state == "created", created.refusal


def remove_claim(store: RowStore, claim_id: str) -> None:
    """Withdraw one realization through the shipped removal operation."""

    removed = realizations.remove_realization_claim(
        store,
        RemoveRealizationClaimRequest(
            repository_id=store.repository_id,
            claim_id=claim_id,
            expected_row_digest=_claim_row_digest(store, claim_id),
        ),
    )
    assert removed.state == "removed", removed.refusal


def author_revision(
    store: RowStore,
    diff,
    revision_id: str,
    *,
    predecessors: tuple[str, ...],
    invariant_id: str | None = None,
) -> None:
    """Author one successor revision of an identity, naming its exact predecessors."""

    created = store.create_revision(
        RevisionRequest(
            repository_id=store.repository_id,
            revision=RevisionDraft(
                revision_id=revision_id,
                invariant_id=invariant_id or diff.retry_invariant_id,
                display_version=BASE_LABEL,
                statement="Retries record both predecessors' budgets before the first refusal.",
                applicability="Every retry the shared budget admits in this repository namespace.",
                conditions=("The first refusal is recorded before the budget is re-armed.",),
                exclusions=(),
                predecessors=predecessors,
                provenance=diff.before.fixture.authorship,
            ),
        )
    )
    assert created.state == "created", created.refusal


def _reassign_family(store: RowStore, diff) -> tuple[str, str, str]:
    """Move one member from the baseline's family revision to an authored successor revision.

    Two store operations and nothing else: the baseline membership is withdrawn by identity and row
    digest, a new family revision is authored naming the baseline's as its predecessor, and the moved
    member revision is authored as a member of it.
    """

    baseline_member = memberships.find_membership_by_pair(
        store, diff.before.fixture.direct_family.revision_id, diff.subject_revision_id
    )
    assert baseline_member is not None
    removed = memberships.remove_family_member(
        store,
        RemoveFamilyMemberRequest(
            repository_id=store.repository_id,
            member_id=baseline_member.member_id,
            expected_row_digest=baseline_member.row_digest,
        ),
    )
    assert removed.state == "removed", removed.refusal
    new_revision_id = str(uuid4())
    authored = families.create_family_revision(
        store,
        FamilyRevisionRequest(
            repository_id=store.repository_id,
            revision=FamilyRevisionDraft(
                family_id=diff.before.fixture.direct_family.family_id,
                revision_id=new_revision_id,
                display_version=BASE_LABEL,
                joint_guarantee=DIRECT_FAMILY_GUARANTEE,
                predecessors=(diff.before.fixture.direct_family.revision_id,),
                provenance=diff.before.fixture.authorship,
            ),
        ),
    )
    assert authored.state == "created", authored.refusal
    added_member_id = str(uuid4())
    member = memberships.create_family_member(
        store,
        FamilyMemberRequest(
            repository_id=store.repository_id,
            member=FamilyMemberDraft(
                member_id=added_member_id,
                family_revision_id=new_revision_id,
                invariant_revision_id=diff.revised_revision_id,
                provenance=diff.before.fixture.authorship,
            ),
        ),
    )
    assert member.state == "created", member.refusal
    return new_revision_id, baseline_member.member_id, added_member_id


# The fix-round paths: a *member* identity's moved realization, the same-citation row, and the family

# --- the review under test ---------------------------------------------------------------------


def review(fixture: MovementFixture, invariant_id: str | None = None):
    """Compose the selected subject over explicit synthetic unit inputs."""

    request = fixture.endpoints.request(invariant_id)
    result = compose_endpoint_review(fixture.endpoints, request)
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def review_of(endpoints, invariant_id: str):
    """Compose one subject over explicit synthetic unit inputs."""

    result = compose_endpoint_review(endpoints, endpoints.request(invariant_id))
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def realization_movements(payload):
    """Every realization relationship the payload displays, in the union's own order."""

    return tuple(
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind == "realization"
    )


def movement_between(payload, before_path: str, after_path: str):
    """The one movement whose recorded sides name these two addresses."""

    found = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None
        and movement.after.path == after_path
        and any(side.path == before_path for side in movement.before)
    ]
    assert len(found) == 1, [
        (m.transition, [s.path for s in m.before], m.after and m.after.path)
        for m in realization_movements(payload)
    ]
    return found[0]


# -- a realization that moved shows both paths under one identity --------------------------------


def test_a_moved_realization_displays_both_recorded_paths_under_one_invariant_identity(
    movement_fixture: MovementFixture,
) -> None:
    """Realization A to B is one relationship: both addresses, one preserved invariant identity.

    This is the packet's conforming example as a measurement over the production composition: the
    baseline claim at ``src/synchronization.py`` and the candidate's claim at ``src/retry_interval.py``
    are the two sides of one movement, the identity both sit under is the reviewed invariant, and each
    of the two locations the pane lists for them carries the same identity and names the other
    address.
    """

    fixture = movement_fixture
    payload = review(fixture)
    movement = movement_between(payload, SYNCHRONIZATION_PATH, SUCCESSOR_PATH)

    assert movement.transition == "moved"
    assert movement.record_kind == "invariant"
    assert movement.record_id == fixture.diff.retry_invariant_id
    # Two baseline relationships the candidate withdrew are continued by the one candidate
    # relationship: the movement displays all of them rather than choosing one to be "the" old side.
    assert {side.path for side in movement.before} == {SYNCHRONIZATION_PATH, INTEGRATION_PATH}
    assert movement.after is not None
    assert movement.after.path == SUCCESSOR_PATH
    assert {side.resolution for side in movement.before} == {"exact_recorded_blob"}
    assert movement.after.resolution == "exact_recorded_blob"

    located = {
        location.path: location
        for location in payload.source.locations
        if location.path in (SYNCHRONIZATION_PATH, SUCCESSOR_PATH)
    }
    assert set(located) == {SYNCHRONIZATION_PATH, SUCCESSOR_PATH}
    assert {location.invariant_id for location in located.values()} == {
        fixture.diff.retry_invariant_id
    }
    assert located[SYNCHRONIZATION_PATH].recorded_side == "before"
    assert located[SYNCHRONIZATION_PATH].counterpart_path == SUCCESSOR_PATH
    assert located[SUCCESSOR_PATH].recorded_side == "after"
    # Two recorded baseline addresses, so no single one is chosen to stand for the other: the
    # movement lists both and the location names none.
    assert located[SUCCESSOR_PATH].counterpart_path is None
    assert located[SYNCHRONIZATION_PATH].before_only is True
    assert located[SYNCHRONIZATION_PATH].transition == "moved"
    assert located[SUCCESSOR_PATH].movement == movement
    # One row per recorded side, not one per movement that displays it: the pane's address view is a
    # list of addresses, and a baseline address two movements continue is still one address.
    assert [location.path for location in payload.source.locations].count(SYNCHRONIZATION_PATH) == 1


def test_only_the_after_graph_is_read_so_the_old_association_vanishes(
    movement_fixture: MovementFixture,
) -> None:
    """The packet's non-conforming reading, measured: the after side cannot produce the old address.

    The population the after side alone names is a real set -- every candidate-side claim address --
    and the baseline address the relationship moved away from is not in it. The union traversal the
    payload is built from does hold it, with the invariant identity and the authored edge beside it,
    so the two readings are distinguished by what they can display rather than by a claim about them.
    """

    fixture = movement_fixture
    payload = review(fixture)
    after_only_paths = {
        movement.after.path
        for movement in realization_movements(payload)
        if movement.after is not None
    }
    assert SYNCHRONIZATION_PATH not in after_only_paths, (
        "the candidate's own graph must not reach the baseline address, or this measures nothing"
    )
    assert SUCCESSOR_PATH in after_only_paths

    union = movement_between(payload, SYNCHRONIZATION_PATH, SUCCESSOR_PATH)
    assert {side.path for side in union.before} == {SYNCHRONIZATION_PATH, INTEGRATION_PATH}
    lineage = {entry.kind for entry in union.lineage}
    assert "succession" in lineage, [entry.statement for entry in union.lineage]
    assert fixture.diff.subject_revision_id in {
        revision
        for entry in union.lineage
        for revision in (entry.revision_id, *entry.related_revision_ids)
    }


def test_a_withdrawn_realization_stays_visible_with_its_deleted_file_and_its_identity(
    movement_fixture: MovementFixture,
) -> None:
    """A retraction keeps its side and reason, and the deleted file keeps its inventory entry.

    The candidate no longer holds the claim at ``src/anchors.py`` and its file is gone from the
    candidate tree. The movement is a retraction with the baseline side still listed, the invariant
    identity it sat under still displayed, and the deleted path still an inventory entry with its own
    status -- deleting the source did not erase the recorded invariant or the former association.
    """

    fixture = movement_fixture
    payload = review(fixture)
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 1, [
        (movement.transition, [side.path for side in movement.before])
        for movement in realization_movements(payload)
    ]
    movement = withdrawn[0]

    assert movement.after is None
    assert movement.record_id == fixture.diff.before.fixture.auxiliary_invariant_id
    assert movement.before[0].path == AUXILIARY_PATH
    assert "withdrawn" in movement.statement
    assert "no authored successor" in movement.statement
    location = next(
        location for location in payload.source.locations if location.path == AUXILIARY_PATH
    )
    assert location.before_only is True
    assert location.invariant_id == fixture.diff.before.fixture.auxiliary_invariant_id
    assert location.transition == "retracted"
    # The invariant is still recorded by the candidate, and the deleted file is still listed.
    assert fixture.diff.before.fixture.auxiliary_invariant_id in payload.knowledge.invariant_ids
    deleted = {entry.path: entry for entry in payload.source.inventory.entries}
    assert deleted[AUXILIARY_PATH].status == "deleted"


def test_a_record_the_other_selection_did_not_reach_is_not_displayed_as_a_deletion(
    movement_fixture: MovementFixture,
) -> None:
    """Present-but-outside-the-selection has its own transition and its own sentence.

    Reassigning the family moved the candidate's selection away from one membership the candidate's
    snapshot still holds. That relationship is displayed -- with its side, its family revision and its
    member -- under ``outside_selection``, and its sentence says the snapshot holds the record and the
    declared selection did not reach it rather than calling it a withdrawal.
    """

    fixture = movement_fixture
    payload = review(fixture)
    outside = [
        movement
        for movement in payload.source.relationships
        if movement.transition == "outside_selection"
    ]
    assert outside, [
        (movement.relationship_kind, movement.transition)
        for movement in payload.source.relationships
    ]
    movement = outside[0]
    assert movement.after is None
    assert movement.before[0].item_coverage == "present_outside_selection"
    assert movement.before[0].record_id is not None
    assert "did not reach this relationship" in movement.statement
    assert "never a deletion" in movement.statement
    assert movement.record_id == movement.before[0].record_id


# -- the source rename is a labelled inference, and it proves nothing -----------------------------


def test_a_source_rename_is_displayed_as_a_labelled_git_inference(
    movement_fixture: MovementFixture,
) -> None:
    """A rename is displayed with Git named as the inference's basis and never as proof of movement.

    The two bound trees hold a deleted path and a near-copy of it at the candidate's path, so Git's
    own similarity detection pairs them. The value says so, names the command that produced it, and
    says in its own sentence that it is a similarity inference about the source -- while the movement
    beside it is the two *recorded* anchors and the author's own successor edge.
    """

    fixture = movement_fixture
    payload = review(fixture)
    movement = movement_between(payload, SYNCHRONIZATION_PATH, SUCCESSOR_PATH)
    inference = movement.rename_inference

    assert inference is not None
    assert inference.state == "inferred", inference.statement
    assert inference.basis == "git_rename_detection"
    assert (inference.before_path, inference.after_path) == (SYNCHRONIZATION_PATH, SUCCESSOR_PATH)
    assert inference.similarity
    assert "find-renames" in inference.command
    assert "Git's own rename detection" in inference.statement
    assert "not proof that the invariant moved" in inference.statement
    # The movement's own sides are the recorded anchors -- both of them -- and the identity and edge
    # the snapshots record. Nothing in the movement is derived from the inference: the inference
    # names which recorded address Git's similarity detection matched, and that is all it does.
    assert SYNCHRONIZATION_PATH in {side.path for side in movement.before}
    assert INTEGRATION_PATH in {side.path for side in movement.before}
    assert movement.after is not None and movement.after.path == SUCCESSOR_PATH
    assert {entry.kind for entry in movement.lineage} >= {"succession"}


def build_no_authored_edge_fixture(directory: Path) -> EndpointFixture:
    """The same source rename as the conforming case, recorded with no authored edge at all.

    The withdrawn baseline claim is not replaced: the candidate's claim cites a revision that records
    *no* predecessor, while the two code trees still show the deleted path and its near-copy at the
    candidate's path. Nothing in the store connects the two recorded associations, so nothing may
    display them as one movement -- whatever Git's own similarity detection says about the files.
    """

    endpoints = build_endpoint_fixture(directory / "no-edge")
    diff = endpoints.diff
    worktree = endpoints.worktree
    (worktree / SUCCESSOR_PATH).write_text(RENAMED_PATH_TEXT, encoding="utf-8")
    blob = _git(worktree, ["hash-object", SUCCESSOR_PATH])
    independent_revision_id = str(uuid4())
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        remove_claim(store, diff.added_claim_id)
        author_revision(store, diff, independent_revision_id, predecessors=())
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=str(uuid4()),
                revision_id=independent_revision_id,
                path=SUCCESSOR_PATH,
                blob=blob,
            ),
        )
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def test_the_same_rename_with_no_authored_edge_is_a_retraction_and_an_addition(
    tmp_path: Path,
) -> None:
    """Git's rename inference never turns a source rename into a movement of the invariant.

    The same two code trees as the conforming case -- the deleted path and its near-copy at the
    candidate's path, so Git still pairs them -- but the candidate's claim cites a revision that
    records *no* predecessor. The display must not pair the two recorded associations: it shows the
    withdrawn baseline relationship and the candidate's own added one, and no relationship carries
    both addresses as its two sides.
    """

    endpoints = build_no_authored_edge_fixture(tmp_path)
    diff = endpoints.diff

    result = compose_endpoint_review(endpoints, endpoints.request(diff.retry_invariant_id))
    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    added = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path == SUCCESSOR_PATH
    ]
    assert [movement.transition for movement in added] == ["added"], [
        movement.transition for movement in added
    ]
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == SYNCHRONIZATION_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 1
    assert not any(
        movement.after is not None
        and movement.after.path == SUCCESSOR_PATH
        and any(side.path == SYNCHRONIZATION_PATH for side in movement.before)
        for movement in realization_movements(payload)
    ), "a Git rename pairing must not become a recorded movement"


# -- the authored split and merge ----------------------------------------------------------------


def test_the_authored_split_and_merge_are_displayed_from_the_candidates_own_edges(
    movement_fixture: MovementFixture,
) -> None:
    """Split and merge are read from the authored predecessor rows and name every related revision.

    The candidate's snapshot records two successors of the baseline's subject revision -- the revised
    revision and the unselected fourth one -- and the case then authors a merge revision naming two
    exact predecessors and a realization on it. Both movements display the authored relation, and the
    split names *all* the successors rather than the one a pairing happened to follow.
    """

    fixture = movement_fixture
    payload = review(fixture)
    revised = movement_between(payload, SYNCHRONIZATION_PATH, SUCCESSOR_PATH)
    splits = [entry for entry in revised.lineage if entry.kind == "split"]
    assert len(splits) == 1, [entry.statement for entry in revised.lineage]
    assert splits[0].revision_id == fixture.diff.subject_revision_id
    assert set(splits[0].related_revision_ids) == {
        fixture.diff.revised_revision_id,
        fixture.diff.unselected_revision_id,
        fixture.merge_revision_id,
    }
    assert splits[0].side == "before"

    merged = movement_between(payload, SYNCHRONIZATION_PATH, MERGE_PATH)
    merges = [entry for entry in merged.lineage if entry.kind == "merge"]
    assert len(merges) == 1, [entry.statement for entry in merged.lineage]
    assert merged.record_id == fixture.diff.retry_invariant_id
    assert merges[0].revision_id == fixture.merge_revision_id
    assert set(merges[0].related_revision_ids) == set(_merge_predecessors(fixture.diff))
    assert "authored predecessors" in merges[0].statement


# -- the family association and the governing route ----------------------------------------------


def test_a_family_association_reassigned_to_a_new_revision_displays_both_recorded_sides(
    movement_fixture: MovementFixture,
) -> None:
    """The membership's family revision and member revision both moved; the family identity did not.

    The baseline membership was withdrawn and a new one authored against the candidate's successor
    family revision, holding the moved member revision. The movement displays both recorded sides, and
    its transition is a *reassignment* rather than a member moving, because the member revision the
    candidate records is the one the baseline's successor recorded.
    """

    fixture = movement_fixture
    payload = review(fixture)
    memberships_shown = [
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind in ("membership", "advertised_family")
    ]
    reassigned = [
        movement
        for movement in memberships_shown
        if movement.after is not None
        and movement.after.revision_id == fixture.new_family_revision_id
    ]
    assert len(reassigned) == 1, [
        (movement.relationship_kind, movement.transition) for movement in memberships_shown
    ]
    movement = reassigned[0]

    # The member revision moved *and* the family revision it sits in moved, so the association is
    # displayed as moved; the family identity itself is preserved and named.
    assert movement.transition == "moved"
    assert movement.record_kind == "family"
    assert movement.record_id == fixture.diff.before.fixture.direct_family.family_id
    assert [side.revision_id for side in movement.before] == [
        fixture.diff.before.fixture.direct_family.revision_id
    ]
    assert len(movement.before) == 1
    assert movement.after is not None
    assert movement.after.member_revision_id == fixture.diff.revised_revision_id
    assert movement.before[0].member_revision_id == fixture.diff.subject_revision_id
    assert "succession" in {entry.kind for entry in movement.lineage}


def test_a_familys_declared_route_set_is_the_governing_association_read_from_text(
    tmp_path: Path,
) -> None:
    """A real converted tree: each route the family declares is one recorded association.

    The retired canonical join row never existed in a derived index, so the old read answered
    ``ungoverned`` for every identity. The route set in the family record file is the fact, and it is
    shown (MIK-R26) as one relationship per route: the base tree declares ``pkg`` and the candidate
    ``.`` and ``pkg``, so ``.`` is added and ``pkg`` is unchanged, and no value holds a joined list.
    """

    world = build_world(tmp_path)
    family_path = world.memory_worktree / f"knowledge/families/{FAMILY}-landing.json"
    family = json.loads(family_path.read_text())
    family["routes"] = [".", "pkg"]
    family_path.write_text(canonical_text(family))
    resolved = _resolve(world)
    with KnowledgeIndex(resolved.candidate_database) as index:
        assert index.family(FAMILY).value.routes == (".", "pkg")
    result = read_complete_knowledge_review(
        world.config,
        world.review(selector=FamilyIdentitySeed(family_id=text_uuid("identity", FAMILY))),
    )
    assert result.payload is not None, result.refusal
    added, unchanged = (
        movement
        for movement in result.payload.source.relationships
        if movement.relationship_kind == "governing_route"
    )
    assert (added.transition, added.before, added.pairing_basis) == ("added", (), None)
    assert added.after is not None and added.after.state == "recorded"
    assert (added.after.route_id, added.after.route_path) == (".", ".")
    assert added.after.record_kind == "family"
    assert "declares the governing route . on the candidate" in added.statement
    assert unchanged.transition == "unchanged" and unchanged.after is not None
    assert [side.route_path for side in (*unchanged.before, unchanged.after)] == ["pkg", "pkg"]
    assert [side.relationship_id for side in (*unchanged.before, unchanged.after)] == ["pkg", "pkg"]
    assert unchanged.pairing_basis == "same_governed_identity"


def _route_snapshot(side: str, routes: tuple[str, ...] | None, *, recorded: bool = True):
    """One in-memory snapshot holding the tables the governing-route read asks.

    ``routes`` is the family's declared set; ``None`` leaves out the route tables altogether, which
    is a file whose route declarations cannot be read. ``recorded`` says whether the identities exist.
    """

    connection = apsw.Connection(":memory:")
    connection.execute(
        "CREATE TABLE family (repository_id TEXT, family_id TEXT);"
        "CREATE TABLE invariant (repository_id TEXT, invariant_id TEXT)"
    )
    if recorded:
        connection.execute("INSERT INTO family VALUES (?, ?)", (ROUTE_REPOSITORY, ROUTE_FAMILY))
        connection.execute(
            "INSERT INTO invariant VALUES (?, ?)", (ROUTE_REPOSITORY, ROUTE_INVARIANT)
        )
    if routes is not None:
        connection.execute(
            "CREATE TABLE ix_uuid (uuid TEXT, id TEXT, role TEXT);"
            "CREATE TABLE ix_route (family TEXT, route TEXT)"
        )
        connection.execute("INSERT INTO ix_uuid VALUES (?, ?, 'identity')", (ROUTE_FAMILY, FAMILY))
        connection.executemany(
            "INSERT INTO ix_route VALUES (?, ?)", [(FAMILY, route) for route in routes]
        )
    return RecordedSnapshot(
        side=side, database=Path(":memory:"), connection=connection, edges=frozenset()
    )


def _routes(selector, before, after):
    """The governing-route movements as ``(transition, before states and routes, after)`` rows."""

    def shown(side):
        return None if side is None else (side.state, side.route_path)

    movements = governing_route_movements(selector, ROUTE_REPOSITORY, before, after)
    return movements, [
        (movement.transition, [shown(side) for side in movement.before], shown(movement.after))
        for movement in movements
    ]


def test_each_declared_route_is_one_relationship_and_an_unread_side_is_never_ungoverned() -> None:
    """The route read's own matrix: one relationship per route, and four states kept apart.

    A route both snapshots declare is unchanged; a route one snapshot declares while the other is
    governed by other routes is one-sided; an identity with no route to show contributes its state.
    A file without the route tables was not read: it is ``unavailable`` with its own gap and the
    comparison is ``unresolved``, never ``ungoverned`` and never ``unchanged``.
    """

    family = FamilyIdentitySeed(family_id=ROUTE_FAMILY)

    movements, rows = _routes(
        family, _route_snapshot("before", ("a", "b")), _route_snapshot("after", ("b", "c"))
    )
    assert rows == [
        ("retracted", [("recorded", "a")], None),
        ("unchanged", [("recorded", "b")], ("recorded", "b")),
        ("added", [], ("recorded", "c")),
    ]
    assert [movement.pairing_basis for movement in movements] == [
        None,
        "same_governed_identity",
        None,
    ]
    assert "on the baseline and not on the candidate" in movements[0].statement
    sides = [side for movement in movements for side in (*movement.before, movement.after) if side]
    assert all(side.route_id == side.relationship_id == side.route_path for side in sides)

    # A changed route is one retracted and one added relationship; a route is never reassigned.
    _movements, rows = _routes(
        family, _route_snapshot("before", ("a",)), _route_snapshot("after", ("b",))
    )
    assert rows == [("retracted", [("recorded", "a")], None), ("added", [], ("recorded", "b"))]

    movements, rows = _routes(
        family, _route_snapshot("before", ()), _route_snapshot("after", ("a",))
    )
    assert rows == [("moved", [("ungoverned", None)], ("recorded", "a"))]
    assert movements[0].gaps == ()

    movements, rows = _routes(family, _route_snapshot("before", ()), _route_snapshot("after", ()))
    assert rows == [("unchanged", [("ungoverned", None)], ("ungoverned", None))]

    movements, rows = _routes(
        family, _route_snapshot("before", ("a",), recorded=False), _route_snapshot("after", ("a",))
    )
    assert rows == [("moved", [("not_recorded", None)], ("recorded", "a"))]
    assert [(gap.side, gap.code) for gap in movements[0].gaps] == [("before", "route_not_recorded")]
    assert "no family identity recorded on the baseline" in movements[0].statement

    movements, rows = _routes(
        family, _route_snapshot("before", None), _route_snapshot("after", None)
    )
    assert rows == [("unresolved", [("unavailable", None)], ("unavailable", None))]
    assert [(gap.side, gap.code) for gap in movements[0].gaps] == [
        ("before", "route_unavailable"),
        ("after", "route_unavailable"),
    ]
    assert "was not read" in movements[0].before[0].detail
    assert "no movement of the governing-route association is stated" in movements[0].statement

    movements, rows = _routes(
        family, _route_snapshot("before", None), _route_snapshot("after", ("a",))
    )
    assert rows == [("unresolved", [("unavailable", None)], ("recorded", "a"))]

    # An invariant file declares no route, so it is ungoverned whatever the file carries; a path
    # selector names no identity and is answered with no association at all.
    invariant = InvariantIdentitySeed(invariant_id=ROUTE_INVARIANT)
    _movements, rows = _routes(
        invariant, _route_snapshot("before", None), _route_snapshot("after", ("a",))
    )
    assert rows == [("unchanged", [("ungoverned", None)], ("ungoverned", None))]
    assert _routes(None, _route_snapshot("before", ("a",)), _route_snapshot("after", ("a",))) == (
        (),
        [],
    )


def test_a_side_that_did_not_resolve_exactly_keeps_its_own_state_and_reason(
    movement_fixture: MovementFixture,
) -> None:
    """An unresolved old/new anchor stays visible with its side and the read's own reason.

    The union holds a claim whose recorded path the fixture's tree does not carry, so its observation
    against the bound tree is not the recorded object. That side keeps its resolution word and the
    read's sentence on the movement's own gap list, and the location the pane lists for it keeps the
    same word -- a side that resolved exactly and a side that did not are two states, and neither is
    rendered as the other or filled in from the other snapshot.
    """

    fixture = movement_fixture
    payload = review(fixture)
    unresolved = [
        movement
        for movement in realization_movements(payload)
        if any(side.path == ABSENT_PATH for side in movement.before)
        or (movement.after is not None and movement.after.path == ABSENT_PATH)
    ]
    assert unresolved, [m.statement for m in realization_movements(payload)]
    movement = unresolved[0]
    gaps = [gap for gap in movement.gaps if gap.code == "anchor_unresolved"]
    assert gaps, [gap.code for gap in movement.gaps]
    assert gaps[0].side in ("before", "after")
    assert "did not resolve exactly" in gaps[0].detail
    assert "path_absent" in gaps[0].detail
    location = next(
        location for location in payload.source.locations if location.path == ABSENT_PATH
    )
    assert location.resolution == "path_absent"
    assert location.observed_source_identity is None
