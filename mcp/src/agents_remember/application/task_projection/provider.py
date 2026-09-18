"""The task-projection half of the capsule compiler's task-context seam.

The compiler declares one typed input seam -- a one-method protocol over
already-produced content -- and this module fills it without changing the frozen
DTO. :class:`TaskProjectionSource` is the protocol implementation for a caller
that has already resolved an admitted scope; :func:`task_context_of` converts a
computed projection into the capsule's own ``CapsuleTaskContext``.

The source never returns ``None`` for an admitted binding, even though the
protocol permits it. Returning ``None`` would mean "this admitted seat has no task
context", and a projection that could not read its task has not established that
-- it has failed. That failure is a typed
:class:`~agents_remember.errors.TaskProjectionSourceError` instead, because a
silent ``None`` is exactly the fallback the requirement forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_remember.errors import TaskProjectionSourceError
from agents_remember.models.role_capsules.types import (
    CapsuleBinding,
    CapsuleTaskContext,
    compute_content_digest,
)

from .projection import project_task_context
from .scope import ResolvedProjectionScope, require_admitted_revision
from .statuses import (
    STATUS_BINDING_MISMATCH,
    STATUS_BRANCH_MISMATCH,
)
from .types import TaskProjection, TaskProjectionRequest

#: The origin carried on every projection this package produces, so a capsule can
#: name where its task context came from without a second identity vocabulary.
PROJECTION_ORIGIN = "agents-remember/task-projection"


def task_context_of(projection: TaskProjection) -> CapsuleTaskContext:
    """The capsule-facing value: the rendered Markdown, its revision and its digest.

    ``content_digest`` is the digest of exactly the bytes carried in ``markdown``,
    which is what the compiler re-verifies before it will carry a projection at
    all; a projection whose digest did not match its own bytes would be refused
    there, so this conversion cannot smuggle one in.
    """

    encoded = projection.markdown.encode("utf-8")
    return CapsuleTaskContext(
        markdown=projection.markdown,
        origin=f"{PROJECTION_ORIGIN}:{projection.task.reference}",
        projection_revision=projection.projection_revision,
        content_digest=compute_content_digest(encoded),
    )


@dataclass(frozen=True, slots=True)
class TaskProjectionSource:
    """One resolved scope, ready to serve the compiler's task-context protocol.

    ``scope`` is bound to exactly one task and work branch when it is resolved.
    The binding handed to :meth:`project` is checked against it first, so a caller
    cannot resolve a scope for one leaf and compile a capsule for another and
    receive a plausible-looking projection of the wrong task.
    """

    scope: ResolvedProjectionScope
    request: TaskProjectionRequest = field(default_factory=TaskProjectionRequest)

    def project_detailed(self, binding: CapsuleBinding) -> TaskProjection:
        """The full computed projection, including its selection and gaps."""

        self._require_same_binding(binding)
        return project_task_context(self.scope, binding, self.request)

    def project(self, binding: CapsuleBinding) -> CapsuleTaskContext:
        """The capsule-facing projection for ``binding``.

        Raises rather than returning ``None``: an admitted binding whose task
        cannot be projected is a failure with a named cause, not a seat without
        context.
        """

        return task_context_of(self.project_detailed(binding))

    def _require_same_binding(self, binding: CapsuleBinding) -> None:
        """Every admitted fact this source's scope already fixed must still agree.

        Two independent guards, each with its own refusal: the binding must name the
        task and the branch this scope was resolved for, and it must carry the task
        revision whose bytes were read. The last one is the same comparison the scope
        path makes, called through the one shared implementation so the two paths
        cannot drift.
        """

        admitted = binding.admitted
        bound = self.scope.bound_task
        if admitted.task_reference != bound.reference:
            raise TaskProjectionSourceError(
                STATUS_BINDING_MISMATCH,
                f"this source resolved the admitted context for {bound.reference!r} but was "
                f"asked to project {admitted.task_reference!r}",
                next_action=(
                    "resolve one TaskProjectionScope per admitted task reference; a projection "
                    "is never produced for a task the scope did not bind"
                ),
            )
        if admitted.work_branch != self.scope.worktree.work_branch:
            raise TaskProjectionSourceError(
                STATUS_BRANCH_MISMATCH,
                f"this source resolved branch {self.scope.worktree.work_branch!r} but was asked "
                f"to project branch {admitted.work_branch!r}",
                next_action="re-resolve the scope for the branch the binding admits",
            )
        # The identity checks run first so each disagreement reports its most specific
        # cause; the admitted revision is the last thing compared, and the same shared
        # implementation the scope path uses makes that comparison once.
        require_admitted_revision(binding, bound.document_digest, bound.reference)


__all__ = ["PROJECTION_ORIGIN", "TaskProjectionSource", "task_context_of"]
