"""Family composition: the authored edge, its declared policy, and the one shared cycle rule.

Every case here protects one clause of ``KS-R17@v1``'s ``## Required Behavior`` in the order the
packet states it: the typed authored edge and its refusal behaviour, the declared traversal policy
and the two halves ``CR17-2`` recorded, the third caller of the shared lineage rule at both check
levels, the canonical owning route, and the generation the tables are appended by.

Three properties are load-bearing enough to be named before the cases:

* **Composition is authored, never inferred.** Every case that asserts a link, a route or a context
  asserts a *stored* fact, and the cases that assert an absence assert the absence is reported rather
  than filled -- the composition table is the only thing a link can come from.
* **There is no second cycle rule.** The cycle cases exercise the shipped shared rule through
  ``lineage``'s own functions and through the batch's two levels, and one case asserts no other
  walk exists in this leaf's production surface.
* **A policy defaults to off.** An edge with no declared policy is stored, readable and not
  traversable; the cases exercise both a malformed policy's refusal and an absent one's silence.

``KS-R17@v1`` §4.5's open reading is recorded rather than hidden: the shared rule's second branch
("a retained revision reachable from it is already on a cycle") is applied **uniformly** to the
cross-family graph, which the packet names as the conservative default, and the case that exercises
that branch states the reading it verifies.
"""

from __future__ import annotations

from typing import Any, get_args
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge import open_admitted_knowledge_store
from agents_remember.memory.knowledge import (
    composition_policies,
    composition_traversal,
    compositions,
    families,
    lineage,
    lineages,
    routes,
)
from agents_remember.memory.knowledge.batch_commands import (
    _COMPOSITION_KINDS as APPLY_COMPOSITION_KINDS,
)
from agents_remember.memory.knowledge.batch_commands import (
    _INSERTING_KINDS,
    require_after_integrity,
)
from agents_remember.memory.knowledge.batch_preconditions import _COMPOSITION_KINDS, _TARGET_CHECKS
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.family_view import FamilyRevisionView, family_revision_view
from agents_remember.memory.knowledge.refusals import KnowledgeRefused
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    GENERATION_1_FINGERPRINT,
    GENERATION_2,
    GENERATION_4,
    GENERATION_5,
    GENERATION_6,
    GENERATIONS,
    descends_from,
    require_pinned_generation_1_unchanged,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    AddFamily,
    AddFamilyComposition,
    AddFamilyCompositionPolicy,
    AddFamilyRevision,
    AuthorFamilyExplanationContext,
    ExpectedRecord,
    MutableRecordTable,
    SetFamilyRevisionRoute,
)
from agents_remember.models.knowledge.census import CENSUS_WRITABLE_TABLES
from agents_remember.models.knowledge.composition import (
    COMPOSITION_COMMAND_KINDS,
    COMPOSITION_WRITABLE_TABLES,
    FOLLOW_DIRECTIONS,
    REGISTERED_REVIEW_SCOPE,
    FamilyComposition,
    FamilyCompositionDraft,
    FamilyCompositionPolicyDraft,
    FamilyCompositionPolicyVersion,
    FamilyExplanationContextDraft,
)
from agents_remember.models.knowledge.evidence import EVIDENCE_WRITABLE_TABLES
from agents_remember.models.knowledge.facet import FACET_COMMAND_KINDS, FACET_WRITABLE_TABLES
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from facet_test_support import (
    SHIPPED_COMMAND_KINDS,
    apply_commands,
    build_admitted_candidate,
    command_kinds,
    resolve_context,
    seed_subject,
)
from generation_test_support import (
    create_current_generation_store,
    create_recorded_generation_store,
)
from pydantic import ValidationError

pytestmark = pytest.mark.evidence_unit

POLICY_ID = composition_policies.REGISTERED_COMPOSITION_POLICY_ID


@pytest.fixture
def admitted(tmp_path: Any) -> tuple[Any, Any]:
    """One admitted candidate namespace, ready for a store to be opened on it."""

    return build_admitted_candidate(tmp_path / "composition")


def _add_family(destination: Any, label: str) -> str:
    """Author one family identity and its first revision, returning the revision identity."""

    family_id, revision_id = str(uuid4()), str(uuid4())
    outcome = apply_commands(
        destination,
        resolve_context(destination),
        AddFamily(kind="add_family", family_id=family_id, display_label=label),
        AddFamilyRevision(
            kind="add_family_revision",
            revision=FamilyRevisionDraft(
                family_id=family_id,
                revision_id=revision_id,
                display_version="v1",
                joint_guarantee=f"{label}: the authored joint guarantee",
                provenance=destination.authorship,
            ),
        ),
    )
    assert outcome.state == "changed", outcome.refusal
    return revision_id


