"""The declared-policy traversal, the Family projection, and the R07 boundary.

Every case here protects one clause of ``KS-R17@v1`` §3, §7 and §8. The load-bearing ones are the
boundary cases rather than the traversal:

* **The retrieval read is untouched.** The composition table is not consulted for selection, and the
  cases measure that twice -- by value (the same dataset and seed produce the same selected items,
  counts, advertised frontier and manifest digest with composition edges present and absent) and by
  the call-site derivation the packet's own Open Truth Gap asks for.
* **The traversal is a different operation.** Following declared edges under a policy version
  constructs a scope and reports the version it executed under; it never changes what
  ``read_knowledge_scope`` selects, and it refuses rather than truncating.
* **The projection reports stored facts.** A missing link, route or context is reported absent, and
  nothing is reconstructed from a label, a path, a member or the joint guarantee.
* **The escalation is recorded and not activated.** The R07 proposal exists as a named artifact with
  its subject and effect scope, so the edge cannot be forgotten and cannot be read as a retirement.

The preservation comparisons are by value and by digest, never by inspection: the shipped read's own
output model and the sealed family-revision ``payload_digest`` are the things compared.
"""

from __future__ import annotations

import shutil
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from agents_remember.application.knowledge import open_admitted_knowledge_store
from agents_remember.application.knowledge_composition import (
    family_view,
    follow_family_composition,
)
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.memory.knowledge import composition_traversal, compositions
from agents_remember.memory.knowledge.family_view import FamilyRevisionView, family_revision_view
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.records import decode_authorship
from agents_remember.memory.knowledge.refusals import KnowledgeRefused, SqliteFailureContext
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.candidate import (
    AuthorFamilyExplanationContext,
    SetFamilyRevisionRoute,
)
from agents_remember.models.knowledge.composition import (
    REGISTERED_REVIEW_SCOPE,
    FamilyComposition,
    FamilyCompositionPolicyDraft,
    FamilyExplanationContext,
    FamilyExplanationContextDraft,
)
from agents_remember.models.knowledge.digest import FAMILY_REVISION_PAYLOAD_VERSION
from agents_remember.models.knowledge.read import (
    FamilyRevisionSeed,
    KnowledgeReadBudget,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    PathSeed,
)
from facet_test_support import (
    build_admitted_candidate,
    seed_subject,
)
from pydantic import ValidationError
from read_scope_test_support import ReadScopeFixture, build_read_scope_fixture
from test_knowledge_family_composition import (
    POLICY_ID,
    _add_family,
    _author_edge,
    _declare_policy,
)

pytestmark = pytest.mark.evidence_unit

PROPOSAL_PATH = Path(
    "/home/firefox/projects/ar-coordination/tasks/agents-remember/260915_knowledge-substrate"
    "/notes/reports/260915-KS-L17-r07-escalation-proposal.md"
)

# The three source files the shipped retrieval selection is made of, plus the projection that is
# deliberately a *different* surface. A later leaf that introduces another read path adds it here.
READ_PATH_SOURCES = (
    Path("mcp/src/agents_remember/memory/knowledge/read.py"),
    Path("mcp/src/agents_remember/memory/knowledge/read_queries.py"),
    Path("mcp/src/agents_remember/application/knowledge_read.py"),
)

REPOSITORY_ROOT = Path(__file__).parents[2]


@pytest.fixture
def admitted(tmp_path: Path) -> tuple[Any, Any]:
    """One admitted candidate namespace, ready for a store to be opened on it."""

    return build_admitted_candidate(tmp_path / "traversal")


def _read_context(database_path: Path, fixture: ReadScopeFixture) -> Any:
    """The read context of one dataset and the fixture's real committed Git tree."""

    return open_read_context(
        database_path,
        fixture.repository_id,
        repository_root=fixture.git_root,
        code_tree_id=fixture.git_tree_id,
    )


def _read(database_path: Path, fixture: ReadScopeFixture, seed: Any) -> KnowledgeReadResult:
    """Run one read through the application seam at one dataset's own snapshot."""

    return read_knowledge_scope(
        database_path,
        _read_context(database_path, fixture),
        KnowledgeReadRequest(seed=seed, budget=KnowledgeReadBudget()),
    )


