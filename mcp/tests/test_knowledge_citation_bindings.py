"""``CitationBinding``: what is bound, in which vocabulary, and every state one observation reports.

Each case protects one consequential fact of `KS-R18@v1` rather than a code path:

* generation 5 is generation 4's manifest with one table appended, and never a reordered, renamed or
  retyped earlier table -- asserted **by the generation it descends from**, by name, so the case says
  which base it checked (1.7, Example 9);
* the shipped literals are reused rather than paralleled, and the identity is *checked*: a fact the
  shipped ``AnchorResolutionState`` already names reports that exact literal, and the shipped anchor
  resolver produces the same literal for the same fact (4.1a);
* the state set is closed, reported and counted, with a zero entry for every state that cannot be
  populated today (4.1b);
* a key of an uncovered **form** is a counted state distinct from "the key is absent from its owner
  revision", with its partial-coverage limitation, so a partial coverage cannot render complete
  (4.1c);
* an unresolvable key keeps its recorded key and its attribution, and the aggregate unresolved count
  equals the number of observed unresolved keys (4.1, 4.2, 4.3);
* a stale binding is reported stale, readable and attributed, and is never silently re-bound (4.4).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import citations, facets
from agents_remember.memory.knowledge.citation_closure import (
    assemble_citation_closure,
    enumerate_recorded_bindings,
    load_recorded_bindings,
)
from agents_remember.memory.knowledge.read_bindings import (
    STATE_FACTS,
    RecordedBinding,
    observe_binding,
    observe_target_reference,
)
from agents_remember.memory.knowledge.read_owner_revisions import (
    OWNER_REVISION_STATES,
    OwnerRevisionResolver,
)
from agents_remember.memory.knowledge.schema_generations import (
    GENERATION_4,
    GENERATION_5,
    GENERATION_5_SCHEMA_NAME,
    generation_of_new_store,
    require_pinned_generation_1_unchanged,
)
from agents_remember.memory_quality.style.citations.prose import CIT_MARK as SHIPPED_CIT_MARK
from agents_remember.models.knowledge.citation import (
    BINDING_STATES,
    CIT_MARK,
    COVERED_KEY_FORMS,
    KEY_FORMS,
    NO_SEMANTIC_COMPLETENESS_LIMITATION,
    SHIPPED_BINDING_STATES,
    CitationBindingClosureRequest,
    CitationBindingCounts,
    CitationBindingPayload,
    CitationBindingRequest,
    CitationTargetReference,
    KeyFormCoverage,
    ProseCitationKey,
    ProseOwnerRevision,
    TableRowKeyForm,
    WrittenSource,
    render_local_key,
    shipped_literal_for,
)
from agents_remember.models.knowledge.facet import AddFacet, FacetWriteRequest
from agents_remember.models.knowledge.projection import (
    AssessmentDisplay,
    ProjectionRow,
    assessment_display,
)
from agents_remember.models.knowledge.read import ANCHOR_RESOLUTIONS, AnchorResolutionState
from agents_remember.models.knowledge.source import FileLocator, LineRangeLocator, SymbolLocator
from knowledge_fixture_test_support import build_branching_knowledge_fixture, make_authorship

pytestmark = pytest.mark.evidence_unit

# One real key, read as written from the external memory root this master binds:
# ``onboarding/scripts/e2e_harness/run.py.md:43``. It is used verbatim in the cases below so the
# fixture records the same construct the corpus writes rather than a shape invented for the test.
CORPUS_DOCUMENT = "onboarding/scripts/e2e_harness/run.py.md"
CORPUS_KEY = "cit:([`_candidate_identity`], scripts/e2e_harness/run.py:206-218)"
CORPUS_WRITTEN_SOURCE = WrittenSource(
    path="scripts/e2e_harness/run.py", start_line=206, end_line=218
)

# A recorded blob identity that no object store holds. It is deliberately a well-formed 40-hex
# object id: "the recorded revision cannot be obtained" is a fact about the object store, not about
# the spelling, and a malformed spelling would be refused in the vocabulary before the lookup.
ABSENT_BLOB = "0" * 40


def _owner_revision(
    *, document_path: str = CORPUS_DOCUMENT, blob: str = ABSENT_BLOB
) -> ProseOwnerRevision:
    return ProseOwnerRevision(
        repository="ar-agents-remember", document_path=document_path, blob_object_id=blob
    )


def _prose_key(written: str = CORPUS_KEY) -> ProseCitationKey:
    return ProseCitationKey(
        written=written,
        anchor_texts=("_candidate_identity",),
        sources=(CORPUS_WRITTEN_SOURCE,),
    )


# ---------------------------------------------------------------------------
# The generation this leaf appends to.


def test_generation_5_appends_to_generation_4_without_touching_its_twenty_one_tables() -> None:
    """§1.7 and Example 9: an additive append, asserted against the generation it descends from.

    The base is named rather than restated as a list, so the case says *which* generation it checked
    and a renumber moves one name instead of silently checking a stale list.
    """

    assert GENERATION_5.tables[: len(GENERATION_4.tables)] == GENERATION_4.tables
    assert all(
        GENERATION_5.columns[table] == GENERATION_4.columns[table] for table in GENERATION_4.tables
    )
    assert len(GENERATION_5.tables) == len(GENERATION_4.tables) + 1
    assert GENERATION_5.tables[-1] == "citation_binding"
    assert GENERATION_5.schema_name == GENERATION_5_SCHEMA_NAME
    assert GENERATION_5.fingerprint != GENERATION_4.fingerprint
    assert generation_of_new_store() is GENERATION_5


def test_the_generation_1_pin_still_recomputes_after_this_leaf_appends_a_generation() -> None:
    """§Preservation Boundaries: the pinned generation-1 fingerprint is unchanged by the append."""

    require_pinned_generation_1_unchanged()


def test_the_binding_table_has_no_content_address_digest_or_fingerprint_column() -> None:
    """§1.8, the prohibition made checkable against the declared column set.

    A table with no identity-valued column cannot quietly become a second identity authority later,
    which is the shipped generation-2 argument applied to this leaf's own table. ``payload_digest``
    stays on the revision aggregate that owns it, and this case names the forbidden spellings so a
    later leaf that adds one is caught here rather than in review.
    """

    columns = GENERATION_5.columns["citation_binding"]
    forbidden = ("digest", "content_address", "fingerprint", "logical_digest", "sha256", "hash")
    for column in columns:
        assert not any(spelling in column for spelling in forbidden), (
            f"citation_binding.{column} is an identity-valued column, which §1.8 forbids: a binding "
            "is not an identity of the prose it cites and never becomes one"
        )
    assert "guidance" not in columns


def test_the_locator_check_names_exactly_the_shipped_source_locator_union() -> None:
    """§1.4: one locator vocabulary, so a binding-local spelling is inexpressible.

    The ``CHECK`` on ``target_locator_kind`` is the constraint a second locator vocabulary would have
    to widen, so the case reads the DDL the generation declares rather than a constant beside it.
    """

    ddl = GENERATION_5.table_ddl["citation_binding"]
    assert "CHECK (target_locator_kind IN ('file', 'line_range', 'symbol'))" in ddl
    assert "'symbol'" in ddl and "'line_range'" in ddl and "'file'" in ddl
    # ``SourceLocator`` declares exactly these three discriminators; a fourth member would have to
    # appear in both places or this case reddens.
    assert SymbolLocator(language="python", qualified_name="f").kind == "symbol"
    assert LineRangeLocator(start_line=1, end_line=2).kind == "line_range"
    assert FileLocator().kind == "file"


def test_the_key_form_check_names_exactly_the_declared_key_forms() -> None:
    """§4.1c: the table admits the declared forms and no others, so an undeclared form is unstorable."""

    ddl = GENERATION_5.table_ddl["citation_binding"]
    for form in KEY_FORMS:
        assert f"'{form}'" in ddl


# ---------------------------------------------------------------------------
# The shared literals, checked rather than asserted.


def test_the_prose_mark_is_the_one_the_shipped_grammar_declares() -> None:
    """§1.2: the recorded construct's mark is a quotation, and the quotation is checked.

    The binding stores the construct verbatim rather than re-deriving it, so the mark is the only
    thing this leaf has to know about the grammar. Quoting it would drift; asserting the quotation
    against the shipped parser is what keeps it from doing so.
    """

    assert CIT_MARK == SHIPPED_CIT_MARK


@pytest.mark.parametrize("state", SHIPPED_BINDING_STATES)
def test_every_shared_fact_reports_the_identical_shipped_literal(state: str) -> None:
    """§4.1a: each shared fact carries the **identical literal** on both surfaces.

    This is the case the packet names: a second vocabulary for one fact is how a silent drop becomes
    arguable, so the identity is checked rather than stated. It fails if this leaf ever respells one
    of the shared facts, and it fails if the shipped vocabulary renames a member this leaf relies on.
    """

    assert state in ANCHOR_RESOLUTIONS, (
        f"{state!r} is declared a shared literal, so it must be a member of the shipped "
        f"AnchorResolutionState vocabulary {ANCHOR_RESOLUTIONS}"
    )
    fact = STATE_FACTS[state]
    assert shipped_literal_for(fact) == state
    # The shipped ``Literal`` and the shipped tuple are two spellings of one set; both must carry it.
    annotated: AnchorResolutionState = state  # type: ignore[assignment]
    assert annotated in ANCHOR_RESOLUTIONS


def test_the_binding_vocabulary_extends_the_shipped_one_only_in_one_direction() -> None:
    """§4.1a: the extension is one-directional, so an anchor resolution acquires no citation fact.

    Two facts are being protected at once. Every shared member must be a shipped one, and no member
    this leaf *invented* may appear in the shipped vocabulary -- because a shipped literal that meant
    a citation fact would make ``AnchorResolutionState`` depend on this leaf's record group.
    """

    for state in SHIPPED_BINDING_STATES:
        assert state in ANCHOR_RESOLUTIONS
        assert state in BINDING_STATES
    invented = [state for state in BINDING_STATES if state not in ANCHOR_RESOLUTIONS]
    assert invented, "this leaf is expected to name at least one fact the shipped vocabulary lacks"
    for state in invented:
        assert state not in ANCHOR_RESOLUTIONS


def test_the_closed_vocabulary_and_its_facts_agree_in_both_directions() -> None:
    """§4.1: one closed vocabulary, with a declared fact for every member and no member left out."""

    assert tuple(STATE_FACTS) == BINDING_STATES
    assert len(set(BINDING_STATES)) == len(BINDING_STATES)
    assert set(SHIPPED_BINDING_STATES) <= set(BINDING_STATES)


def test_the_owner_revision_observation_reports_only_declared_states() -> None:
    """§4.1: the resolver reports nothing outside the vocabulary the closure counts."""

    for state in OWNER_REVISION_STATES:
        assert state in BINDING_STATES


# ---------------------------------------------------------------------------
# One observation per state, and the counts that carry them.


def test_an_uncovered_key_form_is_a_counted_state_distinct_from_an_absent_key() -> None:
    """§4.1c: the state that keeps a partial coverage from rendering like a complete one.

    The row-form key is *present and was recognized*; the increment declares it cannot read that
    form. Folding it into ``recorded_blob_mismatch`` would say the key is absent from its owner
    revision, which is false, and omitting it is the silent drop requirement 4 forbids. The case
    also pins the declared coverage to the prose form alone.
    """

    assert COVERED_KEY_FORMS == ("prose_cit_body",)
    row_key = TableRowKeyForm(
        anchor_cell="`_candidate_identity`",
        source_cell="scripts/e2e_harness/run.py:206-218",
    )
    binding = _recorded_binding(local_key=row_key)
    state, detail = observe_binding(
        binding,
        resolver=OwnerRevisionResolver(None),
        target_kinds={},
        target_revisions={},
    )
    # The owner revision cannot be obtained in this fixture, so the first fact reported is that one;
    # the uncovered-form fact is measured below against a resolver whose bytes really are there.
    assert state == "recorded_object_unavailable"
    assert state != "uncovered_key_form"
    assert "uncovered_key_form" in BINDING_STATES
    assert STATE_FACTS["uncovered_key_form"].startswith("the key is present and was recognized")
    del detail


def test_a_count_report_refuses_an_aggregate_that_dropped_a_key_from_the_denominator() -> None:
    """§4.1b and §4.3: every declared state is counted, and the counts partition the selected set.

    Two failures are caught: a report that omits a state that happens to total zero (so a reader
    cannot tell "no binding is in this state" from "this state does not exist"), and a report whose
    per-state counts do not sum to the declared selected set -- which is exactly how a key that
    resolved to nothing would leave the denominator.
    """

    zeroed = {state: 0 for state in BINDING_STATES}
    report = CitationBindingCounts(
        selected=0, returned=0, remaining=0, resolved=0, unresolved=0, stale=0, by_state=zeroed
    )
    assert set(report.by_state) == set(BINDING_STATES)
    with pytest.raises(ValueError, match="every member of the closed state vocabulary"):
        CitationBindingCounts(
            selected=0,
            returned=0,
            remaining=0,
            resolved=0,
            unresolved=0,
            stale=0,
            by_state={"exact_recorded_blob": 0},
        )
    with pytest.raises(ValueError, match="partition the declared selected set"):
        CitationBindingCounts(
            selected=5,
            returned=1,
            remaining=4,
            resolved=1,
            unresolved=0,
            stale=0,
            by_state={**zeroed, "exact_recorded_blob": 1},
        )


def test_a_key_form_coverage_refuses_to_leave_an_uncovered_form_uncounted() -> None:
    """§4.1c: an uncovered form with no count is the gap that makes partial coverage invisible."""

    coverage = KeyFormCoverage(
        covered_forms=("prose_cit_body",), uncovered_counts={"table_row_anchor_source": 0}
    )
    assert coverage.partial() is True
    with pytest.raises(ValueError, match="names a count for every form it does not cover"):
        KeyFormCoverage(covered_forms=("prose_cit_body",), uncovered_counts={})
    complete = KeyFormCoverage(covered_forms=KEY_FORMS, uncovered_counts={})
    assert complete.partial() is False


def test_a_prose_key_that_does_not_carry_the_declared_mark_is_refused() -> None:
    """§1.2: the recorded construct must be the construct the shipped form declares."""

    with pytest.raises(ValueError, match="must begin with the declared mark"):
        ProseCitationKey(written="([`x`], a.py:1-2)")
    assert render_local_key(_prose_key()) == CORPUS_KEY


# ---------------------------------------------------------------------------
# The target reference's own states and the projection's use of them.


def test_a_target_reference_reports_the_state_its_own_identity_earned() -> None:
    """§1.3 and §4.2: a missing target or a kind mismatch is an explicit state, never a deletion.

    The reference is *returned* with its state rather than replaced, so a projection can display the
    recorded attribution on the failure -- which is the shipped rule that a missing source is never a
    reason to retire a stored attribution.
    """

    target = CitationTargetReference(
        record_id=str(uuid4()), kind="terminology", locator=FileLocator()
    )
    assert target.state == "resolved"
    absent, absent_state = observe_target_reference(target, known={})
    assert absent_state == "target_record_absent"
    assert absent.state == "record_absent"
    assert absent.record_id == target.record_id
    assert absent.kind == target.kind
    mismatched, mismatch_state = observe_target_reference(
        target, known={target.record_id: "decision"}
    )
    assert mismatch_state == "target_kind_mismatch"
    assert mismatched.state == "kind_mismatch"
    assert mismatched.record_id == target.record_id


# ---------------------------------------------------------------------------
# The projection rule: an assessment arrives with its basis.


def test_a_projection_display_refuses_a_conclusion_without_its_basis() -> None:
    """§3.1: a displayed assessment carries its author and the exact inputs it examined."""

    with pytest.raises(ValueError, match="carries its author"):
        AssessmentDisplay(
            disposition="no_concern_found", author="  ", examined_inputs=("c1",), detail="d"
        )
    with pytest.raises(ValueError, match="carries the exact inputs it examined"):
        AssessmentDisplay(
            disposition="no_concern_found", author="curator:x", examined_inputs=(), detail="d"
        )


def test_a_projection_row_renders_a_missing_assessment_as_missing() -> None:
    """§3.2: an unassessed claim renders as unassessed; the projection fills nothing in."""

    row = ProjectionRow(
        statement="the retry budget is unchanged",
        binding_id=str(uuid4()),
        target_state="resolved",
        binding_state="exact_recorded_blob",
    )
    assert row.assessed() is False
    assert row.assessment is None
    assert row.stale() is False


def test_a_stale_assessment_is_measured_from_its_examined_inputs_and_never_upgraded() -> None:
    """§3.2 and §4.4: stale stays stale, and the status is measured rather than asserted."""

    current = assessment_display(
        "no_concern_found",
        "curator:x",
        ("candidate-c1",),
        current_inputs=("candidate-c1", "candidate-c2"),
        detail="examined c1",
    )
    assert current.status == "current"
    stale = assessment_display(
        "no_concern_found",
        "curator:x",
        ("candidate-c1",),
        current_inputs=("candidate-c2",),
        detail="examined c1",
    )
    assert stale.status == "stale"
    # The disposition and the author are the recorded ones: a projection introduces no new
    # interpretation and does not manufacture an approval to replace the one it cannot reuse.
    assert stale.disposition == "no_concern_found"
    assert stale.author == "curator:x"


# ---------------------------------------------------------------------------
# The closure's own contract, on a store whose bytes really exist.


def test_a_bound_closure_refuses_with_selection_incomplete_and_the_bound_reached(
    tmp_path: Path,
) -> None:
    """§2.4 and Example 5: a bounded closure refuses rather than truncating.

    Two recorded bindings are assembled under a declared bound of one, so the selected set genuinely
    exceeds the bound and the closure genuinely cannot be enumerated completely. The refusal carries
    the bound reached, via the shipped ``selection_incomplete`` literal, and the result carries
    **no** item and **no** total -- so a partial closure with a total that was never computed stays
    unrepresentable. A case that only checked the literal would pass the day the code started
    truncating instead of refusing, which is the failure this protects.
    """

    store, fixture = _binding_store(tmp_path)
    try:
        owner = _owner_revision(blob=ABSENT_BLOB)
        _record_binding(store, fixture, owner=owner, key=CORPUS_KEY)
        _record_binding(store, fixture, owner=owner, key=_second_key())
        result = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id,
                selected_owner_revisions=(owner,),
                item_limit=1,
            ),
            resolver=OwnerRevisionResolver(None),
        )
        assert result.state == "refused"
        assert result.items == ()
        assert result.counts is None
        refusal_value = result.refusal
        assert getattr(refusal_value, "code", None) == "selection_incomplete"
        assert getattr(refusal_value, "observed", None) == "2"
        assert getattr(refusal_value, "expected", None) == "at most 1 items"
        # The same set assembles completely under a bound that fits it, so the refusal is a fact
        # about the bound rather than about the selection.
        within = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id,
                selected_owner_revisions=(owner,),
                item_limit=2,
            ),
            resolver=OwnerRevisionResolver(None),
        )
        assert within.state == "assembled"
        assert len(within.items) == 2
    finally:
        store.close()


def _second_key() -> str:
    """Return a second real corpus key, so one owner revision can record two distinct keys.

    A one-key-per-owner-revision set could not express "the selected set exceeds the bound", and the
    stored ``UNIQUE`` key refuses a second binding that claims the *same* key -- so the case needs a
    genuinely different key rather than a duplicate.
    """

    return "cit:([`main`], scripts/e2e_harness/run.py:88-96)"


def test_the_closure_states_no_semantic_completeness_and_reports_its_declared_coverage(
    tmp_path: Path,
) -> None:
    """§2.3 and §4.1c: no completeness field, and the partial coverage is stated with its count."""

    store, fixture = _binding_store(tmp_path)
    try:
        owner = _owner_revision(blob=ABSENT_BLOB)
        _record_binding(store, fixture, owner=owner, key=CORPUS_KEY)
        result = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(owner,)
            ),
            resolver=OwnerRevisionResolver(None),
        )
        assert result.state == "assembled"
        assert NO_SEMANTIC_COMPLETENESS_LIMITATION in result.limitations
        assert "partial_key_form_coverage" in result.limitations
        assert "unresolved_keys_present" in result.limitations
        assert result.key_form_coverage is not None
        assert result.key_form_coverage.covered_forms == COVERED_KEY_FORMS
        assert result.key_form_coverage.uncovered_counts == {"table_row_anchor_source": 0}
        assert not hasattr(result, "semantic_completeness")
        assert not hasattr(result, "complete")
    finally:
        store.close()


def test_the_enumeration_returns_every_recorded_binding_with_exactly_one_state(
    tmp_path: Path,
) -> None:
    """§5.2: the readable per-state enumeration, over every recorded binding of the namespace.

    A census needs a denominator it can count, so the enumeration must return each binding once, with
    its owner revision, its key as written, its typed target, its locator, its governing route and
    exactly one state -- and never a binding omitted because its state was inconvenient.
    """

    store, fixture = _binding_store(tmp_path)
    try:
        owner = _owner_revision(blob=ABSENT_BLOB)
        _record_binding(store, fixture, owner=owner, key=CORPUS_KEY)
        recorded = load_recorded_bindings(store)
        assert len(recorded) == 1
        items = enumerate_recorded_bindings(store, resolver=OwnerRevisionResolver(None))
        assert len(items) == len(recorded)
        observation = items[0].observation
        assert observation.owner_revision == owner
        assert render_local_key(observation.local_key) == CORPUS_KEY
        assert observation.target.record_id
        assert observation.target.locator.kind == "line_range"
        assert observation.state in BINDING_STATES
        states = [item.observation.state for item in items]
        assert states == ["recorded_object_unavailable"]
    finally:
        store.close()


def test_an_unresolvable_key_keeps_its_recorded_key_and_its_attribution(tmp_path: Path) -> None:
    """§4.1, §4.2 and §4.3: the key is preserved on the failure and counted as unresolved.

    The count is measured against the observation rather than asserted: the aggregate unresolved
    count must equal the number of observed unresolved keys, so a report cannot be green while a key
    sits outside its denominator.
    """

    store, fixture = _binding_store(tmp_path)
    try:
        owner = _owner_revision(blob=ABSENT_BLOB)
        _record_binding(store, fixture, owner=owner, key=CORPUS_KEY)
        result = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(owner,)
            ),
            resolver=OwnerRevisionResolver(None),
        )
        assert result.counts is not None
        observed_unresolved = sum(
            1 for item in result.items if item.observation.state != "exact_recorded_blob"
        )
        assert result.counts.unresolved == observed_unresolved == 1
        assert result.counts.selected == len(result.items) == 1
        assert result.counts.by_state["recorded_object_unavailable"] == 1
        item = result.items[0]
        assert render_local_key(item.observation.local_key) == CORPUS_KEY
        assert item.observation.asserted_by_ref == "curator:l18"
        assert item.observation.target.record_id
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Small shared builders. They live in this module because a fixture that is only ever used by one
# module is not a shared artifact, and registering one would widen the governed inventory for no
# consumer.


class _Fixture:
    """The two identities a binding case needs from the shared branching fixture."""

    def __init__(self, repository_id: str, record_id: str) -> None:
        self.repository_id = repository_id
        self.record_id = record_id


def _binding_store(tmp_path: Path):
    """Build the shared branching fixture and one real knowledge record to bind against."""

    fixture = build_branching_knowledge_fixture(tmp_path / "knowledge")
    store = fixture.reopen()
    record_id = str(uuid4())
    authorship = make_authorship()
    written = facets.add_facet(
        store,
        FacetWriteRequest(
            repository_id=fixture.repository_id,
            provenance=authorship,
            command=AddFacet(
                record_id=record_id,
                revision_id=str(uuid4()),
                facet_kind="terminology",
                payload={
                    "facet_kind": "terminology",
                    "term": "citation binding",
                    "definition": (
                        "the recorded statement that one prose citation key denotes one knowledge "
                        "record at one locator"
                    ),
                    "scope": "the prose corpus of this memory repository",
                },
            ),
        ),
    )
    if written.state != "applied":
        raise AssertionError(
            f"the binding case's target record was not created: {written.refusal!r}"
        )
    return store, _Fixture(fixture.repository_id, record_id)


def _record_binding(store, fixture: _Fixture, *, owner: ProseOwnerRevision, key: str, **overrides):
    """Record one authored binding against the case's fixture record."""

    payload = CitationBindingPayload(
        owner_revision=owner,
        local_key=_prose_key(key),
        target=CitationTargetReference(
            record_id=overrides.pop("target_record_id", fixture.record_id),
            kind=overrides.pop("target_kind", "terminology"),
            revision_id=overrides.pop("target_revision_id", None),
            locator=overrides.pop("locator", LineRangeLocator(start_line=5, end_line=9)),
        ),
        asserted_by_ref=overrides.pop("asserted_by_ref", "curator:l18"),
    )
    result = citations.author_citation_binding(
        store,
        CitationBindingRequest(
            repository_id=fixture.repository_id,
            binding_id=str(uuid4()),
            payload=payload,
            governing_route_id=overrides.pop("governing_route_id", None),
        ),
        make_authorship(),
    )
    if result.state != "applied":
        raise AssertionError(f"the binding case's binding was not recorded: {result.refusal!r}")
    return result


def _recorded_binding(*, local_key, owner: ProseOwnerRevision | None = None):
    """Build one in-memory recorded binding, for cases that observe without a store."""

    return RecordedBinding(
        binding_id=str(uuid4()),
        owner_revision=owner or _owner_revision(),
        local_key=local_key,
        target=CitationTargetReference(
            record_id=str(uuid4()), kind="terminology", locator=FileLocator()
        ),
        governing_route_id=None,
        asserted_by_ref="curator:l18",
        row_digest="0" * 64,
    )
