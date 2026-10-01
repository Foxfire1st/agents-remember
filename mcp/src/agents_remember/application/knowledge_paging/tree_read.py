"""The mounted ``knowledge_read`` over a converted memory tree: pages and continuations (MIK-R02).

A read of a memory tree is always a bounded page of one selection, cut by the shared threshold, and
its continuation is the shared token. A continuation is accepted whichever surface minted it:

* a **view** token (minted by a ``knowledge_read`` view page) resumes that view's row walk;
* a **leaf** token (minted by the published-intent block of ``read_ar_files`` for a path, or by an
  earlier ``source_context`` page) resumes the family-complete leaf read (MIK-R01), and the response
  is ``state: "page"`` with the leaf page as its ``payload``;
* a **scope** token (minted for an identity seed of the published-intent route, or by an earlier
  ``knowledge_read`` page of the same walk) resumes the scope read's item walk, and the response is
  ``state: "page"`` with the scope page as its ``payload``.

A fresh ``source_context`` read of a path is the family-complete leaf read itself: its first page,
in the same selection and under the same manifest the ``read_ar_files`` block returns (MIK-R01
rule 6), with the path's route-chain families after it (MIK-R05). A fresh ``source_context`` read
that names a family ID (or ``ID@revision``) in ``familyRevisionId`` and no path is a family seed:
the family's full content (MIK-R05 rule 3). An ``invariant`` view names the families containing
its invariant (rule 7).

The token carries its seed, its effective ordering and the code tree page 1 resolved anchors at, so
a resuming call names only the dataset, the namespace, the view and the token, and every page of
the walk is ordered and resolved alike. A subject, ordering or code tree the caller does name must
agree with the token; a different one is a binding that does not hold. Where the walk's code tree
is read from is the caller's ``repositoryRoot``, else the mount's workspace; a root that does not
hold the tree is refused by name. ``limit`` does not bound a tree read: the threshold does, and the
page says so. Every response, refusals included, states the threshold. Nothing here is kept between
calls.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.knowledge_leaf.currentness import LeafCurrentness
from agents_remember.application.knowledge_leaf.pages import (
    LEAF_VIEW,
    LeafRequest,
    PreparedLeaf,
    absent_chain,
    prepare_leaf,
)
from agents_remember.application.knowledge_leaf.selection import (
    LEAF_POLICY,
    LEAF_POLICY_VERSION,
    family_names,
)
from agents_remember.application.knowledge_paging.bindings import (
    PagingRefusal,
    mint_continuation,
    ordering_refusal,
    position_refusal,
    read_continuation,
    request_binding_refusal,
    resolution_refusal,
)
from agents_remember.application.knowledge_paging.pager import (
    PageBinding,
    PageCut,
    cut_page,
    page_block,
)
from agents_remember.application.knowledge_paging.scope_pages import (
    SCOPE_POLICY,
    SCOPE_POLICY_VERSION,
    ScopePageRequest,
    TreeBinding,
    prepare_scope,
)
from agents_remember.application.knowledge_paging.threshold import threshold_block
from agents_remember.application.knowledge_paging.tree_seeds import (
    SEED_SOURCES,
    tree_seed_refusal,
)
from agents_remember.application.knowledge_paging.view_pages import (
    VIEW_POLICY,
    VIEW_POLICY_VERSION,
    WholeView,
    family_reference,
    read_whole_view,
    view_page_payload,
    view_rows,
)
from agents_remember.application.published_intent import (
    PublishedMemoryTree,
    SelectedKnowledgeDataset,
    memory_tree_block,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.models.knowledge.continuation import KnowledgeContinuation
from agents_remember.models.knowledge.read import (
    KnowledgeReadContext,
    KnowledgeReadResult,
    KnowledgeReadSeed,
)
from agents_remember.models.knowledge.view import MAX_VIEW_ROWS, ViewRefusal, ViewRequest

__all__ = [
    "DEFAULT_ORDERING",
    "PageExtras",
    "ReadSubject",
    "ToolReadRequest",
    "TreeExtras",
    "read_tree_page",
]

# The ordering a view read uses when the caller names none; a view walk binds it like a named one.
DEFAULT_ORDERING = "stable_ordering"

_SEED = TypeAdapter[KnowledgeReadSeed](KnowledgeReadSeed)

# The selection policy and version each paged response is cut under.
_POLICIES = {
    "leaf": (LEAF_POLICY, LEAF_POLICY_VERSION),
    "scope": (SCOPE_POLICY, SCOPE_POLICY_VERSION),
    "view": (VIEW_POLICY, VIEW_POLICY_VERSION),
}

# The tool arguments that name a subject, and the seed field each one binds to.
_SUBJECT_FIELDS = ("invariant_revision_id", "family_revision_id", "source_path")
_SCOPE_SUBJECTS = {
    "path": ("source_path", "path"),
    "family": ("family_revision_id", "id"),
    "invariant_revision": ("invariant_revision_id", "revision_id"),
    "family_revision": ("family_revision_id", "revision_id"),
}


class ToolReadRequest(Protocol):
    """The ``knowledge_read`` arguments a tree read uses."""

    @property
    def view(self) -> str: ...
    @property
    def repository_id(self) -> str: ...
    @property
    def ordering_input(self) -> str | None: ...
    @property
    def code_tree_id(self) -> str | None: ...
    @property
    def repository_root(self) -> str | None: ...
    @property
    def continuation(self) -> str | None: ...
    @property
    def invariant_revision_id(self) -> str | None: ...
    @property
    def family_revision_id(self) -> str | None: ...
    @property
    def source_path(self) -> str | None: ...


@dataclass(frozen=True)
class ReadSubject:
    """The subject a page is read about: the caller's arguments, or the continuation's seed."""

    view: str
    invariant_revision_id: str | None = None
    family_revision_id: str | None = None
    source_path: str | None = None
    ordering_input: str = DEFAULT_ORDERING

    def seed(self) -> dict[str, str]:
        """The subject as a view continuation's seed spells it."""

        fields = {
            "invariant_revision_id": self.invariant_revision_id,
            "family_revision_id": self.family_revision_id,
            "source_path": self.source_path,
            "ordering_input": self.ordering_input,
        }
        return {key: value for key, value in fields.items() if value is not None}


