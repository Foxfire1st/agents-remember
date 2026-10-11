"""MIK-R52 F04/F09 rule 12: maximum coherence publication, oversize refusal, old record.

The bound covers every record builder whose model bounds its payload: the coherence record
builder declares at most its fixed edges plus one evidence-bytes edge per admitted judgment
(2048) plus one review-record edge per admitted assessment (256) plus its validator edges,
so MAX_EVIDENCE_DEPENDENCIES is exactly 7 + 2048 + 256 + 1 = 2312. The master-scope route
review has no bound of its own; it remains limited by this shared constant. Each test drives
the production publication path, so removing the bound or the check makes it fail.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.errors import CuratorCoherenceError
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRecord,
    CuratorCoherenceRequest,
    CuratorSourceCandidate,
)
from agents_remember.models.lifecycles.evidence_dependencies import (
    MAX_CURATOR_REVIEW_ASSESSMENTS,
    MAX_CURATOR_SOURCE_CANDIDATES,
    MAX_EVIDENCE_DEPENDENCIES,
    require_evidence_dependencies,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceReference,
    AssessmentSubject,
    ReviewAssessmentRevision,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    curator_coherence_paths,
    load_curator_coherence_authority,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    _require_payload_bounds,
    curator_coherence_action,
)
from agents_remember.worktrees.route_review import build_route_review
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import WorktreeContract
from curator_coherence_test_support import (
    write_curator_evidence,
    write_curator_task_topology,
)
from pydantic import ValidationError
from test_worktree_support import open_external_contract_fixture

REPO = "repo-a"
FIXTURE_EVIDENCE = "notes/reports/fixtures/retry-budget.md"
FIXTURE_EVIDENCE_TEXT = "# Retry budget fixture\n\nFour attempts, two seconds each.\n"
# Exact bytes of a real pre-derivation coherence record (leaf 260928-MIK-L39, MIK-R39@v1 era):
# notes/reports/curator-coherence/260928-MIK-L39/generations/407e11c3…/record.json.
OLD_RECORD_FIXTURE = Path(__file__).parent / "fixtures" / "old_coherence_record_l39.json"

DIGEST = "ab" * 32


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


@pytest.fixture
def enclosure(tmp_path: Path) -> Iterator[tuple[WorktreeContract, TaskDocumentRef]]:
    """One real external-memory leaf enclosure with leaf document and fixture evidence."""

    contract = open_external_contract_fixture(tmp_path)
    sprint = write_curator_task_topology(contract)
    write_task_doc(contract.task_root, _leaf_document(contract))
    assert contract.memory_worktree is not None
    (contract.memory_worktree / "onboarding").mkdir(parents=True, exist_ok=True)
    source = contract.task_root / FIXTURE_EVIDENCE
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(FIXTURE_EVIDENCE_TEXT, encoding="utf-8")
    yield contract, sprint


def _leaf_document(contract: WorktreeContract) -> TaskDocument:
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


def _candidates(count: int) -> list[dict[str, str]]:
    return [
        {
            "sourceFile": f"src/max-{index:04d}.py",
            "onboardingFile": f"onboarding/max-{index:04d}.md",
            "classification": "content",
        }
        for index in range(count)
    ]


def _revision(index: int) -> ReviewAssessmentRevision:
    return ReviewAssessmentRevision(
        assessmentId=f"assessment-max-{index:03d}",
        subject=AssessmentSubject(
            kind="family",
            recordId=f"FAM-MAX-{index:03d}",
            beforeRevisionIds=("F1",),
            afterRevisionIds=("F1",),
        ),
        disposition="concern_found",
        finding=f"Maximum-capacity assessment {index:03d} holds its stated bound.",
        rationale="Each of the 256 admitted assessments is bound exactly once.",
        assumptions=("Every assessment cites the shared fixture evidence.",),
        evidenceRefs=(
            AssessmentEvidenceReference.model_validate(
                {"namespace": "task", "ref": FIXTURE_EVIDENCE}
            ),
        ),
        comparisonRef="comparison-max",
        scopeManifestRef="scope-union-max",
    )


def _write_evidence_files(contract: WorktreeContract, count: int) -> None:
    for index in range(count):
        path = contract.task_root / f"notes/reports/fixture-evidence-{index:04d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Fixture evidence {index:04d}\n\nBounded payload byte.\n", encoding="utf-8")


def _publish_max(
    contract: WorktreeContract,
    sprint: TaskDocumentRef,
    *,
    judgments: int,
    assessments: int,
    attempt: str = "A001",
) -> dict[str, object]:
    prepared = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="prepare", contract_path=contract.contract_path.as_posix()),
    )
    # MIK-R110 rule 3: prepare answers the candidate-set file location, not the
    # candidates inline. Read the envelope for the candidates + expected fields.
    envelope = json.loads(Path(str(prepared["candidatesPath"])).read_text(encoding="utf-8"))
    candidates = list(envelope["candidates"])  # type: ignore[arg-type]
    assert len(candidates) == judgments
    expected = envelope["expected"]
    published_judgments = [
        CuratorCoherenceJudgment(
            **dict(CuratorSourceCandidate.model_validate(candidate).model_dump(mode="json")),
            disposition="reconciled",
            rationale="The maximum-capacity candidate has one explicit judgment.",
            evidenceRef=f"task:notes/reports/fixture-evidence-{index:04d}.md",
        )
        for index, candidate in enumerate(candidates)
    ]
    return curator_coherence_action(
        contract,
        CuratorCoherenceRequest(
            action="publish",
            contract_path=contract.contract_path.as_posix(),
            semantic_requirement_revision="KS-R15@v1",
            delivery_attempt=attempt,
            judgments=published_judgments,
            review_assessments=[_revision(index) for index in range(assessments)],
            expected_predecessor_digest=str(expected["predecessorAuthorityDigest"]),
            expected_code_candidate_tree=str(expected["codeCandidateTree"]),
            expected_memory_candidate_tree=str(expected["memoryCandidateTree"]),
            expected_task_topology_fingerprint=str(expected["taskTopologyFingerprint"]),
            expected_task_intent=TaskIntentIdentity.model_validate(expected["taskIntent"]),
            expected_attestation_sha256=str(expected["attestationSha256"]),
            caller=DeclaredCaller(role="architect", task_document_ref=sprint),
        ),
    )


def _destination_snapshot(contract: WorktreeContract) -> dict[str, int]:
    destination = contract.task_root / "notes" / "reports" / "evidence"
    if not destination.is_dir():
        return {}
    return {
        path.relative_to(destination).as_posix(): path.stat().st_size
        for path in sorted(destination.rglob("*"))
        if path.is_file()
    }


def test_capacity_constant_is_derived_from_the_coherence_record_bounds(ports: None) -> None:
    """F04 derived-constant: 2312 is exactly fixed + judgments + assessments + validator."""

    assert MAX_CURATOR_SOURCE_CANDIDATES == 2048
    assert MAX_CURATOR_REVIEW_ASSESSMENTS == 256
    assert MAX_EVIDENCE_DEPENDENCIES == 7 + 2048 + 256 + 1 == 2312


def test_maximum_coherence_publication_validates_at_capacity(
    enclosure: tuple[WorktreeContract, TaskDocumentRef], ports: None
) -> None:
    """F04 maximum-capacity: a real coherence record with 2048 judgments and 256 assessments
    publishes through the production action and validates back as valid.

    Distinct evidence bytes per judgment (one file each) keep all 2048 evidence edges;
    a first small publication supplies the predecessor edge, so the record carries the
    full derived bound: 6 fixed + 2048 evidence-bytes + 256 review-record + 1 validator
    + 1 predecessor = 2312."""

    contract, sprint = enclosure
    write_curator_evidence(contract, caller_ref=sprint, source_candidates=_candidates(1))
    _write_evidence_files(contract, 1)
    first = _publish_max(contract, sprint, judgments=1, assessments=0, attempt="A000")
    assert first["state"] in ("published", "already-current"), first["state"]
    write_curator_evidence(
        contract, caller_ref=sprint, source_candidates=_candidates(2048)
    )
    _write_evidence_files(contract, 2048)
    published = _publish_max(contract, sprint, judgments=2048, assessments=256)
    assert published["state"] in ("published", "already-current"), published["state"]
    validated = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="validate", contract_path=contract.contract_path.as_posix()),
    )
    assert validated["state"] == "valid", validated
    authority = load_curator_coherence_authority(contract)
    declaration = require_evidence_dependencies(
        authority.record.dependencies, record_type="curator-coherence/v1"
    )
    assert len(declaration.edges) == MAX_EVIDENCE_DEPENDENCIES == 2312


def test_oversize_publication_refuses_before_any_write(
    enclosure: tuple[WorktreeContract, TaskDocumentRef], ports: None
) -> None:
    """F04 oversize no-write: one judgment past capacity is refused by the model bound naming
    the bound and the count, with the evidence destination and the canonical record unchanged."""

    contract, sprint = enclosure
    write_curator_evidence(contract, caller_ref=sprint, source_candidates=_candidates(1))
    paths = curator_coherence_paths(contract)
    before_destination = _destination_snapshot(contract)
    before_record = paths.canonical.read_bytes() if paths.canonical.exists() else None
    oversize = _candidates(MAX_CURATOR_SOURCE_CANDIDATES + 1)
    with pytest.raises(ValidationError) as refused:
        CuratorCoherenceRequest(
            action="publish",
            contract_path=contract.contract_path.as_posix(),
            judgments=cast(Any, oversize),
            review_assessments=[],
        )
    assert "2048" in str(refused.value)
    assert _destination_snapshot(contract) == before_destination
    after_record = paths.canonical.read_bytes() if paths.canonical.exists() else None
    assert after_record == before_record


def test_oversize_payload_bound_refuses_by_name(ports: None) -> None:
    """F04 runtime bound pin: past-capacity counts refuse naming bound and count even below
    the publication path, so weakening the check fails this test."""

    with pytest.raises(CuratorCoherenceError) as refused:
        _require_payload_bounds([object()] * 2049, [])
    assert "at most 2048" in str(refused.value)
    assert "2049" in str(refused.value)
    with pytest.raises(CuratorCoherenceError) as refused_assessments:
        _require_payload_bounds([], [object()] * 257)
    assert "at most 256" in str(refused_assessments.value)
    assert "257" in str(refused_assessments.value)


def test_old_coherence_record_still_loads(ports: None) -> None:
    """F04/F09 old-record loading: a real pre-derivation coherence record (leaf 260928-MIK-L39,
    MIK-R39@v1 era, bytes vendored exactly) loads through the production record model and its
    dependency declaration still validates under the derived bound."""

    raw = OLD_RECORD_FIXTURE.read_bytes()
    assert len(raw) > 60000
    record = CuratorCoherenceRecord.model_validate(json.loads(raw))
    assert record.leafId == "260928-MIK-L39"
    declaration = require_evidence_dependencies(
        record.dependencies, record_type="curator-coherence/v1"
    )
    assert 0 < len(declaration.edges) <= MAX_EVIDENCE_DEPENDENCIES
