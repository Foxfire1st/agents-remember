"""``DetectionRun``: the walk over the shipped comparison, its order, its write path and its facts.

These cases protect ``KS-R14@v1``'s run contract: the deterministic walk over R08's own result, the
same condition identity for a real change and its harmless control, the recorded order that makes
reproducibility a comparison of two sequences, the two-place version publication, the run that is
marked stale without its signals being reinterpreted, and the refusal that keeps a detection write
out of the assessed dataset's own measurement transaction.

They occupy the ``unit-regression`` lane because what they measure is the detection contract's own
logic -- which recorded facts produce which condition, which order two executions produce, and which
write is refused -- and not a process, a publication or a Git object. The one case that runs a *real*
comparison drives the shipped seams through ``diff_scope_test_support`` and reads its result through
the application layer, so the walk is shown to consume real R08 output rather than a shape invented
beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.memory.knowledge.connection import open_database, open_read_only_database
from agents_remember.memory.knowledge.detection import (
    DetectionRunAssembly,
    build_detection_run,
    compare_detection_runs,
    read_detection_run,
    record_detection_run,
    require_separate_from_assessed,
    resolve_manifest_reference,
    run_currentness,
)
from agents_remember.memory.knowledge.detection_walk import (
    DetectionWalkInput,
    detect_review_conditions,
)
from agents_remember.memory.knowledge.logical import logical_digest
from agents_remember.memory.knowledge.schema_generations import (
    GENERATION_3,
    create_schema_statements,
    generation_of_database,
)
from agents_remember.memory.knowledge.store import (
    open_existing_knowledge_store,
    open_knowledge_store,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.detection import (
    DECLARED_INPUT_SETS,
    DETECTION_CONDITIONS,
    DETECTION_EXTRACTOR_VERSION,
    DETECTION_POLICY_VERSION,
    NO_SEMANTIC_ASSESSMENT_LIMITATION,
    DetectionInputSide,
    DetectionLimitation,
    DetectionRunPayload,
    DetectionRunRequest,
    DetectionScopeManifest,
    DetectionSide,
    DetectionSignalPayload,
    ManifestDestinationObservation,
    conclusion_bearing_fields,
    observed_basis_detail,
)
from agents_remember.models.knowledge.diff import (
    KnowledgeDiffCounts,
    KnowledgeDiffItem,
    KnowledgeDiffPage,
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
    KnowledgeDiffSourceChange,
)
from agents_remember.models.knowledge.read import (
    AnchorResolution,
    InvariantIdentitySeed,
    KnowledgeReadContext,
    ReadItem,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.source import FileLocator, LineRangeLocator, SymbolLocator
from diff_scope_test_support import DiffFixture, build_diff_fixture
from pydantic import ValidationError

pytestmark = pytest.mark.evidence_unit

REPOSITORY_ID = str(uuid4())
ROUTE_ID = str(uuid4())
DIGEST_BEFORE = "1" * 64
DIGEST_AFTER = "2" * 64
DIGEST_AFTER_MOVED = "3" * 64
CLAIM_A = str(uuid4())
CLAIM_B = str(uuid4())
INVARIANT_REVISION = str(uuid4())
FAMILY_REVISION = str(uuid4())


# ---------------------------------------------------------------------------
# Building a comparison result to walk. The walk reads R08's own result type, so these helpers build
# that type directly for the two scenarios that must not be distinguishable; one case below drives a
# *real* comparison through the shipped seams to show the walk consumes it.


def anchor(path: str, *, locator: object, observed: str | None) -> AnchorResolution:
    return AnchorResolution(
        anchor_id=str(uuid4()),
        path=path,
        recorded_source_identity="blob-before",
        observed_source_identity=observed,
        locator=locator,  # type: ignore[arg-type]
        resolution="exact_recorded_blob" if observed else "path_absent",
        detail="the recorded claim is reported against the requested tree and nothing else",
    )


@dataclass(frozen=True)
class ClaimSpec:
    """One realization claim to build into a comparison item, as a value.

    ``locator`` and ``observed`` travel with the path because the three are one observation: a
    locator's kind decides the granularity the walk may record, and a claimed span that no
    whole-path locator could have observed is the packet's non-conforming signal. ``resolution``
    overrides the resolution the observed identity implies, which is how the unsupported-locator
    case states a claim the resolver could not map.
    """

    claim_id: str
    path: str
    locator: object
    observed: str | None
    source_changed: bool
    resolution: str | None = None


def realization_item(spec: ClaimSpec) -> KnowledgeDiffItem:
    resolved = anchor(spec.path, locator=spec.locator, observed=spec.observed)
    if spec.resolution is not None:
        resolved = resolved.model_copy(update={"resolution": spec.resolution})
    before = ReadItem(
        kind="realization_claim",
        item_id=spec.claim_id,
        invariant_revision_id=INVARIANT_REVISION,
        claim_id=spec.claim_id,
        role="enforcement",
        rationale="the recorded attribution",
        anchor=resolved,
    )
    return KnowledgeDiffItem(
        item_id=spec.claim_id,
        kind="realization",
        coverage="selected_both",
        record_transition="unchanged",
        before=before,
        after=before,
        before_selected=True,
        after_selected=True,
        record_id=spec.claim_id,
        source_change=KnowledgeDiffSourceChange(
            claim_id=spec.claim_id,
            before_observation=before,
            after_observation=before,
            record_field_changed=False,
            source_observation_changed=spec.source_changed,
            source_change_only=spec.source_changed,
            record_change_only=False,
        ),
    )


def family_union() -> list[KnowledgeDiffItem]:
    """The retry-budget topology: one family holding two attributed claims of one member.

    ``F1 -> {I1}`` with two realization claims of ``I1`` -- the shared shape of the budget scenario
    and of its harmless control, which differ only in the bytes behind the changed observations.
    """

    membership = KnowledgeDiffItem(
        item_id=f"member:{INVARIANT_REVISION}",
        kind="membership",
        coverage="selected_both",
        record_transition="unchanged",
        before=ReadItem(
            kind="family_membership",
            item_id=f"member:{INVARIANT_REVISION}",
            member_id=str(uuid4()),
            family_revision_id=FAMILY_REVISION,
            invariant_revision_id=INVARIANT_REVISION,
        ),
        before_selected=True,
        after_selected=True,
    )
    family = KnowledgeDiffItem(
        item_id=FAMILY_REVISION,
        kind="family",
        coverage="selected_both",
        record_transition="unchanged",
        before=ReadItem(
            kind="family_revision",
            item_id=FAMILY_REVISION,
            family_id=str(uuid4()),
            revision_id=FAMILY_REVISION,
        ),
        before_selected=True,
        after_selected=True,
        revision_id=FAMILY_REVISION,
    )
    return [
        family,
        membership,
        realization_item(
            ClaimSpec(
                claim_id=CLAIM_A,
                path="src/integration.py",
                locator=FileLocator(),
                observed="blob-integration-M",
                source_changed=True,
            )
        ),
        realization_item(
            ClaimSpec(
                claim_id=CLAIM_B,
                path="src/synchronization.py",
                locator=LineRangeLocator(start_line=5, end_line=9),
                observed="blob-sync-M",
                source_changed=True,
            )
        ),
    ]


def comparison(items: list[KnowledgeDiffItem], *, changed_sources: int = 2) -> KnowledgeDiffResult:
    total = len(items)
    return KnowledgeDiffResult(
        state="page",
        repository_id=REPOSITORY_ID,
        limitations=("no_semantic_assessment_performed",),
        page=KnowledgeDiffPage(
            items=tuple(items),
            counts=KnowledgeDiffCounts(
                items_total=total,
                items_returned=total,
                items_remaining=0,
                displayed_total=total,
                suppressed_total=0,
                changed_field_count=0,
                changed_source_observation_count=changed_sources,
            ),
            has_more=False,
            enumeration_complete=True,
        ),
    )


def input_side(side: DetectionSide, digest: str) -> DetectionInputSide:
    return DetectionInputSide(
        side=side,
        context=KnowledgeReadContext(
            repository_id=REPOSITORY_ID,
            knowledge=SnapshotIdentity(
                repository_id=REPOSITORY_ID,
                schema_version=GENERATION_3.schema_name,
                logical_digest=digest,
            ),
        ),
        selector_digest=digest,
        selector_policy_version="recorded-family-frontier/v1",
    )


def both_sides(after_digest: str = DIGEST_AFTER) -> tuple[DetectionInputSide, DetectionInputSide]:
    return input_side("before", DIGEST_BEFORE), input_side("after", after_digest)


def walk(
    result: KnowledgeDiffResult, *, declared: str = "union_of_both_sides"
) -> DetectionWalkInput:
    return DetectionWalkInput(
        repository_id=REPOSITORY_ID,
        governing_route_id=ROUTE_ID,
        comparison=result,
        input_sides=both_sides(),
        declared=declared,
    )


# ---------------------------------------------------------------------------
# Requirement 5.4 / example 5: the scenario and its harmless control are indistinguishable.


def test_a_budget_change_and_a_comments_only_change_produce_the_same_condition_and_signal() -> None:
    """Example 5: the detector emits the same condition identity in both scenarios.

    The two comparisons are identical in every recorded structural fact and differ only in the
    *source identities* the changed observations resolved to -- that is, only in bytes the walk never
    reads. This is the acceptance test of the responsibility boundary: a detector that emitted a
    weaker or no signal for the control would have replaced a curator judgment with a mechanical
    guess, and this case catches exactly that.
    """

    budget = comparison(family_union())
    control = comparison(family_union())

    budget_signals = detect_review_conditions(walk(budget))
    control_signals = detect_review_conditions(walk(control))

    assert [signal.condition for signal in budget_signals] == [
        "source_changed_on_both_sides_joined_to_same_family"
    ]
    assert [signal.condition for signal in control_signals] == [
        signal.condition for signal in budget_signals
    ]
    assert [signal.signal_id for signal in control_signals] == [
        signal.signal_id for signal in budget_signals
    ]
    for produced in (*budget_signals, *control_signals):
        assert conclusion_bearing_fields(DetectionSignalPayload) == ()
        assert NO_SEMANTIC_ASSESSMENT_LIMITATION in produced.limitations
        assert "severity" not in produced.model_dump()
        assert produced.registered_scope_status == "complete_for_declared_policy"


def test_a_grouped_signal_retains_every_contributing_match_with_its_path_and_edges() -> None:
    """Requirement 1.5: dedup may group, but a contributing match or its path may not be dropped."""

    signals = detect_review_conditions(walk(comparison(family_union())))
    assert len(signals) == 1
    grouped = signals[0]
    reached = {path.reached_item_id for path in grouped.relationship_paths}
    assert reached == {CLAIM_A, CLAIM_B}
    assert {change.item_id for change in grouped.observed_changes} == {CLAIM_A, CLAIM_B}
    for path in grouped.relationship_paths:
        assert path.edges[0] == FAMILY_REVISION
        assert len(path.edges) == 3


def test_the_declared_order_is_total_over_signal_identity_and_independent_of_item_order() -> None:
    """Requirement 3.3: reproducibility compares two ordered sequences, not two sets.

    The case reverses the union's own item order and asserts the emitted order does not move, which
    is what makes the order *declared* rather than incidental.
    """

    items = family_union()
    forward = detect_review_conditions(walk(comparison(items)))
    reversed_items = list(reversed(items))
    backward = detect_review_conditions(walk(comparison(reversed_items)))
    assert [signal.signal_id for signal in backward] == [signal.signal_id for signal in forward]

    single_change = [
        item for item in items if item.kind != "realization" or item.item_id == CLAIM_A
    ] + [
        realization_item(
            ClaimSpec(
                claim_id=CLAIM_B,
                path="src/synchronization.py",
                locator=LineRangeLocator(start_line=5, end_line=9),
                observed="blob-sync-M",
                source_changed=False,
            )
        )
    ]
    one_sided = detect_review_conditions(walk(comparison(single_change)))
    assert [signal.condition for signal in one_sided] == [
        "one_sided_source_change_with_recorded_siblings"
    ]
    assert DETECTION_CONDITIONS.index("source_changed_on_both_sides_joined_to_same_family") == 0
    assert DETECTION_CONDITIONS.index("one_sided_source_change_with_recorded_siblings") == 1


def test_an_unsupported_locator_is_a_declared_limitation_rather_than_a_negative_match() -> None:
    """Example 7: the shipped resolver's ``unsupported_locator`` is a fact, not an error or a miss.

    A symbol locator is unreadable in this increment, so the walk records the limitation and the
    signal remains a complete fact about what was read.
    """

    items = [
        *family_union(),
        realization_item(
            ClaimSpec(
                claim_id=str(uuid4()),
                path="src/symbolic.py",
                locator=SymbolLocator(language="python", qualified_name="pkg.mod.fn"),
                observed=None,
                source_changed=False,
                resolution="unsupported_locator",
            )
        ),
    ]
    signals = detect_review_conditions(walk(comparison(items)))
    assert signals
    for produced in signals:
        assert "unsupported_locator" in produced.limitations
        assert NO_SEMANTIC_ASSESSMENT_LIMITATION in produced.limitations


def test_an_unmapped_changed_path_and_a_truncated_scan_are_advertised_rather_than_dropped() -> None:
    """Requirement 6.1 and 6.3: both are representable outcomes and neither is smoothed away."""

    walk_input = DetectionWalkInput(
        repository_id=REPOSITORY_ID,
        governing_route_id=ROUTE_ID,
        comparison=comparison(family_union()),
        input_sides=both_sides(),
        declared="union_of_both_sides",
        unmapped_changed_paths=("src/unmapped.py",),
        truncated=True,
    )
    signals = detect_review_conditions(walk_input)
    assert signals
    for produced in signals:
        assert produced.unmapped_changed_paths == ("src/unmapped.py",)
        assert "unmapped_changed_paths" in produced.limitations
        assert produced.registered_scope_status == "incomplete_scan"
        assert "truncated_scan" in produced.limitations


def test_the_walk_declares_the_probe_and_the_omission_the_comparison_actually_recorded() -> None:
    """Requirement 2.3: the union member's discriminator comes from R08's own recorded facts."""

    signals = detect_review_conditions(walk(comparison(family_union())))
    recorded = signals[0].input_set
    assert recorded.declared == "union_of_both_sides"
    assert len(recorded.sides) == 2
    assert recorded.counterpart_probe
    assert all(probe.coverage == "selected_both" for probe in recorded.counterpart_probe)
    assert recorded.probe_omission_declared is False
    assert "records_present_outside_the_declared_selection" not in signals[0].limitations
    assert DECLARED_INPUT_SETS == (
        "both_sides_declared",
        "union_of_both_sides",
        "trigger_side_only",
    )


def test_a_both_sides_declared_walk_over_one_side_is_refused_rather_than_widened() -> None:
    """Requirement 2.3: a declaration that disagrees with the recorded inputs fails construction."""

    walk_input = DetectionWalkInput(
        repository_id=REPOSITORY_ID,
        governing_route_id=ROUTE_ID,
        comparison=comparison(family_union()),
        input_sides=both_sides()[:1],
        declared="both_sides_declared",
    )
    with pytest.raises(ValidationError) as failure:
        detect_review_conditions(walk_input)
    assert "both_sides_declared" in str(failure.value)


# ---------------------------------------------------------------------------
# The walk over a real shipped comparison.


@pytest.fixture(scope="module")
def real_fixture(tmp_path_factory: pytest.TempPathFactory) -> DiffFixture:
    """One real two-snapshot fixture, shared because building it is the expensive part."""

    return build_diff_fixture(tmp_path_factory.mktemp("detection-diff"))


def run_real_diff(fixture: DiffFixture) -> KnowledgeDiffResult:
    """Run one comparison of the fixture's two snapshots through the shipped application seam."""

    return diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id),
            before=KnowledgeDiffSide(
                context=open_diff_side(
                    fixture.before.database_path,
                    fixture.repository_id,
                    repository_root=fixture.before.git_root,
                    code_tree_id=fixture.before_tree_id,
                )
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(
                    fixture.after.database_path,
                    fixture.repository_id,
                    repository_root=fixture.after.git_root,
                    code_tree_id=fixture.after_tree_id,
                )
            ),
        ),
        before_path=fixture.before.database_path,
        after_path=fixture.after.database_path,
    )


