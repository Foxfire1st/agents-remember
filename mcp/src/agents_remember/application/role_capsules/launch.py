"""Compile the capsule one launch must supply, through the compiler every seat already uses.

This is the module the severed chain was missing. Until it existed, the compiler (L2), the admission
and MCP surface (L4), the Codex carrier (L5) and the eve carrier (L7) were each individually proven
and no production launch point supplied a capsule to anything.

**One compiler, one routing rule.** Nothing here re-implements selection. A task-attached seat goes
through :func:`~agents_remember.application.skill_resources.compile_task_capsule` — the same
operation the registered MCP tool answers — and a seat with no task document goes through
:func:`~agents_remember.application.role_capsules.compilation.compile_admitted_capsule` with the
routed source set built by the same manifest rule
(:func:`~agents_remember.application.skill_resources.routed_admission_for`). The operation is
``orientation``, the default the registered compile operation already declares.

**What a task-attached seat's admission is.** The task document the launch already holds is resolved
by the task layer, and the enclosure that admits it is resolved through the control plane's own
worktree-contract reader — a leaf document by its leaf id, any other document by its task's own
series contract. The repository root comes from the MCP configuration's registered repository for
the repository the document itself declares; nothing is taken from the caller's string.

**What a free agent's admission is, and the one convention this module had to define.** A free agent
(a role, no task document — ``bootstrap`` is admitted to that class by ``TASKLESS_SEAT_ROLES``) has
no task plane at all: no document to digest, no revision to pin, no projection to carry. The frozen
:class:`~agents_remember.models.role_capsules.types.CapsuleAdmittedFacts` has no typed absence for
it (all four identity fields are non-blank strings), so the absence is minted **once, here**, by
:class:`FreeAgentSeatAdmission`, whose ``task_reference`` is a canonical, self-describing value that
deliberately does not parse as a task reference and whose digest covers the seat admission the
launch actually performed. Nothing else in the tree can produce that value.

**The carriers are the ones the master already built.** Codex's is
:func:`~agents_remember.serving.capsule_delivery.capsule_delivery_from` over the compilation result;
eve's is the carrier L7 materializes plus the two environment references its loader reads. A launch
sets the carrier for the harness it is about to start; the other stays empty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.eve_capsule import (
    EveBindingRequest,
    materialize_eve_binding,
)
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources import (
    CapsuleCompileOutcome,
    CapsuleCompileRequest,
    CapsuleSeatAddress,
    admitted_tool_policy,
    compile_task_capsule,
    routed_admission_for,
    shipped_composition_corpus,
)
from agents_remember.errors import CapsuleCompilationError, HarnessControlError
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.git_facts import read_git_facts
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCompilationResult,
    CapsuleDigest,
    CapsuleRoleSeat,
    compute_content_digest,
)
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.capsule_delivery import capsule_delivery_from
from agents_remember.serving.launch_capsule import (
    LAUNCH_OPERATION,
    LaunchCapsule,
    LaunchCapsuleMode,
    LaunchCapsuleRequest,
    refused_launch_capsule,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

FREE_AGENT_TASK_REFERENCE_PREFIX = "free-agent"
"""How a taskless seat's absent task plane is spelled, once.

The value is ``free-agent:<role>``. It is not a task document path and cannot be mistaken for one:
the task layer's own parser requires ``<repository>/<path>.json``, so this value is refused by
:func:`~agents_remember.application.task_projection.parse_task_reference` rather than silently
resolved to a document that does not exist.
"""

UNVERSIONED_WORK_BRANCH = "<unversioned>"
"""The work branch of a taskless seat whose workspace is not a git work tree.

A free agent is not admitted onto a branch the way a worktree-bound seat is, and saying so is
better than refusing to start the one seat whose job is to set a repository up.
"""

EVE_CARRIER_ROOT = ("runtime", "eve-carriers")
"""Where a launch's eve carrier is written, under the coordination root.

