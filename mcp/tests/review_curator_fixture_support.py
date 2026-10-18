"""Curator publication fixtures, independent of the retired review record channels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.declared_caller import DeclaredCaller
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
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from agents_remember_test_support.testing.review_git_trees import INVARIANT, World, build_world
from curator_coherence_test_support import (
    prepared_publish_inputs,
    read_prepared_candidate_set,
    write_curator_task_topology,
)
from test_worktree_support import write_passing_route_review

EVIDENCE_RELATIVE = "notes/reports/icr-l14-evidence.md"
EVIDENCE_TEXT = "# fixture evidence\n\nthe record this assessment cites\n"
ASSESSMENT_ID = "ICR-L14-AS-1"
ASSESSMENT_FINDING = "The retry budget is still shared, and the candidate moved the constant."


@dataclass(frozen=True)
class RecordedFixture:
    endpoint: World
    contract: WorktreeContract
    sprint: TaskDocumentRef


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
    candidates = list(read_prepared_candidate_set(prepared)["candidates"])  # type: ignore[arg-type]
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
            **prepared_publish_inputs(prepared),
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
