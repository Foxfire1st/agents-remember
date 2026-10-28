"""The changed-intent summary route: the one small read the task entry makes before a review opens.

Transport only, like the other reviewer routes in :mod:`agents_remember.serving.review`: it takes the
task context (repository, master, leaf -- never a path), calls the port the composition root wires,
and serializes the typed result once.

**Every typed answer is a 200.** ``counted``, ``partial`` and ``unavailable`` are all answers about
the comparison, and the body's ``state`` says which; an ``unavailable`` summary carries the owner's
refusal in the body. The review route itself still refuses with its own status when the reviewer is
opened. Mapping an uninitialized leaf's summary onto a 404 would put a console error on every task
page whose leaf has no knowledge yet -- the ordinary state of a new leaf -- which is exactly what the
change-set routes stopped doing for an unrecorded range. Only a process composed without the port
answers non-2xx (503), because then no summary exists at all.

It is its own module rather than a fourth route in ``serving/review.py`` because that module is past
the repository's size rail.

``comparison=<n>`` summarizes the leaf's recorded tree comparison ``n`` (MIK-R89 v2 rule 10). A
number the leaf did not record, and a negative one, are an ``unavailable`` summary carrying the
refusal -- still a 200, so the summary keeps its one status set.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult
from agents_remember.models.knowledge.review_refusal import ReviewRefusal
from agents_remember.serving.changeset_cache import ChangesetCache
from agents_remember.serving.intent_summary_cache import IntentSummaryCache, ReviewIntentSummaryPort
from agents_remember.serving.read_disconnect import read_while_connected

__all__ = [
    "KNOWLEDGE_REVIEW_SUMMARY_ROUTE",
    "ReviewIntentSummaryPort",
    "register_review_summary_route",
]

KNOWLEDGE_REVIEW_SUMMARY_ROUTE = "/api/review/intent/summary"

# The route's own unwired answer: "this process cannot count" is a different fact from "nothing
# changed", so a process composed without the port refuses by name rather than answering zero.
_UNWIRED_SUMMARY: dict[str, Any] = {
    "status": "unavailable",
    "detail": (
        "no review adapter is wired into this process, so the changed-intent summary cannot "
        "resolve a comparison; no count is served rather than a zero"
    ),
    "nextAction": "start the dashboard through its composition root, which supplies the review adapter",
}


def register_review_summary_route(
    app: FastAPI, port: ReviewIntentSummaryPort | None, cache: ChangesetCache
) -> None:
    """Register the read-only summary route. Must be called BEFORE the greedy static mount."""
    reader = None if port is None else IntentSummaryCache(port, cache)

    @app.get(
        KNOWLEDGE_REVIEW_SUMMARY_ROUTE,
        responses={304: {"description": "Summary unchanged; ETag only, no body"}},
    )
    async def api_review_intent_summary(
        request: Request, repo: str, master: str, leaf: str, comparison: int | None = None
    ) -> Response:
        if reader is None:
            return JSONResponse(_UNWIRED_SUMMARY, status_code=503)
        if comparison is not None and comparison < 0:
            return JSONResponse(_negative_comparison(repo, master, leaf, comparison))
        # A read whose client went away stops computing and keeps nothing (D569 mean 4).
        return await read_while_connected(
            request, partial(reader.response, request, repo, master, leaf, comparison)
        )


def _negative_comparison(repo: str, master: str, leaf: str, comparison: int) -> dict[str, Any]:
    """The typed ``unavailable`` summary for a number no record can carry."""

    refused = ReviewIntentSummaryResult(
        state="unavailable",
        repository_id=repo,
        master=master,
        leaf_id=leaf,
        refusal=ReviewRefusal(
            code="candidate_unresolved",
            detail="a recorded tree comparison number is never negative, so none is counted",
            next_action=(
                "name a comparison number the leaf's trees answer lists, or omit comparison to "
                "count the leaf's own review"
            ),
            offending_input=str(comparison),
        ),
    )
    return refused.model_dump(mode="json", exclude_none=True)
