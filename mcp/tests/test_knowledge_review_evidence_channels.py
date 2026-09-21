"""The production composition supplies every owner-produced record class (``ICR-R14@v1``).

F09's defect is one omission with three faces: the dashboard's record loader supplied only the
curator assessments, collapsed "the authority published none" and "the authority could not be read"
into the same empty tuple, and never supplied the detector signals, the verification observations or
any statement of the currentness nobody measured. These cases drive the **production composition** --
``cli.dashboard.serving_collaborators``, the same port ``create_app`` is given -- over a real leaf
enclosure with real datasets, and they produce every record class through the operation that owns it
rather than assembling a payload:

* detector signals through ``detection.record_detection_run``;
* a verification observation and an evidence claim through the application evidence writer;
* an authored effect through the candidate batch operation;
* assessments through the curator-coherence publication.

The load-bearing properties, one case each:

* every available class arrives in the payload the port returns, with the identities, artifact
  references, examined inputs and scope limitations its owner recorded;
* a published authority holding no assessment is a **measured absence** (``none_recorded``) and an
  authority whose bytes cannot be read is **unavailable** with its own provenance -- the two states
  the defect collapsed, shown side by side on the same fixture;
* one unreadable authority does not withdraw the classes that were readable, which is the packet's
  own boundary example;
* a task-context review reports the two matrix-sourced collections ``not_selected`` rather than
  absent, because it never asked the matrix anything;
* no measurement is reported as one: the bundle carries no dependency-currentness measurement, says
  ``not_measured``, and no displayed assessment is promoted to current on the strength of an empty
  measurement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge import (
    admitted_evidence_request,
    admitted_knowledge_destination,
    change_knowledge_candidate,
    resolve_candidate_context,
    write_authorship,
    write_knowledge_evidence,
)
from agents_remember.application.knowledge_diff import open_diff_side
from agents_remember.application.review_evidence_records import review_records_for
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.memory.knowledge.detection import (
    DetectionRunAssembly,
    build_detection_run,
    record_detection_run,
)
from agents_remember.memory.knowledge.schema_v2 import APPENDED_TRIGGERS
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    AddSemanticChangeSet,
    AddUnresolvedQuestion,
    CandidateResolution,
    ChangeBatch,
)
from agents_remember.models.knowledge.detection import (
    DetectionInputSide,
    DetectionRecordedInputSet,
    DetectionRelationshipPath,
    DetectionRunRequest,
    DetectionScopeManifest,
    DetectionSignalPayload,
)
from agents_remember.models.knowledge.evidence import (
    AddEvidenceClaim,
    AddVerificationObservation,
    AnchorCoverage,
    EvidenceClaimPayload,
    InvariantRevisionSubject,
    ResultArtifactReference,
    RunEnvironment,
    VerificationObservationPayload,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.review import (
    KnowledgeReviewPayload,
    ReviewRecordChannel,
)
from agents_remember.models.knowledge.snapshot import SnapshotIdentity
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
from test_knowledge_review_source_endpoints import EndpointFixture, build_endpoint_fixture
from test_worktree_support import write_passing_route_review

pytestmark = pytest.mark.evidence_unit

REPOSITORY_AUTHORITY_HOME = "agents-remember"
EVIDENCE_RELATIVE = "notes/reports/icr-l14-evidence.md"
EVIDENCE_TEXT = "# fixture evidence\n\nthe record this assessment cites\n"
ASSESSMENT_ID = "ICR-L14-AS-1"
ASSESSMENT_FINDING = "The retry budget is still shared, and the candidate moved the constant."

# A code tree no observation of this candidate names: the identity a case records against to show the
# evidence owner selects by candidate binding rather than by "every observation in the dataset".
FOREIGN_CANDIDATE_TREE = "e" * 40

# The two observation command names, distinct so that "the foreign candidate's run was not selected"
# is a statement about an identity that would be visible had it been selected.
OBSERVATION_COMMAND = "icr-l14-suite"
FOREIGN_OBSERVATION_COMMAND = "icr-l14-foreign-suite"


@dataclass(frozen=True)
class RecordedFixture:
    """One live enclosure whose candidate dataset holds records written by their owners."""

    endpoint: EndpointFixture
    contract: WorktreeContract
    sprint: TaskDocumentRef
    authorship: Authorship
    signal_id: str
    observation_id: str
    foreign_observation_id: str
    claim_id: str
    question_id: str
    knowledge_digest: str
    anchor_id: str

    @property
    def candidate_database(self) -> Path:
        return self.endpoint.resolve().candidate_database

    @property
    def candidate_tree_id(self) -> str:
        configured = self.endpoint.resolve().candidate_code_tree_id
        assert configured is not None
        return configured

    def records(self):
        """The record bundle the production composition resolves, through its own entry point."""

        return review_records_for(self.endpoint.config, self.endpoint.request())

    def payload(self) -> KnowledgeReviewPayload:
        """The payload the production review port returns, failing loudly on a refusal."""

        result = review_through_port(self.endpoint, self.endpoint.request())
        assert result.payload is not None
        return result.payload


def review_through_port(endpoint: EndpointFixture, request):
    """One review read through the production composition the dashboard app is built with."""

    port = serving_collaborators(endpoint.config).knowledge_review
    assert port is not None, "the composition root must publish the review port"
    result = port(request)
    assert result.state == "review", result.refusal
    return result


@pytest.fixture
def recorded(tmp_path: Path) -> RecordedFixture:
    """One fresh enclosure per case: no case observes another's dataset or authority bytes."""

    return build_recorded_fixture(tmp_path / "records")