def _selection_fingerprint(result: KnowledgeReadResult) -> dict[str, Any]:
    """Everything ``KS-R07@v1`` preserves, in one comparable value.

    The selected set, the counts, the revision groups, the directly containing families, the
    advertised frontier and the manifest digest -- the outputs the packet's Preservation Boundaries
    list -- rendered from the read's own output model rather than from a re-derivation of it.
    """

    assert result.state == "page", result.refusal
    assert result.page is not None
    return {
        "manifest_digest": result.manifest_digest,
        "policy_version": result.policy_version,
        "seed_digest": result.seed_digest,
        "items": [item.model_dump(mode="json") for item in result.page.items],
        "counts": result.page.counts.model_dump(mode="json"),
        "revision_groups": [group.model_dump(mode="json") for group in result.revision_groups],
        "directly_containing_families": [
            family.model_dump(mode="json") for family in result.directly_containing_families
        ],
        "frontier": [
            item.model_dump(mode="json")
            for item in result.page.items
            if item.kind == "advertised_family"
        ],
        "total_items": result.page.counts.primary_items_total,
    }


def _seeds(fixture: ReadScopeFixture) -> tuple[Any, ...]:
    """One seed per selector kind the fixture supports, so the comparison covers the policy.

    The two seeds are the ones the fixture's own cases use: a path the fixture records realizations
    at, and an exact family revision. A seed that selects nothing is not a comparison, so both are
    asserted to select something before the fingerprints are compared.
    """

    return (
        PathSeed(path=fixture.integration.path),
        FamilyRevisionSeed(
            family_id=fixture.family.family_id, revision_id=fixture.family.revision_id
        ),
    )


def _counts_and_digest(database_path: Path, repository_id: str) -> tuple[dict[str, int], str]:
    """The row counts and logical digest of one dataset, read from the real file."""

    store = open_knowledge_store(database_path, repository_id)
    try:
        counts = {
            table: int(next(iter(store.connection.execute(f"SELECT count(*) FROM {table}")))[0])
            for table in store.generation.tables
        }
    finally:
        store.close()
    return (counts, dataset_identity(database_path).logical_digest)


def _copy_with_composition_edges(fixture: ReadScopeFixture, target: Path) -> Path:
    """Copy the fixture dataset and add composition edges to the copy.

    The edges relate the fixture's own family revisions and are inserted with a provenance envelope
    the dataset already stores, so the copy holds the same records plus a composition graph. That is
    what makes the read comparison a comparison of one variable: the composition table's contents.
    """

    shutil.copyfile(fixture.database_path, target)
    store = open_knowledge_store(target, fixture.repository_id)
    try:
        provenance = next(
            iter(store.connection.execute("SELECT label_provenance FROM family LIMIT 1"))
        )[0]
        for from_revision_id, to_revision_id in (
            (fixture.family.revision_id, fixture.overlapping_family.revision_id),
            (fixture.overlapping_family.revision_id, fixture.direct_family.revision_id),
        ):
            store.connection.execute(
                "INSERT INTO family_composition "
                "(repository_id, composition_id, from_family_revision_id, to_family_revision_id, "
                "policy_id, policy_version_id, provenance) VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                (
                    store.repository_id,
                    str(uuid4()),
                    from_revision_id,
                    to_revision_id,
                    provenance,
                ),
            )
    finally:
        store.close()
    return target


# ---------------------------------------------------------------------------
# Requirements 7.1 and 7.2: the retrieval read is untouched, by value and by derivation.


def test_a_composition_graph_leaves_the_shipped_selection_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    """Requirement 7.1: the same dataset and seed select the same set, counts, frontier and digest.

    The comparison is by value: the dataset is copied, composition edges are added to the copy, and
    every preserved output of the shipped read is compared between the two. An edge that added or
    removed one item, or that widened the advertised frontier, fails here -- which is exactly the
    failure the packet's Forbidden Overreach names.
    """

    fixture = build_read_scope_fixture(tmp_path / "read-scope")
    copied = _copy_with_composition_edges(fixture, tmp_path / "with-composition.db")
    for seed in _seeds(fixture):
        before = _selection_fingerprint(_read(fixture.database_path, fixture, seed))
        assert before["items"], "the fixture must select something for the comparison to mean"
        after = _selection_fingerprint(_read(copied, fixture, seed))
        assert after == before

    # The copy really did gain the edges, so the equality above is not the equality of two
    # unchanged files: every other table's count is identical and the composition table's is not.
    baseline_counts, baseline_digest = _counts_and_digest(
        fixture.database_path, fixture.repository_id
    )
    copied_counts, copied_digest = _counts_and_digest(copied, fixture.repository_id)
    assert baseline_counts["family_composition"] == 0
    assert copied_counts["family_composition"] == 2
    assert copied_digest != baseline_digest
    assert {
        table: count for table, count in copied_counts.items() if table != "family_composition"
    } == {table: count for table, count in baseline_counts.items() if table != "family_composition"}


