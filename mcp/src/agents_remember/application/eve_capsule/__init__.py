"""Materialize the admitted capsule into the carrier one bound eve runtime reads.

This is the seam's produce side. It takes the addressing the rest of the coordination surface
already accepts — an enclosure contract, the task document under it, a role and an operation —
compiles the capsule through the one compiler and the one projection that serve every harness, and
writes the carrier whose bytes the runtime applies before its first model call.

Three rules shape everything here:

* **Admission precedes execution.** Nothing in this module guesses a role from prompt text, invents
  a workspace, or falls back to an unbound launch. A refusal from the compiler or the projection is
  returned as that refusal and no carrier is written, so a runtime that cannot be bound correctly is
  never given something to run.
* **One compiler.** The instruction text is L2's own block content in L2's composition order; the
  task facts are L3's rendered markdown taken verbatim. This module orders nothing and selects
  nothing: it transports a decided value.
* **The admitted worktree, not a re-derived path.** The workspace root and the git identity the
  runtime verifies both come out of L3's projection of the enclosure contract, resolved through the
  documented consumer contract in :mod:`agents_remember.application.task_projection`. A caller can
  point this at a task, not at a directory.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.skill_resources import (
    CapsuleCompileOutcome,
    CapsuleCompileRequest,
    CapsuleSourceSelectionRequest,
    compile_task_capsule,
)
from agents_remember.application.task_projection import (
    ProjectionScopeRequest,
    TaskProjectionRequest,
    project_task_context,
    resolve_task_projection_scope,
    task_context_of,
)
from agents_remember.application.task_projection.types import TaskProjection, WorktreeBinding
from agents_remember.errors import HarnessControlError, TaskProjectionSourceError
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.eve_capsule_carrier import (
    BINDING_REF_ENV,
    CAPSULE_DIGEST_ENV,
    CAPSULE_PATH_ENV,
    EVE_CAPSULE_CARRIER_SCHEMA,
    WORKSPACE_ROOT_ENV,
    WORKSPACE_SCOPE_KIND,
    EveCapsuleCarrier,
    EveCapsuleIdentity,
    EveCapsuleWorkspace,
    EveCapsuleWriteScope,
    carrier_digest,
    instruction_digest,
)
from agents_remember.models.role_capsules import CapsuleCompilationResult

CARRIER_FILENAME = "capsule-carrier.json"
"""The carrier's name inside the directory the launch owner admits for it."""

CARRIER_DIRECTORY = "capsule"
"""Where the carrier lives relative to an epoch's private runtime directory.

Deliberately outside the admitted workspace: the runtime's own file tools are confined to the
workspace root, so the instructions it applies are not a file the model can rewrite.
"""

ABSOLUTE_SURFACE_KIND = "absolute"
"""The scope kind for a surface named by its own path rather than by the workspace."""

WORKSPACE_SCOPE_PATH = "."
"""The workspace scope's path: the admitted code worktree itself, as the runtime resolves it."""

WORKER_WRITE_SURFACES: frozenset[str] = frozenset({"workspace", "report"})
"""What a worker seat may write: the admitted code worktree and its own report surface."""

CURATOR_WRITE_SURFACES: frozenset[str] = frozenset({"workspace", "report", "memory"})
"""What a curator seat may write: the worker's surfaces plus the curation memory surface."""

ROLE_WRITE_SURFACES: Mapping[str, frozenset[str]] = {
    "worker": WORKER_WRITE_SURFACES,
    "curator": CURATOR_WRITE_SURFACES,
}
"""Role to admitted surfaces. A role absent from this table gets the workspace and its report only.

Fail-closed by construction: the fallback is the *smallest* set, so a role whose scope nobody
declared cannot inherit the curator's memory write by accident.
"""


@dataclass(frozen=True, slots=True)
class EveBindingRequest:
    """One seat's addressing, as the spawning owner already knows it.

    ``carrier_directory`` is the launch owner's admitted location for the carrier — an epoch-private
    runtime directory outside the workspace. This module writes there and nowhere else. The three
    surface roots are the seats' admitted write surfaces; none of them is derived here.
    """

    enclosure: EnclosureSelector
    task_path: str
    role: str
    operation: str
    carrier_directory: Path
    code_repository_root: Path | None = None
    report_root: Path | None = None
    memory_root: Path | None = None
    sources: CapsuleSourceSelectionRequest | None = None


