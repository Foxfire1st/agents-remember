"""The explainer's routes: the review page's build, Update, Retire and state (MIK-R186, MIK-R106 v2).

Transport only: a caller names the task context (repository, master, leaf), the route calls the
seat service the composition root wires, and every refusal is a typed 409 (MIK-R185 v2 rule 3).
Reads here start no model (MIK-R106 v2 rule 10).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from agents_remember.canvas.explainer_capsule import capability_text
from agents_remember.canvas.explainer_grades import grades_path, read_grades
from agents_remember.canvas.explainer_paths import (
    EXPLAINER_FRAME,
    leaf_workspace,
    receipt_id_segment,
    repository_path,
)
from agents_remember.canvas.explainer_planned_read import planned_payload
from agents_remember.canvas.explainer_server import page_revision
from agents_remember.canvas.explainer_service import (
    BoundComparison,
    CancelOutcome,
    ExplainerDeps,
    ExplainerHost,
    ExplainerRefused,
    ExplainerState,
    LeafFacts,
    LeafScope,
    PreparedPacket,
    SeatOutcome,
    build_seat,
    cancel_seat,
    retire_seat,
    seat_state,
    update_seat,
)
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    PartStaleness,
    grade_staleness,
    regeneration_request,
    stale_grade_references,
)
from agents_remember.canvas.explainer_staleness_read import screen_target, staleness_read
from agents_remember.canvas.explainer_state import PlannedIdentity, read_binding
from agents_remember.canvas.explainer_target import ExplainerTargetReads
from agents_remember.canvas.paths import PathRefused
from agents_remember.canvas.paths import task_root as leaf_task_root
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_evidence import EvidenceMap, SupplementalReceipt
from agents_remember.models.knowledge.review_frame import (
    COMPARISON_OWNER,
    PLANNED_OWNER,
    FrameBinding,
    FrameManifest,
    comparison_reference,
    planned_reference,
)
from agents_remember.models.knowledge.review_refusal import ReviewRefusal
from agents_remember.models.knowledge.review_trees import (
    ReviewCodeSide,
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
    ReviewTrees,
    ReviewTreesResult,
    TreeKnowledge,
)
from agents_remember.serving.change_watcher import move_notice_watch
from agents_remember.serving.explainer_watcher import (
    CapturedComparison,
    GitComparisonOwner,
    LeafSelection,
    WatcherApplicationPorts,
    blocks_changed,
    notice_for,
)
from agents_remember.serving.review_trees import ReviewTreesPort, ReviewTreesQuery
from agents_remember.tasks.planned_comparison import (
    agreed_revision_number,
    compose_explainer_send_message,
    leaf_document,
    leaf_document_stem,
    manifest_claims,
    manifest_parts,
    master_is_organizational,
    planned_comparison,
    resolve_master_contract,
    retained_revision_numbers,
    revision_kind_of,
    revision_row_of,
    serialize_frame_binding,
    serialize_grades_and_categories,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


class FrameScopePort(Protocol):
    """The verified frame scope a removal act may touch."""

    @property
    def frame_dir(self) -> Path: ...


class RevisionRemovalPort(Protocol):
    """What one revision removal reports: the numbers, the pin, and the bytes reclaimed."""

    @property
    def removed(self) -> tuple[int, ...]: ...

    @property
    def retained(self) -> tuple[int, ...]: ...

    @property
    def pinned(self) -> int | None: ...

    @property
    def bytes_freed(self) -> int: ...

    @property
    def detail(self) -> str: ...


class EvidencePacketPort(Protocol):
    """The prepared packet as the route reads it: one dump for the digest."""

    def model_dump(self, *, by_alias: bool = ...) -> dict[str, object]: ...


class PreparedEvidencePort(Protocol):
    """What one preparation produced, as far as the route reads it."""

    @property
    def packet(self) -> EvidencePacketPort: ...


@dataclass(frozen=True)
class ExplainerApplicationPorts:
    """Application-tier frame, evidence and comparison ports injected into the explainer routes.

    The serving tier ranks below ``application`` in ``layers.toml``, so it cannot import the
    adapters below; the composition root builds this one value and hands it to
    :func:`explainer_composition`. No module-global binding stands in for it.
    """

    recorded_leaf_contract: Callable[[McpRuntimeConfig, str, str, str], WorktreeContract | None]
    prepare_evidence: Callable[..., PreparedEvidencePort]
    compose_receipts: Callable[[Path, dict[str, object], str], tuple[SupplementalReceipt, ...]]
    frame_refused_cls: type[Exception]
    frame_scope: Callable[[Path, str, str], FrameScopePort]
    #: The frame store's removal entry: ``(scope, *, revisions=...)``, one call per removal.
    remove_revisions: Callable[..., RevisionRemovalPort]
    revision_manifest: Callable[[Path, int], FrameManifest | None]
    fresh_supplemental_receipts: Callable[
        [Path, str, Sequence[SupplementalReceipt]], tuple[SupplementalReceipt, ...]
    ]
    task_frames_total: Callable[[Path, str], dict[str, object]]
    code_sides: Callable[[ReviewTreeComparisonRecord], tuple[ReviewCodeSide, ...]]
    missing_tree: Callable[[Path, str], str | None]
    reopen_review_trees: Callable[
        [Path, WorktreeContract, int | None], ReviewTrees | ReviewRefusal | None
    ]
    watcher: WatcherApplicationPorts


__all__ = [
    "EXPLAINER_API_PREFIX",
    "ExplainerApplicationPorts",
    "ExplainerComposition",
    "explainer_composition",
    "leaf_document",
    "register_explainer_routes",
]

EXPLAINER_API_PREFIX = "/api/explainer"
MAX_MESSAGE = 32768
MAX_REQUEST_ID = 128
MAX_REMOVE_BATCH = 1024
UNWIRED_DETAIL = (
    "no explainer service is wired into this process; the page cannot build, update or retire an "
    "explainer"
)
_VALID_HISTORIES = frozenset({"live", "recorded", "planned"})


@dataclass(frozen=True)
class _SendRequest:
    message: str
    request_id: str
    comparison_number: int | None = None
    history: str | None = None
    planned: bool = False


class _ComparisonMoved(ExplainerRefused):
    def __init__(self, *, requested: int, current: int | None) -> None:
        self.requested = requested
        self.current = current
        current_label = f"#{current}" if current is not None else "new state"
        super().__init__(
            "comparison-moved",
            f"requested comparison #{requested}, but the leaf's current comparison is "
            f"{current_label}; refresh the form and Send again",
        )


_SEND_SELECTION: ContextVar[_SendRequest | None] = ContextVar("_SEND_SELECTION", default=None)


@dataclass(frozen=True)
class ExplainerComposition:
    """The seat service the routes call, the series contract, and whether it is wired at all."""

    deps: ExplainerDeps | None = None
    contract_reader: Callable[[LeafScope], Path | None] | None = None
    application: ExplainerApplicationPorts | None = None
    review_trees: ReviewTreesPort | None = None
    task_root_for: Callable[[LeafScope], Path] | None = None

    def contract(self, scope: LeafScope) -> Path | None:
        if self.contract_reader is not None:
            return self.contract_reader(scope)
        if self.deps is not None:
            return resolve_master_contract(
                self.deps.config.coordination_root, scope.repository_id, scope.task
            )
        return None

    @property
    def wired(self) -> bool:
        return self.deps is not None


def _refusal(
    reason: str, detail: str, *, status: int = 409, extra: dict[str, Any] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"state": "refused", "reason": reason, "detail": detail}
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status)


def _scope(request: Request) -> LeafScope:
    params = request.path_params
    return LeafScope(
        repository_id=params["repository_id"],
        task=params["master"],
        leaf_id=params["leaf_id"],
    )


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _master_archived(task_root: Path) -> bool:
    record = task_root / "notes" / "reports" / "master-retirement.json"
    return record.is_file() or ("0_archive" in task_root.parts)


def _facts(task_root_for: Callable[[LeafScope], Path]) -> Callable[[LeafScope], LeafFacts]:
    def read(scope: LeafScope) -> LeafFacts:
        root = task_root_for(scope)
        document = leaf_document(root, scope.leaf_id)
        if document is None:
            raise ExplainerRefused(
                "leaf-unavailable",
                f"no task document names leaf {scope.leaf_id} in {root}",
            )
        agreed = document.get("intentReview") or {}
        agreed_revision = None
        if isinstance(agreed, dict):
            revision = agreed.get("agreedRevision")
            if isinstance(revision, dict):
                agreed_revision = revision.get("sha256")
        return LeafFacts(
            status=str(document.get("status") or ""),
            archived=_master_archived(root),
            agreed_revision=agreed_revision,
        )

    return read


def _trees_of(result: ReviewTreesResult) -> ReviewTrees:
    record = result.comparison
    sides = result.knowledge_sides
    if record is None or len(sides) != 2:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the comparison owner answered {result.state!r} without two knowledge sides; no "
            "admitted packet can be prepared from it",
        )
    return ReviewTrees(
        record,
        None,
        Path(record.memory_candidate.repository),
        TreeKnowledge(sides[0], None),
        TreeKnowledge(sides[1], None),
        code_sides=result.code_sides,
    )


def _is_closed_leaf(
    config: McpRuntimeConfig, scope: LeafScope, application: ExplainerApplicationPorts
) -> bool:
    contract = application.recorded_leaf_contract(
        config, scope.repository_id, scope.task, scope.leaf_id
    )
    if contract is None:
        return False
    worktree = contract.code_worktree
    return worktree is None or not worktree.exists()


def _check_requested_number(
    port: ReviewTreesPort,
    scope: LeafScope,
    result: ReviewTreesResult,
    *,
    requested: int,
    recorded: bool,
) -> None:
    if result.state == "trees" and result.comparison is not None:
        current = result.latest if result.latest is not None else result.comparison.number
        if result.comparison.number != requested or current != requested:
            raise _ComparisonMoved(requested=requested, current=current)
        return
    current_res = port(
        ReviewTreesQuery(
            repository_id=scope.repository_id,
            master=scope.task,
            leaf_id=scope.leaf_id,
            number=None,
            recorded=recorded,
        )
    )
    if current_res.state == "trees" and current_res.comparison is not None:
        current = (
            current_res.latest if current_res.latest is not None else current_res.comparison.number
        )
        if current != requested:
            raise _ComparisonMoved(requested=requested, current=current)


def _recorded(
    config: McpRuntimeConfig,
    port: ReviewTreesPort,
    scope: LeafScope,
    application: ExplainerApplicationPorts,
    *,
    selection: _SendRequest | None = None,
) -> tuple[ReviewTreeComparisonRecord, ReviewTrees]:
    history = None if selection is None else selection.history
    requested = None if selection is None else selection.comparison_number
    recorded = (
        history == "recorded"
        if history is not None
        else _is_closed_leaf(config, scope, application)
    )
    result = port(
        ReviewTreesQuery(
            repository_id=scope.repository_id,
            master=scope.task,
            leaf_id=scope.leaf_id,
            number=requested,
            recorded=recorded,
        )
    )
    if requested is not None:
        _check_requested_number(port, scope, result, requested=requested, recorded=recorded)
    if result.state != "trees" or result.comparison is None:
        detail = None if result.refusal is None else result.refusal.detail
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the comparison owner answered {result.state!r}: {detail or 'no tree comparison'}",
        )
    return result.comparison, _trees_of(result)


def _planned(
    config: McpRuntimeConfig,
    contract: Path,
    scope: LeafScope,
    application: ExplainerApplicationPorts,
) -> tuple[ReviewTreeComparisonRecord, ReviewTrees]:
    """The planned comparison at the master line's two tip commits (MIK-R186 rule 4, MIK-R189 rule 4).

    The line comes from the canonical series contract reader and the master's canonical execution
    nature: an atomic master's line is its integration (work) branch, which previously landed leaves
    advanced, and an organizational master's is the sprint integration branch the contract records
    as its source branch. The line's own tip wins when it diverges from the fork base; the base
    engages only while the line names no commit at all, and nothing substitutes the repository's
    HEAD.
    """

    code_repository = repository_path(config, scope.repository_id)
    memory_repository = repository_path(config, scope.repository_id, memory=True)
    organizational = master_is_organizational(
        config.coordination_root, scope.repository_id, scope.task
    )
    try:
        parsed = load_contract(contract)
    except (ContractError, OSError) as error:
        raise ExplainerRefused(
            "planned-comparison-unavailable",
            f"the master line's series contract cannot be read: {error}",
        ) from error
    code_commit = _line_tip_commit(
        code_repository, *_master_line_branches(parsed, organizational, memory=False)
    )
    if code_commit is None:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the master line's code branches name no commit in {code_repository}",
        )
    memory_commit = _line_tip_commit(
        memory_repository, *_master_line_branches(parsed, organizational, memory=True)
    )
    if memory_commit is None:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the master line's memory branches name no commit in {memory_repository}",
        )
    record = planned_comparison(
        task_id=scope.task,
        leaf_id=scope.leaf_id,
        code_repository=str(code_repository),
        code_commit=code_commit,
        code_tree=_rev_parse(code_repository, f"{code_commit}^{{tree}}"),
        memory_repository=str(memory_repository),
        memory_commit=memory_commit,
        memory_tree=_rev_parse(memory_repository, f"{memory_commit}^{{tree}}"),
    )
    missing_memory = application.missing_tree(memory_repository, record.memory_candidate.tree)
    sides = tuple(
        ReviewKnowledgeSide(
            side=side,
            state="available" if missing_memory is None else "unavailable-history",
            tree=record.memory_candidate.tree,
            index_state="complete" if missing_memory is None else None,
            detail=missing_memory,
        )
        for side in ("before", "after")
    )
    trees = ReviewTrees(
        record,
        None,
        memory_repository,
        TreeKnowledge(sides[0], None),
        TreeKnowledge(sides[1], None),
        code_sides=application.code_sides(record),
    )
    return record, trees


def _rev_parse(repository: Path, ref: str) -> str:
    result = run_git(repository, ["rev-parse", ref])
    if result.returncode != 0:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the master line's {ref!r} cannot be read in {repository}: {result.stderr.strip()}",
        )
    return result.stdout.strip()


def _master_line_branches(
    parsed: WorktreeContract, organizational: bool, *, memory: bool
) -> tuple[str, str]:
    """One side's master-line branch and its fork base, from the canonical contract.

    An atomic master's line is the work branch (the integration line), and an organizational
    master's is the source branch (the sprint's integration branch), by the same separation the
    worktree lifecycle writes and the landed net comparison reads.
    """

    if memory:
        if organizational:
            return parsed.memory_source_branch, parsed.memory_work_branch
        return parsed.memory_work_branch, parsed.memory_source_branch
    if organizational:
        return parsed.code_source_branch, parsed.code_work_branch
    return parsed.code_work_branch, parsed.code_source_branch


def _line_tip_commit(repository: Path, line: str, base: str) -> str | None:
    """The named line's tip commit, or the fork base while the line names no commit at all.

    The first name that resolves wins: a line that exists is followed even when the base has
    advanced past it, and the base is read only for a master whose line holds nothing yet (an
    atomic master before its first landing). No branch name resolves, so the repository's own HEAD
    is never substituted for the contract's line.
    """

    for branch in (line, base):
        if not branch:
            continue
        try:
            result = run_git(repository, ["rev-parse", "--verify", "--quiet", branch])
        except (OSError, RuntimeError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _resolve_contract_for(
    config: McpRuntimeConfig,
    scope: LeafScope,
    contract_for: Callable[[LeafScope], Path | None] | None,
) -> Path | None:
    if contract_for is not None:
        return contract_for(scope)
    return resolve_master_contract(config.coordination_root, scope.repository_id, scope.task)


def _comparison(
    config: McpRuntimeConfig,
    port: ReviewTreesPort,
    contract_for: Callable[[LeafScope], Path | None] | None,
    application: ExplainerApplicationPorts,
) -> Callable[[LeafScope, LeafFacts], BoundComparison]:
    def read(scope: LeafScope, facts: LeafFacts) -> BoundComparison:
        selection = _SEND_SELECTION.get()
        if selection is not None and selection.history == "planned" and not facts.planned:
            raise ExplainerRefused(
                "not-a-planning-leaf",
                f"leaf {scope.leaf_id} is not planning; its page binds a recorded comparison",
            )
        if facts.planned:
            if selection is not None and selection.history in {"live", "recorded"}:
                raise ExplainerRefused(
                    "comparison-unavailable",
                    f"leaf {scope.leaf_id} is planning and has no {selection.history} comparison",
                )
            contract = _resolve_contract_for(config, scope, contract_for)
            if contract is None:
                raise ExplainerRefused(
                    "planned-comparison-unavailable",
                    "a planning leaf binds the master line's tips, and no series contract was "
                    "supplied to read them from",
                )
            record, trees = _planned(config, contract, scope, application)
            binding = FrameBinding.from_record(
                record,
                comparison_owner=PLANNED_OWNER,
                comparison_reference=planned_reference(scope.task, scope.leaf_id),
            )
            planned = PlannedIdentity(
                code_tip=record.code_candidate.commit or record.code_candidate.tree,
                memory_tip=record.memory_candidate.commit or record.memory_candidate.tree,
                code_tree=record.code_candidate.tree,
                memory_tree=record.memory_candidate.tree,
            )
            return BoundComparison(
                binding=binding, planned=planned, record_digest=None, record=trees
            )
        record, trees = _recorded(config, port, scope, application, selection=selection)
        binding = FrameBinding.from_record(
            record,
            comparison_owner=COMPARISON_OWNER,
            comparison_reference=comparison_reference(record),
        )
        record_bytes = record.model_dump_json(by_alias=True).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(record_bytes).hexdigest()
        return BoundComparison(binding=binding, record_digest=digest, record=trees)

    return read


@dataclass(frozen=True)
class _ReadPlumbing:
    """The four values a packet read composes: config, owner, contract and application ports."""

    config: McpRuntimeConfig
    review_trees: ReviewTreesPort
    contract_for: Callable[[LeafScope], Path | None] | None
    application: ExplainerApplicationPorts
    task_root_for: Callable[[LeafScope], Path]


def _prepared_trees(
    plumbing: _ReadPlumbing, scope: LeafScope, bound: BoundComparison
) -> ReviewTrees:
    application = plumbing.application
    config = plumbing.config
    if bound.binding.comparison_owner == PLANNED_OWNER:
        contract = _resolve_contract_for(config, scope, plumbing.contract_for)
        if contract is None:
            if isinstance(bound.record, ReviewTrees):
                return bound.record
            raise ExplainerRefused(
                "planned-comparison-unavailable",
                "a planned packet needs the master line's tips",
            )
        record, trees = _planned(config, contract, scope, application)
        rechecked = FrameBinding.from_record(
            record,
            comparison_owner=PLANNED_OWNER,
            comparison_reference=planned_reference(scope.task, scope.leaf_id),
        )
        if not bound.binding.same_comparison(rechecked):
            raise ExplainerRefused(
                "binding-moved",
                "the master line's tips moved while the planned environment was being prepared; "
                "retry to bind the new tips",
            )
        return bound.record if isinstance(bound.record, ReviewTrees) else trees
    if isinstance(bound.record, ReviewTrees):
        return bound.record
    selection = _SEND_SELECTION.get()
    record, trees = _recorded(
        config, plumbing.review_trees, scope, application, selection=selection
    )
    if selection is not None and not bound.binding.same_comparison(
        FrameBinding.from_record(
            record,
            comparison_owner=COMPARISON_OWNER,
            comparison_reference=comparison_reference(record),
        )
    ):
        raise _ComparisonMoved(requested=bound.binding.comparison_number, current=record.number)
    return trees


def _prepare(
    plumbing: _ReadPlumbing,
) -> Callable[[LeafScope, LeafFacts, BoundComparison], PreparedPacket]:
    def read(scope: LeafScope, _facts: LeafFacts, bound: BoundComparison) -> PreparedPacket:
        application = plumbing.application
        trees = _prepared_trees(plumbing, scope, bound)
        task_root = plumbing.task_root_for(scope)
        document = leaf_document(task_root, scope.leaf_id) or {}
        try:
            prepared = application.prepare_evidence(
                trees,
                scope=f"the change set of {scope.leaf_id}",
                question="explain this change set end to end",
                evidence_map=EvidenceMap(coverage=f"the change set of {scope.leaf_id}"),
                supplemental=application.compose_receipts(task_root, document, scope.leaf_id),
                comparison_owner=bound.binding.comparison_owner,
                comparison_reference=bound.binding.comparison_reference,
                leaf_document=leaf_document_stem(task_root, scope.leaf_id),
            )
        except ValueError as error:
            raise ExplainerRefused("evidence-source-unavailable", str(error)) from error
        payload = json.dumps(prepared.packet.model_dump(by_alias=True), indent=2).encode()
        return PreparedPacket(
            packet=prepared.packet, digest="sha256:" + hashlib.sha256(payload).hexdigest()
        )

    return read


def _grade_fingerprints(
    config: McpRuntimeConfig,
) -> Callable[[LeafScope, FrameBinding], dict[str, BlockFingerprint]]:
    """The production fingerprint reader: each retained grade's reference, hashed at its binding."""

    def read(scope: LeafScope, binding: FrameBinding) -> dict[str, BlockFingerprint]:
        task_root = leaf_task_root(config, scope.repository_id, scope.task)
        try:
            held = read_grades(grades_path(task_root, scope.leaf_id), leaf_id=scope.leaf_id)
        except (ExplainerRefused, ValueError):
            return {}
        reads = ExplainerTargetReads(binding=binding, task_root=task_root)
        found: dict[str, BlockFingerprint] = {}
        for grade in held.grades:
            fingerprint = reads.block_fingerprint(grade.reference)
            if fingerprint is not None:
                found[grade.reference] = fingerprint
        return found

    return read


