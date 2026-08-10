"""Registered active conversation routes.

Two production wires behind the shared composition: the authorized native-hydrated
page and the resumable SSE event stream. Every wire resolves the caller
through the shared authorization dependency, compares ``expectedBridgeEpoch``
against the live submission authority, and maps every typed refusal to the
serving status idiom — pre-stream as typed HTTP errors, established-stream
failures as one typed ``gap`` plus close. Raw 500s are never a routine
refusal path.

The SSE channel emits explicit wire frames over ``StreamingResponse`` (not
generator routes): pre-stream validation must complete before any header is
committed, and the generator-route pattern in this FastAPI version only
encodes events after the stream starts, which would make pre-stream typed
errors impossible.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from agents_remember.errors import (
    AuthorityError,
    ConversationCompositionError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.models.conversations.cursors import (
    ActiveEventCursor,
    ActivePageCursor,
)
from agents_remember.models.conversations.history import (
    ConversationPage,
)
from agents_remember.models.conversations.stream_events import (
    ConversationEventEnvelope,
)
from agents_remember.serving.conversation.active.cursor import (
    ConversationCursorError,
    CursorConflictError,
    CursorInvalidError,
)
from agents_remember.serving.conversation.active.factories import (
    SessionResolutionError,
)
from agents_remember.serving.conversation.active.projector import CLOSE_SENTINEL
from agents_remember.serving.conversation.active.service import (
    ActiveSubscription,
    active_conversation_service,
)
from agents_remember.serving.conversation.dependencies import (
    get_conversation_runtime,
    resolve_conversation_authorization,
)
from agents_remember.serving.conversation.response_contract import (
    CONVERSATION_RESPONSES,
    AgentHistoryHydrated,
)

router = APIRouter(
    prefix="/api/terminal/{ar_session_id}/conversation",
    tags=["structured-conversation-active"],
)

SSE_MEDIA_TYPE = "text/event-stream"
SSE_RETRY_MS = 2000


def _error(status_code: int, slug: str, detail: str, **extra: object) -> JSONResponse:
    return JSONResponse(
        content={"status": slug, "detail": detail, **extra},
        status_code=status_code,
    )


def _map_typed_error(error: Exception) -> JSONResponse:
    if isinstance(error, HarnessBridgeEpochMismatchError):
        return _error(
            409,
            "bridge-epoch-mismatch",
            str(error),
            expectedBridgeEpoch=error.expected,
            actualBridgeEpoch=error.actual,
        )
    if isinstance(error, AuthorityError):
        return _error(403, "authorization-failed", str(error))
    if isinstance(error, ConversationCompositionError):
        return _error(503, "composition-unavailable", str(error))
    if isinstance(error, ConversationCursorError):
        content: dict[str, object] = {}
        if error.reason is not None:
            content["reason"] = error.reason
        return _error(error.http_status, error.status_slug, str(error), **content)
    if isinstance(error, SessionResolutionError):
        return _error(error.http_status, error.status_slug, str(error))
    if isinstance(error, HarnessControlError):
        return _error(503, "control-unavailable", str(error))
    raise error


def _parse_page_cursor(raw: str | None) -> ActivePageCursor | None:
    if raw is None:
        return None
    try:
        return ActivePageCursor(raw)
    except ValueError as exc:
        raise CursorInvalidError("before cursor is not an active page cursor") from exc


def _resume_cursor(
    after: str | None,
    last_event_id: str | None,
) -> ActiveEventCursor:
    if after is not None and last_event_id is not None and after != last_event_id:
        raise CursorConflictError("after and Last-Event-ID name different event cursors")
    raw = after if after is not None else last_event_id
    if raw is None:
        raise CursorInvalidError("an active event resume cursor is required")
    try:
        return ActiveEventCursor(raw)
    except ValueError as exc:
        raise CursorInvalidError("resume value is not an active event cursor") from exc


@router.get("", response_model=ConversationPage, responses=CONVERSATION_RESPONSES)
async def conversation_page(
    ar_session_id: str,
    expected_bridge_epoch: Annotated[str, Query(alias="expectedBridgeEpoch", min_length=1)],
    request: Request,
    before: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> JSONResponse:
    """The authorized native-hydrated page with an atomic event cursor."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        page = await active_conversation_service(runtime).page(
            authorization,
            ar_session_id,
            expected_bridge_epoch=expected_bridge_epoch,
            before=_parse_page_cursor(before),
            limit=limit,
        )
    except (
        AuthorityError,
        ConversationCompositionError,
        HarnessBridgeEpochMismatchError,
        HarnessControlError,
        ConversationCursorError,
        SessionResolutionError,
    ) as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=page.model_dump(mode="json", by_alias=True, exclude_none=True))


