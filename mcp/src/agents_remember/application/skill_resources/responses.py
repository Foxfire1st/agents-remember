"""Build the wire responses the capsule and skill surfaces return.

One place where the compiled values become the response contract, so the MCP
adapter stays a registration layer and the payload shape has a single definition.
Every field here is copied from a value an owner already produced: nothing in this
module decides a selection, a permission, or a trust level.
"""

from __future__ import annotations

from agents_remember.models.role_capsule_resources import (
    SERVER_SUPPLIED_CONTENT_TRUST,
    CapsuleInstructionBlockPayload,
    CapsuleRequestedToolPayload,
    CapsuleSkillReferencePayload,
    CapsuleSourceRecordPayload,
    CapsuleTaskContextPayload,
    RoleCapsuleResponse,
    SkillCatalogEntryPayload,
    SkillCatalogListResponse,
    SkillCatalogReadResponse,
    UnreadableSkillPayload,
)
from agents_remember.models.skill_resources import (
    SKILL_INDEX_URI,
    SkillResourceCatalog,
    SkillResourceEntry,
)

from .capsule import CapsuleCompileOutcome
from .catalog import ServedSkillFile, unreadable_skills


def role_capsule_response(outcome: CapsuleCompileOutcome) -> RoleCapsuleResponse:
    """The response for one compile attempt: the capsule, or the refusal's explanation."""

    binding = outcome.binding
    admitted = None if binding is None else binding.admitted
    response = RoleCapsuleResponse(
        ok=outcome.ok,
        explanation=outcome.explanation(),
        role=None if admitted is None else admitted.seat.role,
        seatAltitude=None if admitted is None else getattr(admitted.seat, "altitude", None),
        operationName=None if binding is None else binding.operation,
        taskReference=None if admitted is None else admitted.task_reference,
        taskDocumentDigest=None if admitted is None else admitted.task_document_digest,
        repositoryId=None if admitted is None else admitted.repository_id,
        workBranch=None if admitted is None else admitted.work_branch,
        grantedTools=[] if admitted is None else sorted(admitted.tool_policy.granted),
    )
    if outcome.refusal is not None:
        response.refusalStatus = outcome.refusal.status
        response.refusalNextAction = outcome.refusal.next_action
    _fill_manifest(response, outcome)
    return response


def _fill_manifest(response: RoleCapsuleResponse, outcome: CapsuleCompileOutcome) -> None:
    if outcome.compilation is None:
        return
    manifest = outcome.compilation.manifest
    response.compositionOrder = list(manifest.composition_order)
    response.instructionIdentities = list(manifest.instruction_identities)
    response.conflictKinds = sorted({row.kind for row in manifest.conflicts})
    response.sources = [
        CapsuleSourceRecordPayload(
            sourceIdentity=row.identity,
            compositionRoot=row.composition_root,
            sourcePath=row.path,
            revision=row.revision,
            selected=row.selected,
            collapsedDuplicate=row.collapsed_duplicate,
            selectionReason=row.selection_reason,
            supersededBy=row.superseded_by,
            supersededKind=row.superseded_kind,
        )
        for row in manifest.sources
    ]
    result = outcome.compilation.result
    if result is None:
        return
    capsule = result.capsule
    response.semanticDigest = result.semantic_digest
    response.instructions = [
        CapsuleInstructionBlockPayload(
            blockIdentity=unit.block.identity,
            compositionRoot=unit.block.composition_root,
            sourcePath=unit.block.source_path,
            revision=unit.block.revision,
            contentDigest=unit.block.content_digest,
            authorities=list(unit.block.authorities),
            content=unit.block.content,
        )
        for unit in capsule.instruction_units
    ]
    response.skillReferences = [
        CapsuleSkillReferencePayload(
            skillIdentity=reference.identity,
            origin=reference.origin,
            uri=reference.uri,
            revision=reference.revision,
        )
        for reference in capsule.skill_references
    ]
    response.requestedTools = [
        CapsuleRequestedToolPayload(toolId=request.tool_id, authority=request.authority)
        for request in capsule.requested_tools
    ]
    context = capsule.task_context
    response.taskContext = (
        None
        if context is None
        else CapsuleTaskContextPayload(
            origin=context.origin,
            projectionRevision=context.projection_revision,
            contentDigest=context.content_digest,
            markdown=context.markdown,
        )
    )


def skill_catalog_list_response(catalog: SkillResourceCatalog) -> SkillCatalogListResponse:
    """Discovery metadata for every served skill, carrying no file body."""

    return SkillCatalogListResponse(
        ok=True,
        origin=catalog.origin,
        sourceRoot=catalog.source_root,
        indexUri=SKILL_INDEX_URI,
        skills=[_entry_payload(entry) for entry in catalog.entries],
        unreadable=[
            UnreadableSkillPayload(skillPath=row.skill_path, reason=row.reason)
            for row in unreadable_skills(catalog)
        ],
    )


def skill_catalog_read_response(
    served: ServedSkillFile, *, skill_revision: str
) -> SkillCatalogReadResponse:
    """One served skill file, with the origin and revision it is published under."""

    entry = served.entry
    record = served.record
    text = served.text
    if text is None:
        raise ValueError(
            f"skill resource {record.uri!r} is not UTF-8 text and cannot be served as an MCP "
            "text resource"
        )
    return SkillCatalogReadResponse(
        ok=True,
        uri=record.uri,
        skillName=entry.name,
        skillIdentity=entry.identity,
        origin=entry.origin,
        relativePath=record.relative_path,
        mimeType=record.mime_type,
        revision=record.revision,
        skillRevision=skill_revision,
        content=text,
        declaredAllowedTools=list(entry.declared_allowed_tools),
        contentTrust=SERVER_SUPPLIED_CONTENT_TRUST,
    )


def _entry_payload(entry: SkillResourceEntry) -> SkillCatalogEntryPayload:
    root = entry.root
    return SkillCatalogEntryPayload(
        name=entry.name,
        description=entry.description,
        origin=entry.origin,
        uri=entry.uri,
        revision="" if root is None else root.revision,
        files=[record.relative_path for record in entry.files],
        declaredAllowedTools=list(entry.declared_allowed_tools),
    )


__all__ = [
    "role_capsule_response",
    "skill_catalog_list_response",
    "skill_catalog_read_response",
]