@dataclass(frozen=True, slots=True)
class EveCarrierSeat:
    """The seat one carrier is written for, with the surfaces that seat was admitted.

    Assembled once, by :func:`carrier_seat`, so the role, the operation, the binding reference and
    the admitted write surfaces cannot reach the writer as four unrelated arguments that a caller
    could supply in part.
    """

    role: str
    operation: str
    binding_ref: str
    report_root: Path | None = None
    memory_root: Path | None = None


@dataclass(frozen=True, slots=True)
class EveBoundLaunch:
    """A carrier on disk plus the environment that names it.

    ``env`` is exactly what a launch must carry; it is a separate return value rather than a side
    effect so a caller cannot start a runtime with a partly-applied binding.
    """

    carrier: EveCapsuleCarrier
    carrier_path: Path
    digest: str
    env: Mapping[str, str]
    compilation: CapsuleCompileOutcome


def materialize_eve_binding(config: McpRuntimeConfig, request: EveBindingRequest) -> EveBoundLaunch:
    """Compile the admitted capsule and write the carrier for one bound eve launch.

    Raises :class:`HarnessControlError` naming the exact refusal when the capsule cannot be compiled
    or an admitted surface is missing, because a caller that receives no bound launch must not be
    able to start one anyway.
    """

    outcome = compile_task_capsule(
        config,
        CapsuleCompileRequest(
            enclosure=request.enclosure,
            task_path=request.task_path,
            operation=request.operation,  # type: ignore[arg-type]
            role=request.role,
            code_repository_root=request.code_repository_root,
        ),
        sources=request.sources,
    )
    if not outcome.ok:
        raise HarnessControlError(_refusal_detail(outcome))
    result = outcome.result
    if result is None:
        raise HarnessControlError(
            "the capsule compiler reported success without a capsule; refusing to write a carrier "
            "from an incomplete result"
        )
    projection = _admitted_projection(config, request, result)
    carrier = build_carrier(
        result=result,
        projection=projection,
        seat=carrier_seat(
            role=request.role,
            task_path=request.task_path,
            operation=request.operation,
            report_root=request.report_root,
            memory_root=request.memory_root,
        ),
    )
    carrier_path = request.carrier_directory / CARRIER_DIRECTORY / CARRIER_FILENAME
    carrier_path.parent.mkdir(parents=True, exist_ok=True)
    payload = carrier.to_bytes()
    carrier_path.write_bytes(payload)
    digest = carrier_digest(payload)
    # Read back what was written and require it to parse equal: a carrier that cannot be read is a
    # runtime that cannot be bound, and finding that out here is free.
    if EveCapsuleCarrier.from_bytes(carrier_path.read_bytes()) != carrier:
        raise HarnessControlError(f"the carrier written to {carrier_path} did not read back")
    return EveBoundLaunch(
        carrier=carrier,
        carrier_path=carrier_path,
        digest=digest,
        env=carrier_env(
            binding_ref=carrier.identity.binding_ref,
            capsule_path=carrier_path,
            capsule_digest=digest,
            workspace_root=Path(carrier.workspace.root),
        ),
        compilation=outcome,
    )


def carrier_seat(
    *,
    role: str,
    task_path: str,
    operation: str,
    report_root: Path | None = None,
    memory_root: Path | None = None,
) -> EveCarrierSeat:
    """One seat's addressing, with the canonical binding reference built from it."""

    return EveCarrierSeat(
        role=role,
        operation=operation,
        binding_ref=binding_ref_for(role=role, task_path=task_path, operation=operation),
        report_root=report_root,
        memory_root=memory_root,
    )


def _admitted_projection(
    config: McpRuntimeConfig, request: EveBindingRequest, result: CapsuleCompilationResult
) -> TaskProjection:
    """The task projection for the binding the compiler admitted, re-derived once and compared.

    The compile outcome reports the capsule it produced but not the projection it read, so this
    resolves the same scope through L3's documented consumer contract — the same selector, the same
    coordination root, and the binding the compile itself admitted — and then requires its task
    context to be byte-identical to the one the capsule carries. The comparison is what makes this a
    check rather than a second opinion: a projection that disagreed with the compiled capsule is
    refused instead of silently becoming the carrier's task facts.
    """

    binding = result.capsule.binding
    try:
        scope = resolve_task_projection_scope(
            binding,
            coordination_root=config.coordination_root,
            scope_request=ProjectionScopeRequest(
                selector=request.enclosure,
                workspace_root=config.workspace_root,
                code_repository_root=request.code_repository_root,
            ),
        )
        projection = project_task_context(scope, binding, TaskProjectionRequest())
    except TaskProjectionSourceError as error:
        raise HarnessControlError(
            f"the admitted task projection could not be resolved for binding "
            f"{binding.admitted.task_reference!r}: {error.render()}"
        ) from error
    _require_matching_task_context(
        capsule_context=result.capsule.task_context, projected=task_context_of(projection)
    )
    return projection


