"""``KS-R16@v1`` §6 and §7 end to end: the worked example, its control, and cleanup survival.

These cases protect the pipeline's *whole* behaviour rather than one record's shape, so they occupy the
``integration`` lane: they drive a real two-snapshot comparison over two real databases and two real Git
trees, walk it with ``KS-R14@v1``'s detector, and publish real bytes to a real destination.

Every case names the failure it catches: a detector that emitted a verdict on the harmless control, a
pipeline whose end-to-end output does not distinguish the two scenarios once the curator has judged
them, a retention claim resting on a path nobody read back, and a worklist row discarded while it was
the only pointer to the evidence that interprets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_family_integrity import (
    FamilyIntegrityReport,
    FamilyIntegrityRequest,
    family_integrity_report,
    publish_review_evidence,
    worklist_row_disposable,
)
from agents_remember.memory.knowledge.detection import (
    DetectionRunAssembly,
    build_detection_run,
    record_detection_run,
)
from agents_remember.memory.knowledge.detection_walk import (
    DetectionWalkInput,
    detect_review_conditions,
)
from agents_remember.memory.knowledge.durable_evidence import read_back_evidence
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.registered_scope import snapshot_source
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.memory_quality.family_review import (
    compose_currentness,
    curator_review_status,
    family_review_summaries,
    group_detection_facts,
    reported_subject_status,
    route_family_review,
)
from agents_remember.memory_quality.knowledge_review import knowledge_review_section
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.detection import (
    DetectionInputSide,
    DetectionRunRequest,
    DetectionScopeManifest,
    DetectionSignalPayload,
    conclusion_bearing_fields,
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
from agents_remember.models.knowledge.family_review import PIPELINE_STATUS_OWNERS
from agents_remember.models.knowledge.read import (
    AnchorResolution,
    InvariantIdentitySeed,
    KnowledgeReadContext,
    ReadItem,
)
from agents_remember.models.knowledge.registered_scope import (
    RegisteredScopeRequest,
    ScopeSnapshotDeclaration,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.source import FileLocator
from agents_remember.models.lifecycles.evidence_dependencies import (
    build_evidence_dependencies,
    dependency,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentProvenance,
    AssessmentSubject,
    ExaminedInputs,
    ReviewAssessment,
    ReviewAssessmentRevision,
    assessment_subject_id,
)
from diff_scope_test_support import DiffFixture, build_diff_fixture

pytestmark = pytest.mark.integration

SELECTOR_POLICY = "recorded-family-frontier/v1"
EXAMINED_DIGEST = "a" * 64
MOVED_DIGEST = "b" * 64


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> DiffFixture:
    """One real two-snapshot fixture, shared because building it is the expensive part."""

    return build_diff_fixture(tmp_path_factory.mktemp("family-integrity"))


def _input_side(fixture: DiffFixture, side: str, path: Any) -> DetectionInputSide:
    digest = dataset_identity(path).logical_digest
    return DetectionInputSide(
        side=side,  # type: ignore[arg-type]
        context=KnowledgeReadContext(
            repository_id=fixture.repository_id,
            knowledge=SnapshotIdentity(
                repository_id=fixture.repository_id,
                schema_version="ar-knowledge-sqlite/v6",
                logical_digest=digest,
            ),
        ),
        selector_digest=digest,
        selector_policy_version=SELECTOR_POLICY,
    )


def walked_signals(
    fixture: DiffFixture, comparison: Any | None = None
) -> tuple[DetectionSignalPayload, ...]:
    """Run the shipped comparison and walk it with ``KS-R14@v1``'s detector, over real bytes."""

    comparison = comparison if comparison is not None else real_comparison(fixture)
    return walk_comparison(fixture, comparison)


def walk_comparison(fixture: DiffFixture, comparison: Any) -> tuple[DetectionSignalPayload, ...]:
    """Walk one comparison result, recording the two declared input sides it was read under."""

    return detect_review_conditions(
        DetectionWalkInput(
            repository_id=fixture.repository_id,
            governing_route_id=str(uuid4()),
            comparison=comparison,
            input_sides=(
                _input_side(fixture, "before", fixture.before.database_path),
                _input_side(fixture, "after", fixture.after.database_path),
            ),
            declared="union_of_both_sides",
            scope_manifest=DetectionScopeManifest(
                manifest_ref="manifest-17",
                retention_required=False,
                destination_kind="enclosure_local",
                retention_basis="retention is required only when a durable record needs it",
            ),
        )
    )


