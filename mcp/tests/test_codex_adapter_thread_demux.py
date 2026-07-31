"""Thread-demux regression tests for multiplexed codex sub-agent traffic.

The codex app-server auto-attaches sub-agent thread listeners to the seat's connection,
so one transport carries interleaved parent + sub-agent notifications and server
requests. Before the demux, the first foreign-thread notification failed the whole
bridge (the 2026-07-24 production seat death); these tests pin the anti-death behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from typing import cast

import pytest
from _agent_wire_fixtures import (
    CollabAgents,
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
    ADAPTER_EVENT_QUEUE_LIMIT,
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
            assert state.status == "completed"
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
        transport.emit(server_request_resolved_notification("agent-thread-2", "agent-approval-2"))
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
                        agents=CollabAgents(
                            "thread-1",
                            receiver_thread_ids=["agent-thread-9"],
                            states={"agent-thread-9": {"status": "running"}},
                        ),
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
    transport.queue_response(
        "thread/items/list",
        {"data": [], "nextCursor": None, "backwardsCursor": None},
    )
    transport.queue_response(
        "thread/items/list",
        {"data": [], "nextCursor": None, "backwardsCursor": None},
    )
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
            "thread/items/list",
            {"threadId": "agent-thread-1", "limit": 1, "sortDirection": "asc"},
        )
        assert agent_page.frames == ()

        await adapter.read_native_page(cursor=None, limit=10, byte_budget=4096)
        assert transport.requests[-1] == (
            "thread/items/list",
            {"threadId": "thread-1", "limit": 1, "sortDirection": "asc"},
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
        await eventually(
            lambda: "agent-fill-turn-0" in adapter._threads["agent-fill-0"].completed_turns
        )
        transport.emit(agent_turn_started("agent-after-evict", "agent-after-evict-turn"))
        await eventually(lambda: "agent-after-evict" in adapter._threads)
        assert "agent-fill-0" not in adapter._threads
        assert live_snapshot(adapter).control == "ready"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_concurrent_parent_server_requests_never_fail_the_bridge() -> None:
    """The 2026-07-26 seat death: concurrent parent pendings are normal vendor traffic."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        # An active parent turn (the incident had the parent mid-turn with MCP calls).
        transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
        await adapter.submit(request("request-1"))
        assert live_snapshot(adapter).activity == "running"

        transport.emit(agent_approval("req-1", "thread-1", "turn-1"))
        await eventually(lambda: live_snapshot(adapter).pending_interaction is not None)
        # The second request on the SAME parent thread used to mark the bridge failed.
        transport.emit(agent_approval("req-2", "thread-1", "turn-1"))
        await eventually(lambda: len(live_snapshot(adapter).pending_interactions) == 2)
        snap = live_snapshot(adapter)
        assert snap.control == "ready"
        assert snap.activity == "blocked"
        # The singular slot carries the OLDEST parent pending for back-compat.
        assert snap.pending_interaction is not None
        assert (
            snap.pending_interaction.interaction_id == snap.pending_interactions[0].interaction_id
        )
        assert {p.interaction_id for p in snap.pending_interactions} == {
            snap.pending_interactions[0].interaction_id,
            snap.pending_interactions[1].interaction_id,
        }

        # Each pending is answerable individually by request id (parent guard honored).
        await adapter.respond(
            InteractionResponse(
                interaction_id=snap.pending_interactions[1].interaction_id,
                response="accept",
                responded_at="2026-07-14T12:04:00+00:00",
                operation=adapter._active_operation,
            )
        )
        assert transport.server_responses[-1] == ("req-2", {"decision": "accept"})
        assert len(live_snapshot(adapter).pending_interactions) == 1
        assert live_snapshot(adapter).control == "ready"

        # The vendor settles the other one; resolution clears by rpc id.
        transport.emit(server_request_resolved_notification("thread-1", "req-1"))
        await eventually(lambda: len(live_snapshot(adapter).pending_interactions) == 0)
        assert live_snapshot(adapter).pending_interaction is None
        assert live_snapshot(adapter).control == "ready"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_experimental_server_request_on_parent_degrades() -> None:
    """An unknown/experimental request METHOD on the parent is declined + preserved, never fatal."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        transport.emit(
            {
                "id": "req-exp-1",
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "i-1",
                    "questions": [],
                },
            }
        )
        await eventually(lambda: bool(transport.server_errors))
        assert transport.server_errors[0][0] == "req-exp-1"
        assert transport.server_errors[0][1] == -32601
        assert "experimental" in transport.server_errors[0][2]
        # The bridge stays ready and the frame crossed as preserved evidence.
        event = await next_event(events)
        while event.raw.get("codexMethod") != "item/tool/requestUserInput":
            event = await next_event(events)
        assert live_snapshot(adapter).control == "ready"
        assert live_snapshot(adapter).pending_interaction is None
        assert live_snapshot(adapter).pending_interactions == ()
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_malformed_known_method_parent_request_fails_loud() -> None:
    """A KNOWN stable method's malformed params on the parent keeps failing the bridge."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        transport.emit(
            {
                "id": "req-bad-shape",
                "method": "item/commandExecution/requestApproval",
                "params": "not-an-object",
            }
        )
        await eventually(lambda: live_snapshot(adapter).control == "failed")
        # Never declined-and-degraded: no error response, nothing outstanding.
        assert transport.server_errors == []
        assert live_snapshot(adapter).pending_interaction is None
        assert live_snapshot(adapter).pending_interactions == ()
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_known_method_request_with_boolean_rpc_id_fails_loud() -> None:
    """id=true on a KNOWN stable method: the bridge fails — no silent outstanding."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        transport.emit(
            {
                "id": True,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "command": "ls",
                },
            }
        )
        await eventually(lambda: live_snapshot(adapter).control == "failed")
        assert transport.server_errors == []
        assert transport.server_responses == []
        snap = live_snapshot(adapter)
        assert snap.pending_interaction is None
        assert snap.pending_interactions == ()
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_unknown_method_on_parent_degrades() -> None:
    """A method outside the stable AND experimental sets is declined + degraded, never fatal."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        transport.emit(
            {
                "id": "req-unknown-1",
                "method": "item/future/requestApproval",
                "params": {"threadId": "thread-1", "turnId": "turn-1"},
            }
        )
        await eventually(lambda: bool(transport.server_errors))
        assert transport.server_errors[0][0] == "req-unknown-1"
        assert transport.server_errors[0][1] == -32601
        assert "unsupported" in transport.server_errors[0][2]
        degraded: Mapping[str, object] | None = None
        while degraded is None:
            event = await next_event(events)
            if isinstance(event.raw.get("degraded"), str):
                degraded = event.raw
        assert degraded["codexMethod"] == "item/future/requestApproval"
        assert live_snapshot(adapter).control == "ready"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_pending_map_overflow_declines_the_newest_request() -> None:
    """The bounded map declines + degrades the newest request; older pendings stay intact."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        for index in range(16):
            transport.emit(agent_approval(f"req-fill-{index}", "thread-1", "turn-1"))
        await eventually(lambda: len(live_snapshot(adapter).pending_interactions) == 16)
        transport.emit(agent_approval("req-overflow", "thread-1", "turn-1"))
        await eventually(lambda: bool(transport.server_errors))
        assert transport.server_errors[0][0] == "req-overflow"
        assert "map is full" in transport.server_errors[0][2]
        snap = live_snapshot(adapter)
        assert snap.control == "ready"
        assert len(snap.pending_interactions) == 16
        assert snap.pending_interaction is not None

        # The declined request crosses as DEGRADED evidence naming the map-full reason.
        degraded: Mapping[str, object] | None = None
        while degraded is None:
            event = await next_event(events)
            if isinstance(event.raw.get("degraded"), str):
                degraded = event.raw
        assert degraded["codexMethod"] == "item/commandExecution/requestApproval"
        assert "map is full" in str(degraded["degraded"])
        payload = degraded[AR_EVIDENCE_KEY]
        assert isinstance(payload, dict)
        assert payload["itemId"] == "req-overflow-item"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_delta_flood_sheds_oldest_deltas_with_an_honest_notice() -> None:
    """Queue pressure sheds high-volume deltas (never a queue-full kill), with accounting.

    The 2026-07-26 seat death: a 3-agent delta flood hit the queue-full raise and
    killed the bridge, and content vanished without a signal. Now every event is
    sequenced, structural events outlive shed deltas, and one load-shed notice
    crosses with the shed count once the consumer catches up.
    """

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    agents = ["flood-1", "flood-2", "flood-3"]
    total_deltas = 0
    try:
        for thread in agents:
            transport.emit(agent_turn_started(thread, f"{thread}-turn"))
            transport.emit(
                agent_message_completed(thread, f"{thread}-turn", f"{thread}-msg", "done")
            )
        for _ in range(500):
            for thread in agents:
                transport.emit(
                    notification(
                        "item/agentMessage/delta",
                        agent_message_delta_params(thread, f"{thread}-turn", f"{thread}-msg", "x"),
                    )
                )
            total_deltas += len(agents)
        # Let the pump run without consuming: the queue fills and shedding engages.
        await eventually(
            lambda: adapter._events.full() or adapter._event_sequence >= 6 + total_deltas
        )
        assert live_snapshot(adapter).control == "ready"
        assert adapter._event_sequence == 6 + total_deltas  # nothing un-sequenced, no raise

        # Structural completions survived the shed; the shed count is accounted.
        drained: list[AdapterEvent] = []
        while not adapter._events.empty():
            event = adapter._events.get_nowait()
            if event is not None:
                drained.append(event)
        completions = [event for event in drained if event.kind == "transcript"]
        assert len(completions) == len(agents)
        assert adapter._dropped_events > 0

        # Once the consumer catches up, one notice crosses with the shed count.
        transport.emit(agent_status_changed("flood-1"))
        notice: AdapterEvent | None = None
        for _ in range(100):
            await asyncio.sleep(0)
            while not adapter._events.empty():
                candidate = adapter._events.get_nowait()
                if candidate is not None and candidate.raw.get("codexMethod") == "ar/load-shed":
                    notice = candidate
            if notice is not None:
                break
        assert notice is not None
        payload = notice.raw[AR_EVIDENCE_KEY]
        assert isinstance(payload, dict)
        assert payload["droppedEvents"] > 0
        assert live_snapshot(adapter).control == "ready"
    finally:
        await adapter.stop("forced")


async def _flood_deltas(
    adapter: CodexAppServerAdapter, transport: FakeCodexTransport, thread: str
) -> int:
    """A pure-delta flood past the queue limit; returns the exact shed count."""

    total = ADAPTER_EVENT_QUEUE_LIMIT + 76
    for _ in range(total):
        transport.emit(
            notification(
                "item/agentMessage/delta",
                agent_message_delta_params(thread, f"{thread}-turn", f"{thread}-msg", "x"),
            )
        )
    await eventually(lambda: adapter._event_sequence == total)
    assert adapter._dropped_events == 76
    return 76


@pytest.mark.anyio
async def test_load_shed_notice_crosses_on_consumer_drain_without_new_traffic() -> None:
    """Flood → full drain → producer silent: the consumer side mints the notice."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        dropped = await _flood_deltas(adapter, transport, "flood-silent")

        # No new producer events: the notice must cross purely off the consumer
        # drain — it is the LAST event the subscriber sees, exactly counted.
        seen: list[AdapterEvent] = []
        events = adapter.subscribe()
        while True:
            event = await next_event(events)
            seen.append(event)
            if event.raw.get("codexMethod") == "ar/load-shed":
                break
        notice = seen[-1]
        assert notice.raw.get("codexMethod") == "ar/load-shed"
        payload = notice.raw[AR_EVIDENCE_KEY]
        assert isinstance(payload, dict)
        assert payload["droppedEvents"] == dropped
        assert len(seen) == ADAPTER_EVENT_QUEUE_LIMIT + 1  # queue contents + notice
        assert adapter._dropped_events == 0
        assert live_snapshot(adapter).control == "ready"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_load_shed_notice_precedes_the_close_sentinel_on_stop() -> None:
    """Flood → drain → stop(): the notice mints BEFORE the sentinel, fully counted."""

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        dropped = await _flood_deltas(adapter, transport, "flood-stop")
        # A synchronous drain leaves the residual shed accounting behind.
        while not adapter._events.empty():
            adapter._events.get_nowait()
        assert adapter._dropped_events == dropped

        await adapter.stop("forced")
        # The subscriber sees the notice, then termination — the minted order ends
        # with [ar/load-shed, close sentinel], never the other way around.
        seen: list[AdapterEvent] = []
        async for event in adapter.subscribe():
            seen.append(event)
        assert len(seen) == 1
        assert seen[0].raw.get("codexMethod") == "ar/load-shed"
        payload = seen[0].raw[AR_EVIDENCE_KEY]
        assert isinstance(payload, dict)
        assert payload["droppedEvents"] == dropped
        assert adapter._dropped_events == 0
    finally:
        await adapter.stop("forced")
