from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from agents_remember.errors import CodexAppServerError
from agents_remember.serving import codex_mcp_readiness
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject, ShutdownMode
from agents_remember.serving.codex_mcp_readiness import (
    CodexMcpReadinessTiming,
    wait_for_codex_mcp_tool,
)
from test_codex_app_server_adapter import (
    TEST_SETTINGS,
    FakeCodexTransport,
    fixture,
    fixture_list,
    fixture_object,
    launch,
    make_adapter,
    prime_start,
    settle,
)


class _ReadinessClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _CleanupFailingTransport(FakeCodexTransport):
    async def stop(self, mode: ShutdownMode) -> None:
        await super().stop(mode)
        raise RuntimeError("fixture cleanup failure")


def _mcp_page(
    status: str,
    *tools: str,
    cursor: str | None = None,
) -> JsonObject:
    return {
        "data": [
            {
                "name": "agents-remember",
                "runtimeStatus": status,
                "authStatus": "unsupported",
                "tools": {name: {} for name in tools},
            }
        ],
        "nextCursor": cursor,
    }


@pytest.mark.anyio
async def test_mcp_readiness_waits_for_the_exact_connected_tool() -> None:
    transport = FakeCodexTransport()
    transport.queue_response("mcpServerStatus/list", _mcp_page("starting"))
    transport.queue_response(
        "mcpServerStatus/list",
        _mcp_page("connected", "dispatch_agent", "server_info"),
    )
    clock = _ReadinessClock()

    evidence = await wait_for_codex_mcp_tool(
        transport,
        thread_id="thread-1",
        tool_name="dispatch_agent",
        timing=CodexMcpReadinessTiming(
            timeout_seconds=1,
            poll_seconds=0.1,
            clock=clock,
            sleeper=clock.sleep,
        ),
    )

    assert evidence.to_json() == {
        "serverName": "agents-remember",
        "runtimeStatus": "connected",
        "toolName": "dispatch_agent",
        "toolCount": 2,
    }
    assert clock.sleeps == [0.1]


@pytest.mark.anyio
async def test_mcp_readiness_paginates_and_rejects_a_repeated_cursor() -> None:
    transport = FakeCodexTransport()
    transport.queue_response(
        "mcpServerStatus/list",
        _mcp_page("connected", "server_info", cursor="page-2"),
    )
    transport.queue_response(
        "mcpServerStatus/list",
        _mcp_page("connected", "dispatch_agent"),
    )

    evidence = await wait_for_codex_mcp_tool(
        transport,
        thread_id="thread-1",
        tool_name="dispatch_agent",
    )

    assert evidence.server_name == "agents-remember"
    assert transport.requests[1][1]["cursor"] == "page-2"

    repeated = FakeCodexTransport()
    repeated.queue_response(
        "mcpServerStatus/list",
        _mcp_page("starting", cursor="same"),
    )
    repeated.queue_response(
        "mcpServerStatus/list",
        _mcp_page("starting", cursor="same"),
    )
    with pytest.raises(CodexAppServerError, match="repeated a pagination cursor"):
        await wait_for_codex_mcp_tool(
            repeated,
            thread_id="thread-1",
            tool_name="dispatch_agent",
        )


