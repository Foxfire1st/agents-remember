"""``SemanticChangeSet`` and the preservation claim: the composed record and its separate sibling.

These cases protect ``KS-R13@v1``'s second half -- the change set's six declared parts, its two exact
snapshot identities, the membership computed from the members' own declarations, the succession edge
requirement 4.8 requires, the preservation claim as a record that is *not* an effect, and the
requirement-revision reference under the opaque-reference clause. They occupy the ``unit-regression``
lane for the same reason the effect-claim module does: what they measure is a typed record's own
construction, storage boundary and derived read, not a process or a Git object.

Each case is named for the property its own assertions measure and says which failure it catches: a
change set that named a moving head, a member silently shared between two change sets, a superseded
change set rewritten in place, a preservation claim folded into the effect vocabulary, a requirement
reference parsed into a requirement identity, and an unresolved reference quietly resolved.

The absence assertions are the requirement's own enforcement style and are made at both planes -- the
frozen payload model refuses an undeclared field through its ordinary ``extra="forbid"`` rule, and no
column of any registered generation carries one of the probe names. A case that asserted only the
validator half would leave the schema free to hold the value later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

import apsw
import pytest
from agents_remember.memory.knowledge.effect_views import (
    realization_claim_resolves,
    subject_resolves,
)
from agents_remember.memory.knowledge.effects import read_effect_scope
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_7,
    GENERATION_8,
    GENERATIONS,
)
from agents_remember.models.knowledge.base import REFERENCE_MAX_LENGTH
from agents_remember.models.knowledge.candidate import (
    AddInvariantEffectClaim,
    AddPreservationClaim,
    AddSemanticChangeSet,
    AddUnresolvedQuestion,
    EffectCommand,
    RemoveRealizationClaim,
)
from agents_remember.models.knowledge.change_set import (
    CHANGE_SET_PAYLOAD_MODELS,
    SEMANTIC_CHANGE_SET_KIND,
    SEMANTIC_CHANGE_SET_SCHEMA,
    SemanticChangeSetPayload,
    SemanticChangeSetView,
)
from agents_remember.models.knowledge.effect import (
    ADMITTED_EFFECT_LABELS,
    MEMBER_PAYLOAD_MODELS,
    InvariantEffectClaimPayload,
    PreservationClaimPayload,
    PreservationSubject,
    UnresolvedQuestionPayload,
)
from candidate_batch_test_support import (
    CandidateHarness,
    CommandSeeds,
    build_candidate_harness,
    measure_refusal,
)
from candidate_batch_test_support import (
    claim_command as realization_claim_command,
)

# The names that would make a change set a task authority rather than a record of authored work.
TASK_AUTHORITY_FIELD_NAMES: tuple[str, ...] = (
    "task_status",
    "seat_owner",
    "lifecycle_gate",
    "approval",
    "approved_by_substrate",
    "implemented",
)

# The names that would make a change set a decision, or a generated narrative.
VERDICT_AND_SUMMARY_FIELD_NAMES: tuple[str, ...] = (
    "summary",
    "narrative",
    "verdict",
    "severity",
    "acceptance",
    "promotion",
    "merge_verdict",
    "intended_change",
)

# One fixed repository identity for the construction-only cases, so a payload built without a store
# still carries a well-formed pair of snapshots.
_FIXED_REPOSITORY_ID = "00000000-0000-0000-0000-000000000000"


def _harness(tmp_path: Path) -> CandidateHarness:
    """Return one admitted candidate namespace."""

    return build_candidate_harness(tmp_path)


def _snapshot(harness: CandidateHarness, digest: str) -> dict[str, str]:
    """Return one exact snapshot identity over one logical digest."""

    return {
        "repository_id": harness.repository_id,
        "schema_version": "ar-knowledge-sqlite/v8",
        "logical_digest": digest * 64,
    }


def _payload(harness: CandidateHarness, **overrides: Any) -> dict[str, Any]:
    """Return one admissible change-set payload, with named fields replaced."""

    payload: dict[str, Any] = {
        "baseline": _snapshot(harness, "a"),
        "candidate": _snapshot(harness, "b"),
        "requirement_revision_refs": (),
        "candidate_realization_claim_ids": (),
    }
    payload.update(overrides)
    return payload


def _change_set(
    harness: CandidateHarness,
    *,
    requirement_refs: tuple[str, ...] = (),
    realization_claim_ids: tuple[str, ...] = (),
    predecessors: tuple[str, ...] = (),
    record_id: str | None = None,
) -> AddSemanticChangeSet:
    """Build one change-set command over this namespace, with a fresh identity pair."""

    return AddSemanticChangeSet(
        record_id=record_id or str(uuid4()),
        revision_id=str(uuid4()),
        payload=_payload(
            harness,
            requirement_revision_refs=requirement_refs,
            candidate_realization_claim_ids=realization_claim_ids,
        ),
        predecessor_change_set_ids=predecessors,
    )


def _preservation(
    harness: CandidateHarness,
    change_set_id: str,
    subject: tuple[str, str],
    *,
    statement: str = "the guarded-merge refusal path is unchanged by this revision",
) -> AddPreservationClaim:
    """Build one preservation-claim command over one change set and one typed subject."""

    return AddPreservationClaim(
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload={
            "change_set_id": change_set_id,
            "subject": {"kind": subject[0], "reference_id": subject[1]},
            "statement": statement,
        },
    )


def _question(
    harness: CandidateHarness,
    change_set_id: str,
    *,
    statement: str = "does the shared deadline also bind the export path?",
) -> AddUnresolvedQuestion:
    """Build one open-question command over one change set."""

    return AddUnresolvedQuestion(
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload={"change_set_id": change_set_id, "statement": statement},
    )


def _effect_claim(
    harness: CandidateHarness,
    change_set_id: str,
    *,
    effect: str = "clarify",
    assessment_refs: tuple[str, ...] = (),
    inputs: tuple[str, ...] = (),
) -> AddInvariantEffectClaim:
    """Build one effect-claim command over one change set."""

    return AddInvariantEffectClaim(
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload={
            "change_set_id": change_set_id,
            "effect": effect,
            "inputs": inputs,
            "outputs": (),
            "rationale": "Only the wording moved; the obligation the statement carries is unchanged.",
            "assessment_refs": assessment_refs,
        },
    )


def _applied(harness: CandidateHarness, command: Any) -> Any:
    """Apply one command and assert it was stored, returning the receipt."""

    result = harness.apply(harness.batch(command))
    if result.state != "changed":
        raise AssertionError(f"the command this case needs was refused: {result.refusal!r}")
    return result


def _scope(harness: CandidateHarness) -> Any:
    """Read the authored-effect scope through the record group's own read operation."""

    store = harness.open()
    try:
        result = read_effect_scope(store)
    finally:
        store.close()
    if result.state != "read":
        raise AssertionError(f"the authored-effect read was refused: {result.refusal!r}")
    return result.scope