# A typed child failure is a SUCCESSFUL local outcome here, so the failure vocabulary lives
# inside the 200 body's ``status``; only parent-bridge refusals reach ``responses``.
@router.post(
    "/agents/{agent_id}/history",
    response_model=AgentHistoryHydrated,
    responses=CONVERSATION_RESPONSES,
)
async def hydrate_agent_history(
    ar_session_id: str,
    agent_id: Annotated[str, Path(min_length=1)],
    expected_bridge_epoch: Annotated[str, Query(alias="expectedBridgeEpoch", min_length=1)],
    request: Request,
) -> JSONResponse:
    """Backfill one selected child; typed child failures remain successful local outcomes."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        result = await active_conversation_service(runtime).hydrate_agent_history(
            authorization,
            ar_session_id,
            expected_bridge_epoch=expected_bridge_epoch,
            agent_id=agent_id,
        )
    except (
        AuthorityError,
        ConversationCompositionError,
        HarnessBridgeEpochMismatchError,
        HarnessControlError,
        ConversationCursorError,
        SessionResolutionError,
    ) as exc:
        return _map_typed_error(exc)
    return JSONResponse(
        content={
            "status": result.status,
            "agentId": result.thread_id,
            **({"detail": result.detail} if result.detail is not None else {}),
            **({"code": result.code} if result.code is not None else {}),
        }
    )


# The SSE wire: every failure is typed PRE-stream (that is why this returns an explicit
# ``StreamingResponse`` instead of being a generator route), and each frame's ``data`` is one
# ``ConversationEventEnvelope`` -- which is what the declaration names.
@router.get(
    "/events",
    response_model=ConversationEventEnvelope,
    responses={
        **CONVERSATION_RESPONSES,
        200: {
            "content": {"text/event-stream": {}},
            "description": "Resumable `conversation` frames, one envelope per frame",
        },
    },
)
async def conversation_events(
    ar_session_id: str,
    expected_bridge_epoch: Annotated[str, Query(alias="expectedBridgeEpoch", min_length=1)],
    request: Request,
    after: Annotated[str | None, Query()] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> Response:
    """The resumable SSE event stream; all failures are typed pre-stream."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        cursor = _resume_cursor(after, last_event_id)
        subscription = await active_conversation_service(runtime).subscribe(
            authorization,
            ar_session_id,
            expected_bridge_epoch=expected_bridge_epoch,
            after=cursor,
        )
    except (
        AuthorityError,
        ConversationCompositionError,
        HarnessBridgeEpochMismatchError,
        HarnessControlError,
        ConversationCursorError,
        SessionResolutionError,
    ) as exc:
        return _map_typed_error(exc)
    return StreamingResponse(
        _event_stream(subscription),
        media_type=SSE_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(
    subscription: ActiveSubscription,
) -> AsyncIterator[bytes]:
    try:
        # Prime the stream with one SSE comment: the first body chunk makes
        # GZipMiddleware flush http.response.start immediately, so a caught-up
        # subscriber's headers arrive at connect instead of with the next
        # mutation. EventSource ignores comment lines natively; this carries no
        # cursor and no event field and disturbs neither replay nor gap order.
        yield b": connected\n\n"
        for envelope in subscription.replay:
            yield _sse_frame(envelope.model_copy(update={"delivery": "resume-replay"}))
        while True:
            item = await subscription.queue.get()
            if item is CLOSE_SENTINEL:
                return
            assert isinstance(item, ConversationEventEnvelope)
            yield _sse_frame(item)
            if item.mutation.op == "gap":
                return
    finally:
        subscription.detach()


def _sse_frame(envelope: ConversationEventEnvelope) -> bytes:
    payload = json.dumps(
        envelope.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"event: conversation\ndata: {payload}\nretry: {SSE_RETRY_MS}\nid: {envelope.cursor}\n\n"
    ).encode()


__all__ = ["router"]
