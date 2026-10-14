from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.sse import ServerSentEvent
from pydantic import BaseModel

from agents_remember.observer import observer_root
from agents_remember.serving.build_info import ServingBuild
from agents_remember.serving.knowledge_reader import KnowledgeReaderPort
from agents_remember.serving.projector import ProjectionReplay, Projector
from agents_remember.serving.review import (
    KnowledgeReviewEntriesPort,
    KnowledgeReviewPort,
    ReviewSourceContentPort,
)
from agents_remember.serving.review_summary import ReviewIntentSummaryPort
from agents_remember.serving.review_trees import ReviewTreesPort
from agents_remember.serving.served_state import served_state_tail

# The serving app's shared logger, named after the public module so existing
# ``assertLogs("agents_remember.serving.app", ...)`` tests keep matching.
logger = logging.getLogger("agents_remember.serving.app")

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_app_common.py:50).
def _encode(data: BaseModel | dict[str, Any]) -> Any:  # pragma: no cover
    """JSON-ready payload: projection nodes dumped by alias (camelCase), markers as-is."""
    if isinstance(data, BaseModel):
        return data.model_dump(by_alias=True, exclude_none=True)
    return data


class _ProjectionBodyCache:
    """One-entry memo of the dumped projection body for the published projection instance.

    ``/api/state`` and the SSE boot snapshot serve the SAME ~1.3 MB
    projection dump, previously re-walked by ``model_dump`` per request/subscriber (measured
    13.7-16.5 ms per ``/api/state`` call). The projector swaps in a NEW projection instance
    every tick (``Projector._published``), so keying the memo by instance identity refreshes
    it every tick: serve freshness is exactly the former per-request dump semantics (ages as
    of the last tick), while every request/subscriber landing within one tick shares a single
    dump. Keying by the coarser ETag revision instead would freeze the volatile age fields
    for ETag-less full GETs and SSE reconnects until the next content change. The volatile
    tail is NOT cached: ``servingBuild`` is injected per serve, so
    callers must shallow-copy the returned dict and never mutate the memo itself. Holding the
    instance in the entry makes the identity compare ABA-safe, and tuple assignment is
    GIL-atomic, so no lock (the ``/api/state`` threadpool and the event loop can race
    harmlessly: a concurrent miss just recomputes the same content).
    """

    def __init__(self) -> None:
        self._entry: tuple[BaseModel, dict[str, Any]] | None = None

    def body(self, projection: BaseModel) -> dict[str, Any]:
        """``projection``'s wire dump (cached per instance; read-only for callers)."""
        entry = self._entry
        if entry is None or entry[0] is not projection:
            entry = (projection, projection.model_dump(by_alias=True, exclude_none=True))
            self._entry = entry
        return entry[1]


_projection_body_cache = _ProjectionBodyCache()


def _if_none_match_matches(header: str | None, revision: str) -> bool:
    """RFC 7232 weak comparison of an ``If-None-Match`` header against our revision.

    The revision is the projector's opaque content fingerprint (boot nonce + content
    sequence); the served ETag is its weak form ``W/"<revision>"``. Weak comparison strips
    ``W/`` prefixes, so any listed entity-tag whose opaque value equals the revision
    matches; ``*`` matches any current representation.
    """
    if header is None:
        return False
    for candidate in header.split(","):
        tag = candidate.strip()
        if tag == "*":
            return True
        if tag.startswith(("W/", "w/")):
            tag = tag[2:]
        if tag.strip('"') == revision:
            return True
    return False


async def stream_events(
    projector: Projector,
    *,
    build: ServingBuild | None = None,
) -> AsyncGenerator[ServerSentEvent]:
    """The SSE event sequence for one atomic projector subscription.

    Module-level (not a route closure) so it is unit-testable without an HTTP client.
    ``build`` (the boot-time serving stamp) rides the snapshot as
    ``servingBuild`` so the cockpit can render which process/commit is answering.

    The tail rides the ``snapshot`` ONLY: a ``delta`` is one projection node, not a state
    body, so there is nothing there for a whole-workspace stamp to be a field of. That
    asymmetry is why the snapshot is a :class:`ServedWorkspaceProjection` and a delta is
    just an encoded node.
    """
    async with contextlib.aclosing(projector.subscribe()) as subscription:
        async for seq, delta in subscription:
            if delta.event == "snapshot" and isinstance(delta.data, BaseModel):
                # The boot snapshot is the same projection dump /api/state serves -- reuse the
                # per-instance memo (copy before the volatile injection below) instead of
                # re-dumping ~1.3 MB per subscriber.
                payload = dict(_projection_body_cache.body(delta.data))
            else:
                payload = _encode(delta.data)
            if delta.event == "snapshot":
                payload.update(served_state_tail(build=build))
            yield ServerSentEvent(data=payload, event=delta.event, id=str(seq), retry=2000)


