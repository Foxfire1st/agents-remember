from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from _agent_wire_fixtures import turn_completed_params, turn_started_params
from agents_remember.errors import (
    CodexAppServerError,
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
)
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject, RequestId
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ShutdownMode,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "codex_app_server_0_144_3.json"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeCodexTransport:
    def __init__(self) -> None:
        self.responses: dict[str, deque[JsonObject | Exception]] = defaultdict(deque)
        self.requests: list[tuple[str, JsonObject]] = []
        self.notifications: list[tuple[str, JsonObject]] = []
        self.server_responses: list[tuple[RequestId, JsonObject]] = []
        self.server_errors: list[tuple[RequestId, int, str]] = []
        self.launches: list[LaunchSpec] = []
        self.stop_modes: list[ShutdownMode] = []
        self.incoming: asyncio.Queue[JsonObject | Exception | None] = asyncio.Queue()
        self.before_write_hook: Callable[[str, Mapping[str, object]], None] | None = None

    async def start(self, launch: LaunchSpec) -> None:
        self.launches.append(launch)

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: Callable[[], None] | None = None,
    ) -> JsonObject:
        if self.before_write_hook is not None:
            self.before_write_hook(method, params)
        if before_write is not None:
            before_write()
        self.requests.append((method, dict(params)))
        response = self.responses[method].popleft()
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        self.notifications.append((method, dict(params)))

    async def _messages(self) -> AsyncIterator[JsonObject]:
        while True:
            message = await self.incoming.get()
            if message is None:
                return
            if isinstance(message, Exception):
                raise message
            yield message

    def messages(self) -> AsyncIterator[JsonObject]:
        return self._messages()

    async def respond(self, request_id: RequestId, result: Mapping[str, object]) -> None:
        self.server_responses.append((request_id, dict(result)))

    async def respond_error(self, request_id: RequestId, *, code: int, message: str) -> None:
        self.server_errors.append((request_id, code, message))

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.incoming.put_nowait(None)

    def queue_response(self, method: str, response: JsonObject | Exception) -> None:
        self.responses[method].append(response)

    def emit(self, message: JsonObject) -> None:
        self.incoming.put_nowait(deepcopy(message))


class BlockingTurnStartTransport(FakeCodexTransport):
    def __init__(self) -> None:
        super().__init__()
        self.turn_start_requested = asyncio.Event()
        self.release_turn_start = asyncio.Event()

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: Callable[[], None] | None = None,
    ) -> JsonObject:
        if method != "turn/start":
            return await super().request(method, params, before_write=before_write)
        if self.before_write_hook is not None:
            self.before_write_hook(method, params)
        if before_write is not None:
            before_write()
        self.requests.append((method, dict(params)))
        self.turn_start_requested.set()
        await self.release_turn_start.wait()
        response = self.responses[method].popleft()
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def fixture() -> JsonObject:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def fixture_object(data: Mapping[str, object], *path: str) -> JsonObject:
    value: object = data
    for key in path:
        assert isinstance(value, dict)
        value = value[key]
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def fixture_list(data: Mapping[str, object], *path: str) -> list[object]:
    value: object = data
    for key in path:
        assert isinstance(value, dict)
        value = value[key]
    assert isinstance(value, list)
    return value


def add_model(
    data: JsonObject,
    *,
    model: str = "gpt-5.6-mini",
    efforts: tuple[str, ...] = ("low", "medium"),
    default_effort: str = "medium",
) -> None:
    fixture_list(data, "modelListResult", "data").append(
        {
            "id": f"model-{model}",
            "model": model,
            "displayName": "GPT-5.6 Mini",
            "description": "Second fixture model",
            "hidden": False,
            "isDefault": False,
            "defaultReasoningEffort": default_effort,
            "supportedReasoningEfforts": [
                {"reasoningEffort": effort, "description": effort.title()} for effort in efforts
            ],
        }
    )


def identity() -> ControlIdentity:
    return ControlIdentity(
        ar_session_id="ar-session-1",
        tmux_name="ar-codex-1",
        created_at="2026-07-14T12:00:00+00:00",
    )


def launch() -> LaunchSpec:
    return LaunchSpec(
        identity=identity(),
        harness_id="codex",
        cwd=Path("/workspace"),
        argv=("codex", "app-server"),
        env={"PRESERVE_INSTALLED_AUTH": "1"},
    )


def request(request_id: str, text: str = "hello") -> PromptRequest:
    return PromptRequest(
        request_id=request_id,
        source="durable",
        text=text,
        submitted_at="2026-07-14T12:01:00+00:00",
        operation=ControlOperationRef(
            bridge_epoch="codex-test-epoch",
            sequence=1,
            operation_id=request_id,
            kind="prompt",
        ),
    )


