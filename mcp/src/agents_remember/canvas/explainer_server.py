"""The explainer's own tools: render, and the three grade writes (MIK-R211 v5 rule 3).

The explainer works in a normal workspace with its harness's own tools and AR's general tools; this
module holds what no harness tool can do (D603, D607):

* ``render`` -- the draft loaded in a model-free browser, its run kept in ``renders/run-NNN/``, and a
  ready page published into the task as its next revision;
* MIK-R187 rule 2's ``define_category``, ``remove_category`` and ``grade``, written through the one
  grade store under the leaf's lock alone.

Only the leaf's explainer may grade. The caller identity is read from this server's own environment at
construction (``PASEO_AGENT_ID``), never from a tool argument, and it must be the id of the leaf's
current generation (MIK-R186 rule 5). A grade carries the binding it was made at and is refused
``binding-moved`` when the environment has left it, checked again under the lock. No answer carries
the binding itself: ``binding.json`` lies in the workspace (MIK-R211 v5 rule 11).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.application.review_frame_store import (
    READY_POINTER,
    FrameRefused,
    begin_draft,
    frame_scope,
    frames_area,
    publish_revision,
    read_draft,
)
from agents_remember.canvas.explainer_access import (
    require_binding,
    require_claim,
    require_seat,
)
from agents_remember.canvas.explainer_answer_bounds import (
    GRADE_REFUSED_INLINE_TOKENS,
    GRADES_FOLDER,
    answer_tokens,
    write_scratch,
)
from agents_remember.canvas.explainer_claims import with_authored_claims
from agents_remember.canvas.explainer_grades import (
    MAX_GRADE_CHARACTERS,
    GradeStore,
    ScalePoint,
    grades_path,
)
from agents_remember.canvas.explainer_inputs import (  # re-exported: __all__ names ExplainerInputs
    ExplainerInputs,
    now_iso,
)
from agents_remember.canvas.explainer_paths import (
    BINDING_NAME,
    DRAFTS_DIRECTORY,
    EXPLAINER_FRAME,
    INSTRUCTIONS_DIRECTORY,
    LOCK_RESOURCE,
    RENDERS_DIRECTORY,
    REVISIONS_DIRECTORY,
    copy_revision,
    leaf_lock_resource,
)
from agents_remember.canvas.explainer_state import (
    BUSY,
    ExplainerBinding,
    ExplainerRefused,
    read_binding,
)
from agents_remember.canvas.explainer_target import ExplainerTargetReads
from agents_remember.canvas.presenter_assets import collect_assets
from agents_remember.canvas.presenter_files import ensure_child, open_binary
from agents_remember.canvas.presenter_store import (
    digest_bytes,
    read_whole,
)
from agents_remember.canvas.readiness import check_page

if TYPE_CHECKING:  # annotation-only: the authoring class loads when a tool is actually called
    from agents_remember.canvas.presenter_tools import AuthoringTools
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, exclusive_file_lock
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import (
    FrameAsset,
    FrameAuthoring,
    FrameBinding,
    FrameInteractionCoverage,
    FrameLimits,
    FramePart,
    FramePassCoverage,
    artifacts_digest,
    coverage_summary,
)

__all__ = [
    "ExplainerInputs",
    "ExplainerJudgment",
    "explainer_authoring",
    "page_revision",
    "render_and_publish",
]

#: L89's per-block fingerprint, handed in by the product (MIK-R187 rule 3).
FingerprintReader = Callable[[tuple[str, ...]], Mapping[str, str]]


@dataclass(frozen=True)
class ExplainerJudgment:
    """``define_category``, ``remove_category`` and ``grade``, written under the leaf's lock.

    The store is the one :class:`GradeStore` file per leaf beside ``review-frames``, so it survives
    Retire. Every call carries the binding it was made at and is refused ``binding-moved`` when the
    environment has left it, and the check is made again under the lock because an Update replaces
    the binding last. A grade needs the fingerprint MIK-R187 rule 3 defines for the block's kind,
    which is L89's machinery, handed in at construction; without one a reference is refused rather
    than graded with an invented fingerprint, which could never be marked stale.
    """

    workspace: Path
    task_root: Path | None
    seat: str | None
    fingerprints: FingerprintReader | None
    now: Callable[[], str]
    _initial_binding: ExplainerBinding | None = None

    def __post_init__(self) -> None:
        try:
            current = read_binding(self.workspace)
        except Exception:
            current = None
        object.__setattr__(self, "_initial_binding", current)

    def define(
        self,
        name: str,
        scale: Sequence[Mapping[str, str]],
        claim: str,
        meaning: str = "",
    ) -> dict[str, Any]:
        """Define one category: 2 to 5 named grades, highest first, each with its meaning.

        The answer is the category and its grade names in order: the next ``grade`` call uses
        exactly these names (MIK-R211 v5 rule 11).
        """

        require_seat(self.workspace, self.seat)
        points = _scale(scale)
        if not name or len(name) > MAX_GRADE_CHARACTERS:
            raise ExplainerRefused(
                "category-name", f"a category name is 1 to {MAX_GRADE_CHARACTERS} characters"
            )
        with self._held(claim):
            store = self._store()
            before = store.load()
            after = store.define(name, points, meaning=meaning)
        return {
            "state": "defined" if after != before else "unchanged",
            "category": name,
            "grades": [point.grade for point in points],
        }

    def remove(self, name: str, claim: str) -> dict[str, Any]:
        """Remove one category and, with it, its grades; the answer confirms what went."""

        require_seat(self.workspace, self.seat)
        with self._held(claim):
            store = self._store()
            before = store.load()
            after = store.remove(name)
        return {
            "state": "removed" if after != before else "unchanged",
            "category": name,
            "gradesRemoved": len(before.grades) - len(after.grades),
        }

    def grade(
        self, references: Sequence[str], category: str, grade: str, reason: str, claim: str
    ) -> dict[str, Any]:
        """Grade blocks; answer how many landed and which were refused, with their reasons.

        Each refused reference and its reason stay in the answer up to about 300 tokens; past that,
        the answer carries the total and the file in ``scratch/grades/`` that holds every one
        (MIK-R211 v5 rule 11): the explainer has to know which grades failed to fix them.
        """

        require_seat(self.workspace, self.seat)
        named = tuple(references)
        if not named:
            raise ExplainerRefused("grade-empty", "a grade names at least one block")
        with self._held(claim) as binding:
            store = self._store()
            _, refused = store.grade(
                references=named,
                category=category,
                grade=grade,
                reason=reason,
                fingerprints=self._fingerprints(named, binding),
                binding=binding.binding,
                at=self.now(),
            )
        if not refused:
            return {"state": "graded", "graded": len(named), "refused": 0}
        entries = [
            {
                "reference": reference,
                "reason": "the bound comparison has no fingerprint for this block",
            }
            for reference in refused
        ]
        answer: dict[str, Any] = {
            "state": "not-graded",
            "refusal": "fingerprint-unavailable",
            "graded": 0,
            "refused": len(entries),
            "detail": "nothing was written: a grade without a fingerprint could not go stale",
        }
        if answer_tokens({"refusedReferences": entries}) <= GRADE_REFUSED_INLINE_TOKENS:
            return {**answer, "refusedReferences": entries}
        path = write_scratch(self.workspace, GRADES_FOLDER, "refused", entries)
        return {**answer, "refusedFile": path}

    @contextmanager
    def _leaf_lock(self) -> Iterator[None]:
        """The leaf's one lock: the same file a build, an Update and a Retire hold (MIK-R187 rule 6).

        The grade store lies with the task's records, so a seat that writes it while a build or an Update
        rewrites the same file would lose one side's categories. The seat therefore takes the leaf's lock --
        `<workspace>/state/leaf`, the resource the service's build, Update and Retire hold (MIK-R211 v5
        rule 12) -- and only then the environment's own lock for the binding it must re-read. It waits,
        because a build is short and a lost category is not.
        """

        if self.task_root is None:
            yield
            return
        path = leaf_lock_resource(self.workspace)
        try:
            with exclusive_file_lock(path, LOCK_RESOURCE):
                yield
        except LockAcquisitionTimeout as error:
            raise ExplainerRefused(
                BUSY, f"{error}: the leaf is building or updating, so this grade write waits"
            ) from error

    @contextmanager
    def _held(self, claim: str) -> Iterator[ExplainerBinding]:
        """Take the leaf lock alone, then re-read the binding and the claim under it.

        The binding is read once before the lock too: a removed environment has none, and its refusal
        must come before the lock file is made, or a late call would leave ``state/`` behind.
        """

        require_binding(self.workspace)
        with self._leaf_lock():
            binding = require_binding(self.workspace)
            require_claim(
                claim,
                binding,
                prior=self._initial_binding,
                held_at=self.workspace / BINDING_NAME,
            )
            if (
                self._initial_binding is None
                or self._initial_binding.binding.comparison_number
                != binding.binding.comparison_number
            ):
                object.__setattr__(self, "_initial_binding", binding)
            yield binding

    def _store(self) -> GradeStore:
        if self.task_root is None:
            raise ExplainerRefused(
                "grades-unavailable",
                "the grade store lies with the task's records, and this server was built without "
                "the task folder",
            )
        leaf = require_binding(self.workspace).binding.leaf_id
        return GradeStore(path=grades_path(self.task_root, leaf), leaf_id=leaf)

    def _fingerprints(
        self, references: tuple[str, ...], binding: ExplainerBinding | None = None
    ) -> dict[str, str]:
        if self.fingerprints is not None:
            return dict(self.fingerprints(references))
        current = binding if binding is not None else require_binding(self.workspace)
        reads = ExplainerTargetReads(binding=current.binding, task_root=self.task_root)
        return reads.resolve_fingerprints(references)


def _scale(points: Sequence[Mapping[str, str]]) -> tuple[ScalePoint, ...]:
    """One category's scale, refusing a point that names no grade, no meaning, or exceeds bounds."""

    built: list[ScalePoint] = []
    for point in points:
        grade = str(point.get("grade", "")).strip()
        meaning = str(point.get("meaning", "")).strip()
        if not grade or not meaning:
            raise ExplainerRefused(
                "category-scale", "each point of a scale names its grade and its meaning"
            )
        if len(grade) > MAX_GRADE_CHARACTERS:
            raise ExplainerRefused(
                "category-scale",
                f"a grade's name is a label of 1 to {MAX_GRADE_CHARACTERS} characters",
            )
        built.append(ScalePoint(grade=grade, meaning=meaning))
    return tuple(built)