def _capsule() -> Callable[[LeafScope], str]:
    def read(_scope: LeafScope) -> str:
        return capability_text()

    return read


@dataclass(frozen=True)
class ExplainerWiring:
    """What the composition root hands the routes: the host, the application ports, two readers."""

    host: ExplainerHost
    application: ExplainerApplicationPorts
    contract_for: Callable[[LeafScope], Path | None] | None = None
    task_root_for: Callable[[LeafScope], Path] | None = None


def explainer_composition(
    config: McpRuntimeConfig,
    review_trees: ReviewTreesPort | None,
    *,
    wiring: ExplainerWiring | None = None,
) -> ExplainerComposition:
    """The routes' seat service, contract reader and application ports, or an unwired one.

    A process that cannot compose all three refuses every explainer route by name: a page that
    cannot build, update or retire says so rather than serving an empty one.
    """

    if review_trees is None or wiring is None:
        return ExplainerComposition()
    application = wiring.application
    resolve = wiring.task_root_for or (
        lambda scope: leaf_task_root(config, scope.repository_id, scope.task)
    )
    composition = ExplainerComposition(
        application=application,
        review_trees=review_trees,
        task_root_for=resolve,
        contract_reader=wiring.contract_for,
    )
    deps = ExplainerDeps(
        config=config,
        host=wiring.host,
        facts=_facts(resolve),
        comparison=_comparison(config, review_trees, wiring.contract_for, application),
        prepare=_prepare(
            _ReadPlumbing(
                config=config,
                review_trees=review_trees,
                contract_for=wiring.contract_for,
                application=application,
                task_root_for=resolve,
            )
        ),
        capsule=_capsule(),
        grade_fingerprints=_grade_fingerprints(config),
        counter=partial(blocks_changed, ports=application.watcher),
    )
    return replace(composition, deps=deps)


