"""ICR-R06@v1: an added or removed statement keeps its available text and names the absent side.

These cases drive the **real** adapter (:func:`compose_review`) over two **real** datasets, each
built through the public store operations by :func:`read_scope_test_support.build_read_scope_fixture`
and then extended with one authored invariant apiece. Nothing here substitutes a payload, edits a
stored row or deletes a record to manufacture a case: the after snapshot simply holds an invariant
the before snapshot does not, and the before snapshot holds one the after snapshot does not. Those two
snapshots are what an addition and a removal *are*, and the reviewed subject is selected from each
side in turn.

The load-bearing properties, one case each:

* an **addition** serves the complete after statement beside an ``absent`` before side that names
  itself, with the comparison's own ``side_absence:before:selector_absent`` travelling beside it;
* a **removal** serves the complete before statement beside an ``absent`` after side, symmetrically;
* a field row whose **one side recorded no value** keeps the other side's value and reports the other
  as absent -- the two are different facts and the row says which is which;
* a field whose value is **structured rather than text** carries the pane's own text projection of
  each side's value instead of ``None``, because ``None`` on a field row is the model's own spelling
  of "the field was absent there": a present value is never displayed as an absent one, and the two
  sides of a changed structured field stay as distinguishable as the comparison says they are;
* a one-sided **record** reports its change through its coverage and keeps the field roster empty, so
  a one-sided statement is never dressed up as nine field differences.

The statements and states these cases measure are the contract the dashboard's statement area
renders; the renderer's own cases live in
``dashboard/src/panels/review/KnowledgeStatements.test.tsx`` and use these same values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_review import (
    ReviewCandidateResolution,
    ReviewRecordInputs,
    compose_review,
)
from agents_remember.application.review_statement_sides import (
    STRUCTURED_VALUE_LEAD,
    STRUCTURED_VALUE_TAIL,
    structured_value_text,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore, open_knowledge_store
from agents_remember.models.knowledge.base import KnowledgeState
from agents_remember.models.knowledge.read import (
    InvariantIdentitySeed,
    KnowledgeReadSeed,
)
from agents_remember.models.knowledge.result import (
    InvariantRequest,
    RevisionDraft,
    RevisionRequest,
)
from agents_remember.models.knowledge.review import (
    KnowledgeReviewPayload,
    ReviewSurfaceRequest,
)
from read_scope_test_support import (
    CONDITIONS,
    EXCLUSIONS,
    ReadScopeFixture,
    build_read_scope_fixture,
)

pytestmark = pytest.mark.evidence_unit

# The authored identities and words. Every id is drawn once in the builder, so a case asserts against
# the exact identity the review was asked for rather than against a shape that could be anything.
ADDED_LABEL = "candidate-only-refusal-record"
ADDED_STATEMENT = "Every refused candidate write records the identity that refused it."
REMOVED_LABEL = "retired-batch-obligation"
REMOVED_STATEMENT = "A withdrawn obligation is reported as withdrawn and never silently dropped."
SHARED_LABEL = "shared-acceptance-record"
SHARED_STATEMENT = "An accepted obligation carries the reference that accepted it."
SHARED_APPLICABILITY = "Every recorded identity in this repository namespace."
SHARED_ACCEPTANCE_REF = "requirement:KS-R08@v1"

MASTER = "260921_complete-code-and-intent-review"
LEAF = "260921-ICR-L6"


@dataclass(frozen=True)
class AuthoredInvariant:
    """One invariant to author: its identities, its words, and the origin state they are authored in.

    The acceptance reference is not a free argument: the vocabulary refuses a *proposed* revision that
    carries one, because a proposal claiming an acceptance is a claim about an event that did not
    happen. So the origin state alone decides it here, exactly as it does in the store.
    """

    invariant_id: str
    revision_id: str
    label: str
    statement: str
    state: KnowledgeState = "proposed"


@dataclass(frozen=True)
class OneSidedPair:
    """Two snapshots of one namespace, plus the identities the cases select subjects by."""

    namespace: str
    before: ReadScopeFixture
    after: ReadScopeFixture
    added_invariant_id: str
    added_revision_id: str
    removed_invariant_id: str
    removed_revision_id: str
    shared_invariant_id: str
    shared_revision_id: str


def author_invariant(fixture: ReadScopeFixture, authored: AuthoredInvariant) -> None:
    """Author one invariant revision through the store's own operations, refusing quietly never."""

    store: OpenedKnowledgeStore = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        created = store.create_invariant(
            InvariantRequest(
                repository_id=fixture.repository_id,
                invariant_id=authored.invariant_id,
                display_label=authored.label,
                provenance=fixture.authorship,
            )
        )
        if created.state != "created":
            raise AssertionError(f"create_invariant refused: {created.refusal}")
        accepted = authored.state == "accepted"
        revision = store.create_revision(
            RevisionRequest(
                repository_id=fixture.repository_id,
                revision=RevisionDraft(
                    revision_id=authored.revision_id,
                    invariant_id=authored.invariant_id,
                    display_version="v1",
                    statement=authored.statement,
                    applicability=SHARED_APPLICABILITY,
                    conditions=CONDITIONS,
                    exclusions=EXCLUSIONS,
                    provenance=fixture.authorship,
                    state_at_origin=authored.state,
                    acceptance_ref=SHARED_ACCEPTANCE_REF if accepted else None,
                ),
            )
        )
        if revision.state != "created":
            raise AssertionError(f"create_revision refused: {revision.refusal}")
    finally:
        store.close()


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> OneSidedPair:
    """One namespace holding two snapshots whose difference is exactly the named additions.

    Module-scoped because both datasets are read-only once built and every case drives the same
    composition over them; a per-case rebuild would pay for two fixture trees to measure the same
    bytes. No case writes after the fixture is built.
    """

    root = tmp_path_factory.mktemp("one-sided")
    namespace = str(uuid4())
    before = build_read_scope_fixture(root / "before", repository_id=namespace)
    after = build_read_scope_fixture(root / "after", repository_id=namespace)
    added_invariant_id, added_revision_id = str(uuid4()), str(uuid4())
    removed_invariant_id, removed_revision_id = str(uuid4()), str(uuid4())
    shared_invariant_id, shared_revision_id = str(uuid4()), str(uuid4())
    author_invariant(
        after,
        AuthoredInvariant(
            invariant_id=added_invariant_id,
            revision_id=added_revision_id,
            label=ADDED_LABEL,
            statement=ADDED_STATEMENT,
        ),
    )
    author_invariant(
        before,
        AuthoredInvariant(
            invariant_id=removed_invariant_id,
            revision_id=removed_revision_id,
            label=REMOVED_LABEL,
            statement=REMOVED_STATEMENT,
        ),
    )
    author_invariant(
        before,
        AuthoredInvariant(
            invariant_id=shared_invariant_id,
            revision_id=shared_revision_id,
            label=SHARED_LABEL,
            statement=SHARED_STATEMENT,
        ),
    )
    author_invariant(
        after,
        AuthoredInvariant(
            invariant_id=shared_invariant_id,
            revision_id=shared_revision_id,
            label=SHARED_LABEL,
            statement=SHARED_STATEMENT,
            state="accepted",
        ),
    )
    return OneSidedPair(
        namespace=namespace,
        before=before,
        after=after,
        added_invariant_id=added_invariant_id,
        added_revision_id=added_revision_id,
        removed_invariant_id=removed_invariant_id,
        removed_revision_id=removed_revision_id,
        shared_invariant_id=shared_invariant_id,
        shared_revision_id=shared_revision_id,
    )