def prime_start(
    transport: FakeCodexTransport,
    data: JsonObject,
    *,
    resume: bool = False,
) -> None:
    transport.queue_response("initialize", fixture_object(data, "initializeResult"))
    transport.queue_response("model/list", fixture_object(data, "modelListResult"))
    method = "thread/resume" if resume else "thread/start"
    key = "threadResumeResult" if resume else "threadStartResult"
    transport.queue_response(method, fixture_object(data, key))


def make_adapter(
    transport: FakeCodexTransport,
    *,
    resume_thread_id: str | None = None,
    approval_policy: object | None = None,
    approvals_reviewer: str | None = None,
    sandbox: str | None = None,
    turn_sandbox_policy: Mapping[str, object] | None = None,
    config: Mapping[str, object] | None = None,
    submission_limit: int = 256,
) -> CodexAppServerAdapter:
    settings = CodexAppServerSettings(
        reasoning_effort="xhigh",
        model="gpt-5.6-sol",
        ephemeral=True,
        resume_thread_id=resume_thread_id,
        approval_policy=approval_policy,
        approvals_reviewer=approvals_reviewer,
        sandbox=sandbox,
        turn_sandbox_policy=turn_sandbox_policy,
        config=config or {},
        submission_limit=submission_limit,
    )
    return CodexAppServerAdapter(
        settings,
        transport_factory=lambda: transport,
        clock=lambda: "2026-07-14T12:02:00+00:00",
    )


async def settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def turn_start_result(data: JsonObject, turn_id: str, status: str = "inProgress") -> JsonObject:
    result = deepcopy(fixture_object(data, "turnStartResult"))
    turn = fixture_object(result, "turn")
    turn["id"] = turn_id
    turn["status"] = status
    return result


def turn_completed_notification(data: JsonObject, turn_id: str) -> JsonObject:
    notification = deepcopy(fixture_object(data, "notifications", "completed"))
    fixture_object(notification, "params", "turn")["id"] = turn_id
    return notification


@pytest.mark.anyio
async def test_handshake_uses_stable_protocol_and_exposes_effort_menu() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(
        transport,
        approval_policy="on-request",
        approvals_reviewer="user",
        sandbox="workspace-write",
        turn_sandbox_policy={"type": "workspaceWrite", "writableRoots": ["/workspace"]},
        config={"feature_flag": True},
    )

    handshake = await adapter.start(launch())
    try:
        assert handshake.adapter_id == "codex-app-server:0.144.3"
        assert handshake.snapshot.vendor_session_id == "thread-1"
        assert handshake.snapshot.raw["advertisedReasoningEfforts"] == [
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
        ]
        assert handshake.snapshot.raw["defaultReasoningEffort"] == "low"
        assert handshake.snapshot.raw["effectiveReasoningEffort"] == "xhigh"
        request_count = len(transport.requests)
        advertised = adapter.advertise()
        assert advertised.selected_model_key == "gpt-5.6-sol"
        assert advertised.selected_effort == "xhigh"
        assert advertised.models[0].display_name == "GPT-5.6 Sol"
        assert advertised.models[0].description
        assert advertised.models[0].effort_options[0].description
        assert len(transport.requests) == request_count
        assert transport.notifications == [("initialized", {})]
        assert [method for method, _ in transport.requests] == [
            "initialize",
            "model/list",
            "thread/start",
        ]
        initialize_params = transport.requests[0][1]
        assert initialize_params["capabilities"] == {"experimentalApi": True}
        thread_params = transport.requests[2][1]
        assert thread_params["config"] == {
            "feature_flag": True,
            "model": "gpt-5.6-sol",
            "model_reasoning_effort": "xhigh",
        }
        assert thread_params["model"] == "gpt-5.6-sol"
        assert thread_params["cwd"] == "/workspace"
        assert launch().argv == ("codex", "app-server")
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_roleless_start_uses_dynamic_model_and_model_local_effort_defaults() -> None:
    data = fixture()
    fixture_object(data, "threadStartResult")["reasoningEffort"] = "low"
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = CodexAppServerAdapter(
        CodexAppServerSettings(ephemeral=True),
        transport_factory=lambda: transport,
    )

    handshake = await adapter.start(launch())
    try:
        assert handshake.snapshot.control == "ready"
        assert handshake.snapshot.raw["model"] == "gpt-5.6-sol"
        assert handshake.snapshot.raw["desiredReasoningEffort"] == "low"
        assert handshake.snapshot.raw["effectiveReasoningEffort"] == "low"
        thread_params = transport.requests[-1][1]
        assert thread_params["model"] == "gpt-5.6-sol"
        assert thread_params["config"] == {
            "model": "gpt-5.6-sol",
            "model_reasoning_effort": "low",
        }
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_discover_retains_paginated_hidden_catalog_without_opening_a_thread() -> None:
    data = fixture()
    first_page = fixture_object(data, "modelListResult")
    first_page["nextCursor"] = "page-2"
    first_model = cast(JsonObject, fixture_list(first_page, "data")[0])
    hidden_model = deepcopy(first_model)
    hidden_model.update(
        {
            "id": "model-hidden",
            "model": "gpt-hidden",
            "displayName": "Hidden Model",
            "description": "Installed but hidden model",
            "hidden": True,
            "isDefault": False,
            "defaultReasoningEffort": "low",
            "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "Low only"}],
        }
    )
    transport = FakeCodexTransport()
    transport.queue_response("initialize", fixture_object(data, "initializeResult"))
    transport.queue_response("model/list", first_page)
    transport.queue_response(
        "model/list",
        {"data": [hidden_model], "nextCursor": None},
    )
    adapter = make_adapter(transport)

    advertised = await adapter.discover(launch())

    assert [model.key for model in advertised.models] == ["gpt-5.6-sol", "gpt-hidden"]
    assert advertised.models[1].hidden is True
    assert [option.key for option in advertised.models[1].effort_options] == ["low"]
    assert advertised.selected_model_key is None
    assert advertised.selected_effort is None
    assert [method for method, _ in transport.requests] == [
        "initialize",
        "model/list",
        "model/list",
    ]
    assert transport.requests[1][1] == {"includeHidden": True}
    assert transport.requests[2][1] == {"includeHidden": True, "cursor": "page-2"}
    assert not any(
        method.startswith("thread/") or method.startswith("turn/")
        for method, _ in transport.requests
    )
    assert transport.stop_modes == ["forced"]