def _json_document(path: Path) -> Any:
    """One JSON control file, parsed from its open handle with no size bound (MIK-R211 v4 rule 3).

    A record, a grant manifest or a page pointer is the product's own bookkeeping; its size never
    decides an answer, so a large one is read to its end rather than refused or treated as absent.
    """

    with open_binary(path) as handle:
        return json.load(handle)


def page_revision(task_root: Path | None, leaf: str) -> int | None:
    """The highest ready page revision the leaf holds, or ``None`` when it has no page."""

    if task_root is None:
        return None
    area = frames_area(task_root, leaf)
    if not area.is_dir():
        return None
    highest: int | None = None
    for frame in sorted(area.iterdir()):
        pointer = frame / READY_POINTER
        if pointer.is_symlink() or not pointer.is_file():
            continue
        try:
            payload = _json_document(pointer)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        value = payload.get("revision") if isinstance(payload, dict) else None
        if isinstance(value, int) and (highest is None or value > highest):
            highest = value
    return highest


def explainer_authoring(workspace: Path) -> AuthoringTools:
    """Render over ``drafts/``, ``instructions/`` and ``renders/``, re-read on every call.

    The environment keeps the grants and the packet in ``instructions/``, the page draft in ``drafts/``
    and each render's run in ``renders/``; the input digests are taken from the files as they are now,
    which is what lets an Update's new packet be read without a restart.
    """

    # MIK-R204 @ v1 rule 1: the class is resolved here, on the call, so declaring render does not
    # load `canvas.presenter_tools`.
    authoring = import_module("agents_remember.canvas.presenter_tools").AuthoringTools
    instructions = workspace / INSTRUCTIONS_DIRECTORY
    drafts = workspace / DRAFTS_DIRECTORY
    try:
        manifest = _json_document(instructions / "grants.json")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplainerRefused(
            "grants-unreadable", f"the environment's grants cannot be read: {error}"
        ) from error
    if not isinstance(manifest, dict):
        raise ExplainerRefused("grants-unreadable", "the environment's grants are not an object")
    ensure_child(drafts, "assets")
    return authoring(
        drafts,
        host=instructions,
        grants=_granted(manifest, instructions, drafts),
        renders=workspace / RENDERS_DIRECTORY,
    )


