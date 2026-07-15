from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from agents_remember.errors import CodexAppServerError, HarnessAdapterDisconnectedError
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject, RequestId
from agents_remember.serving.codex_app_server_session import BusyPolicy
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
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

    async def start(self, launch: LaunchSpec) -> None:
        self.launches.append(launch)

    async def request(self, method: str, params: Mapping[str, object]) -> JsonObject:
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
    busy_policy: BusyPolicy = "steer",
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
        busy_policy=busy_policy,
    )
    return CodexAppServerAdapter(
        settings,
        transport_factory=lambda: transport,
        clock=lambda: "2026-07-14T12:02:00+00:00",
    )


async def settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


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
        assert initialize_params["capabilities"] == {"experimentalApi": False}
        thread_params = transport.requests[2][1]
        assert thread_params["config"] == {
            "feature_flag": True,
            "model_reasoning_effort": "xhigh",
        }
        assert thread_params["model"] == "gpt-5.6-sol"
        assert thread_params["cwd"] == "/workspace"
        assert launch().argv == ("codex", "app-server")
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
        assert transport.requests[-1][1]["config"] == {"model_reasoning_effort": "xhigh"}
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
async def test_busy_policy_steers_or_queues_explicitly() -> None:
    data = fixture()
    steer_transport = FakeCodexTransport()
    prime_start(steer_transport, data)
    steer_transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    steer_transport.queue_response("turn/steer", {"turnId": "turn-1"})
    steer = make_adapter(steer_transport)
    await steer.start(launch())
    try:
        await steer.submit(request("request-1"))
        steered = await steer.submit(request("request-2"))
        assert steered.acceptance == "immediate"
        assert steer_transport.requests[-1] == (
            "turn/steer",
            {
                "threadId": "thread-1",
                "expectedTurnId": "turn-1",
                "input": [{"type": "text", "text": "hello"}],
                "clientUserMessageId": "request-2",
            },
        )
    finally:
        await steer.stop("forced")

    queue_transport = FakeCodexTransport()
    prime_start(queue_transport, data)
    queue_transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    second_turn = deepcopy(fixture_object(data, "turnStartResult"))
    fixture_object(second_turn, "turn")["id"] = "turn-2"
    queue_transport.queue_response("turn/start", second_turn)
    queued_adapter = make_adapter(queue_transport, busy_policy="queue")
    await queued_adapter.start(launch())
    try:
        await queued_adapter.submit(request("request-1"))
        queued = await queued_adapter.submit(request("request-2"))
        assert queued.acceptance == "queued"
        queue_transport.emit(fixture_object(data, "notifications", "completed"))
        await settle()
        turn_starts = [
            params for method, params in queue_transport.requests if method == "turn/start"
        ]
        assert [params["clientUserMessageId"] for params in turn_starts] == [
            "request-1",
            "request-2",
        ]
    finally:
        await queued_adapter.stop("forced")


@pytest.mark.anyio
async def test_correlated_server_approval_and_elicitation_responses() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        transport.emit(fixture_object(data, "serverRequests", "commandApproval"))
        await settle()
        pending = (await adapter.snapshot()).pending_interaction
        assert pending is not None and pending.interaction_id.endswith("approval-1")
        await adapter.respond(
            InteractionResponse(
                interaction_id=pending.interaction_id,
                response="accept",
                responded_at="2026-07-14T12:03:00+00:00",
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
            )
        )
        assert transport.server_responses[-1] == (
            "input-1",
            {"action": "accept", "content": {"value": "chosen"}},
        )
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_experimental_server_request_fails_instead_of_enabling_experimental_api() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        transport.emit(
            {
                "id": "experimental-1",
                "method": "item/tool/requestUserInput",
                "params": {"threadId": "thread-1"},
            }
        )
        await settle()
        assert (await adapter.snapshot()).control == "failed"
        assert transport.server_errors[-1][0:2] == ("experimental-1", -32601)
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


def test_fixture_pins_validated_01443_schema_and_stable_surface() -> None:
    data = fixture()
    snapshot = fixture_object(data, "snapshot")
    assert snapshot["cliVersion"] == "0.144.3"
    assert snapshot["experimental"] is False
    assert fixture_object(snapshot, "schemaSha256")[
        "codex_app_server_protocol.v2.schemas.json"
    ] == ("f3e367406685c979a9893ae0ec07cd6214cf8ff92a80f272fbf4a7045f50ce7c")
    assert "item/tool/requestUserInput" not in fixture_list(snapshot, "stableMethods")
