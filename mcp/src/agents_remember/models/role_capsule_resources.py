"""Wire contracts for the capsule operation and the skill resource surface.

Both top-level responses are strict: this server owns every field in them. The
capsule response carries the compiled content plus its provenance — each
instruction block's source path and revision, each skill reference's origin and
revision, and the requested tool identities — so a consumer can pin exactly what it
received without re-deriving it. A refusal is the same envelope with ``ok`` false, a
typed ``refusalStatus``, and whatever was still true about the seat it addressed.

The nested payloads are plain strict values, not envelopes: they are constructed
once by the application boundary and are never independently tokenized or finalized.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.base import StrictResponseModel, ToolResponse

#: The bridge name a skills list response declares.
SKILL_CATALOG_LIST_OPERATION = "skill_catalog_list"

#: The bridge name a skill read response declares.
SKILL_CATALOG_READ_OPERATION = "skill_catalog_read"

#: The capsule operation's bridge name.
ROLE_CAPSULE_COMPILE_OPERATION = "role_capsule_compile"

#: The trust statement every served skill file carries.
SERVER_SUPPLIED_CONTENT_TRUST = "server-supplied-data"


class CapsuleInstructionBlockPayload(StrictResponseModel):
    """One composed instruction block, with the source it came from and its hashes."""

    blockIdentity: str
    compositionRoot: str
    sourcePath: str
    revision: str
    contentDigest: str
    authorities: list[str] = Field(default_factory=list)
    content: str


class CapsuleSkillReferencePayload(StrictResponseModel):
    """One skill the capsule points at: distinct identity, origin and revision."""

    skillIdentity: str
    origin: str
    uri: str
    revision: str


class CapsuleRequestedToolPayload(StrictResponseModel):
    """One tool identity the capsule requests, with the source that asked for it."""

    toolId: str
    authority: str


class CapsuleTaskContextPayload(StrictResponseModel):
    """The projected task context: rendered Markdown plus the revision it came from."""

    origin: str
    projectionRevision: str
    contentDigest: str
    markdown: str


class CapsuleSourceRecordPayload(StrictResponseModel):
    """One source the compile admitted, whether it was composed or only referenced."""

    sourceIdentity: str
    compositionRoot: str
    sourcePath: str
    revision: str
    selected: bool
    collapsedDuplicate: bool
    selectionReason: str
    supersededBy: str | None = None
    supersededKind: str | None = None


class RoleCapsuleResponse(ToolResponse):
    """One capsule compile: the compiled content, its provenance, or its refusal.

    ``explanation`` is the operator-facing line for either shape, so a caller that
    only wants to know what happened does not have to reconstruct it. A refusal
    still carries the seat and task revision when they were established, because
    those are the facts that make the refusal actionable.
    """

    operation: Literal["role_capsule_compile"] = ROLE_CAPSULE_COMPILE_OPERATION
    explanation: str
    refusalStatus: str | None = None
    refusalNextAction: str | None = None
    role: str | None = None
    seatAltitude: str | None = None
    operationName: str | None = None
    taskReference: str | None = None
    taskDocumentDigest: str | None = None
    repositoryId: str | None = None
    workBranch: str | None = None
    semanticDigest: str | None = None
    compositionOrder: list[str] = Field(default_factory=list)
    instructionIdentities: list[str] = Field(default_factory=list)
    instructions: list[CapsuleInstructionBlockPayload] = Field(default_factory=list)
    skillReferences: list[CapsuleSkillReferencePayload] = Field(default_factory=list)
    requestedTools: list[CapsuleRequestedToolPayload] = Field(default_factory=list)
    grantedTools: list[str] = Field(default_factory=list)
    taskContext: CapsuleTaskContextPayload | None = None
    sources: list[CapsuleSourceRecordPayload] = Field(default_factory=list)
    conflictKinds: list[str] = Field(default_factory=list)


class SkillCatalogEntryPayload(StrictResponseModel):
    """One skill in the discovery registry, with no file body."""

    name: str
    description: str
    origin: str
    uri: str
    revision: str
    files: list[str] = Field(default_factory=list)
    declaredAllowedTools: list[str] = Field(default_factory=list)


class UnreadableSkillPayload(StrictResponseModel):
    """One discovered skill directory that cannot be served, with its cause."""

    skillPath: str
    reason: str


class SkillCatalogListResponse(ToolResponse):
    """Discovery metadata only: what this server publishes, without any body.

    The listing is the host's registry read out loud. It deliberately carries no
    file content, because a discovery surface that shipped bodies would load the
    whole catalog into whatever reads it.
    """

    operation: Literal["skill_catalog_list"] = SKILL_CATALOG_LIST_OPERATION
    origin: str
    sourceRoot: str
    indexUri: str
    skills: list[SkillCatalogEntryPayload] = Field(default_factory=list)
    unreadable: list[UnreadableSkillPayload] = Field(default_factory=list)


class SkillCatalogReadResponse(ToolResponse):
    """One served skill file: the selected content, its origin and its revision."""

    operation: Literal["skill_catalog_read"] = SKILL_CATALOG_READ_OPERATION
    uri: str
    skillName: str
    skillIdentity: str
    origin: str
    relativePath: str
    mimeType: str
    revision: str
    skillRevision: str
    content: str
    declaredAllowedTools: list[str] = Field(default_factory=list)
    contentTrust: str = SERVER_SUPPLIED_CONTENT_TRUST


__all__ = [
    "ROLE_CAPSULE_COMPILE_OPERATION",
    "SERVER_SUPPLIED_CONTENT_TRUST",
    "SKILL_CATALOG_LIST_OPERATION",
    "SKILL_CATALOG_READ_OPERATION",
    "CapsuleInstructionBlockPayload",
    "CapsuleRequestedToolPayload",
    "CapsuleSkillReferencePayload",
    "CapsuleSourceRecordPayload",
    "CapsuleTaskContextPayload",
    "RoleCapsuleResponse",
    "SkillCatalogEntryPayload",
    "SkillCatalogListResponse",
    "SkillCatalogReadResponse",
    "UnreadableSkillPayload",
]