def build_recorded_fixture(directory: Path) -> RecordedFixture:
    """Build the enclosure, then produce one record of every class through its own owner."""

    endpoint = build_endpoint_fixture(directory, memory_mode="external")
    contract = load_contract(endpoint.contract.contract_path)
    sprint = write_curator_task_topology(contract)
    resolved = endpoint.resolve()
    authorship = write_authorship(
        actor_ref="agent:icr-l14",
        authorization_ref="260921-ICR developer approval",
        origin_refs=("requirement:ICR-R14@v1",),
    )
    destination = _destination(endpoint, resolved.candidate_database, authorship)
    snapshot = _candidate_snapshot(endpoint)
    assert resolved.candidate_code_tree_id is not None
    _, signal_id = _record_detection_run(endpoint, resolved, authorship)
    claim_id = _record_evidence_claim(endpoint, destination)
    observation_id = _record_observation(
        destination, OBSERVATION_COMMAND, resolved.candidate_code_tree_id, snapshot
    )
    foreign_observation_id = _record_observation(
        destination, FOREIGN_OBSERVATION_COMMAND, FOREIGN_CANDIDATE_TREE, None
    )
    question_id = _record_authored_effect(endpoint, destination, resolved.candidate_code_tree_id)
    write_passing_route_review(contract)
    _publish_assessment(contract, sprint, endpoint)
    return RecordedFixture(
        endpoint=endpoint,
        contract=contract,
        sprint=sprint,
        authorship=authorship,
        signal_id=signal_id,
        observation_id=observation_id,
        foreign_observation_id=foreign_observation_id,
        claim_id=claim_id,
        question_id=question_id,
        knowledge_digest=snapshot.logical_digest,
        anchor_id=endpoint.diff.after.fixture.integration.anchor_id,
    )


