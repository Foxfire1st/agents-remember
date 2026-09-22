"""The Intent Reviewer's HTTP shim: transport only, over a port the composition root supplies.

This module validates a query string, builds the one typed request the composition consumes, calls
the injected port and maps the typed result onto the change-set routes' own 400/404 status idiom. It
**selects nothing, ranks nothing, computes no scope and resolves no reference**: every one of those
answers comes from the application operations behind the port, and the value it returns is that
call's own typed result serialized once.

**Why a port rather than a direct import.** ``layers.toml`` ranks ``serving`` below ``application``,
so this module may not import the read, diff and view operations it composes. It takes
:data:`KnowledgeReviewPort` the same way the launch route takes the capsule compiler: the composition
root wires it in :mod:`agents_remember.cli.dashboard`, and a process that omits it refuses by name
instead of serving an empty surface.

**The candidate is never addressed by path.** The route accepts a repository, a master, a leaf id and
one recorded subject selector. No filesystem path is accepted, so a browser cannot choose which
dataset is reviewed; the resolution behind the port owns that decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse, Response

from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.read import (
    FamilyIdentitySeed,
    InvariantIdentitySeed,
    KnowledgeReadSeed,
)
from agents_remember.models.knowledge.review import (
    MAXIMUM_REVIEW_PAGE_SIZE,
    REVIEW_PAGED_COLLECTIONS,
    KnowledgeReviewResult,
    ReviewEntryListResult,
    ReviewPagedCollection,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.review_source_content import (
    ReviewSourceContentRequest,
    ReviewSourceContentResult,
)

__all__ = [
    "KNOWLEDGE_REVIEW_ENTRIES_ROUTE",
    "KNOWLEDGE_REVIEW_ROUTE",
    "KNOWLEDGE_REVIEW_SOURCE_CONTENT_ROUTE",
    "NO_PAGING",
    "NO_SELECTOR",
    "AdmittedPaging",
    "KnowledgeReviewEntriesPort",
    "KnowledgeReviewPort",
    "ReviewPagingRef",
    "ReviewSelectorRef",
    "ReviewSourceContentPort",
    "SourceContentRef",
    "UnadmittedReviewQuery",
    "paged_review_request",
    "register_review_routes",
    "review_request_from_query",
    "source_content_request_from_query",
]

# The one route the reviewer surface is reached through. It is GET-only: the surface produces no
# record, and the assessment path this increment does not ship would not be reached from here.
KNOWLEDGE_REVIEW_ROUTE = "/api/review/intent"

# The entry route: the subjects the row above can be opened on. It is a second path rather than a
# second adapter, because the two answer different questions from one resolution -- which subjects
# the pair can be compared on, and what one such comparison renders -- and a caller that had to
# guess a subject id to reach the first would be choosing the candidate, which the browser may not.
KNOWLEDGE_REVIEW_ENTRIES_ROUTE = "/api/review/intent/entries"

# The expansion route: one listed entry's actual content at the two bound code trees. It is a third
# path rather than a field on the payload because the inventory is the whole task's change set and a
# payload that carried every file's text would be a document dump; the browser asks for exactly the
# row a reader opened, naming the generation the listing published. Like the other two it is GET-only
# and it accepts no filesystem path: the repository is resolved from canonical task context.
KNOWLEDGE_REVIEW_SOURCE_CONTENT_ROUTE = "/api/review/intent/source-content"

# The two selector kinds the surface reviews. They are the two identity seeds R07 declares; every
# other seed kind addresses a revision, a membership or a claim rather than a subject a curator
# reviews, and is refused rather than mapped onto one of these.
SELECTOR_KINDS: tuple[str, ...] = ("invariant", "family")

KnowledgeReviewPort = Callable[[ReviewSurfaceRequest], KnowledgeReviewResult]
KnowledgeReviewEntriesPort = Callable[[str, str, str], ReviewEntryListResult]
ReviewSourceContentPort = Callable[[ReviewSourceContentRequest], ReviewSourceContentResult]

# The entry route's own unwired answer. It says which adapter is missing rather than reporting an
# empty list, because "no subject is reviewable here" and "nothing can answer that question" are
# different facts and only one of them is true when the process was composed without the port.
_UNWIRED_ENTRIES: dict[str, Any] = {
    "status": "unavailable",
    "detail": (
        "no review adapter is wired into this process, so the review entry list cannot resolve a "
        "candidate; the surface is not served rather than served empty"
    ),
    "nextAction": (
        "start the dashboard through its composition root, which supplies the review adapter"
    ),
}

# The expansion route's own unwired answer, for the same reason as the two above: "this process
# cannot answer" and "this entry has no content" are different facts and only one of them is true
# when the composition omitted the port.
_UNWIRED_SOURCE_CONTENT: dict[str, Any] = {
    "status": "unavailable",
    "detail": (
        "no review adapter is wired into this process, so an inventory entry's source content "
        "cannot be opened; the surface is not served rather than served as an empty file"
    ),
    "nextAction": (
        "start the dashboard through its composition root, which supplies the review adapter"
    ),
}

# The two failures the ports themselves can raise, as the same actionable shape every other refusal
# on these routes already has. A reader has to be able to act on them (ICR-R16), so each carries the
# next action its own exception implies rather than only the message the exception happened to hold;
# the tool that answers is the caller's own authority, and neither route substitutes a repository or
# a path the caller did not name.
_AUTHORITY_NEXT_ACTION = (
    "name a repository the configured workspace authority admits, then reopen the review; these "
    "routes read no other repository in its place"
)
_NOT_FOUND_NEXT_ACTION = (
    "reopen the review from the task context whose recorded datasets and paths exist; a path this "
    "leaf does not hold is not substituted by another one"
)


def _transport_refusal(
    status: str,
    detail: str,
    *,
    next_action: str,
    offending_input: str | None = None,
) -> dict[str, Any]:
    """One transport-level refusal body, in the typed refusals' own field vocabulary."""

    body: dict[str, Any] = {"status": status, "detail": detail, "nextAction": next_action}
    if offending_input:
        body["offendingInput"] = offending_input
    return body


