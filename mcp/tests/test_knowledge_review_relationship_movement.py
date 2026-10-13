"""The reviewer's relationship union and display over synthetic index-shaped unit rows.

The fixture keeps meaningful reader cases that a text tree cannot author, such as multi-revision
lineage. It calls the actual composition with explicit test-only row inputs; public resolution is
covered by the converted-tree suite. The scalar governing-route retirement case uses that real tree
fixture and verifies the family route sets remain available through their existing reader.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge_review import read_complete_knowledge_review
from agents_remember.application.review_governing_route import governing_route_movements
from agents_remember.application.review_recorded_relationships import RecordedSnapshot
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.memory.knowledge_index.build import build_index
from agents_remember.memory.knowledge_index.projection import (
    INDEX_REPOSITORY_ID,
    text_uuid,
)
from agents_remember.memory.knowledge_index.tree import MemoryTreeSnapshot
from agents_remember.models.knowledge.read import FamilyIdentitySeed, InvariantIdentitySeed
from agents_remember.models.knowledge.source import FileLocator
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import record_path
from diff_scope_test_support import (
    SUCCESSOR_PATH,
    _claim_id_for_path,
    _git,
    rebuild_after_tree,
    replace_after_tree,
)
from read_scope_test_support import (
    ABSENT_PATH,
    AUXILIARY_PATH,
    SUBJECT_REVISION,
    SYNCHRONIZATION_PATH,
    ReadFixtureRealization,
    RealizationSeed,
)
from test_knowledge_review_source_endpoints import (
    EndpointFixture,
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

# The identities of the governing-route read's text-built snapshots. The family and
# invariant records carry the text IDs below, so the projection derives exactly
# these lookup identities; the read runs under the index's own namespace because
# a text-built index is keyed by its tree alone.
ROUTE_FAMILY = text_uuid("identity", FAMILY)
ROUTE_INVARIANT = text_uuid("identity", "INV-R00001")


@dataclass(frozen=True)
class MovementFixture:
    """One live enclosure whose two trees record every movement these cases read back."""

    endpoints: EndpointFixture
    merge_claim_id: str

    @property
    def diff(self):
        return self.endpoints.diff


@pytest.fixture(scope="module")
def movement_fixture(tmp_path_factory: pytest.TempPathFactory) -> MovementFixture:
    """Build the enclosure and record every movement through the text-backed builder."""

    return build_movement_fixture(tmp_path_factory.mktemp("movement"))


def build_movement_fixture(directory: Path) -> MovementFixture:
    """Populate two text trees and the real source trees their anchors name.

    The candidate tree is the endpoint's candidate topology with the withdrawn auxiliary claim
    left out and the merge and successor claims added, both citing the revised revision. It is
    test-only input to the actual composition, and exercises no retired production writer.
    """

    endpoints = build_endpoint_fixture(directory / "endpoints")
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
    after = rebuild_after_tree(
        directory / "movement-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, AUXILIARY_PATH),
        extra_claims=(
            _extra_claim(
                claim_id=str(uuid4()),
                anchor_id=str(uuid4()),
                path=SUCCESSOR_PATH,
                blob=successor_blob,
            ),
            _extra_claim(
                claim_id=str(uuid4()),
                anchor_id=str(uuid4()),
                path=MERGE_PATH,
                blob=merge_blob,
            ),
        ),
    )
    endpoints = replace_after_tree(endpoints, after)
    return MovementFixture(
        endpoints=endpoints,
        merge_claim_id=_claim_id_for_path(after.database_path, MERGE_PATH),
    )


def _extra_claim(*, claim_id: str, anchor_id: str, path: str, blob: str) -> RealizationSeed:
    """One candidate-only realization citing the tree's own revised revision, at a real blob."""

    return RealizationSeed(
        realization=ReadFixtureRealization(anchor_id=anchor_id, claim_id=claim_id, path=path),
        revision_id=SUBJECT_REVISION,
        role="enforcement",
        locator=FileLocator(),
        recorded_blob=blob,
    )


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


