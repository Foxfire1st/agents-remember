"""CAS publication and action dispatch for structured curator coherence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from agents_remember.errors import CuratorCoherenceError
from agents_remember.kernel.atomic_write import atomic_replace, atomic_write_text
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceAuthority,
    CuratorCoherenceRecord,
    CuratorCoherenceRecordedJudgment,
    CuratorCoherenceRequest,
    CuratorCoherenceSnapshot,
    publication_input_statement,
)
from agents_remember.models.lifecycles.evidence_dependencies import (
    EVIDENCE_DEPENDENCY_VALIDATOR,
    build_evidence_dependencies,
    canonical_sha256,
    dependency,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceByte,
    ReviewAssessment,
    ReviewAssessmentRevision,
)
from agents_remember.models.lifecycles.review_assessment_store import (
    AssessmentInputs,
    ReviewAssessmentError,
    bind_assessment,
    require_assessments_are_identified,
    review_record_edges,
    reviewed_bytes,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

from .curator_assessment_evidence import (
    AssessmentEvidenceBlockedError,
    publish_assessment_evidence_bytes,
)
from .curator_coherence import (
    CuratorCoherenceObservation,
    curator_coherence_paths,
    current_curator_coherence_predecessor,
    load_curator_coherence_authority,
    observe_curator_coherence_source,
    require_current_curator_coherence,
)
from .curator_coherence_judgments import (
    exact_curator_judgments,
    require_recorded_judgments_current,
)
from .curator_coherence_render import render_curator_coherence

# The resolver and policy versions one assessment's binding declares. They are named as versioned
# constants rather than spelled inline at the one call site so a change to either is a visible change
# to this module's own contract, and so a stored assessment's declared resolver version is greppable.
ASSESSMENT_RESOLVER_VERSION = "curator-evidence-resolver/v1"
ASSESSMENT_POLICY_VERSION = "review-assessment-policy/v1"


@dataclass(frozen=True)
class _RecordPublication:
    judgments: list[CuratorCoherenceRecordedJudgment]
    assessments: list[ReviewAssessment]
    fingerprint: str
    predecessor: str
    attestation_copy_path: str


@dataclass(frozen=True)
class _ObservationView:
    state: str
    summary: str
    predecessor: str
    currentness_status: str | None = None
    next_action: str | None = None


def curator_coherence_action(
    contract: WorktreeContract,
    request: CuratorCoherenceRequest,
) -> dict[str, object]:
    if request.action == "status":
        return _status(contract, request)
    if request.action == "prepare":
        return _prepare(contract, request)
    if request.action == "validate":
        return _validate(contract, request)
    return _publish(contract, request)


def _status(contract: WorktreeContract, request: CuratorCoherenceRequest) -> dict[str, object]:
    paths = curator_coherence_paths(contract)
    try:
        observation = observe_curator_coherence_source(contract)
    except CuratorCoherenceError as exc:
        return _success(
            request,
            contract,
            state="source-not-ready",
            summary=str(exc),
            canonicalPath=paths.canonical.as_posix(),
            currentnessStatus=exc.status,
            nextAction=exc.next_action,
        )
    if not paths.canonical.exists():
        return _observation_payload(
            request,
            contract,
            observation,
            _ObservationView(
                state="absent",
                summary="No live curator-coherence authority has been published for this leaf.",
                predecessor="",
            ),
        )
    try:
        validated = require_current_curator_coherence(contract)
    except CuratorCoherenceError as exc:
        predecessor = current_curator_coherence_predecessor(contract)
        return _observation_payload(
            request,
            contract,
            observation,
            _ObservationView(
                state="stale",
                summary=str(exc),
                predecessor=predecessor,
                currentness_status=exc.status,
                next_action=exc.next_action,
            ),
        )
    return _validated_payload(request, contract, validated, state="current")


def _prepare(contract: WorktreeContract, request: CuratorCoherenceRequest) -> dict[str, object]:
    observation = observe_curator_coherence_source(contract)
    predecessor = current_curator_coherence_predecessor(contract)
    return _observation_payload(
        request,
        contract,
        observation,
        _ObservationView(
            state="prepared",
            summary=(
                "Exact source candidates and optimistic-concurrency identities are prepared; "
                + publication_input_statement()
            ),
            predecessor=predecessor,
        ),
    )


def _validate(contract: WorktreeContract, request: CuratorCoherenceRequest) -> dict[str, object]:
    validated = require_current_curator_coherence(contract)
    return _validated_payload(request, contract, validated, state="valid")


def _publish(contract: WorktreeContract, request: CuratorCoherenceRequest) -> dict[str, object]:
    observation = observe_curator_coherence_source(contract)
    _require_expected_observation(request, observation)
    judgments = exact_curator_judgments(contract, observation.source_candidates, request.judgments)
    caller = _authorized_publisher(contract, observation, request)
    assessments = _exact_review_assessments(contract, request, observation, caller=caller)
    publication_fingerprint = _publication_fingerprint(request, observation, judgments, assessments)
    predecessor = current_curator_coherence_predecessor(contract)
    replay = _idempotent_replay(contract, publication_fingerprint)
    if replay is not None:
        return _validated_payload(request, contract, replay, state="already-current")
    if predecessor != request.expected_predecessor_digest:
        raise CuratorCoherenceError(
            "curator-coherence-predecessor-stale",
            "the live canonical predecessor changed after prepare",
            expected={"predecessorAuthorityDigest": request.expected_predecessor_digest or ""},
            observed={"predecessorAuthorityDigest": predecessor},
            next_action="prepare",
        )
    record, report = _record(
        contract,
        request,
        observation,
        _RecordPublication(
            judgments=judgments,
            assessments=assessments,
            fingerprint=publication_fingerprint,
            predecessor=predecessor,
            attestation_copy_path=_task_relative(
                contract, _publish_attestation_copy(contract, observation)
            ),
        ),
        caller=caller,
    )
    record_bytes = _json_bytes(record.model_dump(mode="json", by_alias=True))
    report_bytes = report.encode("utf-8")
    record_digest = _digest(record_bytes)
    paths = curator_coherence_paths(contract)
    record_path = paths.generation_record(record_digest)
    report_path = paths.generation_report(record_digest)
    snapshot_path: Path | None = None
    current_contract = load_contract(contract.contract_path)
    if current_contract != contract:
        raise CuratorCoherenceError(
            "curator-coherence-contract-stale",
            "the leaf contract changed during coherence publication",
            next_action="prepare",
        )
    replay = _idempotent_replay(current_contract, publication_fingerprint)
    if replay is not None:
        return _validated_payload(request, current_contract, replay, state="already-current")
    _require_predecessor(current_contract, request)
    _require_observation_unchanged(observation, observe_curator_coherence_source(current_contract))
    require_recorded_judgments_current(current_contract, judgments)
    _publish_generation(record_path, report_path, record_bytes, report_bytes)
    if request.freeze_snapshot:
        snapshot_path = _publish_snapshot(
            current_contract,
            record,
            record_digest,
            record_path,
            report_path,
        )
    _require_observation_unchanged(observation, observe_curator_coherence_source(current_contract))
    require_recorded_judgments_current(current_contract, judgments)
    authority = CuratorCoherenceAuthority(
        leafId=current_contract.leaf_id,
        contractPath=current_contract.contract_path.as_posix(),
        currentRecordDigest=record_digest,
        recordPath=_task_relative(current_contract, record_path),
        reportPath=_task_relative(current_contract, report_path),
        reportSha256=record.reportSha256,
    )
    atomic_write_text(
        paths.canonical,
        _json_bytes(authority.model_dump(mode="json")).decode("utf-8"),
    )
    validated = require_current_curator_coherence(contract)
    payload = _validated_payload(request, contract, validated, state="published")
    if snapshot_path is not None:
        payload["snapshotPath"] = snapshot_path.as_posix()
    return payload


def _exact_review_assessments(
    contract: WorktreeContract,
    request: CuratorCoherenceRequest,
    observation: CuratorCoherenceObservation,
    *,
    caller: DeclaredCaller,
) -> list[ReviewAssessment]:
    """Bind each submitted assessment to the authenticated caller and the exact examined inputs.

    Three things happen here and each one is a refusal under ``KS-R15@v1``:

    * the author identity and role are taken from ``request.caller`` -- the authenticated publication
      path -- and never from the submission, so a caller cannot author an assessment under another
      identity (requirement 2.1);
    * the examined-input binding is rebuilt from the *same* observation the coherence record binds, so
      an assessment and the record it lives in are two statements about one candidate rather than two
      statements that happen to agree;
    * each cited evidence byte is published to the 6.6 destination and read back before the record is
      written, so a byte that cannot be held there is a **blocked** publication rather than a claim of
      survival (``KS-R15@v1`` §6.7).
    """

    assessments = list(request.review_assessments)
    if not assessments:
        return []
    try:
        require_assessments_are_identified(assessments)
    except ReviewAssessmentError as error:
        raise CuratorCoherenceError(
            error.status,
            error.detail,
            next_action=error.next_action,
        ) from error
    bound: list[ReviewAssessment] = []
    for authorized in assessments:
        inputs = _assessment_inputs(contract, observation)
        if authorized.evidenceRefs:
            inputs = replace(inputs, evidenceBytes=_published_evidence_bytes(contract, authorized))
        try:
            bound.append(
                bind_assessment(
                    authorized=authorized,
                    inputs=inputs,
                    author_ref=f"{caller.role}@{caller.task_document_ref.key}",
                    author_role=caller.role,
                    publication_ref=f"curator-coherence/v1:{contract.leaf_id}",
                )
            )
        except ReviewAssessmentError as error:
            raise CuratorCoherenceError(
                error.status,
                error.detail,
                next_action=error.next_action,
            ) from error
    return bound


def _published_evidence_bytes(
    contract: WorktreeContract,
    authorized: ReviewAssessmentRevision,
) -> tuple[AssessmentEvidenceByte, ...]:
    """Publish one assessment's cited bytes to the 6.6 destination and return their recorded facts.

    The binding is built from this function's return value rather than from a separate measurement,
    which is what keeps the record describing bytes that were actually written and read back. A
    destination that cannot hold them, or a read-back whose digest differs, becomes a typed refusal
    carrying the exact destination, the expected digest and the observed state -- ``KS-R15@v1``
    §6.7's blocked item, never a claim of survival and never a second store.
    """

    try:
        publication = publish_assessment_evidence_bytes(
            contract, authorized.assessmentId, authorized.evidenceRefs
        )
    except AssessmentEvidenceBlockedError as error:
        raise CuratorCoherenceError(
            error.status,
            error.detail,
            expected=error.expected,
            observed=error.observed,
            next_action="developer-decision",
        ) from error
    return reviewed_bytes(authorized.evidenceRefs, publication.published)


def _assessment_inputs(
    contract: WorktreeContract,
    observation: CuratorCoherenceObservation,
) -> AssessmentInputs:
    """Return the exact inputs an assessment on this publication examined."""

    return AssessmentInputs(
        scopeManifestRef=contract.leaf_id,
        comparisonRef=contract.contract_path.as_posix(),
        codeCandidateTree=observation.code_candidate_tree,
        memoryCandidateTree=observation.memory_candidate_tree,
        pairIdentityDigest=observation.pair_identity.contractDigest,
        taskTopologyFingerprint=observation.task_topology_fingerprint,
        taskIntentDigest=observation.task_intent.digest,
        resolverVersion=ASSESSMENT_RESOLVER_VERSION,
        policyVersion=ASSESSMENT_POLICY_VERSION,
    )


def _record(
    contract: WorktreeContract,
    request: CuratorCoherenceRequest,
    observation: CuratorCoherenceObservation,
    publication: _RecordPublication,
    *,
    caller: DeclaredCaller,
) -> tuple[CuratorCoherenceRecord, str]:
    assert request.semantic_requirement_revision is not None
    assert request.delivery_attempt is not None
    evidence_edges = {
        judgment.evidenceRef: judgment.evidenceSha256 for judgment in publication.judgments
    }
    dependencies = build_evidence_dependencies(
        "curator-coherence/v1",
        [
            dependency(
                "code-tree",
                "candidate",
                observation.code_candidate_tree,
                algorithm="git-object",
            ),
            dependency(
                "memory-tree",
                "candidate",
                observation.memory_candidate_tree,
                algorithm="git-object",
            ),
            dependency(
                "semantic-topology",
                "leaf-placement",
                observation.task_topology_fingerprint,
            ),
            dependency("task-intent", "leaf", observation.task_intent.digest),
            dependency(
                "memory-attestation",
                observation.attestation_path.resolve().as_posix(),
                observation.attestation_sha256,
            ),
            dependency(
                "evidence-bytes",
                "memory-quality-report",
                observation.attestation.reportSha256,
            ),
            *(
                dependency("evidence-bytes", path, digest)
                for path, digest in sorted(evidence_edges.items())
            ),
            # The record's own edge to each assessment it stores. This is the ONE direction the
            # ``review-record`` kind is used on this route: an assessment declares the inputs it
            # examined, and the coherence record declares an edge to the assessment. Requiring the
            # kind inside the assessment would be a record citing itself -- the self-invalidating
            # sequence ``design/retrieval-review-design.md:368`` exists to avoid.
            *review_record_edges(publication.assessments),
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
                        publication.predecessor,
                    )
                ]
                if publication.predecessor
                else []
            ),
        ],
    )
    provisional = CuratorCoherenceRecord(
        leafId=contract.leaf_id,
        contractPath=contract.contract_path.as_posix(),
        taskDocumentRef=observation.candidate.ref,
        semanticRequirementRevision=request.semantic_requirement_revision.strip(),
        deliveryAttempt=request.delivery_attempt.strip(),
        pairIdentity=observation.pair_identity,
        codeCandidateTree=observation.code_candidate_tree,
        memoryCandidateTree=observation.memory_candidate_tree,
        taskTopologyFingerprint=observation.task_topology_fingerprint,
        taskIntent=observation.task_intent,
        attestationPath=observation.attestation_path.resolve().as_posix(),
        attestationSha256=observation.attestation_sha256,
        attestationReportSha256=observation.attestation.reportSha256,
        attestationCopyPath=publication.attestation_copy_path,
        sourceCandidates=observation.source_candidates,
        judgments=publication.judgments,
        assessments=publication.assessments,
        dependencies=dependencies,
        predecessorAuthorityDigest=publication.predecessor,
        publicationFingerprint=publication.fingerprint,
        publishedBy=f"{caller.role}@{caller.task_document_ref.key}",
        reportSha256="0" * 64,
    )
    report = render_curator_coherence(provisional)
    record = provisional.model_copy(update={"reportSha256": _digest(report.encode("utf-8"))})
    rendered = render_curator_coherence(record)
    if rendered != report:
        raise RuntimeError("curator-coherence projection unexpectedly depends on its own digest")
    return record, rendered


_CALLER_PATH_SHAPE = "task-root-relative document path '<task-slug>/<leaf-document-file>'"
"""The shape ``TaskDocumentRef.path`` must take here, stated in the refusal that demands it (D-26).