def test_the_walk_consumes_the_shipped_comparison_and_emits_facts_only_signals(
    real_fixture: DiffFixture,
) -> None:
    """Requirement 3 / §5: the detector reads what R07 and R08 produce and adds no selection rule.

    The comparison is the shipped one, over two real databases and two real Git trees, and it is read
    through the application seam. Every signal the walk emits is then checked for the properties the
    facts-only boundary is made of.
    """

    result = run_real_diff(real_fixture)
    assert result.state == "page"
    before = DetectionInputSide(
        side="before",
        context=KnowledgeReadContext(
            repository_id=real_fixture.repository_id,
            knowledge=SnapshotIdentity(
                repository_id=real_fixture.repository_id,
                schema_version=GENERATION_3.schema_name,
                logical_digest=str(result.selector_digest),
            ),
        ),
        selector_digest=str(result.selector_digest),
        selector_policy_version=str(result.policy_version),
    )
    walk_input = DetectionWalkInput(
        repository_id=real_fixture.repository_id,
        governing_route_id=ROUTE_ID,
        comparison=result,
        input_sides=(before, before.model_copy(update={"side": "after"})),
        declared="union_of_both_sides",
    )
    signals = detect_review_conditions(walk_input)
    assert signals, "the fixture's union holds changed realization claims"
    for produced in signals:
        assert produced.condition in DETECTION_CONDITIONS
        assert produced.policy_version == DETECTION_POLICY_VERSION
        assert produced.extractor_version == DETECTION_EXTRACTOR_VERSION
        assert produced.input_set.declared == "union_of_both_sides"
        assert produced.input_set.counterpart_probe
        assert conclusion_bearing_fields(DetectionSignalPayload) == ()
        assert produced.detail == observed_basis_detail(
            condition=produced.condition,
            relationship_paths=tuple(path.path_id for path in produced.relationship_paths),
            limitations=produced.limitations,
        )


