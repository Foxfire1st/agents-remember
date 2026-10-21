"""One app's intent summary, reusing only a native read with complete stable dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

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

#: A kept summary answer is served for at most this long. Rule 3 asks for a bound on reuse as well
#: as a bound on size: an answer kept through an unnoticed dependency drift would otherwise be
#: reusable for the life of the process.
DEFAULT_MAX_AGE_SECONDS = 900.0

#: Admission records are bounded too, so the bound itself cannot grow without limit.
DEFAULT_ADMISSIONS = 256


@dataclass(frozen=True)
class ReviewIntentSummaryPort:
    """Native selection inputs and the reader that reports its resolved dependencies.

    Both are asked one :data:`SummaryKey` (repository, master, leaf, comparison number); ``read``
    takes the observer after it.
    """

    inputs: Callable[[str, str, str, int | None], Inputs]
    read: Callable[[str, str, str, int | None, Observe], ReviewIntentSummaryResult]


class IntentSummaryCache:
    def __init__(
        self,
        port: ReviewIntentSummaryPort,
        cache: ChangesetCache,
        *,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        admissions: int = DEFAULT_ADMISSIONS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.port = port
        self.cache = cache
        self._max_age_seconds = max_age_seconds if max_age_seconds > 0 else DEFAULT_MAX_AGE_SECONDS
        self._admissions = admissions
        self._clock = clock
        # The window an answer belongs to is part of its cache key, and the clock alone decides it:
        # when the window rolls over the key changes by itself, so an answer can never be looked up
        # outside the window it was produced in. No admission record is consulted to decide that --
        # a bounded admission table that had forgotten an entry would otherwise mint the old window
        # again and serve an answer older than the bound (L177-R1-06). ``_kept`` stays as a bounded
        # record of the last windows admitted per question, for inspection only.
        self._kept: dict[SummaryKey, int] = {}

    def _window(self, key: SummaryKey) -> int:
        """The age window whose answer may be served for ``key``: the clock's, and nothing else."""

        window = int(self._clock() // self._max_age_seconds)
        self._remember(key, window)
        return window

    def _remember(self, key: SummaryKey, window: int) -> None:
        self._kept.pop(key, None)
        self._kept[key] = window
        while len(self._kept) > self._admissions:
            self._kept.pop(next(iter(self._kept)))

    def response(
        self, request: Request, repo: str, master: str, leaf: str, number: int | None = None
    ) -> Response:
        return self.cache.response(request, lambda: self._response(repo, master, leaf, number))

    def _response(self, repo: str, master: str, leaf: str, number: int | None = None) -> Response:
        key: SummaryKey = (repo, master, leaf, number)
        window = self._window(key)
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
            ("intent-summary", *key, window),
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