def _view(harness: CandidateHarness, record_id: str) -> SemanticChangeSetView:
    """Return one change set's derived view."""

    views = [view for view in _scope(harness).change_sets if view.record_id == record_id]
    if not views:
        raise AssertionError(f"change set {record_id} was not served by the read")
    return views[0]


def _seeded_realization(harness: CandidateHarness) -> tuple[CommandSeeds, str]:
    """Author one realization claim through the batch and return its seeds and its revision."""

    ((_, revision_id),) = harness.seed(1)
    seeds = CommandSeeds()
    _applied(harness, realization_claim_command(seeds, revision_id=revision_id))
    return seeds, revision_id


# ---------------------------------------------------------------------------
# 4.1, 4.2 -- the declared composition and the two snapshot identities.


def test_a_change_set_records_both_snapshot_identities_verbatim(tmp_path: Path) -> None:
    """4.2: the baseline **and** the candidate are stored as the comparison's own identities.

    Catches a change set that named a moving head, or that stored only one side: both identities are
    read back exactly as the author declared them, including the schema version and the logical
    digest, and neither is re-derived from what happens to be current at read time.
    """

    harness = _harness(tmp_path)
    command = _change_set(harness)
    _applied(harness, command)
    stored = _view(harness, command.record_id)

    assert stored.baseline.logical_digest == "a" * 64
    assert stored.candidate.logical_digest == "b" * 64
    assert stored.baseline.schema_version == "ar-knowledge-sqlite/v8"
    assert stored.baseline.repository_id == harness.repository_id
    assert stored.candidate.repository_id == harness.repository_id

    # And the two sides must be two sides of *one* namespace, refused at construction otherwise: a
    # change set assembled from two namespaces is not a comparison any reader could interpret.
    with pytest.raises(ValueError):
        SemanticChangeSetPayload.model_validate(
            {
                "baseline": {
                    "repository_id": _FIXED_REPOSITORY_ID,
                    "schema_version": "ar-knowledge-sqlite/v8",
                    "logical_digest": "a" * 64,
                },
                "candidate": {
                    "repository_id": str(uuid4()),
                    "schema_version": "ar-knowledge-sqlite/v8",
                    "logical_digest": "b" * 64,
                },
            }
        )