def _task_folder(config: McpRuntimeConfig, scope: LeafScope) -> Path:
    return leaf_task_root(config, scope.repository_id, scope.task)


def _manifest_for_scope(
    config: McpRuntimeConfig,
    scope: LeafScope,
    application: ExplainerApplicationPorts,
    *,
    revision: int | None = None,
) -> FrameManifest | None:
    root = _task_folder(config, scope)
    try:
        frame = application.frame_scope(root, scope.leaf_id, EXPLAINER_FRAME)
    except ValueError:
        return None
    if revision is not None:
        return application.revision_manifest(frame.frame_dir, revision)
    manifests = [
        manifest
        for number in retained_revision_numbers(frame.frame_dir)
        if (manifest := application.revision_manifest(frame.frame_dir, number)) is not None
    ]
    for manifest in manifests:
        if revision_kind_of(manifest) == "global":
            return manifest
    return manifests[0] if manifests else None


def _agreed_number(config: McpRuntimeConfig, scope: LeafScope) -> int | None:
    return agreed_revision_number(_task_folder(config, scope), scope.leaf_id)


def _revisions(
    config: McpRuntimeConfig, scope: LeafScope, application: ExplainerApplicationPorts
) -> list[dict[str, Any]]:
    root = _task_folder(config, scope)
    try:
        frame = application.frame_scope(root, scope.leaf_id, EXPLAINER_FRAME)
    except ValueError:
        return []
    agreed = _agreed_number(config, scope)
    return [
        revision_row_of(manifest, agreed=agreed)
        for number in retained_revision_numbers(frame.frame_dir)
        if (manifest := application.revision_manifest(frame.frame_dir, number)) is not None
    ]