def _require_matching_task_context(*, capsule_context: object, projected: object) -> None:
    """The capsule's task context and the re-derived projection must be the same bytes.

    Compared by content digest, which each side computes over its own markdown, so no comparison
    here derives both sides from one artifact.
    """

    compiled_digest = getattr(capsule_context, "content_digest", "")
    projected_digest = getattr(projected, "content_digest", "")
    if compiled_digest != projected_digest:
        raise HarnessControlError(
            "the compiled capsule's task context "
            f"({compiled_digest or 'none'}) is not the admitted projection's "
            f"({projected_digest or 'none'}); refusing to write a carrier whose task facts are not "
            "the ones the capsule was compiled from"
        )


def build_carrier(
    *,
    result: CapsuleCompilationResult,
    projection: TaskProjection,
    seat: EveCarrierSeat,
) -> EveCapsuleCarrier:
    """Project one compilation result and its admitted projection into the carrier value.

    Nothing is re-rendered: each instruction block's own content is carried, in the composition
    order the compiler produced, and L3's task-context markdown is carried whole.
    """

    capsule = result.capsule
    task_context = capsule.task_context
    workspace = _workspace_of(projection.worktree)
    return EveCapsuleCarrier(
        schema=EVE_CAPSULE_CARRIER_SCHEMA,
        identity=EveCapsuleIdentity(
            role=seat.role,
            task_reference=capsule.binding.admitted.task_reference,
            operation=seat.operation,
            binding_ref=seat.binding_ref,
            semantic_digest=capsule.semantic_digest,
        ),
        workspace=workspace,
        instructions=tuple(block.content for block in capsule.instructions),
        instruction_identities=tuple(block.identity for block in capsule.instructions),
        instruction_digests=tuple(
            instruction_digest(block.content) for block in capsule.instructions
        ),
        task_context_markdown="" if task_context is None else task_context.markdown,
        task_context_digest="" if task_context is None else task_context.content_digest,
        write_scopes=write_scopes_for(
            seat.role,
            workspace_root=Path(workspace.root),
            report_root=seat.report_root,
            memory_root=seat.memory_root,
        ),
        granted_tools=tuple(sorted(capsule.binding.admitted.tool_policy.granted)),
        carry_forward=tuple(
            f"{unit.block.identity}@{unit.block.revision}" for unit in capsule.instruction_units
        ),
    )


def write_scopes_for(
    role: str,
    *,
    workspace_root: Path,
    report_root: Path | None,
    memory_root: Path | None,
) -> tuple[EveCapsuleWriteScope, ...]:
    """The surfaces one role is admitted, as explicit scopes the runtime can enforce.

    A worker gets the workspace and its report surface. A curator additionally gets the memory
    surface. Any other role gets the workspace and its report: the smallest set, so an undeclared
    role cannot inherit a wider one.

    A surface the role's table names but nobody admitted is a refusal, not a silent narrowing: a
    carrier whose declared surfaces disagreed with the role authority table would be a runtime
    running under a policy that does not exist.
    """

    surfaces = ROLE_WRITE_SURFACES.get(role, WORKER_WRITE_SURFACES)
    scopes = [
        EveCapsuleWriteScope(
            kind=WORKSPACE_SCOPE_KIND,
            path=WORKSPACE_SCOPE_PATH,
            root=str(_admitted_directory(workspace_root, "workspace")),
        )
    ]
    for surface, root in (("report", report_root), ("memory", memory_root)):
        if surface not in surfaces:
            continue
        if root is None:
            raise HarnessControlError(
                f"role {role!r} is admitted to write the {surface} surface but no {surface} root was "
                "admitted; refusing to write a carrier that quietly grants less than the role's table"
            )
        admitted = str(_admitted_directory(root, surface))
        scopes.append(
            EveCapsuleWriteScope(kind=ABSOLUTE_SURFACE_KIND, path=admitted, root=admitted)
        )
    return tuple(scopes)