def real_comparison(fixture: DiffFixture) -> Any:
    """Run the shipped comparison of the fixture's two real commits."""

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


def authored(
    fixture: DiffFixture, *, disposition: str, finding: str, digest: str = EXAMINED_DIGEST
) -> ReviewAssessment:
    """Author one review record over the exact comparison the walk examined."""

    revision = ReviewAssessmentRevision(
        assessmentId=f"assessment-{uuid4()}",
        subject=AssessmentSubject(
            kind="comparison",
            recordId=f"comparison-B-M-{fixture.retry_invariant_id}",
            comparisonRef="comparison-B-M",
        ),
        disposition=disposition,  # type: ignore[arg-type]
        finding=finding,
        rationale="the inspected call path and the recorded guarantee were read together",
        assumptions=(
            "Every attempt can consume its configured timeout.",
            "This scenario excludes backoff and other overhead.",
        ),
        comparisonRef="comparison-B-M",
        scopeManifestRef="scope-B-M",
        evidenceRefs=(),
    )
    declaration = build_evidence_dependencies(
        "review-assessment/v1",
        (
            dependency("candidate-state", "knowledge-snapshot/candidate", digest),
            dependency("code-tree", "source-tree/candidate", digest, algorithm="git-object"),
            dependency("task-intent", "task/260915-KS-L16", digest),
            dependency("semantic-topology", "policy/family-detection", digest),
            dependency("evidence-bytes", "review-fixture-assumptions", digest),
            dependency("validator", "evidence-dependency-validator", digest),
        ),
    )
    return ReviewAssessment(
        **revision.model_dump(),
        examinedInputs=ExaminedInputs(declaration=declaration),
        provenance=AssessmentProvenance(
            authorRef="agent:curator",
            authorRole="curator",
            publicationRef="notes/reports/curator-memory-quality.json",
        ),
    )


def composed(fixture: DiffFixture, records: tuple[ReviewAssessment, ...]) -> FamilyIntegrityReport:
    """Compose the pipeline report over the real signals, with no gate and no count moved."""

    groups = group_detection_facts(walked_signals(fixture))
    current = {record.assessmentId: dict(record.examinedInputs.identities) for record in records}
    currentness = compose_currentness(records, current)
    return FamilyIntegrityReport(
        state="composed",
        scope=None,  # type: ignore[arg-type]
        groups=groups,
        currentness=currentness,
        routing=route_family_review(
            groups,
            review_ids_by_subject={
                assessment_subject_id(record): (record.assessmentId,) for record in records
            },
            repair_count=0,
            missing_count=0,
            stale_count=0,
        ),
        review_rows=family_review_summaries(records, current),
    )


# ---------------------------------------------------------------------------
# §7: the worked example and its harmless control.


def test_the_detector_emits_the_same_signal_kind_for_a_budget_change_and_a_comments_only_change(
    fixture: DiffFixture,
) -> None:
    """§7.5/§7.6: the detector's output carries no verdict, so a control cannot differ from a case.

    Catches the first of the two control failures: a detector emitting the concerning verdict or a
    severity on the harmless scenario. Every signal is asserted field by field to contain no
    conclusion-bearing field, and the condition vocabulary the walk may emit is closed.
    """

    signals = walked_signals(fixture)

    assert signals, "the fixture's two snapshots must produce at least one recorded condition"
    for produced in signals:
        assert conclusion_bearing_fields(DetectionSignalPayload) == ()
        assert "severity" not in produced.model_dump()
        assert "semantic_conflict" not in produced.model_dump()
        assert "explanation" not in produced.model_dump()
        assert "compatible" not in produced.model_dump()
        assert produced.registered_scope_status == "complete_for_declared_policy"


