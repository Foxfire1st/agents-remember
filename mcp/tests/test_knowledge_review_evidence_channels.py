"""The review port preserves curator records and states retired canonical channels truthfully.

These cases use the existing real converted-tree enclosure fixture. The immutable curator owner
publishes assessments; its absence, damaged authority and moved input remain distinct observable
states. Canonical detection, observation and claim readers are retired with their writers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from agents_remember.application.review_assessment_currentness import (
    comparison_currentness_measurement,
    currentness_channel,
)
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_evidence_records import (
    review_records_for,
    review_records_for_resolution,
)
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.knowledge.review import (
    KnowledgeReviewPayload,
    ReviewEvidencePane,
    ReviewRecordChannel,
)
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRequest,
    CuratorSourceCandidate,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceReference,
    AssessmentSubject,
    ReviewAssessmentRevision,
)
from agents_remember.models.lifecycles.review_assessment_binding import (
    no_currentness_measurement,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    curator_coherence_paths,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from curator_coherence_test_support import write_curator_task_topology
from pydantic import ValidationError
from test_review_git_trees import INVARIANT, World, _resolve, _seed, build_world, commit
from test_worktree_support import write_passing_route_review

pytestmark = [pytest.mark.evidence_unit, pytest.mark.usefixtures("worktree_services")]

REPOSITORY_AUTHORITY_HOME = "agents-remember"
EVIDENCE_RELATIVE = "notes/reports/icr-l14-evidence.md"
EVIDENCE_TEXT = "# fixture evidence\n\nthe record this assessment cites\n"
ASSESSMENT_ID = "ICR-L14-AS-1"
ASSESSMENT_FINDING = "The retry budget is still shared, and the candidate moved the constant."


# The one path a later change lands, so "the candidate moved after the assessment was published" is a
# measurement about a named file rather than a claim about a digest.
MOVED_PATH = "src/icr_l15_moved.py"
MOVED_TEXT = "# a change that landed after the assessment was published\n"


@dataclass(frozen=True)
class RecordedFixture:
    """One converted tree enclosure and its curator-published assessment."""

    endpoint: World
    contract: WorktreeContract
    sprint: TaskDocumentRef

    @property
    def candidate_database(self) -> Path:
        return _resolve(self.endpoint).candidate_database

    @property
    def candidate_tree_id(self) -> str:
        configured = _resolve(self.endpoint).candidate_code_tree_id
        assert configured is not None
        return configured

    def records(self):
        return review_records_for(self.endpoint.config, self.endpoint.review(selector=_seed()))

    def payload(self) -> KnowledgeReviewPayload:
        result = review_through_port(self.endpoint, self.endpoint.review(selector=_seed()))
        assert result.payload is not None
        return result.payload


def review_through_port(endpoint: World, request):
    """Read through the production dashboard port over the real converted memory tree."""

    port = serving_collaborators(endpoint.config).knowledge_review
    assert port is not None
    result = port(request)
    assert result.state == "review", result.refusal
    return result


@pytest.fixture
def recorded(tmp_path: Path) -> RecordedFixture:
    return build_recorded_fixture(tmp_path / "records")


def build_recorded_fixture(directory: Path) -> RecordedFixture:
    endpoint = build_world(directory)
    contract = load_contract(endpoint.contract())
    sprint = write_curator_task_topology(contract)
    write_passing_route_review(contract)
    _publish_assessment(contract, sprint, endpoint)
    return RecordedFixture(endpoint=endpoint, contract=contract, sprint=sprint)


def _publish_assessment(
    contract: WorktreeContract, sprint: TaskDocumentRef, endpoint: World
) -> None:
    """Publish one assessment for this candidate through the curator-coherence authority."""

    evidence = contract.task_root / EVIDENCE_RELATIVE
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(EVIDENCE_TEXT, encoding="utf-8")
    prepared = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="prepare", contract_path=contract.contract_path.as_posix()),
    )
    candidates = list(prepared["candidates"])  # type: ignore[arg-type]
    published = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(
            action="publish",
            contract_path=contract.contract_path.as_posix(),
            semantic_requirement_revision="ICR-R14@v1",
            delivery_attempt="A002",
            judgments=[
                CuratorCoherenceJudgment(
                    **CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json"),
                    disposition="reconciled",
                    rationale="The fixture candidate has one explicit reconciliation judgment.",
                    evidenceRef=f"task:{EVIDENCE_RELATIVE}",
                )
                for candidate in candidates
            ],
            review_assessments=[_assessment_revision(endpoint)],
            expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
            expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
            expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
            expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
            expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
            expected_attestation_sha256=str(prepared["attestationSha256"]),
            caller=DeclaredCaller(role="architect", task_document_ref=sprint),
        ),
    )
    assert published["state"] in {"published", "already-current"}, published


def _assessment_revision(endpoint: World) -> ReviewAssessmentRevision:
    """One authored assessment over the candidate's own reviewed revision pair."""

    return ReviewAssessmentRevision(
        assessmentId=ASSESSMENT_ID,
        subject=AssessmentSubject(
            kind="invariant-revision",
            recordId=text_uuid("identity", INVARIANT),
            beforeRevisionIds=(text_uuid("revision", f"{INVARIANT}@1"),),
            afterRevisionIds=(text_uuid("revision", f"{INVARIANT}@1"),),
        ),
        disposition="concern_found",
        finding=ASSESSMENT_FINDING,
        rationale="The candidate moves the retry budget without an earlier shared deadline.",
        evidenceRefs=(AssessmentEvidenceReference(namespace="task", ref=EVIDENCE_RELATIVE),),
        comparisonRef="icr-l14-comparison",
        scopeManifestRef="icr-l14-scope",
    )


