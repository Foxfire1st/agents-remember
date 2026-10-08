"""One selected subject's review displays only the records that subject may be judged by (ICR-R26).

F09's defect is one omission with two faces: the adapter handed the candidate-wide review matrix and
every supplied assessment to the selected subject's panes, so a valid assessment of a *sibling*
invariant was displayed in both panes of a different invariant. ``ICR-R14@v1`` delivers the complete
owner-produced collections; these cases measure the attribution half -- which of those records may be
displayed beside which subject, and why -- through the **production composition**
(``cli.dashboard.serving_collaborators``, the same port ``create_app`` is given) over a real leaf
enclosure with converted memory trees and records produced by their owning operations:

* five assessments published through the curator-coherence authority, for the selected subject, for a
  *sibling* invariant the selection's recorded relationships reach, for an invariant the selection
  does **not** reach, for an identity neither snapshot records, and one that names the selected
  subject's own identity where a revision belongs (the malformed binding);
* and a source movement after publication, so the same assessment is measured once as this
  generation's record and once as historical input of the previous one.

The load-bearing properties, one case each:

* a sibling subject's assessment is **not** in either pane's assessments -- it is labelled context,
  with its own subject and the recorded relationship that reached it, and none of its finding;
* an unrelated subject's assessment is counted and **not displayed** as this subject's judgment, so
  the complete supplied population stays visible as arithmetic rather than being silently dropped;
* an unresolvable binding is displayed ``unresolved`` beside the exact references it carries;
* an assessment of a previous generation is displayed ``historical`` -- with the candidate tree it
  really examined -- and never as the displayed generation's result;
* the records that *are* the selected subject's carry a recorded binding that names it: the direct
  assessment whose own recorded subject revisions the selection retains;
* the population a record is classified against is the **selection**, not the comparison's bounded
  page, so every page size classifies the same records the same way;
* the source inventory is measured from the two bound code trees and is unaffected by any of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import get_args
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.review_subject_catalogue import read_subject_catalogue
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.knowledge.review import (
    KnowledgeReviewPayload,
    ReviewApplicabilitySummary,
    ReviewDisplayedApplicability,
)
from agents_remember.models.knowledge.review_applicability import (
    REVIEW_APPLICABILITY_CLASSES,
    ReviewContextRecord,
)
from agents_remember.models.knowledge.review_records import ReviewRecordClassName
from agents_remember.models.knowledge_files import canonical_text
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
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from curator_coherence_test_support import write_curator_task_topology
from test_review_git_trees import (
    FAMILY,
    INVARIANT,
    World,
    _resolve,
    _seed,
    build_world,
    commit,
    git,
    invariant,
)
from test_worktree_support import write_passing_route_review

pytestmark = pytest.mark.evidence_unit

EVIDENCE_RELATIVE = "notes/reports/icr-l26-evidence.md"
EVIDENCE_TEXT = "# fixture evidence\n\nthe record these assessments cite\n"
SOURCE_MOVEMENT_PATH = "src/moved_after_the_assessment.py"
SOURCE_MOVEMENT_TEXT = "# added after the assessment was published, so the candidate tree moved\n"

DIRECT_ASSESSMENT = "ICR-L26-AS-DIRECT"
SIBLING_ASSESSMENT = "ICR-L26-AS-SIBLING"
UNRELATED_ASSESSMENT = "ICR-L26-AS-UNRELATED"
UNRESOLVABLE_ASSESSMENT = "ICR-L26-AS-UNRESOLVABLE"
MALFORMED_ASSESSMENT = "ICR-L26-AS-MALFORMED"

SIBLING = "INV-BBBBBB"
UNRELATED = "INV-CCCCCC"
SELECTED_ID = text_uuid("identity", INVARIANT)
BEFORE_REVISION = text_uuid("revision", f"{INVARIANT}@1")
AFTER_REVISION = text_uuid("revision", f"{INVARIANT}@2")
SIBLING_ID = text_uuid("identity", SIBLING)
SIBLING_REVISION = text_uuid("revision", f"{SIBLING}@1")
UNRELATED_ID = text_uuid("identity", UNRELATED)
UNRELATED_REVISION = text_uuid("revision", f"{UNRELATED}@1")


@dataclass(frozen=True)
class Journey:
    """One live enclosure with five curator-published assessments about its candidate.

    The dataset's own record classes (detection signals, evidence claims, observations, authored
    effects) are not in it: their writers were retired with the canonical database (MIK-R26).
    """

    endpoint: World
    contract: WorktreeContract
    sprint: TaskDocumentRef
    unrelated_invariant: str
    malformed_assessment_id: str
    pages: dict[int, KnowledgeReviewPayload]
    candidate_tree_id: str
    first: KnowledgeReviewPayload

    def read(self, page_size: int = 0) -> KnowledgeReviewPayload:
        """One fresh read through the production review port, failing loudly on a refusal."""

        port = serving_collaborators(self.endpoint.config).knowledge_review
        assert port is not None, "the composition root must publish the review port"
        request = self.endpoint.review(selector=_seed(), page_size=page_size)
        result = port(request)
        assert result.state == "review", result.refusal
        assert result.payload is not None
        return result.payload

    def treatments(self, payload: KnowledgeReviewPayload) -> dict[str, str]:
        """The treatment each supplied assessment earned in the knowledge pane, by identity."""

        return {
            row.assessment_id: (
                "<absent>" if row.applicability is None else row.applicability.state
            )
            for row in payload.knowledge.assessments
        }

    def state_of(self, payload: KnowledgeReviewPayload, assessment_id: str) -> str:
        """The treatment one assessment earned in the knowledge pane, or ``<absent>``."""

        for row in payload.knowledge.assessments:
            if row.assessment_id == assessment_id:
                assert row.applicability is not None
                return row.applicability.state
        return "<absent>"

    def summary(self, payload: KnowledgeReviewPayload, records: str) -> ReviewApplicabilitySummary:
        for row in payload.knowledge.applicability:
            if row.records == records:
                return row
        raise AssertionError(f"no applicability summary for {records!r}")


@pytest.fixture(scope="module")
def journey(tmp_path_factory: pytest.TempPathFactory) -> Journey:
    """One enclosure per module: the publication journey is expensive and read-only afterwards."""

    bind_worktree_services(build_default_worktree_services())
    try:
        return _build_journey(tmp_path_factory.mktemp("subject-isolation"))
    finally:
        reset_worktree_services()


@pytest.fixture(scope="module")
def moved(journey: Journey) -> KnowledgeReviewPayload:
    """The same review read after the candidate's source moved, so the generation is another one."""

    target = journey.endpoint.code_worktree / SOURCE_MOVEMENT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SOURCE_MOVEMENT_TEXT, encoding="utf-8")
    return journey.read()


