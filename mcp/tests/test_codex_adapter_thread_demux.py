"""Thread-demux regression tests for multiplexed codex sub-agent traffic.

The codex app-server auto-attaches sub-agent thread listeners to the seat's connection,
so one transport carries interleaved parent + sub-agent notifications and server
requests. Before the demux, the first foreign-thread notification failed the whole
bridge (the 2026-07-24 production seat death); these tests pin the anti-death behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from copy import deepcopy
from typing import cast

import pytest
from _agent_wire_fixtures import (
    agent_message_delta_params,
    agent_message_item,
    collab_agent_tool_call_item,
    command_execution_approval_request,
    item_completed_params,
    notification,
    server_request_resolved_notification,
    sub_agent_activity_item,
    thread_status_changed_params,
    turn_completed_params,
    turn_started_params,
)
from agents_remember.serving.codex_app_server_adapter import (
    THREAD_REGISTRY_LIMIT,
    CodexAppServerAdapter,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    AdapterEvent,
    AdapterSnapshot,
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
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def eventually(predicate: Callable[[], bool]) -> None:
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


def live_snapshot(adapter: CodexAppServerAdapter) -> AdapterSnapshot:
    """The adapter's current snapshot for synchronous predicates (message pump is async)."""

    snap = adapter._snapshot
    assert snap is not None
    return snap


def agent_registry(adapter: CodexAppServerAdapter) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], live_snapshot(adapter).raw["agentRegistry"])


def agent_turn_started(thread_id: str, turn_id: str) -> JsonObject:
    return notification("turn/started", turn_started_params(thread_id, turn_id))


def agent_turn_completed(thread_id: str, turn_id: str) -> JsonObject:
    return notification("turn/completed", turn_completed_params(thread_id, turn_id))


def agent_status_changed(thread_id: str) -> JsonObject:
    return notification("thread/status/changed", thread_status_changed_params(thread_id))


def agent_message_completed(thread_id: str, turn_id: str, item_id: str, text: str) -> JsonObject:
    return notification(
        "item/completed",
        item_completed_params(thread_id, turn_id, agent_message_item(item_id, text)),
    )


def agent_approval(request_id: str, thread_id: str, turn_id: str) -> JsonObject:
    return command_execution_approval_request(request_id, thread_id, turn_id)


async def next_event(events: AsyncIterator[AdapterEvent]) -> AdapterEvent:
    return await asyncio.wait_for(anext(events), timeout=1.0)


