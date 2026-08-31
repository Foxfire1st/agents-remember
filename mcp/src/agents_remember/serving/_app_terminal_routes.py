from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Request, Response, UploadFile, WebSocket
from fastapi.responses import JSONResponse

from agents_remember.errors import HarnessControlError
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.observer.events import now_iso
from agents_remember.serving._app_common import (
    _IMAGE_EXTS,
    _MAX_IMAGE_BYTES,
    DEFAULT_SHELL,
    TerminalAttachTaskRequest,
    TerminalLandedCleanupRequest,
    TerminalOpenRequest,
    TerminalPasteRequest,
    TerminalRenameRequest,
    TerminalRetireRequest,
    _attach_terminal_session,
    _bridge_terminal,
    _catalog_payload,
    _looks_like_image,
    _ServingRuntime,
)
from agents_remember.serving.harness_control_api import resolve_terminal_open_selection
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    stop_control_session,
    submit_control_prompt,
)
from agents_remember.serving.harnesses import detect_harnesses
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.response_contract import (
    DetectedHarnessesResponse,
    SeatTakenConflict,
    StatusRefusal,
    TerminalAlreadyRetired,
    TerminalCleanupResult,
    TerminalHarnessDelivery,
    TerminalHarnessRefusal,
    TerminalImageRefusal,
    TerminalImageStored,
    TerminalLaunchConflict,
    TerminalOpened,
    TerminalPaneDelivery,
    TerminalRenamed,
    TerminalRetired,
    TerminalRetireRefused,
    TerminalSessionsResponse,
    TerminalTaskAttached,
    TerminalTaskRefused,
    TerminalTerminated,
    UnknownActorRefusal,
    UnknownSessionRefusal,
)
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.retire_policy import RetirePolicyError, SeatRef, check_retire_authority
from agents_remember.serving.seat_events import log_rename_event, log_retire_event
from agents_remember.serving.terminal_liveness import LivenessProbe, observe_terminal_liveness
from agents_remember.serving.terminal_opener import (
    ControlRunnerRequest,
    OpenTerminalResult,
    SpawnProvenance,
    TerminalLaunchRequest,
    open_terminal_session,
)
from agents_remember.serving.terminal_task_assignment import (
    TaskAssignmentRuntime,
    assign_terminal_session_to_task,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology


async def _serve_terminal_websocket(
    runtime: _ServingRuntime, websocket: WebSocket, session: str
) -> None:
    # Mode B2: bridge a live terminal-host session to xterm.js. Attach-only
    # -- the session is opened out-of-band (the lifecycle-correlated launch comes
    # later); an unknown id is refused with a private close code. Same localhost posture
    # as the rest of the app (the host spawns a fixed argv, never a wire-supplied command).
    await websocket.accept()
    session_obj = _attach_terminal_session(
        HostedSessionRuntime(catalog=runtime.catalog, host=runtime.host),
        session_id=session,
        attached_at=now_iso(),
        checked_at=runtime.liveness_clock(),
        liveness_config=runtime.liveness_config,
    )
    if session_obj is None:
        await websocket.close(code=4404)
        return
    try:
        await _bridge_terminal(websocket, session_obj)
    finally:
        if not session_obj.is_alive:
            runtime.catalog.mark_exited(session)
        else:
            session_obj.close()
        with contextlib.suppress(RuntimeError):
            await websocket.close()


def _detected_harnesses_payload(runtime: _ServingRuntime) -> dict[str, Any]:
    # The supported TUI harnesses + whether each is installed here. The dashboard
    # renders a launch button per *detected* harness; the argv stays server-side (open via POST).
    # The EFFECTIVE registry (builtin merged with orchestration.harnesses in the
    # GLOBAL agentic settings, per-use) -- settings-defined harnesses get buttons too. Repo-local
    # overrides are leaf-scoped dispatch material (the MCP spawn tool), not workspace buttons.
    registry = load_agentic_settings(runtime.config.coordination_root).harnesses
    return {
        "harnesses": [
            {"id": h.id, "name": h.name, "detected": h.detected}
            for h in detect_harnesses(registry=registry)
        ]
    }


def _register_terminal_session_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """Attaching to a live pane, and what there is to attach to."""

    # THE ONE ROUTE WITHOUT A DECLARED RESPONSE MODEL, and the only one there can be: a
    # websocket is registered as an ``APIWebSocketRoute``, which takes no ``response_model``
    # because it has no response body -- it upgrades the connection and then frames bytes both
    # ways. ``test_serving_response_conformance.py`` recognises this exemption by route CLASS, so
    # a future undeclared *HTTP* route cannot hide behind it.
    @app.websocket("/api/terminal/{session}")
    async def api_terminal(websocket: WebSocket, session: str) -> None:
        await _serve_terminal_websocket(runtime, websocket, session)

    # One of only two routes that return a bare ``dict``, so FastAPI itself validates this
    # body against the model -- the declaration is live enforcement here, not just schema.
    # ``exclude_unset`` reproduces ``TerminalCatalogEntry.to_json``'s conditional key set
    # exactly, instead of back-filling nulls the dashboard has never seen.
    @app.get(
        "/api/terminal/sessions",
        response_model=TerminalSessionsResponse,
        response_model_exclude_unset=True,
    )
    def api_terminal_sessions() -> dict[str, Any]:
        return {
            "sessions": [_catalog_payload(entry) for entry in runtime.liveness_sweeper.refresh()]
        }

    # The second FastAPI-validated route. Every key is required, so nothing is excluded.
    @app.get("/api/harnesses", response_model=DetectedHarnessesResponse)
    def api_harnesses() -> dict[str, Any]:
        return _detected_harnesses_payload(runtime)


# --- terminal control routes --------------------------------------------------------------------


def _landed_cleanup_response(runtime: _ServingRuntime, session_ids: Sequence[str]) -> Response:
    closed: list[str] = []
    skipped: list[dict[str, str]] = []
    closed_entries: list[TerminalCatalogEntry] = []
    for session in session_ids:
        entry = runtime.catalog.get(session)
        if entry is None:
            skipped.append({"session": session, "reason": "unknown-session"})
            continue
        if entry.status != "landed":
            skipped.append({"session": session, "reason": f"status:{entry.status}"})
            continue
        updated = retire_entry(
            runtime.catalog,
            runtime.host,
            entry,
            SeatClosure(
                at=now_iso(),
                by_session=None,
                reason="landed group cleanup",
                edge="landed-group-cleanup",
            ),
        )
        if updated is None:
            skipped.append({"session": session, "reason": "unknown-session"})
            continue
        closed.append(session)
        closed_entries.append(updated)
    for entry in closed_entries:
        log_retire_event(runtime.config, entry)
    return JSONResponse(
        content={
            "status": "cleaned",
            "closed": len(closed),
            "skipped": len(skipped),
            "closedSessions": closed,
            "skippedSessions": skipped,
        },
        status_code=200,
    )


def _terminal_entry_payload(entry: TerminalCatalogEntry) -> dict[str, Any]:
    """The catalog facts every open/conflict answer repeats about one durable row."""

    return {
        "session": entry.id,
        "kind": entry.kind,
        "harness": entry.harness,
        "tmuxName": entry.tmux_name,
        "controlState": entry.control_state,
        "controlEndpoint": (
            str(entry.control_endpoint) if entry.control_endpoint is not None else None
        ),
        "controlProtocol": entry.control_protocol,
        "resolvedModel": entry.resolved_model,
        "resolvedEffort": entry.resolved_effort,
    }


def _open_terminal_response(
    runtime: _ServingRuntime, session: str, request: TerminalOpenRequest
) -> Response:
    # Mode B2 opener: the dashboard *spawns + owns* a
    # session, then the WebSocket above attaches to it. The task-seat claim + ensure + upsert
    # composition lives in the shared `open_terminal_session` so this route and the internal
    # `spawn_agent_session` primitive behind public `dispatch_agent` use ONE opener.
    config = runtime.config
    try:
        resolved_launch = resolve_terminal_open_selection(
            kind=request.kind,
            harness=request.harness,
            model=request.model,
            effort=request.effort,
            workspace=config.workspace_root,
        )
    except HarnessControlError as exc:
        return JSONResponse(
            content={"status": "launch-selection-invalid", "detail": str(exc)},
            status_code=400,
        )
    result = open_terminal_session(
        runtime=HostedSessionRuntime(catalog=runtime.catalog, host=runtime.host),
        session_id=session,
        launch=TerminalLaunchRequest(
            kind=request.kind,
            workspace_root=config.workspace_root,
            shell=os.environ.get("SHELL") or DEFAULT_SHELL,
            harness=request.harness,
            env={"AR_SPAWN_ROLE": request.role} if request.role is not None else None,
            # Resolve harness ids against the effective GLOBAL registry (builtin merged
            # with orchestration.harnesses) so dashboard launches and MCP dispatches agree on argv.
            # Loaded only for harness-kind opens: a malformed settings file must
            # fail the launches that USE it, never a plain scratch terminal.
            harnesses=(
                load_agentic_settings(config.coordination_root).harnesses
                if request.kind == "harness" or request.harness
                else None
            ),
            control=ControlRunnerRequest(
                resolved_launch=resolved_launch,
                endpoint_root=config.coordination_root / "runtime" / "harness-control",
            ),
        ),
        provenance=SpawnProvenance(
            label=request.label,
            lifecycle_id=request.lifecycle_id,
            task_document_ref=request.task_document_ref,
        ),
    )
    refusal = _open_terminal_refusal_response(result, request.task_document_ref)
    if refusal is not None:
        return refusal
    entry = result.entry
    assert entry is not None  # opened/conflict => the actual durable row
    if result.status == "launch-conflict":
        return JSONResponse(
            content={
                **_terminal_entry_payload(entry),
                "status": "launch-selection-conflict",
                "detail": result.detail,
            },
            status_code=409,
        )
    return JSONResponse(
        content={
            **_terminal_entry_payload(entry),
            "label": entry.label,
            "lifecycleId": entry.lifecycle_id,
            "taskDocumentRef": (
                entry.task_document_ref.model_dump()
                if entry.task_document_ref is not None
                else None
            ),
            "seatRole": entry.binding_role,
            "cwd": str(entry.cwd),
            "status": "running",
        },
        status_code=200,
    )


def _open_terminal_refusal_response(
    result: OpenTerminalResult, task_document_ref: TaskDocumentRef | None
) -> Response | None:
    """Map opener refusals before the route reads an opened catalog row."""

    if result.status == "bad-kind":
        return JSONResponse(
            content={"status": "bad-kind", "detail": result.detail}, status_code=400
        )
    if result.status.startswith("task-binding-"):
        return JSONResponse(
            content={"status": result.status, "detail": "named role scope is required"},
            status_code=400,
        )
    if result.status in {"source-lineage-stale", "source-lineage-unavailable"}:
        return JSONResponse(
            content={
                "status": result.status,
                "detail": result.detail,
                "sourceLineage": (
                    result.source_lineage.model_dump()
                    if result.source_lineage is not None
                    else None
                ),
            },
            status_code=409,
        )
    if result.status == "seat-taken":
        return JSONResponse(
            content={
                "status": "seat-taken",
                "taskDocumentRef": (
                    task_document_ref.model_dump() if task_document_ref is not None else None
                ),
                "session": result.owner_session_id,
            },
            status_code=409,
        )
    return None


def _attach_task_response(
    runtime: _ServingRuntime, session: str, request: TerminalAttachTaskRequest
) -> Response:
    # Trusted dashboard administration: claim or move one document-role binding, no respawn.
    result = assign_terminal_session_to_task(
        TaskAssignmentRuntime(
            runtime.catalog,
            runtime.host,
            TaskDocumentTopology(runtime.config.coordination_root),
        ),
        session_id=session,
        task_document_ref=request.task_document_ref,
        role=request.role,
    )
    if result.status == "unknown-session":
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    if result.status == "seat-taken":
        return JSONResponse(
            content={
                "session": result.owner_session_id,
                "status": "seat-taken",
                "taskDocumentRef": request.task_document_ref.model_dump(),
            },
            status_code=409,
        )
    if result.status == "role-required":
        return JSONResponse(
            content={
                "session": session,
                "status": "role-required",
                "taskDocumentRef": request.task_document_ref.model_dump(),
                "detail": "role is required for a hand-opened harness session",
            },
            status_code=400,
        )
    if result.status == "task-binding-invalid":
        return JSONResponse(
            content={
                "session": session,
                "status": result.status,
                "taskDocumentRef": request.task_document_ref.model_dump(),
                "detail": "task document is missing/invalid or role does not match its altitude",
            },
            status_code=400,
        )
    if result.status in {"source-lineage-stale", "source-lineage-unavailable"}:
        return JSONResponse(
            content={
                "session": session,
                "status": result.status,
                "taskDocumentRef": request.task_document_ref.model_dump(),
                "detail": result.detail,
                "sourceLineage": (
                    result.source_lineage.model_dump()
                    if result.source_lineage is not None
                    else None
                ),
            },
            status_code=409,
        )
    return JSONResponse(
        content={
            "session": session,
            "status": "attached",
            "taskDocumentRef": request.task_document_ref.model_dump(),
            "role": result.role,
            "seatRole": result.seat_role,
            "previousSeatRole": result.previous_seat_role,
        },
        status_code=200,
    )


def _live_paste_target(runtime: _ServingRuntime, session: str) -> TerminalCatalogEntry | None:
    """The running catalog row for one seat, re-proven live, or ``None`` if it is neither."""

    entry = runtime.catalog.get(session)
    if entry is None or entry.status != "running":
        return None
    observation = observe_terminal_liveness(
        runtime.catalog,
        runtime.host,
        entry,
        checked_at=runtime.liveness_clock(),
        probe=LivenessProbe(hysteresis=runtime.liveness_config),
    )
    if not observation.alive or observation.entry.status != "running":
        return None
    return observation.entry


def _harness_submit_response(
    entry: TerminalCatalogEntry,
    session: str,
    request: TerminalPasteRequest,
    *,
    delivery_id: str,
) -> Response:
    """Deliver operator input to a protocol harness as one correlated whole message.

    A harness never takes raw pane input, so a legacy session with no adapter and an unsubmitted
    draft are both refused here rather than degraded to a keystroke path.
    """

    if entry.control_endpoint is None:
        return JSONResponse(
            content={
                "session": session,
                "status": "unsupported",
                "detail": "legacy harness session has no protocol adapter",
            },
            status_code=409,
        )
    if not request.submit:
        return JSONResponse(
            content={
                "session": session,
                "status": "draft-not-submitted",
                "detail": "harness drafts remain on the attached terminal surface",
            },
            status_code=409,
        )
    try:
        receipt = submit_control_prompt(
            entry, request.text, ControlSubmission(source="terminal", request_id=delivery_id)
        )
    except HarnessControlError as exc:
        return JSONResponse(
            content={
                "session": session,
                "entryId": delivery_id,
                "status": "unconfirmed",
                "delivered": False,
                "submitted": True,
                "detail": str(exc),
            },
            status_code=200,
        )
    delivered = receipt.acceptance in {"immediate", "queued"}
    return JSONResponse(
        content={
            "session": session,
            "entryId": delivery_id,
            "status": "delivered" if delivered else "unconfirmed",
            "delivered": delivered,
            "submitted": True,
            "acceptance": receipt.acceptance,
            "vendorCorrelationId": receipt.vendor_correlation_id,
            "acceptedAt": receipt.accepted_at,
            "detail": receipt.detail,
        },
        status_code=200,
    )


def _pane_paste_response(
    runtime: _ServingRuntime,
    entry: TerminalCatalogEntry,
    session: str,
    request: TerminalPasteRequest,
    *,
    delivery_id: str,
) -> Response:
    """Paste into an ordinary terminal's pane; transport is the only thing this path can prove."""

    outcome = runtime.paster.paste(
        entry.tmux_name,
        request.text,
        submit=request.submit,
        accepted=None,
    )
    delivered = outcome.delivered
    content: dict[str, object] = {
        "session": session,
        "entryId": delivery_id,
        "status": "delivered" if delivered else "unconfirmed",
        "delivered": delivered,
        "submitted": request.submit and delivered,
    }
    if not delivered:
        content["capture"] = outcome.capture
    return JSONResponse(content=content, status_code=200)


def _paste_response(
    runtime: _ServingRuntime, session: str, request: TerminalPasteRequest
) -> Response:
    # Explicit operator terminal input. Inter-agent messaging uses the durable inbox route;
    # a protocol harness receives one correlated whole message, never raw pane input.
    entry = _live_paste_target(runtime, session)
    if entry is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    delivery_id = uuid4().hex
    if entry.kind == "harness":
        return _harness_submit_response(entry, session, request, delivery_id=delivery_id)
    return _pane_paste_response(runtime, entry, session, request, delivery_id=delivery_id)


def _terminate_response(runtime: _ServingRuntime, session: str) -> Response:
    entry = runtime.catalog.get(session)
    live = runtime.host.get(session)
    if entry is None and live is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    control_stop_detail = None
    if entry is not None and entry.control_endpoint is not None:
        try:
            stop_control_session(entry)
        except HarnessControlError as exc:
            control_stop_detail = str(exc)
    runtime.host.terminate(session, tmux_name=entry.tmux_name if entry is not None else None)
    terminated_at = now_iso()
    updated = runtime.catalog.mark_terminated(session, terminated_at)
    return JSONResponse(
        content={
            "session": session,
            "status": "terminated",
            "terminatedAt": terminated_at,
            **({"tmuxName": updated.tmux_name} if updated is not None else {}),
            **({"controlStopDetail": control_stop_detail} if control_stop_detail else {}),
        },
        status_code=200,
    )


def _retire_response(
    runtime: _ServingRuntime, session: str, request: TerminalRetireRequest
) -> Response:
    # The server-authoritative retire surface. Never a zombie row --
    # a retire is the SAME terminal mark ``/terminate`` writes, plus retirement provenance
    # (who, why, when, which edge); transcripts are never touched. Authority is enforced here,
    # not trusted from the caller: owner-never-self-retires, a manager retires only its own
    # master's worker/reviewer seats, the orchestrator retires anything.
    target_entry = runtime.catalog.get(session)
    if target_entry is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    actor_entry = runtime.catalog.get(request.actor_session)
    if actor_entry is None:
        return JSONResponse(
            content={"status": "unknown-actor", "actorSession": request.actor_session},
            status_code=404,
        )
    if target_entry.status == "terminated":
        return JSONResponse(
            content={
                "session": session,
                "status": "already-retired",
                "retiredAt": target_entry.retired_at,
            },
            status_code=200,
        )
    try:
        check_retire_authority(
            _seat_ref(actor_entry),
            _seat_ref(target_entry),
            TaskDocumentTopology(runtime.config.coordination_root),
        )
    except RetirePolicyError as exc:
        return JSONResponse(
            content={"session": session, "status": "retire-refused", "detail": str(exc)},
            status_code=403,
        )
    updated = retire_entry(
        runtime.catalog,
        runtime.host,
        target_entry,
        SeatClosure(
            at=now_iso(), by_session=request.actor_session, reason=request.reason, edge="manual"
        ),
    )
    assert updated is not None  # the entry existed above; no concurrent delete path removes rows
    log_retire_event(runtime.config, updated)
    return JSONResponse(
        content={
            "session": session,
            "status": "retired",
            "retiredAt": updated.retired_at,
            "retiredBySession": updated.retired_by_session,
            "retiredReason": updated.retired_reason,
            "retiredEdge": updated.retired_edge,
        },
        status_code=200,
    )


def _seat_ref(entry: TerminalCatalogEntry) -> SeatRef:
    """The document-owned seat facts retire authority is decided from."""

    return SeatRef(
        session_id=entry.id,
        task_document_ref=entry.binding_task_document_ref,
        seat_role=entry.binding_role,
        structural_parent_task_document_ref=entry.structural_parent_task_document_ref,
        structural_parent_role=entry.structural_parent_role,
    )


def _rename_response(runtime: _ServingRuntime, session: str, label: str) -> Response:
    # Post-spawn identity rename. Identity text ONLY -- spawn_role
    # (the immutable seat role) is never touched by a rename.
    entry = runtime.catalog.get(session)
    if entry is None or entry.status == "terminated":
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    updated = runtime.catalog.set_label(session, label)
    assert updated is not None
    log_rename_event(runtime.config, updated)
    return JSONResponse(
        content={
            "session": session,
            "status": "renamed",
            "label": updated.label,
            "spawnedLabel": updated.spawned_label,
        },
        status_code=200,
    )


async def _terminal_image_response(  # pragma: no cover - external upload adapter is integration-tested
    runtime: _ServingRuntime, session: str, request: Request, file: UploadFile
) -> Response:
    # The terminal channel is text-only, so a pasted screenshot is carried by
    # saving it under the session's own cwd and injecting the on-disk path (Claude Code auto-attaches
    # an image path before the model runs). Same localhost posture as the rest of serving/; writes
    # ONLY under the session cwd, with a uuid basename (no traversal) and validated type (extension +
    # magic bytes) + size. Returns the absolute path the composer injects over {type:stdin}.
    # SECURITY NOTE: like the rest of serving/ this is unauthenticated and 127.0.0.1-bound, but unlike
    # the JSON POSTs it is multipart (a CORS "simple request", preflight-free). The write target is
    # keyed by an unguessable client UUID, so cross-origin/CSRF writes can't target a real session;
    # an Origin/Host allowlist for all write routes is folded into the documented remote-auth story.
    session_obj = runtime.host.get(session)
    # L16 fold: the catalog read takes the catalog RLock + a JSON file read; this handler runs
    # on the uvicorn loop, so offload it like every other blocking store read.
    entry = await asyncio.to_thread(runtime.catalog.get, session)
    cwd = session_obj.cwd if session_obj is not None else (entry.cwd if entry else None)
    if cwd is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_IMAGE_BYTES + 4096:
        return JSONResponse(content={"status": "too-large"}, status_code=413)  # fast reject
    ext = Path(file.filename or "").suffix.lstrip(".").lower()
    if ext not in _IMAGE_EXTS:
        return JSONResponse(content={"status": "bad-type"}, status_code=400)
    body = await file.read(_MAX_IMAGE_BYTES + 1)  # +1 so an at-cap read still detects oversize
    if len(body) > _MAX_IMAGE_BYTES:
        return JSONResponse(content={"status": "too-large"}, status_code=413)
    if not body or not _looks_like_image(body, ext):
        return JSONResponse(content={"status": "bad-type"}, status_code=400)  # empty / not an image
    dest = cwd / ".dashboard-pastes" / f"{uuid4().hex}.{ext}"
    # flush before the path is injected -- the harness validates existence; blocking disk I/O
    # stays off the event loop.
    await asyncio.to_thread(_write_paste_image, dest, body)
    return JSONResponse(content={"path": str(dest.resolve())}, status_code=200)


def _write_paste_image(dest: Path, body: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)


def _register_terminal_control_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """Everything that changes a seat: open it, bind it, feed it, rename it, retire it."""

    @app.post("/api/terminal/landed-cleanup", response_model=TerminalCleanupResult)
    def api_terminal_landed_cleanup(request: TerminalLandedCleanupRequest) -> Response:
        return _landed_cleanup_response(runtime, request.session_ids)

    @app.post(
        "/api/terminal/{session}",
        response_model=TerminalOpened,
        responses={
            400: {
                "model": StatusRefusal,
                "description": "Invalid task binding, kind, or launch selection",
            },
            409: {
                "model": SeatTakenConflict | TerminalLaunchConflict,
                "description": "The task role is taken, or the seat was launched differently",
            },
        },
    )
    def api_terminal_open(session: str, request: TerminalOpenRequest) -> Response:
        return _open_terminal_response(runtime, session, request)

    @app.post(
        "/api/terminal/{session}/attach-task",
        response_model=TerminalTaskAttached,
        responses={
            400: {
                "model": TerminalTaskRefused,
                "description": "Invalid task binding, or a hand-opened seat with no role",
            },
            404: {"model": UnknownSessionRefusal, "description": "No such session"},
            409: {"model": TerminalTaskRefused, "description": "The task role is already held"},
        },
    )
    def api_terminal_attach_task(session: str, request: TerminalAttachTaskRequest) -> Response:
        return _attach_task_response(runtime, session, request)

    # Two success shapes, because a protocol harness and a plain pane can prove different
    # things: the harness path returns submission evidence, the pane path only transport.
    @app.post(
        "/api/terminal/{session}/paste",
        response_model=TerminalHarnessDelivery | TerminalPaneDelivery,
        responses={
            404: {"model": UnknownSessionRefusal, "description": "No live session"},
            409: {
                "model": TerminalHarnessRefusal,
                "description": "A legacy harness seat, or an unsubmitted draft",
            },
        },
    )
    def api_terminal_paste(session: str, request: TerminalPasteRequest) -> Response:
        return _paste_response(runtime, session, request)

    @app.post(
        "/api/terminal/{session}/terminate",
        response_model=TerminalTerminated,
        responses={404: {"model": UnknownSessionRefusal, "description": "No such session"}},
    )
    def api_terminal_terminate(session: str) -> Response:
        return _terminate_response(runtime, session)

    # Retiring an already-terminal seat is idempotent, not an error, so the 200 carries two
    # shapes; authority refusal is the only 403 on the whole app surface.
    @app.post(
        "/api/terminal/{session}/retire",
        response_model=TerminalRetired | TerminalAlreadyRetired,
        responses={
            403: {"model": TerminalRetireRefused, "description": "Retire authority refused"},
            404: {
                "model": UnknownSessionRefusal | UnknownActorRefusal,
                "description": "The target or the actor session is unknown",
            },
        },
    )
    def api_terminal_retire(session: str, request: TerminalRetireRequest) -> Response:
        return _retire_response(runtime, session, request)

    @app.post(
        "/api/terminal/{session}/rename",
        response_model=TerminalRenamed,
        responses={404: {"model": UnknownSessionRefusal, "description": "No such live session"}},
    )
    def api_terminal_rename(session: str, request: TerminalRenameRequest) -> Response:
        return _rename_response(runtime, session, request.label)

    @app.post(
        "/api/terminal/{session}/image",
        response_model=TerminalImageStored,
        responses={
            400: {"model": TerminalImageRefusal, "description": "Not an accepted image type"},
            404: {"model": UnknownSessionRefusal, "description": "No such session"},
            413: {"model": TerminalImageRefusal, "description": "Over the per-image cap"},
        },
    )
    async def api_terminal_image(
        session: str, request: Request, file: Annotated[UploadFile, File()]
    ) -> Response:
        return await _terminal_image_response(runtime, session, request, file)
