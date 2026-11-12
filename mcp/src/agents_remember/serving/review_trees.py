"""The reviewer's tree view route: one leaf's comparison as four Git trees (MIK-R25).

Transport only, like the other reviewer routes: it takes the task context (repository, master,
leaf -- never a path), optionally the number of a recorded comparison to reopen or ``history=recorded``
for the leaf's latest record, calls the port the composition root wires, and serializes the typed
result once. ``invariants`` (comma-separated identities, as the landed review payload addresses
them) asks for those invariants' entries only, on both code sides with their excerpts: the focused
expression cards of one selection (MIK-R31). ``entry`` with ``entry_side=before|after`` resolves
one recorded entry's owner on that exact side and returns only that invariant's cards.
``lane=files`` asks for the two destinations of the
unexplained-changes lane, and ``file=<path>`` for the classification of one changed path (MIK-R32);
each names one question, so at most one of ``invariants``, ``entry``, ``lane`` and ``file`` is given.

``view=overview`` projects complete location counts and ordering facts without the location
rows or full worklist; the default ``complete`` view remains their evidence reader. An overview
cannot combine with a focused selector. Both representations use the same validated comparison.

Every typed answer is a 200: ``trees``, ``refused`` (with the owner's refusal in the body; a leaf
whose memory is unconverted is refused this way, each knowledge side named ``legacy-unavailable``,
and its review reads source identities only) and ``not-converted`` (a resolution with no tree
comparison that names no unreadable knowledge side). Only a process composed without the port
answers non-2xx (503), because then no answer exists at all.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from agents_remember.kernel.reviewer_worklist_process import worklist_request
from agents_remember.models.knowledge.review_tree_overview import ReviewInvariantChanges
from agents_remember.models.knowledge.review_trees import ReviewTreesResult
from agents_remember.serving.read_disconnect import read_while_connected
from agents_remember.serving.review_open_observation import MAX_OPEN_ID

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
# The reviewer's blocking reads run on this route's own threads, never on the event loop's shared
# default executor, which the dashboard's background loops fill with their own blocking work
# (MIK-R42). A read waiting for a worklist child holds one thread; reads beyond the pool's size
# wait for a free thread, and the background loops can never take these threads.
REVIEW_READ_THREADS = 16


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
    entry: str | None = None
    entry_side: Literal["before", "after"] | None = None
    view: Literal["complete", "overview"] = "complete"

    @property
    def focused(self) -> bool:
        """Whether the query asks one focused question rather than for the leaf-wide view."""

        return bool(self.invariants) or self.lane or self.file is not None or self.entry is not None


ReviewTreesPort = Callable[[ReviewTreesQuery], ReviewTreesResult]

#: Which invariants differ between the comparison a page shows and a newer one (D576): the leaf,
#: then the shown and the newer comparison numbers.
ReviewInvariantChangesPort = Callable[[str, str, str, int, int], ReviewInvariantChanges]
KNOWLEDGE_REVIEW_INVARIANT_CHANGES_ROUTE = "/api/review/trees/changes"

#: The order the page drew one comparison's invariants in, for one of its opens (D585): the leaf,
#: the comparison number, the open's id (none: the page named none) and the selector ids in the
#: page's own order. The background prepares those details in that order; nothing is computed here.
ReviewDetailOrderPort = Callable[[str, str, str, int, str | None, tuple[str, ...]], None]
KNOWLEDGE_REVIEW_DETAIL_ORDER_ROUTE = "/api/review/trees/order"

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
    view: str = "complete"
    invariants: str | None = None
    lane: str | None = None
    file: str | None = None
    entry: str | None = None
    entry_side: str | None = None

    def named(self) -> tuple[str, ...]:
        return tuple(key for key in (self.invariants or "").split(",") if key)

    def problem(self) -> str | None:
        """Why this selection is not a question the route answers, or ``None``."""

        if self.history not in (None, "recorded") or (
            self.comparison is not None and self.comparison < 0
        ):
            return "history may only be 'recorded'; comparison is a recorded number"
        if self.view not in ("complete", "overview"):
            return "view may only be complete or overview"
        named = self.named()
        if len(named) > MAX_ENTRY_INVARIANTS:
            return f"at most {MAX_ENTRY_INVARIANTS} invariants may be named at once"
        if any(len(key) > MAX_INVARIANT_KEY_LENGTH for key in named):
            return f"an invariant identity is at most {MAX_INVARIANT_KEY_LENGTH} characters"
        return self._focus_problem()

    def _focus_problem(self) -> str | None:
        entry_problem = self._entry_problem()
        if entry_problem is not None:
            return entry_problem
        if self.lane not in (None, LANE_FILES):
            return f"lane may only be '{LANE_FILES}'"
        if self.file is not None and not 0 < len(self.file) <= MAX_FILE_PATH_LENGTH:
            return f"file is one changed path of at most {MAX_FILE_PATH_LENGTH} characters"
        asked = [
            bool(self.named()),
            self.lane is not None,
            self.file is not None,
            self.entry is not None,
        ]
        if self.view == "overview" and any(asked):
            return "overview cannot be combined with a focused selector"
        if sum(asked) > 1:
            return "name at most one of invariants, lane, file and entry"
        return None

    def _entry_problem(self) -> str | None:
        if self.entry is not None:
            if not 0 < len(self.entry) <= MAX_INVARIANT_KEY_LENGTH:
                return f"entry is one identifier of at most {MAX_INVARIANT_KEY_LENGTH} characters"
            if self.entry_side not in ("before", "after"):
                return "entry requires entry_side=before or entry_side=after"
            if self.invariants is not None:
                return "name at most one of invariants, lane, file and entry"
        elif self.entry_side is not None:
            return "entry_side requires an entry"
        return None


NO_SELECTION = ReviewTreesSelection()


def _trees_response(port: ReviewTreesPort, query: ReviewTreesQuery) -> Response:
    """One tree view's port call and serialization, on the route's own worker thread.

    The request's worklist deadline runs from here. Its cancellation is the read's own token, bound
    by :func:`~agents_remember.serving.read_disconnect.read_while_connected`: a requester that left
    stops its share of the work, and a shared flight stops only when every asker left (D569).
    """

    with worklist_request():
        result = port(query)
    return JSONResponse(result.model_dump(mode="json", by_alias=True, exclude_none=True))


def register_review_invariant_changes_route(
    app: FastAPI, port: ReviewInvariantChangesPort | None
) -> None:
    """Register the route that names the invariants a leaf's move changed (D576)."""

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="review-changes")
    weakref.finalize(app, executor.shutdown, wait=False)

    @app.get(KNOWLEDGE_REVIEW_INVARIANT_CHANGES_ROUTE)
    async def api_review_invariant_changes(
        request: Request, question: Annotated[_ChangesQuestion, Depends()]
    ) -> Response:
        if port is None:
            return JSONResponse(_UNWIRED, status_code=503)
        wired = port

        def answer() -> JSONResponse:
            result = wired(*question.asked())
            return JSONResponse(result.model_dump(mode="json", by_alias=True, exclude_none=True))

        return await read_while_connected(request, answer, executor=executor)