def review(pair: OneSidedPair, selector: KnowledgeReadSeed) -> KnowledgeReviewPayload:
    """Render one review of the pair through the real adapter, failing loudly on a refusal."""

    result = compose_review(
        ReviewCandidateResolution(
            repository_id=pair.namespace,
            leaf_id=LEAF,
            baseline_database=pair.before.database_path,
            candidate_database=pair.after.database_path,
            baseline_code_root=pair.before.git_root,
            candidate_code_root=pair.after.git_root,
            baseline_code_tree_id=pair.before.git_tree_id,
            candidate_code_tree_id=pair.after.git_tree_id,
        ),
        ReviewSurfaceRequest(
            repository_id=pair.namespace,
            master=MASTER,
            leaf_id=LEAF,
            selector=selector,
        ),
        ReviewRecordInputs(),
    )
    if result.payload is None:
        raise AssertionError(f"the composition refused: {result.refusal}")
    return result.payload


def test_an_added_statement_renders_its_after_text_beside_a_named_absent_before(
    pair: OneSidedPair,
) -> None:
    """An added invariant keeps its complete after statement, and the before side names itself.

    The two facts the dashboard renders are asserted here as the composition serves them: the after
    side is ``present`` and carries the exact authored text, and the before side is ``absent`` --
    never empty text, and never the same state as an unreadable operand.
    """

    payload = review(pair, InvariantIdentitySeed(invariant_id=pair.added_invariant_id))
    knowledge = payload.knowledge

    assert payload.candidate.leaf_id == LEAF
    assert knowledge.invariant_ids == (pair.added_invariant_id,)
    assert knowledge.after_statement.state == "present"
    assert knowledge.after_statement.text == ADDED_STATEMENT
    assert knowledge.before_statement.state == "absent"
    assert knowledge.before_statement.text is None
    assert "before snapshot selected no record" in knowledge.before_statement.detail
    assert "side_absence:before:selector_absent" in payload.limitations


