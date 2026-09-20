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
    AuthoredReconciliation,
    MergeBaseRequest,
    MergeBaseResolution,
    MergeConflict,
    MergeInput,
    MergeInputRole,
    MergeOutcome,
    MergeRequest,
    ResolvedGitBase,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    SnapshotDestinationRequest,
    SnapshotIdentity,
)

__all__ = [
    "ConflictCommits",
    "KnowledgeStageSettlement",
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


@dataclass(frozen=True)
class KnowledgeStageSettlement:
    """What one attempt to settle three conflicted index stages produced.

    ``settled`` is the only success there is: the adapter proved the union *and* published it at the
    destination. Everything else carries the engine's own reason instead of reducing it to a
    boolean -- the typed refusal, or the conflict record naming the table, the operation and the
    exact row identity -- so the caller can hand the explanation to the agent that has to act on it.
    ``detail`` is the seam's own reason for the cases that never reached the adapter at all (a stage
    that is not a knowledge dataset, an unreadable namespace row), and it is empty whenever the
    adapter itself answered, because then the refusal or the conflict is the explanation.
    """

    settled: bool
    conflict: MergeConflict | None = None
    refusal: KnowledgeRefusal | None = None
    detail: str = ""


def merge_conflicted_stages(
    *,
    destination: Path,
    stages: Mapping[MergeInputRole, Path],
    repository_root: Path,
    commits: ConflictCommits,
    reconciliation: AuthoredReconciliation | None = None,
) -> KnowledgeStageSettlement:
    """Merge three materialised index stages and publish the union into ``destination``.

    This is the half of a conflicted knowledge database's settlement that belongs to the application
    layer, and it exists as a function here for a structural reason rather than a stylistic one: a
    worktree module may not import the memory domain at all
    (``test_lower_ranked_owners_do_not_import_the_memory_domain``), so the module that owns the Git
    side of the conflict cannot read a dataset's identity itself. It hands this layer the three
    materialised paths and receives one typed result.

    Returns whether the dataset settled, with the complete explanation when it did not. Every way of
    not settling is reported rather than collapsed: a stage that is not a dataset, a refusal from
    the adapter (a schema disagreement above all), a conflict the engine attributed to an exact row,
    a publication that did not happen. The caller keeps the path conflicted for the agent either
    way, and the agent receives the row, the table, the reason and the advertised next action -- the
    facts the engine already produced and this seam used to discard.

    ``reconciliation`` is the caller's own authored decision for exactly one refused row, and it is
    the only way an authored resolution enters the merge. It is passed through unchanged: this layer
    decides nothing about it, and the adapter still refuses every row the caller did not name. This
    function takes no compatibility verdict: a structural merge says the result is valid, never that
    the combined knowledge is correct.
    """

    prepared = _stage_inputs(stages)
    if isinstance(prepared, KnowledgeStageSettlement):
        return prepared
    identities, repository = prepared
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
        return KnowledgeStageSettlement(settled=False, refusal=resolution)
    outcome = merge_resolved_knowledge_datasets(
        MergeRequest(
            resolution=resolution,
            databases=dict(stages),
            destination=SnapshotDestinationRequest(
                destination_path=destination,
                expected_destination=identities["left"],
            ),
            reconciliation=reconciliation,
        )
    )
    if outcome.state == "structurally_merged" and outcome.publication_state == "published":
        return KnowledgeStageSettlement(settled=True)
    return KnowledgeStageSettlement(
        settled=False, conflict=outcome.conflict, refusal=outcome.refusal
    )


def _stage_inputs(
    stages: Mapping[MergeInputRole, Path],
) -> tuple[dict[MergeInputRole, SnapshotIdentity], RepositoryIdentity] | KnowledgeStageSettlement:
    """Read the three stage identities and the namespace they share, or the seam's own reason.

    Nothing here decides anything: a stage that is not a readable dataset, or a left stage whose
    namespace row cannot be read, is exactly the case where the adapter must not be called at all,
    and the reason is reported so the agent is not left with a bare "it did not settle".
    """

    try:
        identities: dict[MergeInputRole, SnapshotIdentity] = {
            role: dataset_identity(path) for role, path in stages.items()
        }
    except (KnowledgeStorageError, apsw.Error, OSError):
        return KnowledgeStageSettlement(
            settled=False, detail="a conflicted index stage is not a readable knowledge dataset"
        )
    try:
        connection = open_read_only_database(stages["left"])
    except (apsw.Error, OSError):
        return KnowledgeStageSettlement(
            settled=False, detail="the left index stage could not be opened read-only"
        )
    try:
        rows = list(connection.execute("SELECT repository_id, authority_home FROM repository"))
    except apsw.Error:
        return KnowledgeStageSettlement(
            settled=False, detail="the left index stage carries no readable repository namespace"
        )
    finally:
        connection.close()
    if not rows:
        return KnowledgeStageSettlement(
            settled=False, detail="the left index stage carries no repository namespace row"
        )
    return identities, decode_repository_row(rows[0])


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
