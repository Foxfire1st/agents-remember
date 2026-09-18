"""The deterministic, harness-independent role-capsule compiler.

One pure function turns admitted facts plus canonical source bytes into an ordered
capsule and an explanation of itself: :func:`compile_role_capsule`. It opens no
file, reaches no network and calls no model — reading sources is the
:mod:`agents_remember.application.role_capsules` boundary's job, and this module is
an ordinary function over what that boundary admitted.

The work is split so each part can be understood and tested alone:

* :mod:`~agents_remember.models.role_capsules.manifest` parses the canonical
  composition metadata;
* :mod:`~agents_remember.models.role_capsules.selection` turns the admitted binding
  into a scope, refusals included;
* :mod:`~agents_remember.models.role_capsules.source_set` proves the admitted files
  match the locked plan;
* :mod:`~agents_remember.models.role_capsules.resolution` reduces each identity to
  one block and stops on an unresolved contradiction;
* :mod:`~agents_remember.models.role_capsules.tools` narrows requested capabilities
  to the admitted policy.

What this module owns is the seal: the semantic digest and the diagnostic manifest.
The digest covers the admitted facts and the bytes that were composed, and nothing
else — no timestamp, no unselected source, no refusal text — so two runs over the
same admission and the same files are the same capsule.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.diagnostics import (
    CapsuleConflictRecord,
    CapsuleManifest,
    CapsuleRefusalOptions,
    CapsuleRejection,
    CapsuleSourceRecord,
)
from agents_remember.models.role_capsules.manifest import (
    CapsuleCompositionManifest,
    parse_composition_manifest,
)
from agents_remember.models.role_capsules.resolution import (
    ResolvedInstruction,
    gather_candidates,
    override_winners,
    resolve_instructions,
)
from agents_remember.models.role_capsules.selection import CapsuleScope, select_scope
from agents_remember.models.role_capsules.source_set import (
    admit_source_set,
    identity_index,
    sources_by_path,
)
from agents_remember.models.role_capsules.sources import (
    CapsuleSource,
    instruction_identity,
    skills_declared_identity,
)
from agents_remember.models.role_capsules.statuses import STATUS_TASK_CONTEXT_DIGEST_MISMATCH
from agents_remember.models.role_capsules.tools import narrow_tool_requests
from agents_remember.models.role_capsules.types import (
    SUPERSEDE_DUPLICATE_IDENTITY,
    CapsuleBinding,
    CapsuleBlockIdentity,
    CapsuleCapsule,
    CapsuleCompilationResult,
    CapsuleDigest,
    CapsuleInstructionBlock,
    CapsuleInstructionUnit,
    CapsuleSkillReference,
    CapsuleSourceAdmission,
    CapsuleSourceSelection,
    CapsuleTaskContext,
    CapsuleTaskProjectionSource,
    CapsuleToolRequest,
    compute_content_digest,
    compute_semantic_digest,
)

MANIFEST_SCHEMA = "ar-role-capsule-manifest/v1"

#: The revision recorded for a declared source whose bytes were never admitted. It is
#: deliberately not a content digest: an unread source has no content to address.
UNREAD_REVISION = "sha256:" + "0" * 64


def compile_role_capsule(
    binding: CapsuleBinding,
    manifest_bytes: bytes,
    sources: tuple[CapsuleSource, ...],
    selection: CapsuleSourceSelection,
    projection: CapsuleTaskProjectionSource | None = None,
) -> CapsuleCompilationResult:
    """Compile one role capsule, or refuse with a precise typed error.

    ``projection`` is the task-context input seam: a later task-projection consumer
    supplies it, this compiler only consumes it, and its content is verified against
    the digest it declares before it enters the capsule.
    """

    parsed = parse_composition_manifest(manifest_bytes)
    scope = select_scope(parsed, binding)
    by_path = sources_by_path(sources)
    identities = identity_index(parsed, selection)
    admit_source_set(
        parsed,
        CapsuleSourceAdmission(binding=binding, selection=selection),
        by_path,
        identities,
        scope.declared(binding),
    )
    resolved = resolve_instructions(
        gather_candidates(scope.declared(binding), by_path, identities),
        binding.overrides,
        by_path,
        identities,
        _preferred_paths(identities, selection, binding),
    )
    blocks = tuple(item.block() for item in resolved)
    task_context = verified_task_context(binding, projection)
    tools = narrow_tool_requests(_declared_tools(scope), binding)
    skills = skill_references(scope, parsed, by_path)
    digest = semantic_digest(binding, blocks, task_context, tools, skills)
    return CapsuleCompilationResult(
        capsule=CapsuleCapsule(
            instruction_units=tuple(
                CapsuleInstructionUnit(unit_index=index, block=block)
                for index, block in enumerate(blocks)
            ),
            task_context=task_context,
            skill_references=skills,
            requested_tools=tools,
            semantic_digest=digest,
            binding=binding,
        ),
        manifest=compiled_manifest(
            scope,
            binding,
            digest=digest,
            identities=tuple(block.identity for block in blocks),
            records=source_records(resolved, by_path, parsed, selection),
        ),
    )


# --------------------------------------------------------------------------------------
# The task-context channel.
# --------------------------------------------------------------------------------------


def skill_references(
    scope: CapsuleScope,
    parsed: CapsuleCompositionManifest,
    by_path: Mapping[str, CapsuleSource],
) -> tuple[CapsuleSkillReference, ...]:
    """One reference per skill the selected seat declares, in declaration order.

    A skill reference is *optional* in the sense that a seat which declares no skills
    returns an empty tuple — but a declared skill always produces one. Dropping a
    declared reference would be the same silent omission as dropping a required
    instruction block, so the manifest refuses a reference it cannot resolve
    (``_require_role_skills_are_declared``) rather than letting one vanish here.

    ``revision`` is the content digest of the skill's declared root file, admitted
    alongside the instruction sources. That makes the reference content-addressed: it
    moves when the skill it points at moves, and two compilations over the same bytes
    carry the same revision.
    """

    entry = scope.role_entry
    if entry is None or not entry.skills:
        return ()
    references: list[CapsuleSkillReference] = []
    for name in entry.skills:
        declared = parsed.skill_entry(name)
        # The source set has already proven this file admitted *as this skill*, so a
        # missing entry here is unreachable; a second check would be a guard nothing can
        # satisfy. The typing is what makes the lookup total.
        source = by_path[declared.source]
        references.append(
            CapsuleSkillReference(
                identity=skills_declared_identity(declared.origin, name),
                origin=declared.origin,
                uri=declared.uri,
                revision=source.revision,
            )
        )
    return tuple(references)


def verified_task_context(
    binding: CapsuleBinding, projection: CapsuleTaskProjectionSource | None
) -> CapsuleTaskContext | None:
    """The supplied projection, once its bytes are proven to match its declared digest."""

    if projection is None:
        return None
    context = projection.project(binding)
    if context is None:
        return None
    observed = compute_content_digest(context.markdown.encode("utf-8"))
    if observed != context.content_digest:
        raise CapsuleCompilationError(
            status=STATUS_TASK_CONTEXT_DIGEST_MISMATCH,
            detail=(
                f"supplied task context from {context.origin!r} declares "
                f"{context.content_digest} but its bytes digest to {observed}"
            ),
            next_action=(
                "re-render the task projection, or fix the digest it declares; an unverifiable "
                "projection is not carried into a capsule"
            ),
        )
    return context


# --------------------------------------------------------------------------------------
# The seal: semantic digest and diagnostic manifest.
# --------------------------------------------------------------------------------------


def semantic_digest(
    binding: CapsuleBinding,
    blocks: tuple[CapsuleInstructionBlock, ...],
    task_context: CapsuleTaskContext | None,
    tools: tuple[CapsuleToolRequest, ...],
    skills: tuple[CapsuleSkillReference, ...] = (),
) -> CapsuleDigest:
    """The capsule identity: admitted facts plus the bytes that were composed.

    Deliberately excludes everything ephemeral — unselected sources, refusal detail,
    wall-clock values and the diagnostic manifest itself — so the identity of a
    compilation does not move when only diagnostics do.
    """

    seat = binding.admitted.seat
    lines = [
        f"manifest-schema\t{MANIFEST_SCHEMA}",
        f"seat-kind\t{seat.kind}",
        f"seat\t{seat.key}",
        f"role\t{seat.role or ''}",
        f"operation\t{binding.operation}",
        f"repository\t{binding.admitted.repository_id}",
        f"work-branch\t{binding.admitted.work_branch}",
        f"task\t{binding.admitted.task_reference}",
        f"task-document\t{binding.admitted.task_document_digest}",
        "requirements\t" + ",".join(binding.admitted.requirement_identities),
    ]
    # Only the specializations this compilation actually composed are identity: an
    # admitted-but-unselected specialization changes the diagnostics (it is recorded
    # in the manifest) and must not change the capsule.
    lines.append("specializations\t" + ",".join(_selected_specializations(blocks)))
    lines += [
        f"block\t{block.composition_root}\t{block.identity}\t{block.revision}" for block in blocks
    ]
    lines += [f"tool\t{request.tool_id}" for request in tools]
    lines += [
        f"skill\t{reference.origin}\t{reference.identity}\t{reference.revision}"
        for reference in skills
    ]
    if task_context is not None:
        lines.append(
            "task-context\t"
            f"{task_context.origin}\t{task_context.projection_revision}\t"
            f"{task_context.content_digest}"
        )
    return compute_semantic_digest("\n".join(lines))


def _preferred_paths(
    identities: Mapping[str, CapsuleBlockIdentity],
    selection: CapsuleSourceSelection,
    binding: CapsuleBinding,
) -> Mapping[CapsuleBlockIdentity, str]:
    """The one admitted path that is the declared carrier of each identity.

    Only repository specializations can have more than one path claiming an identity,
    so only they need a declared preference; every manifest-routed block has exactly
    one path by construction, enforced where the manifest is parsed. An explicit
    override's ``admitted_path`` outranks the selection order, because it is the
    admitted decision rather than an accident of ordering.
    """

    preferred: dict[CapsuleBlockIdentity, str] = {}
    for path in selection.specialization:
        preferred.setdefault(identities[path], path)
    preferred.update(override_winners(binding.overrides))
    return preferred


def _selected_specializations(blocks: tuple[CapsuleInstructionBlock, ...]) -> tuple[str, ...]:
    return tuple(block.identity for block in blocks if block.composition_root == "specialization")


def source_records(
    resolved: tuple[ResolvedInstruction, ...],
    by_path: Mapping[str, CapsuleSource],
    parsed: CapsuleCompositionManifest,
    selection: CapsuleSourceSelection,
) -> tuple[CapsuleSourceRecord, ...]:
    """Every source the compilation considered or the metadata declared, with its story.

    Declared repository specializations are included even when no source bytes were
    admitted for them: the admission is a fact worth auditing, and an admission that
    quietly vanished from the explanation would be the same class of defect as a
    silently dropped obligation.
    """

    selected_paths = {item.source.path for item in resolved}
    records = [
        CapsuleSourceRecord(
            identity=item.identity,
            composition_root=item.composition_root,
            path=item.source.path,
            revision=item.source.revision,
            authorities=item.authorities,
            selected=True,
            selection_reason=_selection_reason(item),
            superseded_by=item.superseded_by,
            superseded_kind=item.superseded_kind,
        )
        for item in resolved
    ]
    for item in resolved:
        records += [
            CapsuleSourceRecord(
                identity=item.identity,
                composition_root=source.composition_root,
                path=source.path,
                revision=source.revision,
                selected=False,
                collapsed_duplicate=True,
                selection_reason=(
                    "byte-identical duplicate of a selected block identity; collapsed so the same "
                    "obligation is stated once"
                ),
                superseded_by=item.source.path,
                superseded_kind=SUPERSEDE_DUPLICATE_IDENTITY,
            )
            for source in item.collapsed
        ]
    for name, entry in sorted(parsed.skills.items()):
        source = by_path.get(entry.source)
        if source is None:
            continue
        records.append(
            CapsuleSourceRecord(
                identity=skills_declared_identity(entry.origin, name),
                composition_root="skill",
                path=entry.source,
                revision=source.revision,
                selected=False,
                selection_reason=(
                    "admitted as a skill reference's revision source, not as an instruction block; "
                    "the capsule points at this skill rather than composing its content"
                ),
            )
        )
    admitted = set(selection.specialization)
    for name, entry in sorted(parsed.specializations.items()):
        path = entry.source
        if path in selected_paths or any(
            record.path == path and not record.collapsed_duplicate for record in records
        ):
            continue
        source = by_path.get(path)
        records.append(
            CapsuleSourceRecord(
                identity=instruction_identity("specialization", name),
                composition_root="specialization",
                path=path,
                revision=source.revision if source is not None else UNREAD_REVISION,
                selected=False,
                selection_reason=(
                    "declared repository specialization that this role/operation scope did not "
                    "select; recorded so the admission stays auditable"
                    if path in admitted
                    else "declared repository specialization this compilation did not select"
                ),
            )
        )
    return tuple(records)


def compiled_manifest(
    scope: CapsuleScope,
    binding: CapsuleBinding,
    *,
    digest: CapsuleDigest = "",
    identities: tuple[CapsuleBlockIdentity, ...] = (),
    records: tuple[CapsuleSourceRecord, ...] = (),
) -> CapsuleManifest:
    """The diagnostic projection of an admitted binding, with or without a capsule."""

    seat = binding.admitted.seat
    return CapsuleManifest(
        manifest_schema=MANIFEST_SCHEMA,
        role=scope.role,
        operation=scope.operation,
        seat_kind=seat.kind,
        task_reference=binding.admitted.task_reference,
        task_document_digest=binding.admitted.task_document_digest,
        repository_id=binding.admitted.repository_id,
        work_branch=binding.admitted.work_branch,
        requirements=binding.admitted.requirement_identities,
        granted_tools=tuple(sorted(binding.admitted.tool_policy.granted)),
        composition_order=scope.composition_order,
        instruction_identities=identities,
        sources=records,
        semantic_digest=digest,
    )


def refused_manifest(binding: CapsuleBinding, error: CapsuleCompilationError) -> CapsuleManifest:
    """The explanation projection for a compilation that stopped before selection.

    Built from the same admitted facts as a successful manifest, so a refusal and
    the compilation it refused are directly comparable, and carrying the refusal's
    stable status, detail and remedy rather than prose a caller would have to parse.
    """

    return CapsuleManifest.for_refusal(
        MANIFEST_SCHEMA,
        binding,
        _rejection_of(error),
        CapsuleRefusalOptions(conflicts=_conflict_records(error)),
    )


def manifest_for_error(
    parsed: CapsuleCompositionManifest, binding: CapsuleBinding, error: CapsuleCompilationError
) -> CapsuleManifest:
    """The failure projection when the manifest parsed but the compilation stopped."""

    role = binding.admitted.seat.role
    resolved_role = role if role is not None and role in parsed.roles else None
    return CapsuleManifest.for_refusal(
        MANIFEST_SCHEMA,
        binding,
        _rejection_of(error),
        CapsuleRefusalOptions(conflicts=_conflict_records(error), role=resolved_role),
    )


def _declared_tools(scope: CapsuleScope) -> tuple[tuple[str, str], ...]:
    entry = scope.role_entry
    if entry is None:
        return ()
    return tuple((tool_id, f"role:{entry.role}") for tool_id in entry.tools)


def _conflict_records(error: CapsuleCompilationError) -> tuple[CapsuleConflictRecord, ...]:
    """The structured contradiction rows a stopped conflict carries."""

    return tuple(
        CapsuleConflictRecord(
            identity=str(row.get("identity", "")),
            kind=str(row.get("kind", "")),
            authorities=_string_tuple(row.get("authorities")),
            contenders=_string_tuple(row.get("contenders")),
        )
        for row in error.conflicts
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    """A conflict row's string list, tolerating a malformed row without crashing.

    The rows are assembled by this compiler, so a non-list here is a programming
    error rather than user input; it degrades to an empty list so a diagnostic can
    never itself raise while explaining a refusal.
    """

    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _rejection_of(error: CapsuleCompilationError) -> CapsuleRejection:
    """The bounded refusal projection: code, detail and advertised remedy."""

    return CapsuleRejection(status=error.status, detail=error.detail, next_action=error.next_action)


def _selection_reason(item: ResolvedInstruction) -> str:
    if item.superseded_by is not None:
        return (
            f"selected by {', '.join(item.authorities)} and explicitly superseded by "
            f"{item.superseded_by}"
        )
    return f"selected by {', '.join(item.authorities)}"


__all__ = [
    "MANIFEST_SCHEMA",
    "compile_role_capsule",
    "compiled_manifest",
    "manifest_for_error",
    "refused_manifest",
    "semantic_digest",
    "source_records",
    "verified_task_context",
]