def _selected_on_screen(
    composition: ExplainerComposition,
    scope: LeafScope,
    contract: Path | None,
    *,
    comparison_number: int | None = None,
    comparison_owner: str | None = None,
) -> FrameBinding:
    deps = _composition_deps(composition)
    application = composition.application
    if application is None:
        raise ExplainerRefused("explainer-unavailable", UNWIRED_DETAIL)
    if comparison_owner == "planned" or (comparison_number is None and deps.facts(scope).planned):
        resolved_contract = contract or _resolve_contract_for(deps.config, scope, None)
        if resolved_contract is None:
            raise ExplainerRefused(
                "planned-comparison-unavailable",
                "a planning leaf's page compares against the master line's tips, and no series "
                "contract was supplied to read them from",
            )
        record, _trees = _planned(deps.config, resolved_contract, scope, application)
        return FrameBinding.from_record(
            record,
            comparison_owner=PLANNED_OWNER,
            comparison_reference=planned_reference(scope.task, scope.leaf_id),
        )
    if comparison_number is not None:
        leaf_contract = application.recorded_leaf_contract(
            deps.config, scope.repository_id, scope.task, scope.leaf_id
        )
        if leaf_contract is not None:
            reopened = application.reopen_review_trees(
                deps.config.coordination_root, leaf_contract, comparison_number
            )
            if isinstance(reopened, ReviewTrees):
                return FrameBinding.from_record(
                    reopened.record,
                    comparison_owner=COMPARISON_OWNER,
                    comparison_reference=comparison_reference(reopened.record),
                )
    selection = LeafSelection(
        repository_id=scope.repository_id, master=scope.task, leaf_id=scope.leaf_id
    )
    captured = GitComparisonOwner(deps.config, application.watcher).capture(selection)
    if not isinstance(captured, CapturedComparison):
        detail = getattr(captured, "detail", None) or "the comparison owner captured no trees"
        raise ExplainerRefused("comparison-unavailable", str(detail))
    return FrameBinding.from_record(
        captured.record,
        comparison_owner=COMPARISON_OWNER,
        comparison_reference=comparison_reference(captured.record),
    )