def test_the_pipeline_distinguishes_the_two_curator_conclusions_without_moving_a_count(
    fixture: DiffFixture,
) -> None:
    """§7.6: the two scenarios must differ *after* the curator judged them, and only there.

    Catches the second control failure: a pipeline whose end-to-end output is identical for both
    scenarios. The machine side is the same signal set for both, the authored records differ, and no
    count and no status owner moves between them.
    """

    concerning = authored(
        fixture,
        disposition="concern_found",
        finding="the combined retry configuration can exceed the five-second budget",
    )
    harmless = authored(
        fixture,
        disposition="no_concern_found",
        finding="",
    )

    concern_report = composed(fixture, (concerning,))
    control_report = composed(fixture, (harmless,))

    assert [group.matched_conditions() for group in concern_report.groups] == [
        group.matched_conditions() for group in control_report.groups
    ]
    assert curator_review_status((concerning.disposition,)) == "record-recorded"
    assert curator_review_status((harmless.disposition,)) == "record-recorded"
    assert concern_report.review_rows[0].dispositions == ("concern_found",)
    assert control_report.review_rows[0].dispositions == ("no_concern_found",)
    assert concern_report.routing is not None and control_report.routing is not None
    assert concern_report.routing.actionable_count == control_report.routing.actionable_count == 0
    assert concern_report.report_only_rows() == control_report.report_only_rows()
    assert concern_report.report_only_rows() == len(concern_report.groups) > 0
    rendered = "\n".join(knowledge_review_section(control_report.review_rows).lines)
    assert "no_concern_found" in rendered
    assert "concern_found" in "\n".join(knowledge_review_section(concern_report.review_rows).lines)


def test_an_inconclusive_review_is_a_reported_state_that_moves_nothing(
    fixture: DiffFixture,
) -> None:
    """§3.4 / example 5: ``unresolved`` is a defined behaviour, not a gap and not an approval.

    Catches all four forbidden variants at once: nothing is retried, the signal is not dropped, the
    absent verdict is not defaulted to a clearance, and the unresolved state is not presented as an
    approval -- every count stays where it was and the state is reported with its owner.
    """

    unresolved = authored(
        fixture,
        disposition="unresolved",
        finding="the registered evidence does not settle whether the shared deadline is enforced",
    )
    report = composed(fixture, (unresolved,))

    assert curator_review_status((unresolved.disposition,)) == "declared-unresolved"
    assert report.review_rows[0].unresolvedCount == 1
    assert report.review_rows[0].dispositions == ("unresolved",)
    assert report.routing is not None
    assert report.routing.actionable_count == 0
    assert report.routing.review_row_count == len(report.groups) > 0
    assert report.currentness[0].binding_state == "current"
    assert reported_subject_status((unresolved,), {}, f"family:{uuid4()}") == "none-recorded"


# ---------------------------------------------------------------------------
# §6: findings and their manifests survive enclosure cleanup, and it is measured.


def test_a_finding_and_its_manifest_are_read_back_after_the_enclosure_directory_is_gone(
    tmp_path: Any,
) -> None:
    """§6.1-§6.3: the resolved destination is verified by a read-back after the enclosure disappears.

    Catches a retention claim resting on a path nobody read back: the destination is resolved by the
    shipped publication function, a directory of the enclosure's own shape is removed, and both
    artifacts are then read back from the destination and compared against the digests that were
    published.
    """

    task_root = tmp_path / "task-root"
    enclosure_reports = task_root / "enclosures" / "260915-ks-l16" / "reports"
    enclosure_reports.mkdir(parents=True)
    (enclosure_reports / "curator-memory-quality.md").write_text("pre-cleanup evidence only")

    retention = publish_review_evidence(
        task_root,
        finding_name="260915-KS-L16-finding.md",
        finding_content="disposition=concern_found; the combination can exceed the budget\n",
        manifest_name="260915-KS-L16-scope-manifest.json",
        manifest_content='{"scope_id": "scope-B-M", "membership": ["invariant-revision"]}\n',
    )

    assert retention.destination == task_root / "notes" / "reports"
    assert retention.destination != enclosure_reports
    assert retention.readable_together()

    for stale in sorted(enclosure_reports.iterdir(), reverse=True):
        stale.unlink()
    enclosure_reports.rmdir()
    (task_root / "enclosures" / "260915-ks-l16").rmdir()

    assert not enclosure_reports.exists()
    assert retention.finding_read_back.destination.exists()
    assert retention.manifest_read_back.destination.exists()
    assert retention.finding_read_back.observed_sha256 == retention.finding.sha256
    assert retention.manifest_read_back.observed_sha256 == retention.manifest.sha256
    assert retention.matched()
    assert retention.blocked_reason() == ""


