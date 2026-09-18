"""``DetectionSignal``: the required facts, the closed vocabularies and the construction refusals.

These cases protect ``KS-R14@v1``'s facts-only record and its declared-input-set contract. They
occupy the ``unit-regression`` lane because what they measure is a typed record's own construction
boundary -- which fields are required, which vocabulary a value must come from, and which shape is
refused -- and not a process, a publication or a Git object.

Every case is named for the property its own assertions measure, and each one names the failure it
catches: a signal missing a required field, a condition outside the policy's declared vocabulary, a
declaration disagreeing with the discriminator it recorded, an observed change recorded at a
granularity nothing observed, a rendered judgment written into a prose field, and a manifest reported
retained at a destination that cannot retain it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
    open_database,
)
from agents_remember.memory.knowledge.detection import (
    REQUIRED_DETECTION_GENERATION,
    require_detection_generation,
)
from agents_remember.memory.knowledge.schema_generations import (
    GENERATION_2,
    GENERATION_3,
    GENERATION_4,
    create_schema_statements,
)
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.base import UUID_PATTERN
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.detection import (
    CONCLUSION_BEARING_FIELD_NAMES,
    CONDITION_VOCABULARY_VERSION,
    DECLARED_INPUT_SETS,
    DETECTION_CONDITIONS,
    DETECTION_EXTRACTOR_VERSION,
    DETECTION_LIMITATIONS,
    DETECTION_POLICY_VERSION,
    NO_SEMANTIC_ASSESSMENT_LIMITATION,
    DetectionCounterpartProbe,
    DetectionInputSide,
    DetectionObservedChange,
    DetectionRecordedInputSet,
    DetectionRelationshipPath,
    DetectionRunCurrentness,
    DetectionRunPayload,
    DetectionRunResult,
    DetectionScopeManifest,
    DetectionSignalPayload,
    ManifestDestinationObservation,
    conclusion_bearing_fields,
    declared_input_set_discriminators,
    observed_basis_detail,
)
from agents_remember.models.knowledge.diff import DiffCoverage
from agents_remember.models.knowledge.read import (
    KnowledgeReadContext,
)
from pydantic import ValidationError

pytestmark = pytest.mark.evidence_unit

REPOSITORY_ID = str(uuid4())
OTHER_REPOSITORY_ID = str(uuid4())
ROUTE_ID = str(uuid4())
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def snapshot(repository_id: str, digest: str, *, schema_version: str = "ar-knowledge-sqlite/v3"):
    return SnapshotIdentity(
        repository_id=repository_id, schema_version=schema_version, logical_digest=digest
    )


def input_side(side: str, digest: str) -> DetectionInputSide:
    return DetectionInputSide(
        side=side,
        context=KnowledgeReadContext(
            repository_id=REPOSITORY_ID, knowledge=snapshot(REPOSITORY_ID, digest)
        ),
        selector_digest=digest,
        selector_policy_version="recorded-family-frontier/v1",
    )


def both_sides() -> tuple[DetectionInputSide, DetectionInputSide]:
    return input_side("before", DIGEST_A), input_side("after", DIGEST_B)


def manifest(**overrides: object) -> DetectionScopeManifest:
    values: dict[str, object] = {
        "manifest_ref": "scope-manifest/17",
        "retention_required": True,
        "destination_kind": "durable_publication",
        "destination_ref": "notes/reports/evidence/17/manifest-17.json",
        "retention_basis": "a durable assessment needs the exact revisions behind this signal",
    }
    values.update(overrides)
    return DetectionScopeManifest(**values)  # type: ignore[arg-type]


def recorded_input_set(
    declared: str = "union_of_both_sides",
    *,
    probe: bool = True,
    sides: tuple[DetectionInputSide, ...] | None = None,
    omission: bool = False,
) -> DetectionRecordedInputSet:
    before, after = both_sides()
    return DetectionRecordedInputSet(
        declared=declared,  # type: ignore[arg-type]
        sides=sides if sides is not None else (before, after),
        counterpart_probe=(
            (
                DetectionCounterpartProbe(
                    item_id="claim:I1", item_kind="realization", coverage="selected_both"
                ),
            )
            if probe
            else ()
        ),
        probe_omission_declared=omission,
    )


def signal(**overrides: object) -> DetectionSignalPayload:
    paths = (
        DetectionRelationshipPath(
            path_id="path:1",
            snapshot_side="after",
            edges=("family:F1", "revision:I1", "claim:C1"),
            reached_item_id="claim:C1",
            realization_role="enforcement",
        ),
    )
    changes = (
        DetectionObservedChange(
            item_id="claim:C1",
            item_kind="realization",
            granularity="source_file_changed",
            path="src/integration.py",
            recorded_identity="blob-before",
            observed_identity="blob-after",
            locator_kind="file",
        ),
    )
    values: dict[str, object] = {
        "signal_id": str(uuid4()),
        "repository_id": REPOSITORY_ID,
        "governing_route_id": ROUTE_ID,
        "condition": "source_changed_on_both_sides_joined_to_same_family",
        "input_set": recorded_input_set(omission=False),
        "observed_changes": changes,
        "relationship_paths": paths,
        "extractor_version": DETECTION_EXTRACTOR_VERSION,
        "policy_version": DETECTION_POLICY_VERSION,
        "scope_manifest": manifest(),
        "registered_scope_status": "complete_for_declared_policy",
        "unmapped_changed_paths": (),
        "limitations": (NO_SEMANTIC_ASSESSMENT_LIMITATION,),
    }
    values.update(overrides)
    values.setdefault(
        "detail",
        observed_basis_detail(
            condition=str(values["condition"]),
            relationship_paths=tuple(
                path.path_id
                for path in values["relationship_paths"]  # type: ignore[union-attr]
            ),
            limitations=values["limitations"],  # type: ignore[arg-type]
        ),
    )
    return DetectionSignalPayload(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Requirement 1.1 and 5.2: the declared field set, and the facts-only property it must carry.


def test_the_signal_field_set_is_required_and_carries_no_conclusion_bearing_field() -> None:
    """Requirement 5.2's first half is a review of the *declared* field set, plus 1.1's required set.

    This catches the failure mode the packet names directly: a conclusion must be unrepresentable
    rather than discouraged, so the review is over field names rather than over a payload's contents
    -- and every field requirement 1.1 names must be present and required.
    """

    required = {
        "signal_id",
        "repository_id",
        "governing_route_id",
        "condition",
        "input_set",
        "observed_changes",
        "relationship_paths",
        "extractor_version",
        "policy_version",
        "scope_manifest",
        "registered_scope_status",
        "limitations",
    }
    declared = set(DetectionSignalPayload.model_fields)
    assert required <= declared
    for name in required:
        assert DetectionSignalPayload.model_fields[name].is_required(), name

    for model in (DetectionSignalPayload, DetectionRunPayload, DetectionRunResult):
        assert conclusion_bearing_fields(model) == (), model.__name__
    assert "severity" in CONCLUSION_BEARING_FIELD_NAMES
    assert "semantic_conflict" in CONCLUSION_BEARING_FIELD_NAMES
    assert "harmlessness" in CONCLUSION_BEARING_FIELD_NAMES


@pytest.mark.parametrize(
    "missing",
    [
        "repository_id",
        "governing_route_id",
        "condition",
        "input_set",
        "extractor_version",
        "policy_version",
        "scope_manifest",
        "registered_scope_status",
        "limitations",
    ],
)
def test_a_signal_missing_a_required_field_fails_construction(missing: str) -> None:
    """Requirement 1.1: a field that cannot be supplied is a refusal, not an absent value."""

    values = signal().model_dump(mode="json")
    del values[missing]
    with pytest.raises(ValidationError) as failure:
        DetectionSignalPayload.model_validate(values)
    assert missing in str(failure.value)


def test_a_payload_supplying_a_conclusion_bearing_field_is_refused_naming_it() -> None:
    """Requirement 5.2's second half: construction refuses a payload that supplies a conclusion.

    This is the packet's one required refusal test. It catches the whole class at once -- a severity,
    an assessed priority, a conflict verdict, a compatibility judgment, a harmlessness label, a
    causal explanation or an authored finding -- because the shipped base forbids an undeclared field
    by construction, so any of them is an extra field rather than a new code path.
    """

    for conclusion in ("severity", "semantic_conflict", "harmlessness", "assessment", "finding"):
        values = signal().model_dump(mode="json")
        values[conclusion] = "high"
        with pytest.raises(ValidationError) as failure:
            DetectionSignalPayload.model_validate(values)
        assert conclusion in str(failure.value)
        assert "extra_forbidden" in str(failure.value)


def test_a_verdict_written_into_the_detail_string_is_refused_as_the_same_defect() -> None:
    """Requirement 1.6 and 5.2: a verdict in a prose field is refused exactly as a verdict field.

    The detail is a *rendering* of the signal's recorded basis rather than authored text, so this
    catches the temptation the design names -- a detector that also says "these two changes conflict"
    -- without needing a phrase list that could be evaded.
    """

    values = signal().model_dump(mode="json")
    values["detail"] = (
        "condition=source_changed_on_both_sides_joined_to_same_family; this change is dangerous "
        "and breaks an unchanged sibling"
    )
    with pytest.raises(ValidationError) as failure:
        DetectionSignalPayload.model_validate(values)
    assert "detail" in str(failure.value)
    assert "observed basis" in str(failure.value)
    assert "detail" in DetectionSignalPayload.model_fields


# ---------------------------------------------------------------------------
# Requirement 1.2 and 1.3: the closed condition vocabulary and the recorded granularity.


def test_a_condition_outside_the_declared_vocabulary_is_refused_with_the_vocabulary_version() -> (
    None
):
    """Requirement 1.2: the vocabulary is closed and versioned with the policy, never free prose."""

    values = signal().model_dump(mode="json")
    values["condition"] = "the integration path looks suspicious"
    with pytest.raises(ValidationError) as failure:
        DetectionSignalPayload.model_validate(values)
    rendered = str(failure.value)
    assert "source_changed_on_both_sides_joined_to_same_family" in rendered
    assert CONDITION_VOCABULARY_VERSION == "detection-conditions/v1"
    assert len(DETECTION_CONDITIONS) == len(set(DETECTION_CONDITIONS)) == 5


def test_a_whole_file_observation_recorded_as_a_body_change_is_refused() -> None:
    """Requirement 1.3: the granularity is part of the fact.

    A file whose bytes moved is not evidence that the span the author attributed the obligation to
    moved, so recording a ``file`` locator's change as ``attributed_span_changed`` is the packet's
    non-conforming signal. The conforming pair is shown beside it, so the case states what is
    admitted as well as what is refused.
    """

    with pytest.raises(ValidationError) as failure:
        DetectionObservedChange(
            item_id="claim:C1",
            item_kind="realization",
            granularity="attributed_span_changed",
            path="src/integration.py",
            recorded_identity="blob-before",
            observed_identity="blob-after",
            locator_kind="file",
        )
    assert "source_file_changed" in str(failure.value)

    whole_file = DetectionObservedChange(
        item_id="claim:C1",
        item_kind="realization",
        granularity="source_file_changed",
        path="src/integration.py",
        locator_kind="file",
    )
    span = DetectionObservedChange(
        item_id="claim:C2",
        item_kind="realization",
        granularity="attributed_span_changed",
        path="src/retry.py",
        locator_kind="line_range",
    )
    assert whole_file.granularity != span.granularity


# ---------------------------------------------------------------------------
# Requirements 2.1 to 2.5: the declared input set and each member's own recorded discriminator.


def test_the_declared_input_set_vocabulary_is_exactly_three_members_each_with_its_own_discriminator() -> (
    None
):
    """Requirements 2.1 and 2.3: the closed vocabulary, and the three different recorded facts."""

    assert DECLARED_INPUT_SETS == (
        "both_sides_declared",
        "union_of_both_sides",
        "trigger_side_only",
    )
    discriminators = declared_input_set_discriminators()
    assert set(discriminators) == set(DECLARED_INPUT_SETS)
    assert len(set(discriminators.values())) == 3


def test_a_both_sides_declared_signal_recording_one_side_is_refused_naming_both_halves() -> None:
    """Requirement 2.3: the exact silent-widening shape, refused with both halves named.

    Example 3's defect. The refusal must make the defect diagnosable rather than merely rejected, so
    the case asserts that the declared member *and* the single recorded input both appear.
    """

    before, _after = both_sides()
    with pytest.raises(ValidationError) as failure:
        DetectionRecordedInputSet(declared="both_sides_declared", sides=(before,))
    rendered = str(failure.value)
    assert "both_sides_declared" in rendered
    assert "before" in rendered
    assert (
        "after" not in rendered.split("the recorded inputs name")[1].split("instead", maxsplit=1)[0]
    )


def test_a_union_declaration_with_no_recorded_probe_outcome_is_refused_naming_the_missing_fact() -> (
    None
):
    """Requirement 2.3: union semantics the signal cannot show it applied.

    This is the second silent-widening shape a reviewer looks for, and the case asserts the refusal
    names the *member* and the *missing fact* rather than only rejecting the payload.
    """

    before, after = both_sides()
    with pytest.raises(ValidationError) as failure:
        DetectionRecordedInputSet(
            declared="union_of_both_sides", sides=(before, after), counterpart_probe=()
        )
    rendered = str(failure.value)
    assert "union_of_both_sides" in rendered
    assert "counterpart probe" in rendered


def test_a_trigger_side_only_signal_records_one_side_and_no_probe_and_is_not_degraded() -> None:
    """Requirements 2.2, 2.3 and 2.5: one side is a complete fact, not an incomplete two-sided one.

    The conforming shape is asserted as positively as the refusals: one recorded side, no probe, and
    a declared member that says so.
    """

    trigger = input_side("trigger", DIGEST_B)
    recorded = DetectionRecordedInputSet(declared="trigger_side_only", sides=(trigger,))
    assert len(recorded.sides) == 1
    assert recorded.counterpart_probe == ()

    with pytest.raises(ValidationError) as two_sides:
        DetectionRecordedInputSet(
            declared="trigger_side_only", sides=(input_side("before", DIGEST_A), trigger)
        )
    assert "exactly one side" in str(two_sides.value)

    with pytest.raises(ValidationError) as with_probe:
        DetectionRecordedInputSet(
            declared="trigger_side_only",
            sides=(trigger,),
            counterpart_probe=(
                DetectionCounterpartProbe(
                    item_id="claim:C1", item_kind="realization", coverage="selected_both"
                ),
            ),
        )
    assert "no counterpart-probe outcome at all" in str(with_probe.value)


def test_a_trigger_side_only_signal_asserting_a_fact_about_the_unread_side_is_refused() -> None:
    """Example 4: a trigger-side-only read cannot establish that the unread side did not change.

    The direction of this error is the opposite of example 3's: the signal overstates what it ruled
    out rather than what it concluded, and the refusal says so in those terms.
    """

    trigger = input_side("trigger", DIGEST_B)
    values = signal(
        input_set=DetectionRecordedInputSet(declared="trigger_side_only", sides=(trigger,)),
        limitations=("no_counterpart_read", NO_SEMANTIC_ASSESSMENT_LIMITATION),
    ).model_dump(mode="json")
    values["detail"] = (
        "condition=source_changed_on_both_sides_joined_to_same_family; the other side is unchanged"
    )
    with pytest.raises(ValidationError) as failure:
        DetectionSignalPayload.model_validate(values)
    rendered = str(failure.value)
    assert "trigger_side_only" in rendered
    assert "the other side was unchanged" in rendered
    assert "scope limitation" in rendered


def test_a_signal_declaring_an_omission_without_the_limitation_and_the_reverse_are_both_refused() -> (
    None
):
    """Requirement 2.3's omission clause, checked in both directions on the shipped idiom.

    A signal that omitted an item for ``present_outside_the_declared_selection`` must advertise it,
    and one that advertises it must have omitted something: a declaration a record does not need
    teaches a reviewer to ignore the field.
    """

    before, after = both_sides()
    with pytest.raises(ValidationError) as hidden:
        signal(
            input_set=DetectionRecordedInputSet(
                declared="union_of_both_sides",
                sides=(before, after),
                counterpart_probe=(
                    DetectionCounterpartProbe(
                        item_id="claim:C1",
                        item_kind="realization",
                        coverage="present_outside_selection",
                    ),
                ),
                probe_omission_declared=True,
            ),
            limitations=(NO_SEMANTIC_ASSESSMENT_LIMITATION,),
        )
    assert "records_present_outside_the_declared_selection" in str(hidden.value)

    with pytest.raises(ValidationError) as declared_only:
        signal(
            limitations=(
                "records_present_outside_the_declared_selection",
                NO_SEMANTIC_ASSESSMENT_LIMITATION,
            )
        )
    assert "present_outside_the_declared_selection" in str(declared_only.value)


def test_every_signal_states_that_no_semantic_assessment_was_performed() -> None:
    """Requirement 5.1, on the shipped comparison's own unconditional-limitation idiom."""

    with pytest.raises(ValidationError) as failure:
        signal(limitations=())
    assert "no semantic assessment" in str(failure.value)
    assert NO_SEMANTIC_ASSESSMENT_LIMITATION in DETECTION_LIMITATIONS