def test_the_six_declared_parts_are_all_readable_from_one_change_set(tmp_path: Path) -> None:
    """4.1: every one of the six declared parts is readable from the composed record.

    Catches a composition that quietly dropped a part: the three parts the members declare are read as
    computed membership and the three the change set stores are read from its own payload, so a part
    with no home would show up here as an empty list.
    """

    harness = _harness(tmp_path)
    seeds, _ = _seeded_realization(harness)
    ((_, revision_id),) = harness.seed(1)
    change_set = _change_set(
        harness, requirement_refs=("KS-R07@v1",), realization_claim_ids=(seeds.claim_id,)
    )
    _applied(harness, change_set)
    effect = _effect_claim(harness, change_set.record_id, inputs=(revision_id,))
    preservation = _preservation(harness, change_set.record_id, ("invariant_revision", revision_id))
    question = _question(harness, change_set.record_id)
    _applied(harness, effect)
    _applied(harness, preservation)
    _applied(harness, question)

    stored = _view(harness, change_set.record_id)
    assert stored.baseline.logical_digest == "a" * 64
    assert stored.candidate.logical_digest == "b" * 64
    assert stored.requirement_revision_refs == ("KS-R07@v1",)
    assert stored.candidate_realization_claim_ids == (seeds.claim_id,)
    assert stored.members.effect_claim_ids == (effect.record_id,)
    assert stored.members.preservation_claim_ids == (preservation.record_id,)
    assert stored.members.unresolved_question_ids == (question.record_id,)


def test_membership_is_computed_from_the_members_own_declarations(tmp_path: Path) -> None:
    """4.3 and 4.6: which records belong to which change set is a membership fact code computes.

    Catches a member silently shared between two change sets: a claim is listed only under the change
    set it declares, and the other change set lists nothing at all.
    """

    harness = _harness(tmp_path)
    first = _change_set(harness)
    second = _change_set(harness)
    _applied(harness, first)
    _applied(harness, second)
    member = _effect_claim(harness, first.record_id)
    _applied(harness, member)

    assert _view(harness, first.record_id).members.effect_claim_ids == (member.record_id,)
    assert _view(harness, second.record_id).members.effect_claim_ids == ()
    assert _view(harness, second.record_id).members.preservation_claim_ids == ()


def test_a_change_set_may_hold_preservation_claims_and_no_effect_claims(tmp_path: Path) -> None:
    """3.2: that state is complete and valid, not a half-authored record.

    Catches a writer or reader that treated an effect claim as required: the change set is written,
    its preservation claim is a member, and its effect-claim list is legitimately empty.
    """

    harness = _harness(tmp_path)
    ((_, revision_id),) = harness.seed(1)
    change_set = _change_set(harness)
    _applied(harness, change_set)
    preservation = _preservation(harness, change_set.record_id, ("invariant_revision", revision_id))
    _applied(harness, preservation)

    stored = _view(harness, change_set.record_id)
    assert stored.members.preservation_claim_ids == (preservation.record_id,)
    assert stored.members.effect_claim_ids == ()

    # And a change set with no member at all is equally complete: nothing here reports an incomplete
    # change set, so the read serves an empty one rather than refusing it or filling it in.
    empty = _change_set(harness)
    _applied(harness, empty)
    served = _view(harness, empty.record_id)
    assert served.requirement_revision_refs == ()
    assert served.members.effect_claim_ids == ()


# ---------------------------------------------------------------------------
# 3.x -- preservation is a separate record, never an effect.


def test_no_preservation_claim_is_representable_in_the_effect_vocabulary() -> None:
    """3.2: no ``preserve`` member in the effect set and no preservation flag on a claim.

    Catches a tenth effect label or a flag: every preservation spelling is asserted absent from the
    closed set and refused by the effect payload's own shape.
    """

    for spelling in ("preserve", "preserved", "preservation", "keep", "no_change"):
        assert spelling not in ADMITTED_EFFECT_LABELS
        with pytest.raises(ValueError):
            InvariantEffectClaimPayload.model_validate(
                {
                    "change_set_id": str(uuid4()),
                    "effect": spelling,
                    "inputs": (),
                    "outputs": (),
                    "rationale": "a preservation claim is not an effect",
                }
            )
    for field in ("preserved", "preservation", "preservation_claim"):
        with pytest.raises(ValueError):
            InvariantEffectClaimPayload.model_validate(
                {
                    "change_set_id": str(uuid4()),
                    "effect": "clarify",
                    "inputs": (),
                    "outputs": (),
                    "rationale": "a flag is not a claim",
                    field: True,
                }
            )


