"""Invalidation, rebuild, preview, and recovery effects for disposable projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.closeout_projection import (
    ProjectionInvalidationResult,
    ProjectionRebuildResult,
    ProjectionSourceProblem,
    TaskDocProjectionEffect,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument

from .closeout_projection import capture_projection_source, now_iso


@dataclass(frozen=True)
class ProjectionInvalidationReceipt:
    sprint_ref: TaskDocumentRef
    queue_existed: bool
    prior_revision: int | None
    prior_source_fingerprint: str | None
    invalidation: ProjectionInvalidationResult


def refresh_closeout_projection(
    coordination_root: Path,
    sprint_ref: TaskDocumentRef,
    *,
    timestamp: str | None = None,
) -> TaskDocProjectionEffect:
    """Persist empty, build off-side, and publish only an exact-current replacement."""

    recorded_at = timestamp or now_iso()
    store = CloseoutQueueStore(coordination_root, sprint_ref)
    existed = store.exists()
    prior = store.read_raw(timestamp=recorded_at)
    _invalid, invalidation = store.invalidate(timestamp=recorded_at)
    return rebuild_invalidated_closeout_projection(
        coordination_root,
        ProjectionInvalidationReceipt(
            sprint_ref=sprint_ref,
            queue_existed=existed,
            prior_revision=prior.revision if existed else None,
            prior_source_fingerprint=prior.sourceFingerprint,
            invalidation=invalidation,
        ),
        timestamp=recorded_at,
    )


def rebuild_invalidated_closeout_projection(
    coordination_root: Path,
    receipt: ProjectionInvalidationReceipt,
    *,
    timestamp: str | None = None,
) -> TaskDocProjectionEffect:
    """Build/publish after the authoritative mutation's invalidation boundary."""

    recorded_at = timestamp or now_iso()
    sprint_ref = receipt.sprint_ref
    store = CloseoutQueueStore(coordination_root, sprint_ref)
    snapshot = capture_projection_source(coordination_root, sprint_ref, timestamp=recorded_at)
    build = snapshot.build(sprint_ref)
    if build is None:
        rebuild = ProjectionRebuildResult(
            outcome="source-unreadable",
            sourceProblems=list(snapshot.identity.problems),
        )
        state = store.read_raw(timestamp=recorded_at)
    else:
        state, rebuild = store.publish_build(
            build,
            current_source=lambda: (
                capture_projection_source(
                    coordination_root,
                    sprint_ref,
                ).identity
            ),
        )
    next_action = None
    if rebuild.outcome not in {"published", "already-current"}:
        next_action = rebuild_action(sprint_ref)
    return TaskDocProjectionEffect(
        sprintTaskDocumentRef=sprint_ref,
        queueExisted=receipt.queue_existed,
        priorRevision=receipt.prior_revision,
        priorSourceFingerprint=receipt.prior_source_fingerprint,
        invalidation=receipt.invalidation,
        rebuild=rebuild,
        rebuiltRevision=(
            state.revision if rebuild.outcome in {"published", "already-current"} else None
        ),
        nextAction=next_action,
    )


def preview_closeout_projection_effect(
    coordination_root: Path,
    sprint_ref: TaskDocumentRef,
    *,
    timestamp: str | None = None,
    overrides: Mapping[TaskDocumentRef, TaskDocument] | None = None,
) -> TaskDocProjectionEffect:
    """Predict the exact-current scope effect without writing projection bytes."""

    recorded_at = timestamp or now_iso()
    store = CloseoutQueueStore(coordination_root, sprint_ref)
    existed = store.exists()
    prior = store.read_raw(timestamp=recorded_at)
    malformed = next(
        (
            problem
            for problem in prior.sourceProblems
            if problem.kind == "projection" and problem.state == "unreadable"
        ),
        None,
    )
    snapshot = capture_projection_source(
        coordination_root,
        sprint_ref,
        timestamp=recorded_at,
        overrides=overrides,
    )
    build = snapshot.build(sprint_ref)
    rebuild = (
        ProjectionRebuildResult(
            outcome="would-publish",
            sourceFingerprint=build.sourceFingerprint,
            sourceClassification=build.sourceClassification,
            memberCount=len(build.members),
        )
        if build is not None
        else ProjectionRebuildResult(
            outcome="source-unreadable",
            sourceProblems=list(snapshot.identity.problems),
        )
    )
    return TaskDocProjectionEffect(
        sprintTaskDocumentRef=sprint_ref,
        queueExisted=existed,
        priorRevision=prior.revision if existed else None,
        priorSourceFingerprint=prior.sourceFingerprint,
        invalidation=ProjectionInvalidationResult(
            outcome=(
                "would-recover-malformed"
                if malformed is not None
                else "not-created"
                if not existed
                else "already-empty"
                if prior.serviceCondition == "invalid-empty"
                else "would-persist-empty"
            ),
            revision=(
                prior.revision
                if malformed is not None or not existed or prior.serviceCondition == "invalid-empty"
                else prior.revision + 1
            ),
            diagnostic=malformed,
        ),
        rebuild=rebuild,
        rebuiltRevision=(
            (
                prior.revision + 1
                if not existed or prior.serviceCondition == "invalid-empty"
                else prior.revision + 2
            )
            if build is not None
            else None
        ),
        nextAction=None if build is not None else rebuild_action(sprint_ref),
    )


def rebuild_action(sprint_ref: TaskDocumentRef) -> str:
    return (
        "closeout_queue(action='rebuild', sprint_task_document_ref="
        f"{sprint_ref.model_dump(mode='json')!r})"
    )


def projection_refresh_failure_effect(
    coordination_root: Path,
    sprint_ref: TaskDocumentRef,
    error: BaseException,
    *,
    timestamp: str | None = None,
) -> TaskDocProjectionEffect:
    """Bound an interrupted disposable refresh without assigning it lifecycle authority."""

    recorded_at = timestamp or now_iso()
    store = CloseoutQueueStore(coordination_root, sprint_ref)
    existed = store.exists()
    try:
        prior = store.read_raw(timestamp=recorded_at)
        prior_revision = prior.revision if existed else None
        prior_fingerprint = prior.sourceFingerprint
    except Exception:
        prior_revision = None
        prior_fingerprint = None
    problem = ProjectionSourceProblem(
        kind="projection",
        address=store.state_path.as_posix(),
        state="unreadable",
        errorType=type(error).__name__,
        repairAction=rebuild_action(sprint_ref),
    )
    return TaskDocProjectionEffect(
        sprintTaskDocumentRef=sprint_ref,
        queueExisted=existed,
        priorRevision=prior_revision,
        priorSourceFingerprint=prior_fingerprint,
        invalidation=ProjectionInvalidationResult(
            outcome="failed",
            diagnostic=problem,
        ),
        rebuild=ProjectionRebuildResult(
            outcome="not-attempted",
            sourceProblems=[problem],
        ),
        nextAction=rebuild_action(sprint_ref),
    )


__all__ = [
    "ProjectionInvalidationReceipt",
    "preview_closeout_projection_effect",
    "projection_refresh_failure_effect",
    "rebuild_action",
    "rebuild_invalidated_closeout_projection",
    "refresh_closeout_projection",
]