def test_a_sibling_subjects_assessment_is_context_and_never_the_selected_subjects(
    journey: Journey,
) -> None:
    """The F09 shape: the sibling's assessment is not in either pane's assessments, and is labelled.

    It is displayed as context -- with its true subject, the recorded relationship that reached it
    and its author -- so recorded family material stays inspectable without ever reading as a
    judgment on the selected subject. Its finding is deliberately not restated here: that record
    belongs to the sibling's own review.
    """

    payload = journey.first
    sibling = SIBLING_ID
    assert journey.state_of(payload, SIBLING_ASSESSMENT) == "<absent>"
    assert not [
        row for row in payload.evidence.assessments if row.assessment_id == SIBLING_ASSESSMENT
    ]
    context = [row for row in payload.knowledge.context if row.record_id == SIBLING_ASSESSMENT]
    assert len(context) == 1
    row = context[0]
    assert isinstance(row, ReviewContextRecord)
    assert row.subject_kind == "invariant"
    assert row.subject_id == sibling
    # The label names the record's KIND. The sibling's disposition is a judgment that belongs to the
    # sibling's own review, and a context row that carried one would be the contamination F09 was.
    assert row.label == "assessment/invariant-revision"
    assert "concern_found" not in row.model_dump_json()
    assert row.author_ref and row.role_ref == "architect"
    assert row.relationship
    assert "labelled context" in row.detail
    # The same record is context on the evidence pane too: the two panes display one population.
    assert [entry.record_id for entry in payload.evidence.context] == [SIBLING_ASSESSMENT]