def test_a_removed_statement_renders_its_before_text_beside_a_named_absent_after(
    pair: OneSidedPair,
) -> None:
    """A removed invariant keeps its complete before statement, and the after side names itself."""

    payload = review(pair, InvariantIdentitySeed(invariant_id=pair.removed_invariant_id))
    knowledge = payload.knowledge

    assert knowledge.before_statement.state == "present"
    assert knowledge.before_statement.text == REMOVED_STATEMENT
    assert knowledge.after_statement.state == "absent"
    assert knowledge.after_statement.text is None
    assert "after snapshot selected no record" in knowledge.after_statement.detail
    assert "side_absence:after:selector_absent" in payload.limitations


def test_a_field_row_keeps_the_side_that_recorded_a_value_and_names_the_side_that_did_not(
    pair: OneSidedPair,
) -> None:
    """One subject, two sides, and a field only one of them recorded a value for.

    The same revision identity is proposed in the before snapshot and accepted in the after one, so
    the comparison reports the transition and each side's value for it: the acceptance reference is
    recorded only on the accepting side. The row is the whole point -- dropping it, or printing the
    side that did record a value as blank, is what a one-sided statement must not become.
    """

    payload = review(pair, InvariantIdentitySeed(invariant_id=pair.shared_invariant_id))
    knowledge = payload.knowledge

    assert knowledge.before_statement.state == "present"
    assert knowledge.before_statement.text == SHARED_STATEMENT
    assert knowledge.after_statement.state == "present"
    assert knowledge.after_statement.text == SHARED_STATEMENT
    rows = {(row.item_id, row.field): row for row in knowledge.field_changes}
    assert set(rows) == {
        (pair.shared_revision_id, "acceptance_ref"),
        (pair.shared_revision_id, "lifecycle"),
        (pair.shared_revision_id, "payload_digest"),
        (pair.shared_revision_id, "provenance"),
    }
    acceptance = rows[(pair.shared_revision_id, "acceptance_ref")]
    assert acceptance.before_value is None
    assert acceptance.after_value == SHARED_ACCEPTANCE_REF
    lifecycle = rows[(pair.shared_revision_id, "lifecycle")]
    assert (lifecycle.before_value, lifecycle.after_value) == ("proposed", "accepted")