def _record_detection_run(
    endpoint: EndpointFixture, resolved, authorship: Authorship
) -> tuple[str, str]:
    """Record one run and its signal through the detection owner; return both identities."""

    store = open_diff_side(
        resolved.candidate_database,
        endpoint.repository_id,
        repository_root=resolved.candidate_code_root,
        code_tree_id=resolved.candidate_code_tree_id,
    )
    signal = DetectionSignalPayload(
        signal_id=str(uuid4()),
        repository_id=endpoint.repository_id,
        governing_route_id=str(uuid4()),
        condition="absent_anchor",
        input_set=DetectionRecordedInputSet(
            declared="trigger_side_only",
            sides=(
                DetectionInputSide(
                    side="trigger",
                    context=store,
                    selector_digest="f" * 64,
                    selector_policy_version="knowledge-read-selection/v1",
                ),
            ),
        ),
        observed_changes=(),
        relationship_paths=(
            DetectionRelationshipPath(
                path_id="icr-l14-path",
                snapshot_side="trigger",
                edges=("icr-l14-edge",),
                reached_item_id="icr-l14-item",
            ),
        ),
        extractor_version="recorded-anchor-locator/v1",
        policy_version="family-detection/v1",
        scope_manifest=DetectionScopeManifest(
            manifest_ref="icr-l14-manifest",
            retention_required=False,
            destination_kind="enclosure_local",
            retention_basis="the manifest is retained with the run's own report",
        ),
        registered_scope_status="incomplete_scan",
        unmapped_changed_paths=("src/unmapped.py",),
        limitations=(
            "unmapped_changed_paths",
            "truncated_scan",
            "no_semantic_assessment_performed",
        ),
        detail=(
            "condition=absent_anchor; followed_paths=icr-l14-path; limitations=unmapped_changed_paths"
            " | truncated_scan | no_semantic_assessment_performed"
        ),
    )
    candidate_store = _candidate_store(endpoint)
    run_id = str(uuid4())
    try:
        result = record_detection_run(
            candidate_store,
            DetectionRunRequest(
                repository_id=endpoint.repository_id,
                provenance=authorship,
                run=build_detection_run(
                    DetectionRunAssembly(
                        run_id=run_id,
                        repository_id=endpoint.repository_id,
                        assessed_repository_id=endpoint.repository_id,
                        governing_route_id=str(uuid4()),
                        input_sides=signal.input_set.sides,
                        policy_version="family-detection/v1",
                    ),
                    (signal,),
                ),
                signals=(signal,),
                # The run is recorded *in* the candidate dataset, so a dataset it measured may not be
                # that same file: the owner refuses the self-reference, and the basis names the other
                # half of the comparison instead.
                assessed_database_paths=(str(resolved.baseline_database),),
            ),
        )
    finally:
        candidate_store.close()
    assert result.state == "created", result.refusal
    return run_id, signal.signal_id


def _record_evidence_claim(endpoint: EndpointFixture, destination) -> str:
    """Record one authored evidence claim through the application evidence writer."""

    after = endpoint.diff.after.fixture
    command = AddEvidenceClaim(
        claim_id=str(uuid4()),
        revision_id=str(uuid4()),
        subject=InvariantRevisionSubject(revision_id=after.subject_revision_id),
        evidence_anchor_id=after.integration.anchor_id,
        coverage=(AnchorCoverage(anchor_id=after.integration.anchor_id),),
        payload=EvidenceClaimPayload(
            explanation="The recorded realization covers the retry budget the review compares.",
            limitations="Asserted coverage only; the claim states no sufficiency.",
        ),
    )
    result = write_knowledge_evidence(
        destination, admitted_evidence_request(destination, command)
    )
    assert result.state == "applied", result.refusal
    return command.claim_id


def _record_observation(
    destination,
    command_name: str,
    tree_id: str,
    knowledge_candidate: SnapshotIdentity | None,
) -> str:
    """Record one verification observation through the application evidence writer.

    The candidate is named exactly as the record names it -- this dataset's own logical identity and,
    for the candidate's own run, the captured code tree -- so a case can also record one against a
    *different* tree and show that the evidence owner's selection is a binding rather than a listing.
    """

    command = AddVerificationObservation(
        observation_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload=VerificationObservationPayload(
            command_name=command_name,
            command_identity="mcp/.venv/bin/python -m pytest mcp/tests -q",
            knowledge_candidate=knowledge_candidate,
            code_candidate_tree_id=tree_id,
            result_artifact=ResultArtifactReference(
                path="reports/icr-l14-suite.json",
                sha256="d" * 64,
                size_bytes=64,
                digest_checked_against_bytes=False,
            ),
            execution_result="passed",
            environment=RunEnvironment(host="fixture-builder", interpreter="cpython-3.13"),
        ),
    )
    result = write_knowledge_evidence(
        destination, admitted_evidence_request(destination, command)
    )
    assert result.state == "applied", result.refusal
    return command.observation_id


def _candidate_snapshot(endpoint: EndpointFixture) -> SnapshotIdentity:
    """The candidate dataset's own recorded snapshot identity, read from the live store."""

    store = _candidate_store(endpoint)
    try:
        return store.snapshot_identity()
    finally:
        store.close()