D-26 measured the cost of leaving it unsaid: a caller who passed the bare leaf-document name was
refused by ``curator-coherence-caller-refused`` with no statement of the expected shape anywhere --
not in the refusal, not in ``status``/``prepare``, not in the contract -- so the only way to learn it
was the trial and error the schema exists to prevent. It is an instructions-do-not-travel defect.
"""


def _authorized_publisher(
    contract: WorktreeContract,
    observation: CuratorCoherenceObservation,
    request: CuratorCoherenceRequest,
) -> DeclaredCaller:
    """The caller, with a bare leaf-document name resolved against this contract, or a refusal.

    The contract already identifies the leaf unambiguously, so a bare file name that is exactly the
    addressed document's own file name (and repository) cannot mean any other document: it is
    resolved to the canonical ref instead of being refused. Anything else is refused, and the
    refusal now names the shape it wants **and** the exact value this contract expects.
    """

    caller = request.caller
    assert caller is not None
    topology = TaskDocumentTopology(contract.coordination_root)
    try:
        master_ref = topology.parent(observation.candidate.ref)
        sprint_ref = topology.parent(master_ref) if master_ref is not None else None
    except TaskDocumentRefError as exc:
        raise CuratorCoherenceError(exc.status, str(exc), next_action="task_doc") from exc
    expected = observation.candidate.ref if caller.role == "curator" else sprint_ref
    resolved = _resolve_caller_ref(caller.task_document_ref, expected)
    if expected is not None and resolved == expected:
        return DeclaredCaller(role=caller.role, task_document_ref=resolved)
    raise CuratorCoherenceError(
        "curator-coherence-caller-refused",
        _caller_refusal_detail(caller, expected),
        expected={
            "role": caller.role,
            "taskDocumentRef": expected.model_dump(mode="json") if expected is not None else None,
        },
        observed={
            "role": caller.role,
            "taskDocumentRef": caller.task_document_ref.model_dump(mode="json"),
        },
        next_action="developer-decision",
    )


def _resolve_caller_ref(
    caller_ref: TaskDocumentRef,
    expected: TaskDocumentRef | None,
) -> TaskDocumentRef:
    """Resolve a bare document file name to ``expected`` when it names that exact document.

    Only the file name is resolved: the repository must match and the name must equal the expected
    document's own file name, so a bare name can never be read as a different leaf's document.
    """

    if expected is None or "/" in caller_ref.path or caller_ref.repository != expected.repository:
        return caller_ref
    if expected.path.rsplit("/", 1)[-1] != caller_ref.path:
        return caller_ref
    return expected


def _caller_refusal_detail(caller: DeclaredCaller, expected: TaskDocumentRef | None) -> str:
    """The refusal sentence: the rule, the shape, and this contract's exact expected value."""

    supplied = f"{caller.task_document_ref.repository}:{caller.task_document_ref.path}"
    if expected is None:
        return (
            "publish requires the exact leaf curator or owning sprint architect; "
            f"caller.task_document_ref.path must be the {_CALLER_PATH_SHAPE}, and this contract "
            f"has no owning sprint document to resolve one from; received {supplied!r}"
        )
    return (
        "publish requires the exact leaf curator or owning sprint architect; "
        f"caller.task_document_ref.path must be the {_CALLER_PATH_SHAPE} -- for this contract "
        f"that is {expected.path!r} in repository {expected.repository!r}, or the bare file name "
        f"{expected.path.rsplit('/', 1)[-1]!r}; received {supplied!r}"
    )