def test_no_shipped_read_path_consults_the_composition_table() -> None:
    """Requirement 7.2's derivation: the call sites that must not consult composition, walked.

    The packet records this as an Open Truth Gap -- "the exact set of call sites that must not
    consult the composition table for selection" -- and leaves the list to the implementer. The
    derivation *is* the walk, and it has two halves because the two files play different roles:

    * ``read.py`` (the selection policy) and ``application/knowledge_read.py`` (its entry point) must
      not mention composition at all, and the case asserts that;
    * ``read_queries.py`` legitimately declares the new tables' read orders and carries the
      projection's own lookups beside the selection's, because it is the package's one statement
      module. What must hold there is narrower and is asserted exactly: every composition reference
      is in the declared-order mapping or in a function whose name says it is a projection lookup,
      and no selection statement mentions the table.
    """

    for relative in READ_PATH_SOURCES[:1] + READ_PATH_SOURCES[2:]:
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        assert "composition" not in text.lower(), relative

    queries = (REPOSITORY_ROOT / READ_PATH_SOURCES[1]).read_text(encoding="utf-8")
    selection_part, _, projection_part = queries.partition("def fetch_composition_rows(")
    assert "family_composition" not in selection_part.split("_ORDER_COLUMNS")[1].split("}")[1]
    assert "family_composition" in projection_part
    assert "def fetch_owning_routes_for_revisions(" in projection_part
    assert "def fetch_contexts_for_revisions(" in projection_part
    # The projection is a separately named surface, and it never traverses: the only module that
    # follows an edge is the traversal module, and the projection does not import it.
    projection = (
        REPOSITORY_ROOT / "mcp/src/agents_remember/memory/knowledge/family_view.py"
    ).read_text(encoding="utf-8")
    assert "fetch_composition_rows" in projection
    assert "follow_composition_scope" not in projection
    assert "def family_revision_view" in projection