def _channels(payload: KnowledgeReviewPayload) -> dict[str, ReviewRecordChannel]:
    return {channel.records: channel for channel in payload.evidence.channels}


def test_an_unpublished_authority_is_a_measured_absence_and_a_corrupt_one_is_unavailable(
    tmp_path: Path,
) -> None:
    """The two states F09 collapsed: the owner answered "none", versus bytes that cannot be read."""

    recorded = build_recorded_fixture(tmp_path / "distinct")
    authority = curator_coherence_paths(recorded.contract).canonical
    intact = authority.read_bytes()

    authority.unlink()
    absent = _channels(recorded.payload())["assessments"]
    assert absent.state == "none_recorded"
    assert absent.record_count == 0
    assert "absent" in absent.detail

    authority.write_text("{ not the authority this candidate published", encoding="utf-8")
    corrupt = _channels(recorded.payload())["assessments"]
    assert corrupt.state == "unavailable"
    assert corrupt.record_count is None
    assert corrupt.unreadable == (authority.name,)
    assert corrupt.next_action
    assert authority.name in corrupt.detail

    authority.write_bytes(intact)
    restored = _channels(recorded.payload())["assessments"]
    assert restored.state == "recorded"
    assert restored.record_count == 1


def test_a_task_context_review_reports_the_matrix_collection_as_not_selected(
    recorded: RecordedFixture,
) -> None:
    """A review that read no matrix says so, instead of reporting an absence it never asked about."""

    payload = recorded.payload()
    # A subject review asked the matrix, and the matrix holds nothing: a measured absence.
    assert _channels(payload)["authored_effects"].state == "none_recorded"

    result = review_through_port(recorded.endpoint, recorded.endpoint.review())
    assert result.payload is not None
    channels = _channels(result.payload)
    authored = channels["authored_effects"]
    assert authored.state == "not_selected"
    assert authored.record_count is None
    assert authored.next_action
    assert result.payload.knowledge.authored_effects == ()
    assert result.payload.evidence.evidence_links == ()
    assert channels["assessments"].state == "recorded"


def test_the_composition_measures_the_bindings_it_reads(recorded: RecordedFixture) -> None:
    """``ICR-R15@v1``: a measured world, and the identities it publishes no value for named.

    The measurement is the composition's own -- not a mapping whose presence stands for one -- and
    the two facts it carries are asserted against the store: it holds a value for the comparison's
    own candidate endpoint (so it measured something real), and the identities it publishes no value
    for are named on the channel instead of being read as agreement. The stored assessment is
    therefore reported ``not-measured``: its declaration is wider than what this comparison
    publishes, and neither currency nor movement may be claimed from a partial measurement.
    """

    records = recorded.records()
    assert records.currentness is not None
    assert records.currentness.state == "measured"
    assert ("code-tree", "candidate") in records.currentness.values
    assert records.currentness.values[("code-tree", "candidate")][1] == (recorded.candidate_tree_id)

    channels = {channel.records: channel for channel in records.channels}
    for name in ("detection_signals", "verification_observations", "evidence_claims"):
        channel = channels[name]
        assert channel.state == "unavailable" and channel.record_count is None
        assert "retired" in channel.detail and "Converted memory trees" in channel.detail
        assert channel.next_action and channel.owner.startswith("MIK-R26:")
    assert records.signals == records.observations == records.claims == ()
    currentness = channels["assessment_currentness"]
    assert currentness.state == "recorded"
    assert currentness.record_count == 1
    assert currentness.record_count == channels["assessments"].record_count
    # The declared identities this comparison publishes no value for are named, not absorbed.
    assert "candidate-state:knowledge-candidate-pair" in currentness.unreadable
    assert "memory-tree:candidate" in currentness.unreadable
    assert "semantic-topology:registered-scope" in currentness.unreadable
    assert "task-intent:requirement-identities" in currentness.unreadable
    assert "not measured" in currentness.detail

    payload = recorded.payload()
    # The pane's summary agrees with its retired channels: no evidence was counted, so it is not
    # reported as a measured zero.
    assert payload.evidence.evidence_state == "unavailable"
    assert [row.assessment_id for row in payload.evidence.assessments] == [ASSESSMENT_ID]
    assert payload.evidence.assessments[0].binding_state == "not-measured"
    assert all(row.binding_state != "current" for row in payload.evidence.assessments)
    assert _channels(payload)["assessment_currentness"].state == "recorded"