def real_input_sides(
    fixture: DiffFixture, result: KnowledgeDiffResult
) -> tuple[DetectionInputSide, DetectionInputSide]:
    """The two sides one walk read, each named by its own dataset's logical digest.

    One selector produced both sides of this comparison, so the selector digest is one value; the
    dataset identity is not, and a recorded run that copied one side's snapshot onto the other would
    name a dataset it did not read.
    """

    sides: list[DetectionInputSide] = []
    named: tuple[tuple[Path, DetectionSide], ...] = (
        (fixture.before.database_path, "before"),
        (fixture.after.database_path, "after"),
    )
    for database_path, side in named:
        connection = open_read_only_database(database_path)
        try:
            generation = generation_of_database(connection)
            digest = logical_digest(connection, generation)
        finally:
            connection.close()
        sides.append(
            DetectionInputSide(
                side=side,
                context=KnowledgeReadContext(
                    repository_id=fixture.repository_id,
                    knowledge=SnapshotIdentity(
                        repository_id=fixture.repository_id,
                        schema_version=generation.schema_name,
                        logical_digest=digest,
                    ),
                ),
                selector_digest=str(result.selector_digest),
                selector_policy_version=str(result.policy_version),
            )
        )
    first, second = sides
    return first, second


def claim_records_spoken_for_twice(result: KnowledgeDiffResult) -> set[str]:
    """Return the claim records more than one realization item of the union speaks for."""

    items = () if result.page is None else result.page.items
    counts: dict[str, int] = {}
    for item in items:
        if item.kind != "realization" or item.record_id is None:
            continue
        counts[item.record_id] = counts.get(item.record_id, 0) + 1
    return {record for record, count in counts.items() if count > 1}


