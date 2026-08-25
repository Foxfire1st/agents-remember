"""Crash-safe persistence for one disposable sprint closeout projection."""

from __future__ import annotations

import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agents_remember.controlplane.closeout_queue_records import CloseoutProjectionBuild
from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.controlplane.task_publication_lock import task_publication_lock
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.closeout.projection import (
    MAX_CLOSEOUT_SOURCE_PROBLEMS,
    CloseoutProjectionMember,
    CloseoutQueueState,
    ProjectionInvalidationResult,
    ProjectionRebuildResult,
    ProjectionSourceClassification,
    ProjectionSourceProblem,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

PROJECTION_OWNERSHIP = StoreOwnership(
    store="sprint-closeout-projection",
    writers=("mcp", "lifecycle-operation"),
    compaction_owner="mcp",
    rationale=(
        "the MCP invalidates/rebuilds disposable scheduling projections; lifecycle admission "
        "may only inspect exact-current projection state; the off-side build is scratch"
    ),
)


class CloseoutQueueStoreError(RuntimeError):
    """The canonical projection or an off-side build is malformed."""


@dataclass(frozen=True)
class ProjectionSourceIdentity:
    fingerprint: str | None
    problems: tuple[ProjectionSourceProblem, ...] = ()
    classification: ProjectionSourceClassification | None = None
    members: tuple[CloseoutProjectionMember, ...] = ()

    @property
    def readable(self) -> bool:
        return self.fingerprint is not None and not self.problems


def queue_store_paths(coordination_root: Path, sprint_ref: TaskDocumentRef) -> tuple[Path, Path]:
    sprint_path = (coordination_root / "tasks" / sprint_ref.repository / sprint_ref.path).resolve(
        strict=False
    )
    repository_root = (coordination_root / "tasks" / sprint_ref.repository).resolve(strict=False)
    if not sprint_path.is_relative_to(repository_root):
        raise CloseoutQueueStoreError("sprint closeout projection path escapes its task repository")
    root = sprint_path.parent / "artifacts"
    return root / "closeout-candidates.json", root / ".closeout-candidates.build"


class CloseoutQueueStore:
    """Canonical invalid/valid state plus a non-authoritative off-side build artifact."""

    def __init__(self, coordination_root: Path, sprint_ref: TaskDocumentRef) -> None:
        self.coordination_root = coordination_root
        self.sprint_ref = sprint_ref
        self.state_path, self.build_path = queue_store_paths(coordination_root, sprint_ref)

    def exists(self) -> bool:
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise CloseoutQueueStoreError(
                f"closeout projection presence cannot be inspected: {exc}"
            ) from exc
        return True

    def read_raw(self, *, timestamp: str) -> CloseoutQueueState:
        """Read one atomic projection snapshot without creating publication authority.

        The queue is disposable and never owns a lifecycle transition. Writers atomically
        replace its whole state under the exclusive lock, so a reader observes the complete old
        or new snapshot and does not need to create a sibling lock file. Task-document previews
        therefore remain byte-for-byte read-only.
        """

        try:
            return self._read_state(timestamp)
        except CloseoutQueueStoreError as exc:
            return CloseoutQueueState(
                sprintTaskDocumentRef=self.sprint_ref,
                revision=0,
                serviceCondition="invalid-empty",
                sourceProblems=[self._artifact_problem(exc)],
                updatedAt=timestamp,
            )

    def read_effective(
        self,
        *,
        timestamp: str,
        source: ProjectionSourceIdentity,
    ) -> CloseoutQueueState:
        """Treat every unreadable or mismatched source as invalid-empty in memory."""

        raw = self.read_raw(timestamp=timestamp)
        if (
            raw.serviceCondition == "valid-built"
            and source.readable
            and raw.sourceFingerprint == source.fingerprint
            and raw.sourceClassification == source.classification
            and raw.members == list(source.members)
        ):
            return raw
        problems = self._bounded_problems([*raw.sourceProblems, *source.problems])
        if not problems and raw.serviceCondition == "valid-built":
            problems.append(
                ProjectionSourceProblem(
                    kind="task",
                    address=self.sprint_ref.key,
                    state="invalid",
                    errorType="source-fingerprint-mismatch",
                    repairAction=_rebuild_action(self.sprint_ref),
                )
            )
        return CloseoutQueueState(
            sprintTaskDocumentRef=self.sprint_ref,
            revision=raw.revision,
            serviceCondition="invalid-empty",
            sourceProblems=problems,
            updatedAt=timestamp,
        )

    def invalidate(
        self, *, timestamp: str
    ) -> tuple[CloseoutQueueState, ProjectionInvalidationResult]:
        """Persist zero membership before any replacement build is attempted."""

        PROJECTION_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.state_path, PROJECTION_OWNERSHIP):
            existed = True
            malformed: CloseoutQueueStoreError | None = None
            try:
                existed = self.exists()
                current = self._read_state(timestamp)
            except CloseoutQueueStoreError as exc:
                malformed = exc
                current = CloseoutQueueState(
                    sprintTaskDocumentRef=self.sprint_ref,
                    revision=0,
                    serviceCondition="invalid-empty",
                    sourceProblems=[self._artifact_problem(exc)],
                    updatedAt=timestamp,
                )
            if malformed is not None:
                invalid = current.model_copy(update={"sourceProblems": []})
                self._publish(invalid)
                self._clear_build()
                return invalid, ProjectionInvalidationResult(
                    outcome="recovered-malformed",
                    revision=invalid.revision,
                    diagnostic=self._artifact_problem(malformed),
                )
            if not existed:
                self._publish(current)
                self._clear_build()
                return current, ProjectionInvalidationResult(
                    outcome="persisted-empty", revision=current.revision
                )
            if existed and current.serviceCondition == "invalid-empty":
                self._clear_build()
                return current, ProjectionInvalidationResult(
                    outcome="already-empty", revision=current.revision
                )
            invalid = CloseoutQueueState(
                sprintTaskDocumentRef=self.sprint_ref,
                revision=current.revision + 1,
                serviceCondition="invalid-empty",
                updatedAt=timestamp,
            )
            self._publish(invalid)
            self._clear_build()
            return invalid, ProjectionInvalidationResult(
                outcome="persisted-empty", revision=invalid.revision
            )

    def publish_build(
        self,
        build: CloseoutProjectionBuild,
        *,
        current_source: Callable[[], ProjectionSourceIdentity],
    ) -> tuple[CloseoutQueueState, ProjectionRebuildResult]:
        """Publish a complete build only if its exact source identity is still current."""

        if build.sprintTaskDocumentRef != self.sprint_ref:
            raise CloseoutQueueStoreError("projection build belongs to another sprint")
        PROJECTION_OWNERSHIP.check_declared_writer()
        atomic_write_text(
            self.build_path, build.model_dump_json(exclude_none=True, indent=2) + "\n"
        )
        with (
            task_publication_lock(
                self.coordination_root,
                self.sprint_ref.repository,
            ),
            exclusive_access(self.state_path, PROJECTION_OWNERSHIP),
        ):
            current = self._read_state(build.builtAt)
            source = current_source()
            if not source.readable:
                self._clear_build()
                return current, ProjectionRebuildResult(
                    outcome="source-unreadable",
                    sourceProblems=self._bounded_problems(list(source.problems)),
                )
            if source.fingerprint != build.sourceFingerprint:
                self._clear_build()
                return current, ProjectionRebuildResult(
                    outcome="source-changed", sourceFingerprint=source.fingerprint
                )
            if (
                source.classification != build.sourceClassification
                or list(source.members) != build.members
            ):
                self._clear_build()
                return current, ProjectionRebuildResult(
                    outcome="source-changed",
                    sourceFingerprint=source.fingerprint,
                    sourceClassification=source.classification,
                    memberCount=len(source.members),
                )
            if (
                current.serviceCondition == "valid-built"
                and current.sourceFingerprint == build.sourceFingerprint
                and current.sourceClassification == build.sourceClassification
                and current.members == build.members
            ):
                self._clear_build()
                return current, ProjectionRebuildResult(
                    outcome="already-current",
                    revision=current.revision,
                    sourceFingerprint=build.sourceFingerprint,
                    sourceClassification=build.sourceClassification,
                    memberCount=len(build.members),
                )
            published = CloseoutQueueState(
                sprintTaskDocumentRef=self.sprint_ref,
                revision=current.revision + 1,
                serviceCondition="valid-built",
                sourceClassification=build.sourceClassification,
                sourceFingerprint=build.sourceFingerprint,
                members=build.members,
                updatedAt=build.builtAt,
            )
            self._publish(published)
            self._clear_build()
            return published, ProjectionRebuildResult(
                outcome="published",
                revision=published.revision,
                sourceFingerprint=build.sourceFingerprint,
                sourceClassification=build.sourceClassification,
                memberCount=len(build.members),
            )

    def _read_state(self, timestamp: str) -> CloseoutQueueState:
        try:
            mode = self.state_path.lstat().st_mode
        except FileNotFoundError:
            return CloseoutQueueState(
                sprintTaskDocumentRef=self.sprint_ref,
                revision=0,
                serviceCondition="invalid-empty",
                updatedAt=timestamp,
            )
        except OSError as exc:
            raise CloseoutQueueStoreError(
                f"closeout projection cannot be inspected: {exc}"
            ) from exc
        if not stat.S_ISREG(mode):
            raise CloseoutQueueStoreError(
                f"closeout projection is not a regular file: {self.state_path}"
            )
        try:
            state = CloseoutQueueState.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise CloseoutQueueStoreError(f"invalid closeout projection: {exc}") from exc
        if state.sprintTaskDocumentRef != self.sprint_ref:
            raise CloseoutQueueStoreError("closeout projection belongs to another sprint")
        return state

    def _publish(self, state: CloseoutQueueState) -> None:
        atomic_write_text(
            self.state_path,
            state.model_dump_json(exclude_none=True, indent=2) + "\n",
        )

    def _clear_build(self) -> None:
        with suppress(OSError):
            self.build_path.unlink(missing_ok=True)

    def _artifact_problem(self, error: BaseException) -> ProjectionSourceProblem:
        return ProjectionSourceProblem(
            kind="projection",
            address=self.state_path.as_posix(),
            state="unreadable",
            errorType=type(error).__name__,
            repairAction=_rebuild_action(self.sprint_ref),
        )

    def _bounded_problems(
        self,
        problems: list[ProjectionSourceProblem],
    ) -> list[ProjectionSourceProblem]:
        unique: list[ProjectionSourceProblem] = []
        seen: set[str] = set()
        for problem in problems:
            key = problem.model_dump_json()
            if key in seen:
                continue
            seen.add(key)
            unique.append(problem)
        if len(unique) <= MAX_CLOSEOUT_SOURCE_PROBLEMS:
            return unique
        overflow = ProjectionSourceProblem(
            kind="projection",
            address=self.state_path.as_posix(),
            state="invalid",
            errorType="source-problem-cap-exceeded",
            repairAction=_rebuild_action(self.sprint_ref),
        )
        return [*unique[: MAX_CLOSEOUT_SOURCE_PROBLEMS - 1], overflow]


def _rebuild_action(sprint_ref: TaskDocumentRef) -> str:
    return (
        "closeout_queue(action='rebuild', sprint_task_document_ref="
        f"{sprint_ref.model_dump(mode='json')!r})"
    )
