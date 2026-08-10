from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.gate_decisions import (
    GateDecisionContext,
    record_gate_decision,
    record_lifecycle_gate_decision,
)
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import GateVerdict
from agents_remember.controlplane.store import GateStore
from agents_remember.models.operator_inbox import OperatorInboxPostResponse
from agents_remember.models.tool_response import finalize_tool_response
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.observer.projection import TaskDocNode
from agents_remember.serving._app_common import (
    OperatorInboxPostRequest,
    _if_none_match_matches,
    _projection_body_cache,
    _ServingRuntime,
    stream_events,
)
from agents_remember.serving._app_lifespan import _agent_notifier_heartbeat_payload
from agents_remember.serving.actions import (
    ActionEvaluationContext,
    ActionOutcome,
    ActionRequest,
    DismissalIntent,
    GateDecisionIntent,
    evaluate_action,
)
from agents_remember.serving.dispatch_brief import HostedDelivery
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.operator_inbox_posts import (
    OperatorInboxPostContext,
    post_operator_inbox_entry,
)
from agents_remember.serving.projections.snapshots import read_task_document_body
from agents_remember.serving.response_contract import (
    ACTION_RESPONSES,
    ActionAccepted,
    HttpDetailRefusal,
    OperatorInboxDismissed,
    StatusRefusal,
    StreamReadyMarker,
)
from agents_remember.serving.served_state import ServedWorkspaceProjection, served_state_tail

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
    if _if_none_match_matches(if_none_match, revision):
        return Response(status_code=304, headers=headers)
    # The dump rides the per-instance memo; only the volatile tail
    # (build stamp + at-response-time heartbeat) is computed per request, on a copy.
    # This body is therefore ASSEMBLED rather than dumped from one model -- the memo and the
    # ETag both depend on the tick-time half being reusable and the serve-time half not
    # being. What it assembles to is nonetheless declared: it is a
    # ``served_state.ServedWorkspaceProjection``, and the conformance suite validates the
    # real route's output against that model. Validating here instead would re-parse ~1.3 MB
    # per request and hand back exactly what the memo exists to save.
    body = dict(_projection_body_cache.body(snapshot))
    body.update(
        served_state_tail(build=runtime.build, heartbeat=_agent_notifier_heartbeat_payload(runtime))
    )
    return JSONResponse(content=body, headers=headers)


def _task_document_response(runtime: _ServingRuntime, path: str) -> JSONResponse:
    _, snapshot = runtime.projector.current()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="projection not ready")
    doc = read_task_document_body(
        runtime.config.coordination_root,
        doc_path=path,
        enclosures=snapshot.enclosures,
        now=runtime.liveness_clock(),
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="task document not found")
    return JSONResponse(content=doc.model_dump(by_alias=True, exclude_none=True))


def _register_projection_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """The read side of the cockpit: the projection once, tailed, and the raw event river."""

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
        response_model=TaskDocNode,
        responses={
            404: {"model": HttpDetailRefusal, "description": "No such task document"},
            503: {"model": HttpDetailRefusal, "description": "Projection not ready"},
        },
    )
    def api_task_document(path: Annotated[str, Query()]) -> JSONResponse:
        return _task_document_response(runtime, path)

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
    async def api_stream() -> AsyncIterator[ServerSentEvent]:
        async for event in stream_events(
            runtime.projector,
            build=runtime.build,
            agent_notifier_heartbeat=_agent_notifier_heartbeat_payload(runtime),
        ):
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
            runtime.config, last_event_id=last_event_id, interval=runtime.interval
        ):
            yield event


# --- action + operator-inbox routes -------------------------------------------------------------


def _recorded_gate_decision(
    config: McpRuntimeConfig, decision: GateDecisionIntent
) -> dict[str, Any]:
    """Durably record one gate decision as developer-attributed, and answer with the gate.

    Developer attribution is the un-forgeable half of the contract -- the agent's own path is
    model-attributed -- and it is what server-side closeout enforcement consumes.

    Every intent that arrives here is ADDRESSED. Whether a request names a gate to decide is a
    question about the request, so it is settled once, in the layer that validates requests:
    ``actions._gate_decision_outcome`` refuses a decision naming neither a lifecycle target nor a
    gate id with 400 ``missing-target`` and never builds an intent for that shape. A decision
    without a lifecycle id is therefore the gate-id-only cancel that guard let through.
    """

    root = observer_root(config)
    context = GateDecisionContext(
        store=GateStore(root),
        inbox_store=OperatorInboxStore(root),
        expectation_store=ExpectationRowStore(root),
        policy=config.orchestration.gate_policy,
        now=datetime.now(UTC),
    )
    verdict = GateVerdict(
        decision=decision.decision,
        by="developer",
        via="dashboard",
        note=decision.note,
    )
    if decision.lifecycle_id is not None:
        payload = record_lifecycle_gate_decision(
            context,
            lifecycle_id=decision.lifecycle_id,
            expected_gate_id=decision.gate_id,
            verdict=verdict,
            evidence_refs=None,
        )
        return finalize_tool_response("gate_decide", payload)
    assert decision.gate_id is not None
    payload = record_gate_decision(
        context,
        gate_id=decision.gate_id,
        lifecycle_id=None,
        verdict=verdict,
        evidence_refs=None,
    )
    return finalize_tool_response("gate_decide", payload)


