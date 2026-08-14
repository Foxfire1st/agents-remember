from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping

import pytest
from _agent_wire_fixtures import turn_completed_params, turn_started_params
from agents_remember.errors import CodexAppServerError, HarnessAdapterDisconnectedError
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.codex_app_server_state import interaction_prompt, interaction_result
from agents_remember.serving.harness_control_models import (
    InteractionResponse,
)
from test_codex_app_server_adapter import (
    FakeCodexTransport,
    fixture,
    fixture_object,
    launch,
    make_adapter,
    prime_start,
    request,
    settle,
)


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

        for rpc_id, action, expected in (
            ("tool-approval-accept", "accept", {"action": "accept", "content": {}}),
            ("tool-approval-decline", "decline", {"action": "decline"}),
            ("tool-approval-cancel", "cancel", {"action": "cancel"}),
        ):
            transport.emit(
                {
                    "id": rpc_id,
                    "method": "mcpServer/elicitation/request",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "serverName": "agents-remember",
                        "mode": "form",
                        "message": (
                            "Allow the agents-remember MCP server to run tool "
                            '"attach_terminal_session_to_leaf"?'
                        ),
                        "requestedSchema": {"type": "object", "properties": {}},
                    },
                }
            )
            await settle()
            pending = (await adapter.snapshot()).pending_interaction
            assert pending is not None
            assert pending.interaction_id.endswith(rpc_id)
            assert pending.choices == ("accept", "decline", "cancel")
            await adapter.respond(
                InteractionResponse(
                    interaction_id=pending.interaction_id,
                    response=action,
                    responded_at="2026-07-14T12:04:30+00:00",
                    operation=active.operation,
                )
            )
            assert transport.server_responses[-1] == (rpc_id, expected)
    finally:
        await adapter.stop("forced")


def test_mcp_elicitation_response_edges_remain_typed_and_fail_closed() -> None:
    assert interaction_result(
        "item/permissions/requestApproval",
        '{"permissions":{}}',
        params={},
    ) == {"permissions": {}}

    non_empty_form = {
        "mode": "form",
        "message": "Choose a value",
        "requestedSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
    }
    with pytest.raises(CodexAppServerError, match="structured JSON response"):
        interaction_result("mcpServer/elicitation/request", "accept", params=non_empty_form)
    with pytest.raises(CodexAppServerError, match="action must be"):
        interaction_result(
            "mcpServer/elicitation/request",
            '{"action":"later"}',
            params=non_empty_form,
        )
    with pytest.raises(CodexAppServerError, match="requires content"):
        interaction_result(
            "mcpServer/elicitation/request",
            '{"action":"accept"}',
            params=non_empty_form,
        )

    for params in (
        {"mode": "url", "message": "Open the authorization URL"},
        {"mode": "form", "message": "Malformed form", "requestedSchema": "not-an-object"},
    ):
        assert interaction_prompt("mcpServer/elicitation/request", params) == (
            params["message"],
            (),
        )


@pytest.mark.anyio
# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_adapter_reconnect.py:90).
async def test_unknown_server_request_is_declined_while_experimental_history_stays_enabled() -> (  # pragma: no cover
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