@dataclass(frozen=True)
class _ResolvedLiveInputs:
    provider_state: bool
    landing_state: bool
    change_watch: bool


@dataclass(frozen=True)
class LiveProjectionInputs:
    """Which live world-inputs this app drives; ``None`` means "infer from the replay seam".

    One value because it is one question -- is this app attached to a moving world, or replaying a
    recorded one? Sim replay must disable all three together (the feeder writes only *inside* a
    tick, so a change-gated loop would never wake), and answering it three times independently is
    how a replay app ends up half-live.
    """

    provider_state: bool | None = None
    landing_state: bool | None = None
    change_watch: bool | None = None

    def resolved(self, replay: ProjectionReplay) -> _ResolvedLiveInputs:
        """Settle each unset toggle against the replay seam: a feeder means sim, so off."""

        live = replay.before_tick is None
        return _ResolvedLiveInputs(
            provider_state=live if self.provider_state is None else self.provider_state,
            landing_state=live if self.landing_state is None else self.landing_state,
            change_watch=live if self.change_watch is None else self.change_watch,
        )


@dataclass(frozen=True)
class ServingCollaborators:
    """Long-lived collaborators supplied instead of letting :func:`create_app` construct them.

    They are offered as one value because substituting them is one decision of the composition
    root. ``None`` on a port refuses that port's route by name.
    """

    knowledge_review: KnowledgeReviewPort | None = None
    """The application-tier reviewer adapter, supplied by the composition root.

    ``serving`` ranks below ``application`` in ``layers.toml``, so the reviewer route cannot import
    the read/diff/view operations the adapter composes; it takes this port instead. Production
    wires it in
    :mod:`agents_remember.cli.dashboard`; a process that omits it refuses the review route by name
    rather than serving an empty surface, because an empty pane and an unreachable adapter are
    different facts and only one of them is true.
    """

    knowledge_review_entries: KnowledgeReviewEntriesPort | None = None
    """The same adapter's entry half: the subjects the resolved pair can be reviewed on.

    It is one port beside the first rather than a mode of it, because the two answer different
    questions from one resolution, and the entry route is the only one a task view can call before
    it knows a subject. Omitting it refuses that route by name for the same reason the review route
    is refused: an empty entry list would say "nothing is reviewable here", which is a different
    fact from "this process cannot answer".
    """

    review_source_content: ReviewSourceContentPort | None = None
    """The application-tier owner of one inventory entry's content at its two bound code trees.

    A third port rather than a field on the review payload: the inventory is the whole task's change
    set, and the route that opens one entry reads two Git objects the payload never carried. Omitting
    it refuses that route by name -- "this process cannot read the entry" and "this entry has no
    content" are different facts, and a browser served an empty file for the second would be reading
    a document this repository does not hold.
    """

    review_intent_summary: ReviewIntentSummaryPort | None = None
    """The same adapter's changed-intent summary: the counts the task entry shows before opening.

    A fourth port because it answers a fourth question from the same resolution -- how many
    statements each side of the comparison alone holds -- and the task entry asks it without
    loading the subject catalogue. Omitting it refuses that route by name, because "this process
    cannot count" is not "nothing changed".
    """

    review_trees: ReviewTreesPort | None = None
    """The same adapter's tree view (MIK-R25): the four trees, knowledge diff, currentness, worklist.

    A fifth port because it answers for a converted leaf what the subject review does not: the
    Git diff of the memory trees and the worklist view. Omitting it refuses that route by name.
    """

    review_trees_shutdown: Callable[[], None] | None = None
    """Stop and reap the tree route's transient child computations at serving shutdown."""

    knowledge_reader: KnowledgeReaderPort | None = None
    """The path-based knowledge reader (MIK-R29): read-only views of any converted memory tree.

    It needs no task, so it is its own port rather than a mode of the review adapter. Omitting it
    refuses the reader route by name.
    """

    extra_api_routes: Callable[[Any], None] | None = None
    """The composition root's routes, registered before the final static mount.

    This keeps optional dashboard adapters above ``serving`` in the import graph while allowing
    the app factory to preserve route precedence over its catch-all asset mount.
    """


INFERRED_LIVE_INPUTS = LiveProjectionInputs()


@dataclass(frozen=True)
class _ServingRuntime:
    """The long-lived collaborators one serving app is built around.

    Both the route handlers and the background loops read from exactly this set, which is what
    lets each of them be an ordinary module-level function instead of a closure over
    :func:`create_app`'s locals -- and what lets one move to its own package without rewriting.
    """

    config: McpRuntimeConfig
    projector: Projector
    # One serving clock; the replay seam substitutes it.
    clock: Callable[[], datetime]
    build: ServingBuild
    interval: float
    review_trees_shutdown: Callable[[], None] | None = None

    @property
    def observer_root(self) -> Path:
        """The observer store root every durable control-plane store is opened under."""

        return observer_root(self.config)