@pytest.mark.anyio
async def test_discover_rejects_repeated_model_cursor_without_opening_a_thread() -> None:
    data = fixture()
    page = fixture_object(data, "modelListResult")
    page["nextCursor"] = "repeated"
    transport = FakeCodexTransport()
    transport.queue_response("initialize", fixture_object(data, "initializeResult"))
    transport.queue_response("model/list", page)
    transport.queue_response("model/list", page)
    adapter = make_adapter(transport)

    with pytest.raises(CodexAppServerError, match="repeated a pagination cursor"):
        await adapter.discover(launch())

    assert not any(method.startswith("thread/") for method, _ in transport.requests)
    assert transport.stop_modes == ["forced"]


@pytest.mark.anyio
async def test_compatible_patch_version_is_accepted_after_capability_negotiation() -> None:
    data = fixture()
    initialize = fixture_object(data, "initializeResult")
    initialize["userAgent"] = str(initialize["userAgent"]).replace("0.144.3", "0.144.4")
    fixture_object(data, "threadStartResult", "thread")["cliVersion"] = "0.144.4"
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)

    handshake = await adapter.start(launch())
    try:
        assert handshake.snapshot.control == "ready"
        assert handshake.adapter_id == "codex-app-server:0.144.4"
        assert handshake.raw["protocol"] == "codex-app-server/0.144.4"
        assert handshake.raw["codexCliVersion"] == "0.144.4"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_missing_initialize_field_and_version_identity_mismatch_fail_loudly() -> None:
    missing_data = fixture()
    fixture_object(missing_data, "initializeResult").pop("platformOs")
    missing_transport = FakeCodexTransport()
    prime_start(missing_transport, missing_data)
    with pytest.raises(CodexAppServerError, match="requires non-empty platformOs"):
        await make_adapter(missing_transport).start(launch())
    assert missing_transport.stop_modes == ["forced"]

    mismatch_data = fixture()
    initialize = fixture_object(mismatch_data, "initializeResult")
    initialize["userAgent"] = str(initialize["userAgent"]).replace("0.144.3", "0.144.4")
    mismatch_transport = FakeCodexTransport()
    prime_start(mismatch_transport, mismatch_data)
    with pytest.raises(CodexAppServerError, match="differs from negotiated initialize version"):
        await make_adapter(mismatch_transport).start(launch())
    assert mismatch_transport.stop_modes == ["forced"]


@pytest.mark.anyio
async def test_absent_or_unconfirmed_effort_fails_loudly() -> None:
    data = fixture()
    absent_transport = FakeCodexTransport()
    prime_start(absent_transport, data)
    absent = CodexAppServerAdapter(
        CodexAppServerSettings(model="gpt-5.6-sol", reasoning_effort="impossible"),
        transport_factory=lambda: absent_transport,
    )
    with pytest.raises(CodexAppServerError, match="advertised options: low, medium, high, xhigh"):
        await absent.start(launch())

    mismatch_data = fixture()
    fixture_object(mismatch_data, "threadStartResult")["reasoningEffort"] = "high"
    mismatch_transport = FakeCodexTransport()
    prime_start(mismatch_transport, mismatch_data)
    mismatch = make_adapter(mismatch_transport)
    with pytest.raises(CodexAppServerError, match="echoed reasoning effort 'high'"):
        await mismatch.start(launch())


