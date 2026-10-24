from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.observer.projection import TaskSummary
from agents_remember.serving._app_common import (
    _projection_body_cache,
    _ServingRuntime,
    stream_events,
)
from agents_remember.serving.actions import (
    ActionEvaluationContext,
    ActionOutcome,
    ActionRequest,
    DismissalIntent,
    evaluate_action,
)
from agents_remember.serving.change_watcher import MoveNoticeWatch, move_notice_watch
from agents_remember.serving.conditional_read import if_none_match_matches
from agents_remember.serving.direct_execution_grants import (
    DeveloperGrantRequest,
    developer_grant_tool,
)
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.explainer_watcher import (
    NOTICE_EVENT,
    LeafSelection,
    notice_event,
)
from agents_remember.serving.response_contract import (
    ACTION_RESPONSES,
    ActionAccepted,
    HttpDetailRefusal,
    StreamReadyMarker,
)
from agents_remember.serving.review_memory_watch import (
    ReviewMemoryRootPort,
    multiplex_review_memory,
)
from agents_remember.serving.served_state import ServedWorkspaceProjection, served_state_tail

logger = logging.getLogger(__name__)


def _checkout_binding(runtime: _ServingRuntime) -> dict[str, object] | None:
    """The disposable-root binding the process carries, as the state serves it (MIK-R83 rule 7)."""

    binding = runtime.config.checkout_binding
    return None if binding is None else binding.as_payload()


if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


def _state_response(runtime: _ServingRuntime, if_none_match: str | None) -> Response:
    # The change gate: the ETag is the projector's content revision, which only
    # advances when the stable projection form changes (volatile ages excluded -- delta.py).
    # An If-None-Match poll of an unchanged projection therefore costs a header exchange
    # instead of a ~780 KB dump+parse; when content DID change, the full fresh dump serves
    # exactly as before. `Cache-Control: no-cache` keeps any cache honest (always revalidate).
    seq, snapshot = runtime.projector.current()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="projection not ready")
    revision = runtime.projector.revision(seq)
    headers = {"ETag": f'W/"{revision}"', "Cache-Control": "no-cache"}
    if if_none_match_matches(if_none_match, revision):
        return Response(status_code=304, headers=headers)
    # The dump rides the per-instance memo; only the tail (the build stamp) is added per
    # request, on a copy. This body is therefore ASSEMBLED rather than dumped from one
    # model. What it assembles to is nonetheless declared: it is a
    # ``served_state.ServedWorkspaceProjection``, and the conformance suite validates the
    # real route's output against that model. Validating here instead would re-parse ~1.3 MB
    # per request and hand back exactly what the memo exists to save.
    body = dict(_projection_body_cache.body(snapshot))
    body.update(
        served_state_tail(
            build=runtime.build,
            watcher_health=runtime.projector.watcher_health(),
            checkout_binding=_checkout_binding(runtime),
        )
    )
    return JSONResponse(content=body, headers=headers)


def _task_document_response(
    runtime: _ServingRuntime, path: str, if_none_match: str | None = None
) -> Response:
    _, snapshot = runtime.projector.current()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="projection not ready")
    doc = runtime.task_bodies.read(
        runtime.config.coordination_root,
        path,
        now=runtime.clock(),
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="task document not found")
    headers = {"ETag": f'W/"{doc.revision}"', "Cache-Control": "no-cache"}
    if doc.generation.current() and if_none_match_matches(if_none_match, doc.revision):
        return Response(status_code=304, headers=headers)
    return Response(content=doc.body, media_type="application/json", headers=headers)


async def _moved_events(
    identity: tuple[str, str, str], watch: MoveNoticeWatch
) -> AsyncGenerator[ServerSentEvent]:
    """The review page's moved notice: one comparison when it opens, then every publish.

    The comparison runs off the event loop (it captures Git trees), and a failure is logged rather
    than sent: the page keeps its stream and shows no notice it cannot stand behind (rule 7).
    """

    selection = LeafSelection(*identity)
    check = asyncio.create_task(asyncio.to_thread(watch.page_opened, selection))
    check.add_done_callback(_note_check_failure)
    try:
        async with contextlib.aclosing(watch.channel.listen(selection)) as stream:
            async for notice in stream:
                yield ServerSentEvent(event=NOTICE_EVENT, data=notice_event(selection, notice))
    finally:
        check.cancel()


