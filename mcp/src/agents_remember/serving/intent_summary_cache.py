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
# One summary question: repository, master, leaf, and the recorded comparison number it names, or
# ``None`` for the leaf's own resolution (MIK-R89 v2 rule 10). Two numbers are two answers.
type SummaryKey = tuple[str, str, str, int | None]


@dataclass(frozen=True)
class ReviewIntentSummaryPort:
    """Native selection inputs and the reader that reports its resolved dependencies.

    Both are asked one :data:`SummaryKey` (repository, master, leaf, comparison number); ``read``
    takes the observer after it.
    """

    inputs: Callable[[str, str, str, int | None], Inputs]
    read: Callable[[str, str, str, int | None, Observe], ReviewIntentSummaryResult]


class IntentSummaryCache:
    """Each summary question's kept answer, served again while every input it read is identical.

    The cached entry is named by the question alone (:data:`SummaryKey`); whether it may be served
    is decided by its inputs: the native selection inputs and the reads the reader reported, each
    re-observed and compared with the stamp it had when the answer was produced. No age or clock
    enters either. The cache's capacity bounds how many answers are kept; an evicted one is
    produced again.
    """

    def __init__(self, port: ReviewIntentSummaryPort, cache: ChangesetCache) -> None:
        self.port = port
        self.cache = cache

    def response(
        self, request: Request, repo: str, master: str, leaf: str, number: int | None = None
    ) -> Response:
        return self.cache.response(request, lambda: self._response(repo, master, leaf, number))

    def _response(self, repo: str, master: str, leaf: str, number: int | None = None) -> Response:
        key: SummaryKey = (repo, master, leaf, number)
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
