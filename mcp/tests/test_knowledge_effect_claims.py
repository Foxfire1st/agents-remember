"""``InvariantEffectClaim``: the authored effect, its closed vocabulary and its one code.

These cases protect ``KS-R13@v1``'s effect-claim record group at the envelope the substrate already
has -- which payload shape the kind admits, which declarations the one cardinality rule refuses and
with which code, which references resolve, what the stored row's authorship and lifecycle are, and
which fields are *absent* rather than merely unused. They occupy the ``unit-regression`` lane because
what they measure is a typed record's own construction and storage boundary, not a process, a
publication or a Git object. The change-set half of the record group is
``test_knowledge_change_sets.py``.

Each case is named for the property its own assertions measure and says which failure it catches: a
synonym admitted into the vocabulary, a cardinality contradiction reported under a second code, an
author read out of the payload instead of the admission, an unresolved assessment reference turned
into a refusal, a truth verdict that found a column, and an incorrect authored effect that code
somehow corrected.

The forbidden field sets are asserted as an **absence** at both planes, because that is the
requirement's own enforcement style: the payload model refuses an undeclared field through its
ordinary ``extra="forbid"`` rule -- there is no named denylist to relax -- and no column of any
registered generation carries one of the names. A case that asserted only the validator half would
leave the schema free to hold the value later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

import apsw
import pytest
from agents_remember.memory.knowledge.effect_records import EFFECT_RECORD_KINDS
from agents_remember.memory.knowledge.effects import (
    READ_OPERATION,
    REQUIRED_EFFECT_GENERATION,
    read_effect_scope,
)
from agents_remember.memory.knowledge.record_envelope import (
    PAYLOAD_MODELS,
    REQUIREMENT_RECORD_KINDS,
    validate_record_payload,
)
from agents_remember.memory.knowledge.requirements import record_requirement_revision
from agents_remember.memory.knowledge.schema import CANONICAL_TABLES
from agents_remember.memory.knowledge.schema_generations import GENERATION_4, GENERATIONS
from agents_remember.models.knowledge.candidate import (
    AddInvariantEffectClaim,
    AddSemanticChangeSet,
    EffectCommand,
    ProposedCommand,
)
from agents_remember.models.knowledge.change_set import (
    SEMANTIC_CHANGE_SET_KIND,
    SEMANTIC_CHANGE_SET_SCHEMA,
)
from agents_remember.models.knowledge.diff import (
    KNOWLEDGE_DIFF_FIELD_NAMES,
    KnowledgeDiffItem,
    KnowledgeDiffResult,
    KnowledgeDiffSourceChange,
)
from agents_remember.models.knowledge.effect import (
    ADMITTED_EFFECT_LABELS,
    DIVISION_EFFECT_LABELS,
    INVARIANT_EFFECT_CLAIM_KIND,
    INVARIANT_EFFECT_CLAIM_SCHEMA,
    EffectLabel,
    InvariantEffectClaimPayload,
    cardinality_violation,
)
from agents_remember.models.knowledge.requirement import (
    REQUIREMENT_REVISION_KIND,
    REQUIREMENT_REVISION_SCHEMA,
    RequirementOwnerRef,
    RequirementOwnerResolution,
    RequirementRevisionPayload,
    RequirementRevisionRequest,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal
from candidate_batch_test_support import CandidateHarness, build_candidate_harness, measure_refusal
from generation_test_support import create_recorded_generation_store

# The seven labels that claim neither division nor union, in the packet's own words. Derived here by
# subtracting the two division labels from the admitted set rather than restated, so the tuple cannot
# drift from the vocabulary the payload model validates against.
NON_DIVISION_LABELS: tuple[str, ...] = tuple(
    label for label in ADMITTED_EFFECT_LABELS if label not in DIVISION_EFFECT_LABELS
)

# The names that would make a stored claim a verdict rather than a claim. They are *probes*: each case
# asserts one is refused, so the case cannot pass by there being nothing to probe.
VERDICT_FIELD_NAMES: tuple[str, ...] = (
    "verified",
    "is_true",
    "confidence",
    "score",
    "severity",
    "verdict",
    "truth",
    "strength",
)

# The names that would make this record group its own identity authority, or its own narrative. The
# requirement forbids a content address, a logical digest and a schema fingerprint on these records,
# and forbids a generated summary or narrative outright.
IDENTITY_FIELD_NAMES: tuple[str, ...] = (
    "content_address",
    "content_digest",
    "logical_digest",
    "fingerprint",
    "schema_fingerprint",
    "summary",
    "narrative",
    "generated_summary",
)

# The words that would mean an effect leaked into the mechanical comparison. R08's result keeps its
# documented semantic silence, so this leaf adds a record *beside* the comparison and never a field on
# it.
MEANING_WORDS: tuple[str, ...] = (
    "effect",
    "strengthen",
    "weaken",
    "preservation",
    "preserved",
    "severity",
    "harmless",
    "neutrality",
    "verdict",
)


def _harness(tmp_path: Path) -> CandidateHarness:
    """Return one admitted candidate with one seeded invariant revision."""

    return build_candidate_harness(tmp_path)


def _change_set(harness: CandidateHarness) -> str:
    """Author one change set through the batch and return its record identity."""

    record_id = str(uuid4())
    result = harness.apply(
        harness.batch(
            AddSemanticChangeSet(
                record_id=record_id,
                revision_id=str(uuid4()),
                payload=_change_set_payload(harness),
            )
        )
    )
    if result.state != "changed":
        raise AssertionError(f"the change set this case needs was refused: {result.refusal!r}")
    return record_id


def _change_set_payload(
    harness: CandidateHarness, *, candidate_digest: str = "b"
) -> dict[str, Any]:
    """Return one admissible change-set payload over two exact snapshot identities."""

    return {
        "baseline": {
            "repository_id": harness.repository_id,
            "schema_version": "ar-knowledge-sqlite/v8",
            "logical_digest": "a" * 64,
        },
        "candidate": {
            "repository_id": harness.repository_id,
            "schema_version": "ar-knowledge-sqlite/v8",
            "logical_digest": candidate_digest * 64,
        },
    }


def _claim_payload(**overrides: Any) -> dict[str, Any]:
    """Return one admissible effect-claim payload, with named fields replaced."""

    payload: dict[str, Any] = {
        "change_set_id": str(uuid4()),
        "effect": "clarify",
        "inputs": (),
        "outputs": (),
        "rationale": "Only the wording moved; the obligation the statement carries is unchanged.",
        "assessment_refs": (),
    }
    payload.update(overrides)
    return payload


def _claim_command(
    change_set_id: str, revision_id: str | None = None, **overrides: Any
) -> AddInvariantEffectClaim:
    """Build one effect-claim command over one change set, with a fresh identity pair."""

    return AddInvariantEffectClaim(
        record_id=str(uuid4()),
        revision_id=revision_id or str(uuid4()),
        payload=_claim_payload(change_set_id=change_set_id, **overrides),
    )


def _applied(harness: CandidateHarness, command: EffectCommand) -> Any:
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


# ---------------------------------------------------------------------------
# 1.1 -- the closed vocabulary.


def test_the_closed_vocabulary_is_exactly_the_nine_labels_doc13_names() -> None:
    """1.1: the effect set is closed, spelled once, and in the document's own order.

    Catches a synonym, a compound label, a free-text label and a locally added tenth member in one
    assertion: the literal type the payload validates against *is* the declared tuple, so a label the
    tuple does not carry cannot be admitted and a label it carries cannot be refused.
    """

    assert ADMITTED_EFFECT_LABELS == (
        "restore",
        "clarify",
        "introduce",
        "strengthen",
        "weaken",
        "replace",
        "split",
        "merge",
        "retire",
    )
    assert get_args(EffectLabel) == ADMITTED_EFFECT_LABELS
    assert frozenset({"split", "merge"}) == DIVISION_EFFECT_LABELS


def test_an_out_of_vocabulary_label_names_the_observed_label_and_all_nine_admitted_values(
    tmp_path: Path,
) -> None:
    """1.1 and the Failure table, measured on a real dataset.

    Catches a nearest-match correction and a silent default: the refusal carries the observed label
    beside all nine admitted ones, the code is the shipped ``invalid_payload``, and the dataset's own
    logical identity and every table count are unchanged.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    evidence = measure_refusal(
        harness, harness.batch(_claim_command(change_set_id, effect="preserve"))
    )

    assert evidence.refusal.code == "invalid_payload"
    assert evidence.refusal.observed == "preserve"
    expected = evidence.refusal.expected
    assert expected is not None, "the refusal names the admitted labels beside the observed one"
    assert expected.split(" | ") == list(ADMITTED_EFFECT_LABELS)
    assert evidence.wrote_nothing()


