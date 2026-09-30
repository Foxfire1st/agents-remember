"""Pages of the family-complete leaf read, cut by the shared threshold (MIK-R01 with MIK-R02).

The leaf read is one more response the paging seam (:mod:`agents_remember.application.
knowledge_paging`) pages: its ordered selection is :func:`~.selection.leaf_rows`, its manifest the
structure's digest, its policy ``family-complete-leaf/v1``. The same :class:`PreparedLeaf` builds
page 1 inside a ``read_ar_files`` block and every page of ``knowledge_read``'s ``source_context``
view, so both surfaces return one selection under one manifest (rule 6).

**A page that continues a family starts with the family's header reference as a literal row**
(MIK-R02 rule 4, carried from L02): ``rows[0]`` is the ``family_header_reference`` row, which
repeats an identity already returned and so is not counted as a returned row.

Each page states the memory tree it was read from (rule 9), the selection's counts with the walk's
``rowsReturned``/``rowsRemaining`` (rule 8), and the shared continuation, which ``knowledge_read``
resumes with ``view: "source_context"``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.knowledge_currentness.state import (
    Currentness,
    FamilyCurrentness,
    invariant_currentness,
)
from agents_remember.application.knowledge_currentness.surface import CURRENTNESS_FAILURES
from agents_remember.application.knowledge_leaf.selection import (
    LEAF_POLICY,
    LEAF_POLICY_VERSION,
    LeafStructure,
    leaf_counts,
    leaf_rows,
    select_leaf,
)
from agents_remember.application.knowledge_paging.bindings import (
    PagingRefusal,
    mint_continuation,
    position_refusal,
)
from agents_remember.application.knowledge_paging.pager import (
    PageBinding,
    PageCut,
    PageRow,
    page_block,
)
from agents_remember.memory.knowledge.read_refusals import registration_absent_refusal
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.models.knowledge.continuation import KnowledgeContinuation
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "LEAF_VIEW",
    "LeafRequest",
    "PreparedLeaf",
    "prepare_leaf",
]

# The ``knowledge_read`` view a leaf walk resumes on: the source context of its seed path.
LEAF_VIEW: Final = "source_context"


@dataclass(frozen=True)
class LeafRequest:
    """One leaf page: the index it reads, the tree it is bound to, the seed and where it starts.

    ``code_tree`` is the tree entries are observed at (MIK-R03); its ID is what the walk binds.
    ``index_state`` is the index's completeness: a page read from a ``partial`` index is never
    presented as complete (MIK-R23, Failure).
    """

    index_path: Path
    memory_tree_id: str
    index_state: str
    path: str
    code_tree: CodeTree | None = None
    resume: KnowledgeContinuation | None = None


@dataclass(frozen=True)
class PreparedLeaf:
    """One seed's whole leaf selection, ready to be cut: its rows, its binding and its start.

    ``rest`` are the seeds queued behind this one (a collapsed ``read_ar_files`` tail), and
    ``next_manifest`` the manifest of the first of them, which the move on to it binds.
    """

    request: LeafRequest
    structure: LeafStructure
    rows: tuple[PageRow, ...]
    counts: dict[str, Any]
    currentness: Currentness
    binding: PageBinding
    position: int
    rest: tuple[dict[str, str], ...] = ()
    next_manifest: str | None = None

    @property
    def seed_json(self) -> dict[str, str]:
        return {"kind": "path", "path": self.request.path}

    def render(self, cut: PageCut) -> dict[str, Any]:
        """The seed's page for one cut of its rows."""

        return _page(self, cut)

    def deferred(self) -> dict[str, Any]:
        """The seed when its block is already full: its counts and a position-0 continuation."""

        total = len(self.rows)
        return {
            "seed": self.seed_json,
            "state": "deferred",
            "counts": {"total": total, "returned": 0, "remaining": total},
            **self._first_continuation(()),
        }

    def collapsed(self, queued: Sequence[PreparedLeaf]) -> dict[str, Any]:
        """This seed and ``queued`` as one deferred entry whose one continuation walks them all."""

        return {
            "seeds": [self.seed_json, *(one.seed_json for one in queued)],
            "state": "deferred",
            "counts": {"total": sum(len(one.rows) for one in (self, *queued)), "returned": 0},
            **self._first_continuation(tuple(one.seed_json for one in queued)),
        }

    def _first_continuation(self, rest: tuple[dict[str, str], ...]) -> dict[str, Any]:
        token = mint_continuation(
            self.binding, response="leaf", view=LEAF_VIEW, seeds=(self.seed_json, *rest), position=0
        )
        return {
            "continuation": token,
            "continuationOperation": "knowledge_read",
            "continuationView": LEAF_VIEW,
        }