def _move_the_candidate(recorded: RecordedFixture) -> None:
    """Land one real change in the leaf's worktree, so the captured candidate tree moves.

    A commit alone does not move the *content* the capture binds -- the capture is the add-all tree --
    so the change is a new eligible file that is then committed, which is what a later task landing on
    the leaf's branch really does.
    """

    worktree = recorded.endpoint.code_worktree
    added = worktree / MOVED_PATH
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text(MOVED_TEXT, encoding="utf-8")
    commit(worktree, {})


def test_a_moved_candidate_marks_the_stored_assessment_stale_on_the_measured_axis(
    recorded: RecordedFixture,
) -> None:
    """The packet's conforming example, through the production port: changing source is a movement.

    The assessment was published against this candidate's captured tree. One landed change later the
    comparison the review renders binds a different tree, and the measurement says so -- by name --
    while the record's disposition, author and examined inputs stay exactly what their author wrote.
    """

    assert recorded.payload().evidence.assessments[0].binding_state == "not-measured"

    published_tree = recorded.candidate_tree_id
    _move_the_candidate(recorded)
    assert recorded.candidate_tree_id != published_tree

    stale = recorded.payload().evidence.assessments[0]
    assert stale.binding_state == "stale"
    assert stale.assessment_id == ASSESSMENT_ID
    assert stale.disposition == "concern_found"
    assert stale.role_ref == "architect"
    assert stale.author_ref
    assert stale.examined_inputs
    assert stale.finding == ASSESSMENT_FINDING


def test_a_retained_curator_generation_survives_a_damaged_live_authority(
    recorded: RecordedFixture,
) -> None:
    """Explicit immutable-owner recovery keeps the authored assessment and its evidence."""

    authority = curator_coherence_paths(recorded.contract).canonical
    digest = json.loads(authority.read_text())["currentRecordDigest"]
    before = recorded.records()
    authority.write_bytes(b"corrupt")
    assert recorded.records().assessments == ()
    recovered = review_records_for_resolution(
        _resolve(recorded.endpoint), curator_record_digest=digest
    )
    assert recovered.assessments == before.assessments
    assert recovered.artifacts == before.artifacts
    channel = next(row for row in recovered.channels if row.records == "assessments")
    assert channel.state == "recorded" and channel.record_count == 1


def test_an_unreadable_authority_reports_the_measurement_unavailable(tmp_path: Path) -> None:
    """A failed measurement is its own state: nothing is promoted to current or to stale by it."""

    recorded = build_recorded_fixture(tmp_path / "unmeasurable")
    curator_coherence_paths(recorded.contract).canonical.write_bytes(b"corrupt")

    payload = recorded.payload()
    channels = _channels(payload)

    assert channels["assessments"].state == "unavailable"
    currentness = channels["assessment_currentness"]
    assert currentness.state == "unavailable"
    assert currentness.record_count is None
    assert currentness.next_action
    assert payload.evidence.assessments == ()


def test_an_unresolvable_candidate_reports_every_collection_unavailable(tmp_path: Path) -> None:
    """A candidate that does not resolve supplies no records and never claims to have read none."""

    endpoint = build_world(tmp_path / "absent")
    endpoint.contract().unlink()
    bundle = review_records_for(endpoint.config, endpoint.review(selector=_seed()))

    assert bundle.assessments == ()
    assert bundle.signals == ()
    assert bundle.observations == ()
    assert {channel.state for channel in bundle.channels} <= {"unavailable", "not_measured"}
    assert len(bundle.channels) == len({channel.records for channel in bundle.channels})