def test_every_near_miss_spelling_is_refused_rather_than_corrected(tmp_path: Path) -> None:
    """1.1: case, separator, compound and typo spellings are all outside the set, none repaired.

    Catches a canonicalising implementation: the vocabulary is matched literally, so ``PRESERVE`` and
    a hyphenated compound are refused exactly as a typo is. Each spelling is asserted and the failure
    names the one that was wrongly admitted.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    for label in ("PRESERVE", "strengthen-weaken", "restore,clarify", "strenghten", "clarify "):
        evidence = measure_refusal(
            harness, harness.batch(_claim_command(change_set_id, effect=label))
        )
        assert evidence.refusal.code == "invalid_payload", label
        assert evidence.refusal.observed == label, label
        assert evidence.wrote_nothing(), label


def test_the_payload_model_refuses_a_synonym_at_construction(tmp_path: Path) -> None:
    """2.4: the shape check is applied at construction as well as at the storage boundary.

    Catches a widening that only the write path enforces: a payload built directly, without any
    operation, is refused by the frozen model itself.
    """

    with pytest.raises(ValueError):
        InvariantEffectClaimPayload.model_validate(
            _claim_payload(effect="preserve", change_set_id=str(uuid4()))
        )


# ---------------------------------------------------------------------------
# 1.2 -- the one cardinality rule, and the one refusal code.


def test_the_one_cardinality_rule_admits_exactly_the_four_readings_example_six_names() -> None:
    """1.2 and Example 6: one reading predicts all four cases.

    Catches a rule that admits every mutually consistent pair: ``split`` with one output would then
    pass a check that says nothing about the one thing the author did state about counts.
    """

    assert cardinality_violation("weaken", ("rev-a",), ("rev-b",)) is None
    assert cardinality_violation("split", ("rev-a",), ("rev-b", "rev-c")) is None
    assert cardinality_violation("merge", ("rev-a", "rev-b"), ("rev-c",)) is None
    assert cardinality_violation("split", ("rev-a",), ("rev-b",)) is not None


def test_a_split_with_one_output_is_refused_as_invalid_payload_naming_label_and_counts(
    tmp_path: Path,
) -> None:
    """1.2 and Example 6: the first refusal carries the declared label and the observed counts."""

    harness = _harness(tmp_path)
    ((_, revision_id),) = harness.seed(1)
    change_set_id = _change_set(harness)
    evidence = measure_refusal(
        harness,
        harness.batch(
            _claim_command(
                change_set_id, effect="split", inputs=(revision_id,), outputs=(revision_id,)
            )
        ),
    )

    assert evidence.refusal.code == "invalid_payload"
    observed = evidence.refusal.observed
    expected = evidence.refusal.expected
    assert observed is not None and expected is not None
    assert observed.startswith("effect='split' inputs=1 outputs=1")
    assert "two or more outputs" in expected
    assert evidence.wrote_nothing()

    # The union side of the same rule, for the same reason: ``merge`` is admitted only with two or
    # more inputs, so a declaration of one input is refused under the same code.
    merged = measure_refusal(
        harness,
        harness.batch(
            _claim_command(
                change_set_id, effect="merge", inputs=(revision_id,), outputs=(revision_id,)
            )
        ),
    )
    assert merged.refusal.code == "invalid_payload"
    merged_observed = merged.refusal.observed
    assert merged_observed is not None
    assert merged_observed.startswith("effect='merge' inputs=1 outputs=1")


def test_one_revision_on_both_sides_is_refused_and_the_shared_reference_is_named(
    tmp_path: Path,
) -> None:
    """1.2 and Example 6: an effect that names one revision on both sides claims no change.

    Catches a rule that reads only counts: ``strengthen`` with one input and one output is admitted
    *only because* the two sides differ, so the same-revision case must be refused even though its
    counts are inside the admitted combination.
    """

    harness = _harness(tmp_path)
    ((_, revision_id),) = harness.seed(1)
    change_set_id = _change_set(harness)
    evidence = measure_refusal(
        harness,
        harness.batch(
            _claim_command(
                change_set_id,
                effect="strengthen",
                inputs=(revision_id,),
                outputs=(revision_id,),
            )
        ),
    )

    assert evidence.refusal.code == "invalid_payload"
    assert revision_id in (evidence.refusal.observed or "")
    assert "the same revision" in evidence.refusal.detail
    assert evidence.wrote_nothing()


def test_a_cardinality_contradiction_is_never_reported_as_invalid_reference(
    tmp_path: Path,
) -> None:
    """2.1: one code for every inadmissible declared payload; ``invalid_reference`` is reserved.

    Catches the exact instability ``CR13-2`` was a blocking finding about: a declaration that
    contradicts itself must not be reported as a name that resolves to nothing.
    """

    harness = _harness(tmp_path)
    ((_, revision_id),) = harness.seed(1)
    change_set_id = _change_set(harness)
    for command in (
        _claim_command(
            change_set_id, effect="split", inputs=(revision_id,), outputs=(revision_id,)
        ),
        _claim_command(change_set_id, effect="merge", inputs=(), outputs=(revision_id,)),
        _claim_command(
            change_set_id, effect="weaken", inputs=(revision_id,), outputs=(revision_id,)
        ),
    ):
        evidence = measure_refusal(harness, harness.batch(command))
        assert evidence.refusal.code == "invalid_payload"


def test_every_non_division_label_admits_any_counts_when_the_two_sides_differ(
    tmp_path: Path,
) -> None:
    """1.2: the seven labels that claim no division and no union admit any counts.

    Catches a predicate stricter than the requirement states -- one that, for instance, demanded
    exactly one input and one output for these labels, which would refuse an authored ``retire`` of
    three revisions. Every one of the seven is asserted, and the failure names the label that was
    wrongly refused.
    """

    harness = _harness(tmp_path)
    seeds = harness.seed(3)
    inputs = (seeds[0][1], seeds[1][1])
    outputs = (seeds[2][1],)
    for label in NON_DIVISION_LABELS:
        change_set_id = _change_set(harness)
        _applied(
            harness, _claim_command(change_set_id, effect=label, inputs=inputs, outputs=outputs)
        )
        stored = [claim for claim in _scope(harness).effect_claims if claim.effect == label]
        assert len(stored) == 1, label
        assert stored[0].inputs == inputs, label
        assert stored[0].outputs == outputs, label

    # "Any counts" includes none: a claim that names no revision on either side is admissible too, so
    # the predicate is not quietly requiring a nonempty reference set.
    zero = _change_set(harness)
    _applied(harness, _claim_command(zero, effect="introduce", inputs=(), outputs=()))
    assert "introduce" in {claim.effect for claim in _scope(harness).effect_claims}


def test_an_admissible_claim_with_no_references_at_all_is_stored(tmp_path: Path) -> None:
    """1.2: "any counts" includes none, so an authored claim that names no revision is admissible.

    Catches a rule that silently required a nonempty reference set -- a requirement the packet does
    not state, and one that would make a claim about a comparison with no recorded revisions
    unrepresentable.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    _applied(harness, _claim_command(change_set_id, inputs=(), outputs=()))
    assert [claim.effect for claim in _scope(harness).effect_claims] == ["clarify"]