# What a tree read adds beside its page. It is prepared once per page -- from the subject, the
# walk's code tree and every row the page could carry -- and then renders the additions for each
# candidate payload the cut tries, so nothing expensive is recomputed while the page is being cut.
PageExtras = Callable[[dict[str, Any]], dict[str, Any]]
TreeExtras = Callable[[ReadSubject, CodeTree | None, list[Any]], PageExtras]


@dataclass(frozen=True)
class _TreeRead:
    request: ToolReadRequest
    selected: SelectedKnowledgeDataset
    tree: PublishedMemoryTree
    context: KnowledgeReadContext
    extras: TreeExtras
    workspace_root: str | None
    code_tree: CodeTree | None = None

    @property
    def index_complete(self) -> bool:
        return self.tree.index_state == "complete"

    def prepared(self, subject: ReadSubject, candidates: list[Any]) -> PageExtras:
        """The envelope renderer of one page: fixed fields, and the extras prepared once."""

        extras = self.extras(subject, self.code_tree, candidates)
        fixed: dict[str, Any] = {
            "memoryTree": memory_tree_block(self.tree),
            "indexComplete": self.index_complete,
        }
        if subject.view == "invariant":  # MIK-R01 rule 7: the families containing the invariant
            fixed["families"] = _containing_families(self, subject.invariant_revision_id)
        return lambda body: {**fixed, **extras(body)}