@dataclass(frozen=True)
class _ChangesQuestion:
    """The leaf, and the comparison the page shows (``from``) and the newer one (``to``)."""

    repo: str
    master: str
    leaf: str
    shown: Annotated[int, Query(alias="from", ge=1)]
    newer: Annotated[int, Query(alias="to", ge=1)]

    def asked(self) -> tuple[str, str, str, int, int]:
        return (self.repo, self.master, self.leaf, self.shown, self.newer)


class _DetailOrder(BaseModel):
    """The page's order of the invariants it drew for one comparison: the message is the order."""

    repo: str
    master: str
    leaf: str
    comparison: int = Field(ge=1)
    open: str | None = Field(default=None, min_length=1, max_length=MAX_OPEN_ID)
    # Bounded as the card read bounds the invariants it names.
    order: tuple[Annotated[str, Field(max_length=MAX_INVARIANT_KEY_LENGTH)], ...] = Field(
        max_length=MAX_ENTRY_INVARIANTS
    )


def register_review_detail_order_route(app: FastAPI, port: ReviewDetailOrderPort | None) -> None:
    """Register the route the page names its drawn order on, after its open's six reads (D585).

    It hands the order over and returns at once: it reads nothing and computes nothing, so it can
    never lengthen the open's reads.
    """

    @app.post(KNOWLEDGE_REVIEW_DETAIL_ORDER_ROUTE, status_code=204)
    def api_review_detail_order(drawn: _DetailOrder) -> Response:
        if port is None:
            return JSONResponse(_UNWIRED, status_code=503)
        port(drawn.repo, drawn.master, drawn.leaf, drawn.comparison, drawn.open, drawn.order)
        return Response(status_code=204)


def register_review_trees_route(app: FastAPI, port: ReviewTreesPort | None) -> None:
    """Register the read-only tree view route. Must be called BEFORE the greedy static mount."""

    executor = ThreadPoolExecutor(
        max_workers=REVIEW_READ_THREADS, thread_name_prefix="review-trees"
    )
    weakref.finalize(app, executor.shutdown, wait=False)

    @app.get(KNOWLEDGE_REVIEW_TREES_ROUTE)
    async def api_review_trees(
        request: Request,
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
                        "of: invariants, lane=files, one changed file, or entry with entry_side"
                    ),
                },
                status_code=400,
            )
        query = ReviewTreesQuery(
            repository_id=repo,
            master=master,
            leaf_id=leaf,
            number=selection.comparison,
            recorded=selection.history == "recorded",
            view=cast(Literal["complete", "overview"], selection.view),
            invariants=selection.named(),
            lane=selection.lane == LANE_FILES,
            file=selection.file,
            entry=selection.entry,
            entry_side=cast(Literal["before", "after"] | None, selection.entry_side),
        )
        return await read_while_connected(
            request, partial(_trees_response, port, query), executor=executor
        )