def test_the_selected_subjects_own_assessment_is_direct_with_its_recorded_binding(
    journey: Journey,
) -> None:
    """The selected subject's assessment is displayed, and its label names the binding that says so."""

    payload = journey.first
    displayed = {row.assessment_id: row for row in payload.knowledge.assessments}
    assert set(displayed) == {DIRECT_ASSESSMENT, UNRESOLVABLE_ASSESSMENT, MALFORMED_ASSESSMENT}
    direct = displayed[DIRECT_ASSESSMENT]
    assert direct.applicability is not None
    assert direct.applicability.state == "direct"
    assert direct.applicability.subject_id == SELECTED_ID
    assert BEFORE_REVISION in direct.applicability.subject_revision_ids
    assert AFTER_REVISION in direct.applicability.subject_revision_ids
    assert f"code-tree:candidate:{journey.candidate_tree_id}" in direct.applicability.references
    # Provenance travels with the displayed value, exactly as the owner recorded it.
    assert direct.author_ref and direct.role_ref == "architect"
    assert direct.examined_inputs


def test_an_unrelated_subjects_assessment_is_counted_and_not_displayed(journey: Journey) -> None:
    """A subject the selection's recorded relationships do not reach is excluded, and counted.

    The exclusion is arithmetic, not silence: the summary states the whole supplied population, the
    number not displayed as this subject's judgments, and how a reader reaches those records.
    """

    payload = journey.first
    assert journey.state_of(payload, UNRELATED_ASSESSMENT) == "<absent>"
    assert not [
        row for row in payload.evidence.assessments if row.assessment_id == UNRELATED_ASSESSMENT
    ]
    assert UNRELATED_ASSESSMENT not in [row.record_id for row in payload.knowledge.context]
    summary = journey.summary(payload, "assessments")
    assert summary.supplied == 5
    assert (summary.direct, summary.historical, summary.context) == (1, 0, 1)
    assert (summary.candidate, summary.unresolved, summary.unrelated) == (0, 2, 1)
    assert "not displayed as judgments on the selected subject" in summary.detail
    assert summary.unrelated == 1


def test_an_unresolvable_binding_is_displayed_unresolved_with_its_references(
    journey: Journey,
) -> None:
    """A record naming an identity nothing records is exposed, with its references and the reason."""

    payload = journey.first
    displayed = {row.assessment_id: row for row in payload.knowledge.assessments}
    unresolved = displayed[UNRESOLVABLE_ASSESSMENT]
    assert unresolved.applicability is not None
    assert unresolved.applicability.state == "unresolved"
    assert unresolved.applicability.subject_id
    assert unresolved.applicability.references
    assert "could not be resolved" in unresolved.applicability.detail
    # It cannot imply support: the refused state is the unmeasured one, never current.
    assert unresolved.binding_state != "current"


def test_a_previous_generations_assessment_is_historical_and_never_current(
    moved: KnowledgeReviewPayload, journey: Journey
) -> None:
    """The same assessment, read after the source moved, is labelled historical -- with its old tree.

    The boundary the packet names: a relevant assessment of the subject's previous generation stays
    inspectable as history, names the candidate tree it really examined as its old input identity,
    and is never displayed as the generation the comparison now shows.
    """

    assert moved.comparison is not None
    assert moved.comparison.after_code_tree_id != journey.candidate_tree_id
    displayed = {row.assessment_id: row for row in moved.knowledge.assessments}
    historical = displayed[DIRECT_ASSESSMENT]
    assert historical.applicability is not None
    assert historical.applicability.state == "historical"
    assert f"code-tree:candidate:{journey.candidate_tree_id}" in historical.applicability.references
    bound_tree = moved.comparison.after_code_tree_id
    assert bound_tree is not None
    assert bound_tree in historical.applicability.detail
    assert journey.summary(moved, "assessments").historical == 1


