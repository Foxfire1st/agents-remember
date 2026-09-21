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
    KnowledgeReviewResult,
    ReviewEntryListResult,
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
    "KnowledgeReviewEntriesPort",
    "KnowledgeReviewPort",
    "ReviewSourceContentPort",
    "SourceContentRef",
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


def review_request_from_query(
    repository_id: str,
    master: str,
    leaf_id: str,
    selector_kind: str | None,
    selector_id: str | None,
) -> ReviewSurfaceRequest | None:
    """Parse one query string into the typed request, or ``None`` when a selector is not admitted.

    Two shapes are admitted and they are different questions. **No selector at all** is the task
    context: the review is opened from the task and lists the complete source change inventory of the
    pair it resolves, which is what a task with no recorded invariant -- or with no datasets yet --
    still has. **One named kind with an id** is a reviewed subject. A half-named selector, and a kind
    this surface does not review, are both refused with ``None`` rather than guessed at, because a
    caller that asked for a specific subject and received a whole-task review would be reading an
    answer to a question it did not ask.
    """

    if selector_kind is None and selector_id is None:
        return ReviewSurfaceRequest(
            repository_id=repository_id,
            master=master,
            leaf_id=leaf_id,
            selector=None,
        )
    if not selector_kind or not selector_id:
        return None
    seed: KnowledgeReadSeed | None = None
    if selector_kind == "invariant":
        seed = InvariantIdentitySeed(invariant_id=selector_id)
    elif selector_kind == "family":
        seed = FamilyIdentitySeed(family_id=selector_id)
    if seed is None:
        return None
    return ReviewSurfaceRequest(
        repository_id=repository_id,
        master=master,
        leaf_id=leaf_id,
        selector=seed,
    )


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
        selectorKind: Annotated[str | None, Query(alias="selectorKind")] = None,
        selectorId: Annotated[str | None, Query(alias="selectorId")] = None,
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
        request = review_request_from_query(repo, master, leaf, selectorKind, selectorId)
        if request is None:
            return JSONResponse(
                {
                    "status": "bad-request",
                    "detail": (
                        "the review selector names no admitted subject kind; the surface reviews "
                        "one recorded invariant or family identity, or no subject at all when both "
                        "selector parameters are omitted"
                    ),
                    "offendingInput": selectorKind or selectorId,
                    "expected": f"{', '.join(SELECTOR_KINDS)}, or no selector at all",
                    "nextAction": (
                        "name selectorKind=invariant|family together with the subject's record id, "
                        "or omit both to review the task's complete source change inventory"
                    ),
                },
                status_code=400,
            )
        try:
            result = port(request)
        except AuthorityError as err:
            return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
        except FileNotFoundError as err:
            return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)
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
    three one-line registrations; nothing about the answer changes, and the status idiom is the same
    one the two routes above use.
    """

    if port is None:
        return JSONResponse(_UNWIRED_SOURCE_CONTENT, status_code=503)
    request = source_content_request_from_query(ref)
    if request is None:
        return JSONResponse(_incomplete_generation(ref), status_code=400)
    try:
        result = port(request)
    except AuthorityError as err:
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)
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
