"""The revision set one knowledge dataset holds, read through the shipped view API.

Two operations need the same question answered -- "which invariant revisions does this *file* hold?"
-- and they need it answered the same way, because one of them decides whether authored work may be
deleted. The bootstrap run asks it of the repository's published dataset to name what is still owed;
the staging cleanup owner asks it of a staged candidate to decide whether that candidate is the
published truth, or holds authored rows nobody else has. A second implementation of the walk would
let those two answers drift, and the drift would show up as destroyed work rather than as a test
failure.

The answer is deliberately four-valued (see :class:`DatasetContents`). A walk that finished, or a
location holding no dataset file at all, *measured* the set, so a revision not in it is absent: the
one is a completed walk and the other a location with nothing written to it, and neither is an unread
file. A walk that stopped -- a page cap, a refusal, a page that could not be rendered -- and a file
that cannot be read as a dataset of this code did **not** measure the whole set, so absence was not
established for anything they had not reached, and the caller is told which of the two it was instead
of receiving a shorter set it would read as complete. ``revisions`` is a measurement in every case: a
revision the read actually saw is present whatever the read did afterwards.

Nothing here opens a database directly, computes a digest, or decides what a record means. The rows
come from :func:`~agents_remember.application.knowledge_views.read_knowledge_view` at the dataset's
own snapshot, which is the same surface the reviewer and a planning read use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.application.knowledge_views import open_view_context, read_knowledge_view
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.view import (
    MAX_VIEW_ROWS,
    InvariantView,
    ViewPayload,
    ViewRefusal,
    ViewRequest,
)

__all__ = [
    "DATASET_CONTENTS_MAX_PAGES",
    "DatasetContents",
    "dataset_revisions",
]

# How many pages one walk takes before it names itself incomplete. A page carries at most the view's
# own ``MAX_VIEW_ROWS``, so the cap is generous for a real catalogue and finite for an implausible
# one; reaching it is reported as a limitation and never as an absence.
DATASET_CONTENTS_MAX_PAGES = 32

ContentsState = Literal["complete", "absent", "partial", "unavailable"]


@dataclass(frozen=True)
class DatasetContents:
    """What a walk of one dataset's invariant view established.

    The four states are four different facts and are never merged:

    * ``complete`` -- the file is there and the walk reached its end inside the page cap, so an
      absent revision is a **measured** absence;
    * ``absent`` -- there is no dataset file at the location at all, so the location holds no
      revision and that, too, is measured: nothing was written there, which is not the same fact as
      a file that could not be read;
    * ``partial`` -- the walk stopped early or a page refused, so absence was **not** established
      for anything it had not reached;
    * ``unavailable`` -- a file is there and could not be read as a dataset of this code, or no
      namespace was established to address it, so nothing about its contents was established.

    ``pages`` is how many pages were read, so a reader can see that ``complete`` was established by
    a walk that actually ran rather than by a walk that never started.
    """

    state: ContentsState
    detail: str
    revisions: frozenset[str]
    pages: int

    @property
    def absence_established(self) -> bool:
        """Whether NOT finding a revision in this read is a measurement rather than an unknown.

        True only for the two states that finished their work: a completed walk, and a location with
        no dataset file at all. A partial walk and an unreadable file both leave absence unknown, and
        a caller that read them as absent would turn a bounded read into manufactured work.
        """

        return self.state in ("complete", "absent")

    @property
    def measured_empty(self) -> bool:
        """Whether this read established that the location holds no invariant revision at all.

        This is the one case where "no revisions" is a measurement rather than an unread location,
        and it is what lets a cleanup owner remove a candidate that holds no authored row without
        guessing.
        """

        return self.absence_established and not self.revisions


def dataset_revisions(dataset: Path, repository_id: str | None) -> DatasetContents:
    """Every invariant revision the dataset at ``dataset`` holds, or why that was not established.

    ``repository_id`` is the namespace the dataset is bound to, which the caller must have read from a
    dataset rather than assumed: a view read refuses a namespace its file does not carry, and that
    refusal is reported here as an unreadable dataset rather than as an empty one.
    """

    if not dataset.is_file():
        return DatasetContents(
            state="absent",
            detail=(
                f"no dataset file is present at {dataset.as_posix()}, so the location holds no "
                "invariant revision at all; that is a measured absence rather than an unread file"
            ),
            revisions=frozenset(),
            pages=0,
        )
    if repository_id is None:
        return DatasetContents(
            state="unavailable",
            detail=(
                f"a dataset file is present at {dataset.as_posix()} but no namespace was "
                "established to address it, so nothing about its contents was measured"
            ),
            revisions=frozenset(),
            pages=0,
        )
    context = open_view_context(dataset, repository_id)
    if isinstance(context, KnowledgeRefusal):
        return _unreadable(dataset, context.detail)
    found: set[str] = set()
    continuation = None
    pages = 0
    while pages < DATASET_CONTENTS_MAX_PAGES:
        result = read_knowledge_view(
            dataset,
            context,
            ViewRequest(
                view="invariant",
                repository_id=repository_id,
                limit=MAX_VIEW_ROWS,
                continuation=continuation,
            ),
        )
        if result.state == "refused" or result.payload is None:
            return DatasetContents(
                state="partial",
                detail=(
                    f"the invariant view of {dataset.as_posix()} refused page {pages + 1} "
                    f"({_code_of(result.refusal)}), so absence was not established for any revision "
                    "this walk had not reached"
                ),
                revisions=frozenset(found),
                pages=pages,
            )
        payload = result.payload
        pages += 1
        found.update(_page_revisions(payload))
        continuation = payload.continuation
        if continuation is None:
            return DatasetContents(
                state="complete",
                detail=(
                    f"the invariant view of {dataset.as_posix()} was walked to its end in {pages} "
                    f"page(s) and holds {len(found)} invariant revision(s)"
                ),
                revisions=frozenset(found),
                pages=pages,
            )
    return DatasetContents(
        state="partial",
        detail=(
            f"the invariant view of {dataset.as_posix()} still reported rows remaining after "
            f"{DATASET_CONTENTS_MAX_PAGES} pages, so the walk was stopped and absence is not "
            "established for the revisions it had not reached"
        ),
        revisions=frozenset(found),
        pages=pages,
    )


def _unreadable(dataset: Path, detail: str) -> DatasetContents:
    return DatasetContents(
        state="unavailable",
        detail=f"the dataset at {dataset.as_posix()} could not be opened for a view read ({detail})",
        revisions=frozenset(),
        pages=0,
    )


def _code_of(refusal: ViewRefusal | None) -> str:
    if refusal is None:  # pragma: no cover - a refused result always carries its refusal
        return "the read returned neither a payload nor a refusal"
    return refusal.code


def _page_revisions(payload: ViewPayload) -> set[str]:
    """Every invariant revision one page's rows are about, read from the rows' own subjects.

    A row whose subject carries no revision is not counted: the subject is what the store recorded, and
    an empty revision is a fact about that row rather than a revision id this read may invent.
    """

    if not isinstance(payload, InvariantView):
        return set()
    return {row.subject.revision_id for row in payload.rows if row.subject.revision_id}
