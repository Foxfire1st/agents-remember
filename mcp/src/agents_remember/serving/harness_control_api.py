"""Harness-neutral daemon routes for advertise, live set, and reliable submit."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlClientError,
    HarnessControlError,
    HarnessInteractionNotPendingError,
    HarnessRequestConflictError,
)
from agents_remember.serving.conversation import register_conversation_routes
from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    ConversationScope,
)
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
    read_submission_authority,
    read_submission_status,
    reconcile_control_prompt,
    respond_control_interaction,
    set_control_effort,
    set_control_model,
    submit_control_prompt,
    withdraw_control_submission,
)
from agents_remember.serving.harness_control_models import (
    pending_interaction_json,
    public_receipt_json,
    public_reconciliation_json,
    submission_authority_json,
    submission_status_batch_json,
    withdrawal_result_json,
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
    expected_bridge_epoch: str = Field(alias="expectedBridgeEpoch", min_length=1)


class HarnessReconcileRequest(BaseModel):
    request_id: str = Field(alias="requestId", min_length=1)
    expected_bridge_epoch: str = Field(alias="expectedBridgeEpoch", min_length=1)


class HarnessSubmissionStatusRequest(BaseModel):
    expected_bridge_epoch: str = Field(alias="expectedBridgeEpoch", min_length=1)
    request_ids: list[str] = Field(alias="requestIds", min_length=1, max_length=64)

    @field_validator("request_ids")
    @classmethod
    def unique_request_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("requestIds must be unique")
        if any(not item for item in value):
            raise ValueError("requestIds must be non-empty")
        return value


class HarnessWithdrawRequest(BaseModel):
    expected_bridge_epoch: str = Field(alias="expectedBridgeEpoch", min_length=1)
    request_id: str = Field(alias="requestId", min_length=1)


class HarnessInteractionResponseRequest(BaseModel):
    """One session-direct interaction answer; no lifecycle is required anywhere.

    ``response`` is the permission-kind payload (``allow``/``deny``); ``answers`` is the
    structured user-input payload mapping every question's exact text to its answer, sent
    to the adapter as the same JSON answers map the gate channel validates.
    """

    interaction_id: str = Field(alias="interactionId", min_length=1)
    expected_bridge_epoch: str = Field(alias="expectedBridgeEpoch", min_length=1)
    response: str | None = Field(default=None, min_length=1)
    answers: dict[str, str] | None = None

    @field_validator("answers")
    @classmethod
    def non_empty_answers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("answers must answer at least one question")
        if any(not question.strip() or not answer.strip() for question, answer in value.items()):
            raise ValueError("answers must map non-empty question text to non-empty answers")
        return value

    @model_validator(mode="after")
    def exactly_one_payload(self) -> HarnessInteractionResponseRequest:
        if (self.response is None) == (self.answers is None):
            raise ValueError("exactly one of response or answers is required")
        return self


def resolve_terminal_open_selection(
    *,
    kind: str,
    harness: str | None,
    model: str | None,
    effort: str | None,
    workspace: Path,
) -> ResolvedLaunch | None:
    """Translate a complete dashboard pair into a single native launch selection."""

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
    coordination_root: Path,
    harness_registry: HarnessRegistry,
    catalog: TerminalCatalog,
    host: TerminalLivenessHost,
    liveness_clock: Clock,
    liveness_config: TerminalCatalogLivenessConfig,
    capability_catalog: HarnessCapabilityCatalog | None = None,
) -> None:
    """Register request/response control routes; async output remains on existing streams."""

    pre_session = capability_catalog or HarnessCapabilityCatalog(workspace_root)
    # One-time composition binding: the immutable app-scoped conversation
    # runtime is installed exactly once, here, while every existing authority is
    # already in hand. Downstream call sites consume it through the request dependencies
    # and never edit this registration again.
    conversation_runtime = ConversationRuntime(
        scope=ConversationScope(
            workspace_root=workspace_root,
            coordination_root=coordination_root,
        ),
        catalog=catalog,
        host=host,
        harness_registry=harness_registry,
        liveness_clock=liveness_clock,
        liveness_config=liveness_config,
        capability_catalog=pre_session,
        authorization=LocalOperatorAuthorizationResolver.for_workspace(workspace_root),
    )
    register_conversation_routes(app, conversation_runtime)
    # One memo per app registration: the control routes below share it, and a fresh app (tests,
    # a restarted daemon) starts with an empty one.
    liveness_memo = _ControlLivenessMemo()

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
            liveness_memo=liveness_memo,
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
            liveness_memo=liveness_memo,
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
            liveness_memo=liveness_memo,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            result = set_control_effort(entry_or_error, request.effort)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=set_result_json(result), status_code=200)

    @app.get("/api/terminal/{session}/submission-authority")
    def api_submission_authority(session: str) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
            liveness_memo=liveness_memo,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            descriptor = read_submission_authority(entry_or_error)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=submission_authority_json(descriptor), status_code=200)

    @app.post("/api/terminal/{session}/submission-status")
    def api_submission_status(
        session: str,
        request: HarnessSubmissionStatusRequest,
    ) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
            liveness_memo=liveness_memo,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            status = read_submission_status(
                entry_or_error,
                expected_bridge_epoch=request.expected_bridge_epoch,
                request_ids=tuple(request.request_ids),
            )
        except HarnessBridgeEpochMismatchError as exc:
            return _bridge_epoch_mismatch(exc)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=submission_status_batch_json(status), status_code=200)

    @app.post("/api/terminal/{session}/withdraw")
    def api_withdraw_submission(
        session: str,
        request: HarnessWithdrawRequest,
    ) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
            liveness_memo=liveness_memo,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            result = withdraw_control_submission(
                entry_or_error,
                expected_bridge_epoch=request.expected_bridge_epoch,
                request_id=request.request_id,
            )
        except HarnessBridgeEpochMismatchError as exc:
            return _bridge_epoch_mismatch(exc)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=withdrawal_result_json(result), status_code=200)

    @app.post("/api/terminal/{session}/submit")
    def api_terminal_submit(session: str, request: HarnessSubmitRequest) -> JSONResponse:
        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
            liveness_memo=liveness_memo,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            receipt = submit_control_prompt(
                entry_or_error,
                request.text,
                source="cockpit",
                request_id=request.request_id,
                expected_bridge_epoch=request.expected_bridge_epoch,
            )
        except HarnessBridgeEpochMismatchError as exc:
            return _bridge_epoch_mismatch(exc)
        except HarnessRequestConflictError as exc:
            return JSONResponse(
                content={"status": "request-id-conflict", "detail": str(exc)},
                status_code=409,
            )
        except HarnessControlClientError as exc:
            if not exc.may_have_sent:
                return _pre_dispatch_submit_failure(exc)
            return _control_unavailable(exc)
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
            liveness_memo=liveness_memo,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            result = reconcile_control_prompt(
                entry_or_error,
                request.request_id,
                expected_bridge_epoch=request.expected_bridge_epoch,
            )
        except HarnessBridgeEpochMismatchError as exc:
            return _bridge_epoch_mismatch(exc)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(content=public_reconciliation_json(result), status_code=200)

    @app.post("/api/terminal/{session}/interaction-response")
    def api_terminal_interaction_response(
        session: str, request: HarnessInteractionResponseRequest
    ) -> JSONResponse:
        """Session-direct answer to one pending vendor interaction.

        This is the lifecycle-free answer channel: the durable gate channel can only project
        and apply decisions under a lifecycle, so a seat without one could never be answered.
        The epoch guard is the same authority-descriptor pre-check the other control routes
        enforce; the exact interaction id remains the respond authority's own guard.
        """

        entry_or_error = _running_control_entry(
            session,
            catalog=catalog,
            host=host,
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
            liveness_memo=liveness_memo,
        )
        if isinstance(entry_or_error, JSONResponse):
            return entry_or_error
        try:
            authority = read_submission_authority(entry_or_error)
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        if authority.bridge_epoch != request.expected_bridge_epoch:
            return _bridge_epoch_mismatch(
                HarnessBridgeEpochMismatchError(
                    request.expected_bridge_epoch, authority.bridge_epoch
                )
            )
        response_text = (
            request.response
            if request.response is not None
            else json.dumps(request.answers, ensure_ascii=False)
        )
        try:
            snapshot = respond_control_interaction(
                entry_or_error,
                interaction_id=request.interaction_id,
                response=response_text,
            )
        except HarnessInteractionNotPendingError as exc:
            return JSONResponse(
                content={"status": "not-pending", "detail": str(exc)},
                status_code=409,
            )
        except HarnessControlError as exc:
            return _control_unavailable(exc)
        return JSONResponse(
            content={
                "status": "accepted",
                "activity": snapshot.activity,
                "acceptance": snapshot.acceptance,
                "pendingInteraction": pending_interaction_json(snapshot.pending_interaction),
                # Multiplexed sub-agent pendings: additive.
                "pendingInteractions": [
                    pending_interaction_json(pending) for pending in snapshot.pending_interactions
                ],
            },
            status_code=200,
        )


CONTROL_LIVENESS_MEMO_TTL_SECONDS = 2.5
"""Reuse a fresh alive control-route observation instead of re-probing on every request."""

CONTROL_LIVENESS_MEMO_MAX_ENTRIES = 64
"""Hard ceiling on memoized seats -- far above any plausible count controlled inside one TTL."""


class _ControlLivenessMemo:
    """Short-TTL in-process memo of alive harness-with-endpoint observations.

    Every control route paid full liveness pre-work (tmux has-session + capture-pane subprocesses,
    an IPC snapshot read, and a full-catalog JSON upsert) before its real work, so one submit
    cluster (authority -> submit -> status) repeated that per request. Only alive/running
    harness-with-endpoint observations enter the memo -- failures never do, so failure hysteresis
    is untouched, the 10s background sweep remains the reconciler, and the control IPC itself is
    the stronger liveness probe (a dead bridge still fails the request with a typed 503).
    Trade-off: the hosted interaction synchronizer gets fewer control-route observations (at most
    one per TTL per session; its sweep cadence is unchanged). The lock serializes the FastAPI
    threadpool's get/check/put sequences, matching the catalog's own threading idiom.
    """

    def __init__(self, *, max_entries: int = CONTROL_LIVENESS_MEMO_MAX_ENTRIES) -> None:
        self._entries: dict[str, tuple[datetime, TerminalCatalogEntry]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, session: str, *, at: datetime) -> TerminalCatalogEntry | None:
        with self._lock:
            memoized = self._entries.get(session)
            if memoized is None:
                return None
            fresh_until, entry = memoized
            if at >= fresh_until:
                del self._entries[session]
                return None
            return entry

    def put(self, session: str, *, at: datetime, entry: TerminalCatalogEntry) -> None:
        """Memoize one alive observation and reclaim the rows no route can reach any more.

        ``get`` drops a row once it expires, but a seat that leaves ``running``
        never reaches ``get`` again -- ``_running_control_entry`` 404s on the catalog status first --
        so its whole ``TerminalCatalogEntry`` (``control_raw`` included) would be stranded for the
        daemon's lifetime. ``put`` is the memo's only growth point, so reclaiming here bounds the
        dict at every instant, on a path that has just paid for a tmux probe and an IPC snapshot.
        """

        fresh_until = at + timedelta(seconds=CONTROL_LIVENESS_MEMO_TTL_SECONDS)
        with self._lock:
            self._entries[session] = (fresh_until, entry)
            self._reclaim_locked(at=at)

    def _reclaim_locked(self, *, at: datetime) -> None:
        """Drop expired rows, then the oldest survivors while the cap is still exceeded.

        Mirrors ``TerminalCatalog.compact``: expiry is the real reclaimer and the cap is only the
        backstop for a burst of seats memoized inside one TTL window. The row closest to expiry is
        the cheapest to lose -- it buys the least remaining re-probe relief -- and under the
        daemon's forward-moving liveness clock the row just stored holds the newest ``fresh_until``,
        so a ``put`` does not evict its own observation. An overflow eviction only costs the next
        request for that seat one full observation; it never asserts anything about liveness.
        """

        live = {
            session: memoized for session, memoized in self._entries.items() if at < memoized[0]
        }
        if len(live) > self._max_entries:
            by_expiry = sorted(live.items(), key=lambda item: item[1][0])
            live = dict(by_expiry[len(by_expiry) - self._max_entries :])
        self._entries = live


def _running_control_entry(
    session: str,
    *,
    catalog: TerminalCatalog,
    host: TerminalLivenessHost,
    checked_at: datetime,
    liveness_config: TerminalCatalogLivenessConfig,
    liveness_memo: _ControlLivenessMemo,
) -> TerminalCatalogEntry | JSONResponse:
    entry = catalog.get(session)
    if entry is None or entry.status != "running":
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    memoized = liveness_memo.get(session, at=checked_at)
    if memoized is not None:
        return memoized
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
    liveness_memo.put(session, at=checked_at, entry=live_entry)
    return live_entry


def _control_unavailable(error: HarnessControlError) -> JSONResponse:
    return JSONResponse(
        content={"status": "control-unavailable", "detail": str(error)},
        status_code=503,
    )


def _bridge_epoch_mismatch(error: HarnessBridgeEpochMismatchError) -> JSONResponse:
    return JSONResponse(
        content={
            "status": "bridge-epoch-mismatch",
            "expectedBridgeEpoch": error.expected,
            "actualBridgeEpoch": error.actual,
            "detail": str(error),
        },
        status_code=409,
    )


def _pre_dispatch_submit_failure(error: HarnessControlClientError) -> JSONResponse:
    """Expose retry safety only for the control client that certified zero socket bytes."""

    return JSONResponse(
        content={
            "status": "pre-dispatch-failed",
            "detail": str(error),
            "retrySafe": True,
            "stage": "control-ipc",
        },
        status_code=503,
    )