def prepare_leaf(request: LeafRequest) -> PreparedLeaf | KnowledgeRefusal | PagingRefusal:
    """Select a seed path's leaf and check a continuation against it, or return the refusal.

    A path with no live entry is ``registration_absent`` (MIK-R01, Failure).
    """

    with KnowledgeIndex(request.index_path, expected_key=request.memory_tree_id) as index:
        structure = select_leaf(index, request.path)
        if structure is None:
            return registration_absent_refusal(path=request.path, with_proofs=True)
        currentness = _currentness(index, structure, request.code_tree)
        rows, counts = leaf_rows(structure, currentness), leaf_counts(structure, currentness)
        rest = () if request.resume is None else request.resume.rest
        next_manifest = _next_manifest(index, rest)
    if isinstance(next_manifest, PagingRefusal):
        return next_manifest
    binding = PageBinding(
        memory_tree_id=request.memory_tree_id,
        selection_policy=LEAF_POLICY,
        policy_version=LEAF_POLICY_VERSION,
        manifest_digest=structure.manifest_digest,
        code_tree_id=None if request.code_tree is None else request.code_tree.tree,
    )
    position = 0
    if request.resume is not None:
        refusal = position_refusal(
            request.resume, manifest_digest=structure.manifest_digest, total=len(rows)
        )
        if refusal is not None:
            return refusal
        position = request.resume.position
    return PreparedLeaf(
        request,
        structure,
        rows,
        counts,
        currentness,
        binding,
        position,
        rest,
        next_manifest=next_manifest,
    )


def _currentness(
    index: KnowledgeIndex, structure: LeafStructure, code_tree: CodeTree | None
) -> Currentness:
    """The selection's currentness at the walk's code tree; a failed step leaves states unset."""

    try:
        evaluated = invariant_currentness(code_tree, index, structure.invariants)
    except CURRENTNESS_FAILURES as error:
        # Currentness is advisory beside the answer (L03 ruling N2): the rows are still returned,
        # and the page's ``currentness`` block states the reason.
        reason = f"currentness could not be computed ({type(error).__name__}: {error})"
        return Currentness(code_tree=code_tree, problem=reason, invariants=(), families=())
    # A family header counts its live members only: a retired member is not selected (MIK-R23 Q4).
    states = {one.id: one.state for one in evaluated.invariants}
    families = tuple(
        FamilyCurrentness(
            id=family_id,
            members=family.members,
            stale_members=tuple(m for m in family.members if states.get(m) == "stale"),
        )
        for family_id, family in sorted(structure.families.items())
    )
    return replace(evaluated, families=families)


def _next_manifest(
    index: KnowledgeIndex, rest: tuple[dict[str, str], ...]
) -> str | PagingRefusal | None:
    """The manifest of the first queued seed, which the move on to it binds, or ``None``."""

    if not rest:
        return None
    path = rest[0].get("path")
    structure = None if path is None else select_leaf(index, path)
    if structure is None:
        # Unreachable at the bound tree -- only a seed that selected rows is ever queued -- and
        # refused rather than skipped, so a queued seed is never silently dropped.
        return PagingRefusal(
            "continuation_binding_mismatch",
            f"the queued seed {rest[0]!r} selects nothing at this memory tree; start a new read "
            "from the seed without a continuation",
        )
    return structure.manifest_digest


def _continuation(prepared: PreparedLeaf, cut: PageCut) -> tuple[str | None, dict[str, str] | None]:
    """The token that follows this page, and the queued seed it moves on to, if it does."""

    binding, seed = prepared.binding, prepared.seed_json
    if not cut.complete:
        token = mint_continuation(
            binding, response="leaf", view=LEAF_VIEW, seeds=(seed, *prepared.rest), position=cut.end
        )
        return token, None
    if not prepared.rest or prepared.next_manifest is None:
        return None, None
    moved = replace(binding, manifest_digest=prepared.next_manifest)
    token = mint_continuation(
        moved, response="leaf", view=LEAF_VIEW, seeds=prepared.rest, position=0
    )
    return token, prepared.rest[0]


def _page(prepared: PreparedLeaf, cut: PageCut) -> dict[str, Any]:
    request = prepared.request
    continuation, queued = _continuation(prepared, cut)
    page = page_block(cut, prepared.binding)
    # The reference row travels once, as the page's literal first row (MIK-R02 rule 4).
    reference = page.pop("headerReference", None)
    rows = [dict(row.body) for row in prepared.rows[cut.start : cut.end]]
    counts = {**prepared.counts, "rowsReturned": cut.end, "rowsRemaining": cut.remaining}
    block: dict[str, Any] = {
        "seed": prepared.seed_json,
        "state": "page",
        "memoryTreeId": request.memory_tree_id,
        "manifestDigest": prepared.structure.manifest_digest,
        "rows": rows if reference is None else [reference, *rows],
        "counts": counts,
        "hasMore": not cut.complete,
        # A page read from a partial index is never presented as complete (MIK-R23, Failure).
        "enumerationComplete": cut.complete and request.index_state == "complete",
        "indexState": request.index_state,
        "continuation": continuation,
        "continuationOperation": "knowledge_read",
        "continuationView": LEAF_VIEW,
        "page": page,
    }
    if queued is not None:
        # This seed is done; the continuation moves on to the next queued seed.
        block["continuationSeed"] = queued
    return block
