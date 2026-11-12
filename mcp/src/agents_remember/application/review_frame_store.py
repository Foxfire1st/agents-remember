"""The task-confined frame store: drafts, publication, reopening, staleness and reclamation.

MIK-R103's page outlives the process that drew it, so this module owns the acts that make that true:

* **admission before mutation.** A publication validates its draft, binding, evidence, manifest,
  containment and every ceiling *before* it creates a revision, moves the ready pointer or reclaims
  anything; a refused request leaves every prior byte and the pointer where they were (rule 3).
* **one integrity-checked snapshot.** Reopening, serving an asset, judging staleness and handing a
  regeneration its inputs all read one manifest and one set of asset bytes, verify their digests and
  use that snapshot -- not a second read that a concurrent publication could replace (rule 4).
* **drafts are real.** An authoring attempt is a durable :class:`FrameDraft` with a lease; a live
  draft is never reclaimed, an abandoned or finished one may be, and a failed or cancelled attempt
  keeps its bounded outcome. Publication, cleanup and archive all take the store's task lock, so a
  cleanup interleaved with a publisher cannot delete the bytes it is about to publish (rules 9, 12).
* **reclamation leaves receipts.** A removed revision or draft writes a :class:`FrameReclamation`
  before its bytes go and again with the outcome, and reopening consults it, so a reclaimed page is
  never reported as one that was never generated (rule 10, failure and recovery).
* **removal is the developer's** (v2 item 8). :func:`remove_revisions` deletes the retained
  revisions he chose in the Explanation view's revision selector -- never the pinned agreed
  revision, never one a retained revision's recorded regeneration lineage names, and never a number
  that is issued again -- under the one task lock, which it takes with a finite budget so a
  concurrent publication answers a named refusal instead of a hang. Nothing is removed silently and
  no superseded revision is reclaimed without that call.
* **physical confinement.** Every path is resolved against the task's own durable area and every
  intermediate component is checked, so a symlinked frame or leaf directory is held rather than
  followed into another task's bytes (rule 11).

Published pages live with the task they explain: ``<task>/notes/reports/review-frames/<leaf>/<frame>``,
the product-owned durable attachment root, so closing a workspace removes no published page.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agents_remember.application.review_evidence_adapter import require_intended_packet
from agents_remember.application.review_evidence_packet import comparison_reference
from agents_remember.application.review_frame_read import read_frame_bytes
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.file_lock import (
    LockAcquisitionTimeout,
    exclusive_file_lock,
    lock_path_for,
)
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_evidence import (
    EvidencePacket,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import (
    FRAME_POINTER_SCHEMA,
    FrameAsset,
    FrameAuthoring,
    FrameBinding,
    FrameDraft,
    FrameEntrypoint,
    FrameInteractionCoverage,
    FrameLimits,
    FrameManifest,
    FrameOpening,
    FramePart,
    FramePointer,
    FrameReclamation,
    FrameRegeneration,
    FrameStaleness,
    artifacts_digest,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.tasks.document import (
    ResolvedAgreedRevision,
)
from agents_remember.tasks.document import (
    resolve_agreed_revision as _canonical_resolve_agreed_revision,
)
from agents_remember.tasks.task_paths import slugify

__all__ = [
    "FRAMES_DIRECTORY",
    "RECLAMATION_RECEIPT",
    "AgreedRevision",
    "FramePublication",
    "FrameRemoval",
    "FrameScope",
    "begin_draft",
    "cleanup_frames",
    "finish_draft",
    "frame_scope",
    "frame_staleness",
    "frames_area",
    "guard_frame_id",
    "open_asset",
    "publish_revision",
    "reclaim_task_frames",
    "regeneration_request",
    "remove_revisions",
    "reopen_frame",
    "resolve_agreed_revision",
    "revision_manifest",
    "usage",
]

FRAMES_DIRECTORY = "review-frames"
READY_POINTER = "ready.json"
REVISIONS_DIRECTORY = "revisions"
DRAFTS_DIRECTORY = "drafts"
MANIFEST_NAME = "manifest.json"
MANIFEST_DIGEST_NAME = "manifest.sha256"
ASSETS_DIRECTORY = "assets"
DRAFT_NAME = "draft.json"
RECLAMATION_NAME = "reclaimed.json"
TASK_LOCK_NAME = ".frames.lock"
# The record that survives an archived task's reclamation, beside its other durable reports.
RECLAMATION_RECEIPT = "review-frames-reclaimed.json"

_TASK_DOCUMENT_NAMES = frozenset({"task.json", "task.md"})
# A leaf's task document is a small JSON control file. This read bound keeps a damaged one from
# being read whole into memory while leaving every real document far below it.
_TASK_DOCUMENT_READ_BYTES = 8 * 1024 * 1024
_MEDIA_BY_SUFFIX: dict[str, str] = {
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


class FrameRefused(ValueError):
    """A frame act refused with the reason a caller can act on.

    ``reason`` is the value a route or a page shows when the packet names one -- ``frame-store-full``
    for a revision a task's byte total cannot hold (MIK-R103 v2 item 8), ``binding-moved`` for a
    publication the environment no longer serves (MIK-R103 v2 item 3), and the removal reasons
    (``frame-not-found``, ``revision-not-retained``, ``revision-pinned``,
    ``revision-lineage-needed``, ``revision-removal-busy``) for the developer's removal control
    (MIK-R106 v2 rule 6).
    """

    def __init__(self, detail: str, *, reason: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class FramePublication:
    """What one publication call did: a new revision, a retry that added none, or a refusal."""

    manifest: FrameManifest | None
    published: bool
    reclaimed: tuple[int, ...]
    detail: str


@dataclass(frozen=True)
class FrameRemoval:
    """What one developer removal did: the revisions gone, what remains, and the pin it kept.

    The pin is answered even when nothing was removed, so the selector can label the revision the
    architect agreed at the go without a second read of the task document.
    """

    frame_id: str
    removed: tuple[int, ...]
    retained: tuple[int, ...]
    pinned: int | None
    bytes_freed: int
    detail: str


@dataclass(frozen=True)
class FrameScope:
    """The admitted task and frame a store act may touch, verified before any IO (rule 11)."""

    task_root: Path
    area: Path
    frame_id: str
    frame_dir: Path

    def verify(self) -> FrameScope:
        """Refuse a scope whose task, area or frame is symlinked or resolves outside the task."""

        root = self.task_root.resolve()
        for target, label in (
            (self.area, "area"),
            (self.frame_dir, "frame"),
            (self.frame_dir / REVISIONS_DIRECTORY, "revisions"),
            (self.frame_dir / DRAFTS_DIRECTORY, "drafts"),
            (self.frame_dir / READY_POINTER, "ready pointer"),
            (self.frame_dir / RECLAMATION_NAME, "reclamation receipt"),
        ):
            if _escaped(root, target):
                raise FrameRefused(
                    f"the {label} path {target} is not physically inside the task's own durable "
                    "area: a symlinked component or an escaping path is held, not followed"
                )
        return self

    def guard(self, target: Path, *, label: str = "path") -> Path:
        """Verify every component of one target below the task root before any IO (rule 11).

        The frame root being real is not enough: a draft's asset directory or a revision's asset
        directory can be swapped for a link after the root was checked, so each act guards the exact
        target it is about to read or write, ancestors included.
        """

        root = self.task_root.resolve()
        if _escaped(root, target):
            raise FrameRefused(
                f"the {label} {target} is not physically inside the task's own durable area: a "
                "symlinked component or an escaping path is held, not followed"
            )
        return target


@dataclass(frozen=True)
class FrameSnapshot:
    """One immutable revision as a single verified read: its manifest and its asset bytes."""

    manifest: FrameManifest
    manifest_digest: str
    assets: Mapping[str, bytes]

    def text(self, path: str) -> str | None:
        """One asset's bytes as text, when it holds a path this revision lists."""

        payload = self.assets.get(path)
        return None if payload is None else payload.decode("utf-8", "replace")


def _escaped(root: Path, target: Path) -> bool:
    """Whether ``target`` leaves ``root`` physically, through ``..`` or a symlinked component."""

    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        return True
    probe = root
    try:
        relative = target.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink():
            return True
    return False


def guard_frame_id(frame_id: str) -> str:
    """One frame id as one path segment, refusing anything that could leave the area."""

    if not frame_id or frame_id in {".", ".."} or "/" in frame_id or "\\" in frame_id:
        raise FrameRefused(f"a frame id is one path segment, not {frame_id!r}")
    return frame_id


def frames_area(task_root: Path, leaf_id: str) -> Path:
    """The task-owned frame area: this leaf's published pages and their drafts."""

    return durable_reports_root(Path(task_root)) / FRAMES_DIRECTORY / slugify(leaf_id)


def _task_lock(scope: FrameScope) -> Path:
    """The one lock a task's frame store takes: every leaf's publication, cleanup and archive.

    A leaf-scoped lock cannot exclude an archive that walks the task's whole area, which is how an
    active publisher lost its bytes. The lock therefore sits at the area root that every leaf shares.
    """

    resource = scope.area.parent / TASK_LOCK_NAME
    scope.guard(lock_path_for(resource), label="store lock")
    return resource


def _task_area(scope: FrameScope) -> Path:
    """The area every lineage of this task lives under, for the task byte ceiling (rule 8)."""

    return scope.area.parent