def test_a_traversal_under_a_declared_policy_reports_its_version_and_widens_nothing_else(
    admitted: Any,
) -> None:
    """Requirements 3.3, 7.3 and 7.4: the successor is a different operation, and it says so.

    A scope constructed under one policy version reports that version, the direction it stepped in
    and the bound it was given. Nothing about the retrieval read moves: the case also runs the
    shipped read over a dataset that has the edges, which is the boundary proof the packet's
    Expected Evidence asks for.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        first, second, third = (_add_family(destination, name) for name in ("a", "b", "c"))
        version_id = _declare_policy(destination, depth_bound=3)
        for left, right in ((first, second), (second, third)):
            outcome = _author_edge(
                destination, left, right, policy_id=POLICY_ID, policy_version_id=version_id
            )
            assert outcome.state == "changed", outcome.refusal
        scope = composition_traversal.follow_composition_scope(store, first, POLICY_ID, version_id)
        assert scope.seed_family_revision_id == first
        assert scope.policy_identity == (POLICY_ID, version_id, "2026-09-18.1")
        assert scope.direction == "forward"
        assert scope.depth_bound == 3
        assert scope.depth_reached == 2
        assert scope.widened_scope == REGISTERED_REVIEW_SCOPE
        assert scope.reached_family_revision_ids == tuple(sorted((first, second, third)))
        assert len(scope.followed_composition_ids) == 2

        # The declared direction is respected: a traversal from the far end steps nowhere, because
        # following an edge backwards is not what "forward" permits.
        end = composition_traversal.follow_composition_scope(store, third, POLICY_ID, version_id)
        assert end.reached_family_revision_ids == (third,)
        assert end.followed_composition_ids == ()

        # The projection reports the same links without traversing, and the traversal wrote nothing.
        before = _counts_and_digest(destination.database_path, store.repository_id)
        view = family_revision_view(store, first)
        assert isinstance(view, FamilyRevisionView)
        assert [link.to_family_revision_id for link in view.composition_links] == [second]
        assert view.composition_links[0].policy_version_id == version_id
        assert view.composition_links[0].declared_version == "2026-09-18.1"
        assert _counts_and_digest(destination.database_path, store.repository_id) == before


def test_an_unknown_a_malformed_and_a_not_permitted_policy_are_each_refused_by_name(
    admitted: Any,
) -> None:
    """Requirements 3.2 and 3.5: unknown, malformed, and not-permitted all refuse with facts.

    Three distinct refusals, each naming the policy identity, its version and the edge or bound it
    reached. A malformed policy never reaches storage: no finite bound and an unregistered widened
    scope are refused by the value's own construction, so the malformed state is not representable
    to begin with.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        first, second = _add_family(destination, "a"), _add_family(destination, "b")
        version_id = _declare_policy(destination)
        declared = _author_edge(
            destination, first, second, policy_id=POLICY_ID, policy_version_id=version_id
        )
        assert declared.state == "changed", declared.refusal

        # An unknown identity and an unknown version of a known identity are two different facts
        # with two different remedies, so both are exercised.
        with pytest.raises(KnowledgeRefused) as unknown:
            composition_traversal.follow_composition_scope(
                store, first, "policy/nobody-declared", version_id
            )
        assert unknown.value.refusal.code == "invalid_payload"
        assert unknown.value.refusal.record_id == version_id
        assert unknown.value.refusal.observed is not None

        with pytest.raises(KnowledgeRefused) as unknown_version:
            composition_traversal.follow_composition_scope(store, first, POLICY_ID, str(uuid4()))
        assert unknown_version.value.refusal.code == "invalid_payload"
        assert unknown_version.value.refusal.expected == "a declared policy version"

        # An edge whose own declared policy is *another version* is not followable under this one,
        # and the traversal refuses rather than stepping around it: the offender is named, and the
        # version the traversal asked for and the one the edge declares are both reported.
        other_version = _declare_policy(destination, version="2026-09-18.other")
        divergent_source, divergent_target = (
            _add_family(destination, "c"),
            _add_family(destination, "d"),
        )
        divergent = _author_edge(
            destination,
            divergent_source,
            divergent_target,
            policy_id=POLICY_ID,
            policy_version_id=other_version,
        )
        assert divergent.state == "changed", divergent.refusal
        with pytest.raises(KnowledgeRefused) as foreign_edge:
            composition_traversal.follow_composition_scope(
                store, divergent_source, POLICY_ID, version_id
            )
        assert foreign_edge.value.refusal.code == "relationship_constraint"
        assert foreign_edge.value.refusal.record_id == divergent.changed[0].record_id
        assert foreign_edge.value.refusal.expected == f"{POLICY_ID}/{version_id}"
        assert foreign_edge.value.refusal.observed == f"{POLICY_ID}/{other_version}"

        # An edge with no declared policy at all is refused under any policy, and the absence is
        # reported as the absence rather than as a policy identity nobody declared.
        bare_version = _declare_policy(destination, version="2026-09-18.bare")
        bare_source, bare_target = _add_family(destination, "e"), _add_family(destination, "f")
        bare_edge = _author_edge(destination, bare_source, bare_target)
        assert bare_edge.state == "changed", bare_edge.refusal
        with pytest.raises(KnowledgeRefused) as undeclared:
            composition_traversal.follow_composition_scope(
                store, bare_source, POLICY_ID, bare_version
            )
        assert undeclared.value.refusal.code == "relationship_constraint"
        assert undeclared.value.refusal.observed == "no declared policy"
        assert undeclared.value.refusal.record_id == bare_edge.changed[0].record_id

        # A malformed policy never reaches storage at all: the value boundary refuses a bound that
        # is not finite and a scope this build does not register, so the malformed state is not
        # representable to begin with.
        for overrides in ({"depth_bound": 0}, {"widened_scope": "some_other_scope"}):
            with pytest.raises(ValidationError):
                FamilyCompositionPolicyDraft(
                    **{
                        "policy_id": POLICY_ID,
                        "policy_version_id": str(uuid4()),
                        "declared_version": "2026-09-18.9",
                        "direction": "forward",
                        "depth_bound": 2,
                        "widened_scope": REGISTERED_REVIEW_SCOPE,
                        "provenance": destination.authorship,
                        **overrides,
                    }
                )