# ---------------------------------------------------------------------------
# 1.3, 1.4, 2.4 -- the author, the references and the shape boundary.


def test_the_author_is_the_admitted_envelope_and_a_payload_cannot_supply_one(
    tmp_path: Path,
) -> None:
    """1.3: the author is the shipped ``Authorship`` envelope, stamped from the admission.

    Catches a payload-authored provenance: a claim arriving with its own ``actor_ref`` is refused by
    the frozen shape, and the stored revision's provenance is the admission's own envelope.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    evidence = measure_refusal(
        harness,
        harness.batch(
            _claim_command(
                change_set_id,
                actor_ref="agent:i-claim-this-myself",
                recorded_at="2026-01-01T00:00:00Z",
            )
        ),
    )
    assert evidence.refusal.code == "invalid_payload"
    assert evidence.wrote_nothing()

    command = _claim_command(change_set_id)
    _applied(harness, command)
    stored = [c for c in _scope(harness).effect_claims if c.record_id == command.record_id]
    assert stored[0].provenance == harness.authorship


def test_a_rationale_that_says_nothing_is_refused_at_both_planes(tmp_path: Path) -> None:
    """2.1 and 2.4: the rationale is nonempty, checked at construction and at the write boundary."""

    with pytest.raises(ValueError):
        InvariantEffectClaimPayload.model_validate(_claim_payload(rationale="   "))

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    evidence = measure_refusal(
        harness, harness.batch(_claim_command(change_set_id, rationale="\t\n "))
    )
    assert evidence.refusal.code == "invalid_payload"
    assert evidence.wrote_nothing()


def test_an_input_that_resolves_to_no_stored_revision_is_refused_as_invalid_reference(
    tmp_path: Path,
) -> None:
    """2.1 and the Failure table: an unresolvable input is refused with the reference named.

    Catches a re-pointing implementation: the refusal names the offending identity and the reference
    is not silently dropped to make the record valid.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    missing = str(uuid4())
    evidence = measure_refusal(
        harness, harness.batch(_claim_command(change_set_id, inputs=(missing,)))
    )

    assert evidence.refusal.code == "invalid_reference"
    assert evidence.refusal.record_id == missing
    assert evidence.wrote_nothing()

    # The output side reaches the same check rather than a second one beside it.
    other = str(uuid4())
    output_evidence = measure_refusal(
        harness, harness.batch(_claim_command(change_set_id, outputs=(other,)))
    )
    assert output_evidence.refusal.code == "invalid_reference"
    assert output_evidence.refusal.record_id == other