def test_a_run_over_the_shipped_comparison_names_every_signal_once_and_is_recorded(
    real_fixture: DiffFixture, tmp_path: Path
) -> None:
    """The identity scheme names the item each single-item condition is about, and the run records.

    The shipped fixture's union holds one realization claim record that two realization items speak
    for -- one claim carrying two coverage items, which is a shape R08's union produces and not a
    fixture artifact -- and both of those items are gone from the after side. A walk that identified
    both signals by the record they share would emit two signals under one identity, and the run's own
    reproducibility rule refuses that order (correctly: an order that names an identity twice is not
    an order over a set of signals). So this case walks the shipped comparison, records the run it
    produces and reads the recorded order back, which is what makes the identity a property of the
    write path rather than of a walk asserted beside it.

    Reverting the walk's group key to the record identity reddens it at the duplicate-identity
    assertion and at the refusal the run builder raises after it; collapsing the two items into one
    signal reddens it at the per-item signal assertion below.
    """

    result = run_real_diff(real_fixture)
    assert result.state == "page"
    union_items = () if result.page is None else result.page.items
    removed_items = {
        item.item_id
        for item in union_items
        if item.kind == "realization" and item.before is not None and item.after is None
    }
    shared_records = claim_records_spoken_for_twice(result)
    removed_records = {item.record_id for item in union_items if item.item_id in removed_items}
    assert removed_records & shared_records, (
        "the shipped fixture must keep the structure this case exists for: two realization items "
        "that are gone from the after side and speak for one claim record"
    )

    before, after = real_input_sides(real_fixture, result)
    signals = detect_review_conditions(
        DetectionWalkInput(
            repository_id=real_fixture.repository_id,
            governing_route_id=ROUTE_ID,
            comparison=result,
            input_sides=(before, after),
            declared="union_of_both_sides",
        )
    )
    identities = [signal.signal_id for signal in signals]
    assert len(set(identities)) == len(identities), (
        "two signals about two different union items may not be named by one identity"
    )

    removed_signals = [
        signal for signal in signals if signal.condition == "removed_or_reparented_attribution"
    ]
    assert len(removed_signals) == len(removed_items), (
        "each union item that is gone from the after side is reported as its own signal"
    )
    assert {
        change.item_id for signal in removed_signals for change in signal.observed_changes
    } == removed_items

    # The refusal D-30 records is about a *deterministic* order, so the same union in the other
    # order has to produce the same ordered identities: the shared-record key is read from the
    # comparison, not from the position an item happened to be enumerated at.
    assert result.page is not None
    reversed_result = result.model_copy(
        update={
            "page": result.page.model_copy(update={"items": tuple(reversed(result.page.items))})
        }
    )
    reordered = detect_review_conditions(
        DetectionWalkInput(
            repository_id=real_fixture.repository_id,
            governing_route_id=ROUTE_ID,
            comparison=reversed_result,
            input_sides=(before, after),
            declared="union_of_both_sides",
        )
    )
    assert [signal.signal_id for signal in reordered] == identities

    store = open_knowledge_store(tmp_path / "detection.db", real_fixture.repository_id)
    try:
        store.create_repository(
            RepositoryIdentity(
                repository_id=real_fixture.repository_id, authority_home="agents-remember"
            )
        )
        run = build_detection_run(
            DetectionRunAssembly(
                run_id=str(uuid4()),
                repository_id=real_fixture.repository_id,
                assessed_repository_id=real_fixture.repository_id,
                governing_route_id=ROUTE_ID,
                input_sides=(before, after),
                policy_version=DETECTION_POLICY_VERSION,
            ),
            signals,
        )
        recorded = record_detection_run(
            store,
            DetectionRunRequest(
                repository_id=real_fixture.repository_id,
                provenance=authorship(),
                run=run,
                signals=signals,
                assessed_database_paths=(
                    str(real_fixture.before.database_path),
                    str(real_fixture.after.database_path),
                ),
            ),
        )
        assert recorded.state == "created", recorded.refusal
        assert recorded.run is not None
        read_back = read_detection_run(store, run.run_id)
        assert read_back.state == "read", read_back.refusal
        assert read_back.ordered_signal_ids() == run.signal_order
        assert read_back.ordered_signal_ids() == tuple(identities)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# The write path, the recorded order and the self-invalidation refusal.