@pytest.mark.anyio
async def test_mcp_readiness_timeout_is_bounded_and_actionable() -> None:
    transport = FakeCodexTransport()
    transport.queue_response("mcpServerStatus/list", _mcp_page("starting"))
    transport.queue_response("mcpServerStatus/list", _mcp_page("starting"))
    clock = _ReadinessClock()

    with pytest.raises(CodexAppServerError, match="timed out waiting for required role tool"):
        await wait_for_codex_mcp_tool(
            transport,
            thread_id="thread-1",
            tool_name="dispatch_agent",
            timing=CodexMcpReadinessTiming(
                timeout_seconds=0.1,
                poll_seconds=0.1,
                clock=clock,
                sleeper=clock.sleep,
            ),
        )

    assert clock.sleeps == [0.1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("thread_id", "tool_name", "timing", "message"),
    (
        ("", "dispatch_agent", CodexMcpReadinessTiming(), "non-empty thread id"),
        ("thread-1", "", CodexMcpReadinessTiming(), "non-empty tool name"),
        (
            "thread-1",
            "dispatch_agent",
            CodexMcpReadinessTiming(timeout_seconds=0),
            "positive bounded timing",
        ),
    ),
)
async def test_mcp_readiness_rejects_invalid_requests_before_transport(
    thread_id: str,
    tool_name: str,
    timing: CodexMcpReadinessTiming,
    message: str,
) -> None:
    with pytest.raises(CodexAppServerError, match=message):
        await wait_for_codex_mcp_tool(
            FakeCodexTransport(),
            thread_id=thread_id,
            tool_name=tool_name,
            timing=timing,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ({"data": {}}, "requires data array"),
        ({"data": [None]}, "status must be an object"),
        (
            {"data": [{"runtimeStatus": "connected", "tools": {}}]},
            "requires a name",
        ),
        (
            {
                "data": [
                    {
                        "name": "agents-remember",
                        "runtimeStatus": "connected",
                        "tools": [],
                    }
                ]
            },
            "tools must be an object or null",
        ),
        (
            {
                "data": [
                    {
                        "name": "agents-remember",
                        "runtimeStatus": "connected",
                        "tools": {"": {}},
                    }
                ]
            },
            "tool names must be non-empty strings",
        ),
    ),
)
async def test_mcp_readiness_rejects_malformed_status_rows(
    payload: JsonObject,
    message: str,
) -> None:
    transport = FakeCodexTransport()
    transport.queue_response("mcpServerStatus/list", payload)
    with pytest.raises(CodexAppServerError, match=message):
        await wait_for_codex_mcp_tool(
            transport,
            thread_id="thread-1",
            tool_name="dispatch_agent",
        )


@pytest.mark.anyio
async def test_mcp_readiness_rejects_invalid_cursor_and_page_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = FakeCodexTransport()
    invalid.queue_response(
        "mcpServerStatus/list",
        _mcp_page("starting", cursor=""),
    )
    with pytest.raises(CodexAppServerError, match="invalid cursor"):
        await wait_for_codex_mcp_tool(
            invalid,
            thread_id="thread-1",
            tool_name="dispatch_agent",
        )

    limited = FakeCodexTransport()
    limited.queue_response(
        "mcpServerStatus/list",
        _mcp_page("starting", cursor="page-2"),
    )
    monkeypatch.setattr(codex_mcp_readiness, "MCP_STATUS_PAGE_LIMIT", 1)
    with pytest.raises(CodexAppServerError, match="exceeded the pagination limit"):
        await wait_for_codex_mcp_tool(
            limited,
            thread_id="thread-1",
            tool_name="dispatch_agent",
        )


@pytest.mark.anyio
async def test_role_readiness_refuses_a_missing_thread_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeCodexTransport()
    adapter = make_adapter(transport)
    monkeypatch.setattr(adapter, "_require_transport", lambda: transport)

    with pytest.raises(CodexAppServerError, match="without a thread id"):
        await adapter._role_mcp_readiness(replace(launch(), env={"AR_SPAWN_ROLE": "architect"}))


