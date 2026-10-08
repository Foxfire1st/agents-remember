"""Whether the bound comparison's own knowledge records a realization at one unchanged path (ICR-R03).

The review's inventory lists changed paths, and that population stays exactly what the change set
measured. A realization can nevertheless be anchored in a file the task did not change -- a statement
realized by code that already existed -- and a reviewer reading that realization needs those bytes.
This module answers the single question that decides whether such a path may be opened: *does a
realization recorded in the knowledge this comparison binds link this path?* It reads no source and
decides nothing else.

* **Which comparison.** The requested tree pair names it. When the pair is the one the leaf's review
  binds now, its knowledge is the pair of snapshots that same resolution bound -- a live leaf's two
  halves, or a closed leaf's reopened generation. When the leaf has moved past the pair, its knowledge
  is the retained snapshots of the latest comparison generation this leaf published for exactly that
  pair, reopened through the generation owner; with no such record, no knowledge is bound to the
  requested comparison and nothing is admitted. The knowledge the leaf holds *now* is never stood in
  for a superseded comparison's, and no other leaf's, master's or published dataset is consulted.
* **Which fact.** A recorded realization claim whose anchor path is exactly this path, in the before
  or the after snapshot. The requested spelling must already be the one a recorded anchor can carry:
  the path-seed shape rule validates it and must hand back the *same* string, because that rule strips
  surrounding whitespace and a normalized spelling would link a path no anchor names (a padded
  ``" src/x.py"`` is not ``"src/x.py"``, and Git allows both as names). The question is then asked
  with the read owner's own exact claim-at-path query (the one its path seed selects through), after
  the snapshot's identity is resolved from the file itself. That query does not expand families, so a
  path's answer cannot fail on the size of the scope around it. A half a generation did not retain is
  not read: its absence is the record's own statement, not a file to look for elsewhere.
* **Proofs of a tree comparison.** A tree comparison's knowledge is the derived index of each memory
  tree (MIK-R23); a proof entry anchored at the path links it too, so a proof's focused card opens its
  full file like a realization's (MIK-R31). A dataset records no proofs and is asked nothing more.
* **What is not a criterion.** Whether the anchor's recorded bytes still match the tree is an
  observation the realization's own read reports; the link itself is authored data, and the content
  read beside it shows the bytes the requested endpoints actually hold.

A snapshot that could not be read supports no admission and no negative conclusion: when no half links
the path and any half (or the comparison's knowledge itself) could not be read, the answer is
*undetermined* rather than "not linked", and the sentence this module returns says which half answered
what, so a refusal can state the cause instead of asserting an absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import apsw
from pydantic import ValidationError

from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    review_namespace,
)
from agents_remember.application.review_committed_leaf import resolve_committed_leaf_review
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    KnowledgeSide,
    read_generation_refs,
    read_manifest,
    task_root_for_review,
)
from agents_remember.application.review_tree_comparison import (
    comparison_directory,
    comparison_records,
    reopened_trees,
    tree_resolution,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.read_queries import fetch_realizations_at_path
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.read import PathSeed
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_source_content import ReviewSourceContentRequest

__all__ = ["RealizationLink", "recorded_realization_link"]

# The two halves in the order a sentence names them.
_SIDES: tuple[KnowledgeSide, ...] = ("before", "after")

_SideState = Literal["linked", "not_linked", "unread"]


@dataclass(frozen=True)
class RealizationLink:
    """The answer for one path: the halves whose recorded realizations link it, and what was read.

    ``linking_sides`` is empty exactly when no read half records a realization at the path, and
    ``detail`` is then the sentence stating why -- which halves said "not recorded", which could not
    be read, and which comparison they belonged to -- so the refusal can carry it verbatim.
    ``determined`` is false when the negative was not established: no half links the path and some
    knowledge this comparison binds could not be read, so "not linked" would be a claim nobody measured.
    """

    linking_sides: tuple[KnowledgeSide, ...]
    detail: str
    determined: bool = True
    # Every half that was not read does not exist at all: the leaf's knowledge was never created, so
    # the remedy is to initialize it rather than to restore or repair a damaged snapshot.
    never_initialized: bool = False

    @property
    def linked(self) -> bool:
        return bool(self.linking_sides)


@dataclass(frozen=True)
class _SideReading:
    side: KnowledgeSide
    state: _SideState
    detail: str
    absent: bool = False


@dataclass(frozen=True)
class _BoundKnowledge:
    """The comparison whose knowledge answers, and the sentence naming which record it is."""

    resolution: ReviewCandidateResolution
    description: str


def recorded_realization_link(
    config: McpRuntimeConfig,
    request: ReviewSourceContentRequest,
    resolved: ReviewCandidateResolution,
) -> RealizationLink:
    """Whether a realization recorded in the requested comparison's knowledge links the path."""

    bound = _bound_knowledge(config, request, resolved)
    if isinstance(bound, RealizationLink):
        return bound
    if not _anchor_spelling(request.path):
        return RealizationLink(
            linking_sides=(),
            detail=(
                f"the requested spelling {request.path!r} is not exactly a path a recorded source "
                f"anchor can carry, so no realization in {bound.description} can link it and it is "
                "not matched to a nearby spelling"
            ),
        )
    readings = tuple(
        _bound_side_reading(bound, side, database, request.path)
        for side, database in _halves(bound)
    )
    return _link_of(bound, readings)