def test_no_effect_claim_is_representable_as_a_preservation_claim() -> None:
    """3.2 and 3.1: the preservation payload's fields are its own declaration and nothing else.

    Catches a shared shape: a payload arriving with an ``effect`` field is refused rather than stored
    as a claim about a label.
    """

    assert set(PreservationClaimPayload.model_fields) == {
        "preservation_kind",
        "change_set_id",
        "subject",
        "statement",
    }
    with pytest.raises(ValueError):
        PreservationClaimPayload.model_validate(
            {
                "change_set_id": str(uuid4()),
                "subject": {"kind": "invariant", "reference_id": str(uuid4())},
                "statement": "the guard is unchanged",
                "effect": "clarify",
            }
        )


def test_an_effect_claim_and_a_preservation_claim_are_two_kinds_and_never_one_table() -> None:
    """3.2: no shared table whose rows could be read as either.

    Catches a single record kind covering both: the three member kinds resolve to three frozen models
    of three different types, and neither payload type is a subclass of the other.
    """

    assert set(MEMBER_PAYLOAD_MODELS) == {
        ("invariant_effect_claim", "invariant-effect-claim/v1"),
        ("preservation_claim", "preservation-claim/v1"),
        ("unresolved_question", "unresolved-question/v1"),
    }
    models = list(MEMBER_PAYLOAD_MODELS.values())
    assert len({model.__name__ for model in models}) == 3
    assert not issubclass(PreservationClaimPayload, InvariantEffectClaimPayload)
    assert not issubclass(InvariantEffectClaimPayload, PreservationClaimPayload)


def test_a_preservation_subject_is_a_named_reference_of_one_declared_kind() -> None:
    """3.1: the subject is an invariant identity or revision, an anchor, or a change set.

    Catches an effect label used as a subject: the declared kinds are exactly those four, and a
    subject naming another kind -- including an effect -- does not validate.
    """

    for kind in ("invariant", "invariant_revision", "source_anchor", "semantic_change_set"):
        assert PreservationSubject(kind=kind, reference_id=str(uuid4())).kind == kind
    with pytest.raises(ValueError):
        PreservationSubject(kind="effect", reference_id=str(uuid4()))


def test_a_preservation_subject_that_resolves_to_nothing_is_reported_unresolved(
    tmp_path: Path,
) -> None:
    """3.4: the same unresolved-reference state an effect claim's assessment references have.

    Catches a refusal or a re-pointing: the claim is stored, the subject is kept verbatim, and the
    read reports it as an unresolved reference held by that claim.
    """

    harness = _harness(tmp_path)
    change_set = _change_set(harness)
    _applied(harness, change_set)
    missing = str(uuid4())
    preservation = _preservation(harness, change_set.record_id, ("source_anchor", missing))
    _applied(harness, preservation)

    stored = [
        claim
        for claim in _scope(harness).preservation_claims
        if claim.record_id == preservation.record_id
    ]
    assert stored[0].subject.reference_id == missing
    reported = [r for r in stored[0].unresolved_references if r.field == "preservation_subject"]
    assert [r.reference for r in reported] == [missing]


def test_a_preservation_subject_that_resolves_is_not_reported_unresolved(tmp_path: Path) -> None:
    """3.4: the state is reported for a subject that resolves to nothing, and not for one that resolves.

    Catches a reader that reported every subject unresolved: the existence fact is asked of the table
    that would hold the named identity, so a stored revision is reported as resolved.
    """

    harness = _harness(tmp_path)
    ((_, revision_id),) = harness.seed(1)
    change_set = _change_set(harness)
    _applied(harness, change_set)
    preservation = _preservation(harness, change_set.record_id, ("invariant_revision", revision_id))
    _applied(harness, preservation)

    store = harness.open()
    try:
        assert subject_resolves(
            store, PreservationSubject(kind="invariant_revision", reference_id=revision_id)
        )
        assert not subject_resolves(
            store, PreservationSubject(kind="invariant_revision", reference_id=str(uuid4()))
        )
    finally:
        store.close()
    stored = [
        claim
        for claim in _scope(harness).preservation_claims
        if claim.record_id == preservation.record_id
    ]
    assert stored[0].unresolved_references == ()


# ---------------------------------------------------------------------------
# 4.8 -- supersession is a new record with a predecessor edge.


def test_a_successor_change_set_is_a_new_record_with_a_predecessor_edge(tmp_path: Path) -> None:
    """4.8: supersession is a new record naming its exact predecessor, and the predecessor survives.

    Catches an in-place revision and a mutable current pointer: the successor has its own identity and
    its own edge row, and the superseded change set is still readable with its own payload.
    """

    harness = _harness(tmp_path)
    first = _change_set(harness)
    _applied(harness, first)
    second = _change_set(harness, predecessors=(first.record_id,))
    _applied(harness, second)

    assert second.record_id != first.record_id
    assert _view(harness, second.record_id).predecessor_change_set_ids == (first.record_id,)
    assert _view(harness, first.record_id).predecessor_change_set_ids == ()
    assert _view(harness, first.record_id).baseline.logical_digest == "a" * 64


