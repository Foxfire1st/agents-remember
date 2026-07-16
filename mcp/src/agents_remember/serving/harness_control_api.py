"""Harness-neutral daemon routes for advertise, live set, and reliable submit."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_capabilities import (
    capability_snapshot_json,
    set_result_json,
)
from agents_remember.serving.harness_capability_catalog import (
    HarnessCapabilityCatalog,
    HarnessCapabilityLookupError,
)
from agents_remember.serving.harness_control_adapter import BUILTIN_PROTOCOL_HARNESSES
from agents_remember.serving.harness_control_client import (
    read_control_capabilities,
    reconcile_control_prompt,
    set_control_effort,
    set_control_model,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_models import (
    public_receipt_json,
    public_reconciliation_json,
)
from agents_remember.serving.harness_launch import ResolvedLaunch, resolve_settings_launch
from agents_remember.serving.harnesses import Harness
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalLivenessHost,
    observe_terminal_liveness,
)

HarnessRegistry = Callable[[], Sequence[Harness]]
Clock = Callable[[], datetime]


class HarnessSetModelRequest(BaseModel):
    model: str = Field(min_length=1)


class HarnessSetEffortRequest(BaseModel):
    effort: str = Field(min_length=1)


class HarnessSubmitRequest(BaseModel):
    request_id: str = Field(alias="requestId", min_length=1)
    text: str = Field(min_length=1)


class HarnessReconcileRequest(BaseModel):
    request_id: str = Field(alias="requestId", min_length=1)


def resolve_terminal_open_selection(
    *,
    kind: str,
    harness: str | None,
    model: str | None,
    effort: str | None,
    workspace: Path,
) -> ResolvedLaunch | None:
    """Translate a complete dashboard pair into L2's single native launch selection."""

    if model is None and effort is None:
        return None
    if kind != "harness" or harness not in BUILTIN_PROTOCOL_HARNESSES:
        raise HarnessControlError(
            "model/effort launch selection requires an AR native harness session"
        )
    if model is None or effort is None:
        raise HarnessControlError("model and effort must be provided together")
    return resolve_settings_launch(
        harness_id=harness,
        model=model,
        effort=effort,
        workspace=workspace,
    )


def register_harness_control_routes(
    app: FastAPI,
    *,
    workspace_root: Path,
    harness_registry: HarnessRegistry,
    catalog: TerminalCatalog,
    host: TerminalLivenessHost,
    liveness_clock: Clock,
    liveness_config: TerminalCatalogLivenessConfig,
    capability_catalog: HarnessCapabilityCatalog | None = None,
) -> None:
    """Register request/response control routes; async output remains on existing streams."""

    pre_session = capability_catalog or HarnessCapabilityCatalog(workspace_root)

    @app.get("/api/harnesses/{harness}/capabilities")
    async def api_harness_capabilities(harness: str, refresh: bool = False) -> JSONResponse:
        try:
            result = await pre_session.get(
                harness,
                registry=harness_registry(),
                refresh=refresh,
            )
        except HarnessCapabilityLookupError as exc:
            return JSONResponse(
                content={"status": "capability-unavailable", "detail": str(exc)},
                status_code=exc.status_code,
            )
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=result.to_json(), status_code=200)

    @app.get("/api/terminal/{session}/capabilities")
    def api_terminal_capabilities(session: str) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            snapshot = read_control_capabilities(entry_or_error)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=capability_snapshot_json(snapshot), status_code=200)

    @app.post("/api/terminal/{session}/set-model")
    def api_terminal_set_model(session: str, request: HarnessSetModelRequest) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            result = set_control_model(entry_or_error, request.model)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=set_result_json(result), status_code=200)

    @app.post("/api/terminal/{session}/set-effort")
    def api_terminal_set_effort(session: str, request: HarnessSetEffortRequest) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            result = set_control_effort(entry_or_error, request.effort)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=set_result_json(result), status_code=200)

    @app.post("/api/terminal/{session}/submit")
    def api_terminal_submit(session: str, request: HarnessSubmitRequest) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            receipt = submit_control_prompt(
                entry_or_error,
                request.text,
                source="terminal",
                request_id=request.request_id,
            )
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=public_receipt_json(receipt), status_code=200)

    @app.post("/api/terminal/{session}/reconcile")
    def api_terminal_reconcile(session: str, request: HarnessReconcileRequest) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            result = reconcile_control_prompt(entry_or_error, request.request_id)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=public_reconciliation_json(result), status_code=200)


def _running_control_entry(
    session: str,
    *,
    catalog: TerminalCatalog,
    host: TerminalLivenessHost,
    checked_at: datetime,
    liveness_config: TerminalCatalogLivenessConfig,
) -> TerminalCatalogEntry | JSONResponse:
    entry = catalog.get(session)
    if entry is None or entry.status != "running":
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    observation = observe_terminal_liveness(
        catalog,
        host,
        entry,
        checked_at=checked_at,
        config=liveness_config,
    )
    if not observation.alive or observation.entry.status != "running":
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    live_entry = observation.entry
    if live_entry.kind != "harness" or live_entry.control_endpoint is None:
        return JSONResponse(
            content={
                "status": "unsupported",
                "detail": "session has no native protocol control endpoint",
            },
            status_code=409,
        )
    return live_entry


def _control_unavailable(error: HarnessControlError) -> JSONResponse:
    return JSONResponse(
        content={"status": "control-unavailable", "detail": str(error)},
        status_code=503,
    )