def frame_scope(task_root: Path, leaf_id: str, frame_id: str) -> FrameScope:
    """The verified scope one lineage's acts may touch."""

    area = frames_area(task_root, leaf_id)
    scope = FrameScope(
        task_root=Path(task_root),
        area=area,
        frame_id=guard_frame_id(frame_id),
        frame_dir=area / frame_id,
    )
    return scope.verify()


# --- drafts -------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _lease(now: str, limits: FrameLimits) -> str:
    moment = datetime.fromisoformat(now)
    return (moment + timedelta(seconds=limits.draft_lease_seconds)).isoformat(timespec="seconds")


def _canonical(value: object) -> bytes:
    return canonical_json_bytes(json.loads(json.dumps(value, default=str)))


def _draft_path(scope: FrameScope, request_id: str) -> Path:
    if not request_id or "/" in request_id or "\\" in request_id or request_id in {".", ".."}:
        raise FrameRefused(f"a draft request id is one path segment, not {request_id!r}")
    return scope.frame_dir / DRAFTS_DIRECTORY / request_id


def read_draft(scope: FrameScope, request_id: str) -> FrameDraft | None:
    """One lineage's draft record, or ``None`` when it is absent or unreadable."""

    try:
        scope.guard(_draft_path(scope, request_id) / DRAFT_NAME, label="draft record")
        return FrameDraft.model_validate_json(
            read_frame_bytes(_draft_path(scope, request_id) / DRAFT_NAME)
        )
    except (OSError, ValueError):
        return None


def begin_draft(  # noqa: PLR0913 - one draft states its own authoring and bytes
    scope: FrameScope,
    *,
    request_id: str,
    authoring: FrameAuthoring,
    focus: str,
    assets: Mapping[str, bytes],
    working_directory: Path,
    limits: FrameLimits,
    now: str | None = None,
) -> FrameDraft:
    """Admit a leased attempt in its caller-admitted authoring folder (rules 9, 11).

    Only its bounded identity/asset reservation lives with the task. The supplied per-attempt
    working folder belongs to the authoring session; publication imports its verified bytes.
    It is never inferred from a task or durable frame directory.
    """

    scope.verify()
    _check_assets(assets, limits, draft=True)
    working = _working_directory(scope, working_directory)
    moment = now or _now()
    draft = FrameDraft(
        frame_id=scope.frame_id,
        request_id=request_id,
        state="active",
        authoring=authoring,
        working_directory=str(working),
        assets=_entries(assets),
        focus=focus,
        updated_at=moment,
        lease_until=_lease(moment, limits),
    )
    with exclusive_file_lock(_task_lock(scope), "review frame store"):
        existing = read_draft(scope, request_id)
        if existing is not None:
            if (
                existing.authoring,
                existing.focus,
                existing.assets,
                existing.working_directory,
            ) != (authoring, focus, draft.assets, str(working)):
                raise FrameRefused(
                    "this request already owns a different authoring draft; use a new request"
                )
            return existing
        encoded = _canonical(json.loads(draft.model_dump_json(by_alias=True)))
        _check_capacity(
            scope,
            limits,
            revision_bytes=sum(len(payload) for payload in assets.values()) + len(encoded),
            replacing_draft=request_id,
        )
        directory = _draft_path(scope, request_id)
        scope.guard(directory, label="draft directory")
        for path, payload in assets.items():
            target = working / ASSETS_DIRECTORY / path
            _guard_working(working, target)
            atomic_write_bytes(target, payload)
        atomic_write_bytes(directory / DRAFT_NAME, encoded)
    return draft


def _working_directory(scope: FrameScope, working: Path) -> Path:
    """The explicit authoring folder, outside the task and free of symlinked components."""

    if not working.is_absolute() or _escaped(Path(working.anchor), working):
        raise FrameRefused(
            "the authoring folder is not physically inside its admitted working path"
        )
    if working == scope.task_root or scope.task_root in working.parents:
        raise FrameRefused(
            "unready assets belong in the authoring working folder, outside the task"
        )
    return working


def _guard_working(working: Path, target: Path) -> None:
    if _escaped(working, target):
        raise FrameRefused(
            f"the authoring asset {target} is not physically inside its working folder"
        )


def finish_draft(  # noqa: PLR0913 - one outcome, its identity, time and admitted bounds
    scope: FrameScope,
    request_id: str,
    *,
    state: str,
    detail: str,
    limits: FrameLimits,
    now: str | None = None,
) -> FrameDraft | None:
    """Record a failed or cancelled attempt's outcome, keeping its files bounded (rule 9)."""

    scope.verify()
    moment = now or _now()
    with exclusive_file_lock(_task_lock(scope), "review frame store"):
        draft = read_draft(scope, request_id)
        if draft is None:
            return None
        if state not in {"failed", "cancelled"} or draft.state != "active":
            raise FrameRefused("only an active attempt can record a failure or cancellation")
        finished = FrameDraft.model_validate(
            {
                **draft.model_dump(),
                "state": state,
                "detail": detail,
                "updated_at": moment,
                "lease_until": _lease(moment, limits),
            }
        )
        encoded = _canonical(json.loads(finished.model_dump_json(by_alias=True)))
        _check_capacity(
            scope,
            limits,
            revision_bytes=len(encoded) + sum(asset.byte_size for asset in finished.assets),
            replacing_draft=request_id,
        )
        atomic_write_bytes(
            _draft_path(scope, request_id) / DRAFT_NAME,
            encoded,
        )
    return finished


# --- publication --------------------------------------------------------------------------------


def _media_type(path: str) -> str | None:
    # Pinned kit licences retain their upstream basename and use the existing plain-text type.
    if Path(path).name == "LICENSE":
        return "text/plain"
    _stem, dot, suffix = path.rpartition(".")
    return _MEDIA_BY_SUFFIX.get(f".{suffix.lower()}") if dot else None


def _contained(path: str) -> bool:
    return not (
        not path
        or path.startswith("/")
        or "\\" in path
        or ".." in path.split("/")
        or path.startswith(".")
        or "\x00" in path
    )


def _check_assets(assets: Mapping[str, bytes], limits: FrameLimits, *, draft: bool) -> None:
    """Refuse a draft whose files the ceilings or the task discovery would not take (rules 2, 3)."""

    ceiling = limits.draft_bytes if draft else limits.bytes_per_revision
    if limits.files_per_revision is not None and len(assets) > limits.files_per_revision:
        raise FrameRefused(
            f"the attempt holds {len(assets)} file(s), beyond the admitted "
            f"{limits.files_per_revision} per revision"
        )
    total = sum(len(payload) for payload in assets.values())
    if ceiling is not None and total > ceiling:
        raise FrameRefused(
            f"the attempt holds {total} byte(s), beyond the admitted {ceiling} for a "
            f"{'draft' if draft else 'revision'}",
            reason="draft-too-large" if draft else "revision-too-large",
        )
    for path in assets:
        if not _contained(path):
            raise FrameRefused(f"the asset path {path!r} is not one relative path inside the area")
        if Path(path).name in _TASK_DOCUMENT_NAMES:
            raise FrameRefused(
                f"the asset name {Path(path).name!r} is one the product's task discovery reads as "
                "a task document; a published page may not be read as a task"
            )
        if _media_type(path) is None:
            raise FrameRefused(f"the asset {path!r} has no admitted media type")


def _revisions(frame_dir: Path) -> list[tuple[int, Path]]:
    root = frame_dir / REVISIONS_DIRECTORY
    if _escaped(Path(frame_dir.anchor), root):
        raise FrameRefused("the revisions directory is not physically inside its frame")
    if not root.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for child in root.iterdir():
        if child.is_dir() and not child.name.startswith(".") and child.name.isdigit():
            found.append((int(child.name), child))
    return sorted(found)


def _size(path: Path) -> int:
    if _escaped(Path(path.anchor), path):
        raise FrameRefused(f"the store path {path} is not physically inside its admitted area")
    if path.is_file():
        return path.stat().st_size
    size = 0
    for item in path.rglob("*"):
        if _escaped(path, item):
            raise FrameRefused(f"the store child {item} is not physically inside its admitted area")
        if item.is_file():
            size += item.stat().st_size
    return size


def _lineage_usage(frame_dir: Path) -> dict[str, int]:
    """This lineage's bytes, including its metadata and its drafts (rules 8, 10)."""

    revisions = sum(_size(directory) for _number, directory in _revisions(frame_dir))
    drafts = sum(_draft_usage(directory) for directory in _draft_directories(frame_dir))
    pointer = _size(frame_dir / READY_POINTER) if (frame_dir / READY_POINTER).is_file() else 0
    reclamation = (
        _size(frame_dir / RECLAMATION_NAME) if (frame_dir / RECLAMATION_NAME).is_file() else 0
    )
    return {
        "revisions": revisions,
        "drafts": drafts,
        "metadata": pointer + reclamation,
        "total": revisions + drafts + pointer + reclamation,
    }


def _draft_usage(directory: Path) -> int:
    draft = read_draft_path(directory)
    reserved = (
        sum(asset.byte_size for asset in draft.assets)
        if draft and draft.state != "published"
        else 0
    )
    return _size(directory) + reserved


def _draft_directories(frame_dir: Path) -> list[Path]:
    root = frame_dir / DRAFTS_DIRECTORY
    if _escaped(Path(frame_dir.anchor), root):
        raise FrameRefused("the drafts directory is not physically inside its frame")
    if not root.is_dir():
        return []
    return sorted(child for child in root.iterdir() if child.is_dir())