def _port_outcome(port: Callable[[Any], Any], request: Any) -> Any | Response:
    """Call one port, or answer the two failures the change-set routes already name.

    The one implementation of this mapping: both adapters below reach their port through it, so the
    400/404 idiom (and the actionable fields on its bodies) cannot come to differ between them.
    """

    try:
        return port(request)
    except AuthorityError as err:
        return JSONResponse(
            _transport_refusal(
                "bad-path",
                str(err),
                next_action=_AUTHORITY_NEXT_ACTION,
            ),
            status_code=400,
        )
    except FileNotFoundError as err:
        body = _transport_refusal(
            "not-found",
            str(err),
            next_action=_NOT_FOUND_NEXT_ACTION,
            offending_input=str(err),
        )
        body["path"] = str(err)
        return JSONResponse(body, status_code=404)


@dataclass(frozen=True)
class SourceContentRef:
    """Which entry, at which generation, in which task context -- the expansion's whole selector.

    A file read is answerable only once all of it is known: the task context locates the leaf whose
    review is being read, the path names the entry, and the two code tree ids name the generation the
    listing published. Any one of them alone selects nothing, so the selector travels as one value
    from the query string down to the read -- and the two tree ids are named by the caller rather
    than resolved by the server, which is what keeps an opened entry bound to the generation the
    reader was looking at. It carries no filesystem path and no root.
    """

    repo: str
    master: str
    leaf: str
    path: str = ""
    before_code_tree_id: Annotated[str, Query(alias="beforeCodeTreeId")] = ""
    after_code_tree_id: Annotated[str, Query(alias="afterCodeTreeId")] = ""