def _publish_attestation_copy(
    contract: WorktreeContract,
    observation: CuratorCoherenceObservation,
) -> Path:
    """Copy the bound memory-quality attestation beside the record, content-addressed.

    The record binds ``attestationPath`` inside the leaf's **enclosure**, and
    ``lifecycle_finalize_task`` reclaims the enclosure: after cleanup the digest the record commits
    to names bytes that no longer exist anywhere, so a reader can no longer tell a candidate-empty
    publication from one whose attestation listed candidates (D-25's family -- the same cleanup that
    closes the ``validate`` window destroys the evidence the publication binds to). The copy lives
    in the task tree, under the same ``notes/reports/curator-coherence/<leaf>/`` root the record
    itself survives in, and is named by the attestation's own digest so it is immutable and
    idempotent across re-publications.
    """

    paths = curator_coherence_paths(contract)
    source = observation.attestation_path
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise CuratorCoherenceError(
            "curator-coherence-attestation-unreadable",
            "the bound memory-quality attestation cannot be read for its durable copy",
            expected={"attestationPath": source.as_posix()},
            observed={"attestationPath": source.as_posix(), "state": "unreadable"},
            next_action="developer-decision",
        ) from exc
    digest = _digest(payload)
    if digest != observation.attestation_sha256:
        raise CuratorCoherenceError(
            "curator-coherence-attestation-stale",
            "the memory-quality attestation changed between observation and its durable copy",
            expected={"attestationSha256": observation.attestation_sha256},
            observed={"attestationSha256": digest},
            next_action="prepare",
        )
    destination = paths.attestation_copy(digest)
    if destination.exists():
        try:
            existing = destination.read_bytes()
        except OSError as exc:
            raise CuratorCoherenceError(
                "curator-coherence-attestation-unreadable",
                "the durable attestation copy exists but its bytes cannot be read",
                expected={"attestationPath": destination.as_posix()},
                observed={"attestationPath": destination.as_posix(), "state": "unreadable"},
                next_action="developer-decision",
            ) from exc
        if existing != payload:
            raise CuratorCoherenceError(
                "curator-coherence-content-address-collision",
                "a durable attestation copy path already holds different bytes",
                expected={"attestationSha256": digest},
                observed={"attestationSha256": _digest(existing)},
                next_action="developer-decision",
            )
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        _write_fsynced(temporary, payload)
        atomic_replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _require_expected_observation(
    request: CuratorCoherenceRequest, observation: CuratorCoherenceObservation
) -> None:
    expected = {
        "codeCandidateTree": request.expected_code_candidate_tree,
        "memoryCandidateTree": request.expected_memory_candidate_tree,
        "taskTopologyFingerprint": request.expected_task_topology_fingerprint,
        "taskIntent": (
            request.expected_task_intent.model_dump(mode="json", by_alias=True)
            if request.expected_task_intent is not None
            else None
        ),
        "attestationSha256": request.expected_attestation_sha256,
    }
    observed = {
        "codeCandidateTree": observation.code_candidate_tree,
        "memoryCandidateTree": observation.memory_candidate_tree,
        "taskTopologyFingerprint": observation.task_topology_fingerprint,
        "taskIntent": observation.task_intent.model_dump(mode="json", by_alias=True),
        "attestationSha256": observation.attestation_sha256,
    }
    if expected != observed:
        raise CuratorCoherenceError(
            "curator-coherence-prepared-source-stale",
            "a prepared code, memory, task, or attestation identity changed before publish",
            expected=expected,
            observed=observed,
            next_action="prepare",
        )


