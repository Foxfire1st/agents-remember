"""KS-R15@v1 §6, §7 and §10.4: publication, the evidence-byte destination, and the read-back.

Every case in this module drives the **production** curator-coherence publication against a real
external-memory leaf enclosure built by the shared lifecycle fixture. Nothing here re-implements the
publication path, fakes a contract, or asserts a survival it did not read back.

The load-bearing cases are:

* the ``knowledgeReview`` surface's source -- the record's own assessment collection -- is written
  through ``curator_coherence`` ``publish`` and read back from the canonical authority on disk;
* each cited evidence byte is opened **by the task-root-relative path recorded on the assessment**
  and verified against its recorded digest, which is the read-back ``KS-R15@v1`` §7.3 requires and
  the record's own digest cannot substitute for;
* the survival read-back still resolves after the enclosure's ``reports`` directory -- the one
  terminal cleanup removes for a leaf contract -- is gone;
* the exact-coverage obligation ``_judgments_cover_candidates_exactly`` enforces is untouched, which
  is the "read-back that the existing exact-coverage obligation still refuses a mismatched judgment
  set" the packet's Expected Evidence names.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from agents_remember.application.curator_coherence import curator_coherence_tool
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRecord,
    CuratorCoherenceRequest,
    CuratorSourceCandidate,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceReference,
    AssessmentSubject,
    ReviewAssessment,
    ReviewAssessmentDisposition,
    ReviewAssessmentRevision,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.integration.closeout.curator_assessment_evidence import (
    EVIDENCE_DIRECTORY,
    AssessmentEvidenceBlockedError,
    publish_assessment_evidence_bytes,
    read_back_published_bytes,
)
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    all_assessment_subject_ids,
    curator_coherence_paths,
    curator_coherence_subject_assessment_state,
    load_curator_coherence_authority,
    require_current_curator_coherence,
    resolve_curator_evidence_ref,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from agents_remember.worktrees.route_review import build_route_review
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import WorktreeContract
from curator_coherence_test_support import (
    write_curator_evidence,
    write_curator_task_topology,
)
from pydantic import ValidationError
from test_worktree_support import open_external_contract_fixture

pytestmark = pytest.mark.integration

REPO = "repo-a"
ASSESSMENT_ID = "assessment-B-M-F1-curator"
COMPARISON = "comparison-B-M-F1"
SCOPE_MANIFEST = "scope-union-B-M-F1"
FIXTURE_EVIDENCE = "notes/reports/fixtures/retry-budget.md"
FIXTURE_EVIDENCE_TEXT = "# Retry budget fixture\n\nFour attempts, two seconds each.\n"


@pytest.fixture
def enclosure(tmp_path: Path) -> Iterator[tuple[WorktreeContract, TaskDocumentRef]]:
    """One real external-memory leaf enclosure, with its leaf document and a published authority.

    ``open_external_contract_fixture`` builds the genuine pair -- a real code repository, a real
    external memory repository, the series and leaf contracts, the worktrees and the operation
    location. The shared curator support then authors the master/sprint lineage the coherence
    validator walks, and this fixture adds the one thing that support does not: the **leaf document
    itself**, because the coherence route resolves the terminal leaf for the exact contract.
    """

    contract = open_external_contract_fixture(tmp_path)
    sprint = write_curator_task_topology(contract)
    write_task_doc(contract.task_root, _leaf_document(contract))
    assert contract.memory_worktree is not None
    (contract.memory_worktree / "onboarding").mkdir(parents=True, exist_ok=True)
    source = contract.task_root / FIXTURE_EVIDENCE
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(FIXTURE_EVIDENCE_TEXT, encoding="utf-8")
    write_curator_evidence(contract, caller_ref=sprint)
    _write_runtime_config(tmp_path, contract)
    yield contract, sprint


def _write_runtime_config(root: Path, contract: WorktreeContract) -> Path:
    """Install the minimal runtime configuration the application boundary admits a contract under."""

    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "directExecutionEnabled": False,
                "repositories": {REPO: {}},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _leaf_document(contract: WorktreeContract) -> TaskDocument:
    """Return the exact canonical leaf document the coherence route resolves for this contract."""

    slug = contract.leaf_id
    report = contract.task_root / "notes" / "reports" / f"{slug}-review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Review\n\nPass.\n", encoding="utf-8")
    document = TaskDocument.model_validate(
        {
            "id": contract.leaf_id,
            "slug": slug,
            "title": slug,
            "kind": "subTask",
            "status": "inProgress",
            "repo": REPO,
            "createdAt": "2026-09-18T00:00:00+00:00",
            "seriesContractPath": series_contract_path(contract.task_root).as_posix(),
            "enclosures": [
                {
                    "leafId": contract.leaf_id,
                    "enclosurePath": contract.contract_path.as_posix(),
                }
            ],
            "steps": [{"id": "S1", "title": "Ready", "status": "done"}],
        }
    )
    review = build_route_review(
        contract,
        ResolvedTaskDocument(
            ref=TaskDocumentRef(repository=REPO, path=f"{contract.task_name}/{slug}.json"),
            path=contract.task_root / f"{slug}.json",
            document=document,
        ),
        {
            "verdict": "pass",
            "verdictRef": f"notes/reports/{slug}-review.md",
            "routes": [
                {
                    "route": slug,
                    "verdict": "pass",
                    "evidenceRef": f"notes/reports/{slug}-review.md",
                }
            ],
        },
        now=datetime.fromisoformat("2026-09-18T00:00:00+00:00"),
    )
    return document.model_copy(update={"routeReview": review})


def _evidence_reference(spelling: str) -> AssessmentEvidenceReference:
    """Build one evidence reference from its canonical ``namespace:relative`` spelling."""

    namespace, _, ref = spelling.partition(":")
    return AssessmentEvidenceReference.model_validate({"namespace": namespace, "ref": ref})


def _revision(
    *,
    assessment_id: str = ASSESSMENT_ID,
    disposition: ReviewAssessmentDisposition = "concern_found",
    finding: str = "The combined retry configuration can exceed the five-second budget.",
    subject: AssessmentSubject | None = None,
    evidence: tuple[str, ...] = (f"task:{FIXTURE_EVIDENCE}",),
) -> ReviewAssessmentRevision:
    return ReviewAssessmentRevision(
        assessmentId=assessment_id,
        subject=subject
        or AssessmentSubject(
            kind="family",
            recordId="FAM-F",
            beforeRevisionIds=("F1",),
            afterRevisionIds=("F1",),
        ),
        disposition=disposition,
        finding=finding,
        rationale="Four attempts may each consume two seconds; no earlier shared deadline exists.",
        assumptions=("Every attempt can consume its configured timeout.",),
        evidenceRefs=tuple(_evidence_reference(spelling) for spelling in evidence),
        comparisonRef=COMPARISON,
        scopeManifestRef=SCOPE_MANIFEST,
    )


def _prepared(contract: WorktreeContract) -> dict[str, object]:
    return curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="prepare", contract_path=contract.contract_path.as_posix()),
    )


def _publish(
    contract: WorktreeContract,
    sprint: TaskDocumentRef,
    *,
    assessments: list[ReviewAssessmentRevision],
    attempt: str = "A001",
) -> dict[str, object]:
    prepared = _prepared(contract)
    candidates = list(prepared["candidates"])  # type: ignore[arg-type]
    evidence = contract.task_root / "notes" / "reports" / "fixture-curator-evidence.md"
    judgments = [
        CuratorCoherenceJudgment(
            **dict(CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json")),
            disposition="reconciled",
            rationale="The fixture candidate has one explicit reconciliation judgment.",
            evidenceRef=f"task:{evidence.relative_to(contract.task_root).as_posix()}",
        )
        for candidate in candidates
    ]
    return curator_coherence_action(
        contract,
        CuratorCoherenceRequest(
            action="publish",
            contract_path=contract.contract_path.as_posix(),
            semantic_requirement_revision="KS-R15@v1",
            delivery_attempt=attempt,
            judgments=judgments,
            review_assessments=assessments,
            expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
            expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
            expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
            expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
            expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
            expected_attestation_sha256=str(prepared["attestationSha256"]),
            caller=DeclaredCaller(role="architect", task_document_ref=sprint),
        ),
    )


def _tool_publish(
    contract: WorktreeContract,
    sprint: TaskDocumentRef,
    *,
    assessments: list[ReviewAssessmentRevision],
) -> dict[str, object]:
    """Publish through the **application** boundary, which is where a refusal becomes a wire code.

    The domain function raises and the application layer projects that into the typed refusal payload
    a caller reads, so a case that claims "refused with code X" has to go through this boundary for
    the claim to be about the code a caller actually receives.
    """

    config_path = contract.code_repo_path.parent / "settings.json"
    config = load_config(config_path)
    prepared = _prepared(contract)
    evidence = contract.task_root / "notes" / "reports" / "fixture-curator-evidence.md"
    candidates = list(prepared["candidates"])  # type: ignore[arg-type]
    judgments = [
        CuratorCoherenceJudgment(
            **dict(CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json")),
            disposition="reconciled",
            rationale="The fixture candidate has one explicit reconciliation judgment.",
            evidenceRef=f"task:{evidence.relative_to(contract.task_root).as_posix()}",
        )
        for candidate in candidates
    ]
    return curator_coherence_tool(
        config,
        CuratorCoherenceRequest(
            action="publish",
            contract_path=contract.contract_path.as_posix(),
            semantic_requirement_revision="KS-R15@v1",
            delivery_attempt="A001",
            judgments=judgments,
            review_assessments=assessments,
            expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
            expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
            expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
            expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
            expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
            expected_attestation_sha256=str(prepared["attestationSha256"]),
            caller=DeclaredCaller(role="architect", task_document_ref=sprint),
        ),
    )


def _stored(record: CuratorCoherenceRecord, assessment_id: str = ASSESSMENT_ID) -> ReviewAssessment:
    return next(
        assessment for assessment in record.assessments if assessment.assessmentId == assessment_id
    )


class TestPublishedAssessment:
    """§6.3 and §7: the record's canonical home and the evidence bytes' named destination."""

    def test_an_assessment_is_published_to_the_canonical_authority_and_reads_back(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure

        published = _publish(contract, sprint, assessments=[_revision()])

        assert published["ok"] is True, published
        assert published["state"] == "published"
        paths = curator_coherence_paths(contract)
        # The canonical record is under task_root, outside the worktree group: the tree whose
        # `reports` directory cleanup owns is a different tree from this one.
        assert paths.canonical.is_file()
        assert contract.task_root in paths.canonical.parents
        assert contract.worktree_group not in paths.canonical.parents

        validated = require_current_curator_coherence(contract)
        stored = _stored(validated.record)
        assert stored.disposition == "concern_found"
        assert stored.provenance.authorRole == "architect"
        assert stored.provenance.publicationRef == f"curator-coherence/v1:{contract.leaf_id}"
        # The authenticated caller supplies authorship; the caller text supplied none of it.
        assert stored.provenance.authorRef.startswith("architect@")

    def test_each_cited_evidence_byte_is_recorded_with_path_digest_and_size(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure

        _publish(contract, sprint, assessments=[_revision()])

        stored = _stored(load_curator_coherence_authority(contract).record)
        assert len(stored.evidenceRefs) == 1
        declared = [
            edge
            for edge in stored.examinedInputs.declaration.edges
            if edge.kind == "evidence-bytes" and edge.name.startswith(EVIDENCE_DIRECTORY.as_posix())
        ]
        assert declared, [edge.name for edge in stored.examinedInputs.declaration.edges]
        byte_path = declared[0].name
        expected = hashlib.sha256(FIXTURE_EVIDENCE_TEXT.encode()).hexdigest()
        assert declared[0].digest == expected

        # The read-back: open the recorded path and compare the digest, which is the evidence §7.3
        # requires. The record's own digest is not this.
        opened = (contract.task_root / byte_path).read_bytes()
        assert hashlib.sha256(opened).hexdigest() == expected
        assert len(opened) == len(FIXTURE_EVIDENCE_TEXT.encode())
        assert byte_path == f"{EVIDENCE_DIRECTORY.as_posix()}/{ASSESSMENT_ID}/retry-budget.md"

    def test_the_recorded_bytes_survive_removal_of_the_enclosure_reports_directory(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """§7.2 and §7.3: what cleanup removes is not where the record or its bytes live.

        The enclosure's own ``reports`` directory is the one terminal cleanup removes for a leaf
        contract. It is removed here, and the acceptance record and **each** cited byte are then
        opened again by the paths the record itself carries.
        """

        contract, sprint = enclosure
        _publish(contract, sprint, assessments=[_revision()])
        validated = require_current_curator_coherence(contract)
        stored = _stored(validated.record)
        recorded = {
            edge.name: edge.digest
            for edge in stored.examinedInputs.declaration.edges
            if edge.kind == "evidence-bytes" and edge.name.startswith(EVIDENCE_DIRECTORY.as_posix())
        }
        assert recorded

        enclosure_reports = contract.worktree_group / "reports"
        assert enclosure_reports.is_dir()
        shutil.rmtree(enclosure_reports)
        assert not enclosure_reports.exists()

        # Read back: the canonical authority, the immutable generation it selects, the assessment it
        # stores, and every byte the assessment recorded -- by the paths that record carries.
        authority_bytes = curator_coherence_paths(contract).canonical.read_bytes()
        authority = json.loads(authority_bytes)
        assert authority["currentRecordDigest"] == validated.record_digest
        reopened = CuratorCoherenceRecord.model_validate_json(
            (contract.task_root / authority["recordPath"]).read_text(encoding="utf-8")
        )
        reopened_assessment = _stored(reopened)
        assert reopened_assessment.assessmentId == ASSESSMENT_ID
        assert reopened_assessment.finding == stored.finding
        for path, digest in recorded.items():
            content = (contract.task_root / path).read_bytes()
            assert hashlib.sha256(content).hexdigest() == digest

    def test_the_shipped_evidence_resolver_opens_the_recorded_bytes_after_cleanup(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure
        _publish(contract, sprint, assessments=[_revision()])
        stored = _stored(load_curator_coherence_authority(contract).record)
        recorded = [
            edge.name
            for edge in stored.examinedInputs.declaration.edges
            if edge.kind == "evidence-bytes" and edge.name.startswith(EVIDENCE_DIRECTORY.as_posix())
        ]

        shutil.rmtree(contract.worktree_group / "reports")

        for path in recorded:
            resolved = resolve_curator_evidence_ref(contract, f"task:{path}")
            assert resolved.is_file()
            assert resolved.read_bytes()

    def test_publishing_an_assessment_does_not_disturb_the_exact_coverage_obligation(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """§8.5: the shipped refusal still refuses, with an assessment collection present.

        The record is built with a real source candidate and **no** judgment for it, which is the one
        shape ``_judgments_cover_candidates_exactly`` refuses. The assertion is deliberately made on
        a record that *already carries* an assessment: if the extension had been expressed by adding
        entries to ``judgments`` -- the economy ``design/retrieval-review-design.md:30`` forbids --
        this case could not be constructed at all, and the refusal would be unreachable.
        """

        contract, sprint = enclosure
        _publish(contract, sprint, assessments=[_revision()])
        validated = require_current_curator_coherence(contract)
        assert validated.record.assessments

        candidate = {
            "sourceFile": "mcp/src/retry.py",
            "onboardingFile": "onboarding/mcp/src/retry.py.md",
            "classification": "source-change",
        }
        with pytest.raises(ValidationError, match="exactly cover source candidates"):
            CuratorCoherenceRecord.model_validate(
                {
                    **validated.record.model_dump(mode="json", by_alias=True),
                    "sourceCandidates": [candidate],
                    "judgments": [],
                }
            )
        # ... and the positive control: the same record with its judgment restored validates, so the
        # refusal above is about coverage and not about the assessment collection.
        restored = CuratorCoherenceRecord.model_validate(
            {
                **validated.record.model_dump(mode="json", by_alias=True),
                "sourceCandidates": [candidate],
                "judgments": [
                    {
                        **candidate,
                        "disposition": "reconciled",
                        "rationale": "The change is reconciled by the shipped onboarding.",
                        "evidenceRef": f"task:{FIXTURE_EVIDENCE}",
                        "evidenceSha256": hashlib.sha256(
                            FIXTURE_EVIDENCE_TEXT.encode()
                        ).hexdigest(),
                    }
                ],
            }
        )
        assert restored.assessments

    def test_a_leaf_with_no_assessment_publishes_and_reports_none_recorded(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """4.1 and 6.9's read half: absence is a state, never a disposition."""

        contract, sprint = enclosure

        _publish(contract, sprint, assessments=[])

        validated = require_current_curator_coherence(contract)
        assert validated.record.assessments == []
        state = curator_coherence_subject_assessment_state(validated, "family:FAM-F")
        assert state.status == "none-recorded"
        assert state.assessmentCount == 0
        assert state.assessments == ()


