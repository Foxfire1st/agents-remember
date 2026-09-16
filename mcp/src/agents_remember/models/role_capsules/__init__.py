"""Frozen role-capsule contract and the deterministic compiler that fills it.

This package is the master's frozen DTO surface plus the pure logic that turns an
admitted AR binding and canonical instruction sources into one capsule. It holds no
I/O: reading the sources from disk is
:mod:`agents_remember.application.role_capsules`.
"""

from agents_remember.models.role_capsules.compiler import (
    MANIFEST_SCHEMA,
    compile_role_capsule,
    compiled_manifest,
    refused_manifest,
)
from agents_remember.models.role_capsules.diagnostics import (
    CapsuleConflictRecord,
    CapsuleManifest,
    CapsuleRefusalOptions,
    CapsuleRejection,
    CapsuleSourceRecord,
)
from agents_remember.models.role_capsules.manifest import (
    COMPOSITION_MANIFEST_SCHEMA,
    CapsuleCompositionManifest,
    parse_composition_manifest,
)
from agents_remember.models.role_capsules.selection import select_scope
from agents_remember.models.role_capsules.sources import (
    CapsuleDeclaredInstruction,
    CapsuleSource,
    specializations_declared_identity,
)
from agents_remember.models.role_capsules.types import (
    CAPSULE_COMPOSITION_ORDER,
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleBlockIdentity,
    CapsuleCapsule,
    CapsuleCompilationResult,
    CapsuleDigest,
    CapsuleInstructionBlock,
    CapsuleInstructionUnit,
    CapsuleLauncherSeat,
    CapsuleOperation,
    CapsuleOverride,
    CapsuleRequirementBinding,
    CapsuleRole,
    CapsuleRoleSeat,
    CapsuleSeat,
    CapsuleSeatKind,
    CapsuleSelectionReference,
    CapsuleSkillReference,
    CapsuleSourceAdmission,
    CapsuleSourceSelection,
    CapsuleSuppliedProjection,
    CapsuleTaskContext,
    CapsuleTaskProjectionSource,
    CapsuleToolId,
    CapsuleToolPolicy,
    CapsuleToolRequest,
    compute_content_digest,
    compute_semantic_digest,
)
from agents_remember.models.role_capsules.vocabulary import (
    CAPSULE_LAUNCHER_MODE,
    CAPSULE_OPERATIONS,
    CAPSULE_ROLES,
)

__all__ = [
    "CAPSULE_COMPOSITION_ORDER",
    "CAPSULE_LAUNCHER_MODE",
    "CAPSULE_OPERATIONS",
    "CAPSULE_ROLES",
    "COMPOSITION_MANIFEST_SCHEMA",
    "MANIFEST_SCHEMA",
    "CapsuleAdmittedFacts",
    "CapsuleBinding",
    "CapsuleBlockIdentity",
    "CapsuleCapsule",
    "CapsuleCompilationResult",
    "CapsuleCompositionManifest",
    "CapsuleConflictRecord",
    "CapsuleDeclaredInstruction",
    "CapsuleDigest",
    "CapsuleInstructionBlock",
    "CapsuleInstructionUnit",
    "CapsuleLauncherSeat",
    "CapsuleManifest",
    "CapsuleOperation",
    "CapsuleOverride",
    "CapsuleRefusalOptions",
    "CapsuleRejection",
    "CapsuleRequirementBinding",
    "CapsuleRole",
    "CapsuleRoleSeat",
    "CapsuleSeat",
    "CapsuleSeatKind",
    "CapsuleSelectionReference",
    "CapsuleSkillReference",
    "CapsuleSource",
    "CapsuleSourceAdmission",
    "CapsuleSourceRecord",
    "CapsuleSourceSelection",
    "CapsuleSuppliedProjection",
    "CapsuleTaskContext",
    "CapsuleTaskProjectionSource",
    "CapsuleToolId",
    "CapsuleToolPolicy",
    "CapsuleToolRequest",
    "compile_role_capsule",
    "compiled_manifest",
    "compute_content_digest",
    "compute_semantic_digest",
    "parse_composition_manifest",
    "refused_manifest",
    "select_scope",
    "specializations_declared_identity",
]
