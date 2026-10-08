"""The complete subject catalogue (ICR-R09): union, presence, totals, bounded loading, traversal.

Every case drives the production composition -- the catalogue read behind the entries route and
the review composition behind the intent route -- over real two-snapshot populations built
through the shipped store operations. Populations that differ from the shared diff fixture are
copies of its datasets extended through ``create_invariant``/``create_revision`` (a retired or
newly added subject) or ``insert_invariant_identity`` (a recorded identity with no authored
content); nothing here hand-writes a payload the composition then claims to have produced.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_review import (
    compose_review,
    list_knowledge_review_entries,
    read_knowledge_review,
)
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_subject_catalogue import read_subject_catalogue
from agents_remember.models.knowledge.read import FamilyIdentitySeed, InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from diff_scope_test_support import DiffFixture, build_diff_fixture
from knowledge_rows_test_support import (
    InvariantRequest,
    RevisionDraft,
    RevisionRequest,
    open_knowledge_store,
)
from read_scope_test_support import APPLICABILITY, CONDITIONS, EXCLUSIONS
from test_review_git_trees import CODE_FILE, LEAF, MASTER, REPO, build_world, commit

pytestmark = pytest.mark.evidence_unit

RETIRED_LABEL = "retired-before-only-obligation"
ADDED_LABEL = "newly-added-after-only-obligation"
RETIRED_STATEMENT = "The retired obligation the before snapshot still records."
ADDED_STATEMENT = "The added obligation only the candidate records."
BARE_LABEL = "recorded-identity-without-content"


@pytest.fixture
def fixture(tmp_path: Path) -> DiffFixture:
    """One fresh two-snapshot fixture per case; no case observes another's candidate state."""

    return build_diff_fixture(tmp_path / "catalogue")


def resolution_for(
    fixture: DiffFixture, *, before_database: Path | None = None, after_database: Path | None = None
) -> ReviewCandidateResolution:
    """The resolution the adapter would produce for this pair, without a contract."""

    return ReviewCandidateResolution(
        repository_id=fixture.repository_id,
        leaf_id="260921-icr-l9",
        baseline_database=before_database or fixture.before.database_path,
        candidate_database=after_database or fixture.after.database_path,
        baseline_code_root=fixture.before.git_root,
        candidate_code_root=fixture.after.git_root,
        baseline_code_tree_id=fixture.before_tree_id,
        candidate_code_tree_id=fixture.after_tree_id,
    )


def _copy_database(database_path: Path, directory: Path, name: str) -> Path:
    """Copy one snapshot file aside so a case can extend it without touching the fixture."""

    target = directory / name
    shutil.copy(database_path, target)
    return target


def _author_extra_invariant(
    database_path: Path, fixture: DiffFixture, *, label: str, statement: str
) -> str:
    """Author one invariant with one revision into a copied snapshot, through the shipped ops."""

    store = open_knowledge_store(database_path, fixture.repository_id)
    try:
        invariant_id = str(uuid4())
        created = store.create_invariant(
            InvariantRequest(
                repository_id=fixture.repository_id,
                invariant_id=invariant_id,
                display_label=label,
                provenance=fixture.before.fixture.authorship,
            )
        )
        assert created.state == "created", created.refusal
        revision = store.create_revision(
            RevisionRequest(
                repository_id=fixture.repository_id,
                revision=RevisionDraft(
                    revision_id=str(uuid4()),
                    invariant_id=invariant_id,
                    display_version="1",
                    statement=statement,
                    applicability=APPLICABILITY,
                    conditions=CONDITIONS,
                    exclusions=EXCLUSIONS,
                    predecessors=(),
                    provenance=fixture.before.fixture.authorship,
                ),
            )
        )
        assert revision.state == "created", revision.refusal
        return invariant_id
    finally:
        store.close()


def _insert_bare_identity(database_path: Path, fixture: DiffFixture) -> str:
    """Record one invariant identity with no revisions: announced but never authored."""

    store = open_knowledge_store(database_path, fixture.repository_id)
    try:
        invariant_id = str(uuid4())
        created = store.create_invariant(
            InvariantRequest(
                repository_id=fixture.repository_id,
                invariant_id=invariant_id,
                display_label=BARE_LABEL,
                provenance=fixture.before.fixture.authorship,
            )
        )
        assert created.state == "created"
        return invariant_id
    finally:
        store.close()


def review_request_for(fixture: DiffFixture, kind: str, selector_id: str) -> ReviewSurfaceRequest:
    """One subject review request naming a catalogue row's own selector."""

    selector = (
        InvariantIdentitySeed(invariant_id=selector_id)
        if kind == "invariant"
        else FamilyIdentitySeed(family_id=selector_id)
    )
    return ReviewSurfaceRequest(
        repository_id=fixture.repository_id,
        master="260921_complete-code-and-intent-review",
        leaf_id="260921-icr-l9",
        selector=selector,
    )