def test_an_incomplete_scan_must_declare_its_truncation_and_an_unmapped_path_its_gap() -> None:
    """Requirement 6.1 and 6.3: an incomplete scan and an unmapped path are advertised, not smoothed.

    ``complete_for_declared_policy`` is a bounded scan result rather than a sufficiency proof, and an
    empty ``unmapped_changed_paths`` means only that the declared lookup found recorded links; both
    readings are pinned here so neither can drift into a claim the record does not support.
    """

    with pytest.raises(ValidationError) as unmapped:
        signal(unmapped_changed_paths=("src/unmapped.py",))
    assert "unmapped_changed_paths" in str(unmapped.value)

    with pytest.raises(ValidationError) as truncated:
        signal(registered_scope_status="incomplete_scan")
    assert "truncated_scan" in str(truncated.value)

    admitted = signal(
        registered_scope_status="incomplete_scan",
        limitations=("truncated_scan", NO_SEMANTIC_ASSESSMENT_LIMITATION),
    )
    assert admitted.registered_scope_status == "incomplete_scan"


# ---------------------------------------------------------------------------
# Requirements 4.1 to 4.5: the manifest reference and what retention may be reported.


def test_a_manifest_may_not_be_reported_retained_at_a_destination_that_cannot_retain_it() -> None:
    """Example 8: an enclosure-local home is the one destination that cannot satisfy retention.

    The retained report requires a durable destination *identity*; a destination that is
    enclosure-local, worktree-local or a regenerable worklist is reported unresolved, naming the
    reference and what would resolve it, and never as an empty manifest.
    """

    with pytest.raises(ValidationError) as unnamed:
        manifest(destination_ref=None)
    assert "retention it cannot show is not retention" in str(unnamed.value)

    enclosure_local = manifest(
        destination_kind="enclosure_local",
        destination_ref=".ar/enclosure/260915-ks-l14/manifest-17.json",
    )
    unresolved = enclosure_local.resolve(
        ManifestDestinationObservation(
            destination_kind="enclosure_local",
            destination_ref=".ar/enclosure/260915-ks-l14/manifest-17.json",
            resolved=True,
            detail="the enclosure-local path holds the manifest today",
        )
    )
    assert unresolved.retention_state == "unresolved"
    assert unresolved.manifest_ref == "scope-manifest/17"
    assert unresolved.destination_ref == ".ar/enclosure/260915-ks-l14/manifest-17.json"
    assert unresolved.what_would_resolve is not None
    assert "KS-R12@v1" in unresolved.what_would_resolve
    assert "not an empty manifest" in unresolved.detail

    retained = manifest().resolve(
        ManifestDestinationObservation(
            destination_kind="durable_publication",
            destination_ref="notes/reports/evidence/17/manifest-17.json",
            resolved=True,
            detail="the durable publication route holds the manifest",
        )
    )
    assert retained.retention_state == "retained"
    assert retained.what_would_resolve is None