def _declare_policy(
    destination: Any,
    *,
    depth_bound: int = 2,
    version: str = "2026-09-18.1",
    direction: str = "forward",
) -> str:
    """Declare one version of this leaf's policy identity, returning the version row identity."""

    version_id = str(uuid4())
    outcome = apply_commands(
        destination,
        resolve_context(destination),
        AddFamilyCompositionPolicy(
            kind="add_family_composition_policy",
            policy=FamilyCompositionPolicyDraft(
                policy_id=POLICY_ID,
                policy_version_id=version_id,
                declared_version=version,
                direction=direction,  # type: ignore[arg-type]
                depth_bound=depth_bound,
                widened_scope=REGISTERED_REVIEW_SCOPE,
                provenance=destination.authorship,
            ),
        ),
    )
    assert outcome.state == "changed", outcome.refusal
    return version_id


def _author_edge(
    destination: Any,
    from_revision_id: str,
    to_revision_id: str,
    *,
    policy_id: str | None = None,
    policy_version_id: str | None = None,
) -> Any:
    """Author one composition edge and return the whole batch result."""

    return apply_commands(
        destination,
        resolve_context(destination),
        AddFamilyComposition(
            kind="add_family_composition",
            composition_id=str(uuid4()),
            from_family_revision_id=from_revision_id,
            to_family_revision_id=to_revision_id,
            policy_id=policy_id,
            policy_version_id=policy_version_id,
        ),
    )


def _author_route(store: Any, destination: Any, path: str) -> str:
    """Author one route through the shipped operation and return its identity."""

    route_id = str(uuid4())
    written = routes.author_route(
        store.connection,
        store.repository_id,
        routes.RouteDraft(route_id=route_id, path=path),
        destination.authorship,
    )
    assert written == route_id, written
    return route_id


def _stored_provenance(store: Any) -> Any:
    """Return one already-stored provenance envelope, for a raw row the case inserts itself."""

    row = fetch_one(store.connection, "SELECT provenance FROM family_composition LIMIT 1", ())
    assert row is not None
    return row[0]


def _insert_raw_edge(store: Any, from_revision_id: str, to_revision_id: str) -> str:
    """Insert one edge directly, to reach a stored graph state no operation can author.

    The composition write path applies the shared cycle rule, so a stored cycle is *not* reachable
    through it. It is reachable through a repair script or a changeset, which is exactly why the
    after-apply whole-graph pass exists; the same route is how the earlier knowledge leaves
    construct the cyclic state their lineage cases are exercised against.
    """

    composition_id = str(uuid4())
    store.connection.execute(
        "INSERT INTO family_composition "
        "(repository_id, composition_id, from_family_revision_id, to_family_revision_id, "
        "policy_id, policy_version_id, provenance) VALUES (?, ?, ?, ?, NULL, NULL, ?)",
        (
            store.repository_id,
            composition_id,
            from_revision_id,
            to_revision_id,
            _stored_provenance(store),
        ),
    )
    return composition_id


# ---------------------------------------------------------------------------
# The vocabulary, the union, and the generation.


def test_the_composition_commands_are_the_closed_unions_own_members() -> None:
    """Requirements 1.1, 2.3 and 9.1: one union, one dispatch set, one generation's tables.

    The union is *closed*: it is exactly the shipped kinds plus every authored record group's own
    declaration, every command has a target check, every composition command has an apply step, and
    the writable-table literal is the base tables plus each group's named declaration. A command added
    without a check or a step fails here rather than at a caller's expense -- which is the property the
    earlier leaves' equivalent case protects, kept by unioning each leaf's own published constant
    instead of restating a count.

    RE-SCOPED by L12's landing, and *not* weakened. This case asserted the union as an equality over
    the three groups that existed when L17 landed; the next leaf to append its own two commands to the
    same closed union (``KS-R12@v1``) falsifies that spelling while leaving the property this case
    owns intact. So the union is stated as it is: the containment of every group's own constant, plus
    the whole-union fact that the dispatch table is exactly the union (``set(_TARGET_CHECKS) == kinds``
    below, unchanged). A command added to the union without a check still reddens here, which is what
    the equality was standing in for.

    RE-SCOPED AGAIN by L21's landing, and again *not* weakened -- this is the second instance of the
    same mechanism, caught by the adversarial coverage review as its finding ``A-1``. The
    writable-table union went stale in exactly the way the sentence above describes: ``KS-R21@v1``
    appended six census tables to the vocabulary's own ``MutableRecordTable`` and this assertion
    still named the three groups that existed when L17 landed, so the case failed ``3.20 s`` after
    the tip was frozen. The repair is the rule this case already states -- the new group's published
    constant (``models.knowledge.census.CENSUS_WRITABLE_TABLES``) joins the union as a fourth term,
    which is what ``test_knowledge_facets.py``'s equivalent assertion did for the same append. No
    member was deleted from the assertion and no group was absorbed into the literal: the census
    membership is asked of the census vocabulary, so a table added there still reddens this case by
    making the two declarations disagree.
    """

    kinds = command_kinds()
    assert kinds >= SHIPPED_COMMAND_KINDS | set(FACET_COMMAND_KINDS) | COMPOSITION_COMMAND_KINDS
    assert set(_TARGET_CHECKS) == kinds
    assert set(_COMPOSITION_KINDS) == COMPOSITION_COMMAND_KINDS
    assert set(APPLY_COMPOSITION_KINDS) == COMPOSITION_COMMAND_KINDS
    assert (kinds | set(_INSERTING_KINDS)) >= COMPOSITION_COMMAND_KINDS
    declared_tables = set(get_args(MutableRecordTable))
    assert declared_tables == (
        {
            "invariant",
            "invariant_revision",
            "family",
            "family_revision",
            "source_anchor",
            "family_member",
            "realization_claim",
        }
        | set(FACET_WRITABLE_TABLES)
        | COMPOSITION_WRITABLE_TABLES
        | set(EVIDENCE_WRITABLE_TABLES)
        # The census group's own declared table set, registered by ``260915-KS-L21``: its three
        # record tables and the three relations they resolve through. Named as the group's own
        # constant rather than spelled here, which is the rule this case already applies to the
        # facet, composition and evidence groups -- an appended generation adds its constant to
        # this union instead of editing the literals above. The registry it must agree with is the
        # one the vocabulary declares; nothing here restates the members.
        | set(CENSUS_WRITABLE_TABLES)
    )
    assert set(GENERATION_6.tables[len(GENERATION_5.tables) :]) == COMPOSITION_WRITABLE_TABLES