# -- a removed claim and a new claim are a retraction and an addition --------------------------


def test_a_removed_claim_and_a_new_claim_display_as_a_retraction_and_an_addition(
    movement_fixture: MovementFixture,
) -> None:
    """A move is a removed claim and a new claim: two movements, one preserved invariant identity.

    The store refuses to re-anchor a claim in place, so what an author's "the realization moved
    from A to B" *is* is a removed claim at ``src/synchronization.py`` and a new claim at
    ``src/retry_interval.py`` under the same reviewed invariant. Text records hold no predecessor
    edge (MIK-R74 rule 1), so nothing pairs the two rows: the baseline side displays as a
    retraction that names the candidate rows citing the same revision, and the candidate side
    displays as an addition with no baseline side substituted. No movement carries both
    addresses, and the pane lists each address once under the same identity.
    """

    fixture = movement_fixture
    payload = review(fixture)
    added = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path == SUCCESSOR_PATH
    ]
    assert len(added) == 1
    movement = added[0]

    assert movement.transition == "added"
    assert movement.record_kind == "invariant"
    assert movement.record_id == fixture.diff.retry_invariant_id
    assert movement.before == ()
    assert movement.pairing_basis is None
    assert movement.after is not None
    assert movement.after.path == SUCCESSOR_PATH
    assert movement.after.resolution == "exact_recorded_blob"
    assert "no baseline side is substituted" in movement.statement

    retracted = [
        move
        for move in realization_movements(payload)
        if move.transition == "retracted"
        and any(side.path == SYNCHRONIZATION_PATH for side in move.before)
    ]
    assert len(retracted) == 1
    assert retracted[0].after is None
    assert retracted[0].record_id == fixture.diff.retry_invariant_id
    assert "withdrawn association rather than a movement" in retracted[0].statement
    assert not any(
        move.after is not None
        and move.after.path == SUCCESSOR_PATH
        and any(side.path == SYNCHRONIZATION_PATH for side in move.before)
        for move in realization_movements(payload)
    ), "a removed claim and a new claim are two movements, never one movement"

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
    assert located[SUCCESSOR_PATH].recorded_side == "after"
    # No movement pairs the two addresses, so neither location names the other as its
    # counterpart: each address stands on its own recorded side.
    assert located[SYNCHRONIZATION_PATH].counterpart_path is None
    assert located[SUCCESSOR_PATH].counterpart_path is None
    assert located[SYNCHRONIZATION_PATH].before_only is True
    assert located[SYNCHRONIZATION_PATH].transition == "retracted"
    assert located[SUCCESSOR_PATH].transition == "added"
    # One row per recorded side: the pane's address view is a list of addresses.
    assert [location.path for location in payload.source.locations].count(SYNCHRONIZATION_PATH) == 1