def _gate_decision_response(
    config: McpRuntimeConfig,
    outcome: ActionOutcome,
    decision: GateDecisionIntent,
    *,
    target: str | None,
) -> Response:
    """Record the operator's gate decision and answer with the gate it wrote."""

    try:
        gate = _recorded_gate_decision(config, decision)
    except KeyError as exc:
        return JSONResponse(
            content={
                "status": "stale-gate" if decision.gate_id else "no-open-gate",
                "detail": str(exc),
                "target": target,
            },
            status_code=409,
        )
    return JSONResponse(content={**outcome.body, "gate": gate}, status_code=outcome.status_code)


def _dismissal_response(
    config: McpRuntimeConfig, outcome: ActionOutcome, intent: DismissalIntent
) -> Response:
    """Apply one attention dismissal.

    Dismissals are current acknowledgements, not history. A gate-open item is consumed by
    cancelling the gate itself, so it needs no acknowledgement row once the source is gone.

    Every intent that arrives here is SCOPED, because whether a request carries a scope is settled
    once, in the layer that validates requests: ``actions._dismiss_action_outcome`` refuses an
    acknowledgement that names neither a lifecycle, nor a gate to cancel, nor the repo-level
    ``actionable-drift`` signal with 400 ``missing-lifecycle``. So a dismissal that is not the gate
    cancel below is one of the two the acknowledgement row can be written for.
    """

    gate: dict[str, Any] | None = None
    if intent.kind == "gate-open" and intent.gate_id is not None:
        with contextlib.suppress(KeyError):
            gate = _recorded_gate_decision(
                config,
                GateDecisionIntent(
                    lifecycle_id=intent.lifecycle_id,
                    gate_id=intent.gate_id,
                    decision="cancel",
                    note=intent.note or "Dismissed from attention queue.",
                ),
            )
    else:
        AttentionDismissalStore(observer_root(config)).dismiss(
            AttentionDismissalRecord(
                itemId=intent.item_id,
                dismissedAt=intent.dismissed_at,
                kind=intent.kind,
                lifecycleId=intent.lifecycle_id,
                gateId=intent.gate_id,
            )
        )
    body = outcome.body if gate is None else {**outcome.body, "gate": gate}
    return JSONResponse(content=body, status_code=outcome.status_code)


def _action_response(runtime: _ServingRuntime, action: str, request: ActionRequest) -> Response:
    """Route one action: acknowledge it, apply its dismissal, or record its gate decision."""

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
            gate_id=request.gateId,
            note=request.note,
            item_id=request.itemId,
            kind=request.kind,
        ),
    )
    if outcome.dismissal is not None:
        return _dismissal_response(runtime.config, outcome, outcome.dismissal)
    if outcome.gate_decision is not None:
        return _gate_decision_response(
            runtime.config, outcome, outcome.gate_decision, target=request.target
        )
    return JSONResponse(content=outcome.body, status_code=outcome.status_code)


def _operator_inbox_response(
    runtime: _ServingRuntime, request: OperatorInboxPostRequest
) -> Response:
    # External-chat path: a dashboard response with no hosted session is written to the
    # pull-based operator inbox. External agents read it through the MCP operator_inbox_poll /
    # operator_inbox_consume tools; this endpoint only owns the developer/dashboard write side.
    try:
        payload = finalize_tool_response(
            "operator_inbox_post",
            post_operator_inbox_entry(
                OperatorInboxPostContext(
                    config=runtime.config,
                    store=OperatorInboxStore(runtime.observer_root),
                    delivery=HostedDelivery(
                        enabled=request.deliver_to_hosted,
                        catalog=runtime.catalog,
                        host=runtime.host,
                        paster=runtime.paster,
                    ),
                ),
                address=InboxAddress(
                    lifecycle_id=request.lifecycle_id,
                    agent_id=request.agent_id,
                    recipient_role=request.recipient_role,
                ),
                message=InboxMessage(
                    ask=request.ask,
                    response=request.response,
                    message_kind=request.message_kind,
                    gate_id=request.gate_id,
                    artifact_path=request.artifact_path,
                ),
                poster=InboxPoster(
                    created_by="developer",
                    created_via="dashboard",
                    sender_agent_id=request.sender_agent_id,
                    sender_role=request.sender_role,
                ),
            ),
        )
    except ValueError as exc:
        return JSONResponse(content={"status": "bad-address", "detail": str(exc)}, status_code=400)
    return JSONResponse(content=payload, status_code=200)


def _inbox_dismiss_response(runtime: _ServingRuntime, entry_id: str) -> Response:
    removed = OperatorInboxStore(runtime.observer_root).delete(entry_id)
    if not removed:
        return JSONResponse(content={"status": "not-found", "entryId": entry_id}, status_code=404)
    return JSONResponse(content={"status": "dismissed", "entryId": entry_id}, status_code=200)


def _register_action_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """The write side the developer drives: the gate return channel and the operator inbox."""

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

    @app.post(
        "/api/operator-inbox",
        response_model=OperatorInboxPostResponse,
        responses={400: {"model": StatusRefusal, "description": "Unaddressable inbox message"}},
    )
    def api_operator_inbox(request: OperatorInboxPostRequest) -> Response:
        return _operator_inbox_response(runtime, request)

    @app.post(
        "/api/operator-inbox/{entry_id}/dismiss",
        response_model=OperatorInboxDismissed,
        responses={404: {"model": OperatorInboxDismissed, "description": "No such inbox entry"}},
    )
    def api_operator_inbox_dismiss(entry_id: str) -> Response:
        return _inbox_dismiss_response(runtime, entry_id)