def test_a_malformed_revision_binding_is_unresolved_and_never_a_retained_revision(
    journey: Journey,
) -> None:
    """A record naming an *identity* where a revision belongs is unresolved, with its references.

    The packet's Failure And Recovery Behavior forbids exactly this match: a reference that is not a
    recorded revision of the subject is never intersected with the selection's revisions, so an
    assessment whose binding cannot be resolved is exposed as unresolved input instead of being read
    as direct evidence about the invariant whose id it happens to carry.
    """

    payload = journey.first
    displayed = {row.assessment_id: row for row in payload.knowledge.assessments}
    malformed = displayed[journey.malformed_assessment_id]
    assert malformed.applicability is not None
    assert malformed.applicability.state == "unresolved"
    assert f"revision:{SELECTED_ID}" in malformed.applicability.references
    assert "could not be resolved" in malformed.applicability.detail
    # The claim the malformed match used to make is gone: its "revision" is the subject's identity.
    assert "retains" not in malformed.applicability.detail
    assert journey.summary(payload, "assessments").unresolved == 2


def test_every_page_size_classifies_the_same_records_the_same_way(journey: Journey) -> None:
    """A record's treatment is a fact about the selection, never about the page it was read on.

    The comparison's items are a bounded window (ICR-R10), and the shipped client can ask for any
    page size. Classifying from that window alone made the selected subject's own record historical
    and dropped the recorded family context as unrelated; the classification therefore reads the
    recorded population from the snapshots' own owners, and this case pins the three page sizes.
    """

    owner_default = journey.first
    expected = journey.treatments(owner_default)
    assert expected[DIRECT_ASSESSMENT] == "direct"
    assert expected[MALFORMED_ASSESSMENT] == "unresolved"
    expected_context = {
        (row.record_id, row.subject_id, row.relationship) for row in owner_default.knowledge.context
    }
    assert expected_context
    for page_size, page in sorted(journey.pages.items()):
        assert journey.treatments(page) == expected, page_size
        context = {
            (row.record_id, row.subject_id, row.relationship) for row in page.knowledge.context
        }
        assert context == expected_context, page_size
        # The subject the selection's recorded relationships do not reach stays the *only* record
        # counted as unrelated: no page size may report a recorded-reachable record as unreachable.
        summary = journey.summary(page, "assessments")
        assert summary.unrelated == 1, page_size
        assert (
            summary.supplied
            == summary.direct
            + summary.historical
            + summary.context
            + summary.unresolved
            + summary.unrelated
        )


def test_a_direct_label_never_cites_a_retained_list_that_does_not_carry_the_record(
    journey: Journey,
) -> None:
    """The `direct` sentence states the basis the classification used, at every page size.

    ICR-R07's own selection is published for the page the caller asked for, so at a bounded page its
    retained list is a *narrower* set than the recorded revisions this classification reads from the
    snapshots. A label may therefore cite R07 only where R07 really lists the matched revisions, and
    otherwise it names the basis it used -- the snapshots' recorded revisions of the subject. The
    treatment is the same either way; only the sentence differs, and it is never false.
    """

    published_basis = "ICR-R07's own recorded selection lists as retained"
    recorded_basis = "the two snapshots record for that subject"
    matched = (BEFORE_REVISION, AFTER_REVISION)
    for page_size, payload in ((0, journey.first), *sorted(journey.pages.items())):
        row = next(
            entry
            for entry in payload.knowledge.assessments
            if entry.assessment_id == DIRECT_ASSESSMENT
        )
        assert row.applicability is not None
        assert row.applicability.state == "direct"
        detail = row.applicability.detail
        assert (published_basis in detail) != (recorded_basis in detail), page_size
        if published_basis in detail:
            selection = payload.knowledge.revision_selection
            assert selection is not None
            retained = {*selection.before_retained, *selection.after_retained}
            assert set(matched) <= retained, page_size
        else:
            # The verifier's shape: a bounded page cannot list these revisions as retained, so the
            # label says what it actually matched instead of asserting a list that omits them.
            assert page_size != 0, page_size


def test_every_supplied_collection_reports_its_complete_population(journey: Journey) -> None:
    """The panes filter; the owner's complete population stays stated on the channels and the counts."""

    payload = journey.first
    channels = {row.records: row for row in payload.evidence.channels}
    summary = journey.summary(payload, "assessments")
    assert channels["assessments"].record_count == summary.supplied == 5
    # Their canonical writers/readers were retired; these channels have no measured population.
    for records in ("evidence_claims", "detection_signals", "verification_observations"):
        assert (channels[records].state, channels[records].record_count) == ("unavailable", None)
        assert "retired" in channels[records].detail
    # Every summary partitions its collection, over the classes this pane actually displays.
    for row in payload.knowledge.applicability:
        displayed = row.direct + row.historical + row.context + row.candidate + row.unresolved
        assert displayed + row.unrelated == row.supplied
    assert {row.records for row in payload.evidence.applicability} == {
        "assessments",
        "verification_observations",
        "evidence_claims",
    }


