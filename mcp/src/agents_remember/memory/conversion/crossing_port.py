"""The crossing sync's composition-bound adapter (MIK-R24 rule 8).

:class:`GitKnowledgeCrossing` implements the worktree layer's ``KnowledgeCrossingPort``: it reads the
three memory commits, runs :func:`crossing_sync.cross` with the code repository's object store, and
returns the plan as the worktree layer's :class:`CrossingPlanView`. A failing step raises
``CrossingStepFailed`` naming the step; nothing has been written by then.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.crossing_sync import CrossingError, HistoryOwner, cross
from agents_remember.memory.conversion.inputs import memory_from_git
from agents_remember.worktrees.services import (
    CrossingPlanView,
    CrossingRequest,
    CrossingStepFailed,
)


class GitKnowledgeCrossing:
    """Plan a crossing sync's knowledge merge from three memory commits."""

    def plan(self, request: CrossingRequest) -> CrossingPlanView:
        try:
            with tempfile.TemporaryDirectory(prefix="ar-crossing-") as scratch:
                base, own, incoming = (
                    memory_from_git(
                        request.memory_repository, commit, Path(scratch), label=f"{name} {commit}"
                    )
                    for name, commit in zip(("base", "own", "incoming"), request.sides, strict=True)
                )
                plan = cross(
                    (base, own, incoming),
                    CodeObjects(request.code_repository),
                    own_paired_commit=request.code_commit,
                    repository=request.memory_repository,
                    owner=HistoryOwner(kind=request.owner_kind, id=request.owner_id),
                )
        except CrossingError as error:
            raise CrossingStepFailed(str(error)) from error
        except (OSError, ValueError) as error:
            raise CrossingStepFailed(f"crossing sync step 'convert' failed: {error}") from error
        return CrossingPlanView(
            files=plan.files,
            conflicts=tuple((one.path, one.item, one.reason) for one in plan.conflicts),
            conflict_versions=plan.conflict_versions,
            report=plan.report,
        )