def test_a_manifest_reference_that_cannot_be_resolved_names_what_would_resolve_it() -> None:
    """Requirement 4.2: never silently, and never as an empty manifest."""

    unresolved = manifest().resolve(
        ManifestDestinationObservation(
            destination_kind="durable_publication",
            destination_ref="notes/reports/evidence/17/manifest-17.json",
            resolved=False,
            detail="the recorded destination does not exist",
        )
    )
    assert unresolved.retention_state == "unresolved"
    assert unresolved.what_would_resolve is not None
    assert "notes/reports/evidence/17/manifest-17.json" in unresolved.what_would_resolve
    with pytest.raises(ValidationError):
        type(unresolved)(**{**unresolved.model_dump(), "what_would_resolve": None})


# ---------------------------------------------------------------------------
# Requirement 3.6: currentness follows from the version comparison and rewrites nothing.


def test_currentness_follows_from_the_version_comparison_and_never_relabels_a_signal() -> None:
    """Requirement 3.6 / example 6: a stale run keeps its recorded versions, on run and signal alike.

    The value carries the recorded versions beside the current ones and has no field holding a
    re-interpretation, which is why ``signals_unchanged`` is the literal ``True``.
    """

    values = signal().model_dump(mode="json")
    payloads = DetectionRunPayload(
        run_id=str(uuid4()),
        repository_id=REPOSITORY_ID,
        assessed_repository_id=OTHER_REPOSITORY_ID,
        governing_route_id=ROUTE_ID,
        policy_version=DETECTION_POLICY_VERSION,
        extractor_version=DETECTION_EXTRACTOR_VERSION,
        input_sides=both_sides(),
        declared_input_sets=("union_of_both_sides",),
        recorded_conditions=("source_changed_on_both_sides_joined_to_same_family",),
        signal_order=(values["signal_id"],),
        limitations=(NO_SEMANTIC_ASSESSMENT_LIMITATION,),
        detail=(
            f"policy={DETECTION_POLICY_VERSION}; extractor={DETECTION_EXTRACTOR_VERSION}; "
            f"conditions={CONDITION_VOCABULARY_VERSION}; declared_input_sets=union_of_both_sides; "
            f"ordered_signals=1; limitations={NO_SEMANTIC_ASSESSMENT_LIMITATION}"
        ),
    )
    current = DetectionRunCurrentness(
        run_id=payloads.run_id,
        recorded_policy_version=DETECTION_POLICY_VERSION,
        current_policy_version=DETECTION_POLICY_VERSION,
        recorded_extractor_version=DETECTION_EXTRACTOR_VERSION,
        current_extractor_version=DETECTION_EXTRACTOR_VERSION,
        binding_state="current",
    )
    assert current.differing_versions == ()
    assert current.signals_unchanged is True

    stale = DetectionRunCurrentness(
        run_id=payloads.run_id,
        recorded_policy_version=DETECTION_POLICY_VERSION,
        current_policy_version="family-detection/v2",
        recorded_extractor_version=DETECTION_EXTRACTOR_VERSION,
        current_extractor_version=DETECTION_EXTRACTOR_VERSION,
        binding_state="stale",
        differing_versions=("policy_version",),
    )
    assert stale.recorded_policy_version == DETECTION_POLICY_VERSION
    assert stale.current_policy_version == "family-detection/v2"
    assert "signals" not in DetectionRunCurrentness.model_fields

    with pytest.raises(ValidationError) as unstated:
        DetectionRunCurrentness(
            run_id=payloads.run_id,
            recorded_policy_version=DETECTION_POLICY_VERSION,
            current_policy_version="family-detection/v2",
            recorded_extractor_version=DETECTION_EXTRACTOR_VERSION,
            current_extractor_version=DETECTION_EXTRACTOR_VERSION,
            binding_state="current",
        )
    assert "names exactly the version axes that moved" in str(unstated.value)

    with pytest.raises(ValidationError) as lying:
        DetectionRunCurrentness(
            run_id=payloads.run_id,
            recorded_policy_version=DETECTION_POLICY_VERSION,
            current_policy_version="family-detection/v2",
            recorded_extractor_version=DETECTION_EXTRACTOR_VERSION,
            current_extractor_version=DETECTION_EXTRACTOR_VERSION,
            binding_state="current",
            differing_versions=("policy_version",),
        )
    assert "binding state follows from the version comparison" in str(lying.value)


