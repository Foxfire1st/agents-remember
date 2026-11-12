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

from uuid import uuid4

import pytest
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.detection import (
    NO_SEMANTIC_ASSESSMENT_LIMITATION,
    DetectionCounterpartProbe,
    DetectionInputSide,
    DetectionObservedChange,
    DetectionRecordedInputSet,
    DetectionRelationshipPath,
    DetectionScopeManifest,
    DetectionSide,
    DetectionSignalPayload,
    ManifestDestinationObservation,
    observed_basis_detail,
)
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


def input_side(side: DetectionSide, digest: str) -> DetectionInputSide:
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
        "extractor_version": "recorded-anchor-locator/v1",
        "policy_version": "family-detection/v1",
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


# ---------------------------------------------------------------------------
# Requirement 3.5's second place, and 3.1's policy identity, on the run itself.


# ---------------------------------------------------------------------------
# Requirement 7.3: the generation the record group's own table registers through.