def read_tree_page(
    request: ToolReadRequest,
    selected: SelectedKnowledgeDataset,
    context: KnowledgeReadContext,
    *,
    extras: TreeExtras,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """One ``knowledge_read`` response for a converted memory tree: a page, or a refusal."""

    tree = selected.memory_tree
    if tree is None:
        raise ValueError("a tree read needs a selection that names a memory tree")
    read = _TreeRead(request, selected, tree, context, extras, workspace_root)
    if request.continuation is None:
        return _fresh_read(read)
    resume = read_continuation(request.continuation, view=request.view)
    if isinstance(resume, PagingRefusal):
        return _refused(read, resume.code, resume.detail)
    policy, version = _POLICIES[resume.response]
    refusal: PagingRefusal | None = request_binding_refusal(
        resume, memory_tree_id=tree.tree_key, selection_policy=policy, policy_version=version
    )
    refusal = (
        refusal
        or _subject_refusal(request, resume, lambda named: _family_spelling(read, named)[0])
        or ordering_refusal(resume, ordering_input=request.ordering_input)
        or resolution_refusal(resume, code_tree_id=request.code_tree_id)
    )
    if refusal is not None:
        return _refused(read, refusal.code, refusal.detail)
    absent = _resumed_revision_absent(read, resume)
    if absent is not None:
        return _refused(read, "selector_absent", absent)
    read = _at_code_tree(read, resume.code_tree_id)
    missing = _missing_code_tree(read, resume.code_tree_id)
    if missing is not None:
        return _refused(read, missing.code, missing.detail)
    return _resumed_response(read, resume)


def _fresh_read(read: _TreeRead) -> dict[str, Any]:
    """Page 1 of a fresh read, or the refusal of a seed the tree does not hold."""

    absent = _fresh_seed_absent(read)
    if absent is not None:  # never answered as an empty, complete view (L37, P2 task 4)
        return _refused(read, "selector_absent", absent)
    # A fresh view walk is bound to the code tree the caller named, and only that one.
    return _fresh_response(_at_code_tree(read, read.request.code_tree_id))


def _fresh_seed_absent(read: _TreeRead) -> str | None:
    """Why a fresh read's record seed names nothing this tree holds, or ``None``.

    A ``source_context`` family seed has its own spellings (an ID, ``ID@revision`` or a UUID), and
    :func:`_revision_absent` judges it.
    """

    request = read.request
    family_seed = None if request.view == LEAF_VIEW else request.family_revision_id
    return tree_seed_refusal(
        read.selected.database_path,
        read.tree.tree_key,
        invariant_revision_id=request.invariant_revision_id,
        family_revision_id=family_seed,
    )


def _fresh_response(read: _TreeRead) -> dict[str, Any]:
    """Page 1: a ``source_context`` read of a path is the family-complete leaf read (MIK-R01)."""

    request = read.request
    if request.view == LEAF_VIEW and request.source_path is not None:
        return _leaf_response(read, {"kind": "path", "path": request.source_path}, None)
    if request.view == LEAF_VIEW and request.family_revision_id is not None:
        return _family_response(read, request.family_revision_id)
    return _view_response(read, _requested_subject(request), None)


def _resumed_response(read: _TreeRead, resume: KnowledgeContinuation) -> dict[str, Any]:
    """A later page of the walk the continuation names, whichever response it pages."""

    if resume.response == "leaf":
        kind, key = resume.seed.get("kind"), {"path": "path", "family": "id"}
        if kind not in key or not resume.seed.get(key[kind]):
            return _refused(read, "continuation_unreadable", "its seed is not a path or a family")
        return _leaf_response(read, dict(resume.seed), resume)
    if resume.response == "scope":
        return _scope_response(read, resume)
    return _view_response(read, _token_subject(resume), resume)


def _requested_subject(request: ToolReadRequest) -> ReadSubject:
    return ReadSubject(
        view=request.view,
        invariant_revision_id=request.invariant_revision_id,
        family_revision_id=request.family_revision_id,
        source_path=request.source_path,
        ordering_input=DEFAULT_ORDERING
        if request.ordering_input is None
        else request.ordering_input,
    )


def _at_code_tree(read: _TreeRead, code_tree_id: str | None) -> _TreeRead:
    """The read at the walk's code tree: its objects read from the caller's root or the workspace.

    ``None`` resolves nothing: the walk's anchors and currentness are then read at no tree.
    """

    request = read.request
    root = request.repository_root or read.workspace_root
    if code_tree_id is None or root is None:
        tree, root = None, None
    else:
        tree = code_tree_id
    context = KnowledgeReadContext.model_validate(
        {**read.context.model_dump(), "code_tree_id": tree, "repository_root": root}
    )
    code = None if tree is None or root is None else CodeTree(Path(root), tree)
    return replace(read, context=context, code_tree=code)


def _missing_code_tree(read: _TreeRead, code_tree_id: str | None) -> PagingRefusal | None:
    """The refusal for a walk whose code tree the resolved root does not hold, or ``None``."""

    if code_tree_id is None:
        return None
    root = read.context.repository_root
    if root is not None and _holds_tree(Path(root), code_tree_id):
        return None
    where = (
        "no code repository root is known"
        if root is None
        else f"the code root {root} does not hold it"
    )
    return PagingRefusal(
        "selected_input_unavailable",
        f"the walk resolves its anchors at the code tree {code_tree_id}, but {where}; name the "
        "repository that holds it with repositoryRoot, or start a new read",
    )


def _holds_tree(root: Path, tree_id: str) -> bool:
    try:
        completed = run_git(root, ["cat-file", "-e", f"{tree_id}^{{tree}}"])
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _token_subject(resume: KnowledgeContinuation) -> ReadSubject:
    seed = resume.seed
    return ReadSubject(
        view=resume.view,
        invariant_revision_id=seed.get("invariant_revision_id"),
        family_revision_id=seed.get("family_revision_id"),
        source_path=seed.get("source_path"),
        ordering_input=seed.get("ordering_input", DEFAULT_ORDERING),
    )


def _subject_refusal(
    request: ToolReadRequest,
    resume: KnowledgeContinuation,
    family_id: Callable[[str], str],
) -> PagingRefusal | None:
    """A named subject argument that disagrees with the continuation's seed, or ``None``.

    A family seed binds the bare family ID, so a named ``familyRevisionId`` is compared by the
    family it names (``family_id``): ``ID@rev`` and a projected UUID resume it too.
    """

    if resume.response in ("leaf", "scope"):
        field, key = _SCOPE_SUBJECTS.get(str(resume.seed.get("kind")), (None, None))
        bound = {} if field is None or key is None else {field: resume.seed.get(key)}
    else:
        bound = {name: resume.seed.get(name) for name in _SUBJECT_FIELDS}
    for name, (named, compared) in _named_subjects(request, resume, family_id).items():
        if compared != bound.get(name):
            return _seed_mismatch(name, named)
    return None


def _named_subjects(
    request: ToolReadRequest,
    resume: KnowledgeContinuation,
    family_id: Callable[[str], str],
) -> dict[str, tuple[str, str]]:
    """Each subject argument the caller named, with the value it is compared by."""

    named = {name: getattr(request, name) for name in _SUBJECT_FIELDS}
    subjects = {name: (value, value) for name, value in named.items() if value is not None}
    family = subjects.get("family_revision_id")
    if family is not None and resume.response == "leaf" and resume.seed.get("kind") == "family":
        subjects["family_revision_id"] = (family[0], family_id(family[0]))
    return subjects


def _seed_mismatch(argument: str, value: str) -> PagingRefusal:
    return PagingRefusal(
        "continuation_binding_mismatch",
        f"the continuation resumes another seed than the one {argument}={value!r} names; resume "
        "it without naming a subject, or start a new read from this seed",
    )


def _view_response(
    read: _TreeRead, subject: ReadSubject, resume: KnowledgeContinuation | None
) -> dict[str, Any]:
    try:
        view_request = ViewRequest(
            view=subject.view,  # type: ignore[arg-type]
            repository_id=read.request.repository_id,
            invariant_revision_id=subject.invariant_revision_id,
            family_revision_id=subject.family_revision_id,
            source_path=subject.source_path,
            ordering_input=subject.ordering_input,  # type: ignore[arg-type]
            limit=MAX_VIEW_ROWS,
        )
    except ValidationError as error:
        code = "invalid_payload" if resume is None else "continuation_unreadable"
        return _refused(read, code, f"the read's subject is not readable: {error}")
    whole = read_whole_view(read.selected.database_path, read.context, view_request)
    if isinstance(whole, ViewRefusal):
        return _refused(read, whole.code, whole.detail)
    binding = PageBinding(
        memory_tree_id=read.tree.tree_key,
        selection_policy=VIEW_POLICY,
        policy_version=VIEW_POLICY_VERSION,
        manifest_digest=whole.manifest_digest,
        code_tree_id=read.context.code_tree_id,
    )
    position = 0
    if resume is not None:
        refusal = position_refusal(
            resume, manifest_digest=binding.manifest_digest, total=len(whole.rows)
        )
        if refusal is not None:
            return _refused(read, refusal.code, refusal.detail)
        position = resume.position

    extras = read.prepared(subject, [row.model_dump(mode="json") for row in whole.rows[position:]])

    def render(cut: PageCut) -> dict[str, Any]:
        return _view_page(read, (subject, extras), whole, cut, binding)

    family = family_reference(
        read.selected.database_path, read.tree.tree_key, subject.family_revision_id
    )
    _cut, response = cut_page(view_rows(whole, family), position, render)
    return response


def _view_page(
    read: _TreeRead,
    prepared: tuple[ReadSubject, PageExtras],
    whole: WholeView,
    cut: PageCut,
    binding: PageBinding,
) -> dict[str, Any]:
    subject, extras = prepared
    token = (
        None
        if cut.complete
        else mint_continuation(
            binding, response="view", view=subject.view, seeds=(subject.seed(),), position=cut.end
        )
    )
    body = view_page_payload(whole, cut, token, index_complete=read.index_complete)
    return {
        "ok": True,
        "state": "view",
        "view": whole.first.view,
        "repositoryId": read.request.repository_id,
        "snapshot": whole.first.snapshot.logical_digest,
        "completeWithinDeclaredScope": body["completeness"]["complete_within_declared_scope"],
        "continuation": token,
        "payload": body,
        "page": page_block(cut, binding),
        **extras(body),
    }


def _scope_response(read: _TreeRead, resume: KnowledgeContinuation) -> dict[str, Any]:
    try:
        seed = _SEED.validate_python(resume.seed)
    except ValidationError as error:
        return _refused(read, "continuation_unreadable", f"its seed is not a seed: {error}")
    subject = ReadSubject(
        view=resume.view,
        invariant_revision_id=resume.seed.get("revision_id")
        if seed.kind == "invariant_revision"
        else None,
        family_revision_id=resume.seed.get("revision_id")
        if seed.kind == "family_revision"
        else None,
        source_path=resume.seed.get("path"),
    )

    prepared = prepare_scope(
        ScopePageRequest(
            database_path=read.selected.database_path,
            context=read.context,
            seed=seed,
            tree=TreeBinding(tree_id=read.tree.tree_key, index_state=read.tree.index_state),
            resume=resume,
        )
    )
    if isinstance(prepared, PagingRefusal):
        return _refused(read, prepared.code, prepared.detail)
    if isinstance(prepared, KnowledgeReadResult):
        refusal = prepared.refusal
        code = "snapshot_unavailable" if refusal is None else refusal.code
        detail = "the scope read returned no page" if refusal is None else refusal.detail
        return _refused(read, code, detail)
    extras = read.prepared(subject, [dict(row.body) for row in prepared.rows[prepared.position :]])

    def render(cut: PageCut) -> dict[str, Any]:
        block = prepared.render(cut)
        # The page facts and the continuation travel once, at the top of the response.
        payload = {k: v for k, v in block.items() if k not in ("page", "continuation")}
        return {
            "ok": True,
            "state": "page",
            "view": resume.view,
            "repositoryId": read.request.repository_id,
            "snapshot": block["snapshot"],
            "completeWithinDeclaredScope": block["enumerationComplete"],
            "continuation": block["continuation"],
            "payload": payload,
            "page": block["page"],
            **extras(payload),
        }

    _cut, response = cut_page(prepared.rows, prepared.position, render)
    return response


def _family_response(read: _TreeRead, named: str) -> dict[str, Any]:
    """Page 1 of a family seed (MIK-R05 rule 3): ``named`` is a family ID, ``ID@revision``, or the
    projected UUID of either (the spelling the views use).

    A named revision must be the family's revision at this memory tree: a tree holds one.
    """

    absent = _revision_absent(read, named)
    if absent is not None:
        return _refused(read, "selector_absent", absent)
    return _leaf_response(read, {"kind": "family", "id": _family_spelling(read, named)[0]}, None)


def _family_spelling(read: _TreeRead, named: str) -> tuple[str, str | None]:
    """The family ID and the revision a ``familyRevisionId`` spells (``None`` when it names none;
    an empty revision when it ends in a bare ``@``)."""

    with KnowledgeIndex(read.selected.database_path, expected_key=read.tree.tree_key) as index:
        family, at, revision = (index.text_id(named) or named).partition("@")
    return family, revision if at else None


def _revision_absent(read: _TreeRead, named: str) -> str | None:
    """Why ``named`` names a family or revision this tree does not hold (refused
    ``selector_absent``), or ``None`` when it names a held family and none or the tree's revision.

    A tree holds one revision of a family; a bare ``ID@`` names none of them. A family the tree
    does not hold is never answered as an empty read (L37, P2 task 4).
    """

    family, revision = _family_spelling(read, named)
    with KnowledgeIndex(read.selected.database_path, expected_key=read.tree.tree_key) as index:
        record = index.record(family).value
    if record is None or record.kind != "family":
        return f"familyRevisionId {named!r} names no family this memory tree holds; {SEED_SOURCES}"
    if revision is None or str(record.revision) == revision:
        return None
    return (
        f"familyRevisionId {named!r} names a revision this memory tree does not hold (revision "
        f"{record.revision}); name the family ID alone to read the tree's revision"
    )


def _resumed_revision_absent(read: _TreeRead, resume: KnowledgeContinuation) -> str | None:
    """A family-seed walk resumed naming a revision is held to that revision, as a fresh read is."""

    named = read.request.family_revision_id
    if named is None or resume.response != "leaf" or resume.seed.get("kind") != "family":
        return None
    return _revision_absent(read, named)


def _leaf_response(
    read: _TreeRead, seed: dict[str, str], resume: KnowledgeContinuation | None
) -> dict[str, Any]:
    """One page of the family-complete leaf read of ``seed`` (MIK-R01 and MIK-R05), or its refusal.

    The leaf has one declared row order, so an ordering other than the default is refused on a
    fresh read (a resumed one is checked against the token like every walk).
    """

    ordering = read.request.ordering_input
    if resume is None and ordering is not None and ordering != DEFAULT_ORDERING:
        return _refused(
            read,
            "invalid_payload",
            f"the family-complete leaf read has one declared row order; orderingInput "
            f"{ordering!r} does not apply to it -- read without orderingInput",
        )
    prepared = prepare_leaf(
        LeafRequest(
            index_path=read.selected.database_path,
            memory_tree_id=read.tree.tree_key,
            index_state=read.tree.index_state,
            path=seed.get("path"),
            code_tree=read.code_tree,
            resume=resume,
            family=seed.get("id") if seed.get("kind") == "family" else None,
        )
    )
    if not isinstance(prepared, PreparedLeaf):  # a refused continuation, or an absent seed
        refused = _refused(read, prepared.code, prepared.detail)
        path = seed.get("path")
        if prepared.code == "registration_absent" and path:
            refused.update(absent_chain(path))  # MIK-R05: no family route covers the path either
        return refused
    states = LeafCurrentness(read.code_tree, [prepared])
    fixed = {"memoryTree": memory_tree_block(read.tree), "indexComplete": read.index_complete}

    def render(cut: PageCut) -> dict[str, Any]:
        block = prepared.render(cut)
        # The page facts and the continuation travel once, at the top of the response.
        payload = {k: v for k, v in block.items() if k not in ("page", "continuation")}
        return {
            "ok": True,
            "state": "page",
            "view": LEAF_VIEW,
            "repositoryId": read.request.repository_id,
            "snapshot": prepared.request.memory_tree_id,
            "completeWithinDeclaredScope": block["enumerationComplete"],
            "continuation": block["continuation"],
            "payload": payload,
            "page": block["page"],
            **fixed,
            "currentness": states.document(payload),
        }

    _cut, response = cut_page(prepared.rows, prepared.position, render)
    return response


def _containing_families(read: _TreeRead, revision_id: str | None) -> list[dict[str, Any]]:
    """The live families containing the invariant an ``invariant`` view reads, by ID and title."""

    if revision_id is None:
        return []
    with KnowledgeIndex(read.selected.database_path, expected_key=read.tree.tree_key) as index:
        text_id = index.text_id(revision_id)
        return [] if text_id is None else family_names(index, text_id.split("@", 1)[0])


def _refused(read: _TreeRead, code: str, detail: str) -> dict[str, Any]:
    """A refused tree read: it names the memory tree and its index state too (MIK-R01 rule 9)."""

    request = read.request
    return {
        "ok": True,
        "state": "refused",
        "view": request.view,
        "repositoryId": request.repository_id,
        "refusalCode": code,
        "refusalDetail": detail,
        "threshold": threshold_block(),
        "memoryTree": memory_tree_block(read.tree),
        "indexComplete": read.index_complete,
    }