async def _review_stream(
    ordinary: AsyncGenerator[ServerSentEvent],
    identity: tuple[str, str, str] | None,
    root: ReviewMemoryRootPort | None,
    watch: MoveNoticeWatch | None,
) -> AsyncGenerator[ServerSentEvent]:
    """One review page's stream: the projection, the memory invalidations, and the moved notice."""

    async with contextlib.aclosing(multiplex_review_memory(ordinary, identity, root)) as events:
        if identity is None or watch is None:
            async for event in events:
                yield event
            return
        async with (
            contextlib.aclosing(_moved_events(identity, watch)) as moved,
            contextlib.aclosing(_merged_stream(events, moved)) as streamed,
        ):
            async for event in streamed:
                yield event


def _note_check_failure(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("the moved watcher could not compare this review page: %s", error)


async def _next_event(source: AsyncGenerator[ServerSentEvent]) -> ServerSentEvent:
    return await anext(source)


async def _merged_stream(
    source: AsyncGenerator[ServerSentEvent], extra: AsyncGenerator[ServerSentEvent]
) -> AsyncGenerator[ServerSentEvent]:
    """Two event sources as one; the page's own stream ends the connection, notices never do."""

    streams = {asyncio.create_task(_next_event(one)): one for one in (source, extra)}
    try:
        while streams:
            done, _ = await asyncio.wait(streams, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                origin = streams.pop(task)
                try:
                    event = task.result()
                except StopAsyncIteration:
                    if origin is extra:
                        continue
                    return
                yield event
                streams[asyncio.create_task(_next_event(origin))] = origin
    finally:
        for task in streams:
            task.cancel()
        await asyncio.gather(*streams, return_exceptions=True)


def _register_projection_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """The read side of the cockpit: the projection once, tailed, and the raw event river."""

    # One watcher per coordination root: it remembers which pages it compared just now, and
    # publishes to the process's one notice channel that review-scoped streams read (rule 7). The
    # turn-end loop reads this same instance (``move_notice_watch(config).run()``).
    move_watch = (
        None
        if runtime.explainer_application is None
        else move_notice_watch(runtime.config, runtime.explainer_application.watcher)
    )

    # The 304 branch is why this cannot become a model-returning handler: it answers with an
    # ETag and NO body at all, which no ``response_model`` can express. The declaration names
    # the 200 shape; ``mcp/tests/test_served_state_conformance.py`` holds both branches shut.
    @app.get(
        "/api/state",
        response_model=ServedWorkspaceProjection,
        responses={
            304: {"description": "Content revision unchanged; ETag only, no body"},
            503: {"model": HttpDetailRefusal, "description": "Projection not ready"},
        },
    )
    def api_state(
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        return _state_response(runtime, if_none_match)

    @app.get(
        "/api/task-document",
        response_model=TaskSummary,
        responses={
            304: {"description": "Content revision unchanged; ETag only, no body"},
            404: {"model": HttpDetailRefusal, "description": "No such task document"},
            503: {"model": HttpDetailRefusal, "description": "Projection not ready"},
        },
    )
    def api_task_document(
        path: Annotated[str, Query()], if_none_match: Annotated[str | None, Header()] = None
    ) -> Response:
        return _task_document_response(runtime, path, if_none_match)

    # An SSE route: the declared model is what a ``snapshot`` frame's ``data`` carries. A
    # ``delta`` frame is one bare projection node -- that asymmetry is the contract, and the
    # served-state conformance suite pins it on the real generator.
    @app.get(
        "/api/stream",
        response_class=EventSourceResponse,
        response_model=ServedWorkspaceProjection,
        responses={
            200: {
                "content": {"text/event-stream": {}},
                "description": "One `snapshot` frame, then per-entity `delta` frames",
            }
        },
    )
    async def api_stream(
        reviewRepo: str | None = None,
        reviewMaster: str | None = None,
        reviewLeaf: str | None = None,
    ) -> AsyncIterator[ServerSentEvent]:
        supplied = (reviewRepo, reviewMaster, reviewLeaf)
        identity = (
            None
            if all(value is None for value in supplied)
            else (
                reviewRepo or "",
                reviewMaster or "",
                reviewLeaf or "",
            )
        )
        ordinary = stream_events(
            runtime.projector,
            build=runtime.build,
            watcher_health=runtime.projector.watcher_health(),
            checkout_binding=_checkout_binding(runtime),
        )
        # ``aclose`` the inner stream explicitly: ``async for`` does not close its iterator, and
        # a disconnected page must release the projector's subscription at once (MIK-R81).
        async with contextlib.aclosing(
            _review_stream(ordinary, identity, runtime.review_memory_root, move_watch)
        ) as streamed:
            async for event in streamed:
                yield event

    # The raw river: every ``event`` frame is a verbatim observer JSONL record replayed from
    # disk, so its schema belongs to the observer. What THIS route mints is the ready marker,
    # which is therefore what it declares.
    @app.get(
        "/api/events",
        response_class=EventSourceResponse,
        response_model=StreamReadyMarker,
        responses={
            200: {
                "content": {"text/event-stream": {}},
                "description": "Verbatim `event` frames, then one `ready` marker",
            }
        },
    )
    async def api_events(
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> AsyncIterator[ServerSentEvent]:
        async for event in stream_raw_events(
            runtime.config,
            last_event_id=last_event_id,
            interval=runtime.interval,
            ledger=runtime.background_ledger,
        ):
            yield event

    # MIK-R81 rule 1: the always-on background account. Read-only; a failure
    # here never touches anything the dashboard serves (the route builds no
    # projection and writes nothing).
    @app.get("/api/diagnostics/background")
    def api_diagnostics_background() -> JSONResponse:
        ledger = runtime.background_ledger
        if ledger is None:
            raise HTTPException(status_code=503, detail="background accounting not ready")
        return JSONResponse(content=ledger.snapshot())


# --- action routes ---------------------------------------------------------------------------------


def _dismissal_response(
    config: McpRuntimeConfig, outcome: ActionOutcome, intent: DismissalIntent
) -> Response:
    """Acknowledge only a successful durable write; a failure leaves the item visible."""
    try:
        AttentionDismissalStore(observer_root(config)).dismiss(
            AttentionDismissalRecord(
                itemId=intent.item_id,
                dismissedAt=intent.dismissed_at,
                kind=intent.kind,
                actionableFingerprint=intent.actionable_fingerprint,
            )
        )
    except OSError:
        return JSONResponse(
            content={
                "status": "dismissal-not-held",
                "detail": "Dismissal did not hold; try again.",
            },
            status_code=503,
        )
    return JSONResponse(content=outcome.body, status_code=outcome.status_code)


def _action_response(runtime: _ServingRuntime, action: str, request: ActionRequest) -> Response:
    """Route one action: acknowledge it or apply its dismissal."""

    _, snapshot = runtime.projector.current()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="projection not ready")
    outcome = evaluate_action(
        snapshot,
        action,
        request.target,
        ActionEvaluationContext(
            actor=request.actor,
            now=now_iso(),
            item_id=request.itemId,
            kind=request.kind,
        ),
    )
    if outcome.dismissal is not None:
        return _dismissal_response(runtime.config, outcome, outcome.dismissal)
    return JSONResponse(content=outcome.body, status_code=outcome.status_code)


def _register_action_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """The write side the developer drives: actions and the direct-execution grant."""

    # ``status_code=202`` because that is the status this route actually answers with: an
    # accepted intent is recorded, not applied. Left implicit it declared its success shape at
    # 200 -- a pair no request can ever produce, and therefore one no conformance check could
    # ever drive. The handler returns a ``Response`` it built itself, so this is a declaration
    # only and moves no bytes.
    @app.post(
        "/api/actions/{action}",
        response_model=ActionAccepted,
        status_code=202,
        responses=ACTION_RESPONSES,
    )
    def api_action(action: str, request: ActionRequest) -> Response:
        return _action_response(runtime, action, request)

    # The developer's direct-execution grant control (MIK-R94 rule 2): giving and
    # taking back a leaf's grant from the dashboard. The handler returns a
    # ``Response`` it built itself, so this is a declaration only and moves no
    # bytes; the payload mirrors the architect's ``direct_execution_grant``
    # function shape (``ok``/``state``/``changed``/``grant``).
    @app.post(
        "/api/direct-execution-grant",
        status_code=200,
    )
    def api_direct_execution_grant(request: DeveloperGrantRequest) -> Response:
        return JSONResponse(
            content=developer_grant_tool(
                runtime.config,
                request.repository,
                request.path,
                request.action,
            )
        )