# ---------------------------------------------------------------------------
# Requirement 3.5's second place, and 3.1's policy identity, on the run itself.


def test_the_policy_identity_and_the_extractor_version_are_named_constants_not_paths() -> None:
    """Requirement 3.1: a versioned policy name in the shipped constant idiom, never a path."""

    assert DETECTION_POLICY_VERSION == "family-detection/v1"
    assert DETECTION_EXTRACTOR_VERSION == "recorded-anchor-locator/v1"
    # A policy identity is a *value* rather than a path or a build-time assumption: neither
    # constant names a file, and both are ordinary module-level names a caller imports.
    for constant in (DETECTION_POLICY_VERSION, DETECTION_EXTRACTOR_VERSION):
        assert constant
        assert not constant.startswith(("/", "."))
        assert not constant.endswith((".py", ".json", ".db"))
    assert UUID_PATTERN == r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    assert DiffCoverage.__args__ == (
        "selected_both",
        "selected_before_only",
        "selected_after_only",
        "present_outside_selection",
        "absent_from_snapshot",
    )


# ---------------------------------------------------------------------------
# Requirement 7.3: the generation the record group's own table registers through.


def test_a_dataset_predating_the_detection_table_refuses_a_detection_write(tmp_path: Path) -> None:
    """Requirement 7.3: the observed and required generation as facts, with no migration.

    This catches the failure mode a local schema decision produces: a dataset that predates the
    detection table being written through as if it had it, or being migrated to obtain a green
    result.
    """

    repository_id = str(uuid4())
    path = tmp_path / "generation-2.db"
    connection = open_database(path)
    try:
        create_or_validate_schema(connection)
        connection.execute("PRAGMA user_version = 2")
    finally:
        connection.close()
    store = open_existing_knowledge_store(path, repository_id)
    try:
        assert store.generation is GENERATION_2
        denial = require_detection_generation(store, "record_detection_run")
        assert denial is not None
        assert denial.code == "unsupported_schema"
        assert denial.expected == str(REQUIRED_DETECTION_GENERATION.user_version)
        assert denial.observed == str(GENERATION_2.user_version)
        assert denial.table == "detection_run_signal"
    finally:
        store.close()


