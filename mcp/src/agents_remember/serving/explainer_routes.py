"""HTTP request parsing and route handlers for the explainer's bound composition."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from agents_remember.canvas.explainer_maintenance import (
    CancelOutcome,
    cancel_seat,
    retire_seat,
    seat_marks,
    seat_state,
    update_seat,
)
from agents_remember.canvas.explainer_paths import (
    EXPLAINER_FRAME,
    receipt_id_segment,
    repository_path,
)
from agents_remember.canvas.explainer_planned_read import planned_payload
from agents_remember.canvas.explainer_server import page_revision
from agents_remember.canvas.explainer_service import (
    ExplainerDeps,
    ExplainerRefused,
    SeatOutcome,
    build_seat,
    cancel_build,
)
from agents_remember.canvas.paths import PathRefused
from agents_remember.serving import explainer as read_model
from agents_remember.serving.explainer_move_watch import move_notice_watch
from agents_remember.serving.explainer_watcher import (
    LeafSelection,
)
from agents_remember.serving.read_disconnect import read_while_connected
from agents_remember.tasks.planned_comparison import (
    compose_explainer_send_message,
    leaf_document,
    serialize_frame_binding,
)


def _parse_comparison_number(body: dict[str, Any]) -> int | JSONResponse | None:
    if "comparisonNumber" in body and body["comparisonNumber"] is not None:
        raw = body["comparisonNumber"]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return read_model._refusal(
                "invalid-request",
                "comparisonNumber is a non-negative integer",
                status=400,
                extra={"status": "invalid-request"},
            )
        return raw
    raw_comp = body.get("comparison")
    if isinstance(raw_comp, int) and not isinstance(raw_comp, bool) and raw_comp >= 0:
        return raw_comp
    if isinstance(raw_comp, str) and raw_comp.startswith("#") and raw_comp[1:].isdecimal():
        return int(raw_comp[1:])
    return None


def _send_facts(body: dict[str, Any]) -> read_model._SendRequest | JSONResponse:
    history = body.get("history")
    if history is not None and (
        not isinstance(history, str) or history not in read_model._VALID_HISTORIES
    ):
        return read_model._refusal(
            "invalid-request",
            "history may only be 'live', 'recorded', or 'planned'",
            status=400,
            extra={"status": "invalid-request"},
        )
    parsed_num = _parse_comparison_number(body)
    if isinstance(parsed_num, JSONResponse):
        return parsed_num
    raw_message = body.get("message")
    picks = body.get("picks")
    stale_part_ids = body.get("stalePartIds")
    stale_grades = body.get("staleGrades")
    if raw_message is None:
        raw_message = ""
    if not isinstance(raw_message, str) or len(raw_message) > read_model.MAX_MESSAGE:
        return read_model._refusal("request-invalid", "a Send carries its message", status=400)
    composed = compose_explainer_send_message(raw_message, picks, stale_part_ids, stale_grades)
    # An empty Send is the end-to-end review, not a malformed one: the form invites it, and the
    # service composes the ask (MIK-R211 v2 rule 5). Only an over-long message is refused.
    if len(composed) > read_model.MAX_MESSAGE:
        return read_model._refusal("request-invalid", "a Send carries its message", status=400)
    request_id = body.get("requestId")
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > read_model.MAX_REQUEST_ID
    ):
        return read_model._refusal("request-invalid", "a Send carries its request id", status=400)
    return read_model._SendRequest(
        message=composed,
        request_id=request_id,
        comparison_number=parsed_num,
        history=history,
        planned=bool(body.get("planned")) or history == "planned",
    )


def _receipt_id(body: dict[str, Any]) -> str | JSONResponse:
    if "receiptId" not in body or body["receiptId"] is None:
        return f"retire-{uuid.uuid4().hex[:12]}"
    receipt_id = body.get("receiptId")
    if not isinstance(receipt_id, str) or not receipt_id or len(receipt_id) > 64:
        return read_model._refusal("request-invalid", "a Retire carries its receipt id", status=400)
    try:
        return receipt_id_segment(receipt_id)
    except PathRefused:
        return read_model._refusal(
            "request-invalid",
            "a receipt id is one file name: no separator, no traversal, no absolute path",
            status=400,
        )


def _wired(composition: read_model.ExplainerComposition) -> ExplainerDeps | JSONResponse:
    if composition.deps is None:
        return read_model._refusal("explainer-unavailable", read_model.UNWIRED_DETAIL, status=503)
    return composition.deps


def handle_state(composition: read_model.ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    try:
        state = seat_state(deps, scope)
        return JSONResponse(
            read_model._state_payload(
                composition,
                scope,
                state,
                composition.contract(scope),
                request=request,
            )
        )
    except (ExplainerRefused, ValueError) as error:
        return read_model._refusal("explainer-unavailable", str(error))


def handle_stale(composition: read_model.ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    selected_rev = read_model._query_int(request, "revision")
    comp_number = read_model._query_int(request, "comparisonNumber", "comparison")
    comp_owner = request.query_params.get("comparisonOwner") or request.query_params.get("history")
    view = read_model.stale_view(
        composition, scope, revision=selected_rev, selector=(comp_number, comp_owner)
    )
    stale = view["stale"]
    # The top bar's badge reads this route alone on the Review tab: besides the staleness it needs
    # the shown revision (whether one exists, and the number a regeneration names), the watcher's
    # newer-comparison notice, and the label's two marks -- retired, and a build in progress -- as
    # /state names them. The host's seat state is read at the press that needs it.
    # The notice travels as `movedNotice` because `moved` already names the staleness's own list.
    marker, build = seat_marks(deps, scope)
    return JSONResponse(
        {
            "state": "ok",
            "stale": stale,
            **stale,
            "explanation": view["explanation"],
            "movedNotice": read_model._moved(deps, scope, application),
            "retired": marker is not None,
            "building": build
            if isinstance(build, dict) and build.get("state") == "building"
            else None,
        }
    )


async def handle_build(
    composition: read_model.ExplainerComposition, request: Request
) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    named = _send_facts(await read_model._body(request))
    if isinstance(named, JSONResponse):
        return named
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    token = read_model._SEND_SELECTION.set(named)
    try:
        outcome: SeatOutcome = await run_in_threadpool(
            build_seat,
            deps,
            scope,
            request_id=named.request_id,
            message=named.message,
        )
    except read_model._ComparisonMoved as error:
        return read_model._refusal(
            error.reason,
            error.detail,
            extra={
                "requestedComparisonNumber": error.requested,
                "currentComparisonNumber": error.current,
                "requestedComparison": error.requested,
                "currentComparison": error.current,
                "refresh": True,
            },
        )
    except ExplainerRefused as error:
        return read_model._refusal(error.reason, error.detail)
    finally:
        read_model._SEND_SELECTION.reset(token)
    if outcome.built or not outcome.current:
        move_notice_watch(deps.config, application.watcher).clear(LeafSelection.of(scope))
    seat_record = {**outcome.as_record(), "state": outcome.seat}
    return JSONResponse(
        {
            "state": "ok",
            "attempt": f"build:{named.request_id}",
            "seat": seat_record,
            "agentId": outcome.agent_id,
            "workspaceId": str(outcome.workspace),
            "generation": outcome.generation,
            "binding": serialize_frame_binding(outcome.binding),
        }
    )


def handle_update(composition: read_model.ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    try:
        update = update_seat(deps, scope)
    except ExplainerRefused as error:
        return read_model._refusal(error.reason, error.detail)
    move_notice_watch(deps.config, application.watcher).clear(LeafSelection.of(scope))
    counts = dict(update.counts())
    unavailable = update.counts_unavailable
    if unavailable is not None or update.blocks_changed is None:
        counts["countsUnavailable"] = True
    update_record = dict(update.as_record())
    update_record["counts"] = counts
    if unavailable is not None or update.blocks_changed is None:
        update_record["countsUnavailable"] = True
    if unavailable is not None:
        update_record["unavailable"] = unavailable
    return JSONResponse(
        {
            "state": "ok",
            "update": update_record,
            "counts": counts,
            "attempt": f"update:{update.previous_number}",
        }
    )


async def handle_retire(
    composition: read_model.ExplainerComposition, request: Request
) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    receipt_id = _receipt_id(await read_model._body(request))
    if isinstance(receipt_id, JSONResponse):
        return receipt_id
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    try:
        outcome = retire_seat(deps, scope, receipt_id=receipt_id)
    except ExplainerRefused as error:
        return read_model._refusal(error.reason, error.detail)
    move_notice_watch(deps.config, application.watcher).clear(LeafSelection.of(scope))
    record = outcome.as_record()
    receipt = record.get("receipt")
    if isinstance(receipt, dict) and "receipt_id" in receipt:
        receipt["receiptId"] = receipt["receipt_id"]
    return JSONResponse({"state": "ok", "retire": record, "receipt": receipt})


async def handle_cancel(
    composition: read_model.ExplainerComposition, request: Request
) -> JSONResponse:
    """The developer's cancel (MIK-R105 v1 rule 9): the exact seat and request, nothing else.

    Cancellation names whether a turn stopped, publication was prevented, or a request remains
    uncertain. It measures the frame store's ready revision around the host's named cancel, so a
    turn that raced publication is reported truthfully, and it keeps the last ready revision.
    """

    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    body = await read_model._body(request)
    request_id = body.get("requestId")
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > read_model.MAX_REQUEST_ID
    ):
        return read_model._refusal("request-invalid", "a cancel carries its request id", status=400)
    scope = read_model._scope(request)
    # A build has no turn to stop: it is a thread writing a snapshot. Cancel ends it here, before
    # any seat work, so a developer can end an attempt that never reached its seat, and the next
    # Send is free the moment the builder reaches its next checkpoint (MIK-R211 v2 rule 5).
    ended = await run_in_threadpool(cancel_build, deps, scope, request_id=request_id)
    if ended is not None:
        return JSONResponse(
            {
                "state": "ok",
                "cancel": {
                    "leaf": {
                        "repository": scope.repository_id,
                        "task": scope.task,
                        "leaf": scope.leaf_id,
                    },
                    "requestId": request_id,
                    "buildStopped": True,
                    "reason": ended.get("error"),
                    "failedStep": ended.get("failedStep"),
                    "build": ended,
                    "publicationPrevented": True,
                },
            }
        )
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    task_root = read_model._task_folder(deps.config, scope)
    before = page_revision(task_root, scope.leaf_id)
    try:
        outcome: CancelOutcome = await run_in_threadpool(
            cancel_seat, deps, scope, request_id=request_id
        )
    except ExplainerRefused as error:
        return read_model._refusal(error.reason, error.detail)
    after = page_revision(task_root, scope.leaf_id)
    record = outcome.as_record()
    record["publicationPrevented"] = before == after
    record["revision"] = after
    return JSONResponse({"state": "ok", "cancel": record})


def handle_revisions(
    composition: read_model.ExplainerComposition, request: Request
) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    return JSONResponse(
        {
            "state": "ok",
            "revisions": read_model._revisions(deps.config, scope, application),
            "pinned": read_model._agreed_number(deps.config, scope),
            "total": read_model._total(deps.config, scope, application),
        }
    )


async def handle_remove_revisions(
    composition: read_model.ExplainerComposition, request: Request
) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    body = await read_model._body(request)
    named = body.get("revisions")
    if (
        not isinstance(named, list)
        or not named
        or len(named) > read_model.MAX_REMOVE_BATCH
        or any(isinstance(number, bool) or not isinstance(number, int) for number in named)
    ):
        return read_model._refusal(
            "request-invalid", "a removal names the revisions to remove", status=400
        )
    try:
        frame = application.frame_scope(
            read_model._task_folder(deps.config, scope), scope.leaf_id, EXPLAINER_FRAME
        )
        removal = application.remove_revisions(frame, revisions=tuple(named))
    except application.frame_refused_cls as error:
        return read_model._refusal(
            getattr(error, "reason", None) or "revision-removal-refused", str(error)
        )
    except ValueError as error:
        return read_model._refusal("revision-removal-refused", str(error))
    return JSONResponse(
        {
            "state": "ok",
            "removed": list(removal.removed),
            "remaining": len(removal.retained),
            "pinned": removal.pinned,
            "bytesFreed": removal.bytes_freed,
            "detail": removal.detail,
        }
    )


def handle_planned(composition: read_model.ExplainerComposition, request: Request) -> JSONResponse:
    deps = _wired(composition)
    if isinstance(deps, JSONResponse):
        return deps
    application = read_model._application(composition)
    if isinstance(application, JSONResponse):
        return application
    scope = read_model._scope(request)
    try:
        if not deps.facts(scope).planned:
            raise ExplainerRefused(
                "not-a-planning-leaf",
                f"leaf {scope.leaf_id} is not planning; its page shows the comparison it is bound to",
            )
        contract = composition.contract(scope)
        if contract is None:
            raise ExplainerRefused(
                "planned-comparison-unavailable",
                f"leaf {scope.leaf_id} has no enclosure contract to read the master line's tips "
                "from; the planned comparison is built from the leaf's own contract",
            )
        record, _trees = read_model._planned(deps.config, contract, scope, application)
        task_root = read_model._task_folder(deps.config, scope)
        document = leaf_document(task_root, scope.leaf_id)
        if document is None:
            raise ExplainerRefused(
                "leaf-unavailable", f"no task document names leaf {scope.leaf_id} in {task_root}"
            )
        payload = planned_payload(
            document=document,
            comparison=record,
            task_root=task_root,
            coordination_root=Path(deps.config.coordination_root),
            code_repository=repository_path(deps.config, scope.repository_id),
        )
    except (ExplainerRefused, ValueError) as error:
        return read_model._refusal("planned-unavailable", str(error))
    return JSONResponse({"state": "ok", "planned": payload})


def _watched_read(
    handler: Callable[[read_model.ExplainerComposition, Request], Response],
    composition: read_model.ExplainerComposition,
) -> Callable[[Request], Any]:
    """One synchronous read whose client is watched: it stops computing when the client leaves.

    The handler's payload is unchanged; only where it runs is: on a worker thread under the read's
    cancellation token instead of the plain threadpool (D569 mean 4).
    """

    async def watched(request: Request) -> Response:
        return await read_while_connected(request, lambda: handler(composition, request))

    return watched


def register_explainer_routes(app: FastAPI, composition: read_model.ExplainerComposition) -> None:
    base = read_model.EXPLAINER_API_PREFIX + "/{repository_id}/{master}/{leaf_id}"
    for suffix, read in (("/state", handle_state), ("/stale", handle_stale)):
        app.add_api_route(f"{base}{suffix}", _watched_read(read, composition), methods=["GET"])
    for suffix, handler, method in (
        ("/build", handle_build, "POST"),
        ("/send", handle_build, "POST"),
        ("/update", handle_update, "POST"),
        ("/revisions", handle_revisions, "GET"),
        ("/remove-revisions", handle_remove_revisions, "POST"),
        ("/retire", handle_retire, "POST"),
        ("/cancel", handle_cancel, "POST"),
        ("/planned", handle_planned, "GET"),
    ):
        app.add_api_route(f"{base}{suffix}", partial(handler, composition), methods=[method])
