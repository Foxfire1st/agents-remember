"""Published text knowledge, selected through the derived index of its memory tree."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import apsw
from pydantic import ValidationError

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
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndexCache,
    MemoryTreeError,
    default_cache_directory,
)
from agents_remember.models.knowledge.read import (
    KNOWLEDGE_READ_POLICY_VERSION,
    KnowledgeReadContext,
    KnowledgeReadPage,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    PathSeed,
    ReadItem,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH

__all__ = [
    "PublishedIntentSelection",
    "PublishedIntentSourcePair",
    "PublishedIntentUnavailable",
    "PublishedMemoryTree",
    "SelectedKnowledgeDataset",
    "converted_memory_tree",
    "memory_tree_block",
    "published_intent_block",
    "read_published_intent",
    "resolve_published_intent",
    "resolve_published_memory_tree",
    "select_knowledge_dataset",
]

# The top-level policy of a tree block whose seeds were all read by the leaf read (MIK-R01).
LEAF_POLICY_VERSION_LABEL = f"{LEAF_POLICY}/{LEAF_POLICY_VERSION}"

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


def resolve_published_intent(
    context: CoordinationContext,
) -> PublishedIntentSelection | PublishedIntentUnavailable:
    """Resolve text knowledge through its derived index, or refuse the legacy memory format."""
    selected = resolve_published_memory_tree(context)
    if selected is not None:
        return selected
    return _unusable(
        context.memory_root,
        "this memory root is unconverted; sync it across the text-storage boundary or run the knowledge conversion command before reading it",
        code="legacy-format",
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
    """Return a directory holding the converted layout marker."""
    return path if path.is_dir() and (path / LAYOUT_MARKER_PATH).is_file() else None


def select_knowledge_dataset(
    path: Path, *, coordination_root: Path | None
) -> SelectedKnowledgeDataset:
    """Select the derived index of a converted memory root; no database path is accepted."""
    memory_root = converted_memory_tree(path)
    if memory_root is None:
        raise MemoryTreeError(
            f"legacy-format: {path} is not a converted memory root; sync across the text-storage boundary or convert it"
        )
    if coordination_root is None:
        raise MemoryTreeError(
            f"{path} names converted memory and no coordination root is known to keep its derived index cache"
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


def published_intent_block(
    context: CoordinationContext, source_paths: Sequence[str]
) -> dict[str, Any]:
    """The published-intent block one ordinary paired read attaches for its requested paths.

    This is the route's whole public surface for the ordinary read: resolve the repository's
    publication, seed it with the paths the caller already asked about, and read one bounded page
    per path at the dataset's own snapshot. Every failure is returned as a named state, so a caller
    that asked for source bytes still receives them.
    """

    resolved = resolve_published_intent(context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return _unavailable_block(resolved)
    seeds: list[KnowledgeReadSeed | _UnseedablePath] = [_source_seed(path) for path in source_paths]
    return read_published_intent(resolved, seeds)


def read_published_intent(
    selection: PublishedIntentSelection, seeds: Sequence[KnowledgeReadSeed | _UnseedablePath]
) -> dict[str, Any]:
    """Read pages through the index bound to the selected memory tree."""
    tree = selection.memory_tree
    if tree is None:
        return _unavailable_block(
            _unusable(
                selection.database_path,
                "legacy-format: this selection names no converted memory tree",
                code="legacy-format",
            )
        )
    context = _open_context(selection)
    if isinstance(context, PublishedIntentUnavailable):
        return _unavailable_block(context)
    blocks = [_seed_block(selection, context, seed) for seed in seeds]
    scopes = WalkCurrentness(
        selection.database_path, tree.tree_key, _code_tree(selection), _candidates(blocks)
    )
    leaves = [block for block in blocks if isinstance(block, PreparedLeaf)]
    currentness = (LeafCurrentness(_code_tree(selection), leaves), scopes)
    return bounded_block(
        blocks, lambda laid: _tree_block(selection, laid, currentness, _block_policy(seeds))
    )


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
) -> dict[str, Any] | PreparedScope | PreparedLeaf:
    if isinstance(seed, _UnseedablePath):
        return _refused_block(
            {"kind": "path", "path": seed.path}, "invalid_payload", seed.detail, None
        )
    seed_json = _seed_json(seed)
    if seed_json is None:
        return _unaddressable_seed_block(seed)
    assert selection.memory_tree is not None
    try:
        return _tree_page_block(selection, selection.memory_tree, context, seed, seed_json)
    except _SEED_FAILURES as error:
        return _refused_block(
            seed_json,
            "snapshot_unavailable",
            f"the selected memory tree could not be read ({error})",
            None,
        )


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
