"""What the Codex adapter does with frames it did not initiate, and with partial collab shapes.

Three seams meet here, and each one decides something the seat cannot recover from if it is
wrong. ``turn/start`` params decide what the vendor is told about policies it would otherwise
keep. ``turn/started`` and ``thread/settings/updated`` decide whether a frame is the *seat's*
state or a sub-agent's evidence -- the same wire shape means opposite things depending on its
``threadId``. And the collab items that bind the agent registry arrive partially populated in
the wild, so each field has to bind on its own without the missing ones discarding what is
already known.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import cast

import pytest
from _agent_wire_fixtures import (
    CollabAgents,
    collab_agent_tool_call_item,
    item_completed_params,
    notification,
    sub_agent_activity_item,
    turn_started_params,
)
from agents_remember.errors import HarnessAdapterBusyError
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_METHOD_KEY,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
)
from test_codex_adapter_thread_demux import agent_registry, eventually, live_snapshot
from test_codex_app_server_adapter import (
    TEST_SETTINGS,
    FakeCodexTransport,
    fixture,
    fixture_object,
    launch,
    make_adapter,
    next_event_of_kind,
    prime_start,
    request,
)

PARENT = "thread-1"
"""The session thread the ``threadStartResult`` fixture establishes."""

AGENT = "agent-thread-registry"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def only_turn_start(transport: FakeCodexTransport) -> JsonObject:
    starts = [params for method, params in transport.requests if method == "turn/start"]
    assert len(starts) == 1
    return starts[0]


def parent_item(item: JsonObject) -> JsonObject:
    """One ``item/completed`` notification for a parent-thread collab item."""

    return notification("item/completed", item_completed_params(PARENT, "turn-collab", item))


async def settled_evidence(events: AsyncIterator[AdapterEvent]) -> Mapping[str, object]:
    """Wait for the raw-evidence event one collab item produces, and hand it back.

    Collab items have no transcript projection, so every one of them crosses as exactly one
    ``codex-notification``. Waiting for it is what makes the registry assertions that follow
    a statement about a settled frame rather than a race with the message pump.
    """

    return (await next_event_of_kind(events, "codex-notification")).raw


@pytest.mark.anyio
async def test_turn_start_sends_only_the_policies_the_seat_configured() -> None:
    """An unset policy is omitted entirely; a configured one rides as plain JSON data.

    ``turn/start`` inherits the thread's configuration for every policy it does not name, so
    sending ``null`` for an unset one would tell the server to clear what the thread was
    started with. A configured mapping is copied into a plain ``dict`` on the way out: these
    params are about to be serialized as JSON-RPC, and a live view of the settings object is
    neither generally serializable nor stable against a later edit of those settings.
    """

    data = fixture()
    bare_transport = FakeCodexTransport()
    prime_start(bare_transport, data)
    bare_transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    bare = make_adapter(bare_transport)
    await bare.start(launch())
    try:
        await bare.submit(request("no-policies"))
        bare_params = only_turn_start(bare_transport)
        assert "approvalPolicy" not in bare_params
        assert "approvalsReviewer" not in bare_params
        assert "sandboxPolicy" not in bare_params
        assert bare_params["clientUserMessageId"] == "no-policies"
    finally:
        await bare.stop("forced")

    sandbox = {"type": "workspaceWrite", "writableRoots": ["/workspace"]}
    transport = FakeCodexTransport()
    prime_start(transport, data)
    transport.queue_response("turn/start", fixture_object(data, "turnStartResult"))
    adapter = make_adapter(
        transport,
        replace(
            TEST_SETTINGS,
            approval_policy="on-request",
            approvals_reviewer="user",
            turn_sandbox_policy=MappingProxyType(sandbox),
        ),
    )
    await adapter.start(launch())
    try:
        await adapter.submit(request("policies"))
        params = only_turn_start(transport)
        assert params["approvalPolicy"] == "on-request"
        assert params["approvalsReviewer"] == "user"
        assert params["sandboxPolicy"] == sandbox
        # Copied, not aliased: a MappingProxyType would not survive JSON serialization.
        assert isinstance(params["sandboxPolicy"], dict)
        assert params["sandboxPolicy"] is not sandbox
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_a_turn_the_seat_did_not_dispatch_still_makes_it_busy() -> None:
    """``turn/started`` on the session thread owns the vendor, whoever began the turn.

    The human at the terminal can start a turn the bridge never wrote, and a resumed thread
    can already be mid-turn. The seat has to read as running from that notification alone,
    because the adapter's preflight is the guard that keeps a queued submission from being
    written into a vendor that is already working.
    """

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    try:
        assert live_snapshot(adapter).activity == "idle"
        transport.emit(notification("turn/started", turn_started_params(PARENT, "turn-human")))
        await eventually(lambda: live_snapshot(adapter).activity == "running")

        snapshot = live_snapshot(adapter)
        assert snapshot.acceptance == "immediate"
        assert snapshot.raw["activeTurnId"] == "turn-human"
        assert adapter._active_turn_id == "turn-human"
        # The session thread is the seat, never an entry in the sub-agent registry.
        assert "agentRegistry" not in snapshot.raw

        operation = request("queued-behind-the-human").operation
        assert operation is not None
        with pytest.raises(HarnessAdapterBusyError):
            await adapter.preflight_operation(operation)
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_a_sub_agents_settings_frame_is_evidence_not_the_seats_settings() -> None:
    """The same settings frame is inert on a sub-agent thread and fatal on the seat's.

    ``thread/settings/updated`` says nothing about whose settings it describes beyond its
    ``threadId``. Routed by thread, a sub-agent's frame crosses as raw evidence and the seat
    keeps the selection it deliberately made. Routed into the seat's settings authority, that
    very same payload is undeclared drift and fails the bridge -- which is what makes the
    demux load-bearing rather than cosmetic.
    """

    drifted: JsonObject = {"model": "gpt-5.6-mini", "effort": "low"}
    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        transport.emit(
            notification(
                "thread/settings/updated",
                {"threadId": AGENT, "threadSettings": dict(drifted)},
            )
        )
        evidence = await next_event_of_kind(events, "codex-notification")
        assert evidence.raw[AR_EVIDENCE_METHOD_KEY] == "thread/settings/updated"

        assert live_snapshot(adapter).control == "ready"
        assert adapter.advertise().selected_model_key == "gpt-5.6-sol"
        assert adapter.advertise().selected_effort == "xhigh"
        assert live_snapshot(adapter).raw["effectiveReasoningEffort"] == "xhigh"
    finally:
        await adapter.stop("forced")

    seat_transport = FakeCodexTransport()
    prime_start(seat_transport, data)
    seat = make_adapter(seat_transport)
    await seat.start(launch())
    try:
        seat_transport.emit(
            notification(
                "thread/settings/updated",
                {"threadId": PARENT, "threadSettings": dict(drifted)},
            )
        )
        await eventually(lambda: live_snapshot(seat).control == "failed")
        assert "outside the deliberate adapter setter" in str(
            live_snapshot(seat).raw["protocolError"]
        )
    finally:
        await seat.stop("forced")


@pytest.mark.anyio
async def test_sub_agent_activity_binds_only_the_facets_it_carries() -> None:
    """A partial ``subAgentActivity`` enriches the registry; it never erases it.

    The vendor's own struct requires all four fields, so anything short of that is a shape
    this adapter must survive rather than trust. Losing a bound ``agentPath`` because a later
    frame omitted it would rename a live agent back to its thread-id stub in the cockpit, and
    a frame with no ``agentThreadId`` names nobody at all -- it cannot mint a registry entry.
    """

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        transport.emit(
            parent_item(
                sub_agent_activity_item(
                    "sa-1", kind="started", agent_thread_id=AGENT, agent_path="/root/reviewer"
                )
            )
        )
        await settled_evidence(events)
        assert agent_registry(adapter) == {
            AGENT: {"status": "started", "agentPath": "/root/reviewer"}
        }

        # Thread id only: neither facet is present, so neither facet moves.
        transport.emit(
            parent_item({"id": "sa-2", "type": "subAgentActivity", "agentThreadId": AGENT})
        )
        await settled_evidence(events)
        assert agent_registry(adapter) == {
            AGENT: {"status": "started", "agentPath": "/root/reviewer"}
        }

        # No thread id: the activity names nobody, so nothing is registered for it.
        known_threads = set(adapter._threads.thread_ids())
        transport.emit(
            parent_item(
                {
                    "id": "sa-3",
                    "type": "subAgentActivity",
                    "kind": "interrupted",
                    "agentPath": "/root/nobody",
                }
            )
        )
        raw = await settled_evidence(events)
        assert raw[AR_EVIDENCE_METHOD_KEY] == "item/completed"
        assert set(adapter._threads.thread_ids()) == known_threads
        assert agent_registry(adapter) == {
            AGENT: {"status": "started", "agentPath": "/root/reviewer"}
        }

        # A frame that does carry a kind still moves it, so the assertions above are not vacuous.
        transport.emit(
            parent_item(
                sub_agent_activity_item(
                    "sa-4", kind="interacted", agent_thread_id=AGENT, agent_path="/root/reviewer"
                )
            )
        )
        await settled_evidence(events)
        assert agent_registry(adapter)[AGENT]["status"] == "interacted"
    finally:
        await adapter.stop("forced")


@pytest.mark.anyio
async def test_collab_tool_call_registers_only_well_formed_receivers_and_states() -> None:
    """Receivers may mint an agent thread; reported states may only update one that exists.

    ``receiverThreadIds`` is who the call is addressed to, which is why it is allowed to
    create the registry entry. ``agentsStates`` is a claim *about* threads, so a status for a
    thread this connection has never seen -- or one whose body is not the state object the
    vendor documents -- is left to cross as raw evidence instead of inventing an agent the
    cockpit would then show forever.
    """

    data = fixture()
    transport = FakeCodexTransport()
    prime_start(transport, data)
    adapter = make_adapter(transport)
    await adapter.start(launch())
    events = adapter.subscribe()
    try:
        transport.emit(
            parent_item(
                collab_agent_tool_call_item(
                    "collab-1",
                    "spawnAgent",
                    agents=CollabAgents(
                        PARENT,
                        receiver_thread_ids=cast(list[str], ["agent-a", "", 7]),
                    ),
                )
            )
        )
        await settled_evidence(events)
        assert agent_registry(adapter) == {"agent-a": {"status": "unresolved"}}

        # A wait call reporting states: neither a malformed body nor an unknown thread lands.
        transport.emit(
            parent_item(
                collab_agent_tool_call_item(
                    "collab-2",
                    "wait",
                    agents=CollabAgents(
                        PARENT,
                        states=cast(
                            "dict[str, JsonObject]",
                            {"agent-a": "running", "agent-ghost": {"status": "errored"}},
                        ),
                    ),
                )
            )
        )
        await settled_evidence(events)
        assert agent_registry(adapter) == {"agent-a": {"status": "unresolved"}}
        assert adapter._threads.state("agent-ghost") is None

        # A well-formed body whose status is not text is the same non-event.
        transport.emit(
            parent_item(
                collab_agent_tool_call_item(
                    "collab-3",
                    "wait",
                    agents=CollabAgents(PARENT, states={"agent-a": {"status": 3}}),
                )
            )
        )
        await settled_evidence(events)
        assert agent_registry(adapter) == {"agent-a": {"status": "unresolved"}}

        # The documented shape does move it, so the three assertions above are not vacuous.
        transport.emit(
            parent_item(
                collab_agent_tool_call_item(
                    "collab-4",
                    "wait",
                    agents=CollabAgents(PARENT, states={"agent-a": {"status": "running"}}),
                )
            )
        )
        await settled_evidence(events)
        assert agent_registry(adapter) == {"agent-a": {"status": "running"}}
    finally:
        await adapter.stop("forced")
