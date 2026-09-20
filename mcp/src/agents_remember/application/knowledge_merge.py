"""The composition seam between admitted authority and the guarded common-base merge.

This is the third application module the storage design anticipated, and it exists for the same
reason as the snapshot seam: the merge is a different composed operation from a single candidate
write, and keeping it apart leaves each entry point readable as one intent.

Nothing here decides authority and nothing here holds durable state. It resolves a base claim,
merges, and returns the typed result unchanged. Storage ranks below application, so a lower owner --
the worktree or lifecycle package that would call a merge -- receives
:mod:`agents_remember.models.knowledge` values and never an import of this module or of the store.

The adapter this exposes is deliberately *callable rather than wired*: no Git merge driver is
installed, no attribute is configured and no commit is created anywhere on this path. A later,
separately reviewed change is what turns this boundary into a driver, and this module is the exact
seam such a change would call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import apsw

from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.merge import merge_knowledge_datasets
from agents_remember.memory.knowledge.merge_base import resolve_merge_base
from agents_remember.memory.knowledge.records import decode_repository_row
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.merge import (
    MergeBaseRequest,
    MergeBaseResolution,
    MergeInput,
    MergeInputRole,
    MergeOutcome,
    MergeRequest,
    ResolvedGitBase,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    SnapshotDestinationRequest,
    SnapshotIdentity,
)

__all__ = [
    "ConflictCommits",
    "merge_conflicted_stages",
    "merge_resolved_knowledge_datasets",
    "resolve_knowledge_merge_base",
]


def resolve_knowledge_merge_base(
    request: MergeBaseRequest,
) -> MergeBaseResolution | KnowledgeRefusal:
    """Prove the three input identities, their structure and the Git base claim.

    A resolution is returned as the proven value; a failure is returned as the typed refusal, so a
    caller that is composing a tool response branches on one code instead of catching an exception.
    """

    outcome = resolve_merge_base(request)
    if outcome.resolution is not None:
        return outcome.resolution
    if outcome.refusal is None:
        raise KnowledgeMergeSeamDefect(
            "base resolution returned neither a resolution nor a refusal"
        )
    return outcome.refusal


def merge_resolved_knowledge_datasets(request: MergeRequest) -> MergeOutcome:
    """Merge two sides against a proven common base, or return the typed refusal.

    The whole structural outcome is returned, including the coverage of both deltas and the
    publication state when a destination was named. The result carries no compatibility verdict: a
    ``structurally_merged`` outcome is a statement about the candidate's structure and nothing about
    whether the merged knowledge is correct.
    """

    return merge_knowledge_datasets(request)


class KnowledgeMergeSeamDefect(RuntimeError):
    """A seam state the layer below makes unreachable: a refusal and a value both absent."""


def merge_conflicted_stages(
    *,
    destination: Path,
    stages: Mapping[MergeInputRole, Path],
    repository_root: Path,
    commits: ConflictCommits,
) -> bool:
    """Merge three materialised index stages and publish the union into ``destination``.

    This is the half of a conflicted knowledge database's settlement that belongs to the application
    layer, and it exists as a function here for a structural reason rather than a stylistic one: a
    worktree module may not import the memory domain at all
    (``test_lower_ranked_owners_do_not_import_the_memory_domain``), so the module that owns the Git
    side of the conflict cannot read a dataset's identity itself. It hands this layer the three
    materialised paths and receives one boolean.

    Returns whether the dataset was settled. Every way of not settling -- a stage that is not a
    dataset, a refusal from the adapter (a schema disagreement above all), a publication that did not
    happen -- returns ``False``, and the caller then leaves the path conflicted for the agent. This
    function takes no compatibility verdict: a structural merge says the result is valid, never that
    the combined knowledge is correct.
    """

    try:
        identities: dict[MergeInputRole, SnapshotIdentity] = {
            role: dataset_identity(path) for role, path in stages.items()
        }
    except (KnowledgeStorageError, apsw.Error, OSError):
        return False
    try:
        connection = open_read_only_database(stages["left"])
    except (apsw.Error, OSError):
        return False
    try:
        rows = list(connection.execute("SELECT repository_id, authority_home FROM repository"))
    except apsw.Error:
        return False
    finally:
        connection.close()
    if not rows:
        return False
    repository = decode_repository_row(rows[0])

    resolution = resolve_knowledge_merge_base(
        MergeBaseRequest(
            repository=repository,
            git_base=ResolvedGitBase(
                repository_root=repository_root,
                base_commit_id=commits.base,
                left_commit_id=commits.left,
                right_commit_id=commits.right,
            ),
            inputs=(
                _merge_input("base", stages, identities),
                _merge_input("left", stages, identities),
                _merge_input("right", stages, identities),
            ),
        )
    )
    if isinstance(resolution, KnowledgeRefusal):
        return False
    outcome = merge_resolved_knowledge_datasets(
        MergeRequest(
            resolution=resolution,
            databases=dict(stages),
            destination=SnapshotDestinationRequest(
                destination_path=destination,
                expected_destination=identities["left"],
            ),
        )
    )
    return outcome.state == "structurally_merged" and outcome.publication_state == "published"


@dataclass(frozen=True)
class ConflictCommits:
    """The three commits one conflicted merge spans, as the adapter's base claim needs them.

    Grouped rather than passed as three trailing arguments because they are one fact -- which merge
    this is -- and because the adapter requires all three together: a base claim naming two of them
    would not be a claim at all.
    """

    base: str
    left: str
    right: str


def _merge_input(
    role: MergeInputRole,
    stages: Mapping[MergeInputRole, Path],
    identities: Mapping[MergeInputRole, SnapshotIdentity],
) -> MergeInput:
    """One of the three fixed positions, with the identity this layer read for it."""

    return MergeInput(
        role=role,
        reference=f":{_STAGE_NUMBERS[role]}:{stages[role].name}",
        database_path=stages[role],
        expected_identity=identities[role],
    )


_STAGE_NUMBERS: Mapping[MergeInputRole, int] = {"base": 1, "left": 2, "right": 3}