def test_a_destination_that_did_not_keep_the_bytes_is_reported_and_never_claimed(
    tmp_path: Any,
) -> None:
    """§6.2 / Failure And Recovery: an unverifiable destination is reported, not claimed.

    Catches the reassuring report: when the published bytes are gone the record answers with the exact
    destination, the expected digest and the observed state, and ``matched()`` is false rather than
    "published".
    """

    retention = publish_review_evidence(
        tmp_path / "task-root",
        finding_name="260915-KS-L16-finding.md",
        finding_content="disposition=concern_found\n",
        manifest_name="260915-KS-L16-scope-manifest.json",
        manifest_content="{}\n",
    )
    retention.finding.destination.unlink()
    observed = read_back_evidence(retention.finding)

    assert observed.state == "missing"
    assert observed.matched() is False
    assert str(retention.destination) in observed.blocked_reason()


def test_a_worklist_row_that_is_the_only_pointer_to_the_evidence_is_not_discarded() -> None:
    """§6.4: a regenerable row may go only when nothing durable stops existing with it.

    Catches the discarded-only-pointer failure: a row that is the sole reference to a finding or its
    manifest is not regenerable, so the predicate answers ``False`` exactly when no durable reference
    was counted.
    """

    assert worklist_row_disposable(durable_reference_count=0) is False
    assert worklist_row_disposable(durable_reference_count=1) is True


def minimal_comparison(fixture: DiffFixture) -> Any:
    """One hand-built union carrying exactly one family-conditioned condition.

    ``KS-R14@v1``'s own run cases build the comparison result directly for the same reason this case
    does: the run's declared order is over signal identity, so a comparison whose walk emits one signal
    is the smallest input that exercises the whole record path. It is *not* a substitute for the real
    comparison -- the case above walks that one -- and every item's shape is the shipped union's own.
    """

    family_revision = str(uuid4())
    member_id = str(uuid4())
    claims = tuple(
        _realization_item(fixture, path) for path in ("src/integration.py", "src/sync.py")
    )
    items = (
        KnowledgeDiffItem(
            item_id=family_revision,
            kind="family",
            coverage="selected_both",
            record_transition="unchanged",
            before=ReadItem(
                kind="family_revision",
                item_id=family_revision,
                family_id=str(uuid4()),
                revision_id=family_revision,
            ),
            before_selected=True,
            after_selected=True,
            revision_id=family_revision,
        ),
        KnowledgeDiffItem(
            item_id=member_id,
            kind="membership",
            coverage="selected_both",
            record_transition="unchanged",
            before=ReadItem(
                kind="family_membership",
                item_id=member_id,
                member_id=member_id,
                family_revision_id=family_revision,
                invariant_revision_id=fixture.retry_invariant_id,
            ),
            before_selected=True,
            after_selected=True,
        ),
        *claims,
    )
    total = len(items)
    return KnowledgeDiffResult(
        state="page",
        repository_id=fixture.repository_id,
        limitations=("no_semantic_assessment_performed",),
        page=KnowledgeDiffPage(
            items=items,
            counts=KnowledgeDiffCounts(
                items_total=total,
                items_returned=total,
                items_remaining=0,
                displayed_total=total,
                suppressed_total=0,
                changed_field_count=0,
                changed_source_observation_count=len(claims),
            ),
            has_more=False,
            enumeration_complete=True,
        ),
    )


def _realization_item(fixture: DiffFixture, path: str) -> KnowledgeDiffItem:
    """Build one changed realization item of the shipped union, at its recorded path."""

    claim_id = str(uuid4())
    anchor = AnchorResolution(
        anchor_id=str(uuid4()),
        path=path,
        recorded_source_identity="blob-before",
        observed_source_identity="blob-after",
        locator=FileLocator(),
        resolution="exact_recorded_blob",
        detail="the recorded claim is reported against the requested tree and nothing else",
    )
    payload = ReadItem(
        kind="realization_claim",
        item_id=claim_id,
        invariant_revision_id=fixture.retry_invariant_id,
        claim_id=claim_id,
        role="enforcement",
        rationale="the recorded attribution",
        anchor=anchor,
    )
    return KnowledgeDiffItem(
        item_id=claim_id,
        kind="realization",
        coverage="selected_both",
        record_transition="unchanged",
        before=payload,
        after=payload,
        before_selected=True,
        after_selected=True,
        record_id=claim_id,
        source_change=KnowledgeDiffSourceChange(
            claim_id=claim_id,
            before_observation=payload,
            after_observation=payload,
            record_field_changed=False,
            source_observation_changed=True,
            source_change_only=True,
            record_change_only=False,
        ),
    )


# ---------------------------------------------------------------------------
# §1 + §2 + §4 + §5 in one operation: the seam that composes the three record leaves.