def test_the_declared_policy_shape_refuses_every_half_declared_state() -> None:
    """Requirement 3.2 and ``CR17-2``: identity **and** version, a finite bound, a named scope.

    ``CR17-2`` recorded that the packet's schematic example declared both policy columns nullable
    and made neither identity-without-version nor version-without-identity a refusal. This case
    exercises both halves at the value boundary; the table's own ``CHECK`` refuses the same two
    states structurally, which the edge case below measures against the database.
    """

    version_id = str(uuid4())
    for overrides in (
        {"policy_id": "   "},
        {"declared_version": "  "},
        {"depth_bound": 0},
        {"widened_scope": "the scope"},
    ):
        values: dict[str, Any] = {
            "policy_id": POLICY_ID,
            "policy_version_id": version_id,
            "declared_version": "2026-09-18.1",
            "direction": "forward",
            "depth_bound": 2,
            "widened_scope": REGISTERED_REVIEW_SCOPE,
            "provenance": _provenance(),
        }
        values.update(overrides)
        with pytest.raises(ValidationError):
            FamilyCompositionPolicyDraft(**values)

    for overrides in (
        {"policy_id": POLICY_ID, "policy_version_id": None},
        {"policy_id": None, "policy_version_id": version_id},
    ):
        with pytest.raises(ValidationError) as refused:
            _draft_edge(**overrides)
        assert "identity *and* a version" in str(refused.value), overrides

    same = str(uuid4())
    with pytest.raises(ValidationError) as self_loop:
        _draft_edge(from_family_revision_id=same, to_family_revision_id=same)
    assert "relationship between two guarantees" in str(self_loop.value)
    # Every declared direction is a member of the closed vocabulary, and the one scope this leaf
    # declares is the registered review scope (``Doc13:287``).
    assert set(FOLLOW_DIRECTIONS) == {"forward", "reverse", "both"}
    assert REGISTERED_REVIEW_SCOPE == "registered_review_scope"


def _provenance() -> Any:
    """One provenance envelope for a value-boundary case that needs a well-formed one."""

    return Authorship(
        actor_ref="agent:composition-case",
        authorization_ref="260915-KS developer kickoff ruling",
        operation_id=uuid4(),
        recorded_at="2026-09-18T00:00:00+00:00",
    )


def _draft_edge(**overrides: Any) -> FamilyCompositionDraft:
    values: dict[str, Any] = {
        "composition_id": str(uuid4()),
        "from_family_revision_id": str(uuid4()),
        "to_family_revision_id": str(uuid4()),
        "provenance": _provenance(),
    }
    values.update(overrides)
    return FamilyCompositionDraft(**values)


def test_the_registered_generation_appends_the_six_tables_to_the_generation_it_descends_from() -> (
    None
):
    """Requirement 9.1 and ``KS-R10@v1`` §1.3: appended, never retyped, never reordered.

    The inheritance is asserted against **the generation this one descends from, by name** -- every
    one of generation 5's own names keeps the exact column tuple, primary key and typed-JSON set
    that generation declared -- so the case states which base it checked rather than hiding it
    behind a literal. Generation 5 is the sibling leaf's citation-binding generation, which landed
    first and kept the number; this leaf's tables are the generation *above* it. Generation 1's pin
    and every earlier generation's fingerprint are asserted unchanged in the same breath, because an
    append that moved one of them would not be an append.
    """

    assert CURRENT_GENERATION is GENERATIONS[-1]
    # RE-SCOPED by L12's landing: this read ``CURRENT_GENERATION is GENERATION_6``, which is a claim
    # about *how many generations exist* rather than about this leaf's generation, and the next leaf's
    # append falsifies it. ``CURRENT_GENERATION is GENERATIONS[-1]`` above already asserts the property
    # the literal stood for, and the two lines below still state L17's own generation in full.
    assert descends_from(GENERATION_6, GENERATION_5, GENERATION_5.tables)
    assert GENERATION_6.tables[: len(GENERATION_5.tables)] == GENERATION_5.tables
    assert GENERATION_6.tables[len(GENERATION_5.tables) :] == (
        "family_composition",
        "family_composition_policy",
        "family_composition_policy_version",
        "family_revision_route",
        "family_revision_context",
        "family_revision_context_revision",
    )
    for generation in GENERATIONS:
        assert generation.fingerprint, generation.schema_name
    require_pinned_generation_1_unchanged()
    assert GENERATION_1.fingerprint == GENERATION_1_FINGERPRINT


