"""One structured curator-coherence authority shared by memory and closeout."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from agents_remember.errors import (
    CuratorCoherenceError,
    CuratorCoherencePairError,
    FutureCodeCandidateError,
    MemoryCandidatePairError,
    TaskIntentError,
)
from agents_remember.models.closeout.source import EvidenceFact
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceAuthority,
    CuratorCoherencePaths,
    CuratorCoherenceRecord,
    CuratorQualityAttestation,
    CuratorSourceCandidate,
    ValidatedCuratorCoherence,
    ValidatedCuratorCoherenceGeneration,
    require_memory_quality_attestation_dependencies,
)
from agents_remember.models.lifecycles.evidence_dependencies import (
    EVIDENCE_DEPENDENCY_VALIDATOR,
    EvidenceDependencyError,
    build_evidence_dependencies,
    canonical_sha256,
    dependency,
    require_evidence_dependencies,
)
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEntry,
    ReviewAssessment,
    SubjectAssessmentState,
    assessment_state_for,
    assessment_subject_id,
)
from agents_remember.models.lifecycles.review_assessment_binding import (
    supplied_measurement_statuses,
)
from agents_remember.models.lifecycles.review_assessment_store import (
    assessment_edge_name,
    recorded_assessment_digest,
)
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.tasks.task_intent import (
    require_current_task_intent,
    task_intent_identity,
)
from agents_remember.worktrees.modules.future_code_candidate import (
    capture_future_code_candidate,
)
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.modules.memory_candidate_pair import (
    resolve_memory_candidate_pair,
)
from agents_remember.worktrees.queue.closeout_projection_members import (
    candidate_task_topology_fingerprint,
)
from agents_remember.worktrees.queue.closeout_queue_graph import graph_context
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .curator_coherence_judgments import require_recorded_judgments_current
from .curator_coherence_paths import (
    curator_coherence_paths,
    require_leaf_external_memory,
    resolve_curator_evidence_ref,
)
from .curator_coherence_records import read_curator_coherence_generation

QUALITY_REPORT_NAME = "curator-memory-quality.md"
QUALITY_ATTESTATION_NAME = "curator-memory-quality.json"


@dataclass(frozen=True)
class CuratorCoherenceObservation:
    candidate: ResolvedTaskDocument
    pair_identity: MemoryCandidatePairIdentity
    code_candidate_tree: str
    memory_candidate_tree: str
    task_topology_fingerprint: str
    task_intent: TaskIntentIdentity
    attestation_path: Path
    attestation_sha256: str
    quality_report_path: Path
    attestation: CuratorQualityAttestation

    @property
    def source_candidates(self) -> list[CuratorSourceCandidate]:
        return self.attestation.sourceChangeCandidates


@dataclass(frozen=True)
class _QualityAttestationSource:
    attestation_path: Path
    report_path: Path
    pair_identity: MemoryCandidatePairIdentity
    code_candidate_tree: str
    memory_candidate_tree: str


@dataclass(frozen=True)
class CuratorCoherenceNoImpact:
    """Candidate-bound no-impact judgments accepted by onboarding body gates."""

    content_sources: frozenset[str] = frozenset()
    source_routes: frozenset[str] = frozenset()


def curator_coherence_no_impact(
    validated: ValidatedCuratorCoherence,
) -> CuratorCoherenceNoImpact:
    """Project exact current coherence judgments into the two no-impact lanes."""

    judgments = validated.record.judgments
    return CuratorCoherenceNoImpact(
        content_sources=frozenset(
            judgment.sourceFile
            for judgment in judgments
            if judgment.disposition == "no-content-impact"
        ),
        source_routes=frozenset(
            judgment.sourceFile
            for judgment in judgments
            if judgment.disposition == "no-route-impact"
        ),
    )


def observe_curator_coherence_source(
    contract: WorktreeContract,
) -> CuratorCoherenceObservation:
    """Capture every identity a publication must freeze and later re-prove."""

    require_leaf_external_memory(contract)
    try:
        pair_identity = resolve_memory_candidate_pair(
            contract,
            requested_contract_path=contract.contract_path,
            requested_repo_id=contract.repo_name,
        )
    except MemoryCandidatePairError as exc:
        raise CuratorCoherencePairError(exc) from exc
    candidate, master, sprint, graph = _task_context(contract)
    try:
        code_tree = capture_future_code_candidate(contract).codeCandidateTree
        memory_tree = _memory_candidate_tree(contract)
    except FutureCodeCandidateError as exc:
        raise CuratorCoherenceError(exc.status, str(exc), next_action="prepare") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise CuratorCoherenceError(
            "curator-coherence-candidate-unreadable",
            "could not capture the exact code and memory candidates",
            next_action="prepare",
        ) from exc
    attestation_path = contract.worktree_group / "reports" / QUALITY_ATTESTATION_NAME
    report_path = contract.worktree_group / "reports" / QUALITY_REPORT_NAME
    attestation, attestation_digest = _quality_attestation(
        contract,
        _QualityAttestationSource(
            attestation_path=attestation_path,
            report_path=report_path,
            pair_identity=pair_identity,
            code_candidate_tree=code_tree,
            memory_candidate_tree=memory_tree,
        ),
    )
    topology_fingerprint = candidate_task_topology_fingerprint(
        sprint,
        master,
        candidate,
        graph=graph,
    )
    try:
        intent = task_intent_identity(contract.task_root, candidate)
    except TaskIntentError as exc:
        raise CuratorCoherenceError(
            exc.status,
            exc.detail,
            next_action=exc.next_action or "task_doc",
        ) from exc
    return CuratorCoherenceObservation(
        candidate=candidate,
        pair_identity=pair_identity,
        code_candidate_tree=code_tree,
        memory_candidate_tree=memory_tree,
        task_topology_fingerprint=topology_fingerprint,
        task_intent=intent,
        attestation_path=attestation_path,
        attestation_sha256=attestation_digest,
        quality_report_path=report_path,
        attestation=attestation,
    )


def current_curator_coherence_predecessor(contract: WorktreeContract) -> str:
    """Digest the stable authority bytes, including malformed bytes, for exact replacement CAS."""

    path = curator_coherence_paths(contract).canonical
    if not path.exists():
        return ""
    try:
        return _digest(path.read_bytes())
    except OSError as exc:
        raise CuratorCoherenceError(
            "curator-coherence-authority-unreadable",
            "the stable authority exists but its bytes cannot be read for replacement",
            next_action="developer-decision",
        ) from exc


def load_curator_coherence_authority(
    contract: WorktreeContract,
) -> ValidatedCuratorCoherence:
    """Validate authority, immutable generation, and generated projection bytes."""

    paths = curator_coherence_paths(contract)
    try:
        authority_bytes = paths.canonical.read_bytes()
        authority = CuratorCoherenceAuthority.model_validate_json(authority_bytes)
    except (OSError, ValidationError) as exc:
        raise CuratorCoherenceError(
            "curator-coherence-authority-missing",
            f"the stable structured authority is absent or unreadable: {paths.canonical}",
            next_action="publish",
        ) from exc
    _require_authority_identity(contract, authority)
    record_path = paths.generation_record(authority.currentRecordDigest)
    report_path = paths.generation_report(authority.currentRecordDigest)
    expected_record_ref = _task_relative(contract, record_path)
    expected_report_ref = _task_relative(contract, report_path)
    if (authority.recordPath, authority.reportPath) != (
        expected_record_ref,
        expected_report_ref,
    ):
        raise CuratorCoherenceError(
            "curator-coherence-authority-path-invalid",
            "the stable authority does not identify its exact content-addressed generation",
            expected={"recordPath": expected_record_ref, "reportPath": expected_report_ref},
            observed={"recordPath": authority.recordPath, "reportPath": authority.reportPath},
            next_action="publish",
        )
    generation = load_curator_coherence_generation(contract, authority.currentRecordDigest)
    if generation.record.reportSha256 != authority.reportSha256:
        raise CuratorCoherenceError(
            "curator-coherence-projection-digest-mismatch",
            "the live authority names a different generated projection digest",
            next_action="publish",
        )
    return ValidatedCuratorCoherence(
        authority=authority,
        record=generation.record,
        record_path=generation.record_path,
        report_path=generation.report_path,
        record_digest=generation.record_digest,
        evidence=[_fact(paths.canonical), *generation.evidence],
    )


def load_curator_coherence_generation(
    contract: WorktreeContract, record_digest: str
) -> ValidatedCuratorCoherenceGeneration:
    """Read an exact immutable generation without consulting the live pointer or live readiness."""

    return read_curator_coherence_generation(
        contract, curator_coherence_paths(contract), record_digest
    )


def require_current_curator_coherence(
    contract: WorktreeContract,
) -> ValidatedCuratorCoherence:
    """The single validator used by public validation, preflight, and admission."""

    observation = observe_curator_coherence_source(contract)
    validated = load_curator_coherence_authority(contract)
    record = validated.record
    try:
        require_current_task_intent(
            record.taskIntent,
            observation.task_intent,
            owner="curator-coherence",
            next_action="publish",
        )
    except TaskIntentError as exc:
        raise CuratorCoherenceError(
            exc.status,
            exc.detail,
            next_action=exc.next_action or "publish",
        ) from exc
    _require_current_dependencies(record)
    require_recorded_judgments_current(contract, record.judgments)
    observed = {
        "pairIdentity": observation.pair_identity.model_dump(mode="json"),
        "codeCandidateTree": observation.code_candidate_tree,
        "memoryCandidateTree": observation.memory_candidate_tree,
        "taskTopologyFingerprint": observation.task_topology_fingerprint,
        "taskIntent": observation.task_intent.model_dump(mode="json", by_alias=True),
        "attestationSha256": observation.attestation_sha256,
        "sourceCandidates": [
            candidate.model_dump(mode="json") for candidate in observation.source_candidates
        ],
    }
    expected = {
        "pairIdentity": record.pairIdentity.model_dump(mode="json"),
        "codeCandidateTree": record.codeCandidateTree,
        "memoryCandidateTree": record.memoryCandidateTree,
        "taskTopologyFingerprint": record.taskTopologyFingerprint,
        "taskIntent": record.taskIntent.model_dump(mode="json", by_alias=True),
        "attestationSha256": record.attestationSha256,
        "sourceCandidates": [
            candidate.model_dump(mode="json") for candidate in record.sourceCandidates
        ],
    }
    if observed != expected:
        raise CuratorCoherenceError(
            "curator-coherence-stale",
            "the live coherence authority does not bind the exact current candidate sources",
            expected=expected,
            observed=observed,
            next_action="publish",
        )
    if record.taskDocumentRef != observation.candidate.ref:
        raise CuratorCoherenceError(
            "curator-coherence-task-mismatch",
            "the live coherence record names a different leaf task document",
            next_action="publish",
        )
    return validated


def _require_current_dependencies(record: CuratorCoherenceRecord) -> None:
    """Require the record's declared edges to equal the inputs its validator reads.

    A stored assessment's edge is recomputed from the record the edge points at: the digest in
    ``review-record`` is the assessment's own content address, so a reader can tell that the stored
    assessment is the one the record declared rather than a record that was edited beside it. That is
    the *record's* dependency and it is one-directional -- the assessment's own binding declares the
    inputs it examined and deliberately does not declare the record it lives in, because a record
    citing itself is the self-invalidating sequence ``design/retrieval-review-design.md:368`` exists
    to avoid.
    """

    if not isinstance(record.taskIntent, TaskIntentIdentity):
        raise CuratorCoherenceError(
            "curator-coherence-task-intent-missing",
            "curator coherence has no digest-bearing task intent dependency",
            next_action="publish",
        )
    evidence = {judgment.evidenceRef: judgment.evidenceSha256 for judgment in record.judgments}
    review_edges = {
        assessment_edge_name(assessment.assessmentId): recorded_assessment_digest(assessment)
        for assessment in record.assessments
    }
    try:
        expected = build_evidence_dependencies(
            "curator-coherence/v1",
            [
                dependency(
                    "code-tree",
                    "candidate",
                    record.codeCandidateTree,
                    algorithm="git-object",
                ),
                dependency(
                    "memory-tree",
                    "candidate",
                    record.memoryCandidateTree,
                    algorithm="git-object",
                ),
                dependency(
                    "semantic-topology",
                    "leaf-placement",
                    record.taskTopologyFingerprint,
                ),
                dependency("task-intent", "leaf", record.taskIntent.digest),
                dependency(
                    "memory-attestation",
                    record.attestationPath,
                    record.attestationSha256,
                ),
                dependency(
                    "evidence-bytes",
                    "memory-quality-report",
                    record.attestationReportSha256,
                ),
                *(
                    dependency("evidence-bytes", path, digest)
                    for path, digest in sorted(evidence.items())
                ),
                *(
                    dependency("review-record", name, digest)
                    for name, digest in sorted(review_edges.items())
                ),
                dependency(
                    "validator",
                    EVIDENCE_DEPENDENCY_VALIDATOR,
                    canonical_sha256(EVIDENCE_DEPENDENCY_VALIDATOR),
                ),
                *(
                    [
                        dependency(
                            "predecessor-record",
                            "prior-curator-coherence-authority",
                            record.predecessorAuthorityDigest,
                        )
                    ]
                    if record.predecessorAuthorityDigest
                    else []
                ),
            ],
        )
        observed = require_evidence_dependencies(
            record.dependencies,
            record_type="curator-coherence/v1",
        )
    except EvidenceDependencyError as exc:
        raise CuratorCoherenceError(
            exc.status,
            exc.detail,
            next_action="publish",
        ) from exc
    if observed != expected:
        raise CuratorCoherenceError(
            "curator-coherence-dependencies-stale",
            "curator coherence direct dependencies do not match its canonical record inputs",
            next_action="publish",
        )


def curator_coherence_evidence(contract: WorktreeContract) -> list[EvidenceFact]:
    return require_current_curator_coherence(contract).evidence


def _quality_attestation(
    contract: WorktreeContract,
    source: _QualityAttestationSource,
) -> tuple[CuratorQualityAttestation, str]:
    assert contract.memory_worktree is not None
    try:
        attestation_bytes = source.attestation_path.read_bytes()
        report_bytes = source.report_path.read_bytes()
        attestation = CuratorQualityAttestation.model_validate_json(attestation_bytes)
    except (OSError, ValidationError) as exc:
        raise CuratorCoherenceError(
            "curator-coherence-attestation-unreadable",
            "the exact ar-curator-memory-quality/v1 attestation is absent or invalid",
            next_action="memory_quality_check",
        ) from exc
    expected_onboarding = (contract.memory_worktree / "onboarding").resolve().as_posix()
    expected_report = source.report_path.resolve().as_posix()
    expected_ready = (
        "ready-for-closeout",
        0,
        0,
        0,
        0,
        expected_onboarding,
        expected_report,
        _digest(report_bytes),
        source.pair_identity,
    )
    observed_ready = (
        attestation.checklistStatus,
        attestation.curatorActionableCount,
        attestation.memoryRepairCount,
        attestation.missingOnboardingCount,
        attestation.staleRouteIndexCount,
        attestation.onboardingRoot,
        attestation.reportPath,
        attestation.reportSha256,
        attestation.pairIdentity,
    )
    if observed_ready != expected_ready:
        raise CuratorCoherenceError(
            "curator-coherence-memory-not-ready",
            "memory quality and its rendered checklist do not prove exact closeout readiness",
            next_action="memory_quality_check",
        )
    try:
        require_memory_quality_attestation_dependencies(
            attestation,
            code_candidate_tree=source.code_candidate_tree,
            memory_candidate_tree=source.memory_candidate_tree,
        )
    except EvidenceDependencyError as exc:
        raise CuratorCoherenceError(
            exc.status,
            exc.detail,
            next_action="memory_quality_check",
        ) from exc
    return attestation, _digest(attestation_bytes)


def _task_context(contract: WorktreeContract):
    topology = TaskDocumentTopology(contract.coordination_root)
    found = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    if found is None:
        raise CuratorCoherenceError(
            "curator-coherence-task-missing",
            "the leaf enclosure has no exact canonical task document",
            next_action="task_doc",
        )
    try:
        candidate = topology.resolve(topology.canonical_ref(contract.repo_name, found[0]))
        master_ref = topology.parent(candidate.ref)
        if master_ref is None:
            raise TaskDocumentRefError("task-document-parent-missing", candidate.ref.key)
        master = topology.resolve(master_ref)
        sprint_ref = topology.parent(master.ref)
        if sprint_ref is None:
            raise TaskDocumentRefError("task-document-parent-missing", master.ref.key)
        sprint = topology.resolve(sprint_ref)
        authored_graph = sprint.document.executionGraph
        graph = (
            graph_context(topology, sprint.ref, authored_graph=authored_graph)
            if authored_graph is not None
            else None
        )
    except TaskDocumentRefError as exc:
        raise CuratorCoherenceError(exc.status, str(exc), next_action="task_doc") from exc
    return candidate, master, graph.sprint if graph is not None else sprint, graph


def _memory_candidate_tree(contract: WorktreeContract) -> str:
    assert contract.memory_worktree is not None
    reports = contract.worktree_group / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".curator-coherence-memory-", dir=reports) as temporary:
        return worktree_candidate_tree(contract.memory_worktree, Path(temporary) / "index")


def curator_coherence_assessments(
    validated: ValidatedCuratorCoherence,
    *,
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]] | None = None,
) -> tuple[AssessmentEntry, ...]:
    """Project the stored assessment collection without deciding anything about it.

    ``current`` is the caller's measurement of the world -- ``assessmentId -> (kind, name) ->
    (algorithm, digest)`` -- and omitting it means *nothing measured anything*, which is its own
    state: every record is then reported ``not-measured`` rather than ``stale``, because a projection
    that turned an absent measurement into a measured movement published a fact the store never held
    (``ICR-R15@v1``). A supplied measurement is read the shipped comparison's way: an identity it
    covers and that disagrees is a measured movement, an identity it does not cover is unmeasured, and
    only a record whose whole declaration is covered and agrees is reported ``current``.
    """

    return _measured_state(validated.record.assessments, current).assessments


def curator_coherence_subject_assessment_state(
    validated: ValidatedCuratorCoherence,
    subject_id: str,
    *,
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]] | None = None,
) -> SubjectAssessmentState:
    """Report one subject's assessment state as one of the distinct reportable states.

    A subject with no stored assessment answers ``none-recorded`` with a zero count -- never a
    disposition, never ``no_concern_found``, never ``compatible``. A subject whose record is
    ``unresolved`` answers ``unresolved``. A subject whose record no longer matches its recorded
    inputs answers ``stale`` and stays readable. A subject nobody measured answers ``not-measured``,
    and one whose measurement failed answers ``unavailable``. Those, plus ``current``, are the whole
    of the read layer's vocabulary for this question, and no path here manufactures another.
    ``subject_id`` is compared against :func:`assessment_subject_id`, which is the one spelling both
    a writer and a reader derive rather than two spellings that have to agree.
    """

    subject = [
        assessment
        for assessment in validated.record.assessments
        if assessment_subject_id(assessment) == subject_id
    ]
    if not subject:
        return assessment_state_for(())
    return _measured_state(subject, current)


def _measured_state(
    assessments: Sequence[ReviewAssessment],
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]] | None,
) -> SubjectAssessmentState:
    """One subject's (or collection's) state, from the caller's per-record measurement.

    The conversion is the shipped binding module's, in one implementation: ``None`` measures nothing
    and an empty mapping measures nothing either, so neither can promote a stored record to current
    (``ICR-R15@v1``).
    """

    return assessment_state_for(
        assessments, statuses=supplied_measurement_statuses(assessments, current)
    )


def all_assessment_subject_ids(validated: ValidatedCuratorCoherence) -> tuple[str, ...]:
    """Return every subject the stored collection has an assessment for, in stored order."""

    return tuple(
        dict.fromkeys(
            assessment_subject_id(assessment) for assessment in validated.record.assessments
        )
    )


def _require_authority_identity(
    contract: WorktreeContract, authority: CuratorCoherenceAuthority
) -> None:
    if (authority.leafId, authority.contractPath) != (
        contract.leaf_id,
        contract.contract_path.as_posix(),
    ):
        raise CuratorCoherenceError(
            "curator-coherence-authority-identity-mismatch",
            "the stable authority belongs to a different leaf or contract",
            next_action="developer-decision",
        )


def _task_relative(contract: WorktreeContract, path: Path) -> str:
    resolved = path.resolve(strict=False)
    root = contract.task_root.resolve()
    if not resolved.is_relative_to(root):
        raise CuratorCoherenceError(
            "curator-coherence-path-outside-task",
            "coherence authority paths must remain inside the task root",
            next_action="developer-decision",
        )
    return resolved.relative_to(root).as_posix()


def _fact(path: Path) -> EvidenceFact:
    return EvidenceFact(path=path.resolve().as_posix(), sha256=_digest(path.read_bytes()))


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "CuratorCoherenceNoImpact",
    "CuratorCoherenceObservation",
    "CuratorCoherencePaths",
    "ValidatedCuratorCoherence",
    "curator_coherence_evidence",
    "curator_coherence_no_impact",
    "curator_coherence_paths",
    "current_curator_coherence_predecessor",
    "load_curator_coherence_authority",
    "load_curator_coherence_generation",
    "observe_curator_coherence_source",
    "require_current_curator_coherence",
    "resolve_curator_evidence_ref",
]
