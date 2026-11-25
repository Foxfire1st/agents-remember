"""The crossing sync's composition-bound adapter (MIK-R24 rule 8).

:class:`GitKnowledgeCrossing` implements the worktree layer's ``KnowledgeCrossingPort``: it reads the
three memory commits, runs :func:`crossing_sync.cross` with the code repository's object store, and
returns the plan as the worktree layer's :class:`CrossingPlanView`. A failing step raises
``CrossingStepFailed`` naming the step; nothing has been written by then.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from agents_remember.kernel.git_command import run_git
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.crossing_sync import (
    CrossingDestination,
    CrossingError,
    HistoryOwner,
    cross,
)
from agents_remember.memory.conversion.inputs import memory_from_git
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, ONBOARDING_ROOT

if TYPE_CHECKING:  # annotation-only: the worktree layer loads when a plan is asked for
    from agents_remember.worktrees.services import (
        CrossingPlanView,
        CrossingRequest,
    )


def _git_owned_paths(objects: CodeObjects, sides: tuple[str, str, str]) -> frozenset[str]:
    """Find mode changes, nonregular files and file/directory collisions across the sides."""

    modes: dict[str, str] = {}
    directories: set[str] = set()
    owned: set[str] = set()
    for commit in sides:
        for path, mode in objects.tree_modes(commit).items():
            if path.partition("/")[0] not in (KNOWLEDGE_ROOT, ONBOARDING_ROOT):
                continue
            directories.update(parent.as_posix() for parent in PurePosixPath(path).parents)
            if mode not in ("100644", "100755") or modes.setdefault(path, mode) != mode:
                owned.add(path)
    return frozenset(owned | (modes.keys() & directories))


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
                    destination=CrossingDestination(
                        repository=request.memory_repository,
                        git_owned_paths=_git_owned_paths(
                            CodeObjects(request.memory_repository), request.sides
                        ),
                    ),
                    owner=HistoryOwner(
                        kind=request.owner_kind,
                        id=request.owner_id,
                        at=merged_at(request.memory_repository, request.sides[1:]),
                    ),
                )
        except CrossingError as error:
            raise import_module("agents_remember.worktrees.services").CrossingStepFailed(
                str(error)
            ) from error
        except (OSError, ValueError) as error:
            raise import_module("agents_remember.worktrees.services").CrossingStepFailed(
                f"crossing sync step 'convert' failed: {error}"
            ) from error
        return import_module("agents_remember.worktrees.services").CrossingPlanView(
            files=plan.files,
            conflicts=tuple((one.path, one.item, one.reason) for one in plan.conflicts),
            conflict_versions=plan.conflict_versions,
            report=plan.report,
        )


def merged_at(repository: Path, commits: tuple[str, ...]) -> str:
    """The later committer time of the merged lines' commits: the time of the merge's own stamp.

    Read from the commits, not from a clock, so a merge repeated on the same commits stores the same
    bytes.
    """

    times: list[str] = []
    for commit in commits:
        shown = run_git(repository, ["show", "-s", "--format=%cI", commit])
        if shown.returncode != 0 or not shown.stdout.strip():
            raise OSError(f"the commit time of {commit} cannot be read: {shown.stderr.strip()}")
        times.append(shown.stdout.strip())
    return max(times, key=datetime.fromisoformat)