def _require_observation_unchanged(
    expected: CuratorCoherenceObservation,
    observed: CuratorCoherenceObservation,
) -> None:
    expected_identity = _observation_identity(expected)
    observed_identity = _observation_identity(observed)
    if expected_identity != observed_identity:
        raise CuratorCoherenceError(
            "curator-coherence-source-raced",
            "code, memory, task topology, or attestation changed during publication",
            expected=expected_identity,
            observed=observed_identity,
            next_action="prepare",
        )


def _observation_identity(observation: CuratorCoherenceObservation) -> dict[str, object]:
    return {
        "pairIdentity": observation.pair_identity.model_dump(mode="json"),
        "codeCandidateTree": observation.code_candidate_tree,
        "memoryCandidateTree": observation.memory_candidate_tree,
        "taskTopologyFingerprint": observation.task_topology_fingerprint,
        "taskIntent": observation.task_intent.model_dump(mode="json", by_alias=True),
        "attestationSha256": observation.attestation_sha256,
        "sourceCandidates": [candidate.identity for candidate in observation.source_candidates],
    }


def _publication_fingerprint(
    request: CuratorCoherenceRequest,
    observation: CuratorCoherenceObservation,
    judgments: list[CuratorCoherenceRecordedJudgment],
    assessments: list[ReviewAssessment],
) -> str:
    assert request.caller is not None
    return _digest(
        _json_bytes(
            {
                "schema": "ar-curator-coherence-publication/v1",
                "contractPath": request.contract_path,
                "semanticRequirementRevision": request.semantic_requirement_revision,
                "deliveryAttempt": request.delivery_attempt,
                "predecessorAuthorityDigest": request.expected_predecessor_digest,
                "source": _observation_identity(observation),
                "judgments": [judgment.model_dump(mode="json") for judgment in judgments],
                "assessments": [
                    assessment.model_dump(mode="json", by_alias=True) for assessment in assessments
                ],
                "caller": request.caller.model_dump(mode="json"),
                "freezeSnapshot": request.freeze_snapshot,
            }
        )
    )