def test_a_traversal_that_would_exceed_its_declared_bound_is_refused_not_truncated(
    admitted: Any,
) -> None:
    """Requirement 3.5: a truncated traversal is never reported as a complete scope.

    A chain longer than the declared bound is refused with the policy identity, its version and the
    bound reached. The same edges under a bound that admits them return the whole scope, and the
    scope names *which* policy version it ran under -- so the refusal is about the bound and not
    about the graph, and the two versions are never readable as one.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        # Two independent chains, because the two halves must not interfere: an edge declared under
        # one policy version is *not followable* under another, so a single chain carrying both
        # versions would exercise the not-permitted refusal instead of the bound.
        short_chain = [_add_family(destination, f"short-{index}") for index in range(4)]
        complete_chain = [_add_family(destination, f"complete-{index}") for index in range(4)]
        short = _declare_policy(destination, depth_bound=1, version="2026-09-18.short")
        long_enough = _declare_policy(destination, depth_bound=3, version="2026-09-18.long")
        for left, right in pairwise(short_chain):
            outcome = _author_edge(
                destination, left, right, policy_id=POLICY_ID, policy_version_id=short
            )
            assert outcome.state == "changed", outcome.refusal

        with pytest.raises(KnowledgeRefused) as exceeded:
            composition_traversal.follow_composition_scope(store, short_chain[0], POLICY_ID, short)
        assert exceeded.value.refusal.code == "relationship_constraint"
        assert exceeded.value.refusal.expected == "at most 1 steps"
        assert exceeded.value.refusal.record_id is not None
        assert exceeded.value.refusal.observed is not None

        # The same *shape* under a bound that admits it produces the complete scope, and the scope
        # names which policy version it ran under -- so the refusal above is about the bound and not
        # about the graph, and the two versions are never readable as one.
        for left, right in pairwise(complete_chain):
            outcome = _author_edge(
                destination, left, right, policy_id=POLICY_ID, policy_version_id=long_enough
            )
            assert outcome.state == "changed", outcome.refusal
        complete = composition_traversal.follow_composition_scope(
            store, complete_chain[0], POLICY_ID, long_enough
        )
        assert complete.policy_identity[1] == long_enough
        assert complete.policy_identity[2] == "2026-09-18.long"
        assert complete.depth_bound == 3
        assert complete.depth_reached == 3
        assert complete.reached_family_revision_ids == tuple(sorted(complete_chain))
        assert len(complete.followed_composition_ids) == 3


def _route_command(family_revision_id: str, route_id: str) -> Any:
    return SetFamilyRevisionRoute(
        kind="set_family_revision_route", family_revision_id=family_revision_id, route_id=route_id
    )


def _context_command(context: FamilyExplanationContextDraft) -> Any:
    return AuthorFamilyExplanationContext(kind="author_family_explanation_context", context=context)


# ---------------------------------------------------------------------------
# Requirement 9.2: the sealed payload and every earlier identity are preserved.


def test_every_pre_existing_family_revision_keeps_its_payload_digest(tmp_path: Path) -> None:
    """Requirement 9.2: the seal does not move, compared by digest rather than by inspection.

    The dataset is the shared read-scope fixture -- real family revisions authored before this leaf
    existed -- and the case reads every one of their ``payload_digest`` values, writes a composition
    edge and an explanatory context beside them inside one transaction, and reads the digests again.
    The package's own payload version constant is asserted unchanged in the same breath, because a
    changed constant is the other way the seal could move.
    """

    fixture = build_read_scope_fixture(tmp_path / "seal")
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        before = _family_revision_digests(store)
        assert len(before) >= 3, before
        refused = store.within_immediate(
            lambda: _write_beside_the_seal(store, fixture),
            on_refusal=lambda refusal: refusal,
            failure=SqliteFailureContext(
                operation="change_candidate", table="family_composition", record_id="seal-case"
            ),
        )
        assert refused is None, refused
        after = _family_revision_digests(store)
        assert after == before
        edge_row = store.connection.execute("SELECT count(*) FROM family_composition").fetchone()
        assert edge_row is not None
        assert edge_row[0] == 1
        context_row = store.connection.execute(
            "SELECT count(*) FROM family_revision_context_revision"
        ).fetchone()
        assert context_row is not None
        assert context_row[0] == 1
    finally:
        store.close()
    assert FAMILY_REVISION_PAYLOAD_VERSION == "family-revision-payload/v1"


def _write_beside_the_seal(store: Any, fixture: ReadScopeFixture) -> None:
    """Author one composition edge and one explanatory context beside the sealed revisions.

    Both rows are written inside the caller's transaction, through the same production steps a
    candidate batch uses, with a provenance envelope the dataset already stores.
    """

    authorship = _stored_authorship(store)
    context_revision_id = str(uuid4())
    compositions.insert_composition(
        store,
        FamilyComposition(
            repository_id=store.repository_id,
            composition_id=str(uuid4()),
            from_family_revision_id=fixture.family.revision_id,
            to_family_revision_id=fixture.overlapping_family.revision_id,
            provenance=authorship,
        ),
    )
    compositions.insert_context_revision(
        store,
        FamilyExplanationContext(
            context_id=str(uuid4()),
            family_id=fixture.family.family_id,
            family_revision_id=fixture.family.revision_id,
            revision_id=context_revision_id,
            predecessor_revision_id=context_revision_id,
            body="the family's place and purpose, authored beside the seal",
            provenance=authorship,
        ),
    )


def _stored_authorship(store: Any) -> Any:
    """The provenance envelope one stored family identity of this dataset already carries."""

    row = next(iter(store.connection.execute("SELECT label_provenance FROM family LIMIT 1")))
    return decode_authorship(str(row[0]))


def _family_revision_digests(store: Any) -> dict[str, str]:
    """Every stored family revision's identity mapped to its sealed payload digest."""

    rows = store.connection.execute(
        "SELECT revision_id, payload_digest FROM family_revision WHERE repository_id = ? "
        "ORDER BY revision_id",
        (store.repository_id,),
    )
    return {str(row[0]): str(row[1]) for row in rows}