def _bound_side_reading(
    bound: _BoundKnowledge, side: KnowledgeSide, database: Path, path: str
) -> _SideReading:
    """A positive link survives a lost sibling side; unavailable/partial trees prove no absence."""
    resolution = bound.resolution
    for name, state, detail in resolution.knowledge_unavailable:
        if name == side:
            return _SideReading(side, "unread", f"the {side} knowledge side is {state}: {detail}")
    trees = resolution.trees
    wire = None if trees is None else next(one for one in trees.sides() if one.side == side)
    if wire is not None and wire.state != "available":
        return _SideReading(
            side, "unread", f"the {side} memory tree {wire.tree} is {wire.state}: {wire.detail}"
        )
    namespace = review_namespace(resolution.repository_id, database)
    reading = _side_reading(side, database, namespace, path)
    if wire is not None and wire.index_state == "partial" and reading.state != "linked":
        return _SideReading(
            side,
            "unread",
            f"the {side} memory tree {wire.tree} has a partial index: {wire.problems}",
        )
    return reading


def _link_of(bound: _BoundKnowledge, readings: tuple[_SideReading, ...]) -> RealizationLink:
    """The answer the halves' readings give: who links, whether the negative was measured."""

    linking: tuple[KnowledgeSide, ...] = tuple(
        reading.side for reading in readings if reading.state == "linked"
    )
    unread = [reading for reading in readings if reading.state == "unread"]
    return RealizationLink(
        linking_sides=linking,
        detail=_sentence(bound, readings),
        determined=bool(linking) or not unread,
        never_initialized=bool(unread) and all(reading.absent for reading in unread),
    )


def _anchor_spelling(path: str) -> bool:
    """Whether ``path`` is, unchanged, a spelling the stored-anchor shape rule admits.

    The rule normalizes (it strips surrounding whitespace), so validating is not enough: the
    validated value must be the requested string itself, or a padded spelling would be answered with
    the link of the path it normalizes to.
    """

    try:
        return PathSeed(path=path).path == path
    except ValidationError:
        return False


# --- which comparison's knowledge ---------------------------------------------------------------