def test_the_source_inventory_is_independent_of_the_record_selection(journey: Journey) -> None:
    """The inventory is the bound pair's own measurement, and no record selection reaches it."""

    payload = journey.first
    inventory = payload.source.inventory
    assert inventory.state == "measured"
    assert inventory.listed_total == len(inventory.entries)
    # The changed paths include ones no displayed record relates to, which is the point: the
    # inventory is a measurement of the two code trees, not a projection of the records.
    paths = {entry.path for entry in inventory.entries}
    assert "src/unmapped.py" in paths
    assert SOURCE_MOVEMENT_PATH not in paths


def test_the_class_vocabulary_is_the_record_vocabulary_minus_its_measurement_channel() -> None:
    """The applicability classes and the record-class channels cannot drift apart."""

    assert set(REVIEW_APPLICABILITY_CLASSES) == set(get_args(ReviewRecordClassName)) - {
        "assessment_currentness"
    }


def test_a_label_and_a_summary_refuse_a_claim_their_recorded_facts_do_not_support() -> None:
    """The shapes refuse the two lies this requirement is about, at construction."""

    with pytest.raises(ValueError):
        ReviewDisplayedApplicability(
            records="assessments",
            record_id="x",
            state="direct",
            references=("subject:invariant:x",),
            detail="displayed as the selected subject's own record",
        )
    with pytest.raises(ValueError):
        ReviewApplicabilitySummary(
            records="assessments",
            supplied=2,
            direct=1,
            unrelated=0,
            detail="two supplied",
        )
    with pytest.raises(ValueError):
        ReviewApplicabilitySummary(
            records="assessments",
            supplied=3,
            unrelated=2,
            detail="",
        )


# --------------------------------------------------------------------------------------------
# The journey: one enclosure, records produced by their owning operations.
# --------------------------------------------------------------------------------------------


def _build_journey(directory: Path) -> Journey:
    endpoint = build_world(directory / "endpoints")
    family_path = f"knowledge/families/{FAMILY}-landing.json"
    family = json.loads((endpoint.memory / family_path).read_text())
    family["members"].append(SIBLING)
    records = {family_path: canonical_text(family)}
    for identity, statement in ((SIBLING, "Sibling values land."), (UNRELATED, "Unrelated rule.")):
        record = json.loads(invariant(statement=statement))
        record["id"] = identity
        records[f"knowledge/invariants/{identity}-rule.json"] = canonical_text(record)
    endpoint.memory_base = commit(endpoint.memory, records, trailer=endpoint.code_base)
    git(endpoint.memory_worktree, "merge", "--ff-only", "main")
    contract = load_contract(endpoint.contract())
    endpoint.edit()
    unmapped = endpoint.code_worktree / "src/unmapped.py"
    unmapped.parent.mkdir(parents=True)
    unmapped.write_text("# changed source with no recorded realization\n", encoding="utf-8")
    answered = write_knowledge(
        WriteRequest(
            memory_root=endpoint.memory_worktree,
            code_root=endpoint.code_worktree,
            owner=Owner(task=contract.task_id, kind="leaf", id=contract.leaf_id),
            handoff_path="notes/reports/icr-l26-history-handoff.json",
            document={
                "history": [
                    {
                        "subject": INVARIANT,
                        "disposition": "changed",
                        "effect": "clarify",
                        "reason": "The fixture clarifies the selected statement.",
                        "covers": ["RLZ-A00001"],
                    },
                    {
                        "subject": FAMILY,
                        "disposition": "no_impact",
                        "reason": "Both authored member statements retain the landing guarantee.",
                        "examined": [INVARIANT, SIBLING],
                    },
                    *(
                        {
                            "subject": f"onboarding:{path}",
                            "disposition": "no_impact",
                            "reason": "The fixture's harmless source edit preserves this description.",
                        }
                        for path in ("overview", "pkg/a.py", "src/unmapped.py")
                    ),
                ]
            },
            commit=True,
        )
    )
    assert answered.state == "written", answered.render()
    sprint = write_curator_task_topology(contract)
    resolved = _resolve(endpoint)
    assert resolved.candidate_code_tree_id is not None
    catalogue = {row.selector_id for row in read_subject_catalogue(resolved)}
    assert {SELECTED_ID, SIBLING_ID, UNRELATED_ID} <= catalogue
    write_passing_route_review(contract)
    _publish_assessments(contract, sprint)
    return Journey(
        endpoint=endpoint,
        contract=contract,
        sprint=sprint,
        unrelated_invariant=UNRELATED_ID,
        malformed_assessment_id=MALFORMED_ASSESSMENT,
        pages={size: _read_through_port(endpoint, page_size=size) for size in (1, 2)},
        candidate_tree_id=resolved.candidate_code_tree_id,
        first=_read_through_port(endpoint),
    )