@pytest.mark.anyio
async def test_spawned_subagent_traffic_never_fails_the_bridge() -> None:
    """The 2026-07-24 incident: interleaved sub-agent notifications stay demuxed evidence."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        await adapter.submit(request("request-parent"))
        agents = ("agent-thread-1", "agent-thread-2", "agent-thread-3")
        for index, thread_id in enumerate(agents):
            turn_id = f"agent-turn-{index}"
            item_id = f"agent-item-{index}"
            transport.emit(agent_status_changed(thread_id))
            transport.emit(agent_turn_started(thread_id, turn_id))
            transport.emit(agent_message_completed(thread_id, turn_id, item_id, f"agent {index}"))
            # Adapter-defense shape (off-wire: the vendor always keys deltas by thread
            # AND turn): a delta without its own threadId routes through the
            # item→thread index.
            transport.emit(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": item_id, "delta": f"chunk {index}"},
                }
            )
            transport.emit(agent_turn_completed(thread_id, turn_id))
            # Parent traffic interleaved with the sub-agent streams.
            transport.emit(fixture_object(data, "notifications", "blocked"))
        transport.emit(fixture_object(data, "notifications", "completed"))
        await eventually(lambda: "turn-1" in adapter._completed_turns)

        snap = live_snapshot(adapter)
        assert snap.control == "ready"
        # Parent busy semantics stayed parent-scoped: the sub-agent status/turn traffic
        # never moved the parent activity, and the parent's own completion idled it.
        assert snap.activity == "idle"
        for index, thread_id in enumerate(agents):
            state = adapter._threads[thread_id]
            assert not state.is_parent
            assert state.status == "active"
            assert state.active_turn_id is None
            assert list(state.completed_turns) == [f"agent-turn-{index}"]
        assert adapter._parent_state().active_turn_id is None

        delta_threads = set()
        transcript_threads = set()
        while len(delta_threads) < len(agents) or len(transcript_threads) < len(agents):
            event = await next_event(events)
            if event.raw.get("codexMethod") == "item/agentMessage/delta":
                payload = event.raw.get(AR_EVIDENCE_KEY)
                assert isinstance(payload, dict)
                delta_threads.add(payload.get("threadId"))
            for entry in event.transcript:
                if "threadId" in entry.raw:
                    transcript_threads.add(entry.raw["threadId"])
        assert delta_threads == set(agents)
        assert transcript_threads == set(agents)
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_subagent_approval_is_multiplexed_and_answered_by_request_id() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        transport.emit(agent_approval("agent-approval-1", "agent-thread-1", "agent-turn-0"))
        await eventually(lambda: len(live_snapshot(adapter).pending_interactions) == 1)

        snap = live_snapshot(adapter)
        # The singular slot stays the parent's; a sub-agent approval never blocks it.
        assert snap.pending_interaction is None
        assert snap.activity == "idle"
        pending = snap.pending_interactions[0]
        assert pending.raw["threadId"] == "agent-thread-1"
        assert pending.raw["agentLabel"] == "agent agent-th"

        await adapter.respond(
            InteractionResponse(
                interaction_id=pending.interaction_id,
                response="decline",
                responded_at="2026-07-14T12:03:00+00:00",
            )
        )
        assert transport.server_responses[-1] == ("agent-approval-1", {"decision": "decline"})
        assert live_snapshot(adapter).pending_interactions == ()
        assert live_snapshot(adapter).control == "ready"

        # A server-settled sub-agent request clears per thread without respond().
        transport.emit(agent_approval("agent-approval-2", "agent-thread-2", "agent-turn-1"))
        await eventually(lambda: len(live_snapshot(adapter).pending_interactions) == 1)
        transport.emit(
            server_request_resolved_notification("agent-thread-2", "agent-approval-2")
        )
        await eventually(lambda: len(live_snapshot(adapter).pending_interactions) == 0)
        assert live_snapshot(adapter).control == "ready"
        assert live_snapshot(adapter).pending_interaction is None
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_collab_items_bind_agent_identity_into_the_registry() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        transport.emit(
            notification(
                "item/completed",
                item_completed_params(
                    "thread-1",
                    "turn-1",
                    collab_agent_tool_call_item(
                        "collab-1",
                        "spawnAgent",
                        sender_thread_id="thread-1",
                        receiver_thread_ids=["agent-thread-9"],
                        agents_states={"agent-thread-9": {"status": "running"}},
                        prompt="investigate the failure",
                    ),
                ),
            )
        )
        await eventually(lambda: "agent-thread-9" in adapter._threads)
        registry = agent_registry(adapter)
        assert registry["agent-thread-9"] == {"status": "running"}

        transport.emit(
            notification(
                "item/completed",
                item_completed_params(
                    "thread-1",
                    "turn-1",
                    sub_agent_activity_item(
                        "collab-2",
                        kind="started",
                        agent_thread_id="agent-thread-9",
                        agent_path="/root/codex_history_failure",
                    ),
                ),
            )
        )
        await eventually(lambda: "agentPath" in agent_registry(adapter)["agent-thread-9"])
        registry = agent_registry(adapter)
        assert registry["agent-thread-9"] == {
            "status": "started",
            "agentPath": "/root/codex_history_failure",
        }

        # The bound identity becomes the agent label on multiplexed approvals.
        transport.emit(agent_approval("agent-approval-9", "agent-thread-9", "agent-turn-9"))
        await eventually(lambda: len(live_snapshot(adapter).pending_interactions) == 1)
        pending = live_snapshot(adapter).pending_interactions[0]
        assert pending.raw["agentLabel"] == "/root/codex_history_failure"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_unknown_item_delta_degrades_without_failing() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        # Adapter-defense shape (off-wire partial): no item→thread binding exists for
        # this item, so the delta crosses unbound and unmodified instead of inventing
        # a thread or failing the bridge.
        transport.emit(
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "unknown-item", "delta": "orphan"},
            }
        )
        # A delta carrying its own foreign threadId auto-registers its thread; this is
        # the proven full wire shape (threadId + turnId + itemId + delta).
        transport.emit(
            notification(
                "item/agentMessage/delta",
                agent_message_delta_params("agent-thread-7", "agent-turn-7", "item-7", "bound"),
            )
        )
        orphan = await next_event(events)
        assert orphan.raw["codexMethod"] == "item/agentMessage/delta"
        orphan_payload = orphan.raw[AR_EVIDENCE_KEY]
        assert isinstance(orphan_payload, dict)
        assert "threadId" not in orphan_payload
        bound = await next_event(events)
        bound_payload = bound.raw[AR_EVIDENCE_KEY]
        assert isinstance(bound_payload, dict)
        assert bound_payload["threadId"] == "agent-thread-7"

        await eventually(lambda: "agent-thread-7" in adapter._threads)
        assert adapter._threads["agent-thread-7"].status == "unresolved"
        assert live_snapshot(adapter).control == "ready"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_read_native_page_reads_the_requested_agent_thread() -> None:
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    agent_thread = deepcopy(fixture_object(data, "threadStartResult", "thread"))
    agent_thread["id"] = "agent-thread-1"
    transport.queue_response("thread/read", {"thread": agent_thread})
    parent_thread = deepcopy(fixture_object(data, "threadStartResult", "thread"))
    transport.queue_response("thread/read", {"thread": parent_thread})
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        agent_page = await adapter.read_native_page(
            cursor=None,
            limit=10,
            byte_budget=4096,
            thread_id="agent-thread-1",
        )
        assert transport.requests[-1] == (
            "thread/read",
            {"threadId": "agent-thread-1", "includeTurns": True},
        )
        assert agent_page.frames == ()

        await adapter.read_native_page(cursor=None, limit=10, byte_budget=4096)
        assert transport.requests[-1] == (
            "thread/read",
            {"threadId": "thread-1", "includeTurns": True},
        )
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_malformed_agent_thread_frames_degrade_to_raw_evidence() -> None:
    """Agent-thread shape drift never kills the bridge."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        malformed = [
            # thread/status/changed without its status object
            notification("thread/status/changed", {"threadId": "agent-bad-1"}),
            # turn/started without its turn object
            notification("turn/started", {"threadId": "agent-bad-2"}),
            # turn/completed without its turn object
            notification("turn/completed", {"threadId": "agent-bad-3"}),
            # item/completed without turnId/item
            notification("item/completed", {"threadId": "agent-bad-4"}),
            # an unsupported server request raised on an agent thread
            {
                "method": "item/unknown/requestApproval",
                "id": "agent-bad-approval",
                "params": {"threadId": "agent-bad-5"},
            },
        ]
        for frame in malformed:
            transport.emit(frame)
        degraded: list[Mapping[str, object]] = []
        while len(degraded) < len(malformed):
            event = await next_event(events)
            if isinstance(event.raw.get("degraded"), str):
                degraded.append(event.raw)
        assert live_snapshot(adapter).control == "ready"
        assert [entry["codexMethod"] for entry in degraded] == [
            "thread/status/changed",
            "turn/started",
            "turn/completed",
            "item/completed",
            "item/unknown/requestApproval",
        ]
        # The original frame params survived as preserved raw evidence.
        for entry in degraded:
            payload = entry[AR_EVIDENCE_KEY]
            assert isinstance(payload, dict)
            assert str(payload["threadId"]).startswith("agent-bad-")
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_malformed_parent_frame_still_fails_loud() -> None:
    """Parent-thread shape errors stay load-bearing: the bridge fails, never degrades."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        # thread/status/changed for the PARENT thread without its status object.
        transport.emit(notification("thread/status/changed", {"threadId": "thread-1"}))
        await eventually(lambda: live_snapshot(adapter).control == "failed")
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_registry_full_degrades_and_settled_threads_evict() -> None:
    """Unevictable registry-full degrades the frame; a settled
    agent thread is evicted to make room for the next one."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        # Fill the registry with actively-turning agents: none is evictable.
        for index in range(THREAD_REGISTRY_LIMIT - 1):
            transport.emit(agent_turn_started(f"agent-fill-{index}", f"agent-fill-turn-{index}"))
        await eventually(lambda: len(adapter._threads) >= THREAD_REGISTRY_LIMIT)
        assert live_snapshot(adapter).control == "ready"

        # The next foreign thread cannot register: its frame degrades to raw evidence.
        transport.emit(agent_turn_started("agent-overflow", "agent-overflow-turn"))
        degraded: Mapping[str, object] | None = None
        while degraded is None:
            event = await next_event(events)
            if isinstance(event.raw.get("degraded"), str):
                degraded = event.raw
        assert live_snapshot(adapter).control == "ready"
        assert "registry is full" in str(degraded["degraded"])
        payload = degraded[AR_EVIDENCE_KEY]
        assert isinstance(payload, dict)
        assert payload["threadId"] == "agent-overflow"
        assert "agent-overflow" not in adapter._threads

        # Once an agent's turn completes, that settled thread IS evicted to make room.
        transport.emit(agent_turn_completed("agent-fill-0", "agent-fill-turn-0"))
        await eventually(lambda: "agent-fill-turn-0" in adapter._threads["agent-fill-0"].completed_turns)
        transport.emit(agent_turn_started("agent-after-evict", "agent-after-evict-turn"))
        await eventually(lambda: "agent-after-evict" in adapter._threads)
        assert "agent-fill-0" not in adapter._threads
        assert live_snapshot(adapter).control == "ready"
    finally:
        await adapter.stop("forced")