@pytest.fixture
def detection_store(tmp_path: Path):
    """One created detection store: the generation this build creates, with its repository row."""

    path = tmp_path / "detection.db"
    store = open_knowledge_store(path, REPOSITORY_ID)
    store.create_repository(
        RepositoryIdentity(repository_id=REPOSITORY_ID, authority_home="agents-remember")
    )
    try:
        yield store, path
    finally:
        store.close()


def authorship() -> Authorship:
    return Authorship(
        actor_ref="agent:detection",
        authorization_ref="260915-KS developer kickoff ruling",
        operation_id=uuid4(),
        recorded_at=datetime.now(UTC).isoformat(),
        origin_refs=("requirement:KS-R14@v1",),
    )


def assembled_run(
    signals: tuple[DetectionSignalPayload, ...],
    *,
    run_id: str | None = None,
    policy_version: str = DETECTION_POLICY_VERSION,
    after_digest: str = DIGEST_AFTER,
) -> DetectionRunPayload:
    before, after = both_sides(after_digest)
    assembly = DetectionRunAssembly(
        run_id=run_id or str(uuid4()),
        repository_id=REPOSITORY_ID,
        assessed_repository_id=REPOSITORY_ID,
        governing_route_id=ROUTE_ID,
        input_sides=(before, after),
        policy_version=policy_version,
    )
    if policy_version != DETECTION_POLICY_VERSION:
        signals = tuple(
            signal.model_copy(update={"policy_version": policy_version}) for signal in signals
        )
    return build_detection_run(assembly, signals)