def test_the_predecessor_edge_is_written_inside_the_successors_own_creation_batch(
    tmp_path: Path,
) -> None:
    """4.8: the edge is inserted with its successor, and no standalone append operation exists.

    Catches a second write path: the command union carries no member that appends a predecessor edge
    to a stored change set, so the only way an edge exists is with the successor's own record.
    """

    harness = _harness(tmp_path)
    first = _change_set(harness)
    _applied(harness, first)
    second = _change_set(harness, predecessors=(first.record_id,))
    _applied(harness, second)

    store = harness.open()
    try:
        rows = tuple(
            store.connection.execute(
                "SELECT successor_change_set_id, predecessor_change_set_id FROM "
                "change_set_predecessor WHERE repository_id = ?",
                (harness.repository_id,),
            )
        )
    finally:
        store.close()
    assert rows == ((second.record_id, first.record_id),)
    kinds = {model.model_fields["kind"].default for model in get_args(EffectCommand)}
    assert not any("predecessor" in kind or "successor" in kind for kind in kinds)


def test_a_change_set_naming_itself_as_predecessor_is_refused_as_lineage_cycle(
    tmp_path: Path,
) -> None:
    """4.8 and the Failure table: the graph is one rule, so the one-node cycle is that rule's refusal.

    Catches a self-edge reported as a missing reference: the code is the shipped ``lineage_cycle``,
    the refusal names the change set on the cycle, and the transaction left no row behind.
    """

    harness = _harness(tmp_path)
    record_id = str(uuid4())
    command = _change_set(harness, predecessors=(record_id,), record_id=record_id)
    evidence = measure_refusal(harness, harness.batch(command))
    assert evidence.refusal.code == "lineage_cycle"
    assert record_id in (evidence.refusal.observed or "")
    assert evidence.wrote_nothing()


def test_the_succession_edge_table_is_sealed_against_update_and_delete(tmp_path: Path) -> None:
    """4.8: a predecessor change set is never rewritten by the arrival of its successor.

    Catches a rewritten lineage: the appended generation's triggers refuse an update and a delete of
    the edge row, so a stored succession cannot be edited by a repair path or a changeset.
    """

    harness = _harness(tmp_path)
    first = _change_set(harness)
    _applied(harness, first)
    second = _change_set(harness, predecessors=(first.record_id,))
    _applied(harness, second)

    store = harness.open()
    try:
        with pytest.raises(apsw.Error):
            store.connection.execute(
                "UPDATE change_set_predecessor SET predecessor_change_set_id = ? "
                "WHERE repository_id = ? AND successor_change_set_id = ?",
                (str(uuid4()), harness.repository_id, second.record_id),
            )
        with pytest.raises(apsw.Error):
            store.connection.execute(
                "DELETE FROM change_set_predecessor WHERE repository_id = ? "
                "AND successor_change_set_id = ?",
                (harness.repository_id, second.record_id),
            )
    finally:
        store.close()


def test_a_second_successor_of_one_predecessor_is_a_separate_record(tmp_path: Path) -> None:
    """4.8: two successors both naming one predecessor are two records, and neither edits the other.

    Catches a "current version" pointer: a forked succession is reported as two change sets each
    naming the same predecessor rather than as one winner.
    """

    harness = _harness(tmp_path)
    first = _change_set(harness)
    _applied(harness, first)
    left = _change_set(harness, predecessors=(first.record_id,))
    right = _change_set(harness, predecessors=(first.record_id,))
    _applied(harness, left)
    _applied(harness, right)

    assert _view(harness, left.record_id).predecessor_change_set_ids == (first.record_id,)
    assert _view(harness, right.record_id).predecessor_change_set_ids == (first.record_id,)
    assert len({view.record_id for view in _scope(harness).change_sets}) == 3


# ---------------------------------------------------------------------------
# The registry: these records join a registered generation.