def _granted(manifest: Mapping[str, Any], instructions: Path, drafts: Path) -> dict[str, Any]:
    """The grants the authoring tools check: this working folder and these input digests."""

    inputs: dict[str, str] = {}
    for key in ("evidence", "assignment"):
        name = manifest.get(key)
        if not isinstance(name, str) or not name:
            continue
        # No byte cap: the packet and the assignment are the product's own files in this folder,
        # and a bound here is what refused a 647,007-byte packet its own reader (6085456d).
        body = read_whole(instructions / name, reason="input-unreadable", detail=name)
        inputs[name] = digest_bytes(body)
    return {**dict(manifest), "working": str(drafts), "inputs": inputs}


def _binding_for_publish(workspace: Path, draft_binding: FrameBinding) -> FrameBinding:
    """Re-read ``binding.json`` under ``.frames.lock`` alone for publication admission."""

    env_binding = require_binding(workspace)
    frame = env_binding.binding
    if env_binding.planned is not None:
        return frame.model_copy(
            update={
                "code_base_commit": env_binding.planned.code_tip,
                "code_candidate_commit": env_binding.planned.code_tip,
                "memory_base_commit": env_binding.planned.memory_tip,
                "memory_candidate_commit": env_binding.planned.memory_tip,
                "code_base_tree": env_binding.planned.code_tree,
                "code_candidate_tree": env_binding.planned.code_tree,
                "memory_base_tree": env_binding.planned.memory_tree,
                "memory_candidate_tree": env_binding.planned.memory_tree,
            }
        )
    if (
        frame.comparison_owner != "planned"
        and draft_binding.comparison_owner != "planned"
        and frame.comparison_reference == draft_binding.comparison_reference
        and frame.comparison_number == draft_binding.comparison_number
    ):
        return frame.model_copy(update={"comparison_owner": draft_binding.comparison_owner})
    return frame