@pytest.mark.anyio
async def test_resume_preserves_exact_thread_and_effective_settings() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data, resume=True)
    adapter = make_adapter(transport, resume_thread_id="thread-1")
    handshake = await adapter.start(launch())
    try:
        assert handshake.snapshot.vendor_session_id == "thread-1"
        assert [method for method, _ in transport.requests][-1] == "thread/resume"
        assert transport.requests[-1][1]["threadId"] == "thread-1"
        assert transport.requests[-1][1]["config"] == {
            "model": "gpt-5.6-sol",
            "model_reasoning_effort": "xhigh",
        }
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "outcome"),
    [("completed", "completed"), ("interrupted", "cancelled"), ("failed", "failed")],
)
async def test_turn_acceptance_blocking_and_terminal_mapping(status: str, outcome: str) -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        receipt = await adapter.submit(request("request-1"))
        assert receipt.acceptance == "immediate"
        assert receipt.vendor_correlation_id == "turn-1"
        assert transport.requests[-1][1]["clientUserMessageId"] == "request-1"
        assert transport.requests[-1][1]["effort"] == "xhigh"

        transport.emit(fixture_object(data, "notifications", "blocked"))
        await settle()
        assert (await adapter.snapshot()).activity == "blocked"

        terminal = deepcopy(fixture_object(data, "notifications", status))
        transport.emit(terminal)
        while True:
            event = await asyncio.wait_for(anext(events), timeout=1.0)
            if event.kind == "completed":
                break
        assert event.transcript[0].terminal_result is not None
        assert event.transcript[0].terminal_result.outcome == outcome
        assert event.snapshot is not None
        assert event.snapshot.activity == "idle"
        assert event.snapshot.acceptance == "immediate"
        assert (await adapter.snapshot()).activity == "idle"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_busy_second_submit_certifies_zero_bytes_without_steer_or_adapter_queue() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        await adapter.submit(request("request-1"))
        with pytest.raises(HarnessAdapterBusyError, match="active ordinary operation"):
            await adapter.submit(request("request-2"))
        assert [method for method, _ in transport.requests].count("turn/start") == 1
        assert not any(method == "turn/steer" for method, _ in transport.requests)
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_set_model_and_effort_stay_pending_until_same_thread_turn_accepts() -> None:
    data = fixture()
    add_model(data)
    transport = FakeCodexTransport()
    prime_start(transport, data)
    next_turn = deepcopy(fixture_object(data, "turnStartResult"))
    fixture_object(next_turn, "turn")["id"] = "turn-2"
    transport.queue_response("turn/start", next_turn)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        model = await adapter.set_model("gpt-5.6-mini")
        assert (model.ok, model.acceptance, model.effective_value) == (True, "queued", None)
        assert "rebased" in (model.detail or "")
        assert adapter.advertise().selected_model_key == "gpt-5.6-sol"
        pending = await adapter.snapshot()
        assert pending.raw["desiredModel"] == "gpt-5.6-mini"
        assert pending.raw["desiredReasoningEffort"] == "medium"
        assert pending.raw["settingsPending"] is True

        effort = await adapter.set_effort("low")
        assert (effort.ok, effort.acceptance, effort.effective_value) == (True, "queued", None)
        receipt = await adapter.submit(request("switch-turn"))
        assert receipt.acceptance == "immediate"
        method, params = transport.requests[-1]
        assert method == "turn/start"
        assert params["threadId"] == "thread-1"
        assert params["model"] == "gpt-5.6-mini"
        assert params["effort"] == "low"
        assert adapter.advertise().selected_model_key == "gpt-5.6-mini"
        assert adapter.advertise().selected_effort == "low"
        assert (await adapter.snapshot()).raw["settingsPending"] is False
        assert len(transport.launches) == 1
        assert not any(method == "thread/resume" for method, _ in transport.requests)
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "acceptance", "promoted"),
    [
        ("inProgress", "immediate", True),
        ("completed", "immediate", True),
        ("failed", "rejected", False),
        ("interrupted", "rejected", False),
    ],
)
async def test_turn_start_promotes_only_successful_submission_status(
    status: str,
    acceptance: str,
    promoted: bool,
) -> None:
    data = fixture()
    add_model(data)
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response(
        "turn/start",
        {"turn": {"id": f"turn-{status}", "status": status, "items": []}},
    )
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        await adapter.set_model("gpt-5.6-mini")
        receipt = await adapter.submit(request(f"request-{status}"))
        assert receipt.acceptance == acceptance
        assert receipt.vendor_correlation_id == f"turn-{status}"
        assert adapter.advertise().selected_model_key == (
            "gpt-5.6-mini" if promoted else "gpt-5.6-sol"
        )
        snapshot = await adapter.snapshot()
        assert snapshot.raw["settingsPending"] is (not promoted)
        assert snapshot.raw["freshTurnRequired"] is (not promoted)
        assert snapshot.vendor_session_id == "thread-1"
        assert len(transport.launches) == 1
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_codex_terminal_correlation_is_bounded_across_many_synchronous_turns() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    for index in range(5):
        transport.queue_response(
            "turn/start",
            {"turn": {"id": f"turn-sync-{index}", "status": "completed", "items": []}},
        )
    adapter = make_adapter(transport, submission_limit=2)
    await adapter.start(launch())
    try:
        for index in range(5):
            receipt = await adapter.submit(request(f"request-sync-{index}"))
            assert receipt.raw["terminalCompletion"] is True
            assert adapter._turn_operations == {}
            assert len(adapter._completed_turns) <= 2
        assert list(adapter._completed_turns) == ["turn-sync-3", "turn-sync-4"]
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_early_codex_completion_releases_live_correlation_and_late_duplicate_is_inert() -> (
    None
):
    data = fixture()
    transport = BlockingTurnStartTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", turn_start_result(data, "turn-early"))
    adapter = make_adapter(transport, submission_limit=2)
    await adapter.start(launch())
    events = adapter.subscribe()
    early_notification = turn_completed_notification(data, "turn-early")
    try:
        first_request = request("request-early")
        first_task = asyncio.create_task(adapter.submit(first_request))
        await asyncio.wait_for(transport.turn_start_requested.wait(), 1)
        transport.emit(early_notification)
        await settle()
        assert list(adapter._unbound_completions) == ["turn-early"]

        transport.release_turn_start.set()
        first_receipt = await asyncio.wait_for(first_task, 1)
        assert first_receipt.acceptance == "immediate"
        while True:
            first_event = await asyncio.wait_for(anext(events), 1)
            if first_event.kind == "completed":
                break
        assert first_event.operation == first_request.operation
        assert adapter._turn_operations == {}
        assert adapter._unbound_completions == {}
        assert adapter._completed_turns["turn-early"] == first_request.operation

        transport.turn_start_requested = asyncio.Event()
        transport.release_turn_start = asyncio.Event()
        transport.queue_response("turn/start", turn_start_result(data, "turn-successor"))
        successor_request = request("request-successor")
        successor_task = asyncio.create_task(adapter.submit(successor_request))
        await asyncio.wait_for(transport.turn_start_requested.wait(), 1)
        while not adapter._events.empty():
            adapter._events.get_nowait()
        event_sequence = adapter._event_sequence

        # A retained old duplicate is discarded even while the successor start is pending.
        transport.emit(early_notification)
        await settle()
        assert adapter._event_sequence == event_sequence
        assert adapter._unbound_completions == {}

        transport.release_turn_start.set()
        await asyncio.wait_for(successor_task, 1)
        assert adapter._turn_operations == {"turn-successor": successor_request.operation}
        while not adapter._events.empty():
            adapter._events.get_nowait()
        event_sequence = adapter._event_sequence

        transport.emit(early_notification)
        await settle()
        assert adapter._event_sequence == event_sequence
        assert adapter._active_operation == successor_request.operation
        assert adapter._turn_operations == {"turn-successor": successor_request.operation}

        successor_notification = turn_completed_notification(data, "turn-successor")
        transport.emit(successor_notification)
        await settle()
        assert adapter._active_operation is None
        assert adapter._turn_operations == {}
        assert list(adapter._completed_turns) == ["turn-early", "turn-successor"]
    finally:
        transport.release_turn_start.set()
        await adapter.stop("forced")


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["failed", "interrupted"])
async def test_early_completion_plus_rejected_turn_start_clears_all_correlation(
    status: str,
) -> None:
    data = fixture()
    transport = BlockingTurnStartTransport()
    prime_start(transport, data)
    turn_id = f"turn-early-{status}"
    transport.queue_response("turn/start", turn_start_result(data, turn_id, status))
    adapter = make_adapter(transport, submission_limit=2)
    await adapter.start(launch())
    notification = turn_completed_notification(data, turn_id)
    try:
        submit_task = asyncio.create_task(adapter.submit(request(f"request-{status}")))
        await asyncio.wait_for(transport.turn_start_requested.wait(), 1)
        transport.emit(notification)
        await settle()
        assert list(adapter._unbound_completions) == [turn_id]

        transport.release_turn_start.set()
        receipt = await asyncio.wait_for(submit_task, 1)
        assert receipt.acceptance == "rejected"
        assert adapter._unbound_completions == {}
        assert adapter._turn_operations == {}
        assert adapter._active_operation is None
        assert adapter._completed_turns[turn_id] == request(f"request-{status}").operation

        event_sequence = adapter._event_sequence
        transport.emit(notification)
        await settle()
        assert adapter._event_sequence == event_sequence
    finally:
        transport.release_turn_start.set()
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_mismatched_early_completion_is_cleared_before_successor_activation() -> None:
    data = fixture()
    transport = BlockingTurnStartTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", turn_start_result(data, "turn-evicted-old", "completed"))
    transport.queue_response("turn/start", turn_start_result(data, "turn-newest", "completed"))
    transport.queue_response("turn/start", turn_start_result(data, "turn-successor"))
    adapter = make_adapter(transport, submission_limit=1)
    await adapter.start(launch())
    try:
        transport.release_turn_start.set()
        await adapter.submit(request("request-evicted-old"))
        await adapter.submit(request("request-newest"))
        assert list(adapter._completed_turns) == ["turn-newest"]

        transport.turn_start_requested = asyncio.Event()
        transport.release_turn_start = asyncio.Event()
        submit_task = asyncio.create_task(adapter.submit(request("request-successor")))
        await asyncio.wait_for(transport.turn_start_requested.wait(), 1)
        transport.emit(turn_completed_notification(data, "turn-evicted-old"))
        await settle()
        assert list(adapter._unbound_completions) == ["turn-evicted-old"]

        transport.release_turn_start.set()
        with pytest.raises(CodexAppServerError, match="does not match"):
            await submit_task
        assert adapter._unbound_completions == {}
        assert adapter._turn_operations == {}
        assert adapter._active_operation is None
        assert adapter._active_turn_id is None
    finally:
        transport.release_turn_start.set()
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_retained_terminal_turn_id_cannot_be_reused_for_a_successor() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", turn_start_result(data, "turn-reused", "completed"))
    transport.queue_response("turn/start", turn_start_result(data, "turn-reused"))
    adapter = make_adapter(transport, submission_limit=2)
    await adapter.start(launch())
    try:
        first = await adapter.submit(request("request-first"))
        assert first.raw["terminalCompletion"] is True
        with pytest.raises(CodexAppServerError, match="reused a retained terminal"):
            await adapter.submit(request("request-successor"))
        assert adapter._turn_operations == {}
        assert adapter._active_operation is None
        assert list(adapter._completed_turns) == ["turn-reused"]
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_codex_terminal_correlation_is_bounded_across_many_async_turns() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    for index in range(5):
        transport.queue_response("turn/start", turn_start_result(data, f"turn-async-{index}"))
    adapter = make_adapter(transport, submission_limit=2)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        for index in range(5):
            await adapter.submit(request(f"request-async-{index}"))
            transport.emit(turn_completed_notification(data, f"turn-async-{index}"))
            while True:
                event = await asyncio.wait_for(anext(events), 1)
                if event.kind == "completed":
                    break
            assert adapter._turn_operations == {}
            assert len(adapter._completed_turns) <= 2
        assert list(adapter._completed_turns) == ["turn-async-3", "turn-async-4"]
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_preflight_refuses_prompt_and_setter_while_exact_turn_is_active() -> None:
    data = fixture()
    add_model(data)
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        await adapter.submit(request("active-before-set"))
        for operation in (
            request("second-prompt").operation,
            ControlOperationRef(
                bridge_epoch="codex-test-epoch",
                sequence=2,
                operation_id="setter-2",
                kind="set-model",
            ),
        ):
            assert operation is not None
            with pytest.raises(HarnessAdapterBusyError, match="not idle"):
                await adapter.preflight_operation(operation)
        assert not any(method == "turn/steer" for method, _ in transport.requests)
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_reversing_pending_codex_settings_clears_fresh_turn_barrier() -> None:
    data = fixture()
    add_model(data, efforts=("low", "medium", "xhigh"))
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        assert (await adapter.set_model("gpt-5.6-mini")).acceptance == "queued"
        reverted_model = await adapter.set_model("gpt-5.6-sol")
        assert reverted_model.acceptance == "immediate"

        assert (await adapter.set_effort("low")).acceptance == "queued"
        reverted_effort = await adapter.set_effort("xhigh")
        assert reverted_effort.acceptance == "immediate"

        snapshot = await adapter.snapshot()
        assert snapshot.raw["settingsPending"] is False
        assert snapshot.raw["freshTurnRequired"] is False
        receipt = await adapter.submit(request("after-reversal"))
        assert receipt.acceptance == "immediate"
        params = transport.requests[-1][1]
        assert (params["model"], params["effort"]) == ("gpt-5.6-sol", "xhigh")
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_codex_set_rejects_unadvertised_model_and_model_local_effort_without_rpc() -> None:
    data = fixture()
    add_model(data)
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        request_count = len(transport.requests)
        unknown = await adapter.set_model("not-advertised")
        assert (unknown.ok, unknown.acceptance, unknown.effective_value) == (
            False,
            "unsupported",
            None,
        )
        await adapter.set_model("gpt-5.6-mini")
        invalid_effort = await adapter.set_effort("xhigh")
        assert (invalid_effort.ok, invalid_effort.acceptance) == (False, "unsupported")
        assert len(transport.requests) == request_count
        assert adapter.advertise().selected_model_key == "gpt-5.6-sol"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_pending_codex_settings_force_fresh_turn_instead_of_steering_active_turn() -> None:
    data = fixture()
    add_model(data)
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        await adapter.set_model("gpt-5.6-mini")
        await adapter.submit(request("active"))
        with pytest.raises(HarnessAdapterBusyError):
            await adapter.submit(request("after-switch"))
        assert not any(method == "turn/steer" for method, _ in transport.requests)
        starts = [params for method, params in transport.requests if method == "turn/start"]
        assert [params["clientUserMessageId"] for params in starts] == ["active"]
        assert starts[0]["model"] == "gpt-5.6-mini"
        assert starts[0]["effort"] == "medium"
        assert len(transport.launches) == 1
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_settings_notification_promotes_only_deliberate_match_and_keeps_drift_guard() -> None:
    data = fixture()
    add_model(data)
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        await adapter.set_model("gpt-5.6-mini")
        transport.emit(
            {
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "thread-1",
                    "threadSettings": {"model": "gpt-5.6-sol", "effort": "xhigh"},
                },
            }
        )
        await settle()
        assert adapter.advertise().selected_model_key == "gpt-5.6-sol"
        assert (await adapter.snapshot()).raw["settingsPending"] is True

        transport.emit(
            {
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "thread-1",
                    "threadSettings": {"model": "gpt-5.6-mini", "effort": "medium"},
                },
            }
        )
        await settle()
        assert adapter.advertise().selected_model_key == "gpt-5.6-mini"
        assert adapter.advertise().selected_effort == "medium"

        transport.emit(
            {
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "thread-1",
                    "threadSettings": {"model": "gpt-5.6-sol", "effort": "low"},
                },
            }
        )
        await settle()
        assert (await adapter.snapshot()).control == "failed"
        assert "outside the deliberate adapter setter" in str(
            (await adapter.snapshot()).raw["protocolError"]
        )
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_idempotent_codex_set_is_immediate_without_invented_effective_evidence() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        model = await adapter.set_model("gpt-5.6-sol")
        effort = await adapter.set_effort("xhigh")
        assert (model.acceptance, model.effective_value) == ("immediate", None)
        assert (effort.acceptance, effort.effective_value) == ("immediate", None)
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_correlated_server_approval_and_elicitation_responses() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        active = request("interaction-active")
        await adapter.submit(active)
        assert active.operation is not None
        transport.emit(fixture_object(data, "serverRequests", "commandApproval"))
        await settle()
        pending = (await adapter.snapshot()).pending_interaction
        assert pending is not None and pending.interaction_id.endswith("approval-1")
        await adapter.respond(
            InteractionResponse(
                interaction_id=pending.interaction_id,
                response="accept",
                responded_at="2026-07-14T12:03:00+00:00",
                operation=active.operation,
            )
        )
        assert transport.server_responses[-1] == ("approval-1", {"decision": "accept"})

        transport.emit(fixture_object(data, "serverRequests", "elicitation"))
        await settle()
        pending = (await adapter.snapshot()).pending_interaction
        assert pending is not None and pending.interaction_id.endswith("input-1")
        await adapter.respond(
            InteractionResponse(
                interaction_id=pending.interaction_id,
                response='{"action":"accept","content":{"value":"chosen"}}',
                responded_at="2026-07-14T12:04:00+00:00",
                operation=active.operation,
            )
        )
        assert transport.server_responses[-1] == (
            "input-1",
            {"action": "accept", "content": {"value": "chosen"}},
        )
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_unknown_server_request_is_declined_while_experimental_history_stays_enabled() -> (
    None
):
    """One unknown server request is declined, and the bridge survives.

    Enabling the experimental client capability is required to probe bounded
    history methods. It does not mean every experimental server-to-client
    request is supported: this unknown METHOD still receives exact -32601 and
    degrades to preserved evidence instead of marking the bridge failed.
    """

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        transport.emit(
            {
                "id": "experimental-1",
                "method": "item/tool/requestUserInput",
                "params": {"threadId": "thread-1"},
            }
        )
        degraded: Mapping[str, object] | None = None
        while degraded is None:
            event = await asyncio.wait_for(anext(events), timeout=1.0)
            if isinstance(event.raw.get("degraded"), str):
                degraded = event.raw
        assert degraded["codexMethod"] == "item/tool/requestUserInput"
        assert transport.server_errors[-1][0:2] == ("experimental-1", -32601)
        assert (await adapter.snapshot()).control == "ready"
        assert (await adapter.snapshot()).pending_interaction is None
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_reconnect_resumes_reads_and_reconciles_without_resend() -> None:
    data = fixture()
    first = FakeCodexTransport()
    prime_start(first, data)
    first.queue_response(
        "turn/start",
        HarnessAdapterDisconnectedError("lost after write", may_have_sent=True),
    )
    second = FakeCodexTransport()
    prime_start(second, data, resume=True)
    second.queue_response(
        "thread/read",
        {
            "thread": {
                **fixture_object(data, "threadResumeResult", "thread"),
                "turns": [
                    {
                        "id": "turn-recovered",
                        "status": "completed",
                        "items": [
                            {
                                "id": "user-item-1",
                                "type": "userMessage",
                                "clientId": "request-unknown",
                                "content": [{"type": "text", "text": "possibly sent"}],
                            }
                        ],
                    }
                ],
            }
        },
    )
    transports = deque([first, second])
    adapter = CodexAppServerAdapter(
        CodexAppServerSettings(model="gpt-5.6-sol", reasoning_effort="xhigh"),
        transport_factory=transports.popleft,
        clock=lambda: "2026-07-14T12:05:00+00:00",
    )
    await adapter.start(launch())
    try:
        with pytest.raises(HarnessAdapterDisconnectedError):
            await adapter.submit(request("request-unknown", "possibly sent"))
        assert (await adapter.snapshot()).control == "disconnected"
        result = await adapter.reconcile("request-unknown")
        assert result.state == "accepted"
        assert result.vendor_correlation_id == "turn-recovered"
        assert result.raw["resend"] is False
        assert [method for method, _ in second.requests] == [
            "initialize",
            "model/list",
            "thread/resume",
            "thread/read",
        ]
        assert (await adapter.snapshot()).control == "ready"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_child_terminal_registry_survives_stale_status_and_reconnect() -> None:
    """A completed child stays terminal until an explicit later turn starts."""

    data = fixture()
    first = FakeCodexTransport()
    prime_start(first, data)
    second = FakeCodexTransport()
    prime_start(second, data, resume=True)
    transports = deque([first, second])
    adapter = CodexAppServerAdapter(
        CodexAppServerSettings(model="gpt-5.6-sol", reasoning_effort="xhigh"),
        transport_factory=transports.popleft,
        clock=lambda: "2026-07-14T12:05:00+00:00",
    )
    await adapter.start(launch())
    child = "agent-registry-1"
    try:
        first.emit(
            {
                "method": "turn/started",
                "params": turn_started_params(child, "child-turn-1"),
            }
        )
        first.emit(
            {
                "method": "turn/completed",
                "params": turn_completed_params(child, "child-turn-1"),
            }
        )
        first.emit(
            {
                "method": "thread/status/changed",
                "params": {"threadId": child, "status": {"type": "idle"}},
            }
        )
        await settle()
        assert (
            fixture_object((await adapter.snapshot()).raw, "agentRegistry", child)["status"]
            == "completed"
        )

        await adapter._reconnect()
        assert (
            fixture_object((await adapter.snapshot()).raw, "agentRegistry", child)["status"]
            == "completed"
        )

        second.emit(
            {
                "method": "turn/started",
                "params": turn_started_params(child, "child-turn-2"),
            }
        )
        await settle()
        assert (
            fixture_object((await adapter.snapshot()).raw, "agentRegistry", child)["status"]
            == "running"
        )
    finally:
        await adapter.stop("forced")


def test_fixture_pins_validated_01443_schema_and_stable_surface() -> None:
    data = fixture()
    snapshot = fixture_object(data, "snapshot")
    assert snapshot["cliVersion"] == "0.144.3"
    assert snapshot["experimental"] is False
    assert fixture_object(snapshot, "schemaSha256")[
        "codex_app_server_protocol.v2.schemas.json"
    ] == ("f3e367406685c979a9893ae0ec07cd6214cf8ff92a80f272fbf4a7045f50ce7c")
    assert "item/tool/requestUserInput" not in fixture_list(snapshot, "stableMethods")