def _idempotent_replay(contract: WorktreeContract, fingerprint: str):
    paths = curator_coherence_paths(contract)
    if not paths.canonical.exists():
        return None
    try:
        current = load_curator_coherence_authority(contract)
    except CuratorCoherenceError:
        return None
    if current.record.publicationFingerprint != fingerprint:
        return None
    return require_current_curator_coherence(contract)


def _require_predecessor(contract: WorktreeContract, request: CuratorCoherenceRequest) -> None:
    observed = current_curator_coherence_predecessor(contract)
    if observed != request.expected_predecessor_digest:
        raise CuratorCoherenceError(
            "curator-coherence-predecessor-stale",
            "the live canonical predecessor changed during publication",
            expected={"predecessorAuthorityDigest": request.expected_predecessor_digest or ""},
            observed={"predecessorAuthorityDigest": observed},
            next_action="prepare",
        )


def _publish_generation(
    record_path: Path,
    report_path: Path,
    record_bytes: bytes,
    report_bytes: bytes,
) -> None:
    destination = record_path.parent
    if destination.exists():
        try:
            matches = (
                record_path.read_bytes() == record_bytes
                and report_path.read_bytes() == report_bytes
            )
        except OSError as exc:
            raise CuratorCoherenceError(
                "curator-coherence-generation-incomplete",
                "an immutable coherence generation directory is incomplete or unreadable",
                next_action="developer-decision",
            ) from exc
        if not matches:
            raise CuratorCoherenceError(
                "curator-coherence-content-address-collision",
                "an immutable coherence generation path contains different bytes",
                next_action="developer-decision",
            )
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        temporary.mkdir()
        _write_fsynced(temporary / "record.json", record_bytes)
        _write_fsynced(temporary / "report.md", report_bytes)
        atomic_replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _publish_snapshot(
    contract: WorktreeContract,
    record: CuratorCoherenceRecord,
    record_digest: str,
    record_path: Path,
    report_path: Path,
) -> Path:
    paths = curator_coherence_paths(contract)
    attempt = re.sub(r"[^A-Za-z0-9._-]+", "-", record.deliveryAttempt).strip("-")
    if not attempt:
        raise CuratorCoherenceError(
            "curator-coherence-attempt-invalid",
            "delivery attempt cannot form an immutable snapshot identity",
            next_action="publish",
        )
    path = paths.snapshots / f"{attempt}-{record_digest}.json"
    snapshot = CuratorCoherenceSnapshot(
        semanticRequirementRevision=record.semanticRequirementRevision,
        deliveryAttempt=record.deliveryAttempt,
        recordDigest=record_digest,
        recordPath=_task_relative(contract, record_path),
        reportPath=_task_relative(contract, report_path),
    )
    content = _json_bytes(snapshot.model_dump(mode="json")).decode("utf-8")
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise CuratorCoherenceError(
                "curator-coherence-snapshot-conflict",
                "an immutable attempt snapshot path contains different bytes",
                next_action="developer-decision",
            )
        return path
    atomic_write_text(path, content)
    return path