def _bound_knowledge(
    config: McpRuntimeConfig,
    request: ReviewSourceContentRequest,
    resolved: ReviewCandidateResolution,
) -> _BoundKnowledge | RealizationLink:
    """The comparison the requested pair names, or the sentence that no knowledge is bound to it.

    The baseline has already been required to be the resolution's own, so only the after tree can
    differ; when it does, the pair is a superseded one and only a generation that recorded exactly
    that pair holds its knowledge.
    """

    if request.after_code_tree_id == resolved.candidate_code_tree_id:
        return _BoundKnowledge(
            resolution=resolved,
            description="the knowledge this leaf's review binds to the requested pair",
        )
    task_root = task_root_for_review(config, request.repository_id, request.master)
    if resolved.contract is not None:
        for record in reversed(comparison_records(task_root, resolved.leaf_id)):
            if (record.code_base.commit or record.code_base.tree, record.code_candidate.tree) != (
                request.before_code_tree_id,
                request.after_code_tree_id,
            ):
                continue
            trees = reopened_trees(
                config.coordination_root, record, comparison_directory(task_root, resolved.leaf_id)
            )
            return _BoundKnowledge(
                resolution=tree_resolution(resolved.repository_id, resolved.contract, trees),
                description=f"the memory trees recorded by comparison {record.number} for exactly the requested source pair",
            )
    for ref in reversed(read_generation_refs(task_root, resolved.leaf_id)):
        try:
            manifest = read_manifest(ref.directory / COMPARISON_MANIFEST_NAME)
        except KnowledgeStorageError:
            continue
        source = manifest.source
        if (source.baseline_code_tree_id, source.candidate_code_tree_id) != (
            request.before_code_tree_id,
            request.after_code_tree_id,
        ):
            continue
        reopened = resolve_committed_leaf_review(
            config,
            request.repository_id,
            request.master,
            request.leaf_id,
            generation_id=ref.generation_id,
        )
        if isinstance(reopened, ReviewRefusal):
            return RealizationLink(
                linking_sides=(),
                detail=(
                    f"the comparison generation {ref.generation_id} records the requested pair but "
                    f"could not be reopened ({reopened.detail}), so its knowledge was not read"
                ),
                determined=False,
            )
        return _BoundKnowledge(
            resolution=reopened,
            description=(
                f"the knowledge retained by comparison generation {ref.generation_id} (index "
                f"{ref.generation_index}), which recorded exactly the requested pair"
            ),
        )
    return RealizationLink(
        linking_sides=(),
        detail=(
            f"the requested pair is not the one this leaf's review binds now (it binds "
            f"{resolved.candidate_code_tree_id}), and no comparison generation this leaf published "
            "records exactly the requested pair, so no knowledge is bound to the requested "
            "comparison; the knowledge the leaf holds now is not substituted for it"
        ),
    )


def _halves(bound: _BoundKnowledge) -> tuple[tuple[KnowledgeSide, Path], ...]:
    """The snapshot files this comparison binds, leaving out a half its generation did not retain."""

    resolution = bound.resolution
    databases = {"before": resolution.baseline_database, "after": resolution.candidate_database}
    closed = resolution.closed_leaf
    manifest = None if closed is None else closed.manifest
    return tuple(
        (side, databases[side])
        for side in _SIDES
        if manifest is None or manifest.knowledge_side(side).state == "retained"
    )


# --- one half's answer --------------------------------------------------------------------------


def _side_reading(side: KnowledgeSide, database: Path, namespace: str, path: str) -> _SideReading:
    """Ask one snapshot, by the read owner's exact claim-at-path query, whether it links ``path``.

    The snapshot's identity is resolved from the file first (namespace, schema generation), so a file
    that is not the dataset this comparison bound is ``unread`` with its cause rather than an absence.
    """

    if not database.is_file():
        return _SideReading(
            side, "unread", f"the {side} snapshot is absent at {database}", absent=True
        )
    try:
        context = open_read_context(database, namespace)
        connection = open_read_only_database(database)
        try:
            rows = fetch_realizations_at_path(connection, context.repository_id, path)
            proved = not rows and _proof_at_path(connection, path)
        finally:
            connection.close()
    except (KnowledgeStorageError, OSError, ValueError, apsw.Error) as error:
        return _SideReading(
            side,
            "unread",
            f"the {side} snapshot at {database} could not be read ({type(error).__name__}: {error})",
        )
    if rows:
        return _SideReading(side, "linked", f"the {side} snapshot records a realization here")
    if proved:
        return _SideReading(side, "linked", f"the {side} snapshot records a proof here")
    return _SideReading(side, "not_linked", f"the {side} snapshot records none")


def _proof_at_path(connection: apsw.Connection, path: str) -> bool:
    """Whether a derived knowledge index (MIK-R23) records a proof entry anchored at ``path``.

    Proof entries (MIK-R28) have no table in the store's logical schema, so a tree comparison's index
    answers them from its own ``ix_entry``; a dataset has no such table and answers nothing here. A
    proof's card offers its full file like every other card (MIK-R31 rule 5).
    """

    listed = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ix_entry'"
    ).fetchall()
    if not listed:
        return False
    found = connection.execute(
        "SELECT 1 FROM ix_entry WHERE kind = 'proof' AND path = ? LIMIT 1", (path,)
    ).fetchall()
    return bool(found)


def _sentence(bound: _BoundKnowledge, readings: tuple[_SideReading, ...]) -> str:
    """One sentence naming the comparison and what each of its halves answered."""

    if not readings:
        return f"{bound.description} retains neither half, so no realization links this path"
    answers = "; ".join(reading.detail for reading in readings)
    return f"in {bound.description}: {answers}"