def _record_authored_effect(endpoint: EndpointFixture, destination, tree_id: str) -> str:
    """Record one authored unresolved question, over a change set authored in the same batch."""

    change_set_id = str(uuid4())
    question_id = str(uuid4())
    resolution = CandidateResolution(
        lane="draft-candidate",
        code_tree_id=tree_id,
        memory_tree_id=tree_id,
        snapshot_ref="icr-l14-candidate",
        candidate_ref="icr-l14-review",
    )
    snapshot = None
    store = _candidate_store(endpoint)
    try:
        snapshot = store.snapshot_identity()
    finally:
        store.close()
    assert snapshot is not None
    commands = (
        AddSemanticChangeSet(
            record_id=change_set_id,
            revision_id=str(uuid4()),
            payload={
                "baseline": snapshot.model_dump(mode="json"),
                "candidate": snapshot.model_dump(mode="json"),
            },
        ),
        AddUnresolvedQuestion(
            record_id=question_id,
            revision_id=str(uuid4()),
            payload={
                "change_set_id": change_set_id,
                "statement": "Is the shared retry budget still the intended shape after this move?",
            },
        ),
    )
    result = change_knowledge_candidate(
        destination,
        ChangeBatch(expected=resolve_candidate_context(destination, resolution), commands=commands),
    )
    assert result.state == "changed", result.refusal
    return question_id


def _rewrite_stored_revision(endpoint: EndpointFixture, record_id: str, **columns: str) -> None:
    """Rewrite one stored revision's named columns, as an edit outside the operation would.

    The immutability trigger is dropped for the write and **restored with the same SQL the schema
    installed**, because the point of these cases is the *second* defence: a record altered behind
    its identity must not be served as the record that identity names, while the dataset itself stays
    a dataset of this code. A dropped trigger would make the whole database unreadable as this schema
    -- which is a different, already-covered failure -- so the dataset is left intact and only the
    one record is damaged.
    """

    assignments = ", ".join(f"{column} = ?" for column in columns)
    store = _candidate_store(endpoint)
    try:
        store.connection.execute("DROP TRIGGER record_revision_no_rewrite")
        store.connection.execute(
            f"UPDATE record_revision SET {assignments} WHERE repository_id = ? AND record_id = ?",
            (*columns.values(), endpoint.repository_id, record_id),
        )
        store.connection.execute(APPENDED_TRIGGERS["record_revision_no_rewrite"])
    finally:
        store.close()


def _damage_detection_run(endpoint: EndpointFixture, run_id: str) -> None:
    """Alter one stored run's seal, so the owner's own read reports it as damaged."""

    _rewrite_stored_revision(endpoint, run_id, content_digest="0" * 64)


def _damage_claim(endpoint: EndpointFixture, claim_id: str) -> None:
    """Replace one stored claim's payload with bytes that are no longer a claim payload."""

    _rewrite_stored_revision(endpoint, claim_id, payload='{"not": "a claim payload"}')


