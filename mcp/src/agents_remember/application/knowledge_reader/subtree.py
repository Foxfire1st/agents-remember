"""Every entry under a directory, in bounded pages resumed by the shared continuation (MIK-R29, F2).

A directory's path view is bounded to its own level (:func:`.paths.path_view`); the recursive list
of every realization and proof entry under it is this view. It pages exactly as every other bounded
read of a memory tree does (MIK-R02, :mod:`agents_remember.application.knowledge_paging`):

* **The selection** is the directory's live entries (a retired invariant's are not listed), ordered
  by source path and then entry ID, one indivisible row per entry.
* **The cut** is the shared pager's: the longest run of whole rows whose *rendered page* fits the
  shared token threshold. A page also carries the MIK-R03 state of each invariant its rows name,
  computed for that page only, so the work per page is bounded as well as its size.
* **The continuation** is the shared ``knowledge-continuation/v2`` token, bound to the memory tree,
  this selection policy and version, the manifest digest of the ordered selection, the position and
  the code tree. A token of another walk, another tree or another directory is refused by name
  (``continuation_unreadable`` / ``continuation_binding_mismatch``) and no rows are returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Final

from agents_remember.application.knowledge_currentness import CodeTree, Currentness
from agents_remember.application.knowledge_paging import (
    KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
    PageBinding,
    PageCut,
    PageRow,
    cut_page,
)
from agents_remember.application.knowledge_paging.bindings import (
    PagingRefusal,
    mint_continuation,
    position_refusal,
    read_continuation,
    request_binding_refusal,
)
from agents_remember.application.knowledge_paging.pager import page_block
from agents_remember.application.knowledge_paging.threshold import response_tokens
from agents_remember.application.knowledge_reader.files import normal_path
from agents_remember.application.knowledge_reader.paths import states_at
from agents_remember.application.knowledge_reader.selection import ReaderSelection
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.knowledge.read_anchor_memo import is_complete_object_id
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex
from agents_remember.models.knowledge.continuation import KnowledgeContinuation

__all__ = ["SUBTREE_POLICY", "SUBTREE_POLICY_VERSION", "SUBTREE_VIEW", "subtree_page"]

SUBTREE_VIEW: Final = "reader-subtree"
SUBTREE_POLICY: Final = "knowledge-reader-subtree"
SUBTREE_POLICY_VERSION: Final = "1"
_RETIRED: Final = "retired"


def subtree_page(
    selection: ReaderSelection,
    raw_path: str,
    continuation: str | None,
    *,
    threshold: int = KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
) -> dict[str, Any]:
    """One page of every live entry at or under ``raw_path``; ``continuation`` resumes a walk.

    ``threshold`` is the shared bound; only a test names another, to cut a small selection into
    several pages.
    """

    path = normal_path(raw_path)
    walk: KnowledgeContinuation | None = None
    if continuation:
        decoded = read_continuation(continuation, view=SUBTREE_VIEW)
        if isinstance(decoded, PagingRefusal):
            return _refused(decoded, path)
        measured = _at_walk_tree(selection, decoded.code_tree_id)
        if isinstance(measured, PagingRefusal):
            return _refused(measured, path)
        walk, selection = decoded, measured
    index = selection.index
    entries = _live_entries(index, path)
    binding = PageBinding(
        memory_tree_id=index.state.key,
        selection_policy=SUBTREE_POLICY,
        policy_version=SUBTREE_POLICY_VERSION,
        manifest_digest=sha256_digest({"path": path, "entries": [one.id for one in entries]}),
        code_tree_id=_code_tree_id(selection),
    )
    start = 0
    if walk is not None:
        resumed = _resume(walk, path, binding, len(entries))
        if isinstance(resumed, PagingRefusal):
            return _refused(resumed, path)
        start = resumed
    page = _Page(selection, path, entries, binding)
    # The reader's own envelope (the view, its state and the selection block) travels beside the
    # page, so the page is cut to what the threshold leaves once that envelope is counted.
    envelope = response_tokens(
        {"view": "subtree", "state": "view", "selection": selection.to_document()}
    )
    _cut, body = cut_page(page.rows, start, page.render, threshold=threshold - envelope)
    # The answer's selection block is the measured one: a resumed page names the walk's code tree
    # and why (review R3-1), the same tree its ``page.codeTreeId`` states.
    return {"state": "view", "selection": selection.to_document(), **body}


def _refused(refusal: PagingRefusal, path: str) -> dict[str, Any]:
    return {"state": "refused", "code": refusal.code, "detail": refusal.detail, "path": path}


def _at_walk_tree(
    selection: ReaderSelection, walk_tree: str | None
) -> ReaderSelection | PagingRefusal:
    """The selection measuring at the code tree the walk began at (review F16).

    Like every L02 walk, a resumed page resolves its anchors at page 1's code tree, not at wherever
    the checkout has moved since; a tree the repository no longer holds is refused by name.
    """

    if walk_tree == _code_tree_id(selection):
        return selection
    if walk_tree is None:
        note = "the walk began with no code tree, so its pages measure at none"
        return replace(selection, code_tree=None, code_source="none", code_note=note)
    held = run_git(
        selection.code_repository,
        ["cat-file", "-e", f"{walk_tree}^{{tree}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if held.returncode != 0:
        return PagingRefusal(
            "continuation_binding_mismatch",
            f"the walk measured at code tree {walk_tree}, which {selection.code_repository} no "
            "longer holds; start a new read from the seed without a continuation",
        )
    note = f"the code tree this walk began at ({walk_tree[:12]}); the checkout has moved since"
    return replace(
        selection, code_tree=CodeTree(selection.code_repository, walk_tree), code_note=note
    )


def _live_entries(index: KnowledgeIndex, path: str) -> list[Entry]:
    under = index.entries_under(path).value
    live: dict[str, bool] = {}
    for invariant in {entry.invariant for entry in (*under.realizations, *under.proofs)}:
        record = index.record(invariant).value
        live[invariant] = record is not None and record.status != _RETIRED
    return sorted(
        (e for e in (*under.realizations, *under.proofs) if live[e.invariant]),
        key=lambda one: (one.path, one.id),
    )


def _code_tree_id(selection: ReaderSelection) -> str | None:
    tree = selection.code_tree
    return tree.tree if tree is not None and is_complete_object_id(tree.tree) else None


def _resume(
    continuation: KnowledgeContinuation, path: str, binding: PageBinding, total: int
) -> int | PagingRefusal:
    refused = request_binding_refusal(
        continuation,
        memory_tree_id=binding.memory_tree_id,
        selection_policy=binding.selection_policy,
        policy_version=binding.policy_version,
    )
    if refused is None and continuation.seed.get("path") != path:
        refused = PagingRefusal(
            "continuation_binding_mismatch",
            f"the walk lists {continuation.seed.get('path')!r}, not {path!r}; start a new read "
            "from the seed without a continuation",
        )
    refused = refused or position_refusal(
        continuation, manifest_digest=binding.manifest_digest, total=total
    )
    return refused if refused is not None else continuation.position


class _Page:
    """The rows of one walk and the renderer the pager measures, with states computed per page."""

    def __init__(
        self,
        selection: ReaderSelection,
        path: str,
        entries: Sequence[Entry],
        binding: PageBinding,
    ) -> None:
        self.selection = selection
        self.path = path
        self.entries = entries
        self.binding = binding
        self.rows = [PageRow(body=_row(entry)) for entry in entries]
        self.states: dict[str, str | None] = {}
        self.problem: str | None = None

    def render(self, cut: PageCut) -> dict[str, Any]:
        shown = self.entries[cut.start : cut.end]
        states = self._states({entry.invariant for entry in shown})
        token = None
        if not cut.complete:
            token = mint_continuation(
                self.binding,
                response="view",
                view=SUBTREE_VIEW,
                seeds=({"path": self.path},),
                position=cut.end,
            )
        return {
            "directory": self.path,
            "rows": [row.body for row in self.rows[cut.start : cut.end]],
            "states": states,
            "statesProblem": self.problem,
            "page": page_block(cut, self.binding),
            "continuation": token,
        }

    def _states(self, invariants: set[str]) -> dict[str, str | None]:
        missing = sorted(invariants - set(self.states))
        if missing:
            currentness, problem = states_at(self.selection, missing, [])
            self.problem = self.problem or problem
            for invariant in missing:
                self.states[invariant] = _state_of(currentness, invariant)
        return {invariant: self.states[invariant] for invariant in sorted(invariants)}


def _state_of(currentness: Currentness | None, invariant: str) -> str | None:
    known = None if currentness is None else currentness.of(invariant)
    return None if known is None else known.state


def _row(entry: Entry) -> dict[str, Any]:
    anchor = entry.document.get("anchor") or {}
    return {
        "id": entry.id,
        "kind": entry.kind,
        "invariant": entry.invariant,
        "path": entry.path,
        "locator": anchor.get("locator"),
        "role": entry.document.get("role"),
        "facet": entry.document.get("facet"),
    }
