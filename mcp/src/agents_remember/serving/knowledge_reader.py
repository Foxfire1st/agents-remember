"""The knowledge reader's route: read-only views of a repository's knowledge at any memory tree (MIK-R29).

Transport only, like the reviewer routes: ``GET /api/knowledge/reader/<view>`` takes the repository,
the memory tree (``commit``: ``published`` by default, a memory commit, or ``leaf:<scope>``) and the
view's subject (``path``, ``id``, ``census``, ``locator``/``blob`` for code, ``continuation`` for
the next page of a directory's ``subtree``), calls the port the
composition root wires (``application/knowledge_reader``), and serializes its answer once.

Every typed answer is a 200 -- a view, ``not-converted``, ``not-found``, ``unavailable`` -- because
each is a fact about the tree, not a transport failure. ``invalid-request`` is a 400. Only a process
composed without the port answers 503, because then no answer exists at all. The route is GET-only
and never writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agents_remember.serving.media import (
    MediaSource,
    MediaTooLarge,
    RangeNotSatisfiable,
    UnsupportedMedia,
    media_answer,
    media_error_response,
    media_response,
)

__all__ = [
    "KNOWLEDGE_BYTES_ROUTE",
    "KNOWLEDGE_READER_ROUTE",
    "KnowledgeBytesPort",
    "KnowledgeReaderPort",
    "KnowledgeReaderQuery",
    "register_knowledge_bytes_route",
    "register_knowledge_reader_route",
]

KNOWLEDGE_READER_ROUTE = "/api/knowledge/reader/{view}"
KNOWLEDGE_BYTES_ROUTE = "/api/knowledge/bytes"


@dataclass(frozen=True)
class KnowledgeReaderQuery:
    """One reader question: the view, the repository and tree, and the view's own subject."""

    view: str
    repository_id: str
    commit: str | None = None
    path: str | None = None
    record_id: str | None = None
    census_id: str | None = None
    locator: str | None = None
    blob: str | None = None
    continuation: str | None = None


KnowledgeReaderPort = Callable[[KnowledgeReaderQuery], dict[str, Any]]

# The code reader's media answer: the exact tree's blob as a media source, or the same typed
# dictionary a reader answer uses (``absent``/``unavailable``/``unsupported-type``).
KnowledgeBytesPort = Callable[[KnowledgeReaderQuery], "MediaSource | dict[str, Any]"]

_UNWIRED: dict[str, Any] = {
    "status": "unavailable",
    "detail": (
        "no knowledge reader adapter is wired into this process, so no memory tree can be read; "
        "nothing is served rather than an empty view"
    ),
    "nextAction": "start the dashboard through its composition root, which supplies the reader",
}


def register_knowledge_reader_route(app: FastAPI, port: KnowledgeReaderPort | None) -> None:
    """Register the read-only reader route. Must be called BEFORE the greedy static mount."""

    @app.get(KNOWLEDGE_READER_ROUTE)
    def api_knowledge_reader(  # noqa: PLR0913 - one query parameter per addressable subject
        view: str,
        *,
        repo: str,
        commit: str | None = None,
        path: str | None = None,
        id: str | None = None,
        census: str | None = None,
        locator: str | None = None,
        blob: str | None = None,
        continuation: str | None = None,
    ) -> Response:
        if port is None:
            return JSONResponse(_UNWIRED, status_code=503)
        answer = port(
            KnowledgeReaderQuery(
                view=view,
                repository_id=repo,
                commit=commit,
                path=path,
                record_id=id,
                census_id=census,
                locator=locator,
                blob=blob,
                continuation=continuation,
            )
        )
        status = 400 if answer.get("state") == "invalid-request" else 200
        return JSONResponse(answer, status_code=status)


def register_knowledge_bytes_route(app: FastAPI, port: KnowledgeBytesPort | None) -> None:
    """Register the code reader's media route: bytes of a picture or video at the selected tree."""

    @app.get(KNOWLEDGE_BYTES_ROUTE)
    def api_knowledge_bytes(
        request: Request,
        repo: str,
        commit: str | None = None,
        path: str | None = None,
    ) -> Response:
        if port is None:
            return JSONResponse(_UNWIRED, status_code=503)
        try:
            answer = port(
                KnowledgeReaderQuery(view="code", repository_id=repo, commit=commit, path=path)
            )
        except (MediaTooLarge, UnsupportedMedia, RangeNotSatisfiable) as error:
            refusal = media_error_response(error)
            assert refusal is not None  # the caught family always maps
            return refusal
        if isinstance(answer, MediaSource):
            return media_answer(lambda: media_response(answer, request.headers.get("range")))
        status = 400 if answer.get("state") == "invalid-request" else 404
        return JSONResponse(answer, status_code=status)