@dataclass(frozen=True)
class ReviewSelectorRef:
    """Which recorded subject a request reviews, as the two query parameters spelling it.

    It is one value for the same reason the expansion's selector is: the two fields are one question
    -- which subject, of which reviewable kind -- and a caller that supplied only half of it would be
    asking for a subject this surface cannot address. FastAPI derives both from the query string.
    """

    selector_kind: Annotated[str | None, Query(alias="selectorKind")] = None
    selector_id: Annotated[str | None, Query(alias="selectorId")] = None


# The one no-subject value: the task-context request, which compares no knowledge operand. A module
# singleton because the route's `Depends()` default must not be a call performed in the signature.
NO_SELECTOR = ReviewSelectorRef()


@dataclass(frozen=True)
class ReviewPagingRef:
    """Which bounded collection a request continues, and the cursor its owner minted for it.

    It is a reference rather than three loose parameters for the same reason the expansion's selector
    is one: the fields are one question -- "which walk, at which position" -- and FastAPI derives them
    from the query string, so the transport never assembles a part of the question itself. Its default
    is a module-level value rather than a call in the signature, so the route's dependency is one
    shared immutable object instead of one built per request.
    """

    page_of: Annotated[str | None, Query(alias="pageOf")] = None
    continuation: Annotated[str | None, Query()] = None
    # The bound is deliberately *not* a ``Query(le=...)``: this route admits or refuses the pair in
    # its own vocabulary, so an over-bound size reaches the caller as the route's actionable
    # ``bad-request`` naming the maximum rather than as a framework 422 or -- as it was before this
    # was fixed -- an uncaught model error and an opaque 500.
    page_size: Annotated[int, Query(alias="pageSize")] = 0


# The one no-paging value: a request that continues nothing. It is a module singleton because the
# route's `Depends()` default must not be a call performed in the signature.
NO_PAGING = ReviewPagingRef()


@dataclass(frozen=True)
class UnadmittedReviewQuery:
    """One query this route will not turn into a request, and the input that stopped it.

    It exists because the refusal body's own contract is to name *the offending input*: a caller that
    sent a bad collection name and was pointed at its (perfectly good) subject selector cannot repair
    the request. The field is therefore filled by the branch that failed rather than by a fallback
    order over everything the caller sent.
    """

    offending_input: str
    detail: str
    expected: str


def review_request_from_query(
    repository_id: str,
    master: str,
    leaf_id: str,
    selector_kind: str | None,
    selector_id: str | None,
) -> ReviewSurfaceRequest | None:
    """Parse one query string into the typed request, or ``None`` when the selector is not admitted.

    Two shapes are admitted and they are different questions. **No selector at all** is the task
    context: the review is opened from the task and lists the complete source change inventory of the
    pair it resolves, which is what a task with no recorded invariant -- or with no datasets yet --
    still has. **One named kind with an id** is a reviewed subject. A half-named selector, and a kind
    this surface does not review, are both refused with ``None`` rather than guessed at, because a
    caller that asked for a specific subject and received a whole-task review would be reading an
    answer to a question it did not ask.

    The paging pair is admitted by :func:`paged_review_request`, which this function calls; both
    spellings of the request therefore go through one place that decides which shapes are admitted.
    """

    admitted = paged_review_request(
        repository_id,
        master,
        leaf_id,
        ReviewSelectorRef(selector_kind=selector_kind, selector_id=selector_id),
        NO_PAGING,
    )
    # This spelling is the selector-only one its existing callers and tests use, so a problem is
    # reported the way it always was here; the richer answer is :func:`paged_review_request`'s.
    return None if isinstance(admitted, UnadmittedReviewQuery) else admitted