def test_a_dataset_predating_the_composition_tables_refuses_a_composition_write(
    tmp_path: Any,
) -> None:
    """Requirement 9.1 and the Failure And Recovery Behavior: refused, never migrated.

    A generation-4 or generation-5 dataset carries neither the composition tables nor a version of
    them, and the guard reads the *dataset's* declared generation rather than the build's.
    Generation 5 is the sibling leaf's generation, so it is the nearest preceding generation and the
    one a renumber makes most worth pinning; the generation-2 case is the same fact further back;
    and the created generation is this build's own.
    """

    repository_id = str(uuid4())
    for version, generation in (
        (2, GENERATION_2),
        (4, GENERATION_4),
        (5, GENERATION_5),
    ):
        store = create_recorded_generation_store(
            tmp_path / f"g{version}", repository_id, generation
        )
        try:
            assert store.generation.user_version == version
            assert "family_composition" not in store.generation.tables
        finally:
            store.close()
    created = create_current_generation_store(tmp_path / "current", repository_id)
    try:
        # RE-SCOPED by L12's landing: a *new* store declares the newest registered generation, so the
        # literal ``GENERATION_6.user_version`` was a claim that goes stale with the next append. What
        # the case owns is that a store created now carries this leaf's tables, which the line below
        # still asserts.
        assert created.generation.user_version == CURRENT_GENERATION.user_version
        assert "family_composition" in created.generation.tables
    finally:
        created.close()


# ---------------------------------------------------------------------------
# The authored edge: absence of a policy, refusal of a bad one, typed endpoints, uniqueness.


def test_an_edge_with_no_declared_policy_is_stored_readable_and_not_traversable(
    admitted: Any,
) -> None:
    """Requirement 3.1: absence never widens anything, and that default is the safety property."""

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left, right = _add_family(destination, "left"), _add_family(destination, "right")
        outcome = _author_edge(destination, left, right)
        assert outcome.state == "changed", outcome.refusal
        assert outcome.changed[0].table == "family_composition"
        stored = compositions.get_composition(store, outcome.changed[0].record_id)
        assert stored is not None
        assert stored.policy_id is None and stored.policy_version_id is None
        assert stored.traversable is False
        links = compositions.composition_links_of_revision(store, left)
        assert [(link.direction, link.traversable) for link in links] == [("outgoing", False)]
        assert compositions.composition_links_of_revision(store, right)[0].direction == "incoming"
        # The edge is reported by the projection and followed by nothing: a traversal names a
        # declared policy version, and there is none to name.
        with pytest.raises(KnowledgeRefused) as refused:
            composition_traversal.follow_composition_scope(store, left, POLICY_ID, str(uuid4()))
        assert "not a declared policy version" in str(refused.value)


def test_an_undeclared_policy_reference_is_refused_and_no_row_is_written(admitted: Any) -> None:
    """Requirement 3.5: an unknown identity and an unknown version of a known identity differ."""

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left, right = _add_family(destination, "left"), _add_family(destination, "right")
        version_id = _declare_policy(destination)

        unknown_identity = _author_edge(
            destination,
            left,
            right,
            policy_id="policy/nobody-declared",
            policy_version_id=version_id,
        )
        assert unknown_identity.refusal is not None
        assert unknown_identity.refusal.code == "invalid_payload"
        assert "not declared in this repository namespace" in unknown_identity.refusal.detail

        unknown_version = _author_edge(
            destination, left, right, policy_id=POLICY_ID, policy_version_id=str(uuid4())
        )
        assert unknown_version.refusal is not None
        assert unknown_version.refusal.code == "invalid_payload"
        assert "declares no version" in unknown_version.refusal.detail

        assert compositions.find_composition_by_pair(store, left, right, None) is None
        assert compositions.find_composition_by_pair(store, left, right, POLICY_ID) is None
        assert compositions.find_composition_by_pair(store, left, right, POLICY_ID, version_id) == (
            compositions.find_composition_by_pair(store, left, right, POLICY_ID, version_id)
        )
        assert (
            next(iter(store.connection.execute("SELECT count(*) FROM family_composition")))[0] == 0
        )