def test_a_claim_naming_a_change_set_that_is_not_stored_is_refused_as_invalid_reference(
    tmp_path: Path,
) -> None:
    """4.3: a member declares the one change set it belongs to, and that reference resolves.

    Catches a member stored without its group: a claim whose change set is not a stored
    ``semantic_change_set`` record is a dangling reference rather than a claim outside every change
    set.
    """

    harness = _harness(tmp_path)
    evidence = measure_refusal(harness, harness.batch(_claim_command(str(uuid4()))))
    assert evidence.refusal.code == "invalid_reference"
    assert evidence.wrote_nothing()


def test_a_claim_authored_before_its_change_set_in_one_batch_is_refused_with_its_remedy(
    tmp_path: Path,
) -> None:
    """1.5 and the batch contract: commands are applied in order, and a citation arriving first says so.

    Catches a dangling reference reported as an unnamed constraint failure: the batch refuses with
    ``invalid_reference`` naming the identity, because the change set it cites is declared by a later
    command of the same batch and has not been written yet when the claim is applied.
    """

    harness = _harness(tmp_path)
    change_set_id = str(uuid4())
    claim = _claim_command(change_set_id)
    change_set = AddSemanticChangeSet(
        record_id=change_set_id,
        revision_id=str(uuid4()),
        payload=_change_set_payload(harness),
    )
    evidence = measure_refusal(harness, harness.batch(claim, change_set))
    assert evidence.refusal.code == "invalid_reference"
    assert evidence.refusal.record_id == change_set_id
    assert evidence.wrote_nothing()