def paged_review_request(
    repository_id: str,
    master: str,
    leaf_id: str,
    selector: ReviewSelectorRef,
    paging: ReviewPagingRef,
) -> ReviewSurfaceRequest | UnadmittedReviewQuery:
    """Parse one query string, with its paging pair, into the typed request or the refusal it earns.

    ``paging`` is the paging pair (ICR-R10): the collection this call continues and the cursor that
    collection's own owner minted for it. A cursor without a collection is refused here rather than
    forwarded -- which owner's walk it belongs to is not a question this transport may answer -- and a
    collection name outside the two the surface pages is refused for the same reason the selector kind
    is. A page size above the surface's declared maximum is refused here too, in this route's own
    vocabulary, because the model's own bound would otherwise be an uncaught validation error. The
    transport *parses* them and decides nothing else: whether the cursor binds the comparison this
    request resolves to is the owners' own answer.
    """

    admitted = _admitted_paging(paging)
    if isinstance(admitted, UnadmittedReviewQuery):
        return admitted
    if selector.selector_kind is None and selector.selector_id is None:
        return ReviewSurfaceRequest(
            repository_id=repository_id,
            master=master,
            leaf_id=leaf_id,
            selector=None,
            page_of=admitted.collection,
            continuation=admitted.continuation,
            page_size=admitted.page_size,
        )
    if not selector.selector_kind or not selector.selector_id:
        return _unadmitted(
            offending_input=selector.selector_kind or selector.selector_id or "",
            detail=(
                "the review selector is half-named: a subject kind without its record id, or a record "
                "id without its kind, names no reviewable subject and is not the task context either"
            ),
            expected="selectorKind=invariant|family together with selectorId, or neither",
        )
    seed: KnowledgeReadSeed | None = None
    if selector.selector_kind == "invariant":
        seed = InvariantIdentitySeed(invariant_id=selector.selector_id)
    elif selector.selector_kind == "family":
        seed = FamilyIdentitySeed(family_id=selector.selector_id)
    if seed is None:
        return _unadmitted(
            offending_input=selector.selector_kind,
            detail=(
                "the review selector names no admitted subject kind; the surface reviews one recorded "
                "invariant or family identity, or no subject at all when both selector parameters "
                "are omitted"
            ),
            expected=f"{', '.join(SELECTOR_KINDS)}, or no selector at all",
        )
    return ReviewSurfaceRequest(
        repository_id=repository_id,
        master=master,
        leaf_id=leaf_id,
        selector=seed,
        page_of=admitted.collection,
        continuation=admitted.continuation,
        page_size=admitted.page_size,
    )


@dataclass(frozen=True)
class AdmittedPaging:
    """The three paging fields once this route has admitted them, in the request's own types.

    It exists so the admission's answers are typed rather than assembled as a loose mapping: the
    collection is the request model's own literal union, and a caller cannot hand a string the
    membership test never passed.
    """

    collection: ReviewPagedCollection | None = None
    continuation: str | None = None
    page_size: int = 0


def _admitted_paging(paging: ReviewPagingRef) -> AdmittedPaging | UnadmittedReviewQuery:
    """The three paging fields as one request mapping, or the admission problem the pair earns.

    The three checks are one decision -- "is this a paging pair this route admits" -- so they live
    together and each names the value that failed. A page size above the surface's declared maximum is
    refused here in this route's own vocabulary rather than raised out of the request model as an
    uncaught validation error.
    """

    # An empty query spelling is an absent parameter, not a value: ``pageOf=`` is what a form sends
    # when the reader picked "whole review", and forwarding it as a collection name would fail the
    # request model's own literal (an uncaught validation error) instead of answering as the absence
    # it is.
    collection = None if not paging.page_of else _admitted_collection(paging.page_of)
    continuation = paging.continuation or None
    if paging.page_of and collection is None:
        return _unadmitted(
            offending_input=paging.page_of,
            detail=(
                "pageOf names no bounded collection this surface pages; the surface publishes a page "
                "of the knowledge comparison or of the review-matrix records and of nothing else"
            ),
            expected=f"{', '.join(REVIEW_PAGED_COLLECTIONS)}",
        )
    if continuation is not None and collection is None:
        return _unadmitted(
            offending_input=continuation,
            detail=(
                "a continuation was presented without the collection it continues; which owner's walk "
                "a cursor belongs to is not a question this transport may answer"
            ),
            expected="pageOf beside every continuation",
        )
    if not 0 <= paging.page_size <= MAXIMUM_REVIEW_PAGE_SIZE:
        return _unadmitted(
            offending_input=str(paging.page_size),
            detail=(
                f"pageSize is outside the bound this surface applies: a page is between 1 and "
                f"{MAXIMUM_REVIEW_PAGE_SIZE} rows, and 0 asks for the owner's own declared bound; a "
                "size the surface would have to narrow is refused rather than silently shrunk"
            ),
            expected=f"0 (the owner's bound) or 1..{MAXIMUM_REVIEW_PAGE_SIZE}",
        )
    return AdmittedPaging(
        collection=collection, continuation=continuation, page_size=paging.page_size
    )