def test_generation_4_appends_only_and_the_first_twenty_names_are_generation_3_s() -> None:
    """Requirement 7.3's additive rule, asserted as the prefix equality it is.

    Generation 4 is the generation this leaf registers, so the case states what it appends and that
    every earlier declaration is untouched -- the property a local table list would break.
    """

    assert GENERATION_4.user_version == 4
    assert GENERATION_4.schema_name == "ar-knowledge-sqlite/v4"
    assert GENERATION_4.tables[: len(GENERATION_3.tables)] == GENERATION_3.tables
    for table in GENERATION_3.tables:
        assert GENERATION_4.columns[table] == GENERATION_3.columns[table], table
        assert GENERATION_4.primary_keys[table] == GENERATION_3.primary_keys[table], table
    appended = GENERATION_4.tables[len(GENERATION_3.tables) :]
    assert appended == ("detection_run_signal",)
    assert "content_digest" not in GENERATION_4.columns["detection_run_signal"]
    assert GENERATION_4.primary_keys["detection_run_signal"] == (
        "repository_id",
        "run_id",
        "ordinal",
    )
    assert {
        "detection_run_signal_no_reorder",
        "detection_run_signal_no_delete",
    } <= set(GENERATION_4.triggers)
    for generation in (GENERATION_2, GENERATION_3, GENERATION_4):
        for statement in create_schema_statements(generation):
            assert "ALTER TABLE" not in statement.upper()
