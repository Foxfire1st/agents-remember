from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from agents_remember.errors import CodexAppServerError
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject
from test_codex_app_server_adapter import (
    TEST_SETTINGS,
    FakeCodexTransport,
    fixture,
    fixture_list,
    fixture_object,
    launch,
    make_adapter,
    prime_start,
)


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