def test_generation_eight_appends_one_table_to_the_generation_it_descends_from() -> None:
    """The registry contract: the authored-effect record group joins a registered generation.

    Catches a generation that retyped, reordered or dropped an earlier generation's table, and catches
    this leaf's table arriving without a declared key, trigger set or index: generation 8 is
    generation 7's declarations with one table appended, and every one of generation 7's thirty-three
    names keeps its columns and its primary key.

    RENUMBERED for the sync: this module was authored against generation 4 and declared generation 5,
    because that was the tip it was cut from. Three leaves landed generations 5, 6 and 7 first, so the
    generation this leaf's table joins is 8 and the base it descends from is 7 -- the case asserts
    against the generation it *actually* lands on, which is the property the case was always about
    rather than a fact about how many generations happened to exist when it was written.
    """

    assert GENERATION_8.user_version == 8
    assert GENERATION_8.schema_name == "ar-knowledge-sqlite/v8"
    assert GENERATION_8.tables[: len(GENERATION_7.tables)] == GENERATION_7.tables
    for table in GENERATION_7.tables:
        assert GENERATION_8.columns[table] == GENERATION_7.columns[table], table
        assert GENERATION_8.primary_keys[table] == GENERATION_7.primary_keys[table], table
    assert GENERATION_8.tables[len(GENERATION_7.tables) :] == ("change_set_predecessor",)
    assert GENERATION_8.primary_keys["change_set_predecessor"] == (
        "repository_id",
        "successor_change_set_id",
        "predecessor_change_set_id",
    )
    assert set(GENERATION_8.triggers) > set(GENERATION_7.triggers)
    assert set(GENERATION_8.index_ddl) > set(GENERATION_7.index_ddl)
    # RE-SCOPED when the census record group landed generation 9: this case's property is that the
    # authored-effect group's table still declares what it declared, and that property is now stated
    # against the generation that actually carries it rather than against the registry's tip. The
    # assertion that the table is *in* the registry is unchanged and is now stronger -- generation 9
    # descends from generation 8 with only its own tables appended, which this case checks directly.
    assert CURRENT_GENERATION.user_version > GENERATION_8.user_version
    assert "content_digest" not in GENERATION_8.columns["change_set_predecessor"]
    for generation in GENERATIONS:
        for statement in (*generation.index_ddl, *generation.triggers.values()):
            assert "ALTER TABLE" not in statement.upper()


def test_a_new_store_declares_the_generation_that_carries_this_leafs_table(tmp_path: Path) -> None:
    """The registry contract: the created generation is the newest registered one, named not assumed.

    Catches a build that created a dataset its own record group could not read: the created store
    declares the current generation and the authored-effect read serves it.
    """

    harness = _harness(tmp_path)
    store = harness.open()
    try:
        assert store.generation is CURRENT_GENERATION
    finally:
        store.close()
    assert _scope(harness).repository_id == harness.repository_id


# ---------------------------------------------------------------------------
# 5.x -- the requirement-revision reference under the opaque-reference clause.


def test_a_requirement_revision_reference_is_stored_verbatim_and_reported_unresolved(
    tmp_path: Path,
) -> None:
    """5.1, 5.2 and Example 7: the whole opaque-reference clause in observable form.

    Catches a reference resolved by reading the packet's prose, a reference parsed for a requirement
    identity, and a fabricated record created to satisfy one: both references are stored as written,
    both are reported as unresolved references with the change set that holds them, and the store still
    holds no requirement-revision record.
    """

    harness = _harness(tmp_path)
    command = _change_set(harness, requirement_refs=("KS-R07@v1", "req-obligation-5f2a"))
    _applied(harness, command)

    stored = _view(harness, command.record_id)
    assert stored.requirement_revision_refs == ("KS-R07@v1", "req-obligation-5f2a")
    reported = [r for r in stored.unresolved_references if r.field == "requirement_revision_refs"]
    assert [r.reference for r in reported] == ["KS-R07@v1", "req-obligation-5f2a"]
    assert {r.holder_record_id for r in reported} == {command.record_id}
    assert all(r.holder_revision_id == command.revision_id for r in reported)

    store = harness.open()
    try:
        rows = tuple(
            store.connection.execute(
                "SELECT count(*) FROM knowledge_record WHERE repository_id = ? "
                "AND kind = 'requirement_revision'",
                (harness.repository_id,),
            )
        )
    finally:
        store.close()
    assert rows[0][0] == 0


def test_a_requirement_revision_reference_is_never_parsed_or_canonicalised(tmp_path: Path) -> None:
    """5.2: the reference is stored verbatim, so two spellings are two stored values.

    Catches a canonicalising implementation: spellings a parser would fold into one another --
    different case, surplus spacing, a trailing separator -- round-trip byte-identically and stay
    distinct members of the declared set.
    """

    harness = _harness(tmp_path)
    spellings = ("KS-R07@V1", "KS-R07@v1", " ks-r07@v1 ", "KS-R07@v1:", "KS-R07")
    command = _change_set(harness, requirement_refs=spellings)
    _applied(harness, command)
    assert _view(harness, command.record_id).requirement_revision_refs == spellings