def test_the_catalogue_unions_both_snapshots_with_labels_and_presence(
    fixture: DiffFixture,
) -> None:
    """Every recorded invariant and family is listed once, labelled, with its snapshot presence."""

    offered = read_subject_catalogue(resolution_for(fixture))

    by_id = {entry.selector_id: entry for entry in offered}
    assert len(by_id) == len(offered), "one catalogue row per recorded identity"
    assert fixture.retry_invariant_id in by_id
    assert fixture.sibling_invariant_id in by_id
    assert by_id[fixture.retry_invariant_id].presence == "both"
    assert all(entry.selector_kind in ("invariant", "family") for entry in offered)
    assert all(entry.label for entry in offered)
    kinds = [entry.selector_kind for entry in offered]
    assert kinds == sorted(kinds, key=lambda kind: (kind != "invariant",)), (
        "invariants before families"
    )


def test_the_catalogue_stays_kind_grouped_when_retired_subjects_exist(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """Retired invariants sort with the invariants, not after the families.

    The catalogue promises invariants before families; a union that emitted the after side's
    rows before the before side's would place retired invariants after live families. The
    outer loop is the subject kind, so both sides' invariants precede both sides' families
    however many historical subjects the append-only pair holds.
    """

    before = _copy_database(fixture.before.database_path, tmp_path, "before.sqlite")
    retired_id = _author_extra_invariant(
        before, fixture, label=RETIRED_LABEL, statement=RETIRED_STATEMENT
    )

    offered = read_subject_catalogue(resolution_for(fixture, before_database=before))

    kinds = [entry.selector_kind for entry in offered]
    assert kinds == sorted(kinds, key=lambda kind: (kind != "invariant",)), (
        "invariants before families, retired rows included"
    )
    by_id = {entry.selector_id: entry for entry in offered}
    assert by_id[retired_id].presence == "before_only"
    positions = {entry.selector_id: index for index, entry in enumerate(offered)}
    assert positions[retired_id] > positions[fixture.retry_invariant_id], (
        "the candidate orders its kind; the retired row follows in before order"
    )
    assert positions[retired_id] < min(
        positions[entry.selector_id] for entry in offered if entry.selector_kind == "family"
    ), "no retired invariant sorts after a family"


def test_a_retired_before_only_subject_stays_listed_and_reviewable(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """A subject only the before snapshot records is before_only, labelled, and still opens."""

    before = _copy_database(fixture.before.database_path, tmp_path, "before.sqlite")
    retired_id = _author_extra_invariant(
        before, fixture, label=RETIRED_LABEL, statement=RETIRED_STATEMENT
    )

    offered = read_subject_catalogue(resolution_for(fixture, before_database=before))
    by_id = {entry.selector_id: entry for entry in offered}
    assert by_id[retired_id].presence == "before_only"
    assert by_id[retired_id].label == RETIRED_LABEL

    result = compose_review(
        resolution_for(fixture, before_database=before),
        review_request_for(fixture, "invariant", retired_id),
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    assert result.payload.knowledge.before_statement.state == "present"
    assert result.payload.knowledge.before_statement.text == RETIRED_STATEMENT
    assert result.payload.knowledge.after_statement.state == "absent"


def test_a_newly_added_after_only_subject_is_listed_beside_the_retired_half(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """A subject only the candidate records is after_only and opens one-sided the other way."""

    after = _copy_database(fixture.after.database_path, tmp_path, "after.sqlite")
    added_id = _author_extra_invariant(after, fixture, label=ADDED_LABEL, statement=ADDED_STATEMENT)

    offered = read_subject_catalogue(resolution_for(fixture, after_database=after))
    by_id = {entry.selector_id: entry for entry in offered}
    assert by_id[added_id].presence == "after_only"

    result = compose_review(
        resolution_for(fixture, after_database=after),
        review_request_for(fixture, "invariant", added_id),
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    assert result.payload.knowledge.after_statement.state == "present"
    assert result.payload.knowledge.after_statement.text == ADDED_STATEMENT
    assert result.payload.knowledge.before_statement.state == "absent"


def test_a_family_subject_is_listed_without_an_establishable_statement_side(
    fixture: DiffFixture,
) -> None:
    """Families are catalogue subjects even though no statement side can be established for them.

    The routed R08/R09 debt names this leaf as co-owner: the catalogue must never drop a family
    for lacking a statement, and the review states the side as unresolved with its reason.
    """

    offered = read_subject_catalogue(resolution_for(fixture))
    families = [entry for entry in offered if entry.selector_kind == "family"]
    assert families, "the fixture records families in both snapshots"

    result = compose_review(
        resolution_for(fixture),
        review_request_for(fixture, "family", families[0].selector_id),
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    sides = (
        result.payload.knowledge.before_statement.state,
        result.payload.knowledge.after_statement.state,
    )
    assert "unresolved" in sides, sides


def test_every_catalogue_row_opens_through_the_normal_review(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """Each returned row -- not just the first -- reaches its review, before-only included.

    This falsifies the packet's non-conforming behavior: the API returns multiple entries but
    only the first is reachable. The second, third and nth rows open here exactly like the first.
    """

    before = _copy_database(fixture.before.database_path, tmp_path, "before.sqlite")
    retired_id = _author_extra_invariant(
        before, fixture, label=RETIRED_LABEL, statement=RETIRED_STATEMENT
    )
    resolved = resolution_for(fixture, before_database=before)
    offered = read_subject_catalogue(resolved)
    assert len(offered) >= 3, offered

    for position, entry in enumerate(offered):
        result = compose_review(
            resolved, review_request_for(fixture, entry.selector_kind, entry.selector_id)
        )
        assert result.state == "review", (
            f"catalogue row {position} ({entry.selector_kind} {entry.selector_id}) refused: "
            f"{result.refusal}"
        )
    assert retired_id in {entry.selector_id for entry in offered}


def test_a_recorded_but_unselectable_subject_is_listed_and_its_open_carries_the_reason(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """An unavailable subject carries its reason: listed, never silently dropped.

    The bare identity is recorded in the candidate but has no authored content on either side,
    so its review cannot be composed. The catalogue still lists it -- dropping it would imply a
    smaller complete population -- and opening it answers with the comparison's own typed
    refusal naming the subject, while the other subjects and the source stay accessible.
    """

    after = _copy_database(fixture.after.database_path, tmp_path, "after.sqlite")
    bare_id = _insert_bare_identity(after, fixture)
    resolved = resolution_for(fixture, after_database=after)

    offered = read_subject_catalogue(resolved)
    by_id = {entry.selector_id: entry for entry in offered}
    assert bare_id in by_id, "the unavailable subject is carried, not dropped"

    refused = compose_review(resolved, review_request_for(fixture, "invariant", bare_id))
    assert refused.state == "refused", refused.payload
    assert refused.refusal is not None
    assert refused.refusal.code == "comparison_refused"
    assert bare_id in refused.refusal.detail, refused.refusal.detail
    assert refused.refusal.next_action

    neighbour = compose_review(
        resolved, review_request_for(fixture, "invariant", fixture.retry_invariant_id)
    )
    assert neighbour.state == "review", neighbour.refusal
    context = compose_review(
        resolved,
        ReviewSurfaceRequest(
            repository_id=fixture.repository_id,
            master="260921_complete-code-and-intent-review",
            leaf_id="260921-icr-l9",
            selector=None,
        ),
    )
    assert context.state == "review", context.refusal
    assert context.payload is not None
    assert context.payload.source.inventory.state == "measured"


def test_the_entry_route_carries_labelled_totals_for_the_whole_catalogue(tmp_path: Path) -> None:
    world = build_world(tmp_path / "public")
    result = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert result.state == "entries", result.refusal
    assert result.total_subjects == len(result.entries) > 0
    assert result.invariant_total + result.family_total == result.total_subjects
    assert result.invariant_total == sum(
        entry.selector_kind == "invariant" for entry in result.entries
    )
    assert result.family_total == sum(entry.selector_kind == "family" for entry in result.entries)


def test_zero_subjects_is_a_valid_catalogue_beside_the_source_inventory(tmp_path: Path) -> None:
    world = build_world(tmp_path / "public")
    for root in (world.memory, world.memory_worktree):
        for kind in ("invariants", "families"):
            directory = root / "knowledge" / kind
            for path in directory.glob("*.json"):
                path.unlink()
        for path in (root / "onboarding").rglob("*.json"):
            path.unlink()
        memory_head = commit(root, {}, trailer=world.code_base)
        if root == world.memory:
            world.memory_base = memory_head
    world.contract()
    result = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert result.state == "entries", result.refusal
    assert result.entries == ()
    assert result.total_subjects == result.invariant_total == result.family_total == 0
    (world.code_worktree / CODE_FILE).write_text("def land(value):\n    return value + 1\n")
    context = read_knowledge_review(world.config, world.review())
    assert context.state == "review" and context.payload is not None, context.refusal
    assert context.payload.source.inventory.state == "measured"
    assert context.payload.source.inventory.listed_total > 0


def test_catalogue_loading_runs_no_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the catalogue must not enter the shipped comparison")

    monkeypatch.setattr(
        "agents_remember.application.knowledge_diff.diff_knowledge_scope", forbidden
    )
    monkeypatch.setattr(
        "agents_remember.application.knowledge_review.diff_knowledge_scope", forbidden
    )
    world = build_world(tmp_path / "public")
    result = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert result.state == "entries", result.refusal
    assert result.total_subjects == len(result.entries) > 0
