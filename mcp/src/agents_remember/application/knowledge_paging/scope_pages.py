"""Pages of the selective scope read, the selection behind the published-intent block (MIK-R02).

The scope read selects one seed's recorded neighbourhood as an ordered item stream (invariant
revisions, family revisions, memberships, realization claims, then the advertised frontier). On a
converted memory tree this module pages that stream by the shared threshold: each item is one row,
a family revision is the header of its memberships, and every page carries the one continuation
format ``knowledge_read`` accepts. The same function builds page 1 for ``read_ar_files`` and every
later page for ``knowledge_read``, so the pages of one walk are cut from one selection by one rule.

The page keeps the fields the published-intent block always carried (``items``, ``counts``,
``hasMore``, ``enumerationComplete``, ``continuation``) and adds ``page``, the facts every bounded
response states. ``continuationOperation`` now names ``knowledge_read``, and
``continuationView`` the view argument that resumes the walk.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from agents_remember.application.knowledge_paging.bindings import (
    PagingRefusal,
    mint_continuation,
    position_refusal,
)
from agents_remember.application.knowledge_paging.pager import (
    PageBinding,
    PageCut,
    PageRow,
    cut_page,
    page_block,
)
from agents_remember.application.knowledge_read import select_knowledge_scope
from agents_remember.memory.knowledge.read import SelectedScope
from agents_remember.models.knowledge.continuation import KnowledgeContinuation
from agents_remember.models.knowledge.read import (
    KNOWLEDGE_READ_POLICY_VERSION,
    KnowledgeReadContext,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    ReadItem,
    seed_digest,
)

__all__ = [
    "SCOPE_POLICY",
    "SCOPE_POLICY_VERSION",
    "PreparedScope",
    "ScopePageRequest",
    "TreeBinding",
    "prepare_scope",
    "scope_page",
    "scope_rows",
    "scope_view",
]

SCOPE_POLICY, SCOPE_POLICY_VERSION = KNOWLEDGE_READ_POLICY_VERSION.rsplit("/", 1)

# The ``knowledge_read`` view that resumes a scope walk, by seed kind: a path seed is the source
# context of that path, an identity or revision seed the invariant or family it names.
_SEED = TypeAdapter[KnowledgeReadSeed](KnowledgeReadSeed)

_RESUMING_VIEW = {
    "path": "source_context",
    "invariant": "invariant",
    "invariant_revision": "invariant",
    "family": "family",
    "family_revision": "family",
}


@dataclass(frozen=True)
class TreeBinding:
    """The memory tree a page is read from: its key and the state of the index built for it."""

    tree_id: str
    index_state: str


@dataclass(frozen=True)
class ScopePageRequest:
    """One scope page: the dataset, the context and seed it is read at, and where it starts.

    ``wrap`` turns the page block into the response the caller emits; the page is cut to fit that
    response, not only the block. ``resume`` is the continuation a later page starts from.
    """

    database_path: Path
    context: KnowledgeReadContext
    seed: KnowledgeReadSeed
    tree: TreeBinding
    resume: KnowledgeContinuation | None = None
    wrap: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def scope_view(seed: KnowledgeReadSeed) -> str:
    """The ``knowledge_read`` view a scope walk with this seed resumes on."""

    return _RESUMING_VIEW[seed.kind]


def scope_rows(items: Sequence[ReadItem]) -> tuple[PageRow, ...]:
    """The item stream as rows: a family revision heads the memberships of that revision."""

    rows: list[PageRow] = []
    for item in items:
        body = item.model_dump(mode="json", exclude_none=True)
        if item.kind == "family_revision":
            reference = {
                "kind": "family_header_reference",
                "family_id": item.family_id,
                "family_revision_id": item.revision_id,
                "title": item.display_label,
            }
            rows.append(PageRow(body, group=item.revision_id, reference=reference))
        elif item.kind == "family_membership":
            rows.append(PageRow(body, group=item.family_revision_id))
        else:
            rows.append(PageRow(body))
    return tuple(rows)


@dataclass(frozen=True)
class PreparedScope:
    """One seed's whole selection, ready to be cut: its rows, its binding and its start.

    ``rest`` are the seeds queued behind this one (a collapsed ``read_ar_files`` tail), and
    ``next_manifest`` the manifest of the first of them, which the continuation that moves on to it
    binds.
    """

    request: ScopePageRequest
    scope: SelectedScope
    rows: tuple[PageRow, ...]
    binding: PageBinding
    position: int
    rest: tuple[dict[str, str], ...] = ()
    next_manifest: str | None = None

    @property
    def seed_json(self) -> dict[str, str]:
        return _seed_json(self.request.seed)

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

    def collapsed(self, queued: Sequence[PreparedScope]) -> dict[str, Any]:
        """This seed and ``queued`` as one deferred entry whose one continuation walks them all."""

        return {
            "seeds": [self.seed_json, *(one.seed_json for one in queued)],
            "state": "deferred",
            "counts": {"total": sum(len(one.rows) for one in (self, *queued)), "returned": 0},
            **self._first_continuation(tuple(one.seed_json for one in queued)),
        }

    def _first_continuation(self, rest: tuple[dict[str, str], ...]) -> dict[str, Any]:
        view = scope_view(self.request.seed)
        token = mint_continuation(
            self.binding, response="scope", view=view, seeds=(self.seed_json, *rest), position=0
        )
        return {
            "continuation": token,
            "continuationOperation": "knowledge_read",
            "continuationView": view,
        }


def prepare_scope(
    request: ScopePageRequest,
) -> PreparedScope | KnowledgeReadResult | PagingRefusal:
    """Select a seed's scope and check a continuation against it, or return the refusal."""

    selected = select_knowledge_scope(request.database_path, request.context, request.seed)
    if isinstance(selected, KnowledgeReadResult):
        return selected
    binding = PageBinding(
        memory_tree_id=request.tree.tree_id,
        selection_policy=SCOPE_POLICY,
        policy_version=SCOPE_POLICY_VERSION,
        manifest_digest=selected.manifest_digest,
        code_tree_id=request.context.code_tree_id,
    )
    resume = request.resume
    if resume is None:
        return PreparedScope(request, selected, scope_rows(selected.items), binding, 0)
    refusal = position_refusal(
        resume, manifest_digest=selected.manifest_digest, total=len(selected.items)
    )
    if refusal is not None:
        return refusal
    next_manifest = _next_manifest(request, resume.rest)
    if isinstance(next_manifest, (KnowledgeReadResult, PagingRefusal)):
        return next_manifest
    return PreparedScope(
        request,
        selected,
        scope_rows(selected.items),
        binding,
        resume.position,
        rest=resume.rest,
        next_manifest=next_manifest,
    )