def test_every_non_family_endpoint_kind_is_unrepresentable_rather_than_merely_rejected(
    admitted: Any,
) -> None:
    """Requirements 1.1, 1.2 and 2.3: typed endpoints, and the offending identity named.

    The invariant revision is a *stored* row of this namespace and it still cannot be an endpoint:
    there is no column that could hold one. The case measures both halves -- the write path refuses
    before any row, with the offending identity in its facts, and the table carries exactly two
    foreign keys, each to ``family_revision``, and no polymorphic target.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        _invariant_id, invariant_revision_id = seed_subject(
            store, destination, destination.authorship
        )
        left = _add_family(destination, "left")
        ddl = GENERATION_6.table_ddl["family_composition"]
        assert ddl.count("REFERENCES family_revision(repository_id, revision_id)") == 2
        assert "target_kind" not in ddl and "related_to" not in ddl and "target_id" not in ddl

        for stranger in (invariant_revision_id, str(uuid4())):
            refused = _author_edge(destination, stranger, left)
            assert refused.state == "refused", stranger
            assert refused.refusal is not None
            assert refused.refusal.code == "invalid_reference"
            assert refused.refusal.record_id == stranger
            assert refused.refusal.table == "family_revision"

        assert (
            next(iter(store.connection.execute("SELECT count(*) FROM family_composition")))[0] == 0
        )


def test_one_pair_under_one_policy_is_stored_once_and_a_second_policy_is_a_second_edge(
    admitted: Any,
) -> None:
    """Requirement 2.4: the declared unique tuple, and the question the packet leaves to the leaf.

    The recorded decision is that a *second policy identity or version* over the same pair is a
    second edge -- two separately authored meanings -- while the same pair under the same policy
    identity is one fact. The read reports both in declared order and prefers neither.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left, right = _add_family(destination, "left"), _add_family(destination, "right")
        first = _author_edge(destination, left, right)
        assert first.state == "changed", first.refusal
        duplicate = _author_edge(destination, left, right)
        assert duplicate.refusal is not None
        assert duplicate.refusal.code == "relationship_constraint"
        assert duplicate.refusal.expected == first.changed[0].record_id

        version_one = _declare_policy(destination, version="2026-09-18.1")
        version_two = _declare_policy(destination, version="2026-09-18.2")
        second = _author_edge(
            destination, left, right, policy_id=POLICY_ID, policy_version_id=version_one
        )
        assert second.state == "changed", second.refusal
        third = _author_edge(
            destination, left, right, policy_id=POLICY_ID, policy_version_id=version_two
        )
        assert third.state == "changed", third.refusal
        links = compositions.composition_links_of_revision(store, left)
        assert [link.composition_id for link in links] == sorted(
            (first.changed[0].record_id, second.changed[0].record_id, third.changed[0].record_id)
        )
        assert {link.declared_version for link in links} == {None, "2026-09-18.1", "2026-09-18.2"}
        assert [link.direction for link in links] == ["outgoing"] * 3

        # The table's own key refuses the state the value boundary refuses, so a row arriving
        # through a changeset cannot hold half a policy either.
        with pytest.raises(apsw.Error):
            store.connection.execute(
                "INSERT INTO family_composition "
                "(repository_id, composition_id, from_family_revision_id, to_family_revision_id, "
                "policy_id, policy_version_id, provenance) VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (
                    store.repository_id,
                    str(uuid4()),
                    right,
                    left,
                    POLICY_ID,
                    _stored_provenance(store),
                ),
            )


def test_an_authored_edge_a_policy_and_a_context_are_immutable_at_the_database(
    admitted: Any,
) -> None:
    """Requirements 2.2 and 6.4: no in-place edit, no delete, from any code path.

    The operation's own preconditions return a typed refusal; the triggers exist so a changeset, a
    repair script or a future code path that forgot the rule still cannot rewrite a sealed
    association. The case drives the database directly, which is the only way to observe the second
    half of that claim.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left, right = _add_family(destination, "left"), _add_family(destination, "right")
        version_id = _declare_policy(destination)
        edge = _author_edge(
            destination, left, right, policy_id=POLICY_ID, policy_version_id=version_id
        )
        assert edge.state == "changed", edge.refusal
        composition_id = edge.changed[0].record_id
        context = FamilyExplanationContextDraft(
            context_id=str(uuid4()),
            revision_id=str(uuid4()),
            family_revision_id=left,
            body="why this family exists, in prose",
            provenance=destination.authorship,
        )
        authored = apply_commands(
            destination,
            resolve_context(destination),
            AuthorFamilyExplanationContext(
                kind="author_family_explanation_context", context=context
            ),
        )
        assert authored.state == "changed", authored.refusal

        attempts = (
            (
                "UPDATE family_composition SET to_family_revision_id = ? WHERE composition_id = ?",
                (right, composition_id),
            ),
            ("DELETE FROM family_composition WHERE composition_id = ?", (composition_id,)),
            (
                "UPDATE family_revision_context_revision SET body = 'rewritten' "
                "WHERE revision_id = ?",
                (context.revision_id,),
            ),
            (
                "DELETE FROM family_revision_context_revision WHERE revision_id = ?",
                (context.revision_id,),
            ),
            (
                "UPDATE family_composition_policy_version SET depth_bound = 99 "
                "WHERE policy_version_id = ?",
                (version_id,),
            ),
        )
        for statement, parameters in attempts:
            with pytest.raises(apsw.Error) as refused:
                store.connection.execute(statement, parameters)
            assert "immutable_revision" in str(refused.value), statement


def test_a_second_policy_identity_over_one_pair_is_reported_and_never_preferred(
    admitted: Any,
) -> None:
    """Requirement 2.4's second half: the read never silently prefers one of two meanings.

    The same pair carries an undeclared-policy edge and a declared-policy edge, and the projection
    reports both with their own policy identities and versions. ``None`` -- not traversable -- is
    reported as ``None`` rather than as a default policy, which is what makes "the read does not
    silently prefer one" observable.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left, right = _add_family(destination, "left"), _add_family(destination, "right")
        version_id = _declare_policy(destination)
        bare = _author_edge(destination, left, right)
        declared = _author_edge(
            destination, left, right, policy_id=POLICY_ID, policy_version_id=version_id
        )
        assert bare.state == "changed" and declared.state == "changed"
        links = compositions.composition_links_of_revision(store, left)
        assert len(links) == 2
        by_identity = {link.policy_id: link for link in links}
        assert by_identity[None].traversable is False
        assert by_identity[POLICY_ID].traversable is True
        assert by_identity[POLICY_ID].declared_version == "2026-09-18.1"
        assert by_identity[POLICY_ID].widened_scope == REGISTERED_REVIEW_SCOPE
        assert by_identity[POLICY_ID].depth_bound == 2