def test_only_the_after_graph_is_read_so_the_old_association_vanishes(
    movement_fixture: MovementFixture,
) -> None:
    """The packet's non-conforming reading, measured: the after side cannot produce the old address.

    The population the after side alone names is a real set -- every candidate-side claim address --
    and the baseline address the author moved away from is not in it. The union traversal the
    payload is built from does hold it, as a retraction under the invariant identity that names
    the candidate rows citing the same revision, so the two readings are distinguished by what
    they can display rather than by a claim about them.
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

    union = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == SYNCHRONIZATION_PATH for side in movement.before)
    ]
    assert len(union) == 1
    assert union[0].record_id == fixture.diff.retry_invariant_id
    assert "withdrawn association rather than a movement" in union[0].statement
    assert "no relationship citing" in union[0].statement


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
    assert "no relationship citing" in movement.statement
    assert "withdrawn association rather than a movement" in movement.statement
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


# Dead: no text-tree comparison leaves a recorded row of the reviewed invariant outside the
# declared selection, so no payload carries an ``outside_selection`` movement. The transition
# stays in the production vocabulary for selections the text format cannot express; the case
# below names its removal rather than asserting it.
_REMOVED_OUTSIDE_SELECTION_CASE = (
    "test_a_record_the_other_selection_did_not_reach_is_not_displayed_as_a_deletion"
)


# -- the source rename is a labelled inference, and it proves nothing -----------------------------


def test_a_source_rename_is_never_a_recorded_movement_and_carries_no_inference(
    movement_fixture: MovementFixture,
) -> None:
    """A rename is never a recorded movement: no movement names two addresses, so none earns one.

    The two bound trees hold a deleted path and a near-copy of it at the candidate's path, so
    Git's own similarity detection *would* pair them -- but the inference attaches only to a
    movement whose recorded sides name two different addresses, and no movement here does: the
    baseline side displays as a retraction, the candidate side as an addition, and every
    movement's inference is absent. The recorded sides are still both displayed with the
    identity the snapshots record; nothing in either movement is derived from a similarity
    the display never asked for.
    """

    fixture = movement_fixture
    payload = review(fixture)
    assert all(movement.rename_inference is None for movement in realization_movements(payload)), (
        "no movement names two recorded addresses, so Git's inference has nothing to attach to"
    )

    added = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path == SUCCESSOR_PATH
    ]
    assert len(added) == 1
    assert added[0].transition == "added"
    assert added[0].before == ()
    retracted = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == SYNCHRONIZATION_PATH for side in movement.before)
    ]
    assert len(retracted) == 1
    assert retracted[0].after is None
    assert retracted[0].record_id == fixture.diff.retry_invariant_id


def build_no_authored_edge_fixture(directory: Path) -> EndpointFixture:
    """The same source rename as the conforming case, with the successor claim re-recorded.

    The candidate's claim at the successor path cites the revised revision -- a row nothing pairs
    with the withdrawn baseline claim, because pairing needs the same recorded row. The two code
    trees still show the deleted path and its near-copy at the candidate's path, so nothing may
    display the two recorded associations as one movement -- whatever Git's own similarity
    detection says about the files.
    """

    endpoints = build_endpoint_fixture(directory / "no-edge")
    worktree = endpoints.worktree
    (worktree / SUCCESSOR_PATH).write_text(RENAMED_PATH_TEXT, encoding="utf-8")
    blob = _git(worktree, ["hash-object", SUCCESSOR_PATH])
    after = rebuild_after_tree(
        directory / "no-edge-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, SUCCESSOR_PATH),
        extra_claims=(
            _extra_claim(
                claim_id=str(uuid4()),
                anchor_id=str(uuid4()),
                path=SUCCESSOR_PATH,
                blob=blob,
            ),
        ),
    )
    return replace_after_tree(endpoints, after)


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


def test_an_authored_merge_revision_displays_as_an_addition_citing_its_own_revision(
    movement_fixture: MovementFixture,
) -> None:
    """The merge claim's realization displays as an addition under its own identity.

    The case records a second candidate-only realization at its own path, citing the revised
    revision. Text records hold no predecessor edge (MIK-R74 rule 1), so the display states no
    succession, split or merge: the merge path's realization is the candidate's added side citing
    the revised revision, and the revised statement's realization is a second addition citing the
    same revision. Both sit under the reviewed invariant; neither is paired with the retracted
    baseline side.
    """

    fixture = movement_fixture
    payload = review(fixture)
    merged = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path == MERGE_PATH
    ]
    assert len(merged) == 1
    assert merged[0].transition == "added"
    assert merged[0].record_id == fixture.diff.retry_invariant_id
    assert merged[0].before == ()
    assert merged[0].after is not None
    # The cited revision is the revised revision itself, recorded in the statement.
    assert fixture.diff.revised_revision_id in merged[0].statement
    assert "no baseline side is substituted" in merged[0].statement

    revised = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path == SUCCESSOR_PATH
    ]
    assert len(revised) == 1
    assert revised[0].transition == "added"
    assert fixture.diff.revised_revision_id in revised[0].statement


# -- the family association and the governing route ----------------------------------------------


def test_a_family_association_reassigned_to_a_new_revision_displays_both_recorded_sides(
    movement_fixture: MovementFixture,
) -> None:
    """The membership's cited revision changed; the family identity and the row did not.

    The candidate's direct-family membership cites the revised revision where the baseline cited
    the subject revision. The membership row is the same recorded row on both sides -- one family,
    one member invariant -- so the two sides pair by that row and display as one movement. Its
    transition is ``moved`` rather than a withdrawal plus an addition, because the member revision
    the candidate records is not the one the baseline recorded. The family identity itself is
    preserved and named.
    """

    fixture = movement_fixture
    payload = review(fixture)
    memberships_shown = [
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind in ("membership", "advertised_family")
    ]
    direct = [
        movement
        for movement in memberships_shown
        if movement.after is not None
        and movement.after.revision_id == fixture.diff.after.fixture.direct_family.revision_id
        and any(
            side.member_revision_id == fixture.diff.before.fixture.subject_revision_id
            for side in movement.before
        )
    ]
    assert len(direct) == 1, [
        (movement.relationship_kind, movement.transition) for movement in memberships_shown
    ]
    movement = direct[0]

    assert movement.transition == "moved"
    assert movement.record_kind == "family"
    assert movement.record_id == fixture.diff.before.fixture.direct_family.family_id
    assert len(movement.before) == 1
    assert movement.pairing_basis == "same_recorded_relationship"
    assert movement.after is not None
    assert movement.after.member_revision_id == fixture.diff.revised_revision_id
    assert movement.before[0].member_revision_id == fixture.diff.before.fixture.subject_revision_id
    assert "recorded relation and not a resemblance" in movement.statement


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


# Unique snapshot files: one test builds several before/after pairs in one directory.
_SNAPSHOT_SEQUENCE = itertools.count()


def _route_snapshot(
    tmp_path: Path, side: str, routes: tuple[str, ...] | None, *, recorded: bool = True
):
    """One snapshot holding the tables the governing-route read asks, built from text.

    The family and invariant records are authored as text files and indexed with
    :func:`build_index`, so no test stages index rows by hand. ``routes`` is the
    family's declared set, written into the family record the projection reads.
    ``None`` drops the route tables from the built copy afterwards: that fault
    injection (DDL on a tmp copy, never an INSERT) stages a file whose route
    declarations cannot be read. ``recorded=False`` deletes the identity rows
    from the built copy for the same reason: the routes stay declared while the
    identities do not exist.
    """

    family_record = {
        "schema": "ar-family/v1",
        "id": FAMILY,
        "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L74"},
        "revision": 1,
        "status": "proposed",
        "title": FAMILY,
        "guarantee": "Every member the family admits.",
        "members": [],
        "routes": [] if routes is None else list(routes),
        "admission": "legacy-unassessed",
    }
    invariant_record = {
        "schema": "ar-invariant/v1",
        "id": "INV-R00001",
        "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L74"},
        "revision": 1,
        "status": "proposed",
        "statement": "INV-R00001",
        "applicability": "Every admitted candidate write.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }
    files = {
        record_path("family", FAMILY, FAMILY.lower()): canonical_json_bytes(family_record),
        record_path("invariant", "INV-R00001", "inv-r00001"): canonical_json_bytes(
            invariant_record
        ),
    }
    database = tmp_path / f"route-{side}-{next(_SNAPSHOT_SEQUENCE)}.db"
    report = build_index(
        MemoryTreeSnapshot(key="0" * 40, source="directory", location="fixture", files=files),
        database,
    )
    assert report.state == "complete", report.problems
    connection = apsw.Connection(str(database))
    try:
        if routes is None:
            connection.execute("DROP TABLE ix_route")
            connection.execute("DROP TABLE ix_uuid")
        elif not recorded:
            # The invariant row is immutable by trigger; the read keys recorded
            # presence off the family identity, so withdrawing the family row
            # stages "no family identity recorded" while the routes stay declared.
            connection.execute("DELETE FROM family")
    except BaseException:
        connection.close()
        raise
    return RecordedSnapshot(side=side, database=database, connection=connection)


def _routes(selector, before, after):
    """The governing-route movements as ``(transition, before states and routes, after)`` rows."""

    def shown(side):
        return None if side is None else (side.state, side.route_path)

    movements = governing_route_movements(selector, INDEX_REPOSITORY_ID, before, after)
    return movements, [
        (movement.transition, [shown(side) for side in movement.before], shown(movement.after))
        for movement in movements
    ]


def test_each_declared_route_is_one_relationship_and_an_unread_side_is_never_ungoverned(
    tmp_path: Path,
) -> None:
    """The route read's own matrix: one relationship per route, and four states kept apart.

    A route both snapshots declare is unchanged; a route one snapshot declares while the other is
    governed by other routes is one-sided; an identity with no route to show contributes its state.
    A file without the route tables was not read: it is ``unavailable`` with its own gap and the
    comparison is ``unresolved``, never ``ungoverned`` and never ``unchanged``.
    """

    family = FamilyIdentitySeed(family_id=ROUTE_FAMILY)

    movements, rows = _routes(
        family,
        _route_snapshot(tmp_path, "before", ("a", "b")),
        _route_snapshot(tmp_path, "after", ("b", "c")),
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
        family,
        _route_snapshot(tmp_path, "before", ("a",)),
        _route_snapshot(tmp_path, "after", ("b",)),
    )
    assert rows == [("retracted", [("recorded", "a")], None), ("added", [], ("recorded", "b"))]

    movements, rows = _routes(
        family, _route_snapshot(tmp_path, "before", ()), _route_snapshot(tmp_path, "after", ("a",))
    )
    assert rows == [("moved", [("ungoverned", None)], ("recorded", "a"))]
    assert movements[0].gaps == ()

    movements, rows = _routes(
        family, _route_snapshot(tmp_path, "before", ()), _route_snapshot(tmp_path, "after", ())
    )
    assert rows == [("unchanged", [("ungoverned", None)], ("ungoverned", None))]

    movements, rows = _routes(
        family,
        _route_snapshot(tmp_path, "before", ("a",), recorded=False),
        _route_snapshot(tmp_path, "after", ("a",)),
    )
    assert rows == [("moved", [("not_recorded", None)], ("recorded", "a"))]
    assert [(gap.side, gap.code) for gap in movements[0].gaps] == [("before", "route_not_recorded")]
    assert "no family identity recorded on the baseline" in movements[0].statement

    movements, rows = _routes(
        family, _route_snapshot(tmp_path, "before", None), _route_snapshot(tmp_path, "after", None)
    )
    assert rows == [("unresolved", [("unavailable", None)], ("unavailable", None))]
    assert [(gap.side, gap.code) for gap in movements[0].gaps] == [
        ("before", "route_unavailable"),
        ("after", "route_unavailable"),
    ]
    assert "was not read" in movements[0].before[0].detail
    assert "no movement of the governing-route association is stated" in movements[0].statement

    movements, rows = _routes(
        family,
        _route_snapshot(tmp_path, "before", None),
        _route_snapshot(tmp_path, "after", ("a",)),
    )
    assert rows == [("unresolved", [("unavailable", None)], ("recorded", "a"))]

    # An invariant file declares no route, so it is ungoverned whatever the file carries; a path
    # selector names no identity and is answered with no association at all.
    invariant = InvariantIdentitySeed(invariant_id=ROUTE_INVARIANT)
    _movements, rows = _routes(
        invariant,
        _route_snapshot(tmp_path, "before", None),
        _route_snapshot(tmp_path, "after", ("a",)),
    )
    assert rows == [("unchanged", [("ungoverned", None)], ("ungoverned", None))]
    assert _routes(
        None,
        _route_snapshot(tmp_path, "before", ("a",)),
        _route_snapshot(tmp_path, "after", ("a",)),
    ) == (
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