def _admitted_collection(value: str) -> ReviewPagedCollection | None:
    """One admitted collection name as the request model's own literal, or ``None``.

    The membership test is the loop: the tuple's element type is the literal union, so a name that
    matches an admitted entry *is* that value rather than a string this module asserts is one.
    """

    for admitted in REVIEW_PAGED_COLLECTIONS:
        if value == admitted:
            return admitted
    return None


def _unadmitted(*, offending_input: str, detail: str, expected: str) -> UnadmittedReviewQuery:
    """One admission problem, naming the input that actually stopped the request."""

    return UnadmittedReviewQuery(offending_input=offending_input, detail=detail, expected=expected)


def source_content_request_from_query(
    ref: SourceContentRef,
) -> ReviewSourceContentRequest | None:
    """Parse one expansion selector, or ``None`` when the generation is not fully named.

    All three of path, before-tree and after-tree are required together, and a blank one is refused
    rather than defaulted: a missing tree id would make the server choose a generation, which is
    exactly the substitution this read exists to prevent. The route accepts no filesystem path, so a
    browser cannot choose which repository or which generation is read.
    """

    if not ref.path or not ref.before_code_tree_id or not ref.after_code_tree_id:
        return None
    return ReviewSourceContentRequest(
        repository_id=ref.repo,
        master=ref.master,
        leaf_id=ref.leaf,
        path=ref.path,
        before_code_tree_id=ref.before_code_tree_id,
        after_code_tree_id=ref.after_code_tree_id,
    )


def _status_for(
    result: KnowledgeReviewResult | ReviewEntryListResult | ReviewSourceContentResult,
) -> int:
    """The status one result maps onto, in the change-set routes' own two-shape idiom."""

    if result.refusal is None:
        return 200
    code = result.refusal.code
    if code == "review_adapter_unavailable":
        return 503
    if code in {
        "candidate_unresolved",
        "candidate_not_live",
        "candidate_dataset_absent",
        "subject_unresolved",
    }:
        return 404
    return 400


