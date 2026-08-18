"""Strict persisted and wire models for the pre-closeout scheduler."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_remember.models.base import ToolResponse
from agents_remember.models.task_document_ref import TaskDocumentRef

QueueCandidateState = Literal[
    "declared",
    "selected",
    "closeout-in-flight",
    "certified",
    "integration-in-flight",
]
QueueClassification = Literal["ready", "waiting", "blocked", "in-flight"]
QueueAction = Literal[
    "status",
    "declare",
    "withdraw",
    "set-grade",
    "set-admission",
    "select",
    "release-selection",
    "acquire-barrier",
    "release-barrier",
    "abort-barrier",
]
QueueEventAction = Literal[
    "declare",
    "withdraw",
    "set-grade",
    "set-admission",
    "select",
    "release-selection",
    "acquire-barrier",
    "release-barrier",
    "abort-barrier",
    "claim-closeout",
    "certify-closeout",
    "claim-integration",
    "complete-integration",
    "prepare-conflict-resolution",
    "prepare-quality-repair",
    "reclaim-sprint",
]
PriorityGrade = Literal["critical", "high", "normal", "low"]
MemoryReadiness = Literal["ready", "not-applicable"]
MAX_CLOSEOUT_CANDIDATES = 256
MAX_CLOSEOUT_MASTERS = 256
MAX_CLOSEOUT_GRAPH_EDGES = 4096
MAX_QUEUE_EVIDENCE = MAX_CLOSEOUT_CANDIDATES
MAX_QUEUE_REASONS = MAX_CLOSEOUT_MASTERS + 8
MAX_QUEUE_TEXT = 8192
MAX_QUEUE_SHORT_TEXT = 256


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CloseoutQueueRequest(BaseModel):
    """Strict action-specific public request with optimistic queue concurrency."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: QueueAction
    sprint_task_document_ref: TaskDocumentRef
    request_id: str | None = Field(default=None, max_length=MAX_QUEUE_SHORT_TEXT)
    expected_revision: int | None = Field(default=None, ge=0)
    contract_path: str | None = Field(default=None, max_length=MAX_QUEUE_TEXT)
    candidate_task_document_ref: TaskDocumentRef | None = None
    barrier_master_ref: TaskDocumentRef | None = None
    admission: CandidateAdmissionFacts | None = None
    grade: SchedulingGradeInput | None = None
    barrier_judgment_id: str | None = Field(default=None, max_length=MAX_QUEUE_SHORT_TEXT)
    rationale: str = Field(default="", max_length=MAX_QUEUE_TEXT)

    @model_validator(mode="after")
    def _action_payload_is_exact(self) -> CloseoutQueueRequest:
        present = {
            "request_id": self.request_id is not None,
            "expected_revision": self.expected_revision is not None,
            "contract_path": self.contract_path is not None,
            "candidate_task_document_ref": self.candidate_task_document_ref is not None,
            "barrier_master_ref": self.barrier_master_ref is not None,
            "admission": self.admission is not None,
            "grade": self.grade is not None,
            "barrier_judgment_id": self.barrier_judgment_id is not None,
            "rationale": bool(self.rationale.strip()),
        }
        mutation = frozenset({"request_id", "expected_revision"})
        required: dict[QueueAction, frozenset[str]] = {
            "status": frozenset(),
            "declare": mutation | {"contract_path"},
            "withdraw": mutation | {"candidate_task_document_ref"},
            "set-grade": mutation | {"candidate_task_document_ref", "grade"},
            "set-admission": mutation | {"candidate_task_document_ref", "admission"},
            "select": mutation | {"candidate_task_document_ref"},
            "release-selection": mutation | {"candidate_task_document_ref"},
            "acquire-barrier": mutation | {"barrier_master_ref", "rationale"},
            "release-barrier": mutation | {"barrier_master_ref", "rationale"},
            "abort-barrier": mutation | {"barrier_master_ref", "barrier_judgment_id"},
        }
        optional: dict[QueueAction, frozenset[str]] = {"declare": frozenset({"admission"})}
        expected = required[self.action]
        missing = sorted(name for name in expected if not present[name])
        forbidden = sorted(
            name
            for name, is_present in present.items()
            if is_present and name not in expected and name not in optional.get(self.action, ())
        )
        if missing or forbidden:
            raise ValueError(
                f"{self.action} request payload mismatch; missing={missing!r}, forbidden={forbidden!r}"
            )
        return self


class SchedulingGradeInput(_StrictModel):
    """The small caller assertion resolved against the sprint's canonical registers."""

    priority: PriorityGrade
    judgmentId: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    urgency: str | None = Field(default=None, max_length=MAX_QUEUE_TEXT)
    risk: str | None = Field(default=None, max_length=MAX_QUEUE_TEXT)

    @field_validator("judgmentId")
    @classmethod
    def _nonblank_judgment_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("grade judgment id must not be blank")
        return cleaned

    @field_validator("urgency", "risk")
    @classmethod
    def _optional_nonblank_signal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("grade urgency/risk must be omitted or non-blank")
        return cleaned