def _observation_payload(
    request: CuratorCoherenceRequest,
    contract: WorktreeContract,
    observation: CuratorCoherenceObservation,
    view: _ObservationView,
) -> dict[str, object]:
    paths = curator_coherence_paths(contract)
    return _success(
        request,
        contract,
        state=view.state,
        summary=view.summary,
        canonicalPath=paths.canonical.as_posix(),
        pairIdentity=observation.pair_identity.model_dump(mode="json"),
        codeCandidateTree=observation.code_candidate_tree,
        memoryCandidateTree=observation.memory_candidate_tree,
        taskTopologyFingerprint=observation.task_topology_fingerprint,
        taskIntent=observation.task_intent.model_dump(mode="json", by_alias=True),
        **(
            {"currentnessStatus": view.currentness_status}
            if view.currentness_status is not None
            else {}
        ),
        **({"nextAction": view.next_action} if view.next_action is not None else {}),
        attestationPath=observation.attestation_path.resolve().as_posix(),
        attestationSha256=observation.attestation_sha256,
        attestationReportSha256=observation.attestation.reportSha256,
        predecessorAuthorityDigest=view.predecessor,
        candidateCount=len(observation.source_candidates),
        candidates=[
            candidate.model_dump(mode="json") for candidate in observation.source_candidates
        ],
    )