def _draft_entries(assets: Mapping[str, bytes]) -> tuple[FrameAsset, ...]:
    return tuple(
        FrameAsset(
            path=path,
            media_type="text/html" if path.endswith(".html") else "application/octet-stream",
            byte_size=len(payload),
            digest=hashlib.sha256(payload).hexdigest(),
        )
        for path, payload in sorted(assets.items())
    )


def render_and_publish(
    workspace: Path,
    *,
    task_root: Path | None = None,
    seat: str | None = None,
    now: Callable[[], str] | None = None,
    binding_callback: Callable[[], FrameBinding | None] | None = None,
) -> dict[str, Any]:
    """Render the draft in ``drafts/`` and, when ready, publish a revision under ``.frames.lock``.

    Whatever the outcome, the run's whole report -- every error, check, pass and control -- is
    filed in the run's own folder ``renders/run-NNN/`` beside its pictures (D597, MIK-R211 v5).
    Its error list and its summary are computed here once and written into that report, so a reply
    that quotes them can never disagree with the file.
    """

    authoring = explainer_authoring(workspace)
    report = _render_and_publish(
        workspace, task_root=task_root, seat=seat, now=now, binding=binding_callback
    )
    report["allErrors"] = run_errors(report)
    report["summary"] = run_summary(report)
    authoring.keep_report(report)
    return report