# ---------------------------------------------------------------------------
# Requirement 7.5: the escalation is recorded, named, and not activated.


def test_the_r07_escalation_proposal_is_recorded_with_its_subject_and_effect_scope() -> None:
    """Requirement 7.5: named, subject stated, existence recorded -- and explicitly not activated.

    The proposal is a placeholder for a *reference*, never for a conclusion, so the case asserts
    both halves: the artifact names the change ``SD:465`` records and the statements it would alter,
    **and** it states that no shipped policy has moved. A leaf that retired the exclusion, or that
    left the escalation unrecorded, fails here.
    """

    assert PROPOSAL_PATH.is_file(), PROPOSAL_PATH
    text = PROPOSAL_PATH.read_text(encoding="utf-8")
    for required in (
        "storage-design.md:465",
        "alters the R07 revision/frontier policy",
        "proposed — not activated",
        "Recommended scope of effect: axis B only",
        "selection policy — unchanged",
        "frontier advertisement — unchanged",
        "KS-R17@v1",
        "N6",
        "follow_composition_scope",
        "What a developer ruling would settle",
    ):
        assert required in text, required


def test_the_application_seam_is_read_only_carries_the_operation_and_moves_no_selection(
    admitted: Any,
) -> None:
    """Requirements 7.2, 7.3 and 8.4: a fifth read-only seam, and the two axes stay separate.

    The seam opens the dataset through the read-only handle, so a refused traversal leaves the file
    byte-identical; it carries ``follow_family_composition`` as its own operation name rather than
    borrowing the retrieval read's; and running it changes nothing about what
    ``read_knowledge_scope`` selects -- which is the boundary proof stated as one measurement.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        first, second = _add_family(destination, "a"), _add_family(destination, "b")
        version_id = _declare_policy(destination)
        edge = _author_edge(
            destination, first, second, policy_id=POLICY_ID, policy_version_id=version_id
        )
        assert edge.state == "changed", edge.refusal
        repository_id = store.repository_id

    before_bytes = destination.database_path.read_bytes()
    reported = family_view(destination.database_path, repository_id, first)
    assert reported.state == "reported"
    assert reported.view is not None and len(reported.view.composition_links) == 1

    scope = follow_family_composition(
        destination.database_path, repository_id, first, POLICY_ID, version_id
    )
    assert scope.state == "scope"
    assert scope.scope is not None
    assert scope.scope.policy_identity == (POLICY_ID, version_id, "2026-09-18.1")
    assert scope.scope.reached_family_revision_ids == tuple(sorted((first, second)))

    refused = follow_family_composition(
        destination.database_path, repository_id, first, POLICY_ID, str(uuid4())
    )
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.operation == "follow_family_composition"
    assert refused.refusal.code == "invalid_payload"
    assert destination.database_path.read_bytes() == before_bytes

    missing = family_view(destination.database_path, repository_id, str(uuid4()))
    assert missing.state == "refused"
    assert missing.refusal is not None
    assert missing.refusal.operation == "read_knowledge_scope"
    assert destination.database_path.read_bytes() == before_bytes
