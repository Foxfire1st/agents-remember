"""The reviewer's tree view route: one leaf's comparison as four Git trees (MIK-R25).

Transport only, like the other reviewer routes: it takes the task context (repository, master,
leaf -- never a path), optionally the number of a recorded comparison to reopen or ``history=recorded``
for the leaf's latest record, calls the port the composition root wires, and serializes the typed
result once.

Every typed answer is a 200: ``trees``, ``not-converted`` (the leaf's memory is unconverted, so its
review is the dataset review) and ``refused`` (with the owner's refusal in the body). Only a process
composed without the port answers non-2xx (503), because then no answer exists at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.models.knowledge.review_trees import ReviewTreesResult

__all__ = [
    "KNOWLEDGE_REVIEW_TREES_ROUTE",
    "ReviewTreesPort",
    "ReviewTreesQuery",
    "register_review_trees_route",
]

KNOWLEDGE_REVIEW_TREES_ROUTE = "/api/review/trees"


@dataclass(frozen=True)
class ReviewTreesQuery:
    """One tree-view question: the task context, and which recorded comparison, if any."""

    repository_id: str
    master: str
    leaf_id: str
    number: int | None = None
    recorded: bool = False


ReviewTreesPort = Callable[[ReviewTreesQuery], ReviewTreesResult]

_UNWIRED: dict[str, Any] = {
    "status": "unavailable",
    "detail": (
        "no review adapter is wired into this process, so the tree view cannot resolve a "
        "comparison; nothing is served rather than an empty comparison"
    ),
    "nextAction": "start the dashboard through its composition root, which supplies the review adapter",
}


def register_review_trees_route(app: FastAPI, port: ReviewTreesPort | None) -> None:
    """Register the read-only tree view route. Must be called BEFORE the greedy static mount."""

    @app.get(KNOWLEDGE_REVIEW_TREES_ROUTE)
    def api_review_trees(
        repo: str,
        master: str,
        leaf: str,
        comparison: int | None = None,
        history: str | None = None,
    ) -> Response:
        if port is None:
            return JSONResponse(_UNWIRED, status_code=503)
        if history not in (None, "recorded") or (comparison is not None and comparison < 0):
            return JSONResponse(
                {
                    "status": "invalid-request",
                    "detail": "history may only be 'recorded'; comparison is a recorded number",
                    "nextAction": "name the leaf's recorded comparison number, or neither",
                },
                status_code=400,
            )
        result = port(
            ReviewTreesQuery(
                repository_id=repo,
                master=master,
                leaf_id=leaf,
                number=comparison,
                recorded=history == "recorded",
            )
        )
        return JSONResponse(result.model_dump(mode="json", by_alias=True, exclude_none=True))
