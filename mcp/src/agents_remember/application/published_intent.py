"""The repository's published intent, selected and read before a task exists (ICR-R19@v1).

The ordinary planning read is a *paired* read -- source beside its onboarding -- and what it could
not answer is "what has this repository already intended here?". :mod:`application.knowledge_read`
has served a taskless read at one exact snapshot since KS-R07; nothing in the ordinary route ever
selected a dataset, so a fresh planner read an empty scratch database instead of the repository's
own published knowledge. This module is that selection.

Three decisions, and this module owns exactly these:

* **Which dataset.** One repository-scoped location, resolved from the coordination context the
  ordinary read already carries: the memory layer's knowledge dataset at
  ``<memory_root>/knowledge.sqlite`` (:data:`PUBLISHED_DATASET_NAME`). **This route declares the
  location it reads, and the ordinary write side publishes there.** That wiring was the obligation
  this declaration recorded until ICR-R20@v1 landed it: no shipped owner computed or defaulted a
  publication destination before then -- ``IngestPublication.destination_path`` was whatever the
  caller's ``--publish-to`` named, and a run that named none committed without publishing -- so the
  ingest command now selects this one location with ``--publish``, resolving it through this module's
  :func:`published_dataset_path` and reading the published identity back through
  :func:`resolve_published_intent`. A run that names no destination and passes no ``--publish`` still
  commits without publishing, which is why the destination stays a selection rather than a default.
  The two-consecutive-task journey that proves task A's publication lands where task B's planner looks
  is still ICR-R25@v1's. Declaring it here is what makes it one shared spelling instead of two
  conventions that happen to agree. It is a *selection*, not a search: no other repository's dataset is read, no
  working directory is guessed, and naming it needs no leaf, no enclosure and no task. *Which* memory
  root that is follows the resolved context: the canonical external memory root when no enclosure is
  in scope -- the taskless planner this route exists for -- and the memory **worktree** (that task's
  own memory line) when one is. There is deliberately no fallback between the two: a publication that
  is not on the line being read is reported ``not-recorded``, never substituted from the other.
* **Which source identity.** The read context requires the source-resolution pair together or not
  at all, so the pair is resolved here from the same context -- the code repository root and the
  tree id of its current commit -- and a pair that cannot be resolved is left *unrequested* rather
  than completed with a fabrication. A recorded anchor is then observed against a real tree, and a
  blob that moved is reported as the observation it is.
* **What a caller reads.** The seeds: a source seed (``PathSeed``) for each path the ordinary read
  already asked about, and the identity seeds a caller names when it already holds a record id. The
  page itself is built by the shipped selective read (``open_read_context`` +
  ``read_knowledge_scope``) at the dataset's own snapshot. This module selects and shapes; it never
  selects rows itself, and it holds no second store, no cache and no durable state.

Every way the selection can fail is a *named* state rather than an empty success. A location holding
no file system entry at all is ``not-recorded``: a repository whose knowledge begins later is not a
repository whose history was measured as empty, and nothing about it is invented -- the source and
onboarding halves of the ordinary read continue exactly as they did. A location that holds something
other than a dataset file, a dataset that cannot be read as a dataset of this code, or one bound to
another repository's authority home is ``unusable``, and it carries the shipped refusal code for the
exact input (``selected_input_unavailable`` when there is no file to open, ``snapshot_unavailable``
when there are bytes that are not the expected dataset), naming the failed binding. A path no
recorded anchor could carry is refused as a seed instead of being answered with an absence this read
never observed, and a record identity the snapshot does not hold is the read's own
``selector_absent`` -- the named absence of a generation this snapshot does not carry. Today's
database is never substituted for a historical generation.

**Returned invariants carry their currentness (MIK-R03).** For a converted tree the block adds
``currentness``: the state of every invariant and family its pages return at the source-resolution
tree above (``stale``, ``unverifiable``, ``unrealized`` or ``current``), each entry that is not
current, the counts by state and each family's stale members. With no resolved pair every realized
invariant is ``unverifiable``. The pages themselves are unchanged, so nothing is withheld.

**A converted memory tree is selected as a tree (MIK-R23 rule 6).** When the memory root holds the
layout marker (``knowledge/layout.json``), knowledge is text and there is no published database to
select: the ordinary read's published intent is then the memory tree itself, read through the
derived index (:mod:`agents_remember.memory.knowledge_index`) built from that tree's captured state
and cached under the coordination runtime. The index is a dataset of the store's schema, so the
same ``open_read_context`` + ``read_knowledge_scope`` pair reads it; the block names the tree key
and the index state, and a ``partial`` index says so with the files that failed. A memory root
without the marker keeps the database selection above, unchanged. Only this read switches: the
write side's :func:`resolve_published_intent` still selects the database, and no writer reaches the
index.

**A path on a memory tree is read family-complete (MIK-R01).** A path seed of a converted tree is
the family-complete leaf read (:mod:`agents_remember.application.knowledge_leaf`): the path's own
invariants, each containing family's header and remaining members with their entries, then the
advertised families, in one declared row order -- the same selection, under the same manifest, that
``knowledge_read``'s ``source_context`` view returns. An identity seed keeps the scope read. Its
route-chain families follow (MIK-R05), and a path with no entry still returns them; a path with
neither is refused ``registration_absent`` with its ``routeChain`` stating ``no_governing_family``.

**A memory tree's page continues through ``knowledge_read`` (MIK-R02).** A seed read from a
converted tree is paged by the shared token threshold (:mod:`agents_remember.application.
knowledge_paging`): the block states the threshold and the walk's counts in ``page``, and its
``continuation`` is the shared token that the mounted ``knowledge_read`` accepts
(``continuationOperation: "knowledge_read"``, with the view named in ``continuationView``). The
threshold bounds the whole block, not each seed: seeds are laid out in order, and once the block
is full each remaining seed returns only its counts and a position-0 continuation. A page read
from a database keeps its ``read_knowledge_scope`` cursor and item/byte budget, which the mounted
tool does not continue; a caller reads on by the identities that page returned.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import apsw
from pydantic import ValidationError

from agents_remember.application.knowledge_before_half import read_dataset_identity
from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.knowledge_leaf.currentness import LeafCurrentness
from agents_remember.application.knowledge_leaf.pages import (
    LeafRequest,
    PreparedLeaf,
    absent_chain,
    prepare_leaf,
)
from agents_remember.application.knowledge_leaf.selection import LEAF_POLICY, LEAF_POLICY_VERSION
from agents_remember.application.knowledge_paging import threshold_block
from agents_remember.application.knowledge_paging.bindings import PagingRefusal
from agents_remember.application.knowledge_paging.block_pages import bounded_block
from agents_remember.application.knowledge_paging.currentness import WalkCurrentness
from agents_remember.application.knowledge_paging.scope_pages import (
    PreparedScope,
    ScopePageRequest,
    TreeBinding,
    prepare_scope,
)
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import bound_repository, dataset_identity
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndexCache,
    MemoryTreeError,
    default_cache_directory,
)
from agents_remember.models.knowledge.read import (
    KNOWLEDGE_READ_POLICY_VERSION,
    KnowledgeReadBudget,
    KnowledgeReadContext,
    KnowledgeReadPage,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    PathSeed,
    ReadItem,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH

__all__ = [
    "PUBLISHED_DATASET_NAME",
    "PUBLISHED_INTENT_MAX_ITEMS",
    "PublishedIntentSelection",
    "PublishedIntentSourcePair",
    "PublishedIntentUnavailable",
    "PublishedMemoryTree",
    "SelectedKnowledgeDataset",
    "converted_memory_tree",
    "memory_tree_block",
    "published_dataset_path",
    "published_intent_block",
    "read_published_intent",
    "resolve_published_intent",
    "resolve_published_memory_tree",
    "select_knowledge_dataset",
]

# The one file name a repository's published knowledge dataset occupies inside its memory layer.
# THIS ROUTE DECLARES THE LOCATION IT READS, AND THE ORDINARY WRITE SIDE PUBLISHES THERE: the ingest
# command selects it with ``--publish``, which resolves this same path through
# ``published_dataset_path`` and reads the published identity back through
# ``resolve_published_intent``. A run that names no destination and passes no ``--publish`` still
# commits without publishing, which is why the destination is a selection rather than a default.
# Before ICR-R20@v1 no shipped owner computed or defaulted a destination at all --
# ``IngestPublication.destination_path`` was whatever the caller's ``--publish-to`` named -- and the
# two-consecutive-task journey that has to prove task A's publication lands where task B's planner
# looks is still ICR-R25@v1's. Declaring it here is what gives the read side and the write side one
# shared spelling instead of two conventions that agree today.
PUBLISHED_DATASET_NAME = "knowledge.sqlite"

# The top-level policy of a tree block whose seeds were all read by the leaf read (MIK-R01).
LEAF_POLICY_VERSION_LABEL = f"{LEAF_POLICY}/{LEAF_POLICY_VERSION}"

# One bounded page per seed. A path seed selects the revisions realized at that path plus the
# families directly containing them, so the bound is a page size rather than a narrowing: a page
# that leaves items behind reports ``hasMore`` and hands back the continuation that reaches them.
# These two bounds apply to a database read only. A memory tree's pages are cut by the one shared
# token threshold instead, and the byte bound is retired with the database route.
PUBLISHED_INTENT_MAX_ITEMS = 8
_DATABASE_PAGE_MAX_UTF8_BYTES = 8192

# The two spellings a Git object identity of a tree can have. Nothing is invented for a value that
# matches neither: the pair is then left unrequested, which the read reports as "no source
# resolution was requested" rather than as a resolution that silently failed.
_TREE_ID_PATTERN = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")

# The failure classes this route models. Each is a fact about the selected publication rather than
# a defect of the caller, and the ordinary read must survive every one of them: a paired read that
# aborted because a repository's knowledge file was unreadable or unparseable would trade one gap
# for a worse one. ``ValidationError`` belongs here for that same reason -- a dataset whose stored
# namespace row this build cannot decode is an input the route was handed, not a caller mistake.
_PUBLICATION_FAILURES = (KnowledgeStorageError, apsw.Error, OSError, ValidationError)
_TREE_FAILURES = (*_PUBLICATION_FAILURES, MemoryTreeError)
# A seed read through a memory tree's index also fails when the index is not the selected tree's.
_SEED_FAILURES = (*_PUBLICATION_FAILURES, IndexMismatchError)


@dataclass(frozen=True)
class PublishedIntentSourcePair:
    """The source-resolution pair recorded anchors are observed against, resolved together.

    Both halves travel as one value because the read context refuses either alone: a tree id
    without the repository that holds it is not resolvable, and a root without a tree is an
    incomplete request rather than a narrower one.
    """

    repository_root: Path
    code_tree_id: str


@dataclass(frozen=True)
class PublishedIntentSelection:
    """The one published snapshot a repository-context resolution selected, named exactly.

    ``repository_id``, ``schema_version`` and ``logical_digest`` are read from the dataset itself
    rather than from the caller, so a caller cannot hand-write the snapshot the read is verified
    against; the read compares this resolution again against the file it opens.
    """

    database_path: Path
    repository_id: str
    schema_version: str
    logical_digest: str
    source_pair: PublishedIntentSourcePair | None
    memory_tree: PublishedMemoryTree | None = None


@dataclass(frozen=True)
class PublishedMemoryTree:
    """The converted memory tree a selection reads, through the index built for its key.

    ``index_state`` is ``complete`` or ``partial``; ``problems`` names every file a partial index
    could not read, so a page from it is never presented as complete.
    """

    memory_root: Path
    tree_key: str
    index_state: str
    problems: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PublishedIntentUnavailable:
    """Why the repository's intended publication could not be read, named by its binding.

    ``state`` separates the two facts a caller acts on differently: ``not-recorded`` is "this
    location holds no publication at all" (nothing was measured, so nothing is claimed about
    history, and source/onboarding research continues), and ``unusable`` is "something is there and
    is not a dataset this read can answer from" -- a non-file entry, bytes that are not a dataset of
    this code, or another repository's dataset. ``code`` is the shipped refusal vocabulary for the
    same fact (``selected_input_unavailable`` when there is no file to open,
    ``snapshot_unavailable`` when there are bytes that are not the expected dataset), so a caller
    branches on a code rather than on this module's prose.
    """

    state: Literal["not-recorded", "unusable"]
    code: str
    detail: str
    dataset_path: Path


@dataclass(frozen=True)
class _UnseedablePath:
    """One requested path that no recorded anchor could carry, so no seed can select it.

    It is a *refusal to seed* rather than an absence: answering it with ``registration_absent``
    would publish a negative answer this read never observed, which is the one thing a path rule
    must never do.
    """

    path: str
    detail: str


def published_dataset_path(context: CoordinationContext) -> Path:
    """The one location this repository's published knowledge dataset is selected at.

    ``context.memory_root`` is the memory layer the repository's own coordination declaration
    resolves, so the selection is repository-scoped by construction: there is no argument a caller
    can aim at another repository's dataset, and a repository that declares no memory layer has no
    published intent to read rather than a neighbour's to borrow.

    *Which* root that is follows the resolved scope, and the difference is load-bearing: inside a
    leaf enclosure the coordination context's memory root is the contract's memory **worktree** (that
    task's own memory line), and with no enclosure in scope it is the canonical external memory root
    (``kernel/coordination_context/resolver.py``, ``_effective_memory_root``). This route never
    substitutes one for the other, so a publication that is not on the line being read is reported
    ``not-recorded``.
    """

    return context.memory_root / PUBLISHED_DATASET_NAME


def resolve_published_intent(
    context: CoordinationContext,
) -> PublishedIntentSelection | PublishedIntentUnavailable:
    """Resolve the repository's intended published dataset, or name why it cannot be read.

    A converted memory tree publishes no dataset: its knowledge is text, read through the derived
    index (:func:`resolve_published_memory_tree`), and the database file it still holds is frozen
    (MIK-R37 rule 3), so it is never selected here (L23 F8) -- the answer is ``not-recorded``,
    naming the tree.
    """

    database_path = published_dataset_path(context)
    converted = converted_memory_tree(context.memory_root)
    if converted is not None:
        return PublishedIntentUnavailable(
            state="not-recorded",
            code="selected_input_unavailable",
            detail=(
                f"the memory tree {converted} is converted ({LAYOUT_MARKER_PATH}): its knowledge "
                "is text, read through the derived index of that tree (MIK-R23), so no knowledge "
                f"dataset is published at {database_path}, and the database file frozen there at "
                "the cutover is not read (MIK-R37 rule 3)"
            ),
            dataset_path=database_path,
        )
    absent = _absence_state(database_path, context.code_repository_name)
    if absent is not None:
        return absent
    try:
        identity = read_dataset_identity(database_path)
    except _PUBLICATION_FAILURES as error:
        return _unusable(database_path, f"the published dataset could not be read ({error})")
    if isinstance(identity, str):
        return _unusable(database_path, identity)
    mismatch = _authority_mismatch(database_path, context.code_repository_name)
    if mismatch is not None:
        return _unusable(database_path, mismatch)
    return PublishedIntentSelection(
        database_path=database_path,
        repository_id=identity.repository_id,
        schema_version=identity.schema_version,
        logical_digest=identity.logical_digest,
        source_pair=_source_pair(context),
    )


def resolve_published_memory_tree(
    context: CoordinationContext,
) -> PublishedIntentSelection | PublishedIntentUnavailable | None:
    """Select the converted memory tree at ``context.memory_root``, or ``None`` when unconverted.

    The tree's key is recomputed from its current state on every call (MIK-R23 rule 5), and the
    index for that key is reused or built; the selection then names the index file as the dataset
    the shipped read opens. No authority-home check applies: the index is keyed by its tree alone,
    and the tree is the one the repository's own coordination context resolves.
    """

    if converted_memory_tree(context.memory_root) is None:
        return None
    try:
        selected = _index_selection(context.memory_root, context.coordination_root)
        identity = dataset_identity(selected.database_path)
    except _TREE_FAILURES as error:
        return _unusable(context.memory_root, f"the memory tree could not be indexed ({error})")
    return PublishedIntentSelection(
        database_path=selected.database_path,
        repository_id=identity.repository_id,
        schema_version=identity.schema_version,
        logical_digest=identity.logical_digest,
        source_pair=_source_pair(context),
        memory_tree=selected.memory_tree,
    )


@dataclass(frozen=True)
class SelectedKnowledgeDataset:
    """The dataset file a knowledge read opens, and the memory tree it stands for, if any."""

    database_path: Path
    memory_tree: PublishedMemoryTree | None


def converted_memory_tree(path: Path) -> Path | None:
    """Return the converted memory tree a dataset selection names, or ``None``.

    A selection names a converted tree when it is the tree's root directory, or the published
    dataset location inside it (``<memory-root>/knowledge.sqlite``, :data:`PUBLISHED_DATASET_NAME`):
    once a tree holds the layout marker its knowledge is text, and the database file a caller was
    handed before the conversion is no longer the source of truth (MIK-R23 rule 6, MIK-R37 rule 3).
    """

    if path.is_dir() and (path / LAYOUT_MARKER_PATH).is_file():
        return path
    if path.name == PUBLISHED_DATASET_NAME and (path.parent / LAYOUT_MARKER_PATH).is_file():
        return path.parent
    return None


def select_knowledge_dataset(
    path: Path, *, coordination_root: Path | None
) -> SelectedKnowledgeDataset:
    """Resolve one caller-selected dataset path the way every knowledge read does (MIK-R23 rule 6).

    A path naming a converted memory tree resolves to the index of that tree's current state; any
    other path is returned unchanged, so an unconverted tree keeps today's database selection and
    its own refusals. Building an index needs the cache under the coordination runtime, so a
    converted selection without a coordination root is refused with :class:`MemoryTreeError`.
    """

    memory_root = converted_memory_tree(path)
    if memory_root is None:
        return SelectedKnowledgeDataset(database_path=path, memory_tree=None)
    if coordination_root is None:
        raise MemoryTreeError(
            f"{path} names the converted memory tree {memory_root}, and no coordination root is "
            "known to keep its index under"
        )
    return _index_selection(memory_root, coordination_root)


def memory_tree_block(tree: PublishedMemoryTree | None) -> dict[str, Any] | None:
    """The wire spelling of a memory-tree binding: root, tree key, index state and problems."""

    if tree is None:
        return None
    return {
        "memoryRoot": str(tree.memory_root),
        "treeId": tree.tree_key,
        "indexState": tree.index_state,
        "problems": [{"path": path, "detail": detail} for path, detail in tree.problems],
    }


def _index_selection(memory_root: Path, coordination_root: Path) -> SelectedKnowledgeDataset:
    cache = KnowledgeIndexCache(default_cache_directory(coordination_root))
    with cache.for_directory(memory_root) as index:
        state = index.state
    return SelectedKnowledgeDataset(
        database_path=index.database_path,
        memory_tree=PublishedMemoryTree(
            memory_root=memory_root,
            tree_key=state.key,
            index_state=state.state,
            problems=state.problems,
        ),
    )


def _absence_state(database_path: Path, repository_name: str) -> PublishedIntentUnavailable | None:
    """The named state of a location this route cannot read a dataset from, or ``None``.

    Absent and present-but-not-a-regular-file are different facts, and one answer cannot state both:
    a directory (or a dangling link, or a device) sitting where the publication belongs is something
    that was put there, and reporting it as "nothing is recorded here" would hide it and send the
    caller away satisfied. Only a location with no file system entry at all is ``not-recorded``.
    """

    if database_path.is_file():
        return None
    if not database_path.exists() and not database_path.is_symlink():
        return PublishedIntentUnavailable(
            state="not-recorded",
            code="selected_input_unavailable",
            detail=(
                f"no published knowledge dataset is recorded for {repository_name} at "
                f"{database_path}, so no recorded intent was read and nothing is claimed about this "
                "repository's history; source and onboarding research continue unchanged"
            ),
            dataset_path=database_path,
        )
    held = "a directory" if database_path.is_dir() else "no regular file"
    return _unusable(
        database_path,
        (
            f"the expected publication location {database_path} holds {held} rather than the "
            "repository's published dataset, so nothing could be read from it"
        ),
        code="selected_input_unavailable",
    )


def published_intent_block(
    context: CoordinationContext, source_paths: Sequence[str]
) -> dict[str, Any]:
    """The published-intent block one ordinary paired read attaches for its requested paths.

    This is the route's whole public surface for the ordinary read: resolve the repository's
    publication, seed it with the paths the caller already asked about, and read one bounded page
    per path at the dataset's own snapshot. Every failure is returned as a named state, so a caller
    that asked for source bytes still receives them.
    """

    resolved = resolve_published_memory_tree(context) or resolve_published_intent(context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return _unavailable_block(resolved)
    seeds: list[KnowledgeReadSeed | _UnseedablePath] = [_source_seed(path) for path in source_paths]
    return read_published_intent(resolved, seeds)


def read_published_intent(
    selection: PublishedIntentSelection,
    seeds: Sequence[KnowledgeReadSeed | _UnseedablePath],
    *,
    max_items: int = PUBLISHED_INTENT_MAX_ITEMS,
) -> dict[str, Any]:
    """Read one bounded page per seed at the selection's own snapshot, through the shipped read.

    The context is opened by ``open_read_context``, which resolves the snapshot the dataset at that
    path actually holds, and each page is built by ``read_knowledge_scope`` -- the same taskless,
    exact-snapshot operation every other caller of the selective read uses. ``task_ref`` is left
    unset because planning has to be able to read recorded knowledge before a leaf exists.
    """

    context = _open_context(selection)
    if isinstance(context, PublishedIntentUnavailable):
        return _unavailable_block(context)
    blocks = [_seed_block(selection, context, seed, max_items) for seed in seeds]
    if selection.memory_tree is not None:  # MIK-R02: the threshold bounds the whole block
        tree = selection.memory_tree
        scopes = WalkCurrentness(
            selection.database_path, tree.tree_key, _code_tree(selection), _candidates(blocks)
        )
        leaves = [block for block in blocks if isinstance(block, PreparedLeaf)]
        currentness = (LeafCurrentness(_code_tree(selection), leaves), scopes)
        policy = _block_policy(seeds)
        return bounded_block(blocks, lambda laid: _tree_block(selection, laid, currentness, policy))
    # A database selection prepares no scope to cut: every seed entry is already its block.
    pages = cast(list[dict[str, Any]], blocks)
    return _recorded_block(selection, [_bind_index_state(block, selection) for block in pages])


def _tree_block(
    selection: PublishedIntentSelection,
    laid: list[dict[str, Any]],
    currentness: tuple[LeafCurrentness, WalkCurrentness],
    policy: str,
) -> dict[str, Any]:
    """The complete block of a memory-tree read around its laid-out seeds.

    Everything the block carries beside its seeds -- the threshold, and each returned invariant's
    currentness (MIK-R03) at the source-resolution tree -- is added here, so it is inside the
    measured block that the threshold bounds. ``policy`` is the selection policy the block's pages
    were read under (:func:`_block_policy`).
    """

    block = _recorded_block(selection, [_bind_index_state(entry, selection) for entry in laid])
    block["policyVersion"] = policy
    block["threshold"] = threshold_block()
    leaves, scopes = currentness
    block["currentness"] = leaves.document(block["seeds"], scopes)
    pair = selection.source_pair
    if pair is not None:
        block["currentness"]["treeScope"] = _TREE_SCOPE.format(tree=pair.code_tree_id)
    return block


def _block_policy(seeds: Sequence[object]) -> str:
    """The block's top-level ``policyVersion``: the leaf read's where it applies (L01 Q5, N5).

    It is decided by what was asked, not by what was answered: a tree block whose seeds are all
    paths was read by the family-complete leaf read (MIK-R01), even when every path was refused
    (``registration_absent``, an unseedable spelling). A block with an identity seed, which the
    scope read answers, keeps the scope policy. Each page states its own policy in ``page``.
    """

    paths = all(isinstance(seed, PathSeed | _UnseedablePath) for seed in seeds)
    return LEAF_POLICY_VERSION_LABEL if paths else KNOWLEDGE_READ_POLICY_VERSION


def _code_tree(selection: PublishedIntentSelection) -> CodeTree | None:
    pair = selection.source_pair
    return None if pair is None else CodeTree(pair.repository_root, pair.code_tree_id)


def _candidates(blocks: list[dict[str, Any] | PreparedScope | PreparedLeaf]) -> list[Any]:
    """Every row a scope seed of the block could carry, for the one scope currentness computation.

    A leaf seed computed its own selection's currentness when it was prepared.
    """

    return [
        [dict(row.body) for row in block.rows] if isinstance(block, PreparedScope) else block
        for block in blocks
        if not isinstance(block, PreparedLeaf)
    ]


# The currentness block's statement of which tree it observed (MIK-R03, L03 ruling Q1): the tree
# this route resolved for the source it returns, which is a commit's tree.
_TREE_SCOPE = (
    "states observed at the committed code tree {tree} this read resolved; uncommitted "
    "working-tree edits are not reflected"
)


def _bind_index_state(block: dict[str, Any], selection: PublishedIntentSelection) -> dict[str, Any]:
    """Mark one page read from a memory tree with its index state (MIK-R23, Failure).

    A page from a ``partial`` index is never presented as complete: ``enumerationComplete`` is
    forced to ``false`` -- the page enumerated what the index holds, and the index does not hold the
    whole tree -- and ``indexState`` says why. A page from a database is returned unchanged.
    """

    tree = selection.memory_tree
    if tree is None or block.get("state") != "page":
        return block
    bound = {**block, "indexState": tree.index_state}
    if tree.index_state != "complete":
        bound["enumerationComplete"] = False
    return bound


def _source_pair(context: CoordinationContext) -> PublishedIntentSourcePair | None:
    """The source-resolution pair this repository's current commit defines, or ``None``.

    ``None`` is the honest answer for a code root that is not a Git repository, a Git that cannot
    answer, or an answer that is not an object id: the pair is then *unrequested*, which the read
    reports as exactly that. HEAD is used here as the tree a planner is planning against -- not as
    a substitute for a recorded endpoint, which is the review's own binding and not this route's.
    """

    root = context.code_repository_root
    if root is None:
        return None
    try:
        completed = run_git(root, ["rev-parse", "HEAD^{tree}"])
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    tree_id = completed.stdout.strip()
    if not _TREE_ID_PATTERN.match(tree_id):
        return None
    return PublishedIntentSourcePair(repository_root=root, code_tree_id=tree_id)


def _authority_mismatch(database_path: Path, repository_name: str) -> str | None:
    """The reason this publication belongs to another repository, or ``None`` when it does not.

    A namespace is an id, so it cannot say which repository a dataset belongs to; the authority
    home the dataset records is the row that can. Checking it is what makes "never silently select
    another repository" a fact this route verifies instead of a claim it makes.
    """

    try:
        authority_home = _bound_authority_home(database_path)
    except _PUBLICATION_FAILURES as error:
        return f"the published dataset could not be read ({error})"
    if authority_home == repository_name:
        return None
    return (
        f"the dataset at {database_path} is bound to the authority home {authority_home!r}, not "
        f"to {repository_name!r}; this route selects no other repository's publication"
    )


def _bound_authority_home(database_path: Path) -> str | None:
    """The authority home the dataset itself records, read through a read-only handle."""

    connection = open_read_only_database(database_path)
    try:
        repository = bound_repository(connection)
    finally:
        connection.close()
    return None if repository is None else repository.authority_home


def _open_context(
    selection: PublishedIntentSelection,
) -> KnowledgeReadContext | PublishedIntentUnavailable:
    pair = selection.source_pair
    try:
        return open_read_context(
            selection.database_path,
            selection.repository_id,
            repository_root=None if pair is None else pair.repository_root,
            code_tree_id=None if pair is None else pair.code_tree_id,
        )
    except _PUBLICATION_FAILURES as error:
        return _unusable(
            selection.database_path, f"the resolved snapshot could not be opened ({error})"
        )


def _source_seed(path: str) -> KnowledgeReadSeed | _UnseedablePath:
    """The path seed one requested path spells, or the refusal that it spells none."""

    try:
        return PathSeed(path=path)
    except ValidationError as error:
        first = error.errors()[0] if error.errors() else {}
        reason = str(first.get("msg", error))
        return _UnseedablePath(
            path=path,
            detail=(
                f"the requested path {path!r} is not a spelling a recorded source anchor can "
                f"carry, so no seed selects it and no absence is claimed for it ({reason})"
            ),
        )


def _seed_block(
    selection: PublishedIntentSelection,
    context: KnowledgeReadContext,
    seed: KnowledgeReadSeed | _UnseedablePath,
    max_items: int,
) -> dict[str, Any] | PreparedScope | PreparedLeaf:
    if isinstance(seed, _UnseedablePath):
        return _refused_block(
            {"kind": "path", "path": seed.path}, "invalid_payload", seed.detail, None
        )
    seed_json = _seed_json(seed)
    if seed_json is None:
        return _unaddressable_seed_block(seed)
    try:
        if selection.memory_tree is not None:  # MIK-R02: paged by the shared threshold
            return _tree_page_block(selection, selection.memory_tree, context, seed, seed_json)
        result = read_knowledge_scope(
            selection.database_path,
            context,
            KnowledgeReadRequest(
                seed=seed,
                budget=KnowledgeReadBudget(
                    max_items=max_items, max_utf8_bytes=_DATABASE_PAGE_MAX_UTF8_BYTES
                ),
            ),
        )
    except _SEED_FAILURES as error:
        return _refused_block(
            seed_json,
            "snapshot_unavailable",
            f"the published snapshot could not be read for this seed ({error})",
            None,
        )
    return _result_block(seed_json, result)


def _tree_page_block(
    selection: PublishedIntentSelection,
    tree: PublishedMemoryTree,
    context: KnowledgeReadContext,
    seed: KnowledgeReadSeed,
    seed_json: dict[str, Any],
) -> PreparedScope | PreparedLeaf | dict[str, Any]:
    """A seed's selection in a memory tree, ready to be cut within the block, or its refusal.

    A path seed is read by the family-complete leaf read (MIK-R01), an identity seed by the scope
    read.
    """

    if isinstance(seed, PathSeed):
        leaf = prepare_leaf(
            LeafRequest(
                index_path=selection.database_path,
                memory_tree_id=tree.tree_key,
                index_state=tree.index_state,
                path=seed.path,
                code_tree=_code_tree(selection),
            )
        )
        if isinstance(leaf, PreparedLeaf):
            return leaf
        if isinstance(leaf, PagingRefusal):  # pragma: no cover - page 1 carries no continuation
            return _refused_block(seed_json, leaf.code, leaf.detail, None)
        return {**_refusal_block(seed_json, leaf), **absent_chain(seed.path)}
    paged = prepare_scope(
        ScopePageRequest(
            database_path=selection.database_path,
            context=context,
            seed=seed,
            tree=TreeBinding(tree_id=tree.tree_key, index_state=tree.index_state),
        )
    )
    if isinstance(paged, KnowledgeReadResult):
        return _result_block(seed_json, paged)
    if isinstance(paged, PagingRefusal):  # pragma: no cover - a first page carries no continuation
        return _refused_block(seed_json, paged.code, paged.detail, None)
    return paged


def _seed_json(seed: object) -> dict[str, Any] | None:
    """The seed's own recorded spelling, or ``None`` when the value is not a seed model at all.

    ``read_published_intent`` is an exported application function, so it can be reached with a value
    its annotation describes but the runtime never checked. Asking the value for its own dump and
    answering ``None`` when it has none is what keeps that mistake a named refusal instead of an
    ``AttributeError`` raised from inside this read.
    """

    dumper = getattr(seed, "model_dump", None)
    if not callable(dumper):
        return None
    dumped = dumper(mode="json")
    return dumped if isinstance(dumped, dict) else None


def _unaddressable_seed_block(seed: object) -> dict[str, Any]:
    """The refusal for a value that is not one of the two typed seeds this route addresses.

    A caller's mistake is stated as a refusal with the offending value's Python type (and a bounded
    repr for a scalar) rather than as a defect of this read: the same discipline every other input on
    this route follows, and the reason a seed that selected nothing is never reported as an absence.
    """

    described: dict[str, Any] = {"kind": "unaddressable", "python_type": type(seed).__name__}
    if isinstance(seed, (str, bytes, int, float, bool)):
        described["value"] = repr(seed)[:120]
    return _refused_block(
        described,
        "invalid_payload",
        (
            "the requested seed is not one of the typed seeds this route addresses (a path seed or "
            "an invariant/family identity or revision seed), so no recorded scope was selected for "
            "it and no absence is claimed"
        ),
        None,
    )


def _result_block(seed_json: dict[str, Any], result: KnowledgeReadResult) -> dict[str, Any]:
    """One read result as the exact identities and authored words it carries, or its refusal."""

    if result.state == "refused" or result.refusal is not None:
        return _refusal_block(seed_json, result.refusal)
    if result.page is None:  # pragma: no cover - the result model carries one outcome
        return _no_outcome_block(seed_json)
    return _page_block(seed_json, result, result.page)


def _refusal_block(seed_json: dict[str, Any], refusal: KnowledgeRefusal | None) -> dict[str, Any]:
    """The block for a refused read, carrying the read's own code, detail and next action."""

    if refusal is None:  # pragma: no cover - the result model carries one outcome
        return _no_outcome_block(seed_json)
    return _refused_block(seed_json, refusal.code, refusal.detail, refusal.next_action)


def _no_outcome_block(seed_json: dict[str, Any]) -> dict[str, Any]:
    """The block for a result that carried neither outcome: a refusal, never a fabricated page."""

    return _refused_block(
        seed_json, "snapshot_unavailable", "the read returned neither a page nor a refusal", None
    )


def _page_block(
    seed_json: dict[str, Any], result: KnowledgeReadResult, page: KnowledgeReadPage
) -> dict[str, Any]:
    """One bounded page: its exact snapshot, its items and the continuation that reaches the rest.

    ``continuationOperation`` names which operation continues the token the page mints. It is stated
    because the answer is not the obvious one: this cursor continues ``read_knowledge_scope``, while
    the mounted ``knowledge_read`` tool continues *views* and therefore refuses it. A caller that
    needs more than this page carries reads on by the identity the page returned instead.
    """

    return {
        "seed": seed_json,
        "state": "page",
        "snapshot": None if result.snapshot is None else result.snapshot.logical_digest,
        "seedDigest": result.seed_digest,
        "manifestDigest": result.manifest_digest,
        "items": [_item_json(item) for item in page.items],
        "counts": page.counts.model_dump(mode="json"),
        "hasMore": page.has_more,
        "enumerationComplete": page.enumeration_complete,
        "continuation": page.continuation,
        "continuationOperation": "read_knowledge_scope",
    }


def _item_json(item: ReadItem) -> dict[str, Any]:
    """One selected item exactly as the read selected it: its identities, words and anchor.

    The item is dumped by its own model rather than re-spelled field by field, so a field the read
    adds later travels without this module deciding whether it matters. ``exclude_none`` keeps the
    page readable without ever dropping a value the read recorded.
    """

    return item.model_dump(mode="json", exclude_none=True)


def _refused_block(
    seed_json: dict[str, Any], code: str, detail: str, next_action: str | None
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "seed": seed_json,
        "state": "refused",
        "refusalCode": code,
        "refusalDetail": detail,
    }
    if next_action:
        block["nextAction"] = next_action
    return block


def _recorded_block(
    selection: PublishedIntentSelection, seeds: list[dict[str, Any]]
) -> dict[str, Any]:
    pair = selection.source_pair
    return {
        "state": "recorded",
        "datasetPath": str(selection.database_path),
        "repositoryId": selection.repository_id,
        "schemaVersion": selection.schema_version,
        "snapshot": selection.logical_digest,
        "policyVersion": KNOWLEDGE_READ_POLICY_VERSION,
        "sourceResolution": (
            None
            if pair is None
            else {
                "repositoryRoot": str(pair.repository_root),
                "codeTreeId": pair.code_tree_id,
            }
        ),
        "seeds": seeds,
        **_memory_tree_block(selection.memory_tree),
    }


def _memory_tree_block(tree: PublishedMemoryTree | None) -> dict[str, Any]:
    """The memory-tree binding of a selection read through the index, or nothing for a database."""

    block = memory_tree_block(tree)
    return {} if block is None else {"memoryTree": block}


def _unavailable_block(unavailable: PublishedIntentUnavailable) -> dict[str, Any]:
    """The block for a publication that could not be read, with the failed binding named."""

    return {
        "state": unavailable.state,
        "datasetPath": str(unavailable.dataset_path),
        "refusalCode": unavailable.code,
        "refusalDetail": unavailable.detail,
        "seeds": [],
    }


def _unusable(
    database_path: Path, detail: str, *, code: str = "snapshot_unavailable"
) -> PublishedIntentUnavailable:
    """One ``unusable`` publication, carrying the shipped code for the exact input it was handed."""

    return PublishedIntentUnavailable(
        state="unusable",
        code=code,
        detail=detail,
        dataset_path=database_path,
    )
