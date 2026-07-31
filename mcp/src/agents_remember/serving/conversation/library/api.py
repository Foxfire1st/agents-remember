"""Native conversation library child routes: list/read/open/status/reconcile (260718-CHATS-L2).

The routes resolve the L0 runtime and authorization inside the handler (the exact same calls
the two request dependencies make) so every typed refusal — loopback violation, composition
failure, scope escape, cursor misuse, capability gate, conflict — maps to its precise HTTP
status here instead of surfacing as a raw 500 (reviewer obligation O4). Bodies are the landed
L9 wire models serialized once with camel-case aliases.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agents_remember.errors import AuthorityError, ConversationCompositionError
from agents_remember.serving.conversation.dependencies import (
    get_conversation_runtime,
    resolve_conversation_authorization,
)
from agents_remember.serving.conversation.library.errors import (
    CatalogGenerationError,
    ConversationLibraryError,
    InvalidLibraryCursorError,
    LibraryCapabilityError,
    LibraryScopeError,
    LibraryStoreError,
    OpenLedgerFullError,
    OpenRequestConflictError,
    StaleNativeIdentityError,
    UnknownLibraryHarnessError,
    UnknownNativeConversationError,
    UnknownOpenRequestError,
)
from agents_remember.serving.conversation.library.factories import (
    build_library_service,
    build_open_service,
    require_normalized_harness,
)
from agents_remember.serving.conversation.library.open_service import OpenRequest
from agents_remember.serving.conversation.library.scope import clamp_limit
from agents_remember.serving.conversation.models import (
    LibraryListCursor,
    LibraryReadCursor,
    OpenConversationOperation,
    WireModel,
)

router = APIRouter(
    prefix="/api/harnesses/{harness_id}/conversations",
    tags=["structured-conversation-library"],
)

_LIST_DEFAULT_LIMIT = 50
_LIST_MAX_LIMIT = 100
_READ_DEFAULT_LIMIT = 50
_READ_MAX_LIMIT = 100

_OPEN_STATUS_BY_OUTCOME = {
    "opened": 201,
    "pending": 202,
    "timeout-unknown": 202,
    "unsupported": 422,
    "stale-identity": 409,
    "request-conflict": 409,
    "launch-failed": 503,
    "identity-mismatch": 409,
}


class OpenLaunchContext(WireModel):
    leaf_key: str | None = None
    seat_role: str | None = None


class OpenConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1)
    expected_identity_digest: str | None = Field(default=None, alias="expectedIdentityDigest")
    cwd: str | None = None
    launch_context: OpenLaunchContext | None = Field(default=None, alias="launchContext")


class OpenStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1)


@router.get("")
async def api_library_list(
    request: Request,
    harness_id: str,
    cwd: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> JSONResponse:
    async def run() -> object:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        harness = require_normalized_harness(harness_id)
        list_cursor = _list_cursor(cursor)
        service = build_library_service(runtime, authorization)
        return await service.list(
            harness,
            cwd,
            cursor=list_cursor,
            limit=clamp_limit(limit, default=_LIST_DEFAULT_LIMIT, maximum=_LIST_MAX_LIMIT),
        )

    return await _library_call(run, success_status=200)


@router.get("/{conversation_key}")
async def api_library_read(
    request: Request,
    harness_id: str,
    conversation_key: str,
    before: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> JSONResponse:
    async def run() -> object:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        harness = require_normalized_harness(harness_id)
        read_cursor = _read_cursor(before)
        service = build_library_service(runtime, authorization)
        return await service.read(
            harness,
            conversation_key,
            before=read_cursor,
            limit=clamp_limit(limit, default=_READ_DEFAULT_LIMIT, maximum=_READ_MAX_LIMIT),
        )

    return await _library_call(run, success_status=200)


@router.post("/{conversation_key}/open")
async def api_library_open(
    request: Request,
    harness_id: str,
    conversation_key: str,
    body: Annotated[OpenConversationRequest, Body()],
) -> JSONResponse:
    async def run() -> OpenConversationOperation:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        harness = require_normalized_harness(harness_id)
        if not body.expected_identity_digest:
            raise InvalidLibraryCursorError("open requires expectedIdentityDigest")
        service = build_open_service(runtime, authorization)
        return await service.open(
            harness,
            conversation_key,
            OpenRequest(
                request_id=body.request_id,
                expected_identity_digest=body.expected_identity_digest,
                cwd=body.cwd,
                launch_context=_launch_context(body),
            ),
        )

    return await _open_call(run)


@router.post("/{conversation_key}/open-status")
async def api_library_open_status(
    request: Request,
    harness_id: str,
    conversation_key: str,
    body: Annotated[OpenStatusRequest, Body()],
) -> JSONResponse:
    async def run() -> OpenConversationOperation:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        harness = require_normalized_harness(harness_id)
        service = build_open_service(runtime, authorization)
        return await service.status(harness, conversation_key, request_id=body.request_id)

    return await _open_call(run)


@router.post("/{conversation_key}/open-reconcile")
async def api_library_open_reconcile(
    request: Request,
    harness_id: str,
    conversation_key: str,
    body: Annotated[OpenStatusRequest, Body()],
) -> JSONResponse:
    async def run() -> OpenConversationOperation:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        harness = require_normalized_harness(harness_id)
        service = build_open_service(runtime, authorization)
        return await service.reconcile(harness, conversation_key, request_id=body.request_id)

    return await _open_call(run)


# -- mapping authority --------------------------------------------------------


async def _library_call(
    run: Callable[[], Awaitable[object]],
    *,
    success_status: int,
) -> JSONResponse:
    try:
        result = await run()
    except Exception as exc:  # the typed family maps below; nothing else may leak
        return _error_response(exc)
    return JSONResponse(content=_dump(result), status_code=success_status)


async def _open_call(run: Callable[[], Awaitable[OpenConversationOperation]]) -> JSONResponse:
    try:
        operation = await run()
    except Exception as exc:
        return _error_response(exc)
    status_code = _OPEN_STATUS_BY_OUTCOME.get(operation.outcome, 500)
    return JSONResponse(content=_dump(operation), status_code=status_code)


def _error_response(exc: Exception) -> JSONResponse:
    """One precise status for every typed refusal (reviewer O4: no raw 500s)."""

    if isinstance(exc, LibraryCapabilityError):
        return JSONResponse(
            content={
                "status": "capability-unavailable",
                "capabilityState": exc.capability_state,
                "detail": str(exc),
            },
            status_code=422,
        )
    for error_type, status, status_code in _ERROR_STATUS_TABLE:
        if isinstance(exc, error_type):
            return _envelope(status, str(exc), status_code)
    raise exc  # genuinely unexpected states stay loud, never silently mapped


# Order matters: subclasses precede their bases (LibraryScopeError before AuthorityError,
# the concrete library errors before ConversationLibraryError).
_ERROR_STATUS_TABLE: tuple[tuple[type[Exception], str, int], ...] = (
    (LibraryScopeError, "scope-denied", 403),
    (AuthorityError, "authorization-failed", 403),
    (UnknownLibraryHarnessError, "unknown-harness", 404),
    (UnknownNativeConversationError, "unknown-conversation", 404),
    (UnknownOpenRequestError, "unknown-request", 404),
    (CatalogGenerationError, "cursor-reset-required", 409),
    (StaleNativeIdentityError, "stale-identity", 409),
    (OpenRequestConflictError, "request-conflict", 409),
    (InvalidLibraryCursorError, "invalid-cursor", 400),
    (OpenLedgerFullError, "open-ledger-full", 503),
    (LibraryStoreError, "library-unavailable", 503),
    (ConversationCompositionError, "composition-unavailable", 503),
    (ConversationLibraryError, "library-unavailable", 503),
)


def _envelope(status: str, detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(content={"status": status, "detail": detail}, status_code=status_code)


def _dump(value: object) -> object:
    if isinstance(value, WireModel):
        # Null is meaningful on this wire (nextCursor/olderCursor/identity absence is
        # contract-significant), matching the existing serving serializers.
        return value.model_dump(mode="json", by_alias=True)
    return value


def _list_cursor(raw: str | None) -> LibraryListCursor | None:
    if raw is None:
        return None
    try:
        return LibraryListCursor(raw)
    except ValueError as exc:
        raise InvalidLibraryCursorError(f"list cursor is malformed: {exc}") from exc


def _read_cursor(raw: str | None) -> LibraryReadCursor | None:
    if raw is None:
        return None
    try:
        return LibraryReadCursor(raw)
    except ValueError as exc:
        raise InvalidLibraryCursorError(f"read cursor is malformed: {exc}") from exc


def _launch_context(body: OpenConversationRequest) -> dict[str, str | None]:
    context = body.launch_context
    if context is None:
        return {}
    return {"leafKey": context.leaf_key, "seatRole": context.seat_role}


__all__ = ["router"]