def _next_manifest(
    request: ScopePageRequest, rest: tuple[dict[str, str], ...]
) -> str | KnowledgeReadResult | PagingRefusal | None:
    """The manifest of the first queued seed, which the move on to it binds, or ``None``."""

    if not rest:
        return None
    try:
        seed = _SEED.validate_python(rest[0])
    except ValidationError as error:
        return PagingRefusal("continuation_unreadable", f"a queued seed is not a seed: {error}")
    selected = select_knowledge_scope(request.database_path, request.context, seed)
    return selected if isinstance(selected, KnowledgeReadResult) else selected.manifest_digest


def scope_page(
    request: ScopePageRequest,
) -> dict[str, Any] | KnowledgeReadResult | PagingRefusal:
    """One page of a seed's scope, its read's own refusal, or a refused continuation."""

    prepared = prepare_scope(request)
    if not isinstance(prepared, PreparedScope):
        return prepared
    wrap = request.wrap or (lambda block: block)
    _cut, response = cut_page(
        prepared.rows, prepared.position, lambda cut: wrap(prepared.render(cut))
    )
    return response


def _seed_json(seed: KnowledgeReadSeed) -> dict[str, str]:
    return {key: str(value) for key, value in seed.model_dump(mode="json").items()}


def _continuation(
    prepared: PreparedScope, cut: PageCut
) -> tuple[str | None, dict[str, str] | None]:
    """The token that follows this page, and the queued seed it moves on to, if it does."""

    binding, seed = prepared.binding, prepared.seed_json
    if not cut.complete:
        view = scope_view(prepared.request.seed)
        token = mint_continuation(
            binding, response="scope", view=view, seeds=(seed, *prepared.rest), position=cut.end
        )
        return token, None
    if not prepared.rest or prepared.next_manifest is None:
        return None, None
    queued = prepared.rest[0]
    moved = replace(binding, manifest_digest=prepared.next_manifest)
    view = _RESUMING_VIEW.get(queued.get("kind", ""), "source_context")
    token = mint_continuation(moved, response="scope", view=view, seeds=prepared.rest, position=0)
    return token, queued


def _page(prepared: PreparedScope, cut: PageCut) -> dict[str, Any]:
    request, scope, binding = prepared.request, prepared.scope, prepared.binding
    continuation, queued = _continuation(prepared, cut)
    continued = prepared.seed_json if queued is None else queued
    counts = scope.counts.model_copy(
        update={"primary_items_returned": cut.end, "primary_items_remaining": cut.remaining}
    )
    block = {
        "seed": prepared.seed_json,
        "state": "page",
        "snapshot": request.context.knowledge.logical_digest,
        "seedDigest": seed_digest(request.seed),
        "manifestDigest": scope.manifest_digest,
        "items": [dict(row.body) for row in prepared.rows[cut.start : cut.end]],
        "counts": counts.model_dump(mode="json"),
        "hasMore": not cut.complete,
        # A page read from a partial index is never presented as complete (MIK-R23, Failure).
        "enumerationComplete": cut.complete and request.tree.index_state == "complete",
        "indexState": request.tree.index_state,
        "continuation": continuation,
        "continuationOperation": "knowledge_read",
        "continuationView": _RESUMING_VIEW.get(continued.get("kind", ""), "source_context"),
        "page": page_block(cut, binding),
    }
    if queued is not None:
        # This seed is done; the continuation moves on to the next queued seed.
        block["continuationSeed"] = queued
    return block
