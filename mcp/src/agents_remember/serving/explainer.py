"""Compose explainer dependencies and assemble reads bound to the selected comparison.

The HTTP handlers live in :mod:`agents_remember.serving.explainer_routes`. Reads here start
no model (MIK-R106 v2 rule 10).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse

from agents_remember.canvas.explainer_capsule import capability_text
from agents_remember.canvas.explainer_grades import grades_path, read_grades
from agents_remember.canvas.explainer_paths import (
    EXPLAINER_FRAME,
    leaf_workspace,
)
from agents_remember.canvas.explainer_reopen import absent_task_refusal
from agents_remember.canvas.explainer_service import (
    BoundComparison,
    ExplainerDeps,
    ExplainerHost,
    ExplainerRefused,
    ExplainerState,
    LeafFacts,
    LeafScope,
    PreparedPacket,
)
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    PartStaleness,
    grade_staleness,
    regeneration_request,
    stale_grade_references,
)
from agents_remember.canvas.explainer_staleness_read import screen_target, staleness_read
from agents_remember.canvas.explainer_state import read_binding
from agents_remember.canvas.explainer_target import ExplainerTargetReads
from agents_remember.canvas.paths import TASK_SEGMENT, PathRefused, path_segment
from agents_remember.canvas.paths import task_root as leaf_task_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_evidence import EvidenceMap, SupplementalReceipt
from agents_remember.models.knowledge.review_frame import (
    COMPARISON_OWNER,
    PLANNED_OWNER,
    FrameBinding,
    FrameManifest,
    comparison_reference,
    coverage_summary,
    planned_reference,
)
from agents_remember.models.knowledge.review_refusal import ReviewRefusal
from agents_remember.models.knowledge.review_trees import (
    ReviewCodeSide,
    ReviewTreeComparisonRecord,
    ReviewTrees,
)
from agents_remember.serving.explainer_comparison import (
    _SEND_SELECTION,
    ComparisonPreparationPort,
    _comparison,
    _ComparisonMoved,
    _planned,
    _recorded,
    _resolve_contract_for,
    _SendRequest,
)
from agents_remember.serving.explainer_move_watch import move_notice_watch
from agents_remember.serving.explainer_watcher import (
    CapturedComparison,
    GitComparisonOwner,
    LeafSelection,
    WatcherApplicationPorts,
    blocks_changed,
    notice_for,
)
from agents_remember.serving.review_trees import ReviewTreesPort
from agents_remember.tasks.planned_comparison import (
    agreed_revision_number,
    leaf_document,
    leaf_document_stem,
    manifest_claims,
    manifest_parts,
    resolve_master_contract,
    retained_revision_numbers,
    revision_kind_of,
    revision_row_of,
    serialize_frame_binding,
    serialize_grades_and_categories,
)
from agents_remember.tasks.task_paths import CLEANUP_REPORT_NAME
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
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
    "_SEND_SELECTION",
    "ExplainerApplicationPorts",
    "ExplainerComposition",
    "_ComparisonMoved",
    "_SendRequest",
    "_planned",
    "_recorded",
    "_resolve_contract_for",
    "explainer_composition",
    "leaf_document",
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


#: Keep one staleness answer by its question while every read it made holds (D570): the question,
#: the computation, and which answers are such a function at all.
type StaleAnswersPort = Callable[
    [tuple[object, ...], Callable[[], dict[str, Any]], Callable[[dict[str, Any]], bool]],
    dict[str, Any],
]


@dataclass(frozen=True)
class ExplainerComposition:
    """The seat service the routes call, the series contract, and whether it is wired at all."""

    deps: ExplainerDeps | None = None
    contract_reader: Callable[[LeafScope], Path | None] | None = None
    application: ExplainerApplicationPorts | None = None
    review_trees: ReviewTreesPort | None = None
    task_root_for: Callable[[LeafScope], Path] | None = None
    stale_answers: StaleAnswersPort | None = None

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
    # The lexical check first: a root under 0_archive/ is archived, and nothing in it is opened.
    if "0_archive" in task_root.parts:
        return True
    return (task_root / "notes" / "reports" / "master-retirement.json").is_file()


def _explainer_task_root(config: McpRuntimeConfig, scope: LeafScope) -> Path:
    """The default resolver, as the explainer's build and send read it.

    A master with no folder at ``tasks/<repository>/<master>`` is refused with the plain fact and
    the place a reader can check if it was archived (R103 v2). Nothing under ``0_archive`` is
    looked at; the shared resolver keeps its own text for every other caller.
    """

    master = path_segment(scope.task, name="task")
    try:
        return leaf_task_root(config, scope.repository_id, master)
    except PathRefused as refused:
        tasks = config.coordination_root / TASK_SEGMENT / scope.repository_id
        raise ExplainerRefused(
            "task-unavailable", absent_task_refusal(tasks, scope.repository_id, master)
        ) from refused


def _facts(task_root_for: Callable[[LeafScope], Path]) -> Callable[[LeafScope], LeafFacts]:
    def read(scope: LeafScope) -> LeafFacts:
        root = task_root_for(scope)
        if _master_archived(root):
            # Refused by name before the leaf's document is read: the archive's receipt is named
            # by where the archive files it, and nothing under the archived task is opened.
            return LeafFacts(
                status="",
                archived=True,
                archive_receipt=durable_reports_root(root) / CLEANUP_REPORT_NAME,
            )
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
            archived=False,
            agreed_revision=agreed_revision,
        )

    return read


@dataclass(frozen=True)
class _ReadPlumbing:
    """The four values a packet read composes: config, owner, contract and application ports."""

    config: McpRuntimeConfig
    review_trees: ReviewTreesPort
    contract_for: Callable[[LeafScope], Path | None] | None
    application: ExplainerApplicationPorts
    task_root_for: Callable[[LeafScope], Path]


def _prepared_trees(bound: BoundComparison) -> ReviewTrees:
    if not isinstance(bound.record, ReviewTrees):
        raise ExplainerRefused(
            "evidence-source-unavailable", "the bound comparison has no prepared trees"
        )
    if bound.witness is None or not bound.witness.current():
        raise ExplainerRefused("binding-moved", "comparison inputs moved before packet preparation")
    return bound.record


def _prepare(
    plumbing: _ReadPlumbing,
) -> Callable[[LeafScope, LeafFacts, BoundComparison], PreparedPacket]:
    def read(scope: LeafScope, _facts: LeafFacts, bound: BoundComparison) -> PreparedPacket:
        application = plumbing.application
        trees = _prepared_trees(bound)
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
    comparison_prepare: ComparisonPreparationPort
    contract_for: Callable[[LeafScope], Path | None] | None = None
    task_root_for: Callable[[LeafScope], Path] | None = None
    stale_answers: StaleAnswersPort | None = None


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
    # Only the build and send path's facts read the explainer's own resolver; every other reader
    # keeps the shared resolver's refusal.
    facts_root = wiring.task_root_for or partial(_explainer_task_root, config)
    composition = ExplainerComposition(
        application=application,
        review_trees=review_trees,
        task_root_for=resolve,
        contract_reader=wiring.contract_for,
        stale_answers=wiring.stale_answers,
    )
    deps = ExplainerDeps(
        config=config,
        host=wiring.host,
        facts=_facts(facts_root),
        comparison=_comparison(config, wiring.comparison_prepare, wiring.contract_for, application),
        recheck=lambda scope, facts, bound: (
            _facts(facts_root)(scope) == facts
            and bound.witness is not None
            and bound.witness.current()
        ),
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


def stale_view(
    composition: ExplainerComposition,
    scope: LeafScope,
    *,
    revision: int | None = None,
    selector: tuple[int | None, str | None] = (None, None),
) -> dict[str, Any]:
    """The staleness of one shown revision against one comparison, and that revision's view.

    The top bar's badge reads it, and the background build computes the page's own question with
    it (D569), so the two derive one key here. An answer for a named recorded comparison is kept
    on every read it made (D570); a planned or unnamed comparison reads the live master line or
    the environment's facts, and is computed every time, as is an answer that found no revision
    or could not be read.
    """

    deps = _composition_deps(composition)
    application = composition.application
    if application is None:
        raise ExplainerRefused("explainer-unavailable", UNWIRED_DETAIL)
    number, owner = selector
    kept = composition.stale_answers is not None and number is not None and owner == "recorded"

    def produce() -> dict[str, Any]:
        manifest = _manifest_for_scope(deps.config, scope, application, revision=revision)
        # Only a planned comparison is compared against the series contract's tips.
        contract = None if kept else composition.contract(scope)
        stale = _stale_answer(composition, scope, manifest, contract, selector=selector)
        return {"stale": stale, "explanation": _explanation_view(manifest)}

    if not kept or composition.stale_answers is None:
        return produce()
    question = (scope.repository_id, scope.task, scope.leaf_id, revision, number, owner)
    return composition.stale_answers(question, produce, _keepable_stale)


def _keepable_stale(view: dict[str, Any]) -> bool:
    return view["explanation"] is not None and "unavailable" not in view["stale"]


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
        "coverage": coverage_summary(manifest.interaction_coverage),
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
        "explainer": _labelled_for_the_revision(state.as_record(), manifest),
    }


def _labelled_for_the_revision(
    record: dict[str, Any], manifest: FrameManifest | None
) -> dict[str, Any]:
    """The label names the comparison of the revision on screen, not the live binding's.

    The page shows one retained revision and the explanation was written against that revision's own
    comparison, so that is the number it carries. The live binding's newer comparison is the reason
    the page is stale, and it reaches the reader through the staleness record and the top bar's
    badge, never through this label.
    """

    if manifest is None or manifest.binding.comparison_number is None:
        return record
    return {**record, "label": f"#{manifest.binding.comparison_number}"}


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


def _total(
    config: McpRuntimeConfig, scope: LeafScope, application: ExplainerApplicationPorts
) -> dict[str, Any]:
    return application.task_frames_total(_task_folder(config, scope), scope.leaf_id)