def test_the_channel_model_refuses_a_count_no_owner_measured() -> None:
    """The availability vocabulary itself refuses a state that would read as a measured zero."""

    with pytest.raises(ValueError, match="measured no count"):
        ReviewRecordChannel(
            records="assessments",
            state="unavailable",
            owner="curator_coherence.load_curator_coherence_authority",
            record_count=0,
            detail="the authority could not be read",
        )
    with pytest.raises(ValueError, match="must name what would produce one"):
        ReviewRecordChannel(
            records="assessments",
            state="unavailable",
            owner="curator_coherence.load_curator_coherence_authority",
            detail="the authority could not be read",
        )
    with pytest.raises(ValueError, match="none_recorded"):
        ReviewRecordChannel(
            records="assessments",
            state="recorded",
            owner="curator_coherence.load_curator_coherence_authority",
            record_count=0,
            detail="no assessment was supplied",
        )


def test_the_currentness_collection_has_exactly_three_product_states() -> None:
    """A resolved candidate is always measured, so this collection never reports ``not_measured``.

    ``comparison_currentness_measurement`` holds a value for the two validator identities the shipped
    assessment publication declares whatever else a resolution binds, so its state is always
    ``measured`` -- the identities it holds vary, the state does not. The channel therefore states one
    of ``recorded`` / ``none_recorded`` / ``unavailable``, and a measurement nobody performed is
    refused loudly rather than rendered as a comparison that never happened.
    """

    bare = ReviewCandidateResolution(
        repository_id="repo-a",
        leaf_id="",
        baseline_database=Path("/nonexistent-baseline.sqlite"),
        candidate_database=Path("/nonexistent-candidate.sqlite"),
        baseline_code_root=None,
        candidate_code_root=None,
        baseline_code_tree_id=None,
        candidate_code_tree_id=None,
    )
    measurement = comparison_currentness_measurement(bare)
    assert measurement.state == "measured"
    assert {kind for kind, _name in measurement.values} == {"validator"}
    assert "validator:curator-evidence-resolver/v1" in measurement.detail

    collection = ReviewRecordChannel(
        records="assessments",
        state="recorded",
        owner="curator_coherence.load_curator_coherence_authority",
        record_count=1,
        detail="one assessment supplied by its owner",
    )
    recorded = currentness_channel(collection, measurement, ())
    assert recorded.state == "recorded"
    assert recorded.record_count == 1

    with pytest.raises(AssertionError, match="always measures a resolved candidate"):
        currentness_channel(
            collection,
            no_currentness_measurement("nothing measured this comparison"),
            (),
        )


def test_the_evidence_summary_cannot_report_a_zero_beside_an_unread_evidence_class() -> None:
    """``none_recorded`` is a measured zero, so the summary state follows its evidence channels."""

    def pane(
        state: str, channel_state: str, records: str = "evidence_claims"
    ) -> ReviewEvidencePane:
        counted = channel_state == "none_recorded"
        return ReviewEvidencePane.model_validate(
            {
                "evidence_state": state,
                "assessment_state": "unassessed",
                "source_inspection_available": True,
                "channels": [
                    {
                        "records": records,
                        "state": channel_state,
                        "owner": "owner",
                        "record_count": 0 if counted else None,
                        "detail": "detail",
                        "next_action": None if counted else "read the class another way",
                    }
                ],
            }
        )

    assert pane("unavailable", "unavailable").evidence_state == "unavailable"
    assert pane("none_recorded", "none_recorded").evidence_state == "none_recorded"
    # A class the summary does not count leaves it a measured zero.
    assert pane("none_recorded", "unavailable", "detection_signals").evidence_state == (
        "none_recorded"
    )
    for state, channel_state in (
        ("none_recorded", "unavailable"),
        ("unavailable", "none_recorded"),
    ):
        with pytest.raises(ValidationError, match="agrees with its channels"):
            pane(state, channel_state)


def test_the_wire_payload_carries_the_channels() -> None:
    """The channels are part of the served payload's own schema, not a local return value."""

    fields = set(KnowledgeReviewPayload.model_fields)
    assert "evidence" in fields
    schema = json.dumps(KnowledgeReviewPayload.model_json_schema())
    assert "ReviewRecordChannel" in schema
    assert "not_measured" in schema