Outside every workspace by construction, which is what L7's carrier requires: the runtime's own
file tools are confined to the workspace, so the instructions it applies are not a file the model
can rewrite.
"""


@dataclass(frozen=True, slots=True)
class AdmittedTaskSeat:
    """One task-attached seat, resolved once from the launch's own admitted document.

    The five facts every carrier needs — the role, the document reference, the enclosure selector,
    the repository root and the document's own repository name — are resolved together, because
    resolving them apart is how two carriers end up binding to two different enclosures.
    """

    role: str
    reference: TaskDocumentRef
    selector: EnclosureSelector
    contract_path: Path
    repository_root: Path
    document_repository: str


@dataclass(frozen=True, slots=True)
class FreeAgentSeatAdmission:
    """The typed admission of a seat that has no task document.

    This is the *only* producer of a taskless
    :class:`~agents_remember.models.role_capsules.types.CapsuleAdmittedFacts`, and it carries the
    facts that genuinely exist for such a seat: the corpus's own declared altitude for the role, the
    workspace the session will run in, and the branch that workspace is on. What it does **not**
    carry is a task document, because there is none — :attr:`task_reference` says so by name, and
    the digest covers this admission rather than a document that does not exist.
    """

    role: str
    operation: str
    altitude: str
    repository_id: str
    work_branch: str

    def __post_init__(self) -> None:
        for label, value in (
            ("role", self.role),
            ("operation", self.operation),
            ("altitude", self.altitude),
            ("repository_id", self.repository_id),
            ("work_branch", self.work_branch),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a free-agent seat admission requires a non-blank {label}")

    @property
    def task_reference(self) -> str:
        """The named absence the frozen DTO's task field carries for this seat."""

        return f"{FREE_AGENT_TASK_REFERENCE_PREFIX}:{self.role}"

    @property
    def is_taskless(self) -> bool:
        return True

    def digest(self) -> CapsuleDigest:
        """The admission's own content address: what was admitted, and nothing else.

        Canonical JSON over exactly the fields above, so the same seat in the same workspace
        compiles to the same capsule identity and a different seat or workspace does not.
        """

        canonical = json.dumps(
            {
                "schema": "ar-free-agent-seat-admission/v1",
                "role": self.role,
                "operation": self.operation,
                "altitude": self.altitude,
                "repositoryId": self.repository_id,
                "workBranch": self.work_branch,
                "taskDocument": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return compute_content_digest(canonical)

    def admitted_facts(self, *, tool_policy: object) -> CapsuleAdmittedFacts:
        """The frozen admitted facts for this seat: the seat, and no task document."""

        return CapsuleAdmittedFacts(
            task_reference=self.task_reference,
            task_document_digest=self.digest(),
            seat=CapsuleRoleSeat(role=self.role, altitude=self.altitude),  # type: ignore[arg-type]
            repository_id=self.repository_id,
            work_branch=self.work_branch,
            tool_policy=tool_policy,  # type: ignore[arg-type]
        )


def free_agent_seat_admission(
    config: McpRuntimeConfig,
    *,
    role: str,
    operation: str = LAUNCH_OPERATION,
    workspace_root: Path,
) -> FreeAgentSeatAdmission:
    """Admit one taskless seat from the authorities that exist for it.

    Two of the three facts come from owners: the altitude is the corpus's own declaration for the
    role, read from the composition manifest the compiler will read again, and the workspace
    identity is the workspace's own Git facts. The third — that there is no task document — is
    stated rather than invented.
    """

    with shipped_composition_corpus() as (root, manifest):
        parsed = parse_composition_manifest((root / manifest).read_bytes())
    entry = parsed.roles.get(role)
    if entry is None:
        raise CapsuleCompilationError(
            status="unknown-role",
            detail=f"the composition manifest declares no role {role!r}",
            next_action="name a role the canonical corpus declares",
        )
    repository_id, work_branch = workspace_identity(config, workspace_root)
    return FreeAgentSeatAdmission(
        role=role,
        operation=operation,
        altitude=entry.altitude,
        repository_id=repository_id,
        work_branch=work_branch,
    )


def workspace_identity(config: McpRuntimeConfig, workspace_root: Path) -> tuple[str, str]:
    """The repository identity a taskless seat is admitted under, from its own workspace.

    When the workspace lies inside a repository this server has registered, that registration is the
    identity — the same authority ``_registered_repository_root`` reads for a task-attached seat.
    Otherwise the workspace directory's own name is used, because it is the only true name a seat
    started outside every registered repository has. The branch is the workspace's own Git branch.
    """

    root = workspace_root.resolve()
    repository_id = _registered_repository_for(config, root)
    facts = read_git_facts(repository_id or root.name or "workspace", root)
    branch = facts.branch if facts.state == "available" and facts.branch else ""
    return repository_id or root.name or str(root), branch or UNVERSIONED_WORK_BRANCH


def _registered_repository_for(config: McpRuntimeConfig, workspace_root: Path) -> str | None:
    """The registered repository containing ``workspace_root``, or ``None``.

    The deepest containing root wins, so a repository nested inside another's directory is still
    itself.
    """

    containing = [
        (len(scope.path.parts), scope.repo_id)
        for scope in config.repositories.values()
        if _contains(scope.path, workspace_root)
    ]
    if not containing:
        return None
    return max(containing)[1]


def _contains(parent: Path, child: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except OSError:  # pragma: no cover - an unresolvable path is simply not contained
        return False


def compile_launch_capsule(
    config: McpRuntimeConfig, request: LaunchCapsuleRequest
) -> LaunchCapsule:
    """Compile the capsule one launch will supply, or refuse it by name.

    Called by the launch points through the serving tier's :data:`LaunchCapsuleResolver` port, so a
    serving-rank route can decide and record its instruction mode without importing this tier.
    """

    role = (request.role or "").strip()
    if role not in CAPSULE_ROLES:
        return refused_launch_capsule(
            request.role,
            "role-not-capsule-addressable",
            (
                f"role {request.role!r} is not one of the frozen capsule roles "
                f"({', '.join(CAPSULE_ROLES)}), so no capsule can be compiled for it"
            ),
        )
    if request.task_document_ref is None:
        return _compile_free_agent(config, request, role)
    return _compile_admitted_task(config, request, role)


def _compile_admitted_task(
    config: McpRuntimeConfig, request: LaunchCapsuleRequest, role: str
) -> LaunchCapsule:
    """The task-attached path: the launch's own admitted document, compiled and carried."""

    ref = request.task_document_ref
    assert ref is not None  # the caller branched on it
    topology = TaskDocumentTopology(config.coordination_root)
    try:
        resolved = topology.resolve(ref)
    except TaskDocumentRefError as error:
        return refused_launch_capsule(role, "task-binding-unresolved", str(error))
    document = resolved.document
    repository_root = _registered_repository_root(config, document.repo)
    if repository_root is None:
        return refused_launch_capsule(
            role,
            "repository-not-registered",
            (
                f"the admitted task document {ref.key} declares repository {document.repo!r}, which "
                "this server's configuration does not register; the capsule's admitted repository "
                "and work branch cannot be resolved"
            ),
        )
    leaf_id = document.id if document.kind == "subTask" else None
    contract_path = _enclosure_contract(config, document.repo, ref.path, leaf_id)
    if contract_path is None:
        return refused_launch_capsule(
            role,
            "enclosure-not-found",
            (
                f"the admitted task document {ref.key} has no worktree enclosure under "
                f"{_task_root_of(ref.path)!r}; the capsule's admitted work branch cannot be resolved. "
                "Run worktree_start for this leaf (or admit the task's own series contract) and "
                "dispatch again"
            ),
        )
    selector = EnclosureSelector(contract_path=contract_path)
    seat = AdmittedTaskSeat(
        role=role,
        reference=ref,
        selector=selector,
        contract_path=contract_path,
        repository_root=repository_root,
        document_repository=document.repo,
    )
    if (request.harness or "").strip() == "eve":
        return _compile_eve_task(config, seat)
    outcome = compile_task_capsule(
        config,
        CapsuleCompileRequest(
            enclosure=selector,
            task_path=ref.path,
            operation=LAUNCH_OPERATION,
            role=role,
            code_repository_root=repository_root,
        ),
    )
    if not outcome.ok:
        return _refusal(role, outcome)
    result = outcome.result
    assert result is not None  # outcome.ok
    return _capsule_launch(role, result)


def _compile_eve_task(config: McpRuntimeConfig, seat: AdmittedTaskSeat) -> LaunchCapsule:
    """The eve carrier: the launch environment that names the compiled capsule.

    The carrier is materialized by L7's produce side — the same materializer its runtime verifies —
    so this path compiles nothing itself and adds no second carrier format.
    """

    report_root, memory_root = _admitted_surfaces(seat.contract_path)
    try:
        bound = materialize_eve_binding(
            config,
            EveBindingRequest(
                enclosure=seat.selector,
                task_path=seat.reference.path,
                role=seat.role,
                operation=LAUNCH_OPERATION,
                carrier_directory=_carrier_directory(config, seat.role, seat.reference.path),
                code_repository_root=seat.repository_root,
                report_root=report_root,
                memory_root=memory_root,
            ),
        )
    except HarnessControlError as error:
        return refused_launch_capsule(seat.role, "eve-carrier-refused", str(error))
    result = bound.compilation.result
    if (
        result is None
    ):  # pragma: no cover - materialize_eve_binding refuses rather than returning this
        return refused_launch_capsule(
            seat.role,
            "capsule-compilation-incomplete",
            "the eve carrier was built from no capsule",
        )
    return _capsule_launch(
        seat.role,
        result,
        eve_env=dict(bound.env),
        carrier=bound.carrier_path,
        # Read back out of the carrier the runtime will re-verify: the launch runs in the workspace
        # the artifact itself admits, so the session's cwd and the carrier's AR_WORKSPACE_ROOT are
        # one value rather than two that have to agree.
        session_workspace=Path(bound.carrier.workspace.root),
    )


def _compile_free_agent(
    config: McpRuntimeConfig, request: LaunchCapsuleRequest, role: str
) -> LaunchCapsule:
    """The free-agent path: a seat with no task document still receives its compiled capsule."""

    if (request.harness or "").strip() == "eve":
        return refused_launch_capsule(
            role,
            "eve-carrier-requires-admitted-worktree",
            (
                "the eve carrier binds a runtime to the admitted git worktree of a task enclosure, "
                "and a taskless seat has none; an eve free agent therefore has no verified "
                "instruction channel. Owner: the final verification leaf, with the eve carrier "
                "producer (L7)"
            ),
        )
    admission = free_agent_seat_admission(config, role=role, workspace_root=request.workspace_root)
    policy = admitted_tool_policy(config)
    binding = CapsuleBinding(
        operation=LAUNCH_OPERATION,  # type: ignore[arg-type]
        admitted=admission.admitted_facts(tool_policy=policy),
    )
    with shipped_composition_corpus() as (root, manifest):
        manifest_bytes = (root / manifest).read_bytes()
        try:
            routed = routed_admission_for(
                root,
                manifest,
                manifest_bytes,
                CapsuleSeatAddress(role=role, operation=LAUNCH_OPERATION),
            )
        except CapsuleCompilationError as error:
            return refused_launch_capsule(role, error.status, error.render())
        outcome = compile_admitted_capsule(binding, routed)
    if outcome.result is None:
        return refused_launch_capsule(
            role,
            getattr(outcome.error, "status", "capsule-compilation-refused"),
            outcome.render_explanation(),
        )
    return _capsule_launch(role, outcome.result)


def _capsule_launch(
    role: str,
    result: CapsuleCompilationResult,
    *,
    eve_env: dict[str, str] | None = None,
    carrier: Path | None = None,
    session_workspace: Path | None = None,
) -> LaunchCapsule:
    """The delivered value: the carrier for this launch plus its per-run record."""

    capsule = result.capsule
    rendered = result.render_instructions()
    report: dict[str, object] = {
        "mode": LaunchCapsuleMode.CAPSULE.value,
        "role": role,
        "operation": capsule.binding.operation,
        "semanticDigest": capsule.semantic_digest,
        "taskReference": capsule.binding.admitted.task_reference,
        "instructionCount": len(capsule.instruction_units),
        "instructionBytes": len(rendered.encode("utf-8")),
    }
    if carrier is not None:
        report["eveCarrier"] = str(carrier)
    if session_workspace is not None:
        report["sessionWorkspace"] = str(session_workspace)
    if eve_env:
        return LaunchCapsule(
            mode=LaunchCapsuleMode.CAPSULE,
            role=role,
            eve_env=dict(eve_env),
            session_workspace=session_workspace,
            report=report,
        )
    return LaunchCapsule(
        mode=LaunchCapsuleMode.CAPSULE,
        role=role,
        codex_delivery=capsule_delivery_from(result),
        report=report,
    )


def _refusal(role: str, outcome: CapsuleCompileOutcome) -> LaunchCapsule:
    refusal = outcome.refusal
    return refused_launch_capsule(
        role,
        "capsule-not-compiled" if refusal is None else refusal.status,
        outcome.explanation(),
    )


def _admitted_surfaces(contract_path: Path) -> tuple[Path | None, Path | None]:
    """The enclosure's own report and memory surfaces, for the carrier's write scopes.

    Read out of the contract the enclosure lookup already resolved — the worktree group's ``reports``
    directory and the contract's memory worktree — so a carrier declares the surfaces that exist
    rather than the surfaces this module would have liked. A surface that is absent stays ``None``,
    and the carrier's own role authority table then decides whether that role needed it.
    """

    try:
        contract = load_contract(contract_path)
    except (ContractError, OSError):  # pragma: no cover - the compiler refuses this path already
        return None, None
    # The report surface is the enclosure's own artifact boundary, not a caller's directory: a leaf
    # that has not produced a report yet does not have the directory, and a carrier for a role
    # admitted to write there must still be buildable. Creating it is the launch claiming the
    # surface the contract already declared for it.
    report_root = contract.worktree_group / "reports"
    report_root.mkdir(parents=True, exist_ok=True)
    return report_root, contract.memory_worktree


def _enclosure_contract(
    config: McpRuntimeConfig, repository: str, task_path: str, leaf_id: str | None
) -> Path | None:
    """The worktree enclosure that admits one task document, through the control plane's own reader.

    A leaf document is admitted by its leaf enclosure; any other document by its task's own series
    contract. Both are resolved by :class:`WorktreeContractReader`, the one owner of that lookup, so
    the launch never invents a second way to name an enclosure.
    """

    task_root = _task_root_of(task_path)
    if not task_root:
        return None
    return WorktreeContractReader().find_task_contract(
        config.coordination_root, repository, task_root, leaf_id=leaf_id
    )


def _registered_repository_root(config: McpRuntimeConfig, repository: str) -> Path | None:
    """The configured code root for the repository a task document declares, if registered."""

    scope = config.repositories.get(repository)
    return None if scope is None else scope.path


def _task_root_of(task_path: str) -> str:
    """The task root a document reference sits under: its path without the document file.

    For a top-level task this is the task name; for a master nested inside its sprint it is the
    sprint's own directory. Deriving it from the document's own path keeps one spelling of the
    address instead of a second guess about how deep the task sits.
    """

    head, _, _ = task_path.rpartition("/")
    return head


def _carrier_directory(config: McpRuntimeConfig, role: str, task_path: str) -> Path:
    """Where this seat's eve carrier is written: per seat, outside every workspace."""

    slug = task_path.replace("/", "-").removesuffix(".json")
    root = config.coordination_root.joinpath(*EVE_CARRIER_ROOT)
    return root / f"{role}-{slug}"


__all__ = [
    "EVE_CARRIER_ROOT",
    "FREE_AGENT_TASK_REFERENCE_PREFIX",
    "UNVERSIONED_WORK_BRANCH",
    "AdmittedTaskSeat",
    "FreeAgentSeatAdmission",
    "compile_launch_capsule",
    "free_agent_seat_admission",
    "workspace_identity",
]
