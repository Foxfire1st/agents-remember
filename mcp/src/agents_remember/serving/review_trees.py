"""The reviewer's tree view route: one leaf's comparison as four Git trees (MIK-R25).

Transport only, like the other reviewer routes: it takes the task context (repository, master,
leaf -- never a path), optionally the number of a recorded comparison to reopen or ``history=recorded``
for the leaf's latest record, calls the port the composition root wires, and serializes the typed
result once. ``invariants`` (comma-separated identities, as the landed review payload addresses
them) asks for those invariants' entries only, on both code sides with their excerpts: the focused
expression cards of one selection (MIK-R31). ``lane=files`` asks for the two destinations of the
unexplained-changes lane, and ``file=<path>`` for the classification of one changed path (MIK-R32);
each names one question, so at most one of ``invariants``, ``lane`` and ``file`` is given.

Every typed answer is a 200: ``trees``, ``not-converted`` (the leaf's memory is unconverted, so its
review is the dataset review) and ``refused`` (with the owner's refusal in the body). Only a process
composed without the port answers non-2xx (503), because then no answer exists at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.models.knowledge.review_trees import ReviewTreesResult

__all__ = [
    "KNOWLEDGE_REVIEW_TREES_ROUTE",
    "ReviewTreesPort",
    "ReviewTreesQuery",
    "ReviewTreesSelection",
    "register_review_trees_route",
]

KNOWLEDGE_REVIEW_TREES_ROUTE = "/api/review/trees"
# One card read names the invariants of one family selection; a bound keeps a request finite.
MAX_ENTRY_INVARIANTS = 500
# Each named identity is a UUID (36 characters); a longer key is not an identity this route answers.
MAX_INVARIANT_KEY_LENGTH = 64
# A repository-relative path, bounded like every path the review vocabulary carries.
MAX_FILE_PATH_LENGTH = 4096
LANE_FILES = "files"


@dataclass(frozen=True)
class ReviewTreesQuery:
    """One tree-view question: the task context, and which recorded comparison, if any."""

    repository_id: str
    master: str
    leaf_id: str
    number: int | None = None
    recorded: bool = False
    # The invariants whose entries the cards need (MIK-R31); naming any answers only those entries.
    invariants: tuple[str, ...] = ()
    # The unexplained-changes lane's destinations (MIK-R32), or one changed path's classification.
    lane: bool = False
    file: str | None = None

    @property
    def focused(self) -> bool:
        """Whether the query asks one focused question rather than for the leaf-wide view."""

        return bool(self.invariants) or self.lane or self.file is not None


ReviewTreesPort = Callable[[ReviewTreesQuery], ReviewTreesResult]

_UNWIRED: dict[str, Any] = {
    "status": "unavailable",
    "detail": (
        "no review adapter is wired into this process, so the tree view cannot resolve a "
        "comparison; nothing is served rather than an empty comparison"
    ),
    "nextAction": "start the dashboard through its composition root, which supplies the review adapter",
}


@dataclass(frozen=True)
class ReviewTreesSelection:
    """Which comparison, and which invariants' entries, one tree-view request asks about.

    One value rather than three loose query parameters, like the review route's own references:
    FastAPI derives it from the query string, and the transport checks it as one question.
    """

    comparison: int | None = None
    history: str | None = None
    invariants: str | None = None
    lane: str | None = None
    file: str | None = None

    def named(self) -> tuple[str, ...]:
        return tuple(key for key in (self.invariants or "").split(",") if key)

    def problem(self) -> str | None:
        """Why this selection is not a question the route answers, or ``None``."""

        if self.history not in (None, "recorded") or (
            self.comparison is not None and self.comparison < 0
        ):
            return "history may only be 'recorded'; comparison is a recorded number"
        named = self.named()
        if len(named) > MAX_ENTRY_INVARIANTS:
            return f"at most {MAX_ENTRY_INVARIANTS} invariants may be named at once"
        if any(len(key) > MAX_INVARIANT_KEY_LENGTH for key in named):
            return f"an invariant identity is at most {MAX_INVARIANT_KEY_LENGTH} characters"
        return self._focus_problem()

    def _focus_problem(self) -> str | None:
        if self.lane not in (None, LANE_FILES):
            return f"lane may only be '{LANE_FILES}'"
        if self.file is not None and not 0 < len(self.file) <= MAX_FILE_PATH_LENGTH:
            return f"file is one changed path of at most {MAX_FILE_PATH_LENGTH} characters"
        asked = [bool(self.named()), self.lane is not None, self.file is not None]
        if sum(asked) > 1:
            return "name at most one of invariants, lane and file"
        return None


NO_SELECTION = ReviewTreesSelection()


def register_review_trees_route(app: FastAPI, port: ReviewTreesPort | None) -> None:
    """Register the read-only tree view route. Must be called BEFORE the greedy static mount."""

    @app.get(KNOWLEDGE_REVIEW_TREES_ROUTE)
    def api_review_trees(
        repo: str,
        master: str,
        leaf: str,
        selection: Annotated[ReviewTreesSelection, Depends()] = NO_SELECTION,
    ) -> Response:
        if port is None:
            return JSONResponse(_UNWIRED, status_code=503)
        problem = selection.problem()
        if problem is not None:
            return JSONResponse(
                {
                    "status": "invalid-request",
                    "detail": problem,
                    "nextAction": (
                        "name the leaf's recorded comparison number, or neither, and at most one "
                        "of: the invariants of one family selection, lane=files, or one changed file"
                    ),
                },
                status_code=400,
            )
        result = port(
            ReviewTreesQuery(
                repository_id=repo,
                master=master,
                leaf_id=leaf,
                number=selection.comparison,
                recorded=selection.history == "recorded",
                invariants=selection.named(),
                lane=selection.lane == LANE_FILES,
                file=selection.file,
            )
        )
        return JSONResponse(result.model_dump(mode="json", by_alias=True, exclude_none=True))