# ---------------------------------------------------------------------------
# 1.5, 2.2, 2.3, 2.5 -- what the record is, and what it is not.


def test_the_candidate_batch_union_carries_the_four_authored_effect_commands_and_nothing_else() -> (
    None
):
    """1.5: the record is written through the one batch path, whose union is closed over typed acts.

    Catches a derived-label path arriving as a new union member: the four commands this record group
    adds are exactly the ones that write it, and no member of the union accepts a computed label, a
    pre-decided verdict or free-form text.
    """

    union_members = set(get_args(get_args(ProposedCommand)[0]))
    assert union_members >= set(get_args(EffectCommand))
    assert len(set(get_args(EffectCommand))) == 4
    kinds = {model.model_fields["kind"].default for model in get_args(EffectCommand)}
    assert kinds == {
        "add_invariant_effect_claim",
        "add_preservation_claim",
        "add_unresolved_question",
        "add_semantic_change_set",
    }


def test_the_envelope_registry_resolves_every_effect_kind_to_its_own_frozen_model() -> None:
    """1.5 and 2.4: one registry entry per kind, and the kind sets do not overlap.

    Catches a kind registered against another group's shape, which would let a claim be written and
    read back as something else.
    """

    for kind in EFFECT_RECORD_KINDS:
        schemas = {schema for (registered, schema) in PAYLOAD_MODELS if registered == kind}
        assert len(schemas) == 1, kind
    assert EFFECT_RECORD_KINDS == (
        INVARIANT_EFFECT_CLAIM_KIND,
        "preservation_claim",
        "unresolved_question",
        SEMANTIC_CHANGE_SET_KIND,
    )
    assert not set(EFFECT_RECORD_KINDS) & REQUIREMENT_RECORD_KINDS
    assert PAYLOAD_MODELS[(INVARIANT_EFFECT_CLAIM_KIND, INVARIANT_EFFECT_CLAIM_SCHEMA)] is (
        InvariantEffectClaimPayload
    )
    assert PAYLOAD_MODELS[(REQUIREMENT_REVISION_KIND, REQUIREMENT_REVISION_SCHEMA)] is (
        RequirementRevisionPayload
    )
    assert (
        PAYLOAD_MODELS[(SEMANTIC_CHANGE_SET_KIND, SEMANTIC_CHANGE_SET_SCHEMA)].__name__
        == "SemanticChangeSetPayload"
    )


def test_no_field_can_hold_a_truth_verdict_about_the_label() -> None:
    """2.3: "the label is true" is not representable as a stored field.

    Catches a verdict arriving as a payload field. Every probe is asserted, so the case cannot pass by
    there being nothing to probe, and the failure names the probe that was admitted: ``extra="forbid"``
    refuses each one as the shipped ``invalid_payload`` rather than dropping it silently.
    """

    for field in VERDICT_FIELD_NAMES:
        validated = validate_record_payload(
            INVARIANT_EFFECT_CLAIM_KIND,
            INVARIANT_EFFECT_CLAIM_SCHEMA,
            _claim_payload(**{field: True}),
        )
        assert isinstance(validated, KnowledgeRefusal), field
        assert validated.code == "invalid_payload", field