def run_errors(report: Mapping[str, Any]) -> list[str]:
    """Every error one render observed, each whole and named by where it came from."""

    found: list[str] = [
        f"readiness {item.get('check')}: {item.get('detail')}"
        for item in (report.get("readiness") or {}).get("failed", [])
    ]
    found += [f"load: {error}" for error in report.get("errors") or []]
    if report.get("pictureError"):
        found.append(f"picture: {report['pictureError']}")
    interactive = report.get("interactive") or {}
    found += [f"interaction: {error}" for error in interactive.get("errors") or []]
    found += [f"picture: {error}" for error in interactive.get("pictureErrors") or []]
    for one in interactive.get("passes") or []:
        name = f"{one.get('width')}-{one.get('method')}" + (
            "" if one.get("effects") else "-effects-off"
        )
        found += [f"pass {name}: {error}" for error in one.get("errors") or []]
        found += [
            f"pass {name}: control {control.get('index')}: {control.get('error')}"
            for control in one.get("controls") or []
            if control.get("status") == "error"
        ]
    publication = report.get("publication") or {}
    if publication.get("state") == "refused":
        found.append(f"publication {publication.get('refusal')}: {publication.get('detail')}")
    return found


def run_summary(report: Mapping[str, Any]) -> dict[str, int]:
    """The run's counts, read from the report's own fields and its own error list (D597)."""

    passes = (report.get("interactive") or {}).get("passes") or []
    controls = [control for one in passes for control in one.get("controls") or []]
    checks = (report.get("readiness") or {}).get("checks") or {}
    return {
        "passes": len(passes),
        "passesIncomplete": len((report.get("interactive") or {}).get("unvisited") or []),
        "controlsVisited": sum(control.get("status") != "unvisited" for control in controls),
        "controlsFailed": sum(control.get("status") == "error" for control in controls),
        "checksPassed": int(checks.get("passed", 0)),
        "checksFailed": int(checks.get("failed", 0)),
        "errors": len(report.get("allErrors") or []),
    }