def test_a_structured_field_value_is_rendered_as_its_own_text_and_never_as_an_absence(
    pair: OneSidedPair,
) -> None:
    """A recorded, structured value is served as its own rendering -- never as ``None``, never alike.

    ``ReviewFieldChange`` reads ``None`` as the recorded fact that the field was absent on that side.
    The provenance the comparison compared and reported as *changed* is present on both sides, so
    serving it as ``None`` would state two absences where the comparison measured a difference. Each
    side therefore carries the pane's text projection of the value that side really holds, and the
    projection is checkable: every token round-trips back to the stored envelope, so the rendering
    invents nothing, and the two sides differ exactly where the stored values do.
    """

    payload = review(pair, InvariantIdentitySeed(invariant_id=pair.shared_invariant_id))
    row = next(row for row in payload.knowledge.field_changes if row.field == "provenance")

    assert row.before_value is not None and row.after_value is not None
    assert row.before_value != row.after_value
    for value, fixture in (
        (row.before_value, pair.before),
        (row.after_value, pair.after),
    ):
        assert value.startswith(STRUCTURED_VALUE_LEAD)
        assert value.endswith(STRUCTURED_VALUE_TAIL)
        projected = json.loads(value[len(STRUCTURED_VALUE_LEAD) : -len(STRUCTURED_VALUE_TAIL)])
        assert projected["actor_ref"] == fixture.authorship.actor_ref
        assert projected["operation_id"] == str(fixture.authorship.operation_id)
        assert projected["authorization_ref"] == fixture.authorship.authorization_ref
    assert structured_value_text({"b": 1, "a": 2}) == structured_value_text({"a": 2, "b": 1})


def test_a_one_sided_record_is_reported_by_its_coverage_and_not_by_a_roster_of_field_rows(
    pair: OneSidedPair,
) -> None:
    """An addition keeps its field roster empty, because nine rows would state nine differences.

    The comparison reports a record only one snapshot holds through that item's coverage; widening it
    into "every field changed" would turn one absence into nine, so the pane's roster stays empty and
    the statement sides carry the whole fact.
    """

    payload = review(pair, InvariantIdentitySeed(invariant_id=pair.added_invariant_id))
    knowledge = payload.knowledge

    assert knowledge.selection_state == "subject_selected"
    assert knowledge.field_changes == ()
    assert knowledge.before_conditions == ()
    assert knowledge.after_conditions == CONDITIONS


def test_a_review_that_compared_no_subject_serves_no_operand_at_all(pair: OneSidedPair) -> None:
    """The task-context review is the case a one-sided rendering must not swallow.

    No subject was selected, so *neither* statement side is present and neither may become an empty
    operand: both sides are ``unresolved`` with the reason the same value carries. This is the state
    that tells the client to draw no diff at all, and it is measured here rather than inferred from
    the subject path.
    """

    result = compose_review(
        ReviewCandidateResolution(
            repository_id=pair.namespace,
            leaf_id=LEAF,
            baseline_database=pair.before.database_path,
            candidate_database=pair.after.database_path,
            baseline_code_root=pair.before.git_root,
            candidate_code_root=pair.after.git_root,
            baseline_code_tree_id=pair.before.git_tree_id,
            candidate_code_tree_id=pair.after.git_tree_id,
        ),
        ReviewSurfaceRequest(
            repository_id=pair.namespace,
            master=MASTER,
            leaf_id=LEAF,
            selector=None,
        ),
        ReviewRecordInputs(),
    )
    assert result.payload is not None
    knowledge = result.payload.knowledge

    assert knowledge.selection_state == "task_context"
    assert knowledge.before_statement.state == "unresolved"
    assert knowledge.after_statement.state == "unresolved"
    assert knowledge.before_statement.text is None and knowledge.after_statement.text is None
    assert knowledge.before_statement.detail == knowledge.after_statement.detail
    assert "no knowledge operand was compared" in knowledge.before_statement.detail
    assert knowledge.field_changes == ()
    assert result.payload.comparison is None