def test_no_field_can_hold_an_identity_of_this_record_groups_own() -> None:
    """The Preservation Boundaries: no content address, no logical digest, no fingerprint, no summary.

    Catches this record group minting a second identity authority, or a generated narrative arriving
    as a field. Every probe is asserted and the refusal is named per probe, so a single admitted name
    is reported as the name it is rather than as a general failure.
    """

    for field in IDENTITY_FIELD_NAMES:
        validated = validate_record_payload(
            INVARIANT_EFFECT_CLAIM_KIND,
            INVARIANT_EFFECT_CLAIM_SCHEMA,
            _claim_payload(**{field: "x" * 64}),
        )
        assert isinstance(validated, KnowledgeRefusal), field
        assert validated.code == "invalid_payload", field


def test_no_registered_generation_carries_a_verdict_identity_or_summary_column() -> None:
    """2.3 and the Preservation Boundaries, at the row plane rather than the value plane.

    Catches a column added later that the payload model cannot see. Two facts are asserted, and they
    are different ones: no registered generation declares a column any *verdict*, *severity* or
    *summary* probe name could land in -- on any table of this record group -- and the one table this
    leaf appends carries no content address, logical digest or fingerprint column either.

    ``record_revision.content_digest`` is deliberately **not** counted as a forbidden column, and the
    exception is the requirement's own: the sealed revision's content digest is the shipped envelope's,
    where the design already puts it, and what the packet forbids is these records carrying an identity
    *of their own*. The payload models are where "of their own" is enforced, and the cases above assert
    that absence directly.
    """

    verdict_and_summary = set(VERDICT_FIELD_NAMES) | {
        "summary",
        "narrative",
        "generated_summary",
        "severity",
        "verdict",
    }
    record_group_tables = ("knowledge_record", "record_revision", "change_set_predecessor")
    for generation in GENERATIONS:
        for table in record_group_tables:
            columns = set(generation.columns.get(table, ()))
            assert not verdict_and_summary & columns, (generation.user_version, table)
    own_identity = {"content_address", "logical_digest", "fingerprint", "schema_fingerprint"}
    for generation in GENERATIONS:
        assert not own_identity & set(generation.columns.get("change_set_predecessor", ()))
    assert set(record_group_tables) <= (
        set(CANONICAL_TABLES) | set(GENERATION_4.tables) | {"change_set_predecessor"}
    )
    assert "content_digest" in GENERATION_4.columns["record_revision"]


def test_an_incorrect_authored_effect_is_stored_exactly_as_authored(tmp_path: Path) -> None:
    """Example 5 and the worst case: code refuses, corrects and flags nothing.

    An author records ``weaken`` on a revision whose statement grew by two paragraphs. The shape is
    valid, so the record is stored as authored; nothing in the read reports the growth or flags the
    disagreement, and no field of the claim is derived from the comparison. The failure this catches
    is the one the whole requirement exists to prevent: a store that quietly disagreed with its author.
    """

    harness = _harness(tmp_path)
    ((_, revision_id),) = harness.seed(1)
    change_set_id = _change_set(harness)
    command = _claim_command(
        change_set_id,
        effect="weaken",
        inputs=(revision_id,),
        rationale="The two added paragraphs narrow the exception, so the obligation binds less.",
    )
    _applied(harness, command)
    stored = [c for c in _scope(harness).effect_claims if c.record_id == command.record_id]

    assert len(stored) == 1
    assert stored[0].effect == "weaken"
    assert stored[0].unresolved_references == ()
    assert stored[0].lifecycle == "proposed"


def test_the_stored_lifecycle_is_the_shipped_vocabulary_and_never_a_third_state(
    tmp_path: Path,
) -> None:
    """2.5: the projection's status is the shipped ``proposed|accepted`` lifecycle, not a new one.

    Catches a third authored status: every row this leaf writes is surfaced as ``proposed``, read
    through the envelope's own lifecycle column, and no code path sets ``accepted``.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    _applied(harness, _claim_command(change_set_id))
    scope = _scope(harness)
    assert {claim.lifecycle for claim in scope.effect_claims} == {"proposed"}
    assert {change_set.lifecycle for change_set in scope.change_sets} == {"proposed"}


def test_a_command_declaring_accepted_origin_data_is_refused_as_promotion_not_supported(
    tmp_path: Path,
) -> None:
    """2.5: authoring a proposal is not a promotion path, and the refusal says so by name.

    The command *can* declare accepted origin data -- that is what makes the opposite property
    checkable -- and the batch refuses it rather than storing an acceptance nobody made.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    command = AddInvariantEffectClaim(
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload=_claim_payload(change_set_id=change_set_id),
        state_at_origin="accepted",
        acceptance_ref="approval:somebody-else",
    )
    evidence = measure_refusal(harness, harness.batch(command))
    assert evidence.refusal.code == "promotion_not_supported"
    assert evidence.wrote_nothing()


