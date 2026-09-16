"""Frozen input/output contract for the deterministic role-capsule compiler.

This module is the master's frozen DTO surface. A later consumer (task
projection, MCP delivery, a harness adapter) reads these shapes and does not
redefine them: a consumer that believes one is wrong reports that to the owner
rather than forking a second shape.

Layer placement. This package holds only dependency-free value types, canonical
source parsing and selection logic, so it sits in ``models``
(``layers.toml``: ``errors < kernel < models < ...``). Reading a source file from
disk, resolving an admitted coordination root and encoding bytes are the
:mod:`agents_remember.application.role_capsules` boundary's job. Nothing here
opens a file, reaches the network, or calls a model.

The three planes are kept structurally apart, because collapsing any two of them
is the failure this contract exists to prevent:

* :class:`CapsuleAdmittedFacts` and :class:`CapsuleBinding` are the *admitted*
  input. Every field is decided by an existing AR owner; the compiler never
  infers a role from prose and never grants a capability.
* :class:`CapsuleCapsule` is the *content* output the model eventually reads.
* :class:`CapsuleManifest` is the *diagnostic* output. It carries source
  revisions, content digests and the reason each block was selected; it never
  feeds :attr:`CapsuleCapsule.semantic_digest`, so timestamps and ephemeral
  diagnostics cannot change the identity of the same compilation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agents_remember.models.role_capsules.vocabulary import (
    CAPSULE_COMPOSITION_ORDER,
    CapsuleCompositionRoot,
    CapsuleOperation,
    CapsuleRole,
    CapsuleSeatKind,
)

if TYPE_CHECKING:
    from agents_remember.models.role_capsules.diagnostics import CapsuleManifest

# --------------------------------------------------------------------------------------
# Vocabulary for the contract below.
# --------------------------------------------------------------------------------------

#: An instruction block identity inside its composition root: ``"<root>:<name>"``.
#: It is the canonical dedup/supersession key and is independent of which file
#: happened to carry the block.
CapsuleBlockIdentity = str
#: Where the instruction came from, named so the reason is legible in a manifest:
#: ``"<root>:<name>:<selection>"``.
CapsuleSelectionReference = str
#: A capability identifier in the existing AR permission policy, e.g. ``"task_doc"``.
CapsuleToolId = str
#: A skill reference identity with its server/repository origin, e.g.
#: ``"l-01-agent-lifecycles@skills"``.
CapsuleSkillIdentity = str
#: A stable requirement identity with its revision, e.g. ``"CAPS-R02@v1"``.
CapsuleRequirementRevision = str
#: A content digest in ``"sha256:<hex>"`` form.
CapsuleDigest = str

SHA256_PREFIX = "sha256:"

SELECTION_SHARED_CORE = "shared-core"
SELECTION_ROLE_BLOCK = "role-block"
SELECTION_OPERATION_BLOCK = "operation-block"
SELECTION_REPOSITORY_SPECIALIZATION = "repository-specialization"
SELECTION_EXPLICIT_SUPERSESSION = "explicit-supersession"

SUPERSEDE_EXPLICIT = "explicit-supersession"
SUPERSEDE_DUPLICATE_IDENTITY = "duplicate-identity-collapsed"

CONFLICT_EQUAL_AUTHORITY = "equal-authority-contradiction"
CONFLICT_DUPLICATE_IDENTITY = "duplicate-identity"


def compute_content_digest(content: bytes) -> CapsuleDigest:
    """Return the one canonical content digest used everywhere in this contract."""

    return SHA256_PREFIX + hashlib.sha256(content).hexdigest()


def compute_semantic_digest(canonical_document: str) -> CapsuleDigest:
    """Return the semantic digest of an already-canonicalised document."""

    return SHA256_PREFIX + hashlib.sha256(canonical_document.encode("utf-8")).hexdigest()


def _require_non_blank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _require_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(SHA256_PREFIX):
        raise ValueError(f"{field} must be a {SHA256_PREFIX}<hex> digest")
    digits = value[len(SHA256_PREFIX) :]
    if len(digits) != 64 or any(character not in "0123456789abcdef" for character in digits):
        raise ValueError(f"{field} must be a {SHA256_PREFIX}<hex> digest")
    return value


# --------------------------------------------------------------------------------------
# Admitted input.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapsuleRequirementBinding:
    """One owned requirement revision the admitted seat is accountable for."""

    stable_id: str
    revision: str

    def __post_init__(self) -> None:
        _require_non_blank(self.stable_id, "requirement stable_id")
        _require_non_blank(self.revision, "requirement revision")

    @property
    def identity(self) -> CapsuleRequirementRevision:
        return f"{self.stable_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class CapsuleRoleSeat:
    """An admitted role seat and the altitude it was admitted at."""

    role: CapsuleRole
    altitude: str

    def __post_init__(self) -> None:
        _require_non_blank(self.altitude, "seat altitude")

    @property
    def kind(self) -> CapsuleSeatKind:
        return "role"

    @property
    def block_identity(self) -> CapsuleBlockIdentity:
        return f"role:{self.role}"

    @property
    def key(self) -> str:
        return f"role:{self.role}@{self.altitude}"


@dataclass(frozen=True, slots=True)
class CapsuleLauncherSeat:
    """The ambient launcher routing condition: no role, no owned role block."""

    routing_condition: str

    def __post_init__(self) -> None:
        _require_non_blank(self.routing_condition, "launcher routing_condition")

    @property
    def kind(self) -> CapsuleSeatKind:
        return "launcher"

    @property
    def role(self) -> None:
        return None

    @property
    def block_identity(self) -> None:
        return None

    @property
    def key(self) -> str:
        return f"launcher:{self.routing_condition}"


CapsuleSeat = CapsuleRoleSeat | CapsuleLauncherSeat


@dataclass(frozen=True, slots=True)
class CapsuleToolPolicy:
    """The existing capability/permission snapshot compiled *against*, never widened.

    :attr:`granted` is what the existing AR permission policy already permits. A
    capsule's requested tool ids are a subset of it; a request outside it is a
    refusal, not a new grant.
    """

    granted: frozenset[CapsuleToolId]
    notes: str = ""

    def permits(self, tool_id: CapsuleToolId) -> bool:
        return tool_id in self.granted


@dataclass(frozen=True, slots=True)
class CapsuleAdmittedFacts:
    """The identity tuple an existing AR owner admits before compilation runs.

    Every field here is decided elsewhere: the task reference by the task
    document owner, the seat by dispatch identity, the work branch by the
    worktree owner, the requirement revisions by the task document, and the tool
    policy by the permission snapshot. The compiler reads them; it never derives
    one from another and never rewrites one.
    """

    task_reference: str
    task_document_digest: CapsuleDigest
    seat: CapsuleSeat
    repository_id: str
    work_branch: str
    requirements: tuple[CapsuleRequirementBinding, ...] = ()
    tool_policy: CapsuleToolPolicy = CapsuleToolPolicy(granted=frozenset())

    def __post_init__(self) -> None:
        _require_non_blank(self.task_reference, "task_reference")
        _require_digest(self.task_document_digest, "task_document_digest")
        _require_non_blank(self.repository_id, "repository_id")
        _require_non_blank(self.work_branch, "work_branch")

    @property
    def requirement_identities(self) -> tuple[CapsuleRequirementRevision, ...]:
        return tuple(binding.identity for binding in self.requirements)


@dataclass(frozen=True, slots=True)
class CapsuleSourceSelection:
    """The exact source set to load, as root-relative paths.

    The paths are supplied explicitly and are *validated* against the manifest,
    in both directions. Deriving them from the manifest instead would make the
    "a required block is missing" check vacuous: the compiler would only ever see
    the subset the manifest asked for.
    """

    root: str
    manifest: str
    core: tuple[str, ...] = ()
    role: tuple[str, ...] = ()
    operation: tuple[str, ...] = ()
    specialization: tuple[str, ...] = ()
    skill: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_blank(self.root, "source root")
        _require_non_blank(self.manifest, "manifest path")

    def paths(self) -> tuple[str, ...]:
        return self.core + self.role + self.operation + self.specialization + self.skill

    def for_root(self, composition_root: CapsuleCompositionRoot) -> tuple[str, ...]:
        return {
            "core": self.core,
            "role": self.role,
            "operation": self.operation,
            "specialization": self.specialization,
            "skill": self.skill,
        }[composition_root]


@dataclass(frozen=True, slots=True)
class CapsuleSourceAdmission:
    """One admitted request to compile: the binding together with its source selection.

    The two travel as one value because they are admitted together — a binding without
    its selection cannot be compiled and a selection without its binding has no scope —
    and because passing them separately made every call site a positional-argument
    matrix that no reader could check.
    """

    binding: CapsuleBinding
    selection: CapsuleSourceSelection


@dataclass(frozen=True, slots=True)
class CapsuleOverride:
    """One explicitly admitted supersession, carrying its provenance.

    The override replaces the identity wholesale. A caller declares it in the
    binding, so the supersession is an admitted decision with a reason, never a
    choice the compiler makes by filename accident.
    """

    superseded_identity: CapsuleBlockIdentity
    superseding_identity: CapsuleBlockIdentity
    authority: str
    rationale: str
    admitted_path: str = ""

    def __post_init__(self) -> None:
        for field in ("superseded_identity", "superseding_identity", "authority", "rationale"):
            _require_non_blank(getattr(self, field), field)
        if self.admitted_path:
            _require_non_blank(self.admitted_path, "override admitted_path")


@dataclass(frozen=True, slots=True)
class CapsuleBinding:
    """The admitted AR binding the compiler compiles *for*.

    The operation is explicit and separate from the seat: a launcher has no role
    but still runs an operation. A caller-held free-text prompt is never an input
    to this type, so it cannot become a role.
    """

    operation: CapsuleOperation
    admitted: CapsuleAdmittedFacts
    specializations: tuple[CapsuleBlockIdentity, ...] = ()
    overrides: tuple[CapsuleOverride, ...] = ()


# --------------------------------------------------------------------------------------
# The task-projection seam (filled by a later task-projection consumer).
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapsuleTaskContext:
    """One supplied task-context projection, as a separate channel from instructions."""

    markdown: str
    origin: str
    projection_revision: str
    content_digest: CapsuleDigest

    def __post_init__(self) -> None:
        _require_non_blank(self.origin, "task context origin")
        _require_non_blank(self.projection_revision, "task context projection_revision")
        _require_digest(self.content_digest, "task context content_digest")


@runtime_checkable
class CapsuleTaskProjectionSource(Protocol):
    """The typed input seam a later task projection (L3) satisfies.

    It is deliberately a one-method protocol over already-produced content: this
    leaf's compiler consumes a projection and never renders, loads or rewrites
    task state. An implementation returns ``None`` when it has no projection for
    the admitted binding; returning a partial projection is not an option, and a
    projection whose declared digest does not match its own bytes is refused by
    the compiler rather than injected.
    """

    def project(self, binding: CapsuleBinding) -> CapsuleTaskContext | None:  # pragma: no cover
        """Return the projection for ``binding``, or ``None`` when there is none."""


@dataclass(frozen=True, slots=True)
class CapsuleSuppliedProjection:
    """A projection supplied directly, for an admitted caller that already has it."""

    context: CapsuleTaskContext

    def project(self, binding: CapsuleBinding) -> CapsuleTaskContext | None:
        del binding
        return self.context


# --------------------------------------------------------------------------------------
# Content output.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapsuleInstructionBlock:
    """One trusted instruction block, in composition order.

    ``revision`` and ``content_digest`` are part of the block, not of the
    diagnostic manifest: two admissions over different source bytes must not
    share a capsule identity. ``authorities`` names every reason the block was
    selected, sorted, so the same block reached through two reasons is one block.
    """

    identity: CapsuleBlockIdentity
    composition_root: CapsuleCompositionRoot
    authorities: tuple[CapsuleSelectionReference, ...]
    source_path: str
    revision: CapsuleDigest
    content_digest: CapsuleDigest
    content: str

    def __post_init__(self) -> None:
        _require_non_blank(self.identity, "block identity")
        _require_non_blank(self.source_path, "block source_path")
        _require_digest(self.revision, "block revision")
        _require_digest(self.content_digest, "block content_digest")

    def render(self) -> str:
        return self.content


@dataclass(frozen=True, slots=True)
class CapsuleInstructionUnit:
    """A single-unit instruction carrier: the ordered element of a capsule.

    One unit per block, in composition order, so a capsule's instruction payload is
    exactly ``tuple(unit.block.content for unit in units)`` — nothing is
    re-rendered, re-wrapped or re-sorted on the way out.
    """

    unit_index: int
    block: CapsuleInstructionBlock

    def __post_init__(self) -> None:
        if self.unit_index < 0:
            raise ValueError("instruction unit index must not be negative")

    def render(self) -> str:
        return self.block.content


@dataclass(frozen=True, slots=True)
class CapsuleSkillReference:
    """One optional skill reference with its origin.

    The origin is part of the reference because identity is
    ``server-origin + skill URI``: a bare name would collide across servers, and a
    skill reference is a pointer to separately delivered content, never an
    activation and never a permission grant.
    """

    identity: CapsuleSkillIdentity
    origin: str
    uri: str
    revision: CapsuleDigest

    def __post_init__(self) -> None:
        for field in ("identity", "origin", "uri"):
            _require_non_blank(getattr(self, field), field)
        _require_digest(self.revision, "skill reference revision")


@dataclass(frozen=True, slots=True)
class CapsuleToolRequest:
    """A requested tool identity, inside the admitted permission policy.

    This is a request. It is not a grant, it is not evidence of one, and the
    compiler never adds a tool the permission snapshot did not already permit.
    """

    tool_id: CapsuleToolId
    authority: str

    def __post_init__(self) -> None:
        _require_non_blank(self.tool_id, "tool id")
        _require_non_blank(self.authority, "tool request authority")


@dataclass(frozen=True, slots=True)
class CapsuleCapsule:
    """The compiled content, in one deterministic ordered value."""

    instruction_units: tuple[CapsuleInstructionUnit, ...]
    task_context: CapsuleTaskContext | None
    skill_references: tuple[CapsuleSkillReference, ...]
    requested_tools: tuple[CapsuleToolRequest, ...]
    semantic_digest: CapsuleDigest
    binding: CapsuleBinding

    @property
    def instructions(self) -> tuple[CapsuleInstructionBlock, ...]:
        return tuple(unit.block for unit in self.instruction_units)


@dataclass(frozen=True, slots=True)
class CapsuleCompilationResult:
    """The one return shape: the capsule, its diagnostic manifest, and the digest."""

    capsule: CapsuleCapsule
    manifest: CapsuleManifest

    @property
    def semantic_digest(self) -> CapsuleDigest:
        return self.capsule.semantic_digest

    def render_instructions(self) -> str:
        """The ordered instruction content, exactly as the capsule carries it.

        This is a byte-for-byte concatenation of the selected block content in
        composition order. It re-renders nothing, so a consumer comparing two
        compilations compares the real payload.
        """

        return "".join(block.render() for block in self.capsule.instructions)

    def render_task_context(self) -> str:
        """The sourced task-context Markdown, or ``""`` when none was supplied."""

        context = self.capsule.task_context
        return "" if context is None else context.markdown


# ``CapsuleCompilationResult.manifest`` names the diagnostic projection, which lives
# in :mod:`agents_remember.models.role_capsules.diagnostics` and is re-exported by this
# package. It is a forward reference here because the diagnostic half imports this
# module's value types, and one direction of that pair has to be the annotation-only
# one.
__all__ = [
    "CAPSULE_COMPOSITION_ORDER",
    "CONFLICT_DUPLICATE_IDENTITY",
    "CONFLICT_EQUAL_AUTHORITY",
    "SELECTION_EXPLICIT_SUPERSESSION",
    "SELECTION_OPERATION_BLOCK",
    "SELECTION_REPOSITORY_SPECIALIZATION",
    "SELECTION_ROLE_BLOCK",
    "SELECTION_SHARED_CORE",
    "SHA256_PREFIX",
    "SUPERSEDE_DUPLICATE_IDENTITY",
    "SUPERSEDE_EXPLICIT",
    "CapsuleAdmittedFacts",
    "CapsuleBinding",
    "CapsuleBlockIdentity",
    "CapsuleCapsule",
    "CapsuleCompilationResult",
    "CapsuleDigest",
    "CapsuleInstructionBlock",
    "CapsuleInstructionUnit",
    "CapsuleLauncherSeat",
    "CapsuleOperation",
    "CapsuleOverride",
    "CapsuleRequirementBinding",
    "CapsuleRequirementRevision",
    "CapsuleRole",
    "CapsuleRoleSeat",
    "CapsuleSeat",
    "CapsuleSeatKind",
    "CapsuleSelectionReference",
    "CapsuleSkillIdentity",
    "CapsuleSkillReference",
    "CapsuleSourceAdmission",
    "CapsuleSourceSelection",
    "CapsuleSuppliedProjection",
    "CapsuleTaskContext",
    "CapsuleTaskProjectionSource",
    "CapsuleToolId",
    "CapsuleToolPolicy",
    "CapsuleToolRequest",
    "compute_content_digest",
    "compute_semantic_digest",
]