def _render_and_publish(
    workspace: Path,
    *,
    task_root: Path | None,
    seat: str | None,
    now: Callable[[], str] | None,
    binding: Callable[[], FrameBinding | None] | None,
) -> dict[str, Any]:
    authoring = explainer_authoring(workspace)
    drafts = workspace / DRAFTS_DIRECTORY
    limits = FrameLimits()
    assets = collect_assets(drafts, limits)
    packet = with_authored_claims(
        EvidencePacket.model_validate_json(authoring.read("evidence.json")), assets
    )
    report = authoring.render(packet=packet, assets=assets)
    readiness = check_page(assets, entrypoint="index.html", packet=packet)
    report["readiness"] = {
        **readiness.diagnostics(),
        "checks": {
            "passed": len(readiness.checks) - len(readiness.failures),
            "failed": len(readiness.failures),
        },
    }
    if task_root is None or not readiness.ready or report.get("state") != "loaded":
        return report
    part_map = (
        ()
        if readiness.part_map is None or readiness.part_map.implicit
        else tuple(
            FramePart(part=entry.part_id, title=entry.title, claims=entry.claims)
            for entry in readiness.part_map.parts
        )
    )
    moment = (now or now_iso)()
    digest = artifacts_digest(_draft_entries(assets))
    coverage = interaction_coverage(report.get("interactive"))
    # The request names the page's bytes and the coverage it is published with: a resumed run that
    # reaches more is a successor revision that says so, never the published one patched (15:43Z).
    request_id = (
        f"explainer-{digest[:16]}-{packet.comparison.comparison_number}-{_coverage_key(coverage)}"
    )
    scope = frame_scope(task_root, packet.comparison.leaf_id, EXPLAINER_FRAME)
    draft_binding = FrameBinding.from_packet(packet.comparison)
    source_cb = binding or (lambda: _binding_for_publish(workspace, draft_binding))
    # The request names the page's bytes, so rendering the same page again (to resume its interaction
    # run, or to read it again) is the same draft: its first authoring is kept, never refused. The
    # request is content identity, not session identity: a reopened seat (a new session) that renders
    # the same bytes is the same request too, answered unchanged. begin_draft still refuses a request
    # whose focus, assets or folder differ.
    earlier = read_draft(scope, request_id)
    try:
        begin_draft(
            scope,
            request_id=request_id,
            authoring=(
                earlier.authoring
                if earlier is not None
                else FrameAuthoring(
                    request_id=request_id,
                    session_id=seat or None,
                    started_at=moment,
                    finished_at=moment,
                )
            ),
            focus=packet.scope,
            assets=assets,
            working_directory=drafts.resolve(),
            limits=limits,
            now=moment,
        )
        publication = publish_revision(
            scope,
            request_id=request_id,
            entrypoint="index.html",
            evidence=packet,
            supplemental=packet.supplemental,
            created_at=moment,
            limits=limits,
            part_map=part_map,
            binding_source=source_cb,
            interaction_coverage=coverage,
        )
    except (FrameRefused, ExplainerRefused) as error:
        reason = getattr(error, "reason", None) or "publication-refused"
        detail = getattr(error, "detail", str(error))
        report["publication"] = {"state": "refused", "refusal": reason, "detail": detail}
        if reason == "binding-moved":
            report["state"] = "refused"
            report["refusal"] = reason
            report["detail"] = detail
        return report
    manifest = publication.manifest
    report["publication"] = {
        "state": "published" if publication.published else "unchanged",
        "revision": None if manifest is None else manifest.revision,
        "binding": None if manifest is None else manifest.binding.comparison_reference,
        "coverage": coverage_summary(
            coverage if manifest is None else manifest.interaction_coverage
        ),
        "partMap": [part.model_dump(mode="json") for part in part_map],
    }
    if publication.published and manifest is not None:
        # The workspace's revisions/ states what is published: the new revision is copied in from
        # the task at once, never left for the next build to find (the Manager's 16:40Z ruling).
        number = manifest.revision
        copy_revision(scope.frame_dir / REVISIONS_DIRECTORY / str(number), workspace, number)
        report["publication"]["workspace"] = (
            f"refreshed {REVISIONS_DIRECTORY}/{number}/ from the task's published revision {number}"
        )
    return report


def interaction_coverage(interactive: Mapping[str, Any] | None) -> FrameInteractionCoverage:
    """What one render's interaction observation reached, as the revision states it (MIK-R211 v5).

    Read from the runner's own report: each pass with its controls, how many it visited and whether
    it finished, and the run's complete only when no pass is left unvisited, with the runner's own
    words for the window that bounds the run (whose limit it is). A render that made no interaction
    report says so, with no passes: "not checked", never an implied coverage.
    """

    if not interactive:
        return FrameInteractionCoverage(passes_total=0, passes_complete=0, complete=False)
    passes = tuple(
        FramePassCoverage(
            name=_pass_name(one),
            controls=len(one.get("controls") or []),
            visited=sum(c.get("status") != "unvisited" for c in one.get("controls") or []),
            complete=bool(one.get("complete")),
        )
        for one in interactive.get("passes") or []
    )
    return FrameInteractionCoverage(
        passes_total=len(passes),
        passes_complete=sum(one.complete for one in passes),
        complete=bool(passes)
        and not interactive.get("unvisited")
        and all(one.complete for one in passes),
        passes=passes,
        window=str(interactive["window"]) if interactive.get("window") else None,
    )


def _pass_name(one: Mapping[str, Any]) -> str:
    """The runner's own name for a pass (canvas-interactive-report.mjs passName)."""

    method = str(one.get("method") or "")
    return f"{one.get('width')}-{method if one.get('effects') else f'effects-off-{method}'}"


def _coverage_key(coverage: FrameInteractionCoverage) -> str:
    """A short digest of one coverage record: part of the request a publication is made under."""

    return hashlib.sha256(coverage.model_dump_json().encode()).hexdigest()[:12]