class SchedulingGrade(_StrictModel):
    """One exact canonical Priority/Judgment Register row projected for scheduling."""

    priority: PriorityGrade
    urgency: str | None = Field(default=None, max_length=MAX_QUEUE_TEXT)
    risk: str | None = Field(default=None, max_length=MAX_QUEUE_TEXT)
    judgmentId: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    subject: str = Field(max_length=MAX_QUEUE_TEXT)
    rationale: str = Field(max_length=MAX_QUEUE_TEXT)
    evidenceRefs: list[str] = Field(max_length=MAX_QUEUE_EVIDENCE)
    decidedBy: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    confidence: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    supersedes: str = Field(default="", max_length=MAX_QUEUE_SHORT_TEXT)

    @field_validator("judgmentId", "subject", "rationale", "decidedBy", "confidence")
    @classmethod
    def _nonblank_canonical_judgment(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("canonical grade provenance must not be blank")
        return cleaned

    @field_validator("evidenceRefs")
    @classmethod
    def _unique_evidence(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("grade evidence references must not be blank")
        if any(len(item) > MAX_QUEUE_TEXT for item in cleaned):
            raise ValueError("grade evidence references exceed the bounded path size")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("grade evidence references must be unique")
        return cleaned


class EvidenceFact(_StrictModel):
    """One exact evidence file whose bytes are rechecked before a transition."""

    path: str = Field(max_length=MAX_QUEUE_TEXT)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def _nonblank_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("evidence path must not be blank")
        return cleaned


class AppliedQueueRequest(_StrictModel):
    """Bounded retry receipt retained in the authoritative queue state."""

    requestId: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)

    @field_validator("requestId")
    @classmethod
    def _nonblank_request_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("request id must not be blank")
        return cleaned


class CandidateAdmissionFacts(_StrictModel):
    """Explicit logistics facts; false values explain waiting without becoming judgment."""

    resourceReady: bool = True
    resourceReason: str = Field(default="", max_length=MAX_QUEUE_TEXT)
    admissionReady: bool = True
    admissionReason: str = Field(default="", max_length=MAX_QUEUE_TEXT)

    @model_validator(mode="after")
    def _explain_false_facts(self) -> CandidateAdmissionFacts:
        if not self.resourceReady and not self.resourceReason.strip():
            raise ValueError("resourceReason is required when resourceReady is false")
        if not self.admissionReady and not self.admissionReason.strip():
            raise ValueError("admissionReason is required when admissionReady is false")
        self.resourceReason = self.resourceReason.strip()
        self.admissionReason = self.admissionReason.strip()
        return self


CloseoutQueueRequest.model_rebuild()


class RouteReviewFact(_StrictModel):
    required: bool
    status: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    candidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    verdict: str | None = Field(default=None, max_length=MAX_QUEUE_SHORT_TEXT)
    verdictRef: str | None = Field(default=None, max_length=MAX_QUEUE_TEXT)
    routeCount: int | None = Field(default=None, ge=1)
    recordSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: list[EvidenceFact] = Field(default_factory=list, max_length=MAX_QUEUE_EVIDENCE)


class CloseoutCandidateRecord(_StrictModel):
    """One exact reviewed and curated leaf candidate declared before history moves."""

    taskDocumentRef: TaskDocumentRef
    owningMaster: TaskDocumentRef
    contractPath: str = Field(max_length=MAX_QUEUE_TEXT)
    candidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryCandidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    graphRevision: str = Field(pattern=r"^[0-9a-f]{64}$")
    codeBaseCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryBaseCommit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    ledgerMemoryCommit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    routeReview: RouteReviewFact
    memoryMode: Literal["external", "internal", "disabled"]
    memoryReadiness: MemoryReadiness
    memoryEvidence: list[EvidenceFact] = Field(default_factory=list, max_length=MAX_QUEUE_EVIDENCE)
    admission: CandidateAdmissionFacts = Field(default_factory=CandidateAdmissionFacts)
    grade: SchedulingGrade | None = None
    gradeJudgmentDigest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    gradeEvidence: list[EvidenceFact] = Field(default_factory=list, max_length=MAX_QUEUE_EVIDENCE)
    state: QueueCandidateState = "declared"
    inFlightOwnerFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    closeoutCodeCommit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    closeoutMemoryContentCommit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    closeoutLedgerCommit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    declaredBy: str = Field(max_length=MAX_QUEUE_TEXT)
    declaredAt: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)

    @field_validator("contractPath", "declaredBy", "declaredAt")
    @classmethod
    def _nonblank_candidate_metadata(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("candidate metadata must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _state_and_memory_are_serviceable(self) -> CloseoutCandidateRecord:
        owner_required = self.state in {"closeout-in-flight", "integration-in-flight"}
        if owner_required != (self.inFlightOwnerFingerprint is not None):
            raise ValueError("only lifecycle-in-flight candidates require an owner fingerprint")
        certified = self.state in {"certified", "integration-in-flight"}
        if certified != (self.closeoutCodeCommit is not None):
            raise ValueError("certified candidate states require the exact closeout code commit")
        if not certified and any(
            value is not None
            for value in (self.closeoutMemoryContentCommit, self.closeoutLedgerCommit)
        ):
            raise ValueError("uncertified candidates cannot carry closeout commits")
        if self.memoryMode == "external":
            if (
                self.memoryReadiness != "ready"
                or len(self.memoryEvidence) < 2
                or self.memoryCandidateTree is None
                or self.memoryBaseCommit is None
                or self.ledgerMemoryCommit is None
            ):
                raise ValueError("external-memory candidates require exact ready memory evidence")
            if certified and (
                self.closeoutMemoryContentCommit is None or self.closeoutLedgerCommit is None
            ):
                raise ValueError("certified external-memory candidates require memory commits")
        elif (
            self.memoryReadiness != "not-applicable"
            or self.memoryEvidence
            or any(
                value is not None
                for value in (
                    self.memoryCandidateTree,
                    self.memoryBaseCommit,
                    self.ledgerMemoryCommit,
                    self.closeoutMemoryContentCommit,
                    self.closeoutLedgerCommit,
                )
            )
        ):
            raise ValueError("non-external candidates use the typed not-applicable memory state")
        return self


class ActiveAtomicBarrier(_StrictModel):
    master: TaskDocumentRef
    graphRevision: str = Field(pattern=r"^[0-9a-f]{64}$")
    acquiredBy: str = Field(max_length=MAX_QUEUE_TEXT)
    acquiredAt: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    rationale: str = Field(max_length=MAX_QUEUE_TEXT)

    @field_validator("acquiredBy", "acquiredAt", "rationale")
    @classmethod
    def _nonblank_barrier_metadata(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("barrier provenance and rationale must not be blank")
        return cleaned


class CloseoutQueueState(_StrictModel):
    schemaVersion: Literal["ar-closeout-queue/v1"] = "ar-closeout-queue/v1"
    sprintTaskDocumentRef: TaskDocumentRef
    revision: int = Field(ge=0)
    graphRevision: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: dict[str, CloseoutCandidateRecord] = Field(
        default_factory=dict, max_length=MAX_CLOSEOUT_CANDIDATES
    )
    activeBarrier: ActiveAtomicBarrier | None = None
    appliedRequests: list[AppliedQueueRequest] = Field(default_factory=list, max_length=128)
    closed: bool = False
    updatedAt: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)

    @model_validator(mode="after")
    def _candidate_keys_match(self) -> CloseoutQueueState:
        expected = {candidate.taskDocumentRef.key for candidate in self.candidates.values()}
        if expected != set(self.candidates):
            raise ValueError("queue candidate keys must equal canonical task-document references")
        request_ids = [request.requestId for request in self.appliedRequests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("applied queue request ids must be unique")
        active = [
            candidate for candidate in self.candidates.values() if candidate.state != "declared"
        ]
        if len(active) > 1:
            raise ValueError("at most one closeout candidate may own the sprint landing lane")
        if (
            self.activeBarrier is not None
            and active
            and active[0].owningMaster != self.activeBarrier.master
        ):
            raise ValueError("an active atomic barrier excludes every other master's lane owner")
        if self.closed and (self.candidates or self.activeBarrier is not None):
            raise ValueError("a closed sprint queue must be quiescent")
        return self


class CloseoutQueueCandidateView(_StrictModel):
    taskDocumentRef: TaskDocumentRef
    owningMaster: TaskDocumentRef
    contractPath: str = Field(max_length=MAX_QUEUE_TEXT)
    candidateTree: str
    graphRevision: str
    candidateState: QueueCandidateState
    classification: QueueClassification
    reasons: list[str] = Field(max_length=MAX_QUEUE_REASONS)
    legalNextOperations: list[str] = Field(max_length=16)
    grade: SchedulingGrade | None = None


class CloseoutQueueResponse(ToolResponse):
    operation: Literal["closeout_queue"] = "closeout_queue"
    action: QueueAction
    state: str
    summary: str = Field(max_length=MAX_QUEUE_TEXT)
    sprintTaskDocumentRef: TaskDocumentRef
    revision: int = Field(ge=0)
    graphRevision: str = Field(pattern=r"^[0-9a-f]{64}$")
    activeBarrier: ActiveAtomicBarrier | None = None
    ready: list[CloseoutQueueCandidateView] = Field(default_factory=list)
    waiting: list[CloseoutQueueCandidateView] = Field(default_factory=list)
    blocked: list[CloseoutQueueCandidateView] = Field(default_factory=list)
    inFlight: list[CloseoutQueueCandidateView] = Field(default_factory=list)
    updatedAt: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