def _validated_payload(request, contract, validated, *, state: str) -> dict[str, object]:
    record = validated.record
    return _success(
        request,
        contract,
        state=state,
        summary="The sole live structured curator-coherence authority is exact and current.",
        canonicalPath=curator_coherence_paths(contract).canonical.as_posix(),
        recordPath=validated.record_path.as_posix(),
        reportPath=validated.report_path.as_posix(),
        semanticRequirementRevision=record.semanticRequirementRevision,
        deliveryAttempt=record.deliveryAttempt,
        pairIdentity=record.pairIdentity.model_dump(mode="json"),
        codeCandidateTree=record.codeCandidateTree,
        memoryCandidateTree=record.memoryCandidateTree,
        taskTopologyFingerprint=record.taskTopologyFingerprint,
        taskIntent=record.taskIntent.model_dump(mode="json", by_alias=True),
        attestationPath=record.attestationPath,
        attestationSha256=record.attestationSha256,
        attestationReportSha256=record.attestationReportSha256,
        predecessorAuthorityDigest=record.predecessorAuthorityDigest,
        recordDigest=validated.record_digest,
        reportDigest=record.reportSha256,
        candidateCount=len(record.sourceCandidates),
        candidates=[candidate.model_dump(mode="json") for candidate in record.sourceCandidates],
        reviewAssessments=[
            assessment.model_dump(mode="json", by_alias=True) for assessment in record.assessments
        ],
        validationResult={
            "state": "valid",
            "candidateCount": len(record.sourceCandidates),
            "checked": [
                "code-candidate",
                "memory-candidate",
                "code-memory-pair",
                "task-topology",
                "task-intent",
                "attestation",
                "candidate-judgments",
                "authority-record",
                "generated-projection",
                *(["review-assessments"] if record.assessments else []),
            ],
        },
    )


def _success(
    request: CuratorCoherenceRequest,
    contract: WorktreeContract,
    *,
    state: str,
    summary: str,
    **extra: object,
) -> dict[str, object]:
    return {
        "ok": True,
        "operation": "curator_coherence",
        "action": request.action,
        "state": state,
        "summary": summary,
        "contractPath": contract.contract_path.as_posix(),
        **extra,
    }


def _task_relative(contract: WorktreeContract, path: Path) -> str:
    return path.resolve(strict=False).relative_to(contract.task_root.resolve()).as_posix()


def _write_fsynced(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = ["curator_coherence_action"]