def _publish_assessment(
    contract: WorktreeContract, sprint: TaskDocumentRef, endpoint: EndpointFixture
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


def _assessment_revision(endpoint: EndpointFixture) -> ReviewAssessmentRevision:
    """One authored assessment over the candidate's own reviewed revision pair."""

    return ReviewAssessmentRevision(
        assessmentId=ASSESSMENT_ID,
        subject=AssessmentSubject(
            kind="invariant-revision",
            recordId=endpoint.diff.retry_invariant_id,
            beforeRevisionIds=(endpoint.diff.subject_revision_id,),
            afterRevisionIds=(endpoint.diff.revised_revision_id,),
        ),
        disposition="concern_found",
        finding=ASSESSMENT_FINDING,
        rationale="The candidate moves the retry budget without an earlier shared deadline.",
        evidenceRefs=(AssessmentEvidenceReference(namespace="task", ref=EVIDENCE_RELATIVE),),
        comparisonRef="icr-l14-comparison",
        scopeManifestRef="icr-l14-scope",
    )


def _destination(endpoint: EndpointFixture, database: Path, authorship: Authorship):
    """The admitted destination the evidence writers take for one candidate dataset."""

    return admitted_knowledge_destination(
        database,
        RepositoryIdentity(
            repository_id=endpoint.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
        ),
        authorship,
    )


def _candidate_store(endpoint: EndpointFixture):
    """The candidate dataset of the live enclosure, opened for the owner that writes into it."""

    resolved = endpoint.resolve()
    return open_existing_knowledge_store(resolved.candidate_database, endpoint.repository_id)


def _channels(payload: KnowledgeReviewPayload) -> dict[str, ReviewRecordChannel]:
    return {channel.records: channel for channel in payload.evidence.channels}


def test_the_production_composition_supplies_every_owner_produced_record_class(
    recorded: RecordedFixture,
) -> None:
    """The port returns every class its owners hold, with each record's own fields intact."""

    payload = recorded.payload()
    channels = _channels(payload)

    assert [row.signal_id for row in payload.knowledge.signals] == [recorded.signal_id]
    signal = payload.knowledge.signals[0]
    assert signal.condition == "absent_anchor"
    assert signal.input_set == "trigger_side_only"
    assert "truncated_scan" in signal.scope_limitations
    assert signal.relationship_paths == ("icr-l14-path:icr-l14-item",)

    observations = {row.observation_id: row for row in payload.evidence.observations}
    assert set(observations) == {OBSERVATION_COMMAND}
    assert FOREIGN_OBSERVATION_COMMAND not in observations
    observation = observations[OBSERVATION_COMMAND]
    assert observation.execution_result == "passed"
    assert observation.result_artifact_ref == "reports/icr-l14-suite.json"
    assert observation.result_artifact_digest == "d" * 64
    assert observation.tested_candidate == recorded.knowledge_digest

    links = {link.claim_id: link for link in payload.evidence.evidence_links}
    assert set(links) == {recorded.claim_id}
    link = links[recorded.claim_id]
    assert link.author_ref == "agent:icr-l14"
    assert link.lifecycle == "proposed"
    assert link.limitations == ("Asserted coverage only; the claim states no sufficiency.",)
    assert link.claimed_coverage == (f"source_anchor:{recorded.anchor_id}",)
    assert link.unresolved == ()
    assert [effect.record_id for effect in payload.knowledge.authored_effects] == [
        recorded.question_id
    ]

    assert [row.assessment_id for row in payload.evidence.assessments] == [ASSESSMENT_ID]
    assessment = payload.evidence.assessments[0]
    assert assessment.author_ref
    assert assessment.role_ref == "architect"
    assert assessment.examined_inputs
    assert assessment.binding_state != "current"

    assert channels["detection_signals"].state == "recorded"
    assert channels["detection_signals"].record_count == 1
    assert channels["verification_observations"].state == "recorded"
    assert channels["verification_observations"].record_count == 1
    assert channels["evidence_claims"].state == "recorded"
    assert channels["evidence_claims"].record_count == 1
    assert channels["authored_effects"].state == "recorded"
    assert channels["authored_effects"].record_count == 1
    assert channels["assessments"].state == "recorded"
    assert channels["assessments"].record_count == 1


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


def test_one_unreadable_authority_leaves_every_readable_class_supplied(tmp_path: Path) -> None:
    """The packet's boundary example: a corrupt authority beside a valid observation."""

    recorded = build_recorded_fixture(tmp_path / "partial")
    curator_coherence_paths(recorded.contract).canonical.write_bytes(b"corrupt")

    payload = recorded.payload()
    channels = _channels(payload)

    assert channels["assessments"].state == "unavailable"
    assert payload.evidence.assessments == ()
    assert channels["verification_observations"].state == "recorded"
    assert [row.observation_id for row in payload.evidence.observations] == [OBSERVATION_COMMAND]
    assert channels["detection_signals"].state == "recorded"
    assert [row.signal_id for row in payload.knowledge.signals] == [recorded.signal_id]
    assert channels["evidence_claims"].state == "recorded"
    assert [link.author_ref for link in payload.evidence.evidence_links] == ["agent:icr-l14"]


def test_a_task_context_review_reports_the_matrix_collection_as_not_selected(
    recorded: RecordedFixture,
) -> None:
    """A review that read no matrix says so, instead of reporting an absence it never asked about."""

    payload = recorded.payload()
    assert _channels(payload)["authored_effects"].state == "recorded"

    result = review_through_port(recorded.endpoint, recorded.endpoint.task_request())
    assert result.payload is not None
    channels = _channels(result.payload)
    authored = channels["authored_effects"]
    assert authored.state == "not_selected"
    assert authored.record_count is None
    assert authored.next_action
    assert result.payload.knowledge.authored_effects == ()
    # The claims collection is the candidate's own and needs no selection, so it is still supplied;
    # no matrix row selected one for display, which is what the empty link list states.
    assert channels["evidence_claims"].state == "recorded"
    assert result.payload.evidence.evidence_links == ()
    assert channels["detection_signals"].state == "recorded"
    assert channels["assessments"].state == "recorded"


def test_no_measurement_is_reported_as_one(recorded: RecordedFixture) -> None:
    """An empty measurement never grants currentness, and "not measured" is a stated fact."""

    records = recorded.records()
    assert records.current is None
    channels = {channel.records: channel for channel in records.channels}
    currentness = channels["assessment_currentness"]
    assert currentness.state == "not_measured"
    assert currentness.record_count is None
    assert currentness.next_action

    payload = recorded.payload()
    assert all(row.binding_state != "current" for row in payload.evidence.assessments)
    assert _channels(payload)["assessment_currentness"].state == "not_measured"


def test_a_damaged_detection_run_is_named_while_its_siblings_are_supplied(tmp_path: Path) -> None:
    """One unreadable run is a named record, not the withdrawal of the whole detector channel."""

    recorded = build_recorded_fixture(tmp_path / "damaged-run")
    resolved = recorded.endpoint.resolve()
    _, healthy_signal = _record_detection_run(
        recorded.endpoint, resolved, recorded.authorship
    )
    damaged_run, damaged_signal = _record_detection_run(
        recorded.endpoint, resolved, recorded.authorship
    )
    _damage_detection_run(recorded.endpoint, damaged_run)

    payload = recorded.payload()
    channel = _channels(payload)["detection_signals"]
    assert channel.state == "recorded"
    assert channel.record_count == 2
    assert channel.unreadable == (damaged_run,)
    assert "could not be read and are named" in channel.detail
    supplied = {row.signal_id for row in payload.knowledge.signals}
    assert supplied == {recorded.signal_id, healthy_signal}
    assert damaged_signal not in supplied


def test_a_damaged_evidence_claim_is_named_while_its_siblings_are_supplied(tmp_path: Path) -> None:
    """One undecodable claim is named, its sibling keeps every authored field, and no silence."""

    recorded = build_recorded_fixture(tmp_path / "damaged-claim")
    destination = _destination(
        recorded.endpoint, recorded.candidate_database, recorded.authorship
    )
    damaged_claim = _record_evidence_claim(recorded.endpoint, destination)
    _damage_claim(recorded.endpoint, damaged_claim)

    payload = recorded.payload()
    channel = _channels(payload)["evidence_claims"]
    assert channel.state == "recorded"
    assert channel.record_count == 1
    assert channel.unreadable == (damaged_claim,)

    links = {link.claim_id: link for link in payload.evidence.evidence_links}
    assert set(links) == {recorded.claim_id, damaged_claim}
    assert links[recorded.claim_id].author_ref == "agent:icr-l14"
    assert links[recorded.claim_id].limitations == (
        "Asserted coverage only; the claim states no sufficiency.",
    )
    # The damaged claim keeps its identity and is marked as content this composition could not
    # supply; it is never rendered as a claim that declared no limitations.
    assert links[damaged_claim].limitations == ()
    assert [reference.field for reference in links[damaged_claim].unresolved] == ["claim_content"]


def test_an_unresolvable_candidate_reports_every_collection_unavailable(tmp_path: Path) -> None:
    """A candidate that does not resolve supplies no records and never claims to have read none."""

    endpoint = build_endpoint_fixture(tmp_path / "absent", datasets=False)
    request = endpoint.request()
    bundle = review_records_for(endpoint.config, request)

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


def test_the_wire_payload_carries_the_channels() -> None:
    """The channels are part of the served payload's own schema, not a local return value."""

    fields = set(KnowledgeReviewPayload.model_fields)
    assert "evidence" in fields
    schema = json.dumps(KnowledgeReviewPayload.model_json_schema())
    assert "ReviewRecordChannel" in schema
    assert "not_measured" in schema