def test_a_requirement_revision_reference_beyond_the_shipped_bound_is_refused() -> None:
    """5.2: the reference is bounded, and the bound is the shipped reference length.

    Catches an unbounded stored reference: a payload carrying one longer than the shipped bound does
    not validate, so the bound is a property of the record rather than a habit of its authors.
    """

    payload = _construction_payload()
    with pytest.raises(ValueError):
        SemanticChangeSetPayload.model_validate(
            {**payload, "requirement_revision_refs": ("x" * (REFERENCE_MAX_LENGTH + 1),)}
        )
    validated = SemanticChangeSetPayload.model_validate(
        {**payload, "requirement_revision_refs": ("x" * REFERENCE_MAX_LENGTH,)}
    )
    assert validated.requirement_revision_refs == ("x" * REFERENCE_MAX_LENGTH,)


def test_a_reference_declared_twice_is_refused() -> None:
    """5.1 and 4.4: a repeated reference is one reference stored once.

    Catches a change set that stored the same requirement reference or the same realization claim
    twice, which would make two identical members look like two authored ones.
    """

    payload = _construction_payload()
    with pytest.raises(ValueError):
        SemanticChangeSetPayload.model_validate(
            {**payload, "requirement_revision_refs": ("KS-R07@v1", "KS-R07@v1")}
        )
    with pytest.raises(ValueError):
        SemanticChangeSetPayload.model_validate(
            {**payload, "candidate_realization_claim_ids": (str(uuid4()),) * 2}
        )


def test_a_candidate_realization_claim_that_resolves_to_nothing_is_refused_as_invalid_reference(
    tmp_path: Path,
) -> None:
    """4.4: the change set records shipped realization-claim rows by exact identity.

    Catches a reference satisfied by invention: an identity no stored claim carries is refused as
    ``invalid_reference`` naming it, and no second copy of a claim is authored to make it resolve.
    """

    harness = _harness(tmp_path)
    missing = str(uuid4())
    evidence = measure_refusal(
        harness, harness.batch(_change_set(harness, realization_claim_ids=(missing,)))
    )
    assert evidence.refusal.code == "invalid_reference"
    assert evidence.refusal.record_id == missing
    assert evidence.wrote_nothing()


def test_a_realization_claim_removed_after_the_change_set_is_reported_unresolved(
    tmp_path: Path,
) -> None:
    """4.4 and 4.9: the reference never resolves itself, so a row that stops being stored is reported.

    Catches a reader that silently dropped a reference whose row is gone: the change set still names
    the claim, the read reports the identity verbatim as unresolved, and nothing is substituted for it
    or re-pointed at a neighbouring claim.
    """

    harness = _harness(tmp_path)
    seeds, _ = _seeded_realization(harness)
    change_set = _change_set(harness, realization_claim_ids=(seeds.claim_id,))
    _applied(harness, change_set)

    store = harness.open()
    try:
        assert realization_claim_resolves(store, seeds.claim_id)
    finally:
        store.close()

    digest = harness.digest_of("realization_claim", seeds.claim_id)
    _applied(
        harness,
        RemoveRealizationClaim(claim_id=seeds.claim_id, expected_row_digest=digest),
    )

    stored = _view(harness, change_set.record_id)
    assert stored.candidate_realization_claim_ids == (seeds.claim_id,)
    reported = [
        r for r in stored.unresolved_references if r.field == "candidate_realization_claim_ids"
    ]
    assert [r.reference for r in reported] == [seeds.claim_id]


# ---------------------------------------------------------------------------
# 4.5, 4.6, 4.7, 5.4 -- what the record is not allowed to conclude or to store.


def test_an_open_question_is_reported_open_and_nothing_records_it_answered(tmp_path: Path) -> None:
    """4.5: a question is a member with its own identity, statement and author, and it stays open.

    Catches a nullable "answered" field, a default of answered and a dropped question: the payload
    declares exactly its declaration, the question is a member of its change set, and neither the
    record nor the read carries a field saying it was resolved.
    """

    harness = _harness(tmp_path)
    change_set = _change_set(harness)
    _applied(harness, change_set)
    question = _question(harness, change_set.record_id)
    _applied(harness, question)

    assert set(UnresolvedQuestionPayload.model_fields) == {
        "question_kind",
        "change_set_id",
        "statement",
    }
    stored = [
        item
        for item in _scope(harness).unresolved_questions
        if item.record_id == question.record_id
    ]
    assert stored[0].statement == "does the shared deadline also bind the export path?"
    assert stored[0].change_set_id == change_set.record_id
    assert stored[0].provenance == harness.authorship
    for name in ("answered", "resolved", "closed", "answer"):
        assert name not in UnresolvedQuestionPayload.model_fields
        assert name not in type(stored[0]).model_fields