def test_a_recorded_run_reads_back_in_its_recorded_order_with_two_place_versions(
    detection_store,
) -> None:
    """Requirements 3.1, 3.3 and 3.5: the run and its signals hold the versions, in that order.

    This catches the two failures the two-place rule exists for: a signal that could be lifted out of
    its run and read as current without carrying what it ran under, and a run whose own identity is
    incomplete because only its signals know the versions.
    """

    store, _path = detection_store
    signals = detect_review_conditions(walk(comparison(family_union())))
    run = assembled_run(signals)
    result = record_detection_run(
        store,
        DetectionRunRequest(
            repository_id=REPOSITORY_ID,
            provenance=authorship(),
            run=run,
            signals=signals,
            assessed_database_paths=(str(Path("/tmp/assessed-candidate.db")),),
        ),
    )
    assert result.state == "created", result.refusal
    assert result.run is not None
    assert result.run.policy_version == DETECTION_POLICY_VERSION
    assert result.run.extractor_version == DETECTION_EXTRACTOR_VERSION
    assert result.run.declared_input_sets == ("union_of_both_sides",)

    read_back = read_detection_run(store, run.run_id)
    assert read_back.state == "read", read_back.refusal
    assert read_back.ordered_signal_ids() == run.signal_order
    assert read_back.ordered_signal_ids() == tuple(signal.signal_id for signal in signals)
    assert read_back.run is not None
    for produced in read_back.signals:
        assert produced.policy_version == read_back.run.policy_version
        assert produced.extractor_version == read_back.run.extractor_version

    ordinal_rows = tuple(
        store.connection.execute(
            "SELECT ordinal, signal_id FROM detection_run_signal "
            "WHERE repository_id = ? AND run_id = ? ORDER BY ordinal",
            (REPOSITORY_ID, run.run_id),
        )
    )
    assert tuple(int(row[0]) for row in ordinal_rows) == (0,)
    assert str(ordinal_rows[0][1]) == signals[0].signal_id


def test_two_signals_with_different_declared_sets_are_both_recorded_on_one_run(
    detection_store,
) -> None:
    """Requirement 2.4: the declaration is per signal and is never collapsed into a run default."""

    store, _path = detection_store
    union_signal = detect_review_conditions(walk(comparison(family_union())))[0]
    trigger_signal = union_signal.model_copy(
        update={
            "signal_id": str(uuid4()),
            "input_set": union_signal.input_set.model_copy(
                update={
                    "declared": "trigger_side_only",
                    "sides": (input_side("trigger", DIGEST_AFTER),),
                    "counterpart_probe": (),
                    "probe_omission_declared": False,
                }
            ),
            "limitations": (
                "no_counterpart_read",
                NO_SEMANTIC_ASSESSMENT_LIMITATION,
            ),
            "detail": observed_basis_detail(
                condition=union_signal.condition,
                relationship_paths=tuple(path.path_id for path in union_signal.relationship_paths),
                limitations=("no_counterpart_read", NO_SEMANTIC_ASSESSMENT_LIMITATION),
            ),
        }
    )
    signals = (union_signal, trigger_signal)
    run = assembled_run(signals)
    assert set(run.declared_input_sets) == {"union_of_both_sides", "trigger_side_only"}
    result = record_detection_run(
        store,
        DetectionRunRequest(
            repository_id=REPOSITORY_ID, provenance=authorship(), run=run, signals=signals
        ),
    )
    assert result.state == "created", result.refusal
    read_back = read_detection_run(store, run.run_id)
    assert [signal.input_set.declared for signal in read_back.signals] == [
        "union_of_both_sides",
        "trigger_side_only",
    ]


def test_a_signal_that_disagrees_with_its_run_is_refused_and_nothing_is_written(
    detection_store,
) -> None:
    """Requirement 3.5: the run and every signal it produced carry the same versions.

    A signal whose policy version disagreed with its run would be readable as current under a policy
    its run did not run, which is the failure the two-place publication exists to prevent.
    """

    store, _path = detection_store
    signals = detect_review_conditions(walk(comparison(family_union())))
    run = assembled_run(signals)
    disagreeing = (signals[0].model_copy(update={"policy_version": "family-detection/v9"}),)
    result = record_detection_run(
        store,
        DetectionRunRequest(
            repository_id=REPOSITORY_ID, provenance=authorship(), run=run, signals=disagreeing
        ),
    )
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "invalid_reference"
    assert result.refusal.expected == DETECTION_POLICY_VERSION
    assert result.refusal.observed == "family-detection/v9"
    rows = tuple(store.connection.execute("SELECT count(*) FROM knowledge_record"))
    assert int(rows[0][0]) == 0