def _read_through_port(endpoint: World, page_size: int = 0) -> KnowledgeReviewPayload:
    """One read through the production port; the page-size series is read in the same generation."""

    port = serving_collaborators(endpoint.config).knowledge_review
    assert port is not None
    request = endpoint.review(selector=_seed(), page_size=page_size)
    result = port(request)
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def _publish_assessments(
    contract: WorktreeContract,
    sprint: TaskDocumentRef,
) -> None:
    """Publish five assessments in one generation, through the curator-coherence authority."""

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
            semantic_requirement_revision="ICR-R26@v1",
            delivery_attempt="A001",
            judgments=[
                CuratorCoherenceJudgment(
                    **CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json"),
                    disposition="reconciled",
                    rationale="The fixture candidate has one explicit reconciliation judgment.",
                    evidenceRef=f"task:{EVIDENCE_RELATIVE}",
                )
                for candidate in candidates
            ],
            review_assessments=[
                _assessment(
                    DIRECT_ASSESSMENT,
                    SELECTED_ID,
                    (BEFORE_REVISION,),
                    (AFTER_REVISION,),
                ),
                _assessment(
                    SIBLING_ASSESSMENT,
                    SIBLING_ID,
                    (SIBLING_REVISION,),
                    (SIBLING_REVISION,),
                ),
                _assessment(
                    UNRELATED_ASSESSMENT,
                    UNRELATED_ID,
                    (UNRELATED_REVISION,),
                    (UNRELATED_REVISION,),
                ),
                _assessment(
                    UNRESOLVABLE_ASSESSMENT,
                    str(uuid4()),
                    (str(uuid4()),),
                    (str(uuid4()),),
                ),
                # The malformed binding: the selected subject's own *identity* where a revision
                # belongs. The publication path validates structure and not whether the subject
                # records that revision, so this is a real record of this store -- and it must be
                # reported unresolved rather than matched.
                _assessment(
                    MALFORMED_ASSESSMENT,
                    SELECTED_ID,
                    (),
                    (SELECTED_ID,),
                ),
            ],
            expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
            expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
            expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
            expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
            expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
            expected_attestation_sha256=str(prepared["attestationSha256"]),
            caller=DeclaredCaller(role="architect", task_document_ref=sprint),
        ),
    )
    assert published["state"] in {"published", "already-current"}, json.dumps(
        published, default=str
    )


def _assessment(
    assessment_id: str, subject_id: str, before: tuple[str, ...], after: tuple[str, ...]
) -> ReviewAssessmentRevision:
    return ReviewAssessmentRevision(
        assessmentId=assessment_id,
        subject=AssessmentSubject(
            kind="invariant-revision",
            recordId=subject_id,
            beforeRevisionIds=before,
            afterRevisionIds=after,
        ),
        disposition="concern_found",
        finding=f"{assessment_id} records a concern about its own subject.",
        rationale="The fixture records a distinct authored judgment per subject.",
        evidenceRefs=(AssessmentEvidenceReference(namespace="task", ref=EVIDENCE_RELATIVE),),
        comparisonRef="icr-l26-comparison",
        scopeManifestRef="icr-l26-scope",
    )