def test_nothing_here_stores_task_status_seat_ownership_or_approval() -> None:
    """5.4 and Doc13:106: a change set is not a competing task authority.

    Catches a requirement or task authority arriving as a field: every probe is refused by the frozen
    payload's ordinary ``extra="forbid"`` rule, and no registered generation declares a column one
    could land in.
    """

    payload = _construction_payload()
    for field in TASK_AUTHORITY_FIELD_NAMES:
        with pytest.raises(ValueError):
            SemanticChangeSetPayload.model_validate({**payload, field: "x"})
    for generation in GENERATIONS:
        for table in ("knowledge_record", "change_set_predecessor"):
            assert not set(TASK_AUTHORITY_FIELD_NAMES) & set(generation.columns.get(table, ()))


def test_a_change_set_carries_no_verdict_and_no_generated_summary() -> None:
    """4.6 and 4.7: no generated summary, no verdict, no acceptance and no promotion.

    Catches an inference arriving as a field: every probe is refused, and the payload's own field set
    is exactly its declared composition.
    """

    payload = _construction_payload()
    for field in VERDICT_AND_SUMMARY_FIELD_NAMES:
        with pytest.raises(ValueError):
            SemanticChangeSetPayload.model_validate({**payload, field: "x"})
    assert set(SemanticChangeSetPayload.model_fields) == {
        "change_set_kind",
        "baseline",
        "candidate",
        "requirement_revision_refs",
        "candidate_realization_claim_ids",
    }


def test_the_change_set_payload_registers_under_one_kind_and_one_schema() -> None:
    """4.1: one kind, one frozen shape, and the pair this record group declares is the pair registered.

    Catches a second schema admitted for the kind, which would let two differently shaped change sets
    be stored as one record type.
    """

    assert set(CHANGE_SET_PAYLOAD_MODELS) == {
        (SEMANTIC_CHANGE_SET_KIND, SEMANTIC_CHANGE_SET_SCHEMA)
    }
    assert (
        CHANGE_SET_PAYLOAD_MODELS[(SEMANTIC_CHANGE_SET_KIND, SEMANTIC_CHANGE_SET_SCHEMA)]
        is SemanticChangeSetPayload
    )


def test_the_read_projection_reports_every_unresolved_reference_verbatim_with_its_holder(
    tmp_path: Path,
) -> None:
    """4.9: the chosen projection shape, measured.

    The read is the authored-effect scope (``read_effect_scope``); the fact is carried on the record
    that holds it as ``unresolved_references`` and repeated at the scope level under the same name;
    each entry names its field, its holder revision and the stored text.

    Catches a suppressed, substituted, resolved or re-pointed reference: the same references are
    reported from both planes, and the scope-level list is exactly the union of the per-record lists.
    """

    harness = _harness(tmp_path)
    change_set = _change_set(harness, requirement_refs=("KS-R09@v2",))
    _applied(harness, change_set)
    claim = _effect_claim(harness, change_set.record_id, assessment_refs=("assessment:later",))
    _applied(harness, claim)

    scope = _scope(harness)
    per_record = (
        *[r for view in scope.change_sets for r in view.unresolved_references],
        *[r for view in scope.effect_claims for r in view.unresolved_references],
        *[r for view in scope.preservation_claims for r in view.unresolved_references],
    )
    assert scope.unresolved_references == per_record
    assert {r.reference for r in scope.unresolved_references} == {"KS-R09@v2", "assessment:later"}
    assert all(r.detail for r in scope.unresolved_references)
    assert {r.field for r in scope.unresolved_references} == {
        "requirement_revision_refs",
        "assessment_refs",
    }


def test_the_read_is_derived_and_a_rebuild_reproduces_it_byte_for_byte(tmp_path: Path) -> None:
    """The Scope: every view is a pure function of the stored rows.

    Catches a stored view: two reads over unchanged rows are equal and dump to the same JSON, so
    deleting the projection changes nothing and there is no second place a value could live.
    """

    harness = _harness(tmp_path)
    change_set = _change_set(harness, requirement_refs=("KS-R13@v1",))
    _applied(harness, change_set)
    first = _scope(harness)
    second = _scope(harness)
    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.detail


def _construction_payload() -> dict[str, Any]:
    """Return one admissible change-set payload over two fixed snapshot identities."""

    return {
        "baseline": {
            "repository_id": _FIXED_REPOSITORY_ID,
            "schema_version": "ar-knowledge-sqlite/v8",
            "logical_digest": "a" * 64,
        },
        "candidate": {
            "repository_id": _FIXED_REPOSITORY_ID,
            "schema_version": "ar-knowledge-sqlite/v8",
            "logical_digest": "b" * 64,
        },
    }