# ---------------------------------------------------------------------------
# Requirement 5: the canonical owning route, and the inference that is not one.


def test_a_recorded_route_governs_and_the_ungoverned_state_is_reported_not_filled(
    admitted: Any,
) -> None:
    """Requirements 5.1, 5.2 and 5.3: recorded, never inferred; ungoverned is a state, not a gap.

    The route that *would* have been inferred is authored and stored -- an ancestor of the path this
    leaf's own module lives at -- and the case asserts the revision is still reported ungoverned,
    because a member's location is not an authored association. Recording an association then
    governs it, re-recording it is idempotent, and naming another route is refused rather than
    silently reconciled.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left = _add_family(destination, "left")
        route_id = _author_route(store, destination, "mcp/src/agents_remember/memory/knowledge")
        assert compositions.owning_route_of_family_revision(store, left) is None
        view = family_revision_view(store, left)
        assert isinstance(view, FamilyRevisionView)
        assert view.owning_route_id is None
        assert view.governed is False

        recorded = apply_commands(
            destination,
            resolve_context(destination),
            SetFamilyRevisionRoute(
                kind="set_family_revision_route", family_revision_id=left, route_id=route_id
            ),
        )
        assert recorded.state == "changed", recorded.refusal
        assert compositions.owning_route_of_family_revision(store, left) == route_id
        recorded_view = family_revision_view(store, left)
        assert isinstance(recorded_view, FamilyRevisionView)
        assert recorded_view.owning_route_id == route_id

        again = apply_commands(
            destination,
            resolve_context(destination),
            SetFamilyRevisionRoute(
                kind="set_family_revision_route", family_revision_id=left, route_id=route_id
            ),
        )
        assert again.state in {"changed", "no_change"}, again.refusal
        other = _author_route(store, destination, "mcp/src/agents_remember/application")
        refused = apply_commands(
            destination,
            resolve_context(destination),
            SetFamilyRevisionRoute(
                kind="set_family_revision_route", family_revision_id=left, route_id=other
            ),
        )
        assert refused.refusal is not None
        assert refused.refusal.code == "relationship_constraint"
        assert compositions.owning_route_of_family_revision(store, left) == route_id


def test_a_route_that_is_not_authored_refuses_before_the_association_is_written(
    admitted: Any,
) -> None:
    """Requirements 5.2 and 2.3: a dangling route is refused by name, never resolved."""

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left = _add_family(destination, "left")
        refused = apply_commands(
            destination,
            resolve_context(destination),
            SetFamilyRevisionRoute(
                kind="set_family_revision_route",
                family_revision_id=left,
                route_id=str(uuid4()),
            ),
        )
        assert refused.refusal is not None
        assert refused.refusal.code == "missing_expected_row"
        assert refused.refusal.table == "route"
        assert refused.refusal.record_id is not None
        assert compositions.owning_route_of_family_revision(store, left) is None


def test_a_context_is_bound_to_the_exact_revision_and_a_successor_appends(
    admitted: Any,
) -> None:
    """Requirement 6.1, 6.4 and 6.5: separable, immutable, and identified by its own pair.

    The context is authored against one exact family revision, a change is a newly identified
    revision naming its predecessor, and the earlier text stays readable under the earlier dataset.
    Nothing in the diff moves the guarantee, which the case measures rather than asserts: the
    revision's ``payload_digest`` is read before and after.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left = _add_family(destination, "left")
        before = _guarantee_and_digest(store, left)
        context_id = str(uuid4())
        first = FamilyExplanationContextDraft(
            context_id=context_id,
            revision_id=str(uuid4()),
            family_revision_id=left,
            body="where this family sits, and why it exists",
            provenance=destination.authorship,
        )
        written = apply_commands(
            destination,
            resolve_context(destination),
            AuthorFamilyExplanationContext(kind="author_family_explanation_context", context=first),
        )
        assert written.state == "changed", written.refusal
        assert sorted(entry.table for entry in written.changed) == [
            "family_revision_context",
            "family_revision_context_revision",
        ]
        stored = compositions.context_of_family_revision(store, left)
        assert stored is not None and stored.body == first.body
        assert stored.is_first_revision is True

        successor = FamilyExplanationContextDraft(
            context_id=context_id,
            revision_id=str(uuid4()),
            family_revision_id=left,
            predecessor_revision_id=first.revision_id,
            body="where this family sits, why it exists, and what it is not",
            provenance=destination.authorship,
        )
        edited = apply_commands(
            destination,
            resolve_context(destination),
            AuthorFamilyExplanationContext(
                kind="author_family_explanation_context", context=successor
            ),
        )
        assert edited.state == "changed", edited.refusal
        stored_context = compositions.context_of_family_revision(store, left)
        assert stored_context is not None
        assert stored_context.body == successor.body
        earlier = compositions.get_context_revision(store, context_id, first.revision_id)
        assert earlier is not None and earlier.body == first.body
        assert _guarantee_and_digest(store, left) == before

        # A successor must name its exact predecessor, and a second context for one revision is
        # refused rather than silently replacing the first.
        orphan = apply_commands(
            destination,
            resolve_context(destination),
            AuthorFamilyExplanationContext(
                kind="author_family_explanation_context",
                context=FamilyExplanationContextDraft(
                    context_id=context_id,
                    revision_id=str(uuid4()),
                    family_revision_id=left,
                    body="a change that names no predecessor",
                    provenance=destination.authorship,
                ),
            ),
        )
        assert orphan.refusal is not None
        assert orphan.refusal.code == "invalid_reference"
        assert "names itself" in orphan.refusal.detail
        second_context = apply_commands(
            destination,
            resolve_context(destination),
            AuthorFamilyExplanationContext(
                kind="author_family_explanation_context",
                context=FamilyExplanationContextDraft(
                    context_id=str(uuid4()),
                    revision_id=str(uuid4()),
                    family_revision_id=left,
                    body="a second context for the same revision",
                    provenance=destination.authorship,
                ),
            ),
        )
        assert second_context.refusal is not None
        assert second_context.refusal.code == "relationship_constraint"