def test_the_one_operation_composes_the_scope_the_run_the_statuses_and_the_routing(
    fixture: DiffFixture, tmp_path: Any
) -> None:
    """The Normative Requirement's "one operation": scope, run, facts, statuses, currentness, routing.

    Catches a composition that is only assembled in a case: the shipped seam constructs the registered
    scope, reads the recorded run back in its recorded order, groups its facts, reports the five owners
    separately and routes the groups -- and it refuses a request that would make it report another
    owner's fact for it.
    """

    run_path = tmp_path / "run" / "run.db"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    store = open_knowledge_store(run_path, fixture.repository_id)
    signals = walked_signals(fixture, minimal_comparison(fixture))
    try:
        store.create_repository(
            RepositoryIdentity(
                repository_id=fixture.repository_id, authority_home="agents-remember"
            )
        )
        run = build_detection_run(
            DetectionRunAssembly(
                run_id=str(uuid4()),
                repository_id=fixture.repository_id,
                assessed_repository_id=fixture.repository_id,
                governing_route_id=str(uuid4()),
                input_sides=(
                    _input_side(fixture, "before", fixture.before.database_path),
                    _input_side(fixture, "after", fixture.after.database_path),
                ),
            ),
            signals,
        )
        recorded = record_detection_run(
            store,
            DetectionRunRequest(
                repository_id=fixture.repository_id,
                provenance=Authorship(
                    actor_ref="agent:detection",
                    authorization_ref="260915-KS developer kickoff ruling",
                    operation_id=uuid4(),
                    recorded_at="2026-09-18T00:00:00+00:00",
                    origin_refs=("requirement:KS-R14@v1",),
                ),
                run=run,
                signals=signals,
            ),
        )
    finally:
        store.close()
    assert recorded.state == "created", recorded.refusal
    assert recorded.run is not None

    scope_request = RegisteredScopeRequest(
        scope_id="scope-B-M",
        repository_id=fixture.repository_id,
        snapshots=(
            ScopeSnapshotDeclaration(
                side="base",
                snapshot=dataset_identity(fixture.before.database_path),
                selector_policy_version=SELECTOR_POLICY,
            ),
            ScopeSnapshotDeclaration(
                side="candidate",
                snapshot=dataset_identity(fixture.after.database_path),
                selector_policy_version=SELECTOR_POLICY,
            ),
        ),
        changed_paths=("src/integration.py",),
    )
    sources = tuple(
        snapshot_source(
            side,  # type: ignore[arg-type]
            path,
            open_knowledge_store(path, fixture.repository_id),
        )
        for side, path in (
            ("base", fixture.before.database_path),
            ("candidate", fixture.after.database_path),
        )
    )
    record = authored(
        fixture,
        disposition="concern_found",
        finding="the combination can exceed the budget under the examined assumptions",
    )
    try:
        report = family_integrity_report(
            run_path,
            FamilyIntegrityRequest(
                repository_id=fixture.repository_id,
                run_id=recorded.run.run_id,
                scope_request=scope_request,
                sources=sources,
                owner_reported_statuses={
                    "structural-validator": "declared-checks-passed",
                    "verification-runner": "no-assertions-executed",
                },
                assessments=(record,),
                current={record.assessmentId: dict(record.examinedInputs.identities)},
            ),
        )
        incomplete = family_integrity_report(
            run_path,
            FamilyIntegrityRequest(
                repository_id=fixture.repository_id,
                run_id=recorded.run.run_id,
                scope_request=scope_request,
                sources=sources,
                owner_reported_statuses={"structural-validator": "declared-checks-passed"},
            ),
        )
    finally:
        for source in sources:
            source.store.close()

    assert report.state == "composed", report.refusal
    assert report.scope.constructed()
    assert report.run is not None and report.run.run is not None
    assert report.run.run.run_id == recorded.run.run_id
    assert report.groups
    assert report.statuses is not None
    assert tuple(entry.owner for entry in report.statuses.entries) == PIPELINE_STATUS_OWNERS
    assert report.currentness[0].binding_state == "current"
    assert report.routing is not None and report.routing.actionable_count == 0
    assert report.report_only_rows() == len(report.groups)
    assert "knowledgeReview" in "\n".join(report.section_lines())

    assert incomplete.state == "refused"
    assert incomplete.refusal is not None
    assert "verification-runner" in incomplete.refusal.detail