def carrier_env(
    *,
    binding_ref: str,
    capsule_path: Path,
    capsule_digest: str,
    workspace_root: Path,
) -> dict[str, str]:
    """The launch environment that names one bound carrier.

    The variable names come from the carrier module, which is also where the reader's expectations
    are declared, so the two halves of the binding cannot drift into two spellings.
    """

    return {
        BINDING_REF_ENV: binding_ref,
        CAPSULE_PATH_ENV: str(capsule_path),
        CAPSULE_DIGEST_ENV: capsule_digest,
        WORKSPACE_ROOT_ENV: str(workspace_root),
    }


def binding_ref_for(*, role: str, task_path: str, operation: str) -> str:
    """The canonical binding reference for one seat.

    Content-free by design: it names *which* seat this is, so a carrier written for another binding
    is refused by name, and it is built only from values the task layer admitted.
    """

    for label, value in (("role", role), ("task", task_path), ("operation", operation)):
        if not value or value != value.strip() or ":" in value:
            raise HarnessControlError(
                f"a binding reference cannot be built from {label} {value!r}: it must be non-empty, "
                "trimmed, and free of the ':' separator the reference uses"
            )
    return f"ar-binding:{role}:{task_path}:{operation}"


def capsule_carrier_digest_of(path: Path) -> str:
    """Recompute one carrier file's content address, for a caller that must compare it."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise HarnessControlError(f"the eve capsule carrier at {path} is unreadable") from exc
    return carrier_digest(payload)


def digest_of_file(path: Path) -> str:
    """The content address of a file's bytes, used to report what a comparison was made against."""

    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _workspace_of(worktree: WorktreeBinding) -> EveCapsuleWorkspace:
    """The admitted worktree as the runtime receives it: root plus verifiable git identity.

    The branch and the base commit are the worktree owner's facts, read out of the projection rather
    than probed here, so the runtime compares what it finds on disk against what the contract
    admitted instead of against something this module worked out.
    """

    root = _admitted_directory(Path(worktree.code_worktree), "code worktree")
    if not worktree.repository_id or not worktree.work_branch:
        raise HarnessControlError(
            "the admitted projection carried no repository or work branch; refusing to bind a launch "
            "to a worktree whose git identity nobody admitted"
        )
    return EveCapsuleWorkspace(
        root=str(root),
        repository_id=worktree.repository_id,
        work_branch=worktree.work_branch,
        base_commit=worktree.base_commit,
        contract_path=worktree.contract_path,
    )


def _admitted_directory(path: Path, label: str) -> Path:
    """One admitted surface root, proven to be an absolute existing directory.

    Fail-closed: a relative path would resolve against whatever the runtime's working directory
    happened to be, and a path that does not exist cannot be a surface anyone admitted.
    """

    if not str(path) or not path.is_absolute():
        raise HarnessControlError(
            f"the admitted {label} root {str(path)!r} is not an absolute path; refusing to declare a "
            "write surface whose location depends on the runtime's working directory"
        )
    if not path.is_dir():
        raise HarnessControlError(
            f"the admitted {label} root {path} is not an existing directory; refusing to bind a "
            "launch to a surface that is not there"
        )
    return path


def _refusal_detail(outcome: CapsuleCompileOutcome) -> str:
    refusal = outcome.refusal
    if refusal is None:
        return "the capsule compiler refused without an explanation"
    return outcome.explanation()


__all__ = [
    "ABSOLUTE_SURFACE_KIND",
    "CARRIER_DIRECTORY",
    "CARRIER_FILENAME",
    "CURATOR_WRITE_SURFACES",
    "ROLE_WRITE_SURFACES",
    "WORKER_WRITE_SURFACES",
    "WORKSPACE_SCOPE_PATH",
    "EveBindingRequest",
    "EveBoundLaunch",
    "EveCarrierSeat",
    "binding_ref_for",
    "build_carrier",
    "capsule_carrier_digest_of",
    "carrier_env",
    "carrier_seat",
    "digest_of_file",
    "materialize_eve_binding",
    "write_scopes_for",
]
