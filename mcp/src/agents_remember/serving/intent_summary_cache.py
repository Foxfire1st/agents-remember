"""One app's intent summary, reusing only a native read with complete stable dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult
from agents_remember.serving.changeset_cache import (
    ChangesetCache,
    ObjectStoreRead,
    ReadInput,
    RepositoryRead,
    Retention,
    cached_read,
    observe_inputs,
)

type Inputs = tuple[ReadInput, ...]
type Observe = Callable[[Inputs], None]


@dataclass(frozen=True)
class ReviewIntentSummaryPort:
    """Native selection inputs and the reader that reports its resolved dependencies."""

    inputs: Callable[[str, str, str], Inputs]
    read: Callable[[str, str, str, Observe], ReviewIntentSummaryResult]


class IntentSummaryCache:
    def __init__(self, port: ReviewIntentSummaryPort, cache: ChangesetCache) -> None:
        self.port = port
        self.cache = cache

    def response(self, request: Request, repo: str, master: str, leaf: str) -> Response:
        return self.cache.response(request, lambda: self._response(repo, master, leaf))

    def _response(self, repo: str, master: str, leaf: str) -> Response:
        key = (repo, master, leaf)
        initial = self.port.inputs(*key)
        actual: Inputs = ()

        def observe(sources: Inputs) -> None:
            nonlocal actual
            actual = sources
            observe_inputs(actual)

        def produce() -> ReviewIntentSummaryResult:
            nonlocal actual
            actual = ()
            result = self.port.read(*key, observe)
            if result.state == "unavailable":
                actual = (
                    *actual,
                    *(
                        ObjectStoreRead(source.root)
                        for source in initial
                        if isinstance(source, RepositoryRead)
                    ),
                )
                observe_inputs(actual)
            return result

        result = cached_read(
            ("intent-summary", *key),
            initial,
            produce,
            # Repositories were already consumed by resolution. A newly discovered
            # repository requires another complete read. Index bytes are observed by
            # the callback before counting, so normal cold reads count only once.
            retention=Retention(
                keep=lambda _: all(
                    not isinstance(source, RepositoryRead) or source in initial for source in actual
                ),
                # A refused summary may settle while Git cannot describe the working
                # view; a counted one may not (L174-R1-04).
                unknown=lambda result: result.state == "unavailable",
            ),
            additional_inputs=lambda: actual,
        )
        return JSONResponse(result.model_dump(mode="json", exclude_none=True))

    @property
    def retained_inputs(self) -> int:
        return sum(key[0] == "intent-summary" for key in self.cache._values)
