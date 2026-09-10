"""Persisted route-review models shared by task documents and worktree gates."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agents_remember.models.lifecycles.evidence_dependencies import (
    EvidenceDependencies,
    canonical_sha256,
    require_evidence_dependencies,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import (
    TaskIntentIdentity,
    TaskIntentState,
    missing_task_intent,
)

RouteReviewVerdict = Literal["pass", "pass-with-notes", "block"]


class _RouteReviewDoc(BaseModel):
    """Strict base matching the task-document persisted-model contract."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RouteReviewUnit(_RouteReviewDoc):
    """One independently reviewed major route in the candidate code tree."""

    route: str
    verdict: RouteReviewVerdict
    evidenceRef: str
    evidenceSha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @field_validator("route", "evidenceRef")
    @classmethod
    def _trim_nonblank_route_review_value(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("route-review route and evidenceRef must not be blank")
        return trimmed


class RouteReviewChildIntent(_RouteReviewDoc):
    """One canonical child intent bound by an atomic-master review."""

    ref: TaskDocumentRef
    taskIntent: TaskIntentIdentity


class RouteReviewScope(_RouteReviewDoc):
    """The canonical altitude and child population of one master review."""

    kind: Literal["atomic-master"] = "atomic-master"
    masterRef: TaskDocumentRef
    masterIntent: TaskIntentIdentity
    childIntents: list[RouteReviewChildIntent] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_child_identity_uniqueness(self) -> Self:
        refs = [child.ref for child in self.childIntents]
        if len(refs) != len(set(refs)):
            raise ValueError("atomic-master route-review child references must be unique")
        return self


class ReviewFinding(_RouteReviewDoc):
    """An immutable issue definition from the exhaustive first review."""

    findingId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    description: str = Field(min_length=1, max_length=8192)

    @field_validator("findingId", "description")
    @classmethod
    def _trim_finding_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("review finding identity and description must not be blank")
        return cleaned


class ReviewState(_RouteReviewDoc):
    """Small persisted state for one monotonic review sequence."""

    round: int = Field(default=0, ge=0)
    pending: bool = False
    baselineFindings: list[ReviewFinding] = Field(default_factory=list)
    remainingFindingIds: list[str] = Field(default_factory=list)
    # The ordinary cap is fixed in the transition service.  These two fields carry one
    # explicit developer answer and its bounded additional allowance without introducing
    # a second authority or history store.
    developerApproval: str | None = Field(default=None, max_length=8192)
    additionalRounds: int = Field(default=0, ge=0)

    @field_validator("developerApproval")
    @classmethod
    def _trim_developer_approval(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("developer approval must not be blank")
        return trimmed

    @model_validator(mode="after")
    def _check_state(self) -> Self:
        finding_ids = [item.findingId for item in self.baselineFindings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("review baseline finding IDs must be unique")
        if self.round == 0 and (
            self.pending
            or finding_ids
            or self.remainingFindingIds
            or self.developerApproval is not None
            or self.additionalRounds
        ):
            raise ValueError(
                "zero-round review state cannot carry pending, finding, or exception data"
            )
        if (self.additionalRounds == 0) != (self.developerApproval is None):
            raise ValueError(
                "review developer approval and additional rounds must be provided together"
            )
        if len(self.remainingFindingIds) != len(set(self.remainingFindingIds)):
            raise ValueError("review remaining finding IDs must be unique")
        if not set(self.remainingFindingIds).issubset(set(finding_ids)):
            raise ValueError("review remaining findings must belong to the sealed baseline")
        return self


class RouteReviewRecord(_RouteReviewDoc):
    """Plane-stamped review evidence bound to one exact Git candidate tree."""

    candidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    verdict: RouteReviewVerdict
    verdictRef: str
    reviewedAt: str
    routes: list[RouteReviewUnit] = Field(min_length=1)
    taskIntent: TaskIntentState = Field(default_factory=missing_task_intent)
    scope: RouteReviewScope | None = None
    verdictSha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    dependencies: EvidenceDependencies | None = None
    recordDigest: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @field_validator("verdictRef", "reviewedAt")
    @classmethod
    def _trim_nonblank_route_review_metadata(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("route-review verdictRef and reviewedAt must not be blank")
        return trimmed

    @model_validator(mode="after")
    def _check_route_review_coherence(self) -> Self:
        names = [route.route for route in self.routes]
        if len(names) != len(set(names)):
            raise ValueError("route-review routes must be unique")
        blocked = any(route.verdict == "block" for route in self.routes)
        if self.verdict == "block" and not blocked:
            raise ValueError("a blocking route-review verdict requires at least one blocked route")
        if self.verdict != "block" and blocked:
            raise ValueError("a passing route-review verdict cannot contain a blocked route")
        if self.scope is not None and not isinstance(self.taskIntent, TaskIntentIdentity):
            raise ValueError("an atomic-master route review requires an aggregate task intent")
        dependency_shape = (
            bool(self.verdictSha256),
            self.dependencies is not None,
            bool(self.recordDigest),
            all(route.evidenceSha256 for route in self.routes),
        )
        if any(dependency_shape) and not all(dependency_shape):
            raise ValueError(
                "route-review content addressing requires every evidence digest, "
                "the dependency declaration, and the record digest"
            )
        if all(dependency_shape):
            require_evidence_dependencies(self.dependencies, record_type="route-review/v1")
            # ``scope`` is master-only. Excluding absent optionals preserves the
            # canonical bytes and digest of existing leaf-owned records while a
            # populated atomic-master scope remains fully content addressed.
            payload = self.model_dump(
                mode="json",
                by_alias=True,
                exclude={"recordDigest"},
                exclude_none=True,
            )
            if canonical_sha256(payload) != self.recordDigest:
                raise ValueError("route-review record digest does not match its canonical bytes")
        return self


__all__ = [
    "ReviewFinding",
    "ReviewState",
    "RouteReviewChildIntent",
    "RouteReviewRecord",
    "RouteReviewScope",
    "RouteReviewUnit",
    "RouteReviewVerdict",
]
