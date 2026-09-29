"""Minting a continuation, and refusing one whose bindings do not hold (MIK-R02 rule 3).

A continuation is checked in two halves, each before anything is returned:

* **Before the selection is read** -- the token must be one this format minted, for the view the
  caller named, cut with this build's threshold, under the selection policy and version this build
  pages that response with, and minted at the memory tree the call selected.
* **Once the selection exists** -- its manifest must be the token's manifest, and the position must
  lie inside it.

A token that is not this format's, or that belongs to another view's walk, keeps the meaning the
view codec gives it: ``continuation_unreadable``. Every other binding that does not hold is
``continuation_binding_mismatch``. Either way no rows are returned, so a caller never receives a
page assembled from two selections. A changed memory tree names the tree the call now selects, and
the caller restarts from the seed (MIK-R02, Failure And Recovery).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.application.knowledge_paging.pager import PageBinding
from agents_remember.application.knowledge_paging.threshold import (
    KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
)
from agents_remember.models.knowledge.continuation import (
    CONTINUATION_FORMAT,
    KnowledgeContinuation,
    PagedResponse,
    decode_continuation,
    encode_continuation,
)

__all__ = [
    "PagingRefusal",
    "mint_continuation",
    "ordering_refusal",
    "position_refusal",
    "read_continuation",
    "request_binding_refusal",
    "resolution_refusal",
]

PagingRefusalCode = Literal[
    "continuation_unreadable", "continuation_binding_mismatch", "selected_input_unavailable"
]

_RESTART = "start a new read from the seed without a continuation"


@dataclass(frozen=True)
class PagingRefusal:
    """One refused continuation: the shipped code and what did not hold."""

    code: PagingRefusalCode
    detail: str


def mint_continuation(
    binding: PageBinding,
    *,
    response: PagedResponse,
    view: str,
    seeds: tuple[dict[str, str], ...],
    position: int,
) -> str:
    """The token that resumes one walk at ``position`` of ``seeds[0]``, with the rest queued."""

    return encode_continuation(
        KnowledgeContinuation(
            memory_tree_id=binding.memory_tree_id,
            response=response,
            view=view,
            seed=seeds[0],
            selection_policy=binding.selection_policy,
            policy_version=binding.policy_version,
            manifest_digest=binding.manifest_digest,
            position=position,
            threshold_tokens=KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
            code_tree_id=binding.code_tree_id,
            rest=seeds[1:],
        )
    )


def read_continuation(token: str, *, view: str) -> KnowledgeContinuation | PagingRefusal:
    """Decode one token for the view a caller named, or refuse it as unreadable."""

    continuation = decode_continuation(token)
    if continuation is None:
        return PagingRefusal(
            "continuation_unreadable",
            f"the continuation is not a {CONTINUATION_FORMAT} token, which is the only "
            "continuation a converted memory tree's pages mint and accept; "
            f"{_RESTART}",
        )
    if continuation.view != view:
        return PagingRefusal(
            "continuation_unreadable",
            f"the continuation resumes the {continuation.view!r} view's walk, not {view!r}; "
            f"read it with view {continuation.view!r}",
        )
    return continuation


def request_binding_refusal(
    continuation: KnowledgeContinuation,
    *,
    memory_tree_id: str,
    selection_policy: str,
    policy_version: str,
) -> PagingRefusal | None:
    """The refusal for a binding that is checkable before the selection is read, or ``None``."""

    if continuation.memory_tree_id != memory_tree_id:
        return _mismatch(
            f"the memory tree changed since this walk began: it was minted at "
            f"{continuation.memory_tree_id} and the selected tree is now {memory_tree_id}"
        )
    if continuation.threshold_tokens != KNOWLEDGE_PAGE_THRESHOLD_TOKENS:
        return _mismatch(
            f"the walk was cut with a {continuation.threshold_tokens}-token threshold and this "
            f"read pages with {KNOWLEDGE_PAGE_THRESHOLD_TOKENS}"
        )
    minted = (continuation.selection_policy, continuation.policy_version)
    if minted != (selection_policy, policy_version):
        return _mismatch(
            f"the walk was selected under {minted[0]}/{minted[1]} and this read selects under "
            f"{selection_policy}/{policy_version}"
        )
    return None


def resolution_refusal(
    continuation: KnowledgeContinuation, *, code_tree_id: str | None
) -> PagingRefusal | None:
    """The refusal for a caller-named code tree other than the walk's own, or ``None``.

    ``code_tree_id`` is the tree the caller *named*; a caller that names none resumes at the
    walk's tree.
    """

    if code_tree_id is None or code_tree_id == continuation.code_tree_id:
        return None
    walk = continuation.code_tree_id or "no code tree"
    return _mismatch(
        f"the walk resolved its anchors at {walk} and this read names the code tree "
        f"{code_tree_id}; resume without codeTreeId to read at the walk's tree"
    )


def ordering_refusal(
    continuation: KnowledgeContinuation, *, ordering_input: str | None
) -> PagingRefusal | None:
    """The refusal for a caller-named ordering other than the walk's own, or ``None``.

    A view walk binds its effective ordering (named or defaulted) in its seed; a scope walk has
    the scope read's one declared item order, so naming any ordering for it names another walk.
    """

    if ordering_input is None:
        return None
    walk = continuation.seed.get("ordering_input")
    if ordering_input == walk:
        return None
    ordered = "the scope read's declared item order" if walk is None else repr(walk)
    return _mismatch(
        f"the walk is ordered by {ordered} and this read names orderingInput "
        f"{ordering_input!r}; resume without orderingInput to read in the walk's order"
    )


def position_refusal(
    continuation: KnowledgeContinuation, *, manifest_digest: str, total: int
) -> PagingRefusal | None:
    """The refusal for a manifest or position that is not this selection's, or ``None``."""

    if continuation.manifest_digest != manifest_digest:
        return _mismatch(
            f"the walk pages the selection {continuation.manifest_digest} and the seed now "
            f"selects {manifest_digest}"
        )
    if continuation.position > total:
        return _mismatch(
            f"the continuation names position {continuation.position}, past the end of the "
            f"{total}-row selection"
        )
    return None


def _mismatch(detail: str) -> PagingRefusal:
    return PagingRefusal("continuation_binding_mismatch", f"{detail}; {_RESTART}")