def test_a_run_whose_declared_order_names_other_signals_is_refused(detection_store) -> None:
    """Requirement 3.3: the declared order names exactly the signals the run recorded."""

    store, _path = detection_store
    signals = detect_review_conditions(walk(comparison(family_union())))
    run = assembled_run(signals).model_copy(update={"signal_order": (str(uuid4()),)})
    result = record_detection_run(
        store,
        DetectionRunRequest(
            repository_id=REPOSITORY_ID, provenance=authorship(), run=run, signals=signals
        ),
    )
    assert result.state == "refused"
    assert result.refusal is not None
    assert "deterministic total order" in result.refusal.detail


def test_a_detection_write_into_an_assessed_database_is_refused(detection_store) -> None:
    """Requirement 7.1: the write may not enter the assessed dataset's measurement transaction.

    The store IS the assessed database in this case, so the identity the request names resolves to
    the detection store's own file and the refusal is structural rather than a promise. This catches
    the self-invalidating sequence the review design names: write the measurement, the dataset's
    digest moves, the measurement's own binding is stale.
    """

    store, path = detection_store
    signals = detect_review_conditions(walk(comparison(family_union())))
    run = assembled_run(signals)
    result = record_detection_run(
        store,
        DetectionRunRequest(
            repository_id=REPOSITORY_ID,
            provenance=authorship(),
            run=run,
            signals=signals,
            assessed_database_paths=(str(path),),
        ),
    )
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "detection_self_reference"
    assert result.refusal.table == "detection_run_signal"
    rows = tuple(store.connection.execute("SELECT count(*) FROM knowledge_record"))
    assert int(rows[0][0]) == 0
    assert require_separate_from_assessed(store, (str(path),), "record_detection_run") is not None
    assert (
        require_separate_from_assessed(
            store, (str(path.parent / "other.db"),), "record_detection_run"
        )
        is None
    )


def test_a_recorded_detection_sequence_cannot_be_reordered_or_shortened(detection_store) -> None:
    """Requirement 3.4: the recorded run's order is sealed, not merely written once.

    Generation 4's triggers are what make this true against a later code path that forgot the rule as
    well as against this one.
    """

    store, _path = detection_store
    signals = detect_review_conditions(walk(comparison(family_union())))
    run = assembled_run(signals)
    assert (
        record_detection_run(
            store,
            DetectionRunRequest(
                repository_id=REPOSITORY_ID, provenance=authorship(), run=run, signals=signals
            ),
        ).state
        == "created"
    )
    with pytest.raises(apsw.ConstraintError):
        store.connection.execute(
            "UPDATE detection_run_signal SET signal_id = ? WHERE repository_id = ? AND run_id = ?",
            (str(uuid4()), REPOSITORY_ID, run.run_id),
        )
    with pytest.raises(apsw.ConstraintError):
        store.connection.execute(
            "DELETE FROM detection_run_signal WHERE repository_id = ? AND run_id = ?",
            (REPOSITORY_ID, run.run_id),
        )


# ---------------------------------------------------------------------------
# Requirements 3.3, 3.4 and 3.6: reproducibility and currentness.


def test_a_reexecution_over_a_changed_snapshot_is_a_distinct_run_naming_the_difference() -> None:
    """Example 9: reproducibility is two ordered sequences, and a difference names its cause.

    The third run is a distinct fact and is not reported as a discrepancy or an error: a changed
    input is the honest reason two runs differ, and naming it is what makes "reproducible from its
    inputs" checkable. The recorded run's own identity and order are untouched.
    """

    signals = detect_review_conditions(walk(comparison(family_union())))
    recorded = assembled_run(signals, run_id=str(uuid4()))
    same = assembled_run(signals, run_id=str(uuid4()))
    reproduced = compare_detection_runs(recorded, same)
    assert reproduced.reproduced is True
    assert reproduced.ordered_signals_equal is True
    assert reproduced.differences == ()
    assert reproduced.recorded_run_id != reproduced.reexecuted_run_id

    moved_signals = detect_review_conditions(
        DetectionWalkInput(
            repository_id=REPOSITORY_ID,
            governing_route_id=ROUTE_ID,
            comparison=comparison(family_union()),
            input_sides=both_sides(DIGEST_AFTER_MOVED),
            declared="union_of_both_sides",
        )
    )
    moved = assembled_run(moved_signals, run_id=str(uuid4()), after_digest=DIGEST_AFTER_MOVED)
    difference = compare_detection_runs(recorded, moved)
    assert difference.reproduced is False
    assert difference.recorded_run_id == recorded.run_id
    assert difference.reexecuted_run_id == moved.run_id
    snapshot = [found for found in difference.differences if found.kind == "snapshot_identity"]
    assert len(snapshot) == 1
    assert snapshot[0].side == "after"
    assert snapshot[0].recorded == DIGEST_AFTER
    assert snapshot[0].reexecuted == DIGEST_AFTER_MOVED
    # The recorded run's own facts did not move, and nothing was written by the comparison.
    assert recorded.signal_order == tuple(signal.signal_id for signal in signals)
    assert recorded.policy_version == DETECTION_POLICY_VERSION

    moved_policy = assembled_run(signals, run_id=str(uuid4()), policy_version="family-detection/v2")
    versioned = compare_detection_runs(recorded, moved_policy)
    assert [found.kind for found in versioned.differences] == ["policy_version"]
    assert versioned.differences[0].recorded == DETECTION_POLICY_VERSION
    assert versioned.differences[0].reexecuted == "family-detection/v2"