def test_a_stored_claim_revision_cannot_be_rewritten_or_deleted(tmp_path: Path) -> None:
    """1.5 and 2.1: the row is immutable once written.

    Catches an in-place rewrite: the shipped envelope triggers refuse an update of the sealed payload
    and a delete of the revision, and the claim's own record row cannot be rebound to another kind or
    schema.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    command = _claim_command(change_set_id)
    _applied(harness, command)
    store = harness.open()
    try:
        with pytest.raises(apsw.Error):
            store.connection.execute(
                "UPDATE record_revision SET payload = '{}' WHERE repository_id = ? "
                "AND revision_id = ?",
                (harness.repository_id, command.revision_id),
            )
        with pytest.raises(apsw.Error):
            store.connection.execute(
                "DELETE FROM record_revision WHERE repository_id = ? AND revision_id = ?",
                (harness.repository_id, command.revision_id),
            )
        with pytest.raises(apsw.Error):
            store.connection.execute(
                "UPDATE knowledge_record SET kind = 'decision' WHERE repository_id = ? "
                "AND record_id = ?",
                (harness.repository_id, command.record_id),
            )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 1.4 -- the unresolved assessment reference, and the duplicate/disagreement pair.


def test_an_assessment_reference_that_resolves_to_nothing_is_stored_and_reported_unresolved(
    tmp_path: Path,
) -> None:
    """1.4 and the Failure table: an unresolved assessment reference is a state, never a refusal.

    Catches a write that refused the reference, a placeholder substituted for it, and a read that
    presented it as an assessment that happened: the write succeeds, the read reports the stored text
    verbatim together with the claim that holds it, and the claim's own ``assessment_refs`` are
    unchanged.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    command = _claim_command(change_set_id, assessment_refs=("assessment:curator-42",))
    _applied(harness, command)
    scope = _scope(harness)
    stored = [c for c in scope.effect_claims if c.record_id == command.record_id]

    assert stored[0].assessment_refs == ("assessment:curator-42",)
    reported = [
        reference
        for reference in stored[0].unresolved_references
        if reference.field == "assessment_refs"
    ]
    assert [reference.reference for reference in reported] == ["assessment:curator-42"]
    assert reported[0].holder_record_id == command.record_id
    assert reported[0].holder_revision_id == command.revision_id


def test_a_duplicate_identical_claim_is_refused_as_duplicate_identity(tmp_path: Path) -> None:
    """The Failure table: a second claim declaring exactly what a stored one declares is a duplicate.

    Catches a duplicate stored twice: the refusal names the stored claim it duplicates, the code is
    the shipped ``duplicate_identity``, and the dataset is unchanged.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    _applied(harness, _claim_command(change_set_id, effect="clarify", inputs=(), outputs=()))
    evidence = measure_refusal(
        harness,
        harness.batch(_claim_command(change_set_id, effect="clarify", inputs=(), outputs=())),
    )

    assert evidence.refusal.code == "duplicate_identity"
    assert evidence.wrote_nothing()


def test_two_differently_labelled_claims_for_one_comparison_are_both_stored(tmp_path: Path) -> None:
    """Example 9: an authored disagreement is not a conflict and is not resolved.

    Catches a resolution field, a winning label and an automatic conflict: both claims are stored,
    both are readable, and neither is promoted over the other.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    first = _claim_command(change_set_id, effect="clarify", rationale="The wording moved.")
    second = _claim_command(
        change_set_id, effect="strengthen", rationale="The added deadline binds harder."
    )
    _applied(harness, first)
    _applied(harness, second)

    stored = {claim.effect for claim in _scope(harness).effect_claims}
    assert stored == {"clarify", "strengthen"}


def test_a_second_claim_with_the_same_label_but_other_references_is_not_a_duplicate(
    tmp_path: Path,
) -> None:
    """The Failure table: only an identical *declaration* is a duplicate.

    Catches an over-broad duplicate rule keyed on the label alone, which would refuse two authors
    describing two different revision moves with the same word.
    """

    harness = _harness(tmp_path)
    seeds = harness.seed(2)
    change_set_id = _change_set(harness)
    _applied(harness, _claim_command(change_set_id, effect="clarify", inputs=(seeds[0][1],)))
    _applied(harness, _claim_command(change_set_id, effect="clarify", inputs=(seeds[1][1],)))
    assert len(_scope(harness).effect_claims) == 2


# ---------------------------------------------------------------------------
# The read, and the comparison's documented silence.