@pytest.mark.anyio
async def test_handshake_uses_stable_protocol_and_exposes_effort_menu() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(
        transport,
        replace(
            TEST_SETTINGS,
            approval_policy="on-request",
            approvals_reviewer="user",
            sandbox="workspace-write",
            turn_sandbox_policy={"type": "workspaceWrite", "writableRoots": ["/workspace"]},
            config={"feature_flag": True},
        ),
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
async def test_role_start_waits_for_dispatch_tool_before_becoming_ready() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response(
        "mcpServerStatus/list",
        {
            "data": [
                {
                    "name": "agents-remember",
                    "runtimeStatus": "connected",
                    "tools": {"dispatch_agent": {}, "server_info": {}},
                }
            ],
            "nextCursor": None,
        },
    )
    adapter = make_adapter(transport)
    role_launch = replace(
        launch(),
        env={"PRESERVE_INSTALLED_AUTH": "1", "AR_SPAWN_ROLE": "architect"},
    )

    handshake = await adapter.start(role_launch)
    try:
        assert handshake.snapshot.control == "ready"
        assert handshake.snapshot.raw["requiredMcpTool"] == {
            "serverName": "agents-remember",
            "runtimeStatus": "connected",
            "toolName": "dispatch_agent",
            "toolCount": 2,
        }
        assert handshake.raw["requiredMcpTool"] == handshake.snapshot.raw["requiredMcpTool"]
        assert [method for method, _ in transport.requests][-1] == "mcpServerStatus/list"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_role_start_refuses_settled_mcp_surface_without_dispatch_tool() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response(
        "mcpServerStatus/list",
        {
            "data": [
                {
                    "name": "agents-remember",
                    "runtimeStatus": "connected",
                    "tools": {"server_info": {}},
                }
            ],
            "nextCursor": None,
        },
    )
    adapter = make_adapter(transport)
    role_launch = replace(
        launch(),
        env={"PRESERVE_INSTALLED_AUTH": "1", "AR_SPAWN_ROLE": "architect"},
    )

    with pytest.raises(CodexAppServerError, match="settled without required role tool"):
        await adapter.start(role_launch)

    assert transport.stop_modes == ["forced"]


@pytest.mark.anyio
async def test_role_start_preserves_readiness_refusal_when_cleanup_also_fails() -> None:
    data = fixture()
    transport = _CleanupFailingTransport()
    prime_start(transport, data)
    transport.queue_response(
        "mcpServerStatus/list",
        _mcp_page("connected", "server_info"),
    )
    adapter = make_adapter(transport)
    role_launch = replace(launch(), env={"AR_SPAWN_ROLE": "architect"})

    with pytest.raises(
        CodexAppServerError,
        match="settled without required role tool",
    ) as caught:
        await adapter.start(role_launch)

    assert caught.value.__notes__ == [
        "Codex role startup cleanup also failed: RuntimeError: fixture cleanup failure"
    ]
    assert transport.stop_modes == ["forced"]


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
async def test_client_user_agent_uses_host_version_and_exact_client_identity() -> None:
    data = fixture()
    initialize = fixture_object(data, "initializeResult")
    initialize["userAgent"] = (
        "agents_remember/0.147.0 (Ubuntu 22.4.0; x86_64) unknown (agents_remember; 3.0.0)"
    )
    fixture_object(data, "threadStartResult", "thread")["cliVersion"] = "0.147.0"
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)

    handshake = await adapter.start(launch())
    try:
        assert handshake.adapter_id == "codex-app-server:0.147.0"
        assert handshake.raw["codexCliVersion"] == "0.147.0"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_client_user_agent_rejects_wrong_client_identity() -> None:
    data = fixture()
    initialize = fixture_object(data, "initializeResult")
    initialize["userAgent"] = (
        "agents_remember/0.147.0 (Ubuntu 22.4.0; x86_64) unknown (agents_remember; 3.0.1)"
    )
    transport = FakeCodexTransport()
    prime_start(transport, data)

    with pytest.raises(CodexAppServerError, match="incompatible userAgent"):
        await make_adapter(transport).start(launch())
    assert transport.stop_modes == ["forced"]


@pytest.mark.anyio
async def test_desktop_server_product_keeps_exact_client_identity() -> None:
    data = fixture()
    initialize = fixture_object(data, "initializeResult")
    initialize["userAgent"] = (
        "Codex Desktop/0.147.0 (Ubuntu 22.4.0; x86_64) unknown (agents_remember; 3.0.0)"
    )
    transport = FakeCodexTransport()
    prime_start(transport, data)
    fixture_object(data, "threadStartResult", "thread")["cliVersion"] = "0.147.0"

    adapter = make_adapter(transport)
    handshake = await adapter.start(launch())
    try:
        assert handshake.adapter_id == "codex-app-server:0.147.0"
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
    adapter = make_adapter(transport, replace(TEST_SETTINGS, resume_thread_id="thread-1"))
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
async def test_settings_updates_cover_matching_stale_and_drift_branches() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
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
        assert (await adapter.snapshot()).raw["settingsPending"] is False

        await adapter.set_effort("high")
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
        assert (await adapter.snapshot()).raw["settingsPending"] is True

        transport.emit(
            {
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "thread-1",
                    "threadSettings": {"model": "gpt-5.6-sol", "effort": "high"},
                },
            }
        )
        await settle()
        assert (await adapter.snapshot()).raw["settingsPending"] is False

        await adapter.set_effort("medium")
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
        snapshot = await adapter.snapshot()
        assert snapshot.control == "failed"
        assert "outside the deliberate adapter setter" in str(snapshot.raw["protocolError"])
    finally:
        await adapter.stop("forced")