def _area_usage(area: Path) -> int:
    """The whole task area's bytes, one lineage at a time, metadata included (rule 8)."""

    if not area.is_dir():
        return 0
    total = 0
    for leaf in area.iterdir():
        if _escaped(area, leaf):
            raise FrameRefused(f"the task store path {leaf} is not physically inside its area")
        if leaf.is_dir():
            for frame in leaf.iterdir():
                if _escaped(area, frame):
                    raise FrameRefused(f"the frame {frame} is not physically inside its area")
                if frame.is_dir():
                    total += _lineage_usage(frame)["total"]
    return total


def _check_capacity(  # noqa: PLR0913 - one write's costs and the exact objects it replaces
    scope: FrameScope,
    limits: FrameLimits,
    *,
    revision_bytes: int,
    replacing_draft: str | None,
    reclaiming: tuple[int, ...] = (),
    replacing_metadata: tuple[str, ...] = (),
) -> None:
    """Refuse a write that would exceed a lineage or task ceiling, before any mutation (rule 8)."""

    freed = 0
    if replacing_draft:
        draft = _draft_path(scope, replacing_draft)
        freed = _draft_usage(draft) if draft.is_dir() else 0
    for name in replacing_metadata:
        path = scope.guard(scope.frame_dir / name)
        freed += _size(path) if path.is_file() else 0
    for number in reclaiming:
        reclaimed = scope.frame_dir / REVISIONS_DIRECTORY / str(number)
        freed += _size(reclaimed) if reclaimed.is_dir() else 0
    task_after = _area_usage(_task_area(scope)) - freed + revision_bytes
    if task_after > limits.task_bytes:
        raise FrameRefused(
            f"the task's frame store would hold {task_after} byte(s), beyond the admitted "
            f"{limits.task_bytes}; nothing was published and no current page was evicted. Remove "
            "an old revision in the Explanation view's revision selector, or ask for the pin to be "
            "moved; the revision that is pinned is never offered",
            reason="frame-store-full",
        )


