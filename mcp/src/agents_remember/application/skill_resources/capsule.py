"""The read-only capsule operation: admitted task binding in, typed capsule out.

One call does the whole job for a consumer that has an admitted task and an
operation: resolve the task layer's own binding for that document, bind it to the
worktree enclosure that owns it, project the task context, admit exactly the
canonical source files the composition manifest routes for that seat, and compile.
Nothing is written, and a refusal is a value carrying its own explanation rather
than an exception the caller has to translate.

**Where the seat comes from.** The caller declares which task document it is
addressing and which role it believes it holds; the seat is derived from that
document's own altitude, and the declared role is validated against it by the task
layer's own role/altitude rule. The role argument is therefore checked against the
task document and never used as authority, so changing the string to a role the
document cannot carry is a refusal rather than a second seat.

**What is read.** The admitted source root is the package's canonical skills copy;
the caller names no path inside it. The composition manifest decides which files a
seat and operation compose, so a required block that is missing is a refusal rather
than a silently thinner capsule, and no caller can ask this operation for an
arbitrary file.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.role_capsules import (
    CapsuleAdmissionRequest,
    CapsuleCompilationOutcome,
    compile_admitted_capsule,
)
from agents_remember.application.task_projection import (
    ProjectionScopeRequest,
    TaskProjection,
    TaskProjectionRequest,
    TaskProjectionSource,
    parse_task_reference,
    resolve_task_projection_scope,
)
from agents_remember.errors import (
    AuthorityError,
    CapsuleCompilationError,
    TaskProjectionSourceError,
)
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.coordination_context.models import (
    CoordinationHints,
    CoordinationRequest,
    EnclosureSelector,
)
from agents_remember.kernel.coordination_context_resolver import resolve_coordination_context
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.diagnostics import CapsuleManifest
from agents_remember.models.role_capsules.manifest import (
    CapsuleCompositionManifest,
    parse_composition_manifest,
)
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCompilationResult,
    CapsuleDigest,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES, CapsuleOperation
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.store import capture_task_doc_source
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

from .provider import SHIPPED_SKILL_ORIGIN, shipped_composition_corpus

#: The admitted corpus is the lifecycle tree: every source path the composition
#: manifest declares is relative to that directory, and the compiler's own
#: declared-path rules are written against those relative paths. The manifest is
#: therefore admitted as ``composition-manifest.json`` beside them.
COMPOSITION_MANIFEST = "composition-manifest.json"


@dataclass(frozen=True, slots=True)
class CapsuleCompileRequest:
    """One admitted capsule request, as the application boundary receives it.

    ``enclosure`` names the worktree enclosure that admits the seat; it is the
    existing AR enclosure selector, so this operation accepts exactly the
    addressing the rest of the coordination surface accepts and invents no second
    way to name a task. ``task_path`` is a path relative to ``tasks/<repository>``;
    the canonical reference key is built from the repository the enclosure declares
    plus that path, so a caller cannot present an address in a shape the task layer
    would not itself produce.
    """

    enclosure: EnclosureSelector
    task_path: str
    operation: CapsuleOperation
    role: str
    code_repository_root: Path | None = None


@dataclass(frozen=True, slots=True)
class CapsuleCompileOutcome:
    """Exactly one of a compiled capsule or the explanation of its refusal.

    ``binding`` is present in both shapes: an operator always gets to see which
    seat and which revision the operation was addressing, which is what makes a
    refusal actionable rather than a wall.
    """

    compilation: CapsuleCompilationOutcome | None
    refusal: CapsuleCompilationError | None
    binding: CapsuleBinding | None = None
    projection: TaskProjection | None = None

    @property
    def ok(self) -> bool:
        """Whether a capsule was actually compiled.

        Not "an outcome object exists": a refusal is also an outcome, and every
        caller that branches on success has to see a refusal as a failure.
        """

        return self.compilation is not None and self.compilation.result is not None

    @property
    def result(self) -> CapsuleCompilationResult | None:
        """The compiled result, or ``None`` when this outcome is a refusal."""

        return None if self.compilation is None else self.compilation.result

    @property
    def manifest(self) -> CapsuleManifest | None:
        """The diagnostic manifest, present in both shapes."""

        return None if self.compilation is None else self.compilation.manifest

    def explanation(self) -> str:
        """One operator-facing line: the digest, or the refusal and its remedy."""

        if self.refusal is not None:
            return self.refusal.render()
        assert self.compilation is not None
        return self.compilation.render_explanation()


@dataclass(frozen=True, slots=True)
class CapsuleSourceSelectionRequest:
    """Where the composition corpus lives, which manifest routes it, and who publishes it.

    All three default to the package's canonical skills copy. A caller supplies
    them only to point the operation at a different canonical corpus, which is how
    a test drives a synthetic tree without touching the shipped one. The origin
    travels with the tree because a corpus's skill URIs are its own identity: the
    admitted path of a skill is the URI's path below that origin, and a URI that
    does not carry it is refused rather than resolved by a guess.
    """

    root: Path | None = None
    manifest: str = COMPOSITION_MANIFEST
    origin: str = SHIPPED_SKILL_ORIGIN


@dataclass(frozen=True, slots=True)
class CapsuleSeatAddress:
    """One seat, addressed by the two values that decide its composition.

    The role and the operation are what the manifest routes on, so a caller that has no task
    document — and therefore no :class:`CapsuleCompileRequest` to hand over — can still ask for
    exactly the routed source set the compiler would admit for that seat.
    """

    role: str
    operation: str


@dataclass(frozen=True, slots=True)
class AdmittedEnclosure:
    """The enclosure an admitted request resolved to, with the roots it implies.

    ``code_repository_root`` is the repository root this resolution used — the caller's own value
    when it supplied one, otherwise the one read out of the contract the request named. It travels
    on the enclosure so the task projection resolves against the same root rather than re-deriving
    (or failing to derive) one of its own; that second derivation is the other half of D13.
    """

    coordination_root: Path
    contract: WorktreeContract
    code_repository_root: Path | None = None

    @property
    def repository_id(self) -> str:
        return self.contract.repo_name


def compile_task_capsule(
    config: McpRuntimeConfig,
    request: CapsuleCompileRequest,
    *,
    sources: CapsuleSourceSelectionRequest | None = None,
) -> CapsuleCompileOutcome:
    """Compile the capsule for one admitted task document, or explain the refusal.

    Four steps, each of which can only refuse: resolve the enclosure, admit the
    binding it implies, project the task context, and compile the routed source set.
    A refusal at any step is returned with whatever binding was already established,
    so the explanation names a seat and a revision rather than only a symptom.
    """

    try:
        enclosure = _enclosure(config, request)
    except TaskProjectionSourceError as error:
        return CapsuleCompileOutcome(compilation=None, refusal=_as_refusal(error))
    try:
        require_repo(config, enclosure.repository_id)
    except AuthorityError as error:
        return _refused(error, status="repository-not-allowed")
    try:
        admitted = _admitted_facts(config, enclosure, request)
    except (CapsuleCompilationError, TaskProjectionSourceError) as error:
        return _refused(error, status=error.status)
    binding = CapsuleBinding(operation=request.operation, admitted=admitted)
    try:
        projection = _projection(config, enclosure, binding, request)
    except TaskProjectionSourceError as error:
        return CapsuleCompileOutcome(compilation=None, refusal=_as_refusal(error), binding=binding)
    return _compile_routed(binding, projection, request, sources)


def _compile_routed(
    binding: CapsuleBinding,
    projection: TaskProjectionSource,
    request: CapsuleCompileRequest,
    sources: CapsuleSourceSelectionRequest | None,
) -> CapsuleCompileOutcome:
    """Compile the source set the manifest routes for this seat, or return its refusal."""

    selected = sources or CapsuleSourceSelectionRequest()
    with _composition_tree(selected) as (root, manifest):
        manifest_bytes = _manifest_bytes(root, manifest)
        if isinstance(manifest_bytes, CapsuleCompilationError):
            return CapsuleCompileOutcome(compilation=None, refusal=manifest_bytes, binding=binding)
        try:
            admission = routed_admission_request(
                root, manifest, manifest_bytes, request, origin=selected.origin
            )
        except CapsuleCompilationError as error:
            return CapsuleCompileOutcome(compilation=None, refusal=error, binding=binding)
        outcome = compile_admitted_capsule(binding, admission, projection=projection)
    return CapsuleCompileOutcome(compilation=outcome, refusal=outcome.error, binding=binding)


def _projection(
    config: McpRuntimeConfig,
    enclosure: AdmittedEnclosure,
    binding: CapsuleBinding,
    request: CapsuleCompileRequest,
) -> TaskProjectionSource:
    """The projection source: the task layer resolves the enclosure, this seals it.

    The admitted revision travels into the projection as the digest of the bytes
    the task store actually handed over, so the projection's own comparison is
    between two independently obtained facts rather than between a value and
    itself.
    """

    scope = resolve_task_projection_scope(
        binding,
        coordination_root=enclosure.coordination_root,
        scope_request=ProjectionScopeRequest(
            selector=request.enclosure,
            workspace_root=config.workspace_root,
            code_repository_root=enclosure.code_repository_root,
        ),
    )
    # The task-projection request carries no packet locations: the preferred route is
    # the task document declaring its packet itself, which needs no admission from
    # this operation, and an exact-text-only requirement surfaces as the projection's
    # own recorded gap rather than as a silently dropped obligation.
    return TaskProjectionSource(scope=scope, request=TaskProjectionRequest())


def _admitted_facts(
    config: McpRuntimeConfig,
    enclosure: AdmittedEnclosure,
    request: CapsuleCompileRequest,
) -> CapsuleAdmittedFacts:
    """The binding facts, each resolved from an existing owner rather than trusted.

    The enclosure answers the repository and the work branch; the task store answers
    the exact JSON bytes the document currently has; the task layer answers whether
    the declared role may sit at that document's altitude. The role is therefore an
    input to a check, never the source of the seat.
    """

    if request.role not in CAPSULE_ROLES:
        raise CapsuleCompilationError(
            status="unknown-role",
            detail=(
                f"role {request.role!r} is not one of the frozen capsule roles "
                f"({', '.join(CAPSULE_ROLES)})"
            ),
            next_action="name one of the frozen role identities",
        )
    contract = enclosure.contract
    ref = _task_reference(enclosure.repository_id, request.task_path)
    topology = TaskDocumentTopology(enclosure.coordination_root)
    try:
        resolved = topology.resolve(ref)
        altitude = topology.validate_role(ref, request.role)
    except TaskDocumentRefError as error:
        raise TaskProjectionSourceError(
            "role-altitude-mismatch",
            f"task document {ref.key} cannot carry role {request.role!r}: {error}",
            next_action=(
                "address a task document at the altitude this role occupies; the seat is "
                "derived from the document, not from the requested role"
            ),
        ) from error
    if contract.leaf_id and resolved.document.id != contract.leaf_id:
        raise TaskProjectionSourceError(
            "task-binding-mismatch",
            f"the enclosure is for leaf {contract.leaf_id!r} but the admitted task document "
            f"declares id {resolved.document.id!r}",
            next_action="address the leaf this enclosure was started for",
        )
    admitted_bytes = capture_task_doc_source(Path(resolved.path)).json_bytes
    if admitted_bytes is None:
        raise TaskProjectionSourceError(
            "task-unknown",
            f"the admitted task document {ref.key} has no readable JSON source",
            next_action="admit a task document that exists in this coordination root",
        )
    return CapsuleAdmittedFacts(
        task_reference=ref.key,
        task_document_digest=_digest(admitted_bytes),
        seat=CapsuleRoleSeat(role=request.role, altitude=altitude),
        repository_id=enclosure.repository_id,
        work_branch=contract.code_work_branch,
        tool_policy=admitted_tool_policy(config),
    )


def _task_reference(repository_id: str, task_path: str) -> TaskDocumentRef:
    """The task layer's canonical reference for a repository-relative task path.

    The repository half comes from the enclosure, not from the caller, and the path
    half is parsed by the task layer's own reference type, so a path that escapes the
    task root or is not canonical is refused before any read is attempted.
    """

    return parse_task_reference(f"{repository_id}/{task_path.strip().lstrip('/')}")


def _enclosure(config: McpRuntimeConfig, request: CapsuleCompileRequest) -> AdmittedEnclosure:
    """The enclosure the admitted selector names, or a typed refusal.

    The repository identity is read out of the resolved contract rather than taken
    from the caller, so the capsule's admitted facts and the task reference it builds
    are both anchored to the enclosure that actually owns them.
    """

    declared_root = _declared_repository_root(request)
    try:
        context = resolve_coordination_context(
            workspace_root=config.workspace_root,
            code_repository_root=declared_root,
            request=CoordinationRequest(
                hints=CoordinationHints(
                    topology="external", coordination_root=config.coordination_root
                ),
                selector=request.enclosure,
                contract_reader=WorktreeContractReader(),
            ),
        )
    except (ValueError, OSError) as error:
        raise TaskProjectionSourceError(
            "binding-unresolved",
            f"coordination context did not resolve: {error}",
            next_action=(
                "supply an unambiguous enclosure selector (contract path, or task name plus "
                "leaf id or worktree name)"
            ),
        ) from error
    contract_path = context.contract_path
    if contract_path is None:
        raise TaskProjectionSourceError(
            "binding-unresolved",
            "the admitted enclosure did not resolve to a worktree contract",
            next_action=(
                "name the enclosure explicitly; without a contract there is no admitted branch "
                "or task to bind to"
            ),
        )
    try:
        contract = load_contract(contract_path)
    except (ContractError, OSError) as error:
        raise TaskProjectionSourceError(
            "contract-unavailable",
            f"worktree contract {contract_path.as_posix()} is unreadable: {error}",
            next_action="re-run worktree_status for this task and admit the contract it reports",
        ) from error
    return AdmittedEnclosure(
        coordination_root=context.coordination_root,
        contract=contract,
        code_repository_root=declared_root,
    )


def _declared_repository_root(request: CapsuleCompileRequest) -> Path | None:
    """The code repository root this request admits, from the contract it already names.

    Defect D13: the coordination resolver refuses to resolve anything without a repository name or
    root — ``code_repository_name is required when code_repository_root is not supplied`` — while the
    *registered* ``role_capsule_compile`` tool exposes ``contract_path`` and no repository field at
    all. The enclosure contract the caller names already declares the repository and its code path
    (``repo_name`` / ``code.repo_path``), so that declaration is the authority read here rather than
    a second value smuggled in beside it. A caller that supplies ``code_repository_root`` keeps
    supplying it: this only fills the half the tool cannot express.
    """

    if request.code_repository_root is not None:
        return request.code_repository_root
    contract_path = request.enclosure.contract_path
    if contract_path is None:
        return None
    try:
        return load_contract(contract_path).code_repo_path
    except (ContractError, OSError) as error:
        raise TaskProjectionSourceError(
            "contract-unavailable",
            f"worktree contract {contract_path.as_posix()} is unreadable: {error}",
            next_action="re-run worktree_status for this task and admit the contract it reports",
        ) from error


def admitted_tool_policy(config: McpRuntimeConfig) -> CapsuleToolPolicy:
    """The tool identities this server is authorized to advertise.

    A capsule requests tools and never grants them: the compiler narrows every
    request against this snapshot and refuses a request outside it. The snapshot is
    the published AR MCP tool surface, so a tool identity this server does not
    advertise cannot be requested through it.
    """

    del config  # the configuration decides the surface; the surface is what is admitted
    from agents_remember.models.tools.public_roster import PUBLIC_TOOLS  # noqa: PLC0415

    return CapsuleToolPolicy(
        granted=frozenset(PUBLIC_TOOLS),
        notes="the published AR MCP tool surface; a capsule request outside it is refused",
    )


@contextmanager
def _composition_tree(selected: CapsuleSourceSelectionRequest) -> Iterator[tuple[Path, str]]:
    """The admitted source root and manifest, held open for the whole compile.

    The default corpus is the package's own skills copy, which the packaging helper
    may materialize for the duration of one call; the read therefore happens inside
    this context rather than against a path that could be reclaimed underneath it.
    """

    if selected.root is not None:
        yield selected.root, selected.manifest
        return
    with shipped_composition_corpus() as corpus:
        yield corpus


def _manifest_bytes(root: Path, manifest: str) -> bytes | CapsuleCompilationError:
    try:
        return (root / manifest).read_bytes()
    except OSError as error:
        return CapsuleCompilationError(
            status="source-missing",
            detail=f"composition manifest {manifest!r} is unreadable under {root.as_posix()}: {error}",
            next_action="point the operation at the canonical corpus root that carries it",
        )


def routed_admission_request(
    root: Path,
    manifest: str,
    manifest_bytes: bytes,
    request: CapsuleCompileRequest,
    *,
    origin: str = SHIPPED_SKILL_ORIGIN,
) -> CapsuleAdmissionRequest:
    """The exact source set the manifest routes for this seat and operation.

    Selection is read from the manifest rather than from the caller: the admitted
    paths are the shared core blocks this seat composes, its own role file, the
    operation's file, and the root file of every skill it declares.
    """

    return routed_admission_for(
        root,
        manifest,
        manifest_bytes,
        CapsuleSeatAddress(role=request.role, operation=request.operation),
        origin=origin,
    )


def routed_admission_for(
    root: Path,
    manifest: str,
    manifest_bytes: bytes,
    seat: CapsuleSeatAddress,
    *,
    origin: str = SHIPPED_SKILL_ORIGIN,
) -> CapsuleAdmissionRequest:
    """The routed source set for one seat, addressed by its role and operation alone.

    The same selection routine as :func:`routed_admission_request`, taking the two
    values that actually decide it. A launch that supplies a capsule to a seat with
    no task document has no enclosure and no task path to hand over, and it must
    still go through this one routing rule rather than re-deriving a source set.
    """

    parsed = parse_composition_manifest(manifest_bytes)
    entry = parsed.roles.get(seat.role)
    if entry is None:
        raise CapsuleCompilationError(
            status="unknown-role",
            detail=f"the composition manifest declares no role {seat.role!r}",
            next_action="name a role the manifest declares",
        )
    operation_entry = parsed.operations.get(seat.operation)
    if operation_entry is None:
        raise CapsuleCompilationError(
            status="unknown-operation",
            detail=f"the composition manifest declares no operation {seat.operation!r}",
            next_action="name an operation the manifest declares",
        )
    return CapsuleAdmissionRequest(
        root=root,
        manifest=manifest,
        core=tuple(parsed.core[name].source for name in sorted(entry.core)),
        role=(entry.file,),
        operation=(operation_entry.source,),
        specialization=(),
        skill=tuple(sorted(_skill_root_source(parsed, name, origin) for name in entry.skills)),
    )


def _skill_root_source(parsed: CapsuleCompositionManifest, name: str, origin: str) -> str:
    """The skill's root file, as the admitted path the manifest declares for it.

    The manifest's ``skills.<name>.source`` is the one path the compiler's declared
    plan locks and its reference revision reads, so it is what this operation
    admits; the published ``uri`` is the address a client reads the skill at and is
    checked for provenance, not used as a filesystem path. Composing a path out of
    the URI would let two spellings of one skill disagree about which file a
    revision covers, which is exactly the ambiguity the compiler's plan exists to
    remove.
    """

    skill = parsed.skill_entry(name)
    prefix = f"skill://{origin}/"
    if not skill.uri.startswith(prefix):
        raise CapsuleCompilationError(
            status="source-not-declared",
            detail=(
                f"declared skill URI {skill.uri!r} for {name!r} does not begin with the "
                f"publishing origin {prefix!r}"
            ),
            next_action="declare the skill with the resource URI this server publishes it at",
        )
    if skill.uri[len(prefix) :].rstrip("/").split("/")[-1] != name:
        raise CapsuleCompilationError(
            status="source-not-declared",
            detail=(f"declared skill URI {skill.uri!r} does not end in the skill name {name!r}"),
            next_action="declare the skill URI whose final segment is the skill's own name",
        )
    return skill.source


def _digest(content: bytes) -> CapsuleDigest:
    return compute_content_digest(content)


def _as_refusal(error: TaskProjectionSourceError) -> CapsuleCompilationError:
    return CapsuleCompilationError(
        status=error.status,
        detail=error.detail,
        next_action=error.next_action,
    )


def _refused(error: object, *, status: str) -> CapsuleCompileOutcome:
    detail = getattr(error, "detail", None) or str(error)
    return CapsuleCompileOutcome(
        compilation=None,
        refusal=CapsuleCompilationError(
            status=status,
            detail=str(detail),
            next_action="correct the admitted input and compile again",
        ),
    )


def capsule_payload(result: object) -> dict[str, object]:
    """The wire shape of one compiled capsule: content, provenance and explanation."""

    capsule = result.capsule  # type: ignore[attr-defined]
    return {
        "semanticDigest": result.semantic_digest,  # type: ignore[attr-defined]
        "instructions": [
            {
                "identity": unit.block.identity,
                "compositionRoot": unit.block.composition_root,
                "sourcePath": unit.block.source_path,
                "revision": unit.block.revision,
                "contentDigest": unit.block.content_digest,
                "authorities": list(unit.block.authorities),
                "content": unit.block.content,
            }
            for unit in capsule.instruction_units
        ],
        "skillReferences": [
            {
                "identity": reference.identity,
                "origin": reference.origin,
                "uri": reference.uri,
                "revision": reference.revision,
            }
            for reference in capsule.skill_references
        ],
        "requestedTools": [
            {"toolId": request.tool_id, "authority": request.authority}
            for request in capsule.requested_tools
        ],
        "taskContext": None
        if capsule.task_context is None
        else {
            "origin": capsule.task_context.origin,
            "projectionRevision": capsule.task_context.projection_revision,
            "contentDigest": capsule.task_context.content_digest,
            "markdown": capsule.task_context.markdown,
        },
    }


__all__ = [
    "COMPOSITION_MANIFEST",
    "CapsuleCompileOutcome",
    "CapsuleCompileRequest",
    "CapsuleSeatAddress",
    "CapsuleSourceSelectionRequest",
    "admitted_tool_policy",
    "capsule_payload",
    "compile_task_capsule",
    "routed_admission_for",
    "routed_admission_request",
]
