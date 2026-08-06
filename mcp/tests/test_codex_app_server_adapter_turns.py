from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from agents_remember.errors import HarnessAdapterBusyError
from test_codex_app_server_adapter import (
    TEST_SETTINGS,
    FakeCodexTransport,
    add_model,
    fixture,
    fixture_object,
    launch,
    make_adapter,
    next_event_of_kind,
    prime_start,
    request,
    settle,
)


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
        event = await next_event_of_kind(events, "completed")
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
    adapter = make_adapter(transport, replace(TEST_SETTINGS, submission_limit=2))
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