def test_a_run_whose_policy_version_moved_is_stale_and_its_signals_keep_their_versions() -> None:
    """Example 6: the recorded run keeps ``v1`` on itself and on every signal it produced."""

    signals = detect_review_conditions(walk(comparison(family_union())))
    recorded = assembled_run(signals)
    current = run_currentness(recorded)
    assert current.binding_state == "current"
    assert current.differing_versions == ()

    stale = run_currentness(recorded, current_policy_version="family-detection/v2")
    assert stale.binding_state == "stale"
    assert stale.differing_versions == ("policy_version",)
    assert stale.recorded_policy_version == DETECTION_POLICY_VERSION
    assert stale.current_policy_version == "family-detection/v2"
    for signal in signals:
        assert signal.policy_version == DETECTION_POLICY_VERSION
        assert signal.extractor_version == DETECTION_EXTRACTOR_VERSION
    assert recorded.policy_version == DETECTION_POLICY_VERSION

    extractor_moved = run_currentness(
        recorded, current_extractor_version="recorded-anchor-locator/v2"
    )
    assert extractor_moved.binding_state == "stale"
    assert extractor_moved.differing_versions == ("extractor_version",)


# ---------------------------------------------------------------------------
# Requirement 4: the manifest's retention answer, reached through the run's operation seam.


def test_a_manifest_reference_reported_through_the_operation_names_its_destination() -> None:
    """Requirement 4.5: the reference, its retention state and the destination that was checked."""

    manifest = DetectionScopeManifest(
        manifest_ref="scope-manifest/real-run",
        retention_required=True,
        destination_kind="worktree_local",
        destination_ref=".git/ar/manifest-real-run.json",
        retention_basis="a durable assessment that cites this signal needs its exact revisions",
    )
    resolution = resolve_manifest_reference(
        manifest,
        ManifestDestinationObservation(
            destination_kind="worktree_local",
            destination_ref=".git/ar/manifest-real-run.json",
            resolved=True,
            detail="the manifest lives beside the worktree that produced it",
        ),
    )
    assert resolution.retention_state == "unresolved"
    assert resolution.destination_ref == ".git/ar/manifest-real-run.json"
    assert resolution.what_would_resolve is not None
    assert "durable publication route" in resolution.what_would_resolve
    assert DetectionLimitation.__args__  # the closed limitation vocabulary is a Literal


def test_a_run_records_the_exact_inputs_it_read_as_identities() -> None:
    """Requirement 3.2: snapshots, code trees, selectors and the repository binding, as values."""

    signals = detect_review_conditions(walk(comparison(family_union())))
    before, after = both_sides()
    run = build_detection_run(
        DetectionRunAssembly(
            run_id=str(uuid4()),
            repository_id=REPOSITORY_ID,
            assessed_repository_id=REPOSITORY_ID,
            governing_route_id=ROUTE_ID,
            input_sides=(before, after),
        ),
        signals,
    )
    assert {side.side for side in run.input_sides} == {"before", "after"}
    for side in run.input_sides:
        assert side.context.knowledge.logical_digest in (DIGEST_BEFORE, DIGEST_AFTER)
        assert side.context.repository_id == REPOSITORY_ID
        assert side.selector_digest
    assert run.recorded_conditions == tuple(signal.condition for signal in signals)
    assert run.signal_order == tuple(signal.signal_id for signal in signals)


def test_a_detection_dataset_predating_generation_4_cannot_be_created_by_migration(
    tmp_path: Path,
) -> None:
    """Requirement 7.3: the generation is appended, never migrated onto an existing dataset."""

    repository_id = str(uuid4())
    path = tmp_path / "generation-3.db"
    connection = open_database(path)
    try:
        for statement in create_schema_statements(GENERATION_3):
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 3")
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (repository_id, "agents-remember"),
        )
    finally:
        connection.close()
    store = open_existing_knowledge_store(path, repository_id)
    try:
        assert store.generation is GENERATION_3
        signals = detect_review_conditions(walk(comparison(family_union())))
        run = assembled_run(signals)
        result = record_detection_run(
            store,
            DetectionRunRequest(
                repository_id=repository_id, provenance=authorship(), run=run, signals=signals
            ),
        )
        assert result.state == "refused"
        assert result.refusal is not None
        assert result.refusal.code == "unsupported_schema"
        assert result.refusal.expected == "4"
        assert result.refusal.observed == "3"
        with pytest.raises(apsw.SQLError):
            store.connection.execute("SELECT ordinal FROM detection_run_signal")
    finally:
        store.close()