def _grade_only_stale_answer(task_root: Path, leaf_id: str) -> dict[str, Any]:
    try:
        held = read_grades(grades_path(task_root, leaf_id), leaf_id=leaf_id)
    except (ExplainerRefused, ValueError) as error:
        return {"parts": [], "total": 0, "unavailable": str(error), "countsUnavailable": True}
    states = grade_staleness(held, {})
    stale_refs = stale_grade_references(states)
    empty_parts = PartStaleness(
        revision=0, target="unmeasured", parts=(), whole_page=False, checked=0
    )
    regen = regeneration_request(empty_parts, states)
    categories, grades = serialize_grades_and_categories(held, states)
    return {
        "parts": [],
        "total": 0,
        "unavailable": "the leaf has no retained revision",
        "staleGrades": list(stale_refs),
        "grades": grades,
        "categories": categories,
        "regeneration": {
            "label": regen.label,
            "message": regen.message,
            "partCount": 0,
            "gradeCount": len(stale_refs),
        },
    }


def _stale_answer(
    composition: ExplainerComposition,
    scope: LeafScope,
    manifest: FrameManifest | None,
    contract: Path | None,
    *,
    selector: tuple[int | None, str | None] = (None, None),
) -> dict[str, Any]:
    deps = _composition_deps(composition)
    application = composition.application
    if application is None:
        raise ExplainerRefused("explainer-unavailable", UNWIRED_DETAIL)
    comparison_number, comparison_owner = selector
    task_root = _task_folder(deps.config, scope)
    if manifest is None:
        return _grade_only_stale_answer(task_root, scope.leaf_id)
    try:
        held = read_grades(grades_path(task_root, scope.leaf_id), leaf_id=scope.leaf_id)
        environment = read_binding(
            leaf_workspace(deps.config, scope.repository_id, scope.task, scope.leaf_id)
        )
        explicit_selector = comparison_number is not None or comparison_owner is not None
        on_screen = (
            _selected_on_screen(
                composition,
                scope,
                contract,
                comparison_number=comparison_number,
                comparison_owner=comparison_owner,
            )
            if (explicit_selector or environment is None)
            else None
        )
        target = screen_target(
            task_root=task_root,
            on_screen=on_screen,
            environment=None if environment is None else environment.binding,
            supplemental=application.fresh_supplemental_receipts(
                task_root, scope.leaf_id, manifest.supplemental
            ),
        )
        read = staleness_read(
            task_root=task_root,
            leaf_id=scope.leaf_id,
            manifest=manifest,
            target=target,
        )
    except (ExplainerRefused, ValueError) as error:
        return {"parts": [], "total": 0, "unavailable": str(error), "countsUnavailable": True}
    parts = [
        {
            "id": part.part,
            "partId": part.part,
            "title": part.title,
            "reason": "; ".join(part.reasons),
        }
        for part in read.parts.parts
    ]
    categories, grades = serialize_grades_and_categories(held, read.grades)
    carried = [carry.as_record() for carry in read.carried_hunks]
    by_reference = {record["reference"]: record["carried"] for record in carried}
    for row in grades:
        merged = by_reference.get(str(row.get("reference") or ""))
        if merged:
            row["carried"] = merged
    return {
        "parts": parts,
        "total": len(parts),
        "wholePage": read.parts.whole_page,
        "staleGrades": list(read.stale_grades),
        "grades": grades,
        "categories": categories,
        "trusted": list(read.trusted.lines),
        "regeneration": {
            "label": read.regeneration.label,
            "message": read.regeneration.message,
            "partCount": read.regeneration.part_count,
            "gradeCount": read.regeneration.grade_count,
        },
        "moved": list(read.moved),
        "carriedHunks": carried,
    }