def register_review_routes(
    app: FastAPI,
    config: McpRuntimeConfig,
    port: KnowledgeReviewPort | None,
    entries_port: KnowledgeReviewEntriesPort | None = None,
    source_content_port: ReviewSourceContentPort | None = None,
) -> None:
    """Register the read-only reviewer routes. Must be called BEFORE the greedy static mount.

    ``config`` is accepted for symmetry with the other route registrars and for the workspace facts
    a port may need; the routes themselves resolve nothing from it, because resolution belongs to
    the port's own tier. Every port is the composition root's, so a process that wires none of them
    refuses by name instead of serving a surface with nothing behind it.
    """

    del config

    @app.get(KNOWLEDGE_REVIEW_ENTRIES_ROUTE)
    def api_review_intent_entries(repo: str, master: str, leaf: str) -> Response:
        if entries_port is None:
            return JSONResponse(_UNWIRED_ENTRIES, status_code=503)
        result = entries_port(repo, master, leaf)
        return JSONResponse(
            _json(result), status_code=200 if result.state == "entries" else _status_for(result)
        )

    @app.get(KNOWLEDGE_REVIEW_SOURCE_CONTENT_ROUTE)
    def api_review_intent_source_content(ref: Annotated[SourceContentRef, Depends()]) -> Response:
        return _source_content_response(source_content_port, ref)

    @app.get(KNOWLEDGE_REVIEW_ROUTE)
    def api_review_intent(
        repo: str,
        master: str,
        leaf: str,
        selector: Annotated[ReviewSelectorRef, Depends()] = NO_SELECTOR,
        paging: Annotated[ReviewPagingRef, Depends()] = NO_PAGING,
    ) -> Response:
        if port is None:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "detail": (
                        "no review adapter is wired into this process, so the Intent Reviewer "
                        "cannot resolve a candidate; the surface is not served rather than served "
                        "empty"
                    ),
                    "nextAction": (
                        "start the dashboard through its composition root, which supplies the "
                        "review adapter"
                    ),
                },
                status_code=503,
            )
        request = paged_review_request(repo, master, leaf, selector, paging)
        if isinstance(request, UnadmittedReviewQuery):
            # The body names the input the admission actually refused. It used to fall back over
            # everything the caller sent, so a bad pageOf was reported as the (admitted) selector
            # kind -- pointing a caller repairing the request at the one parameter that was fine.
            return JSONResponse(
                {
                    "status": "bad-request",
                    "detail": request.detail,
                    "offendingInput": request.offending_input,
                    "expected": request.expected,
                    "nextAction": (
                        "name selectorKind=invariant|family together with the subject's record id, "
                        "or omit both to review the task's complete source change inventory; to "
                        "advance a page, send back the continuation the previous response published "
                        "beside the pageOf it was published for"
                    ),
                },
                status_code=400,
            )
        result = _port_outcome(port, request)
        if isinstance(result, Response):
            return result
        return JSONResponse(_json(result), status_code=_status_for(result))


def _json(
    result: KnowledgeReviewResult | ReviewEntryListResult | ReviewSourceContentResult,
) -> dict[str, Any]:
    """Serialize one typed result once, through the model that declares its shape."""

    return result.model_dump(mode="json", exclude_none=True)


def _source_content_response(
    port: ReviewSourceContentPort | None, ref: SourceContentRef
) -> Response:
    """One expansion read's whole transport: the unwired answer, the selector check, the 400/404 map.

    It is a module-level function rather than the route body so the registrar stays a composition of
    three one-line registrations; nothing about the answer changes, and the status idiom (and its
    actionable fields) is the one implementation both adapters reach through :func:`_port_outcome`.
    """

    if port is None:
        return JSONResponse(_UNWIRED_SOURCE_CONTENT, status_code=503)
    request = source_content_request_from_query(ref)
    if request is None:
        return JSONResponse(_incomplete_generation(ref), status_code=400)
    result = _port_outcome(port, request)
    if isinstance(result, Response):
        return result
    return JSONResponse(_json(result), status_code=_status_for(result))


def _incomplete_generation(ref: SourceContentRef) -> dict[str, Any]:
    """The 400 body for a query that did not name the generation it wants opened."""

    return {
        "status": "bad-request",
        "detail": (
            "an inventory entry's content is opened for one exact generation: the path and both "
            "bound code tree ids are required together, and a missing one is refused rather than "
            "resolved to a generation the caller did not name"
        ),
        "offendingInput": ref.path or ref.before_code_tree_id or ref.after_code_tree_id,
        "expected": "path, beforeCodeTreeId and afterCodeTreeId",
        "nextAction": (
            "expand an entry through the inventory the review returned, whose own path and code "
            "tree ids are the address of the content"
        ),
    }
