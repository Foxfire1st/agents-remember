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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult

__all__ = [
    "KNOWLEDGE_REVIEW_SUMMARY_ROUTE",
    "ReviewIntentSummaryPort",
    "register_review_summary_route",
]

KNOWLEDGE_REVIEW_SUMMARY_ROUTE = "/api/review/intent/summary"

ReviewIntentSummaryPort = Callable[[str, str, str], ReviewIntentSummaryResult]

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


def register_review_summary_route(app: FastAPI, port: ReviewIntentSummaryPort | None) -> None:
    """Register the read-only summary route. Must be called BEFORE the greedy static mount."""

    @app.get(KNOWLEDGE_REVIEW_SUMMARY_ROUTE)
    def api_review_intent_summary(repo: str, master: str, leaf: str) -> Response:
        if port is None:
            return JSONResponse(_UNWIRED_SUMMARY, status_code=503)
        result = port(repo, master, leaf)
        return JSONResponse(result.model_dump(mode="json", exclude_none=True))
