"""Pure input-naming, keepability, and key helpers for recorded review answer preparation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from agents_remember.application.review_comparison_generation import leaf_generation_root
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.recorded_reads import (
    changed_observations,
    file_identity,
    has_failed_observation,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review import (
    KnowledgeReviewResult,
    ReviewEntryListResult,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult
from agents_remember.models.knowledge.review_trees import ReviewTrees, ReviewTreesResult
from agents_remember.serving.changeset_cache import (
    ContentRead,
    DirectoryRead,
    ListingRead,
    ObjectStoreRead,
    ReadInput,
    RepositoryRead,
)
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _comparison_sources_of(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    repository_id: str,
    master: str,
    leaf_id: str,
) -> tuple[ReadInput, ...]:
    """The record, the directories its generation lives in, and the stores it is read from.

    A successor record is another answer, so the names the comparison directory lists are an input
    -- its names, not the identities of the records under them: a record is an input only of the
    answers that read it (D570); a generation's listing is one too. The object census is an
    additional invalidator; direct recorded-closure checks detect a pruned dependency even
    when an unrelated new object compensates for its count.
    """

    task_root = config.coordination_root / "tasks" / repository_id / master
    sources: list[ReadInput] = [
        ContentRead(contract.contract_path),
        ListingRead(comparison_directory(task_root, leaf_id)),
        DirectoryRead(leaf_generation_root(task_root, leaf_id)),
    ]
    for root in (contract.code_repo_path, contract.memory_repo_path):
        if root is not None and Path(root).is_dir():
            sources.append(ObjectStoreRead(Path(root)))
    return tuple(dict.fromkeys(sources))


def _additions_kept(sources: tuple[ReadInput, ...]) -> tuple[ReadInput, ...]:
    """``sources`` with each object store observed as a kept success needs it (D570).

    A kept success names its objects by id and proves their closures present again before each
    reuse, so an object added to its store -- another worktree's capture or commit, in the store
    every worktree of a repository shares -- changes nothing it read and keeps it; a removal, a
    repack or an alternate-store change still moves the store's stamp. The strict census
    (:class:`ObjectStoreRead`) stays with an answer that may be a refusal, which an added object
    can answer.
    """

    return tuple(
        dict.fromkeys(
            RepositoryRead(source.root) if isinstance(source, ObjectStoreRead) else source
            for source in sources
        )
    )


def _reads_from_its_record(contract: WorktreeContract | None) -> bool:
    """Whether a leaf is answered from a durable record rather than from a live enclosure.

    A leaf whose enclosure still names a capture worktree is a live comparison: its answers depend
    on a working view this module never observes, so they are computed on every read. The entry
    catalogue has no ``history`` selector of its own, so this is how its recordedness is decided.
    """

    if contract is None:
        return False
    worktree = contract.code_worktree
    return worktree is None or not worktree.exists()


def _keepable_tree_view(value: object) -> bool:
    """Whether a tree-view answer is a function of its recorded inputs alone.

    An answer is kept only after the reuse found every recorded tree readable, so a side it names
    ``unavailable-history`` names a read that failed -- an index that could not be built over a
    readable tree -- not a state of the record; a tree that is gone is never kept anyway. A card
    answer whose ``entry_memory`` is unavailable while its after side is available names a failed
    read too (MIK-R89 v2 rule 3, Failure and Recovery). Neither is kept, so the next read tries
    again; an unconverted (``legacy-unavailable``) side is the record's own state and is kept.
    """

    if not isinstance(value, ReviewTreesResult):
        return True
    sides = (*value.knowledge_sides, *value.code_sides)
    if any(side.state == "unavailable-history" for side in sides):
        return False
    if value.entry_memory is None or value.entry_memory.state == "available":
        return True
    return not any(
        side.side == "after" and side.state == "available" for side in value.knowledge_sides
    )


def _answer_name(key: tuple[object, ...]) -> str:
    """A short name of one answer's question, for an open's log: intent, overview, files, ..."""

    kind = str(key[0]) if key else "answer"
    if kind in ("live", "live-kept"):
        return "live " + _answer_name(key[1:])
    if kind == "trees" and len(key) > 1 and isinstance(key[1], tuple):
        fields = key[1]
        view, lane = fields[6], fields[8]
        return "files" if lane else "overview" if view == "overview" else "trees"
    return kind


def _tree_key(query: ReviewTreesQuery) -> tuple[object, ...]:
    """Every field of a tree-view query that selects its answer."""

    return (
        "trees",
        query.repository_id,
        query.master,
        query.leaf_id,
        query.number,
        query.recorded,
        query.view,
        query.invariants,
        query.lane,
        query.file,
        query.entry,
        query.entry_side,
    )


def _index_inputs(trees: ReviewTrees) -> tuple[tuple[ReadInput, ...], tuple[str, ...]] | None:
    """The logical and physical inputs of these already-resolved indexes."""
    databases = (trees.before.database, trees.after.database)
    if any(path is None for path in databases):
        return None
    try:
        paths = tuple(path for path in databases if path is not None)
        identities = tuple(dataset_identity(path).logical_digest for path in paths)
    except (KnowledgeStorageError, OSError, ValueError):
        return None
    physical = tuple(
        Path(f"{path}{suffix}") for path in paths for suffix in ("", "-wal", "-journal")
    )
    return tuple(ContentRead(path) for path in physical), identities


def _whole_review(request: ReviewSurfaceRequest) -> bool:
    """A whole review read -- the task context's or one subject's: no page, no refreshed binding.

    Both are the questions whose every read was named (D570; a subject's since D574), so a live
    leaf's answer to them is kept on its comparison's witness.
    """

    return (
        request.page_of is None
        and request.continuation is None
        and request.previous_binding_digest is None
    )


def _reviewed_comparison(value: object) -> int | None:
    """The comparison number a live review answer was resolved on, or ``None``."""

    if not isinstance(value, KnowledgeReviewResult) or value.payload is None:
        return None
    movement = value.payload.external_git_movement
    if movement is None or movement.comparison is None:
        return None
    return movement.comparison.number


def _listed_comparison(value: object) -> int | None:
    """The comparison number a live subject catalogue was listed on, or ``None``."""

    if not isinstance(value, ReviewEntryListResult) or value.state != "entries":
        return None
    return value.comparison


def _unmeasured_open(value: ReviewIntentSummaryResult) -> bool:
    """A counted summary whose stored worklist measured no open count."""

    return (
        value.state == "counted"
        and value.attribution is not None
        and value.attribution.state == "counted"
        and value.unexplained_open is None
    )


def _keepable_summary(value: ReviewIntentSummaryResult) -> bool:
    return (
        value.state == "counted"
        and value.attribution is not None
        and value.attribution.state == "counted"
        and value.unexplained_open is not None
    )


def _keepable_persisted_tree(value: object) -> bool:
    return (
        isinstance(value, ReviewTreesResult)
        and value.state == "trees"
        and _keepable_tree_view(value)
        and (value.entry_inventory is None or value.entry_inventory.state == "ready")
        and (value.overview is None or value.overview.state == "ready")
    )


def _expected_reads(
    sources: tuple[ReadInput, ...], resolved_reads: Mapping[str, str]
) -> dict[str, str] | None:
    """Byte and selection identities obtained from current caller-owned resolution."""
    if has_failed_observation(resolved_reads) or changed_observations(resolved_reads):
        return None
    expected = dict(resolved_reads)
    for source in sources:
        path = source.path if isinstance(source, ContentRead) else source
        if isinstance(path, Path):
            identity = file_identity(path)
            previous = expected.get(str(path))
            if previous is not None and previous != identity:
                return None
            expected[str(path)] = identity
    return expected