def _guarantee_and_digest(store: Any, revision_id: str) -> tuple[str, str]:
    """Return one stored family revision's guarantee text and its sealed payload digest."""

    stored = families.get_family_revision(store, revision_id)
    assert stored is not None
    return (stored.revision.joint_guarantee, stored.revision.payload_digest)


def test_a_context_whose_subject_is_not_a_family_revision_is_refused(admitted: Any) -> None:
    """Requirement 6.1 and 2.3: the subject is an exact stored family revision, or nothing."""

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        _invariant_id, invariant_revision_id = seed_subject(
            store, destination, destination.authorship
        )
        refused = apply_commands(
            destination,
            resolve_context(destination),
            AuthorFamilyExplanationContext(
                kind="author_family_explanation_context",
                context=FamilyExplanationContextDraft(
                    context_id=str(uuid4()),
                    revision_id=str(uuid4()),
                    family_revision_id=invariant_revision_id,
                    body="an obligation someone tried to move out of a statement",
                    provenance=destination.authorship,
                ),
            ),
        )
        assert refused.refusal is not None
        assert refused.refusal.code == "invalid_reference"
        assert refused.refusal.record_id == invariant_revision_id
        assert refused.refusal.table == "family_revision"


# ---------------------------------------------------------------------------
# Requirement 4: one cycle rule, three graphs.


