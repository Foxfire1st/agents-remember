from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from agents_remember.errors import CodexAppServerError, HarnessAdapterBusyError
from agents_remember.models.conversations.control_wire import (
    ControlOperationRef,
)
from test_codex_app_server_adapter import (
    TEST_SETTINGS,
    BlockingTurnStartTransport,
    FakeCodexTransport,
    add_model,
    assert_notification_is_inert,
    fixture,
    fixture_object,
    launch,
    make_adapter,
    next_event_of_kind,
    prime_start,
    request,
    settle,
    turn_completed_notification,
    turn_start_result,
)


@pytest.mark.anyio
async def test_early_codex_completion_releases_live_correlation_and_late_duplicate_is_inert() -> (
    None
):
    data = fixture()
    transport = BlockingTurnStartTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", turn_start_result(data, "turn-early"))
    adapter = make_adapter(transport, replace(TEST_SETTINGS, submission_limit=2))
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
        first_event = await next_event_of_kind(events, "completed")
        assert first_event.operation == first_request.operation
        assert first_event.transcript[0].request_id == "request-early"
        assert adapter._turn_operations == {}
        assert adapter._unbound_completions == {}
        assert adapter._completed_turns["turn-early"] == first_request.operation

        transport.turn_start_requested = asyncio.Event()
        transport.release_turn_start = asyncio.Event()
        transport.queue_response("turn/start", turn_start_result(data, "turn-successor"))
        successor_request = request("request-successor")
        successor_task = asyncio.create_task(adapter.submit(successor_request))
        await asyncio.wait_for(transport.turn_start_requested.wait(), 1)

        # A retained old duplicate is discarded even while the successor start is pending.
        await assert_notification_is_inert(adapter, transport, early_notification)
        assert adapter._unbound_completions == {}

        transport.release_turn_start.set()
        await asyncio.wait_for(successor_task, 1)
        assert adapter._turn_operations == {"turn-successor": successor_request.operation}

        await assert_notification_is_inert(adapter, transport, early_notification)
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
    adapter = make_adapter(transport, replace(TEST_SETTINGS, submission_limit=2))
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
    adapter = make_adapter(transport, replace(TEST_SETTINGS, submission_limit=1))
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
    adapter = make_adapter(transport, replace(TEST_SETTINGS, submission_limit=2))
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
    adapter = make_adapter(transport, replace(TEST_SETTINGS, submission_limit=2))
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        for index in range(5):
            await adapter.submit(request(f"request-async-{index}"))
            transport.emit(turn_completed_notification(data, f"turn-async-{index}"))
            await next_event_of_kind(events, "completed")
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
async def test_reversing_pending_codex_settings_clears_fresh_turn_blocker() -> None:
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
