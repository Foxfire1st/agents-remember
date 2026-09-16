"""The diagnostic projection of a compilation: what was considered and why.

This is the explanation half of the frozen contract, kept beside — not inside — the
content half. A consumer that only delivers a capsule never has to construct any of
it, and a consumer that has to explain a refusal, or audit which source revision a
block came from, reads exactly these shapes.

Nothing here is an input to
:attr:`~agents_remember.models.role_capsules.types.CapsuleCapsule.semantic_digest`.
It carries the ephemeral facts a digest must ignore: sources that were considered and
not selected, collapsed duplicates, refusal text and wall-clock-free but run-specific
provenance. Keeping the two halves in separate modules is the structural version of
that rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.models.role_capsules.types import (
    CAPSULE_COMPOSITION_ORDER,
    CapsuleBinding,
    CapsuleBlockIdentity,
    CapsuleCompositionRoot,
    CapsuleDigest,
    CapsuleOperation,
    CapsuleRequirementRevision,
    CapsuleRole,
    CapsuleSeatKind,
    CapsuleSelectionReference,
    CapsuleToolId,
    _require_digest,
    _require_non_blank,
)


@dataclass(frozen=True, slots=True)
class CapsuleSourceRecord:
    """One source the compilation considered, with its revision and selection story."""

    identity: CapsuleBlockIdentity
    composition_root: CapsuleCompositionRoot
    path: str
    revision: CapsuleDigest
    authorities: tuple[CapsuleSelectionReference, ...] = ()
    selected: bool = False
    collapsed_duplicate: bool = False
    selection_reason: str = ""
    superseded_by: str | None = None
    superseded_kind: str | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.identity, "source record identity")
        _require_non_blank(self.path, "source record path")
        _require_digest(self.revision, "source record revision")


@dataclass(frozen=True, slots=True)
class CapsuleConflictRecord:
    """A stopped compilation's contradiction, kept as structured diagnostic facts."""

    identity: CapsuleBlockIdentity
    kind: str
    authorities: tuple[CapsuleSelectionReference, ...]
    contenders: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_blank(self.identity, "conflict identity")
        _require_non_blank(self.kind, "conflict kind")


@dataclass(frozen=True, slots=True)
class CapsuleRejection:
    """A precise refusal: a stable code, a legible detail, and the remedy.

    The code is stable so a caller can branch on it; the detail names what broke
    for an operator who does not know the internals; ``next_action`` names the
    owner that has to change. A refusal that cannot describe its own cause is
    indistinguishable from a wall.
    """

    status: str
    detail: str
    next_action: str = ""

    def __post_init__(self) -> None:
        _require_non_blank(self.status, "rejection status")
        _require_non_blank(self.detail, "rejection detail")

    def render(self) -> str:
        if self.next_action:
            return f"{self.status}: {self.detail} (remedy: {self.next_action})"
        return f"{self.status}: {self.detail}"


@dataclass(frozen=True, slots=True)
class CapsuleRefusalOptions:
    """Optional overrides for a refusal's explanation projection."""

    conflicts: tuple[CapsuleConflictRecord, ...] = ()
    role: CapsuleRole | None = None


@dataclass(frozen=True, slots=True)
class CapsuleManifest:
    """The diagnostic projection: what was considered, selected, and why.

    Everything an operator needs to explain a compilation — the admitted binding
    facts, each source revision and content digest, the reason each block was
    selected, both supersession classes, and any stopped conflict. It is not part
    of the capsule identity and never will be: it carries ephemeral facts
    (unselected sources, unused specializations, refusal detail) that must not
    move the semantic digest.
    """

    manifest_schema: str
    role: CapsuleRole | None
    operation: CapsuleOperation
    seat_kind: CapsuleSeatKind
    task_reference: str
    task_document_digest: CapsuleDigest
    repository_id: str
    work_branch: str
    requirements: tuple[CapsuleRequirementRevision, ...]
    granted_tools: tuple[CapsuleToolId, ...]
    composition_order: tuple[CapsuleCompositionRoot, ...]
    instruction_identities: tuple[CapsuleBlockIdentity, ...]
    sources: tuple[CapsuleSourceRecord, ...]
    conflicts: tuple[CapsuleConflictRecord, ...] = ()
    rejection: CapsuleRejection | None = None
    semantic_digest: CapsuleDigest = ""

    def __post_init__(self) -> None:
        _require_non_blank(self.manifest_schema, "manifest_schema")
        _require_non_blank(self.task_reference, "manifest task_reference")
        _require_digest(self.task_document_digest, "manifest task_document_digest")

    @classmethod
    def for_refusal(
        cls,
        manifest_schema: str,
        binding: CapsuleBinding,
        rejection: CapsuleRejection,
        options: CapsuleRefusalOptions | None = None,
    ) -> CapsuleManifest:
        """The explanation of a compilation that stopped, with no capsule behind it.

        A refusal is still an admitted binding, and the half of the manifest that is
        about admission stays true: who the seat was, which operation was asked for,
        which requirement revisions were owned and which tools were already
        permitted. Only the selection facts are empty, because nothing was selected.
        """

        chosen = options or CapsuleRefusalOptions()
        seat = binding.admitted.seat
        return cls(
            manifest_schema=manifest_schema,
            role=chosen.role if chosen.role is not None else seat.role,
            operation=binding.operation,
            seat_kind=seat.kind,
            task_reference=binding.admitted.task_reference,
            task_document_digest=binding.admitted.task_document_digest,
            repository_id=binding.admitted.repository_id,
            work_branch=binding.admitted.work_branch,
            requirements=binding.admitted.requirement_identities,
            granted_tools=tuple(sorted(binding.admitted.tool_policy.granted)),
            composition_order=CAPSULE_COMPOSITION_ORDER,
            instruction_identities=(),
            sources=(),
            conflicts=chosen.conflicts,
            rejection=rejection,
        )

    def summary(self) -> dict[str, object]:
        """The compact, JSON-shaped explanation a consumer or operator can print."""

        return {
            "manifestSchema": self.manifest_schema,
            "role": self.role,
            "operation": self.operation,
            "seatKind": self.seat_kind,
            "taskReference": self.task_reference,
            "repositoryId": self.repository_id,
            "workBranch": self.work_branch,
            "requirements": list(self.requirements),
            "instructionIdentities": list(self.instruction_identities),
            "semanticDigest": self.semantic_digest,
            "rejection": (
                None
                if self.rejection is None
                else {
                    "status": self.rejection.status,
                    "detail": self.rejection.detail,
                    "nextAction": self.rejection.next_action,
                }
            ),
            "sources": [
                {
                    "identity": record.identity,
                    "path": record.path,
                    "revision": record.revision,
                    "selected": record.selected,
                    "reason": record.selection_reason,
                    "supersededBy": record.superseded_by,
                }
                for record in self.sources
            ],
        }


__all__ = [
    "CapsuleConflictRecord",
    "CapsuleManifest",
    "CapsuleRefusalOptions",
    "CapsuleRejection",
    "CapsuleSourceRecord",
]