def _leaf_document_stem(task_root: Path, leaf_id: str) -> str | None:
    """The leaf's own task-document stem, when the task folder holds that document.

    A planned binding's intended claim may rest on the leaf's own document, and the claim's receipt is
    recognised by this stem (MIK-R102 v2 item 1); a folder without the document answers ``None`` and
    the claim then rests on its other admitted sources or is refused.
    """

    for path in sorted(Path(task_root).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("id") == leaf_id:
            return path.stem
    return None


def publish_revision(  # noqa: PLR0913 - one publication states a draft's own inputs
    scope: FrameScope,
    *,
    request_id: str,
    entrypoint: str,
    evidence: EvidencePacket,
    supplemental: tuple[SupplementalReceipt, ...],
    created_at: str,
    limits: FrameLimits,
    asset_ancestry: tuple[int, ...] = (),
    part_map: tuple[FramePart, ...] = (),
    binding_source: Callable[[], FrameBinding | None] | None = None,
    interaction_coverage: FrameInteractionCoverage | None = None,
) -> FramePublication:
    """Admit and record one new ready revision, atomically and idempotently (rules 3, 8, 9, 14).

    ``interaction_coverage`` is written into the manifest as the revision's stated coverage
    (MIK-R211 v5); a frame that renders no interactions passes none.
    """

    scope.verify()
    draft = read_draft(scope, request_id)
    if draft is None:
        raise FrameRefused(
            f"no draft {request_id!r} exists in frame {scope.frame_id}; an authoring attempt is "
            "recorded before it publishes"
        )
    if draft.state == "published" and draft.published_revision is not None:
        existing = revision_manifest(scope.frame_dir, draft.published_revision)
        wanted = FrameBinding.from_packet(evidence.comparison)
        if existing is not None and not existing.binding.same_comparison(wanted):
            raise FrameRefused(
                f"attempt {request_id!r} already published revision {draft.published_revision} for "
                f"comparison {existing.binding.comparison_reference}; the request now names "
                f"{wanted.comparison_reference}, a different identity, and a retry never answers a "
                "foreign comparison with the old revision"
            )
        if existing is not None:
            return FramePublication(
                manifest=existing,
                published=False,
                reclaimed=(),
                detail=(
                    f"attempt {request_id!r} already published revision "
                    f"{draft.published_revision} for this same comparison; the retry added no "
                    "revision"
                ),
            )
    if draft.state != "active":
        raise FrameRefused(
            f"attempt {request_id!r} is {draft.state}, not active; a finished attempt publishes "
            "nothing and its outcome stays recorded"
        )
    binding = FrameBinding.from_packet(evidence.comparison)
    # A planned binding's claims are `intended` or they are refused here, at the seam where the
    # revision's binding meets its claim map (MIK-R189 rule 6; MIK-R102 v2's separate authorities).
    require_intended_packet(
        evidence,
        comparison_owner=binding.comparison_owner,
        leaf_id=binding.leaf_id,
        leaf_document=_leaf_document_stem(scope.task_root, binding.leaf_id),
    )
    with exclusive_file_lock(_task_lock(scope), "review frame store"):
        return _publish_locked(
            scope,
            draft=draft,
            entrypoint=entrypoint,
            binding=binding,
            evidence=evidence,
            supplemental=supplemental,
            created_at=created_at,
            limits=limits,
            ancestry=asset_ancestry,
            part_map=part_map,
            binding_source=binding_source,
            interaction_coverage=interaction_coverage,
        )


def _publish_locked(  # noqa: PLR0913 - the admission's own decided inputs
    scope: FrameScope,
    *,
    draft: FrameDraft,
    entrypoint: str,
    binding: FrameBinding,
    evidence: EvidencePacket,
    supplemental: tuple[SupplementalReceipt, ...],
    created_at: str,
    limits: FrameLimits,
    ancestry: tuple[int, ...],
    part_map: tuple[FramePart, ...] = (),
    binding_source: Callable[[], FrameBinding | None] | None = None,
    interaction_coverage: FrameInteractionCoverage | None = None,
) -> FramePublication:
    """The validated publication, executed only after every admission check has passed."""

    if binding_source is not None:
        served = binding_source()
        if served is not None and not binding.same_comparison(served):
            raise FrameRefused(
                "the environment's binding moved before this publication took the frame store's "
                f"lock: the draft binds {binding.comparison_reference}, the environment now serves "
                f"{served.comparison_reference}",
                reason="binding-moved",
            )
    current = read_draft(scope, draft.request_id)
    if current is None or current.state not in {"active", "published"}:
        raise FrameRefused("the authoring attempt finished before publication took its lock")
    draft = current
    existing = _find_same_attempt(scope, draft, binding)
    if existing is not None:
        return FramePublication(
            manifest=existing,
            published=False,
            reclaimed=(),
            detail=(
                f"attempt {draft.request_id!r} with these bytes and this comparison already "
                f"published revision {existing.revision}; the retry added no revision"
            ),
        )
    draft_total = sum(asset.byte_size for asset in draft.assets)
    if limits.bytes_per_revision is not None and draft_total > limits.bytes_per_revision:
        raise FrameRefused(
            f"the attempt holds {draft_total} byte(s), beyond the admitted "
            f"{limits.bytes_per_revision} for a revision",
            reason="revision-too-large",
        )
    assets = _draft_assets(scope, draft.request_id)
    _check_assets(assets, limits, draft=False)
    if entrypoint not in assets or _media_type(entrypoint) != "text/html":
        raise FrameRefused("the entrypoint is one of the draft's own HTML assets")
    entries = _entries(assets)
    digest = artifacts_digest(entries)
    pointer = _pointer(scope.frame_dir)
    retained = _revisions(scope.frame_dir)
    revision = (retained[-1][0] + 1) if retained else 1
    if pointer is not None and revision <= pointer.revision:
        raise FrameRefused(
            f"the ready pointer already names revision {pointer.revision}; this publisher holds a "
            "stale view and would overwrite a newer ready revision"
        )
    # A number a developer removed is never issued again: the reclamation receipt keeps every number
    # this lineage published, so the next publication cannot land on a removed revision's identity.
    revision = max(revision, _highest_removed(scope.frame_dir) + 1)
    manifest = _manifest(
        scope,
        revision=revision,
        parent=pointer.revision if pointer else None,
        binding=binding,
        focus=draft.focus,
        authoring=draft.authoring,
        created_at=created_at,
        entrypoint=entrypoint,
        entries=entries,
        digest=digest,
        evidence=evidence,
        supplemental=supplemental,
        ancestry=ancestry,
        part_map=part_map,
        interaction_coverage=interaction_coverage,
    )
    encoded = _canonical(json.loads(manifest.model_dump_json(by_alias=True)))
    if limits.manifest_bytes is not None and len(encoded) > limits.manifest_bytes:
        raise FrameRefused(
            f"the manifest is {len(encoded)} byte(s), beyond the admitted {limits.manifest_bytes}; "
            "nothing was published"
        )
    # Nothing is removed silently: the number of revisions is not a ceiling any more, and a task
    # that reaches its byte total is refused by name with the removal control offered instead
    # (MIK-R103 v2 item 8).
    planned: tuple[int, ...] = ()
    pointer_bytes = len(
        _canonical(
            {
                "schema": FRAME_POINTER_SCHEMA,
                "frame_id": scope.frame_id,
                "revision": revision,
                "manifest_digest": "0" * 64,
                "published_at": created_at,
            }
        )
    )
    published_draft = _canonical(
        json.loads(_published_draft(draft, revision, created_at).model_dump_json(by_alias=True))
    )
    reclamation_bytes = 0
    _check_capacity(
        scope,
        limits,
        revision_bytes=(
            sum(entry.byte_size for entry in entries)
            + len(encoded)
            + 64
            + pointer_bytes
            + len(published_draft)
            + reclamation_bytes
        ),
        replacing_draft=draft.request_id,
        reclaiming=planned,
        replacing_metadata=(READY_POINTER, RECLAMATION_NAME) if planned else (READY_POINTER,),
    )
    _write_revision(scope, revision, assets, encoded)
    atomic_write_bytes(
        scope.frame_dir / READY_POINTER,
        _canonical(
            json.loads(
                FramePointer(
                    frame_id=scope.frame_id,
                    revision=revision,
                    manifest_digest=hashlib.sha256(encoded).hexdigest(),
                    published_at=created_at,
                ).model_dump_json(by_alias=True)
            )
        ),
    )
    _mark_published(scope, draft, revision, created_at)
    reclaimed = tuple(
        number
        for number in planned
        if _reclaim_revision(scope, number, "superseded at the revision ceiling", created_at)
    )
    return FramePublication(
        manifest=manifest,
        published=True,
        reclaimed=reclaimed,
        detail=(
            f"revision {revision} is ready for comparison {binding.comparison_reference} with "
            f"{len(entries)} asset(s) and the admitted evidence it explains"
        ),
    )


def _find_same_attempt(
    scope: FrameScope, draft: FrameDraft, binding: FrameBinding
) -> FrameManifest | None:
    """The revision this attempt already published, when it did (rules 3, 6)."""

    for number, _directory in _revisions(scope.frame_dir):
        manifest = revision_manifest(scope.frame_dir, number)
        if (
            manifest is not None
            and manifest.authoring.request_id == draft.request_id
            and manifest.binding.same_comparison(binding)
        ):
            return manifest
    return None


def _manifest(  # noqa: PLR0913 - one manifest's own decided fields
    scope: FrameScope,
    *,
    revision: int,
    parent: int | None,
    binding: FrameBinding,
    focus: str,
    authoring: FrameAuthoring,
    created_at: str,
    entrypoint: str,
    entries: tuple[FrameAsset, ...],
    digest: str,
    evidence: EvidencePacket,
    supplemental: tuple[SupplementalReceipt, ...],
    ancestry: tuple[int, ...],
    part_map: tuple[FramePart, ...] = (),
    interaction_coverage: FrameInteractionCoverage | None = None,
) -> FrameManifest:
    return FrameManifest(
        frame_id=scope.frame_id,
        revision=revision,
        parent_revision=parent,
        binding=binding,
        focus=focus,
        authoring=authoring,
        created_at=created_at,
        entrypoint=FrameEntrypoint(path=entrypoint),
        assets=entries,
        artifact_digest=digest,
        evidence=evidence,
        supplemental=supplemental,
        asset_ancestry=ancestry,
        part_map=part_map,
        interaction_coverage=interaction_coverage,
    )


def _draft_assets(scope: FrameScope, request_id: str) -> dict[str, bytes]:
    draft = read_draft(scope, request_id)
    if draft is None:
        raise FrameRefused(f"draft {request_id!r} is unavailable")
    working = _working_directory(scope, Path(draft.working_directory))
    root = working / ASSETS_DIRECTORY
    _guard_working(working, root)
    if not root.is_dir():
        raise FrameRefused(f"draft {request_id!r} holds no assets")
    payloads: dict[str, bytes] = {}
    for asset in draft.assets:
        path = root / asset.path
        _guard_working(working, path)
        try:
            payload = read_frame_bytes(path, asset.byte_size)
        except ValueError as failure:
            raise FrameRefused(str(failure)) from failure
        if len(payload) != asset.byte_size or hashlib.sha256(payload).hexdigest() != asset.digest:
            raise FrameRefused(f"the authoring asset {asset.path!r} changed after draft admission")
        payloads[asset.path] = payload
    return payloads


def _entries(assets: Mapping[str, bytes]) -> tuple[FrameAsset, ...]:
    return tuple(
        FrameAsset(
            path=path,
            media_type=_media_type(path) or "application/octet-stream",
            byte_size=len(payload),
            digest=hashlib.sha256(payload).hexdigest(),
        )
        for path, payload in sorted(assets.items())
    )


def _write_revision(
    scope: FrameScope, revision: int, assets: Mapping[str, bytes], encoded: bytes
) -> None:
    """Write one revision beside the others; only a complete directory is promoted."""

    root = scope.frame_dir / REVISIONS_DIRECTORY
    scope.guard(root, label="revisions directory")
    staging = root / f".staging-{revision}-{os.getpid()}"
    scope.guard(staging, label="publication staging")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        for path, payload in assets.items():
            scope.guard(staging / ASSETS_DIRECTORY / path, label="publication asset")
            atomic_write_bytes(staging / ASSETS_DIRECTORY / path, payload)
        atomic_write_bytes(staging / MANIFEST_NAME, encoded)
        atomic_write_bytes(
            staging / MANIFEST_DIGEST_NAME, hashlib.sha256(encoded).hexdigest().encode()
        )
        os.replace(staging, root / str(revision))
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _published_draft(draft: FrameDraft, revision: int, created_at: str) -> FrameDraft:
    return draft.model_copy(
        update={
            "state": "published",
            "published_revision": revision,
            "updated_at": created_at,
            "lease_until": _lease(created_at, FrameLimits()),
        }
    )


def _mark_published(scope: FrameScope, draft: FrameDraft, revision: int, created_at: str) -> None:
    finished = _published_draft(draft, revision, created_at)
    atomic_write_bytes(
        _draft_path(scope, draft.request_id) / DRAFT_NAME,
        _canonical(json.loads(finished.model_dump_json(by_alias=True))),
    )


# --- reopening ----------------------------------------------------------------------------------


def revision_manifest(frame_dir: Path, revision: int) -> FrameManifest | None:
    """One revision's manifest, or ``None`` when it is absent or unreadable (never guessed)."""

    try:
        return FrameManifest.model_validate_json(_manifest_bytes(frame_dir, revision))
    except (OSError, ValueError):
        return None


def _manifest_bytes(frame_dir: Path, revision: int) -> bytes:
    """Every revision retains its publication hash, including after the ready pointer moves.

    The immutable hash file is artifact integrity metadata, not comparison authority. Like the
    ready pointer it detects changed bytes; it does not defend against a writer replacing both.
    """

    directory = frame_dir / REVISIONS_DIRECTORY / str(revision)
    for name in (MANIFEST_NAME, MANIFEST_DIGEST_NAME):
        if _escaped(Path(frame_dir.anchor), directory / name):
            raise FrameRefused("the revision manifest is not physically inside its frame")
    try:
        encoded = read_frame_bytes(directory / MANIFEST_NAME)
        digest = read_frame_bytes(directory / MANIFEST_DIGEST_NAME, 64)
    except ValueError as failure:
        raise FrameRefused(str(failure)) from failure
    if hashlib.sha256(encoded).hexdigest().encode() != digest:
        raise FrameRefused(
            f"the manifest of revision {revision} differs from its publication digest"
        )
    return encoded


def _pointer(frame_dir: Path) -> FramePointer | None:
    try:
        if _escaped(Path(frame_dir.anchor), frame_dir / READY_POINTER):
            raise FrameRefused("the ready pointer is not physically inside its frame")
        return FramePointer.model_validate_json(read_frame_bytes(frame_dir / READY_POINTER))
    except (OSError, ValueError):
        return None


def _snapshot(scope: FrameScope, revision: int) -> FrameSnapshot | str:
    """One revision's manifest and asset bytes, verified from a single read of each (rules 4, 13)."""

    scope.verify()
    manifest_path = scope.frame_dir / REVISIONS_DIRECTORY / str(revision) / MANIFEST_NAME
    scope.guard(manifest_path, label="revision manifest")
    try:
        encoded = _manifest_bytes(scope.frame_dir, revision)
    except (OSError, FrameRefused) as failure:
        return f"the manifest of revision {revision} could not be read ({failure})"
    try:
        manifest = FrameManifest.model_validate_json(encoded)
    except ValueError as failure:
        return f"the manifest of revision {revision} is not a readable frame manifest ({failure})"
    root = scope.frame_dir / REVISIONS_DIRECTORY / str(revision) / ASSETS_DIRECTORY
    scope.guard(root, label="revision asset directory")
    assets: dict[str, bytes] = {}
    for asset in manifest.assets:
        scope.guard(root / asset.path, label="revision asset")
        try:
            payload = read_frame_bytes(root / asset.path, asset.byte_size)
        except ValueError as failure:
            return f"the asset {asset.path!r} of revision {revision} is corrupt ({failure})"
        except OSError:
            return (
                f"the asset {asset.path!r} of revision {revision} is missing; the page is "
                "unavailable and nothing regenerates it by itself"
            )
        if len(payload) != asset.byte_size or hashlib.sha256(payload).hexdigest() != asset.digest:
            return (
                f"the asset {asset.path!r} of revision {revision} is corrupt: its bytes do not "
                "match the size and digest the manifest recorded"
            )
        assets[asset.path] = payload
    return FrameSnapshot(
        manifest=manifest,
        manifest_digest=hashlib.sha256(encoded).hexdigest(),
        assets=assets,
    )


def reopen_frame(scope: FrameScope) -> FrameOpening:
    """Read the ready revision from its own bytes; start nothing, rewrite nothing (rules 4, 10)."""

    scope.verify()
    pointer = _pointer(scope.frame_dir)
    if pointer is None:
        return FrameOpening(
            state="unavailable",
            frame_id=scope.frame_id,
            detail=_no_ready_detail(scope),
        )
    snapshot = _snapshot(scope, pointer.revision)
    if isinstance(snapshot, str):
        return FrameOpening(
            state="unavailable",
            frame_id=scope.frame_id,
            revision=pointer.revision,
            detail=snapshot,
        )
    if snapshot.manifest_digest != pointer.manifest_digest:
        return FrameOpening(
            state="unavailable",
            frame_id=scope.frame_id,
            revision=pointer.revision,
            detail=(
                f"the manifest bytes do not match the digest the ready pointer recorded for "
                f"revision {pointer.revision}"
            ),
        )
    return FrameOpening(
        state="ready",
        frame_id=scope.frame_id,
        revision=pointer.revision,
        manifest=snapshot.manifest,
        asset_paths=tuple(sorted(snapshot.assets)),
        detail=(
            f"revision {pointer.revision} opens from its own {len(snapshot.assets)} asset(s) for "
            f"comparison {snapshot.manifest.binding.comparison_reference}"
        ),
    )


def _no_ready_detail(scope: FrameScope) -> str:
    """Why a lineage has no ready revision: reclaimed, never generated, or only a draft."""

    reclamation = _reclamation(scope.frame_dir) or _task_reclamation(scope)
    if reclamation is not None:
        return (
            f"this lineage's ready revision was reclaimed: {reclamation.reason} "
            f"({', '.join(str(number) for number in reclamation.revisions) or 'no revision recorded'})"
            "; the receipt beside the task records it and nothing regenerates it by itself"
        )
    drafts = _draft_directories(scope.frame_dir)
    if drafts:
        return (
            "this lineage holds no ready revision; a draft is not a ready page, and nothing starts "
            "because one is missing"
        )
    return "this lineage holds no ready revision"


def _task_reclamation(scope: FrameScope) -> FrameReclamation | None:
    """The task archive's own receipt, when it names this lineage's frame (rule 10).

    A successful archive removes the lineage directory with its receipt, so the only surviving
    record is beside the task's reports. Reopening reads it, so a reclaimed page is explained rather
    than reported as one that was never generated.
    """

    receipt = scope.area.parent.parent / RECLAMATION_RECEIPT
    scope.guard(receipt, label="task reclamation receipt")
    try:
        document = json.loads(read_frame_bytes(receipt))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    removed = document.get("removed") or []
    if not any(
        isinstance(entry, dict) and Path(str(entry.get("path") or "")).name == scope.frame_id
        for entry in removed
    ):
        return None
    return FrameReclamation(
        frame_id=scope.frame_id,
        state="deleted" if document.get("state") == "reclaimed" else "partial",
        recorded_at=str(document.get("recordedAt") or ""),
        revisions=(),
        reason="the task was archived and its published pages removed with its attachments",
        failures=tuple(
            str(entry.get("reason") or "") for entry in document.get("failed") or [] if entry
        ),
    )


def _reclamation(frame_dir: Path) -> FrameReclamation | None:
    try:
        if _escaped(Path(frame_dir.anchor), frame_dir / RECLAMATION_NAME):
            raise FrameRefused("the reclamation receipt is not physically inside its frame")
        return FrameReclamation.model_validate_json(read_frame_bytes(frame_dir / RECLAMATION_NAME))
    except (OSError, ValueError):
        return None


def open_asset(scope: FrameScope, revision: int, path: str) -> bytes:
    """One ready asset's exact bytes from a verified snapshot (rules 4, 13).

    Serving an asset is the same act as reopening the page: when the revision asked for is the ready
    one, its manifest must still be the manifest the pointer recorded, so a manifest corrupted after
    publication is refused here as it is on reopen, and a historical revision is verified against
    its own bytes only.
    """

    scope.verify()
    pointer = _pointer(scope.frame_dir)
    snapshot = _snapshot(scope, revision)
    if isinstance(snapshot, str):
        raise FrameRefused(snapshot)
    if (
        pointer is not None
        and pointer.revision == revision
        and snapshot.manifest_digest != pointer.manifest_digest
    ):
        raise FrameRefused(
            f"the manifest of ready revision {revision} is not the manifest the pointer recorded; "
            "its bytes changed after publication and are not served"
        )
    payload = snapshot.assets.get(path)
    if payload is None:
        raise FrameRefused(
            f"{path!r} is not one of revision {revision}'s assets; the manifest is the only "
            "address list this store serves"
        )
    return payload


# --- staleness and explicit regeneration --------------------------------------------------------


def _changed_sides(retained: FrameBinding, selected: FrameBinding) -> tuple[str, ...]:
    """Which sides of the comparison moved, in the owner's full identity (rules 1, 5).

    A side is its repository, tree, commit and ref together: the same tree reached through another
    commit or pinned by another ref is another operand, so it is reported moved rather than current.
    """

    changed: list[str] = []
    if retained.side_identity()[0] != selected.side_identity()[0]:
        changed.append("code-base")
    if retained.side_identity()[1] != selected.side_identity()[1]:
        changed.append("code-candidate")
    if retained.side_identity()[2] != selected.side_identity()[2]:
        changed.append("memory-base")
    if retained.side_identity()[3] != selected.side_identity()[3]:
        changed.append("memory-candidate")
    if retained.conversion_identity() != selected.conversion_identity():
        changed.append("converted-base")
    if (
        retained.comparison_owner,
        retained.task_id,
        retained.leaf_id,
        retained.comparison_number,
    ) != (
        selected.comparison_owner,
        selected.task_id,
        selected.leaf_id,
        selected.comparison_number,
    ):
        changed.append("comparison")
    return tuple(changed)


def _selected_binding(record: ReviewTreeComparisonRecord, owner: str) -> FrameBinding:
    return FrameBinding.from_record(
        record, comparison_owner=owner, comparison_reference=comparison_reference(record)
    )


def frame_staleness(
    scope: FrameScope,
    selected: ReviewTreeComparisonRecord,
    *,
    comparison_owner: str,
    supplemental_state: str = "unread",
) -> FrameStaleness:
    """Compare the ready revision's full identity with the selected comparison (rule 5)."""

    return _staleness_from(
        scope, _ready_snapshot(scope), selected, comparison_owner, supplemental_state
    )


def _ready_snapshot(scope: FrameScope) -> FrameSnapshot | str:
    """The ready revision as one verified snapshot, or the reason there is none."""

    pointer = _pointer(scope.frame_dir)
    if pointer is None:
        return _no_ready_detail(scope)
    snapshot = _snapshot(scope, pointer.revision)
    if isinstance(snapshot, str):
        return snapshot
    if snapshot.manifest_digest != pointer.manifest_digest:
        return (
            f"the manifest bytes do not match the digest the ready pointer recorded for revision "
            f"{pointer.revision}"
        )
    return snapshot


def _staleness_from(
    scope: FrameScope,
    snapshot: FrameSnapshot | str,
    selected: ReviewTreeComparisonRecord,
    comparison_owner: str,
    supplemental_state: str,
) -> FrameStaleness:
    """Judge one already-read snapshot against the selection; no second read happens here."""

    opening = (
        FrameOpening(
            state="unavailable",
            frame_id=scope.frame_id,
            detail=snapshot,
        )
        if isinstance(snapshot, str)
        else FrameOpening(
            state="ready",
            frame_id=scope.frame_id,
            revision=snapshot.manifest.revision,
            manifest=snapshot.manifest,
            asset_paths=tuple(sorted(snapshot.assets)),
            detail="ready",
        )
    )
    if opening.manifest is None:
        return FrameStaleness(
            state="unavailable",
            frame_id=scope.frame_id,
            revision=opening.revision or 1,
            supplemental_state=supplemental_state,  # type: ignore[arg-type]
            detail=opening.detail,
        )
    manifest = opening.manifest
    chosen = _selected_binding(selected, comparison_owner)
    changed = _changed_sides(manifest.binding, chosen)
    if not changed:
        return FrameStaleness(
            state="current",
            frame_id=scope.frame_id,
            revision=manifest.revision,
            retained=manifest.binding,
            selected=chosen,
            supplemental_state=supplemental_state,  # type: ignore[arg-type]
            detail=(
                f"revision {manifest.revision} binds comparison "
                f"{manifest.binding.comparison_reference} in its full identity, which is the "
                "comparison selected now"
            ),
        )
    return FrameStaleness(
        state="stale",
        frame_id=scope.frame_id,
        revision=manifest.revision,
        retained=manifest.binding,
        selected=chosen,
        changed=changed,
        supplemental_state=supplemental_state,  # type: ignore[arg-type]
        regeneration_offered=True,
        detail=(
            f"revision {manifest.revision} binds comparison "
            f"{manifest.binding.comparison_reference}; the selected comparison is "
            f"{chosen.comparison_reference} and {', '.join(changed)} moved. The page is stale for "
            "this selection, regeneration is offered, and nothing starts by itself"
        ),
    )


def regeneration_request(
    scope: FrameScope,
    selected: ReviewTreeComparisonRecord,
    *,
    comparison_owner: str,
    supplemental_state: str = "unread",
) -> FrameRegeneration:
    """The stale revision's own assets, evidence and receipts for one explicit regeneration."""

    snapshot = _ready_snapshot(scope)
    staleness = _staleness_from(scope, snapshot, selected, comparison_owner, supplemental_state)
    opening = (
        FrameOpening(
            state="unavailable",
            frame_id=scope.frame_id,
            detail=snapshot,
        )
        if isinstance(snapshot, str)
        else FrameOpening(
            state="ready",
            frame_id=scope.frame_id,
            revision=snapshot.manifest.revision,
            manifest=snapshot.manifest,
            asset_paths=tuple(sorted(snapshot.assets)),
            detail="ready",
        )
    )
    if staleness.state != "stale" or opening.manifest is None or staleness.selected is None:
        return FrameRegeneration(
            state="refused",
            frame_id=scope.frame_id,
            stale_revision=opening.revision,
            selected=staleness.selected,
            detail=(
                "regeneration is offered only for a stale retained revision; this one is "
                f"{staleness.state}: {staleness.detail}"
            ),
        )
    manifest = opening.manifest
    return FrameRegeneration(
        state="ready",
        frame_id=scope.frame_id,
        stale_revision=manifest.revision,
        selected=staleness.selected,
        retained=manifest.binding,
        entrypoint=manifest.entrypoint.path,
        assets=manifest.assets,
        evidence_digest=manifest.evidence_digest(),
        supplemental=manifest.supplemental,
        asset_ancestry=(*manifest.asset_ancestry, manifest.revision),
        detail=(
            f"the authoring attempt starts from revision {manifest.revision}'s "
            f"{len(manifest.assets)} asset(s), its admitted evidence and its receipts, and explains "
            f"comparison {staleness.selected.comparison_reference}; carried claims are checked "
            "under MIK-R102 and no old byte is relabelled as current"
        ),
    )


# --- usage and bounded reclamation --------------------------------------------------------------


def usage(area: Path, *, now: str | None = None, include_frames: bool = True) -> dict[str, Any]:
    """Count the same stored bytes and held paths, optionally describing each frame.

    Scalar consumers omit display-only pointer and draft reads. The lineage traversal,
    reserved draft bytes and refusal checks are shared with the detailed view.
    """

    frames: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    total = 0
    moment = now or _now()
    if area.is_dir():
        root = area.resolve()
        for child in sorted(area.iterdir()):
            if child.name.startswith("."):
                continue
            if _escaped(root, child):
                held.append(
                    {"frame_id": child.name, "reason": "the frame path is a symlink; not counted"}
                )
                continue
            if not child.is_dir():
                continue
            try:
                lineage = _lineage_usage(child)
            except FrameRefused as refusal:
                held.append({"frame_id": child.name, "reason": str(refusal)})
                continue
            total += lineage["total"]
            if not include_frames:
                continue
            pointer = _pointer(child)
            drafts = [
                draft.state
                for draft in (read_draft_path(directory) for directory in _draft_directories(child))
                if draft is not None
            ]
            frames.append(
                {
                    "frame_id": child.name,
                    "revisions": [number for number, _ in _revisions(child)],
                    "ready_revision": pointer.revision if pointer else None,
                    "drafts": drafts,
                    "bytes": lineage["total"],
                    "revision_bytes": lineage["revisions"],
                    "draft_bytes": lineage["drafts"],
                    "metadata_bytes": lineage["metadata"],
                    "observed_at": moment,
                }
            )
    return {"area": str(area), "frames": frames, "held": held, "bytes": total}


def read_draft_path(directory: Path) -> FrameDraft | None:
    """One draft record read from its directory, or ``None`` when it is absent or unreadable."""

    try:
        if _escaped(Path(directory.anchor), directory / DRAFT_NAME):
            raise FrameRefused("the draft record is not physically inside its frame")
        return FrameDraft.model_validate_json(read_frame_bytes(directory / DRAFT_NAME))
    except (OSError, ValueError):
        return None


def _reclaim_revision(scope: FrameScope, number: int, reason: str, now: str) -> bool:
    """Remove one revision, leaving an intent and then an outcome record (rule 10)."""

    directory = scope.frame_dir / REVISIONS_DIRECTORY / str(number)
    manifest = revision_manifest(scope.frame_dir, number)
    digests = (manifest.artifact_digest,) if manifest is not None else ()
    _record_reclamation(
        scope, state="intent", revisions=(number,), digests=digests, reason=reason, now=now
    )
    try:
        shutil.rmtree(directory)
    except (OSError, FrameRefused) as failure:
        _record_reclamation(
            scope,
            state="partial",
            revisions=(number,),
            digests=digests,
            reason=reason,
            now=now,
            failures=(f"revision {number}: {failure}",),
        )
        return False
    _record_reclamation(
        scope, state="deleted", revisions=(number,), digests=digests, reason=reason, now=now
    )
    return True


def _reclamation_record(  # noqa: PLR0913 - one record's own facts and outcome
    scope: FrameScope,
    *,
    state: str,
    revisions: tuple[int, ...],
    digests: tuple[str, ...],
    reason: str,
    now: str,
    drafts: tuple[str, ...] = (),
    failures: tuple[str, ...] = (),
) -> FrameReclamation:
    previous = _reclamation(scope.frame_dir)
    merged_revisions = tuple(sorted(set((previous.revisions if previous else ()) + revisions)))
    merged_digests = tuple(sorted(set((previous.manifest_digests if previous else ()) + digests)))
    merged_drafts = tuple(sorted(set((previous.drafts if previous else ()) + drafts)))
    merged_failures = tuple(sorted(set((previous.failures if previous else ()) + failures)))
    return FrameReclamation(
        frame_id=scope.frame_id,
        state=state,  # type: ignore[arg-type]
        recorded_at=now,
        revisions=merged_revisions,
        drafts=merged_drafts,
        manifest_digests=merged_digests,
        reason=reason,
        failures=merged_failures,
    )


def _record_reclamation(scope: FrameScope, **facts: Any) -> None:
    record = _reclamation_record(scope, **facts)
    atomic_write_bytes(
        scope.guard(scope.frame_dir / RECLAMATION_NAME),
        _canonical(json.loads(record.model_dump_json(by_alias=True))),
    )


# --- the developer's removal of retained revisions -----------------------------------------------


REMOVAL_REASON = "the developer removed revisions in the Explanation view's revision selector (MIK-R103 v2 item 8)"
# A removal waits this long for the task lock and then refuses by name. A publication holding the
# lock must never make the developer's control hang, and nothing is deleted before the lock is held.
REMOVAL_LOCK_SECONDS = 2.0
_PINNED_NUMBER_KEYS = ("revision", "revisionId", "revisionNumber")


def remove_revisions(
    scope: FrameScope,
    *,
    revisions: tuple[int, ...],
    now: str | None = None,
    lock_wait_seconds: float = REMOVAL_LOCK_SECONDS,
) -> FrameRemoval:
    """Delete retained revisions one developer chose, under the store's one task lock (rule 10).

    This is the "remove old revisions" control of MIK-R106 v2 rule 6, and the only way a published
    revision leaves the store: a publication reclaims nothing (v2 item 8). The request is refused by
    name, before a byte is deleted, when it is empty or malformed, when the frame does not exist,
    when a named number is not retained, when a named number is the pinned agreed revision, when a
    retained revision's recorded regeneration lineage needs it, when a retained manifest cannot be
    read, and when the store's lock is held past ``lock_wait_seconds``. Because every refusal comes
    before the first deletion, a refused call mutates nothing.

    A removal keeps the lineage readable: every remaining revision's manifest and binding still read,
    its ``parent_revision`` still names the publication it followed (a removed parent answers as no
    longer retained, never as an error), and the reclamation receipt names every revision and
    artifact digest that went, so a reopen explains the loss instead of reporting a page that never
    existed. A removed number is never issued again. When the ready revision itself is removed the
    pointer moves to the highest retained revision, or is cleared when nothing remains.
    """

    scope.verify()
    wanted = _wanted_revisions(revisions)
    moment = now or _now()
    try:
        with exclusive_file_lock(
            _task_lock(scope),
            "review frame store",
            deadline=time.monotonic() + lock_wait_seconds,
        ):
            return _remove_locked(scope, wanted, moment)
    except LockAcquisitionTimeout as busy:
        raise FrameRefused(
            f"frame {scope.frame_id} is busy: a publication or another removal holds the store's "
            "lock, so nothing was removed and no revision was partly deleted; retry when it "
            "finishes",
            reason="revision-removal-busy",
        ) from busy


def _wanted_revisions(revisions: tuple[int, ...]) -> tuple[int, ...]:
    """The named numbers as one ascending request, refusing an empty, repeated or non-numeric one."""

    if not revisions:
        raise FrameRefused(
            "a removal names at least one retained revision", reason="revision-request-invalid"
        )
    if any(
        isinstance(number, bool) or not isinstance(number, int) or number < 1
        for number in revisions
    ):
        raise FrameRefused(
            f"a revision number is one positive integer, not {revisions!r}",
            reason="revision-request-invalid",
        )
    wanted = tuple(sorted(revisions))
    if len(set(wanted)) != len(wanted):
        raise FrameRefused(
            f"a removal names each revision once, not {revisions!r}",
            reason="revision-request-invalid",
        )
    return wanted


def _remove_locked(scope: FrameScope, wanted: tuple[int, ...], moment: str) -> FrameRemoval:
    """Validate the whole request, then delete it in one receipted pass; the lock is already held."""

    if not scope.frame_dir.is_dir():
        raise FrameRefused(
            f"frame {scope.frame_id} does not exist under this leaf's frame area, so there is "
            "nothing to remove",
            reason="frame-not-found",
        )
    retained = tuple(number for number, _ in _revisions(scope.frame_dir))
    unknown = tuple(number for number in wanted if number not in retained)
    if unknown:
        held = ", ".join(str(number) for number in retained) or "none"
        raise FrameRefused(
            f"revision(s) {', '.join(str(number) for number in unknown)} are not retained in frame "
            f"{scope.frame_id}, which holds {held}; a number is removed once and never offered again",
            reason="revision-not-retained",
        )
    pinned = _pinned_revision(scope)
    if pinned is not None and pinned in wanted:
        raise FrameRefused(
            f"revision {pinned} is the agreed revision the architect pinned in the leaf's task "
            "document; it is never offered for removal and it counts toward the task total",
            reason="revision-pinned",
        )
    needed = _lineage_need(scope.frame_dir, retained, frozenset(wanted))
    if needed is not None:
        ancestor, holder = needed
        raise FrameRefused(
            f"revision {holder}, which stays, records revision {ancestor} in its regeneration "
            "lineage; a retained revision's lineage names bytes the store still holds, so that "
            "revision is not removable",
            reason="revision-lineage-needed",
        )
    sizes = {
        number: _size(scope.frame_dir / REVISIONS_DIRECTORY / str(number)) for number in wanted
    }
    digests = _removal_digests(scope.frame_dir, wanted)
    _record_reclamation(
        scope,
        state="intent",
        revisions=wanted,
        digests=digests,
        reason=REMOVAL_REASON,
        now=moment,
    )
    removed, failures = _delete_revisions(scope, wanted)
    _record_reclamation(
        scope,
        state="partial" if failures else "deleted",
        revisions=removed,
        digests=digests,
        reason=REMOVAL_REASON,
        now=moment,
        failures=failures,
    )
    remaining = tuple(number for number, _ in _revisions(scope.frame_dir))
    _repoint(scope, wanted, remaining, moment)
    return FrameRemoval(
        frame_id=scope.frame_id,
        removed=removed,
        retained=remaining,
        pinned=pinned,
        bytes_freed=sum(sizes[number] for number in removed),
        detail=_removal_detail(scope.frame_id, removed, remaining, failures),
    )


def _removal_detail(
    frame_id: str,
    removed: tuple[int, ...],
    remaining: tuple[int, ...],
    failures: tuple[str, ...],
) -> str:
    """One sentence naming what went, what stays, and anything the store could not delete."""

    detail = (
        f"removed revision(s) {', '.join(str(number) for number in removed) or 'none'} from frame "
        f"{frame_id}; {', '.join(str(number) for number in remaining) or 'no revision'} remain"
    )
    if failures:
        detail = f"{detail}; {len(failures)} could not be deleted ({'; '.join(failures)})"
    return detail


#: The one canonical agreement resolver (MIK-R189 rule 8), defined in ``tasks.document`` so every
#: rank can import it; re-exported here for the store's historical consumers. It resolves the strict
#: ``intentReview.agreedRevision`` record (revision id, SHA-256, packet versions) to the exact
#: retained revision, its manifest bytes and digest verdict.
AgreedRevision = ResolvedAgreedRevision


def resolve_agreed_revision(
    task_root: Path,
    document: Any = None,
    *,
    leaf_id: str | None = None,
    frame_id: str = "explainer",
) -> ResolvedAgreedRevision | None:
    """Resolve ``intentReview.agreedRevision`` to the retained revision, manifest path and digest."""

    return _canonical_resolve_agreed_revision(
        task_root, document, leaf_id=leaf_id, frame_id=frame_id
    )


def _pinned_revision(scope: FrameScope) -> int | None:
    """The agreed revision's number from the leaf's task document (MIK-R189 rule 9).

    The architect writes it at the go as ``intentReview.agreedRevision``, and the store reads it
    there and nowhere else. A document with no intent review, or with no agreed revision, pins
    nothing -- the same reading every other frame act gives a record it cannot find. A pin the
    document states but whose number cannot be read refuses the removal instead of guessing: a
    revision the store cannot identify is one it must not delete.
    """

    document = _leaf_document(scope)
    review = document.get("intentReview") if document is not None else None
    agreement = review.get("agreedRevision") if isinstance(review, dict) else None
    if not isinstance(agreement, dict):
        return None
    resolved = resolve_agreed_revision(scope.task_root, agreement, leaf_id=scope.area.name)
    if resolved is not None and resolved.digest_verified:
        return resolved.revision
    raise FrameRefused(
        "the leaf's task document pins an agreed revision, but no verified revision can be resolved "
        f"from {', '.join(sorted(agreement)) or 'its empty record'}; no revision is offered for "
        "removal while the pin cannot be identified",
        reason="revision-pin-unreadable",
    )


def _leaf_document(scope: FrameScope) -> dict[str, Any] | None:
    """The leaf's own task document, matched by the id its frame area is named for.

    The document is the durable record the product writes beside the task, so it is found the way
    the page's own reader finds it: a JSON document in the task folder whose ``id`` names this leaf.
    Each path is checked before it is read; a symlinked or unreadable document is skipped rather
    than followed (rule 11).
    """

    root = scope.task_root.resolve()
    for path in sorted(scope.task_root.glob("*.json")):
        if _escaped(root, path):
            continue
        try:
            document = json.loads(read_frame_bytes(path, _TASK_DOCUMENT_READ_BYTES))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and slugify(str(document.get("id") or "")) == scope.area.name:
            return document
    return None


def _lineage_need(
    frame_dir: Path, retained: tuple[int, ...], removing: frozenset[int]
) -> tuple[int, int] | None:
    """The first removed number a retained revision's regeneration lineage names, and its holder."""

    for number in retained:
        if number in removing:
            continue
        manifest = revision_manifest(frame_dir, number)
        if manifest is None:
            raise FrameRefused(
                f"revision {number} is retained but its manifest cannot be read, so no removal can "
                "show that the lineage it belongs to stays readable",
                reason="revision-lineage-unreadable",
            )
        for ancestor in manifest.asset_ancestry:
            if ancestor in removing:
                return ancestor, number
    return None


def _removal_digests(frame_dir: Path, wanted: tuple[int, ...]) -> tuple[str, ...]:
    """The artifact digests the receipt records before the bytes they name are deleted."""

    return tuple(
        manifest.artifact_digest
        for number in wanted
        if (manifest := revision_manifest(frame_dir, number)) is not None
    )


def _delete_revisions(
    scope: FrameScope, wanted: tuple[int, ...]
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Delete each named revision directory; a link on the way is held, never followed (rule 11)."""

    root = scope.frame_dir / REVISIONS_DIRECTORY
    scope.guard(root, label="revisions directory")
    removed: list[int] = []
    failures: list[str] = []
    for number in wanted:
        try:
            scope.guard(root / str(number), label=f"revision {number}")
            shutil.rmtree(root / str(number))
        except (OSError, FrameRefused) as failure:
            failures.append(f"revision {number}: {failure}")
            continue
        removed.append(number)
    return tuple(removed), tuple(failures)


def _repoint(
    scope: FrameScope, removed: tuple[int, ...], remaining: tuple[int, ...], moment: str
) -> None:
    """Move the ready pointer off a removed revision, or clear it when no revision remains.

    Reopening reads the pointer, so a removal that took the ready revision leaves the newest
    revision the page can still serve in its place. Nothing is invented here: the pointer records
    the digest of the manifest bytes it will be verified against, as a publication does.
    """

    pointer = _pointer(scope.frame_dir)
    if pointer is None or pointer.revision not in removed:
        return
    ready = scope.frame_dir / READY_POINTER
    scope.guard(ready, label="ready pointer")
    if not remaining:
        ready.unlink(missing_ok=True)
        return
    revision = remaining[-1]
    try:
        digest = hashlib.sha256(_manifest_bytes(scope.frame_dir, revision)).hexdigest()
    except (OSError, FrameRefused):
        return
    atomic_write_bytes(
        ready,
        _canonical(
            json.loads(
                FramePointer(
                    frame_id=scope.frame_id,
                    revision=revision,
                    manifest_digest=digest,
                    published_at=moment,
                ).model_dump_json(by_alias=True)
            )
        ),
    )


def _highest_removed(frame_dir: Path) -> int:
    """The highest revision number this lineage's receipt says was removed, or ``0``."""

    record = _reclamation(frame_dir)
    return max(record.revisions, default=0) if record is not None else 0


def cleanup_frames(area: Path, *, dry_run: bool = False, now: str | None = None) -> dict[str, Any]:
    """Remove one area's expired drafts, holding live ones and every revision.

    The number of revisions is not a ceiling (MIK-R103 v2 item 8): nothing is removed silently, and
    a revision leaves only when the developer removes it in the Explanation view's selector.
    """

    moment = now or _now()
    report: dict[str, Any] = {
        "area": str(area),
        "dry_run": dry_run,
        "removed": [],
        "would_remove": [],
        "held": [],
        "failed": [],
        "bytes_reclaimed": 0,
    }
    if not area.is_dir():
        report["held"].append({"target": str(area), "reason": "the area does not exist"})
        return report
    root = area.resolve()
    for ancestor in (area, area.parent):
        if _escaped(Path(area.anchor), ancestor):
            raise FrameRefused(
                f"the supplied area {area} is not a real path: a symlinked ancestor is held, not "
                "normalized away"
            )
    with exclusive_file_lock(area.parent / TASK_LOCK_NAME, "review frame store"):
        for frame in sorted(area.iterdir()):
            if not frame.is_dir():
                continue
            if _escaped(root, frame):
                report["held"].append(
                    {
                        "target": str(frame),
                        "reason": "the frame directory is symlinked or escapes the area",
                    }
                )
                continue
            scope = FrameScope(
                task_root=Path(str(area)),
                area=area,
                frame_id=frame.name,
                frame_dir=frame,
            )
            try:
                scope.guard(frame / REVISIONS_DIRECTORY, label="revisions directory")
                scope.guard(frame / DRAFTS_DIRECTORY, label="draft directory")
            except FrameRefused as refusal:
                report["held"].append({"target": str(frame), "reason": str(refusal)})
                continue
            _cleanup_lineage(scope, moment, report, dry_run)
    return report


def _cleanup_lineage(
    scope: FrameScope,
    moment: str,
    report: dict[str, Any],
    dry_run: bool,
) -> None:
    for directory in _draft_directories(scope.frame_dir):
        draft = read_draft_path(directory)
        if draft is not None and draft.state == "active" and moment <= draft.lease_until:
            report["held"].append(
                {"target": str(directory), "reason": "the draft is active and its lease is live"}
            )
            continue
        if dry_run:
            report["would_remove"].append({"target": str(directory), "bytes": _size(directory)})
            continue
        if _remove_draft(scope, directory, draft, moment):
            report["removed"].append({"target": str(directory), "bytes": _size(directory)})
        else:
            report["failed"].append({"target": str(directory), "reason": "deletion failed"})


def _remove_draft(
    scope: FrameScope, directory: Path, draft: FrameDraft | None, moment: str
) -> bool:
    _record_reclamation(
        scope,
        state="intent",
        revisions=(),
        digests=(),
        drafts=(draft.request_id if draft else directory.name,),
        reason="abandoned or finished draft",
        now=moment,
    )
    try:
        if draft is not None:
            working = _working_directory(scope, Path(draft.working_directory))
            for asset in draft.assets:
                path = working / ASSETS_DIRECTORY / asset.path
                _guard_working(working, path)
                path.unlink(missing_ok=True)
        shutil.rmtree(directory)
    except (OSError, FrameRefused) as failure:
        _record_reclamation(
            scope,
            state="partial",
            revisions=(),
            digests=(),
            drafts=(draft.request_id if draft else directory.name,),
            reason="abandoned or finished draft",
            now=moment,
            failures=(f"draft {directory.name}: {failure}",),
        )
        return False
    _record_reclamation(
        scope,
        state="deleted",
        revisions=(),
        digests=(),
        drafts=(draft.request_id if draft else directory.name,),
        reason="abandoned or finished draft",
        now=moment,
    )
    return True


# --- archive-time reclamation -------------------------------------------------------------------


def reclaim_task_frames(
    task_root: Path,
    *,
    dry_run: bool = False,
    now: str | None = None,
    keep: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Remove every published frame of one archived task, recording intent and outcome (rule 10).

    A frame whose id is in ``keep`` is not the archive's to remove: it is neither listed nor touched.
    """

    reports = durable_reports_root(Path(task_root))
    area = reports / FRAMES_DIRECTORY
    outcome: dict[str, Any] = {
        "state": "absent",
        "area": str(area),
        "receipt": None,
        "frames": [],
        "would_remove": [],
        "removed": [],
        "failed": [],
    }
    if not area.is_dir():
        return outcome
    if dry_run:
        found = _archived_frames(area, outcome, keep)
        outcome["state"] = "would-delete"
        outcome["would_remove"] = [
            {"path": str(frame), "bytes": _lineage_usage(frame)["total"]} for frame, _entry in found
        ]
        return outcome
    receipt = reports / RECLAMATION_RECEIPT
    moment = now or _now()
    for ancestor in (area, area.parent):
        if _escaped(Path(area.anchor), ancestor):
            outcome["state"] = "failed"
            outcome["failed"].append(
                {
                    "path": str(area),
                    "reason": "the area or an ancestor is a symlink; held, not followed",
                }
            )
            return outcome
    with exclusive_file_lock(area / TASK_LOCK_NAME, "review frame store"):
        found = _archived_frames(area, outcome, keep)
        prior = _prior_archive(receipt)
        intent = _archive_receipt(
            found,
            state="intent",
            removed=prior["removed"],
            failed=prior["failed"],
            moment=moment,
            earlier_frames=prior["frames"],
        )
        try:
            atomic_write_bytes(receipt, _canonical(intent))
        except OSError as failure:
            outcome["state"] = "failed"
            outcome["failed"].append(
                {
                    "path": str(area),
                    "reason": f"the reclamation receipt cannot be written ({failure})",
                }
            )
            return outcome
        removed: list[dict[str, Any]] = []
        # The enumeration's own refusals travel with the outcome instead of being overwritten.
        failed: list[dict[str, Any]] = list(outcome["failed"])
        for frame, _entry in found:
            size = _lineage_usage(frame)["total"]
            try:
                shutil.rmtree(frame)
            except OSError as failure:
                failed.append({"path": str(frame), "reason": str(failure)})
                continue
            removed.append({"path": str(frame), "bytes": size})
        outcome["removed"] = removed
        # Empty-directory failures are part of the outcome this archive records, so pruning runs
        # before the final receipt is written and its failures are persisted with the rest.
        _prune_empty(area, outcome)
        failed = [*failed, *outcome["failed"]]
        outcome["failed"] = failed
        outcome["state"] = "partial" if failed else "reclaimed"
        document = _archive_receipt(
            found,
            state=outcome["state"],
            removed=[*prior["removed"], *removed],
            failed=[*prior["failed"], *failed],
            moment=moment,
            earlier_frames=prior["frames"],
        )
        atomic_write_bytes(receipt, _canonical(document))
        outcome["receipt"] = str(receipt)
    return outcome


def _archived_frames(
    area: Path, outcome: dict[str, Any], keep: frozenset[str] = frozenset()
) -> list[tuple[Path, dict[str, Any]]]:
    """Every frame directory under an archived area but the kept ones, never following a symlink."""

    root = area.resolve()
    found: list[tuple[Path, dict[str, Any]]] = []
    for leaf in sorted(area.iterdir()):
        if not leaf.is_dir():
            continue
        if _escaped(root, leaf):
            outcome["failed"].append(
                {"path": str(leaf), "reason": "the leaf directory is symlinked or escapes the area"}
            )
            continue
        for frame in sorted(leaf.iterdir()):
            if not frame.is_dir() or frame.name in keep:
                continue
            try:
                FrameScope(task_root=area, area=leaf, frame_id=frame.name, frame_dir=frame).verify()
            except FrameRefused as refusal:
                outcome["failed"].append({"path": str(frame), "reason": str(refusal)})
                continue
            if _escaped(root, frame):
                outcome["failed"].append(
                    {
                        "path": str(frame),
                        "reason": "the frame directory is symlinked or escapes the area",
                    }
                )
                continue
            found.append(
                (
                    frame,
                    {
                        "leaf": leaf.name,
                        "frame_id": frame.name,
                        "revisions": [number for number, _ in _revisions(frame)],
                        "ready_revision": (
                            pointer.revision if (pointer := _pointer(frame)) is not None else None
                        ),
                    },
                )
            )
    outcome["frames"] = [entry for _frame, entry in found]
    return found


def _prior_archive(receipt: Path) -> dict[str, list[dict[str, Any]]]:
    """What an earlier attempt of this archive already recorded, so a retry keeps it (rule 10)."""

    empty: dict[str, list[dict[str, Any]]] = {"frames": [], "removed": [], "failed": []}
    try:
        document = json.loads(read_frame_bytes(receipt))
    except (OSError, ValueError):
        return empty
    if not isinstance(document, dict):
        return empty
    return {
        name: [entry for entry in document.get(name, []) if isinstance(entry, dict)]
        for name in empty
    }


def _archive_receipt(  # noqa: PLR0913 - one record's own state, outcome and earlier entries
    found: list[tuple[Path, dict[str, Any]]],
    *,
    state: str,
    removed: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    moment: str,
    earlier_frames: list[dict[str, Any]],
) -> dict[str, Any]:
    """The archive's own record, keeping every frame an earlier attempt already named."""

    seen = {str(entry.get("path")) for entry in removed}
    merged_removed = list(removed)
    for entry in earlier_frames:
        path = str(entry.get("path") or "")
        if path and path not in seen and not Path(path).exists():
            merged_removed.append({"path": path, "bytes": 0, "earlier": True})
            seen.add(path)
    frames = {str(entry.get("frame_id")): entry for entry in earlier_frames}
    frames.update({str(entry.get("frame_id")): entry for _frame, entry in found})
    return {
        "schema": "ar-review-frames-reclamation/v1",
        "state": state,
        "recordedAt": moment,
        "area": str(found[0][0].parent.parent) if found else None,
        "frames": list(frames.values()),
        "removed": merged_removed,
        "failed": failed,
        "detail": (
            "the task was archived, so its published pages were removed with its other attachments; "
            "a reopen of any frame named here is unavailable because this receipt records the "
            "reclamation, and nothing regenerates a page by itself"
            if state != "intent"
            else "the task is being archived; this intent was written before any page was removed"
        ),
    }


def _prune_empty(area: Path, outcome: dict[str, Any]) -> None:
    """Remove the leaf directories this reclamation emptied, and the area with them."""

    for child in sorted(area.iterdir()):
        if child.is_dir() and not list(child.iterdir()):
            try:
                child.rmdir()
            except OSError as failure:
                outcome["failed"].append({"path": str(child), "reason": str(failure)})
    if not list(area.iterdir()):
        try:
            area.rmdir()
        except OSError as failure:
            outcome["failed"].append({"path": str(area), "reason": str(failure)})