class TestReadStatesOverAStoredCollection:
    """Requirement 4.3: the read distinguishes the states over what is actually stored."""

    def test_unresolved_is_reported_as_its_own_state_not_as_a_clearance(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure
        _publish(
            contract,
            sprint,
            assessments=[
                _revision(disposition="unresolved", finding="Could not decide the budget question.")
            ],
        )

        validated = require_current_curator_coherence(contract)
        stored = _stored(validated.record)
        measured = {ASSESSMENT_ID: dict(stored.examinedInputs.identities)}

        state = curator_coherence_subject_assessment_state(
            validated, "family:FAM-F", current=measured
        )

        # Measured current and still reported `unresolved`: the third disposition is a state of the
        # assessment, not a synonym for a stale or an absent one.
        assert state.status == "unresolved"
        assert state.unresolvedCount == 1
        assert state.staleCount == 0
        assert state.assessments[0].disposition == "unresolved"

    def test_a_measured_move_marks_the_stored_assessment_stale_and_keeps_it_readable(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """5.3: only currentness changes; the finding, author and disposition stay historical fact."""

        contract, sprint = enclosure
        _publish(contract, sprint, assessments=[_revision()])
        validated = require_current_curator_coherence(contract)
        stored = _stored(validated.record)
        measured = {
            ASSESSMENT_ID: {
                **dict(stored.examinedInputs.identities),
                ("code-tree", "candidate"): ("git-object", "9" * 40),
            }
        }

        state = curator_coherence_subject_assessment_state(
            validated, "family:FAM-F", current=measured
        )

        assert state.status == "stale"
        assert state.staleCount == 1
        assert stored.finding and stored.provenance.authorRef and stored.disposition
        # The record is still there: a mismatch is marked, never deleted to hide it.
        assert validated.record.assessments

    def test_an_unmeasured_assessment_is_reported_stale_not_current(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure
        _publish(contract, sprint, assessments=[_revision()])

        validated = require_current_curator_coherence(contract)

        assert (
            curator_coherence_subject_assessment_state(validated, "family:FAM-F").status == "stale"
        )
        assert all_assessment_subject_ids(validated) == ("family:FAM-F",)


class TestAssessmentRefusals:
    """The exact refusals the packet's Expected Evidence names, with their returned codes."""

    def test_a_submission_supplying_an_author_is_refused_as_an_undeclared_field(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure
        prepared = _prepared(contract)
        evidence = contract.task_root / "notes" / "reports" / "fixture-curator-evidence.md"
        candidates = list(prepared["candidates"])  # type: ignore[arg-type]
        judgments = [
            CuratorCoherenceJudgment(
                **dict(CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json")),
                disposition="reconciled",
                rationale="The fixture candidate has one explicit reconciliation judgment.",
                evidenceRef=f"task:{evidence.relative_to(contract.task_root).as_posix()}",
            )
            for candidate in candidates
        ]

        with pytest.raises(ValidationError) as refusal:
            CuratorCoherenceRequest(
                action="publish",
                contract_path=contract.contract_path.as_posix(),
                semantic_requirement_revision="KS-R15@v1",
                delivery_attempt="A001",
                judgments=judgments,
                # The submission is deliberately not a revision of the declared shape: it is the
                # serialized form a caller sends with an author smuggled in, and the request model is
                # what must refuse it as an undeclared field.
                review_assessments=cast(
                    "list[ReviewAssessmentRevision]",
                    [
                        {
                            **_revision().model_dump(mode="json"),
                            "authorRef": "somebody-else@leaf",
                        }
                    ],
                ),
                expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
                expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
                expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
                expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
                expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
                expected_attestation_sha256=str(prepared["attestationSha256"]),
                caller=DeclaredCaller(role="architect", task_document_ref=sprint),
            )

        assert "authorRef" in str(refusal.value)

    def test_an_assessment_citing_no_evidence_is_refused_with_nothing_written(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """1.3's "no row written": the canonical authority is absent afterwards, not stale."""

        contract, sprint = enclosure
        before = curator_coherence_paths(contract).canonical.read_bytes()

        refused = _tool_publish(contract, sprint, assessments=[_revision(evidence=())])

        assert refused["ok"] is False
        assert refused["status"] == "review-assessment-binding-incomplete"
        # "No row written" measured: the canonical authority is byte-identical afterwards and no
        # generation was appended, so the subject's state is still whatever it was.
        assert curator_coherence_paths(contract).canonical.read_bytes() == before
        assert require_current_curator_coherence(contract).record.assessments == []

    def test_two_assessments_with_one_identity_are_refused(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure

        before = curator_coherence_paths(contract).canonical.read_bytes()

        refused = _tool_publish(
            contract,
            sprint,
            assessments=[_revision(), _revision(finding="A second concern for one identity.")],
        )

        assert refused["ok"] is False
        assert refused["status"] == "review-assessment-duplicate-identity"
        assert curator_coherence_paths(contract).canonical.read_bytes() == before
        assert require_current_curator_coherence(contract).record.assessments == []

    def test_a_citation_the_resolver_refuses_cannot_be_published(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure

        before = curator_coherence_paths(contract).canonical.read_bytes()

        refused = _tool_publish(
            contract,
            sprint,
            assessments=[_revision(evidence=("task:notes/reports/fixtures/absent.md",))],
        )

        assert refused["ok"] is False
        assert refused["status"] == "curator-coherence-evidence-invalid"
        assert curator_coherence_paths(contract).canonical.read_bytes() == before

    def test_a_byte_that_does_not_read_back_blocks_with_the_destination_and_digest(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """§6.7 and §7.4: a blocked item carries the destination, the expected digest and the state."""

        contract, _sprint = enclosure
        references = _revision().evidenceRefs
        publication = publish_assessment_evidence_bytes(contract, ASSESSMENT_ID, references)

        # Damage the published byte the way an outside writer would, then read it back.
        recorded = publication.published[0].byte
        (contract.task_root / recorded.path).write_bytes(b"tampered\n")

        with pytest.raises(AssessmentEvidenceBlockedError) as blocked:
            read_back_published_bytes(contract, publication)

        assert blocked.value.status == "review-assessment-evidence-read-back-mismatch"
        assert blocked.value.expected["path"] == recorded.path
        assert blocked.value.expected["sha256"] == recorded.sha256
        assert blocked.value.observed["state"] == "present-but-different"

    def test_the_published_copy_is_the_cited_bytes_by_content(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """A published byte is a copy at the 6.6 destination, and it is the *cited* bytes.

        The two facts the record carries are different on purpose -- the citation names what the
        curator read, the path names what the substrate kept -- so the thing that makes the copy the
        cited bytes is content, and this measures it: the recorded digest is the cited file's digest,
        and the published copy hashes to it.
        """

        contract, _sprint = enclosure
        publication = publish_assessment_evidence_bytes(
            contract, ASSESSMENT_ID, _revision().evidenceRefs
        )

        cited = (contract.task_root / FIXTURE_EVIDENCE).read_bytes()
        recorded = publication.published[0].byte
        assert recorded.sha256 == hashlib.sha256(cited).hexdigest()
        assert recorded.size == len(cited)
        assert recorded.path != FIXTURE_EVIDENCE
        assert (contract.task_root / recorded.path).read_bytes() == cited

    def test_a_citation_naming_a_worktree_file_is_published_to_the_surviving_tree(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        """The reason the destination exists: a `code:` citation lives in a tree cleanup removes."""

        contract, _sprint = enclosure
        cited = contract.code_worktree / "feature.txt"
        cited.write_text("cited bytes\n", encoding="utf-8")

        publication = publish_assessment_evidence_bytes(
            contract,
            ASSESSMENT_ID,
            (AssessmentEvidenceReference(namespace="code", ref="feature.txt"),),
        )

        recorded = publication.published[0].byte
        assert recorded.path.startswith(EVIDENCE_DIRECTORY.as_posix())
        assert contract.task_root in (contract.task_root / recorded.path).parents
        assert (contract.task_root / recorded.path).read_text(encoding="utf-8") == "cited bytes\n"

    def test_a_published_byte_removed_after_publication_is_a_blocked_absent_state(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, _sprint = enclosure
        publication = publish_assessment_evidence_bytes(
            contract, ASSESSMENT_ID, _revision().evidenceRefs
        )
        recorded = publication.published[0].byte
        (contract.task_root / recorded.path).unlink()

        with pytest.raises(AssessmentEvidenceBlockedError) as blocked:
            read_back_published_bytes(contract, publication)

        assert blocked.value.status == "review-assessment-evidence-read-back-absent"
        assert blocked.value.expected["path"] == recorded.path
        assert blocked.value.observed["state"] == "absent-or-unreadable"

    def test_an_assessment_identity_that_cannot_name_a_directory_is_refused(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, _sprint = enclosure

        for assessment_id in ("../escape", "nested/name", ".hidden", ""):
            with pytest.raises(AssessmentEvidenceBlockedError) as blocked:
                publish_assessment_evidence_bytes(contract, assessment_id, _revision().evidenceRefs)
            assert blocked.value.status == "review-assessment-evidence-destination-invalid"


class TestPublisherCallerAddress:
    """D-26: the caller refusal states the canonical path shape it demands.

    A publication binds one exact leaf, but ``caller.task_document_ref.path`` has to be the
    task-root-relative document path (``<task-slug>/<leaf-document-file>``). Nothing said so -- not
    the refusal, not ``prepare``, not ``status``, not the contract -- so the only way to learn it was
    the refused call itself. The contract already identifies the leaf unambiguously, so a bare file
    name that is exactly the addressed document's own name is resolved to it; anything else is
    refused with the shape **and** this contract's exact expected value.
    """

    def test_a_bare_leaf_document_name_is_resolved_against_the_contract(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, _sprint = enclosure
        # The task directory's own name, which is what a canonical ref path is relative to.
        canonical = f"{contract.task_root.name}/{contract.leaf_id}.json"
        prepared = _prepared(contract)
        candidates = list(prepared["candidates"])  # type: ignore[arg-type]
        evidence = contract.task_root / "notes" / "reports" / "fixture-curator-evidence.md"
        judgments = [
            CuratorCoherenceJudgment(
                **dict(CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json")),
                disposition="reconciled",
                rationale="The fixture candidate has one explicit reconciliation judgment.",
                evidenceRef=f"task:{evidence.relative_to(contract.task_root).as_posix()}",
            )
            for candidate in candidates
        ]

        payload = curator_coherence_action(
            contract,
            CuratorCoherenceRequest(
                action="publish",
                contract_path=contract.contract_path.as_posix(),
                semantic_requirement_revision="KS-R15@v1",
                delivery_attempt="A001",
                judgments=judgments,
                expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
                expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
                expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
                expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
                expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
                expected_attestation_sha256=str(prepared["attestationSha256"]),
                caller=DeclaredCaller(
                    role="curator",
                    task_document_ref=TaskDocumentRef(
                        repository=REPO, path=f"{contract.leaf_id}.json"
                    ),
                ),
            ),
        )

        assert payload["state"] == "published"
        record = load_curator_coherence_authority(contract).record
        assert record.taskDocumentRef.path == canonical
        # The resolved ref is what the record (and any assessment's author identity) carries, not
        # the bare spelling the caller supplied.
        assert record.publishedBy == f"curator@{REPO}/{canonical}"

    def test_a_bare_name_for_another_leaf_is_refused_with_the_canonical_path(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, _sprint = enclosure

        payload = _tool_publish_bare_name(contract, f"{contract.leaf_id}-not-the-leaf.json")

        assert payload["state"] == "refused"
        assert payload["status"] == "curator-coherence-caller-refused"
        detail = str(payload["detail"])
        canonical = f"{contract.task_root.name}/{contract.leaf_id}.json"
        assert "<task-slug>/<leaf-document-file>" in detail
        assert canonical in detail
        assert payload["expected"]["taskDocumentRef"]["path"] == canonical
        assert (
            payload["observed"]["taskDocumentRef"]["path"]
            == f"{contract.leaf_id}-not-the-leaf.json"
        )


def _tool_publish_bare_name(
    contract: WorktreeContract,
    bare_path: str,
) -> dict[str, object]:
    """Publish through the application boundary with a bare-name caller (where the code is read)."""

    config_path = contract.code_repo_path.parent / "settings.json"
    config = load_config(config_path)
    prepared = _prepared(contract)
    candidates = list(prepared["candidates"])  # type: ignore[arg-type]
    evidence = contract.task_root / "notes" / "reports" / "fixture-curator-evidence.md"
    judgments = [
        CuratorCoherenceJudgment(
            **dict(CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json")),
            disposition="reconciled",
            rationale="The fixture candidate has one explicit reconciliation judgment.",
            evidenceRef=f"task:{evidence.relative_to(contract.task_root).as_posix()}",
        )
        for candidate in candidates
    ]
    return curator_coherence_tool(
        config,
        CuratorCoherenceRequest(
            action="publish",
            contract_path=contract.contract_path.as_posix(),
            semantic_requirement_revision="KS-R15@v1",
            delivery_attempt="A001",
            judgments=judgments,
            expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
            expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
            expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
            expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
            expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
            expected_attestation_sha256=str(prepared["attestationSha256"]),
            caller=DeclaredCaller(
                role="curator",
                task_document_ref=TaskDocumentRef(repository=REPO, path=bare_path),
            ),
        ),
    )


class TestAttestationDurability:
    """The bound attestation must outlive the enclosure whose path the record commits to.

    Every authority published in this accumulation binds an ``attestationPath`` inside its own leaf
    enclosure, and ``lifecycle_finalize_task`` reclaims that enclosure -- so after cleanup the
    record's ``attestationSha256`` names bytes that exist nowhere. This is D-25's family: the same
    cleanup that closes the ``validate`` window destroys the evidence a publication binds to.
    """

    def test_the_bound_attestation_is_copied_into_the_surviving_tree_and_reads_back_after_cleanup(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure
        _publish(contract, sprint, assessments=[_revision()])
        validated = require_current_curator_coherence(contract)
        record = validated.record

        assert record.attestationCopyPath is not None
        assert contract.task_root in (contract.task_root / record.attestationCopyPath).parents
        # The enclosure-local file the record binds, named by the record itself.
        bound = Path(record.attestationPath)
        assert contract.worktree_group in bound.parents
        assert bound.is_file()
        assert hashlib.sha256(bound.read_bytes()).hexdigest() == record.attestationSha256

        shutil.rmtree(contract.worktree_group / "reports")

        # What the envelope's own cleanup removes is gone; what the record carries is not.
        assert not bound.exists()
        copied = contract.task_root / record.attestationCopyPath
        payload = copied.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == record.attestationSha256
        attestation = json.loads(payload)
        assert [candidate["sourceFile"] for candidate in attestation["sourceChangeCandidates"]] == [
            candidate.sourceFile for candidate in record.sourceCandidates
        ]
        assert record.attestationPath != record.attestationCopyPath

    def test_the_copy_is_content_addressed_so_a_republish_does_not_rewrite_it(
        self, enclosure: tuple[WorktreeContract, TaskDocumentRef]
    ) -> None:
        contract, sprint = enclosure
        _publish(contract, sprint, assessments=[_revision()])
        first = require_current_curator_coherence(contract).record
        assert first.attestationCopyPath is not None
        before = (contract.task_root / first.attestationCopyPath).read_bytes()

        # A second publication over the same bound attestation reuses the same immutable copy.
        _publish(contract, sprint, assessments=[_revision()], attempt="A002")
        second = require_current_curator_coherence(contract).record
        assert second.attestationCopyPath == first.attestationCopyPath
        assert (contract.task_root / second.attestationCopyPath).read_bytes() == before
