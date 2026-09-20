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
from typing import Annotated, Any

from fastapi import FastAPI, Query
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

__all__ = [
    "KNOWLEDGE_REVIEW_ENTRIES_ROUTE",
    "KNOWLEDGE_REVIEW_ROUTE",
    "KnowledgeReviewEntriesPort",
    "KnowledgeReviewPort",
    "register_review_routes",
    "review_request_from_query",
]

# The one route the reviewer surface is reached through. It is GET-only: the surface produces no
# record, and the assessment path this increment does not ship would not be reached from here.
KNOWLEDGE_REVIEW_ROUTE = "/api/review/intent"

# The entry route: the subjects the row above can be opened on. It is a second path rather than a
# second adapter, because the two answer different questions from one resolution -- which subjects
# the pair can be compared on, and what one such comparison renders -- and a caller that had to
# guess a subject id to reach the first would be choosing the candidate, which the browser may not.
KNOWLEDGE_REVIEW_ENTRIES_ROUTE = "/api/review/intent/entries"

# The two selector kinds the surface reviews. They are the two identity seeds R07 declares; every
# other seed kind addresses a revision, a membership or a claim rather than a subject a curator
# reviews, and is refused rather than mapped onto one of these.
SELECTOR_KINDS: tuple[str, ...] = ("invariant", "family")

KnowledgeReviewPort = Callable[[ReviewSurfaceRequest], KnowledgeReviewResult]
KnowledgeReviewEntriesPort = Callable[[str, str, str], ReviewEntryListResult]

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


def review_request_from_query(
    repository_id: str, master: str, leaf_id: str, selector_kind: str, selector_id: str
) -> ReviewSurfaceRequest | None:
    """Parse one query string into the typed request, or ``None`` when a selector is not admitted."""

    if selector_kind == "invariant":
        seed: KnowledgeReadSeed = InvariantIdentitySeed(invariant_id=selector_id)
    elif selector_kind == "family":
        seed = FamilyIdentitySeed(family_id=selector_id)
    else:
        return None
    return ReviewSurfaceRequest(
        repository_id=repository_id,
        master=master,
        leaf_id=leaf_id,
        selector=seed,
    )


def _status_for(result: KnowledgeReviewResult | ReviewEntryListResult) -> int:
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
) -> None:
    """Register the read-only reviewer routes. Must be called BEFORE the greedy static mount.

    ``config`` is accepted for symmetry with the other route registrars and for the workspace facts
    a port may need; the routes themselves resolve nothing from it, because resolution belongs to
    the port's own tier. Both ports are the composition root's, so a process that wires neither
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

    @app.get(KNOWLEDGE_REVIEW_ROUTE)
    def api_review_intent(
        repo: str,
        master: str,
        leaf: str,
        selectorKind: Annotated[str, Query(alias="selectorKind")],
        selectorId: Annotated[str, Query(alias="selectorId")],
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
                        "one recorded invariant or family identity"
                    ),
                    "offendingInput": selectorKind,
                    "expected": ", ".join(SELECTOR_KINDS),
                    "nextAction": "name selectorKind=invariant|family with the subject's record id",
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


def _json(result: KnowledgeReviewResult | ReviewEntryListResult) -> dict[str, Any]:
    """Serialize one typed result once, through the model that declares its shape."""

    return result.model_dump(mode="json", exclude_none=True)