def test_the_authored_effect_read_serves_a_derived_scope_and_reproduces_byte_for_byte(
    tmp_path: Path,
) -> None:
    """The Scope: the record group's read returns authorship, lifecycle and unresolved references.

    Catches a stored view: reading twice over unchanged rows reproduces the scope exactly, so there
    is no second place a value could live.
    """

    harness = _harness(tmp_path)
    change_set_id = _change_set(harness)
    command = _claim_command(change_set_id, assessment_refs=("assessment:none-yet",))
    _applied(harness, command)

    first = _scope(harness)
    second = _scope(harness)
    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.effect_claims[0].provenance == harness.authorship
    assert READ_OPERATION == "read_effect_scope"


def test_the_record_group_is_registered_by_generation_eight_and_refuses_an_earlier_dataset(
    tmp_path: Path,
) -> None:
    """The Scope and R10's registry contract: these records join a registered generation.

    Catches a silent widening: against a genuine generation-4 dataset the read refuses as
    ``unsupported_schema`` with both numbers as facts, and nothing is migrated, repaired or written
    through.

    RENUMBERED for the sync: this module was authored against generation 4 and declared generation 5.
    Three leaves landed generations 5, 6 and 7 first, so the generation whose registry carries this
    record group's succession table is 8. The predating dataset this case uses stays generation 4 --
    it is the *oldest* generation that predates these tables, and the refusal must name the required
    generation as a fact read from the group's own constant rather than from a literal, which is why
    the assertion below compares against ``REQUIRED_EFFECT_GENERATION``.
    """

    store = create_recorded_generation_store(
        tmp_path / "generation-4.sqlite", str(uuid4()), GENERATION_4
    )
    try:
        result = read_effect_scope(store)
    finally:
        store.close()

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "unsupported_schema"
    assert result.refusal.observed == str(GENERATION_4.user_version)
    assert result.refusal.expected == str(REQUIRED_EFFECT_GENERATION.user_version)


def test_no_field_of_the_mechanical_comparison_can_hold_an_authored_meaning() -> None:
    """The Preservation Boundaries: R08's result keeps its documented semantic silence.

    Catches an effect or a preservation claim leaking into the comparison as a field: none of the
    three result models declares one, and the shipped field-name vocabulary carries none of the words
    that would mean a meaning had moved onto the mechanical side.
    """

    for model in (KnowledgeDiffItem, KnowledgeDiffSourceChange, KnowledgeDiffResult):
        fields = set(model.model_fields)
        for word in MEANING_WORDS:
            assert not any(word in field.lower() for field in fields), (model.__name__, word)
    for name in KNOWLEDGE_DIFF_FIELD_NAMES:
        for word in MEANING_WORDS:
            assert word not in name.lower(), name


def test_the_record_groups_own_operation_vocabulary_declares_the_read_and_nothing_else() -> None:
    """The read is the record group's one operation: recording is the batch's, not a second path.

    Catches a second write path grown beside the batch: the run-time operation vocabulary carries the
    read and every authored-effect write names the batch's own operation.
    """

    operations = set(get_args(KnowledgeOperation))
    assert "read_effect_scope" in operations
    assert "add_invariant_effect_claim" not in operations
    assert "add_semantic_change_set" not in operations


def test_a_stored_requirement_revision_is_untouched_by_this_record_group(tmp_path: Path) -> None:
    """The opaque-reference clause's boundary: this leaf resolves no requirement reference.

    Catches a second requirement authority: L19's record is written by L19's own operation and read
    back unchanged by this record group, which neither resolves a stored reference against it nor
    writes one to satisfy a reference. The change-set module asserts the reporting half -- the
    reference stays verbatim and unresolved.
    """

    harness = _harness(tmp_path)
    store = harness.open()
    try:
        written = record_requirement_revision(store, _requirement_request(harness))
    finally:
        store.close()

    assert written.state == "recorded"
    assert written.scope is not None
    assert written.scope.kind == REQUIREMENT_REVISION_KIND
    assert written.scope.record_schema == REQUIREMENT_REVISION_SCHEMA
    assert _scope(harness).change_sets == ()


def _requirement_request(harness: CandidateHarness) -> Any:
    """Build one admissible requirement-revision request for the boundary case."""

    return RequirementRevisionRequest(
        repository_id=harness.repository_id,
        provenance=harness.authorship,
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload=RequirementRevisionPayload(
            owner=RequirementOwnerRef(
                path="requirements/KS-R13-v1-authored-effects-and-change-sets.md",
                stableId="KS-R13",
                version="v1",
            ),
            owner_resolution=RequirementOwnerResolution(state="resolved"),
            explanation="An obligation this record group stores no requirement authority for.",
        ),
    )