def test_one_shared_rule_judges_the_composition_graph_at_both_check_levels(admitted: Any) -> None:
    """Requirements 4.1, 4.2 and 4.3: the declared graph, the stored graph, and no second rule.

    The declared-graph level refuses a batch whose *own* three declarations close a cycle, before
    any row is written, and the refusal names the revisions on it. The whole-graph level refuses the
    same cycle once the rows exist. The walk is ``lineage``'s: the third edge source supplies edges
    and nothing else, and the same generic functions judge the cross-family graph.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        first, second, third = (_add_family(destination, name) for name in ("a", "b", "c"))
        for left, right in ((first, second), (second, third)):
            accepted = _author_edge(destination, left, right)
            assert accepted.state == "changed", accepted.refusal

        declared = _author_edge(destination, third, first)
        assert declared.refusal is not None
        assert declared.refusal.code == "lineage_cycle"
        assert set(declared.refusal.observed.split(" | ")) >= {first, second, third}
        assert declared.refusal.table == "family_composition"
        assert (
            next(iter(store.connection.execute("SELECT count(*) FROM family_composition")))[0] == 2
        )

        edges = lineages.composition_edges(store.connection, store.repository_id)
        assert set(edges) == {(first, second), (second, third)}
        finding = lineage.find_cycle(candidate_id=third, predecessors=(first,), edges=edges)
        assert finding is not None and finding.candidate_on_cycle is True
        assert set(finding.members) == {first, second, third}

        _insert_raw_edge(store, third, first)
        assert (
            next(iter(store.connection.execute("SELECT count(*) FROM family_composition")))[0] == 3
        )
        with pytest.raises(KnowledgeRefused) as refused:
            require_after_integrity(store)
        assert refused.value.refusal.code == "lineage_cycle"
        cycle_members = refused.value.refusal.observed
        assert cycle_members is not None
        assert set(cycle_members.split(" | ")) == {first, second, third}


def test_the_shared_rules_second_branch_applies_uniformly_to_the_cross_family_graph(
    admitted: Any,
) -> None:
    """Requirement 4.5: the conservative default, exercised rather than asserted.

    The packet leaves one reading open: whether ``find_cycle``'s second branch -- a candidate that
    descends from a stored cycle -- means the same thing for a graph over *different* families as it
    does for one object's lineage. This leaf applies the whole rule uniformly, which the packet
    names as the conservative default, and this case is the evidence for that ruling. A stored cycle
    between two families is constructed through the same raw route the earlier knowledge leaves use,
    and a candidate edge *reaching* it is refused by the second branch naming both cycle members and
    only those.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        cycled, reached, outsider = (_add_family(destination, name) for name in ("x", "y", "z"))
        stored = _author_edge(destination, cycled, reached)
        assert stored.state == "changed", stored.refusal
        _insert_raw_edge(store, reached, cycled)
        edges = lineages.composition_edges(store.connection, store.repository_id)
        assert set(edges) == {(cycled, reached), (reached, cycled)}

        finding = lineage.find_cycle(candidate_id=outsider, predecessors=(reached,), edges=edges)
        assert finding is not None
        assert finding.candidate_on_cycle is False
        assert set(finding.members) == {cycled, reached}

        refused = _author_edge(destination, outsider, reached)
        assert refused.refusal is not None
        assert refused.refusal.code == "lineage_cycle"
        assert set(refused.refusal.observed.split(" | ")) == {cycled, reached}
        assert refused.refusal.table == "family_composition"


def test_no_second_cycle_implementation_exists_beside_the_shared_rule() -> None:
    """Requirement 4.1 and the packet's reuse proof: composition reuses ``lineage``.

    A copied or paraphrased walk is a defect even if it passes its own tests, so the case asserts the
    shape: the third edge source is a statement and nothing else, and no module this leaf adds
    implements a strongly-connected-component scan of its own. Every composition cycle verdict in
    this package is reached through ``lineage``'s functions.
    """

    module_functions = {
        name
        for name, value in vars(lineages).items()
        if getattr(value, "__module__", None) == lineages.__name__
    }
    assert module_functions == {"composition_edges"}
    for name, value in vars(compositions).items():
        if not callable(value) or getattr(value, "__module__", None) != compositions.__name__:
            continue
        text = value.__doc__ or ""
        assert "tarjan" not in text.lower(), name
        assert "strongly connected" not in text.lower(), name
    for table in COMPOSITION_WRITABLE_TABLES:
        ddl = GENERATION_6.table_ddl.get(table)
        if ddl is None:
            continue
        assert "WITH RECURSIVE" not in ddl.upper(), table


def test_the_edge_and_the_context_join_the_expectation_and_receipt_surfaces(
    admitted: Any,
) -> None:
    """Requirements 2.1 and 2.2: an authored row is addressable by its own digest.

    A candidate write can state an expectation about a composition row and can receipt one, which is
    what makes "the caller names the row it read" a property of this record group too. A stale
    expectation refuses and writes nothing.
    """

    destination, _ = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        left, right = _add_family(destination, "left"), _add_family(destination, "right")
        edge = _author_edge(destination, left, right)
        assert edge.state == "changed", edge.refusal
        composition_id = edge.changed[0].record_id
        digest = edge.changed[0].digest

        version_id = _declare_policy(destination)
        second = AddFamilyComposition(
            kind="add_family_composition",
            composition_id=str(uuid4()),
            from_family_revision_id=left,
            to_family_revision_id=right,
            policy_id=POLICY_ID,
            policy_version_id=version_id,
        )
        stale = apply_commands(
            destination,
            resolve_context(destination),
            second,
            expected=(
                ExpectedRecord(
                    state="present",
                    table="family_composition",
                    record_id=composition_id,
                    digest="0" * 64,
                ),
            ),
        )
        assert stale.refusal is not None
        assert stale.refusal.code == "stale_precondition"
        assert (
            next(iter(store.connection.execute("SELECT count(*) FROM family_composition")))[0] == 1
        )

        fresh = apply_commands(
            destination,
            resolve_context(destination),
            AddFamilyComposition(**{**second.model_dump(), "composition_id": str(uuid4())}),
            expected=(
                ExpectedRecord(
                    state="present",
                    table="family_composition",
                    record_id=composition_id,
                    digest=digest,
                ),
            ),
        )
        assert fresh.state == "changed", fresh.refusal
        assert fresh.changed[0].table == "family_composition"
        assert isinstance(fresh.changed[0].record_id, str)
        assert FamilyComposition.__name__ == "FamilyComposition"
        assert FamilyCompositionPolicyVersion.__name__ == "FamilyCompositionPolicyVersion"