def _moved(
    deps: ExplainerDeps, scope: LeafScope, application: ExplainerApplicationPorts
) -> dict[str, Any] | None:
    selection = LeafSelection(scope.repository_id, scope.task, scope.leaf_id)
    notice = notice_for(deps.config, selection, application.watcher)
    if notice is None:
        move_notice_watch(deps.config, application.watcher).clear(selection)
        return None
    return notice.as_record()


def _explanation_view(manifest: FrameManifest | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    return {
        "revision": manifest.revision,
        "binding": serialize_frame_binding(manifest.binding),
        "comparison": manifest.binding.comparison_reference,
        "entrypoint": manifest.entrypoint.path,
        "artifactDigest": manifest.artifact_digest,
        "at": manifest.created_at,
        "kind": revision_kind_of(manifest),
        "focus": manifest.focus,
    }


def _query_int(request: Request, *names: str) -> int | None:
    for name in names:
        raw = request.query_params.get(name)
        if raw is None:
            continue
        cleaned = raw.strip().lstrip("#")
        if cleaned.isdecimal():
            return int(cleaned)
    return None


def _state_payload(
    composition: ExplainerComposition,
    scope: LeafScope,
    state: ExplainerState,
    contract: Path | None,
    *,
    request: Request | None = None,
) -> dict[str, Any]:
    deps = _composition_deps(composition)
    application = composition.application
    if application is None:
        raise ExplainerRefused("explainer-unavailable", UNWIRED_DETAIL)
    selected_rev = None if request is None else _query_int(request, "revision")
    comp_number = None if request is None else _query_int(request, "comparisonNumber", "comparison")
    comp_owner = (
        None
        if request is None
        else (request.query_params.get("comparisonOwner") or request.query_params.get("history"))
    )
    manifest = _manifest_for_scope(deps.config, scope, application, revision=selected_rev)
    stale = _stale_answer(
        composition, scope, manifest, contract, selector=(comp_number, comp_owner)
    )
    build_state = state.building
    active_building = (
        build_state
        if isinstance(build_state, dict) and build_state.get("state") == "building"
        else None
    )
    serialized_binding = (
        None if state.binding is None else serialize_frame_binding(state.binding.binding)
    )
    has_identity = state.present or state.generation > 0
    return {
        "state": "ok",
        "binding": serialized_binding,
        "comparisonReference": (
            None if serialized_binding is None else serialized_binding.get("reference")
        ),
        # L183-CD4: the page names the seat it shows, id and generation, not just its state. Before
        # a build records a generation both are null rather than the fields being dropped.
        "seat": {
            "state": state.seat,
            "agentId": state.agent_id or None,
            "generation": state.generation or None,
        },
        "agentId": state.agent_id if has_identity else None,
        "workspaceId": str(state.workspace) if has_identity else None,
        "workspace": str(state.workspace),
        "generation": state.generation,
        "retired": state.retired is not None,
        "retiredAt": None if state.retired is None else state.retired.get("at"),
        "explanation": _explanation_view(manifest),
        "building": active_building,
        "build": build_state,
        "stale": stale,
        "staleParts": stale.get("parts", []),
        "staleGrades": stale.get("staleGrades", []),
        "grades": stale.get("grades", []),
        "categories": stale.get("categories", []),
        "regeneration": stale.get("regeneration"),
        "parts": [] if manifest is None else manifest_parts(manifest),
        "claims": [] if manifest is None else manifest_claims(manifest),
        "moved": _moved(deps, scope, application),
        "limit": None,
        "agreedRevision": _agreed_number(deps.config, scope),
        "frameUsage": _total(deps.config, scope, application),
        "transcript": [],
        "explainer": state.as_record(),
    }


def _parse_comparison_number(body: dict[str, Any]) -> int | JSONResponse | None:
    if "comparisonNumber" in body and body["comparisonNumber"] is not None:
        raw = body["comparisonNumber"]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return _refusal(
                "invalid-request",
                "comparisonNumber is a non-negative integer",
                status=400,
                extra={"status": "invalid-request"},
            )
        return raw
    raw_comp = body.get("comparison")
    if isinstance(raw_comp, int) and not isinstance(raw_comp, bool) and raw_comp >= 0:
        return raw_comp
    if isinstance(raw_comp, str) and raw_comp.startswith("#") and raw_comp[1:].isdecimal():
        return int(raw_comp[1:])
    return None


def _send_facts(body: dict[str, Any]) -> _SendRequest | JSONResponse:
    history = body.get("history")
    if history is not None and (not isinstance(history, str) or history not in _VALID_HISTORIES):
        return _refusal(
            "invalid-request",
            "history may only be 'live', 'recorded', or 'planned'",
            status=400,
            extra={"status": "invalid-request"},
        )
    parsed_num = _parse_comparison_number(body)
    if isinstance(parsed_num, JSONResponse):
        return parsed_num
    raw_message = body.get("message")
    picks = body.get("picks")
    stale_part_ids = body.get("stalePartIds")
    stale_grades = body.get("staleGrades")
    if raw_message is None and (picks or stale_part_ids or stale_grades):
        raw_message = ""
    if not isinstance(raw_message, str) or len(raw_message) > MAX_MESSAGE:
        return _refusal("request-invalid", "a Send carries its message", status=400)
    composed = compose_explainer_send_message(raw_message, picks, stale_part_ids, stale_grades)
    if not composed.strip() or len(composed) > MAX_MESSAGE:
        return _refusal("request-invalid", "a Send carries its message", status=400)
    request_id = body.get("requestId")
    if not isinstance(request_id, str) or not request_id or len(request_id) > MAX_REQUEST_ID:
        return _refusal("request-invalid", "a Send carries its request id", status=400)
    return _SendRequest(
        message=composed,
        request_id=request_id,
        comparison_number=parsed_num,
        history=history,
        planned=bool(body.get("planned")) or history == "planned",
    )


def _receipt_id(body: dict[str, Any]) -> str | JSONResponse:
    if "receiptId" not in body or body["receiptId"] is None:
        return f"retire-{uuid.uuid4().hex[:12]}"
    receipt_id = body.get("receiptId")
    if not isinstance(receipt_id, str) or not receipt_id or len(receipt_id) > 64:
        return _refusal("request-invalid", "a Retire carries its receipt id", status=400)
    try:
        return receipt_id_segment(receipt_id)
    except PathRefused:
        return _refusal(
            "request-invalid",
            "a receipt id is one file name: no separator, no traversal, no absolute path",
            status=400,
        )


def _composition_deps(composition: ExplainerComposition) -> ExplainerDeps:
    deps = composition.deps
    if deps is None:
        raise ExplainerRefused("explainer-unavailable", UNWIRED_DETAIL)
    return deps


def _composition_config(composition: ExplainerComposition) -> McpRuntimeConfig:
    return _composition_deps(composition).config


def _application(
    composition: ExplainerComposition,
) -> ExplainerApplicationPorts | JSONResponse:
    if composition.application is None:
        return _refusal("explainer-unavailable", UNWIRED_DETAIL, status=503)
    return composition.application


def _wired(composition: ExplainerComposition) -> ExplainerDeps | JSONResponse:
    if composition.deps is None:
        return _refusal("explainer-unavailable", UNWIRED_DETAIL, status=503)
    return composition.deps


def handle_state(composition: ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    try:
        state = seat_state(deps, scope)
        return JSONResponse(
            _state_payload(
                composition,
                scope,
                state,
                composition.contract(scope),
                request=request,
            )
        )
    except (ExplainerRefused, ValueError) as error:
        return _refusal("explainer-unavailable", str(error))


def handle_stale(composition: ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    selected_rev = _query_int(request, "revision")
    comp_number = _query_int(request, "comparisonNumber", "comparison")
    comp_owner = request.query_params.get("comparisonOwner") or request.query_params.get("history")
    manifest = _manifest_for_scope(deps.config, scope, application, revision=selected_rev)
    stale = _stale_answer(
        composition,
        scope,
        manifest,
        composition.contract(scope),
        selector=(comp_number, comp_owner),
    )
    return JSONResponse({"state": "ok", "stale": stale, **stale})


async def handle_build(composition: ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    named = _send_facts(await _body(request))
    if isinstance(named, JSONResponse):
        return named
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    token = _SEND_SELECTION.set(named)
    try:
        outcome: SeatOutcome = await run_in_threadpool(
            build_seat,
            deps,
            scope,
            request_id=named.request_id,
            message=named.message,
        )
    except _ComparisonMoved as error:
        return _refusal(
            error.reason,
            error.detail,
            extra={
                "requestedComparisonNumber": error.requested,
                "currentComparisonNumber": error.current,
                "requestedComparison": error.requested,
                "currentComparison": error.current,
                "refresh": True,
            },
        )
    except ExplainerRefused as error:
        return _refusal(error.reason, error.detail)
    finally:
        _SEND_SELECTION.reset(token)
    if outcome.built or not outcome.current:
        move_notice_watch(deps.config, application.watcher).clear(LeafSelection.of(scope))
    seat_record = {**outcome.as_record(), "state": outcome.seat}
    return JSONResponse(
        {
            "state": "ok",
            "attempt": f"build:{named.request_id}",
            "seat": seat_record,
            "agentId": outcome.agent_id,
            "workspaceId": str(outcome.workspace),
            "generation": outcome.generation,
            "binding": serialize_frame_binding(outcome.binding),
        }
    )


def handle_update(composition: ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    try:
        update = update_seat(deps, scope)
    except ExplainerRefused as error:
        return _refusal(error.reason, error.detail)
    move_notice_watch(deps.config, application.watcher).clear(LeafSelection.of(scope))
    counts = dict(update.counts())
    unavailable = update.counts_unavailable
    if unavailable is not None or update.blocks_changed is None:
        counts["countsUnavailable"] = True
    update_record = dict(update.as_record())
    update_record["counts"] = counts
    if unavailable is not None or update.blocks_changed is None:
        update_record["countsUnavailable"] = True
    if unavailable is not None:
        update_record["unavailable"] = unavailable
    return JSONResponse(
        {
            "state": "ok",
            "update": update_record,
            "counts": counts,
            "attempt": f"update:{update.previous_number}",
        }
    )


async def handle_retire(composition: ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    receipt_id = _receipt_id(await _body(request))
    if isinstance(receipt_id, JSONResponse):
        return receipt_id
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    try:
        outcome = retire_seat(deps, scope, receipt_id=receipt_id)
    except ExplainerRefused as error:
        return _refusal(error.reason, error.detail)
    move_notice_watch(deps.config, application.watcher).clear(LeafSelection.of(scope))
    record = outcome.as_record()
    receipt = record.get("receipt")
    if isinstance(receipt, dict) and "receipt_id" in receipt:
        receipt["receiptId"] = receipt["receipt_id"]
    return JSONResponse({"state": "ok", "retire": record, "receipt": receipt})


async def handle_cancel(composition: ExplainerComposition, request: Request) -> JSONResponse:
    """The developer's cancel (MIK-R105 v1 rule 9): the exact seat and request, nothing else.

    Cancellation names whether a turn stopped, publication was prevented, or a request remains
    uncertain. It measures the frame store's ready revision around the host's named cancel, so a
    turn that raced publication is reported truthfully, and it keeps the last ready revision.
    """

    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    body = await _body(request)
    request_id = body.get("requestId")
    if not isinstance(request_id, str) or not request_id or len(request_id) > MAX_REQUEST_ID:
        return _refusal("request-invalid", "a cancel carries its request id", status=400)
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    task_root = _task_folder(deps.config, scope)
    before = page_revision(task_root, scope.leaf_id)
    try:
        outcome: CancelOutcome = await run_in_threadpool(
            cancel_seat, deps, scope, request_id=request_id
        )
    except ExplainerRefused as error:
        return _refusal(error.reason, error.detail)
    after = page_revision(task_root, scope.leaf_id)
    record = outcome.as_record()
    record["publicationPrevented"] = before == after
    record["revision"] = after
    return JSONResponse({"state": "ok", "cancel": record})


def handle_revisions(composition: ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    return JSONResponse(
        {
            "state": "ok",
            "revisions": _revisions(deps.config, scope, application),
            "pinned": _agreed_number(deps.config, scope),
            "total": _total(deps.config, scope, application),
        }
    )


def _total(
    config: McpRuntimeConfig, scope: LeafScope, application: ExplainerApplicationPorts
) -> dict[str, Any]:
    return application.task_frames_total(_task_folder(config, scope), scope.leaf_id)


async def handle_remove_revisions(
    composition: ExplainerComposition, request: Request
) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    body = await _body(request)
    named = body.get("revisions")
    if (
        not isinstance(named, list)
        or not named
        or len(named) > MAX_REMOVE_BATCH
        or any(isinstance(number, bool) or not isinstance(number, int) for number in named)
    ):
        return _refusal("request-invalid", "a removal names the revisions to remove", status=400)
    try:
        frame = application.frame_scope(
            _task_folder(deps.config, scope), scope.leaf_id, EXPLAINER_FRAME
        )
        removal = application.remove_revisions(frame, revisions=tuple(named))
    except application.frame_refused_cls as error:
        return _refusal(getattr(error, "reason", None) or "revision-removal-refused", str(error))
    except ValueError as error:
        return _refusal("revision-removal-refused", str(error))
    return JSONResponse(
        {
            "state": "ok",
            "removed": list(removal.removed),
            "remaining": len(removal.retained),
            "pinned": removal.pinned,
            "bytesFreed": removal.bytes_freed,
            "detail": removal.detail,
        }
    )


def handle_planned(composition: ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = _application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = _scope(request)
    try:
        if not deps.facts(scope).planned:
            raise ExplainerRefused(
                "not-a-planning-leaf",
                f"leaf {scope.leaf_id} is not planning; its page shows the comparison it is bound to",
            )
        contract = composition.contract(scope)
        if contract is None:
            raise ExplainerRefused(
                "planned-comparison-unavailable",
                f"leaf {scope.leaf_id} has no enclosure contract to read the master line's tips "
                "from; the planned comparison is built from the leaf's own contract",
            )
        record, _trees = _planned(deps.config, contract, scope, application)
        task_root = _task_folder(deps.config, scope)
        document = leaf_document(task_root, scope.leaf_id)
        if document is None:
            raise ExplainerRefused(
                "leaf-unavailable", f"no task document names leaf {scope.leaf_id} in {task_root}"
            )
        payload = planned_payload(
            document=document,
            comparison=record,
            task_root=task_root,
            coordination_root=Path(deps.config.coordination_root),
            code_repository=repository_path(deps.config, scope.repository_id),
        )
    except (ExplainerRefused, ValueError) as error:
        return _refusal("planned-unavailable", str(error))
    return JSONResponse({"state": "ok", "planned": payload})


def register_explainer_routes(app: FastAPI, composition: ExplainerComposition) -> None:
    base = EXPLAINER_API_PREFIX + "/{repository_id}/{master}/{leaf_id}"
    for suffix, handler, method in (
        ("/state", handle_state, "GET"),
        ("/stale", handle_stale, "GET"),
        ("/build", handle_build, "POST"),
        ("/send", handle_build, "POST"),
        ("/update", handle_update, "POST"),
        ("/revisions", handle_revisions, "GET"),
        ("/remove-revisions", handle_remove_revisions, "POST"),
        ("/retire", handle_retire, "POST"),
        ("/cancel", handle_cancel, "POST"),
        ("/planned", handle_planned, "GET"),
    ):
        app.add_api_route(f"{base}{suffix}", partial(handler, composition), methods=[method])
