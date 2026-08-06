from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from typing import Any, cast

from agents_remember.errors import HarnessControlError, HarnessInteractionNotPendingError
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_adapter import HarnessProtocolRegistry
from agents_remember.serving.harness_control_bridge import BridgeLimits, HarnessControlBridge
from agents_remember.serving.harness_control_client import _snapshot as parse_snapshot_wire
from agents_remember.serving.harness_control_models import (
    AcceptanceState,
    ActivityState,
    AdapterEvent,
    AdapterSnapshot,
    InteractionResponse,
    PendingInteraction,
    TerminalResult,
    TranscriptEntry,
    pending_interaction_json,
    snapshot_json,
)
from agents_remember.serving.harness_terminal_surface import HarnessTerminalSurface
from agents_remember.serving.hosted_control_projection import control_snapshot_entry
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry
from test_harness_control import (
    _BlockingSetAdapter,
    _BlockingSubmitAdapter,
    _catalog_entry,
    _FakeAdapter,
    _identity,
    _launch,
    _settle_events,
)


class HarnessControlConformanceTests1(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_ordered_terminal_durable_acceptance(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.acceptances.append("immediate")
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: "2026-07-13T18:00:00+00:00")
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        try:
            first = await surface.submit_terminal("terminal prompt", request_id="request-1")
            second = await surface.submit_durable("durable prompt", request_id="request-2")
            adapter.complete("request-1")
            await _settle_events()

            self.assertEqual((first.acceptance, second.acceptance), ("immediate", "queued"))
            self.assertEqual(
                [(item.request_id, item.source) for item in adapter.submissions],
                [("request-1", "terminal"), ("request-2", "durable")],
            )
            self.assertEqual(first.vendor_correlation_id, "vendor-request-1")
            self.assertEqual(adapter.launches, [_launch(identity)])
            with self.assertRaisesRegex(HarnessControlError, "already started"):
                await bridge.start(_launch(identity))
        finally:
            await bridge.stop("forced")

    async def test_capability_setters_share_launch_set_prompt_queue_order(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            model_task = asyncio.create_task(bridge.submissions().set_model("model-b"))
            await asyncio.sleep(0)
            prompt_task = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("after set", source="durable", request_id="after-set")
                )
            )
            model, receipt = await asyncio.gather(model_task, prompt_task)
            self.assertEqual((model.ok, model.acceptance), (True, "immediate"))
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(
                adapter.control_log,
                [
                    ("launch", "fake"),
                    ("model", "model-b"),
                    ("prompt", "after-set"),
                ],
            )
        finally:
            await bridge.stop("forced")

    async def test_cancelled_setter_late_completion_does_not_kill_command_queue(self) -> None:
        identity = _identity()
        adapter = _BlockingSetAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            setter = asyncio.create_task(bridge.submissions().set_model("model-b"))
            await asyncio.wait_for(adapter.set_started.wait(), timeout=1.0)
            setter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await setter
            adapter.release_set.set()

            receipt = await asyncio.wait_for(
                bridge.submissions().submit(
                    bridge.prompt(
                        "still alive",
                        source="durable",
                        request_id="after-cancel",
                    )
                ),
                timeout=1.0,
            )
            self.assertEqual(receipt.acceptance, "queued")
            await _settle_events()
            self.assertEqual(adapter.control_log[-1], ("prompt", "after-cancel"))
            self.assertEqual(bridge.snapshot().control, "ready")
        finally:
            await bridge.stop("forced")

    async def test_bad_set_result_installs_resolvable_unknown_barrier_without_poisoning(
        self,
    ) -> None:
        invalid = (
            SetResult(True, "echo-verified", "value"),
            SetResult(True, "queued", "value", effective_value="value"),
            SetResult(True, "unknown", "value"),
            SetResult(False, "immediate", "value"),
            SetResult(False, "unsupported", "value", effective_value="value"),
            SetResult(False, cast(Any, "garbage"), "value"),
            SetResult(False, cast(Any, "rejected"), "value"),
            SetResult(False, cast(Any, ""), "value"),
        )
        for result in invalid:
            with self.subTest(result=result):
                identity = _identity()
                adapter = _FakeAdapter()
                adapter.set_results.append(result)
                bridge = HarnessControlBridge(identity, adapter)
                await bridge.start(_launch(identity))
                try:
                    projected = await bridge.submissions().set_model("value")
                    self.assertEqual((projected.ok, projected.acceptance), (False, "unknown"))
                    receipt = await bridge.submissions().submit(
                        bridge.prompt(
                            "runner survives",
                            source="durable",
                            request_id="survives",
                        )
                    )
                    self.assertEqual(receipt.acceptance, "queued")
                    operation = adapter.setter_operations[-1]
                    await bridge.submissions().resolve_operation(
                        operation.operation_id,
                        "set-model",
                        resolution="not-applied",
                        detail="operator rejected incoherent setter evidence",
                    )
                    await _settle_events()
                    self.assertEqual(len(adapter.submissions), 1)
                finally:
                    await bridge.stop("forced")

    async def test_unregistered_adapter_setters_remain_explicitly_unsupported(self) -> None:
        adapter = HarnessProtocolRegistry().create("custom-harness")
        model = await adapter.set_model("anything")
        effort = await adapter.set_effort("anything")
        self.assertEqual((model.ok, model.acceptance), (False, "unsupported"))
        self.assertEqual((effort.ok, effort.acceptance), (False, "unsupported"))

    async def test_automated_delivery_preserves_draft_and_draft_submits_next(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        try:
            surface.update_draft("human draft in progress")
            automated = await surface.submit_durable(
                "automated whole message", request_id="delivery-1"
            )
            self.assertEqual(automated.acceptance, "immediate")
            self.assertEqual(surface.draft.text, "human draft in progress")
            adapter.complete("delivery-1")
            await _settle_events()

            committed = await surface.submit_draft(request_id="draft-1")
            self.assertEqual(committed.acceptance, "immediate")
            self.assertEqual(
                [(item.source, item.text) for item in adapter.submissions],
                [
                    ("durable", "automated whole message"),
                    ("terminal", "human draft in progress"),
                ],
            )
            self.assertEqual(surface.draft.text, "")
            self.assertFalse(hasattr(surface, "discard_draft"))
        finally:
            await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_conformance_1.py:202).
    async def test_newer_human_edit_survives_in_flight_draft_submission(
        self,
    ) -> None:  # pragma: no cover
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        surface.update_draft("committed revision")
        submission = asyncio.create_task(surface.submit_draft(request_id="draft-1"))
        try:
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            surface.update_draft("newer uncommitted revision")
            adapter.release_submit.set()
            await asyncio.wait_for(submission, timeout=1.0)
            self.assertEqual(surface.draft.text, "newer uncommitted revision")
            self.assertEqual([item.text for item in adapter.submissions], ["committed revision"])
        finally:
            adapter.release_submit.set()
            if not submission.done():
                submission.cancel()
                await asyncio.gather(submission, return_exceptions=True)
            await bridge.stop("forced")

    async def test_ambiguous_draft_submission_retains_human_text(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.disconnects.append(True)
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        surface.update_draft("retain until acceptance is known")
        try:
            receipt = await surface.submit_draft(request_id="draft-unknown")
            self.assertEqual(receipt.acceptance, "unknown")
            self.assertEqual(surface.draft.text, "retain until acceptance is known")
            self.assertEqual(len(adapter.submissions), 1)
        finally:
            await bridge.stop("forced")

    async def test_snapshot_subscription_is_bounded_and_coalesces_to_latest_state(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter, limits=BridgeLimits(subscriber_queue=1))
        await bridge.start(_launch(identity))
        stream = bridge.subscribe()
        try:
            initial = await anext(stream)
            self.assertEqual(initial.activity, "idle")
            updates: tuple[tuple[int, ActivityState], ...] = (
                (1, "running"),
                (2, "settling"),
            )
            for sequence, activity in updates:
                adapter.emit(
                    AdapterEvent(
                        sequence=sequence,
                        kind="state",
                        identity=identity,
                        created_at=f"time-{sequence}",
                        snapshot=replace(
                            bridge.snapshot(),
                            activity=activity,
                            acceptance="queued",
                        ),
                    )
                )
                await _settle_events()
            latest = await asyncio.wait_for(anext(stream), timeout=1.0)
            self.assertEqual(latest.activity, "settling")
        finally:
            await stream.aclose()
            await bridge.stop("forced")

    async def test_an_adapter_that_fails_to_stop_is_reported_as_failed_not_disconnected(
        self,
    ) -> None:
        """A stop that did not happen must not be published as a clean disconnect.

        ``disconnected`` is the word for "the harness is gone and nothing is running". If
        the adapter raised on the way out, the vendor process may well still be alive, and
        a subscriber that saw ``disconnected`` would offer to start a second one on the same
        tmux name. The bridge therefore publishes ``failed`` with the error, and re-raises
        as a `HarnessControlError` so the caller does not meet a raw vendor exception.
        """
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.stop_error = RuntimeError("vendor process refused to die")
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        stream = bridge.subscribe()
        try:
            await anext(stream)

            with self.assertRaisesRegex(
                HarnessControlError, "unexpected adapter RuntimeError during control stop"
            ):
                await bridge.stop("graceful")

            published = await asyncio.wait_for(anext(stream), timeout=1.0)
            self.assertEqual(
                (published.control, published.activity, published.acceptance),
                ("failed", "unknown", "rejected"),
            )
            self.assertIn("vendor process refused to die", str(published.raw["bridgeError"]))
            # And the failure is the bridge's own state, not just something a subscriber saw.
            self.assertEqual(bridge.snapshot().control, "failed")
        finally:
            await stream.aclose()

    async def test_busy_blocked_settling_completion_and_readable_transcript(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            receipt = await bridge.submissions().submit(
                bridge.prompt("run work", source="terminal", request_id="request-1")
            )
            self.assertEqual(receipt.acceptance, "immediate")
            operation = adapter.submissions[-1].operation
            assert operation is not None
            pending = PendingInteraction(
                interaction_id="approval-1",
                kind="approval",
                prompt="Allow command?",
                created_at="2026-07-13T18:01:00+00:00",
                choices=("allow", "deny"),
            )
            states: tuple[tuple[ActivityState, AcceptanceState, PendingInteraction | None], ...] = (
                ("running", "queued", None),
                ("blocked", "rejected", pending),
                ("settling", "queued", None),
            )
            for sequence, (activity, acceptance, interaction) in enumerate(states, start=1):
                snapshot = AdapterSnapshot(
                    identity=identity,
                    control="ready",
                    activity=activity,
                    acceptance=acceptance,
                    vendor_session_id="vendor-session-1",
                    pending_interaction=interaction,
                )
                adapter.emit(
                    AdapterEvent(
                        sequence=sequence,
                        kind="state",
                        identity=identity,
                        created_at=f"2026-07-13T18:0{sequence}:00+00:00",
                        snapshot=snapshot,
                    )
                )
                await _settle_events()
                self.assertEqual(bridge.snapshot().activity, activity)

            completed = AdapterSnapshot(
                identity=identity,
                control="ready",
                activity="idle",
                acceptance="immediate",
                vendor_session_id="vendor-session-1",
            )
            entry = TranscriptEntry(
                sequence=1,
                role="result",
                text="done\x1b[31m",
                created_at="2026-07-13T18:04:00+00:00",
                request_id="request-1",
                terminal_result=TerminalResult(
                    outcome="completed", completed_at="2026-07-13T18:04:00+00:00"
                ),
            )
            adapter.emit(
                AdapterEvent(
                    sequence=4,
                    kind="completed",
                    identity=identity,
                    created_at=entry.created_at,
                    snapshot=completed,
                    transcript=(entry,),
                    operation=operation,
                )
            )
            await _settle_events()

            self.assertEqual(bridge.snapshot().activity, "idle")
            rendered = HarnessTerminalSurface(bridge).render().decode()
            self.assertIn("result=completed", rendered)
            self.assertNotIn("\x1b", rendered)
        finally:
            await bridge.stop("forced")

    async def test_pending_interaction_response_uses_adapter_snapshot(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            await bridge.submissions().submit(
                bridge.prompt("ask", source="terminal", request_id="question-operation")
            )
            operation = adapter.submissions[-1].operation
            assert operation is not None
            pending = PendingInteraction(
                interaction_id="question-1",
                kind="question",
                prompt="Continue?",
                created_at="2026-07-13T18:00:00+00:00",
            )
            blocked = replace(
                bridge.snapshot(),
                activity="blocked",
                acceptance="rejected",
                pending_interaction=pending,
            )
            adapter.emit(AdapterEvent(1, "state", identity, pending.created_at, snapshot=blocked))
            await _settle_events()

            result = await bridge.submissions().respond(
                InteractionResponse(
                    interaction_id="question-1",
                    response="continue",
                    responded_at="2026-07-13T18:01:00+00:00",
                )
            )
            self.assertEqual(result.activity, "settling")
            self.assertIsNone(result.pending_interaction)
            self.assertEqual(adapter.responses[-1].operation, operation)
        finally:
            await bridge.stop("forced")

    async def test_subagent_pending_interaction_responds_without_parent_operation(self) -> None:
        """Multiplexed sub-agent approvals answer through the authority.

        Agent entries ride the plural ``pending_interactions`` tuple and own no parent
        operation, so the active-operation guard must not strand them; the response routes
        to the adapter with no operation attached, and an unknown id is still refused.
        """

        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            agent_pending = PendingInteraction(
                interaction_id="agent-approval-1",
                kind="permission",
                prompt="Allow the sub-agent command?",
                created_at="2026-07-13T18:00:00+00:00",
                choices=("allow", "deny"),
                raw={"threadId": "agent-thread-1", "agentLabel": "agent agent-t"},
            )
            blocked = replace(
                bridge.snapshot(),
                pending_interactions=(agent_pending,),
            )
            adapter.emit(
                AdapterEvent(1, "state", identity, agent_pending.created_at, snapshot=blocked)
            )
            await _settle_events()

            # No ordinary operation was ever submitted: the guard is parent-only.
            result = await bridge.submissions().respond(
                InteractionResponse(
                    interaction_id="agent-approval-1",
                    response="allow",
                    responded_at="2026-07-13T18:01:00+00:00",
                )
            )
            self.assertEqual(result.pending_interactions, ())
            self.assertIsNone(result.pending_interaction)
            self.assertEqual(adapter.responses[-1].interaction_id, "agent-approval-1")
            self.assertIsNone(adapter.responses[-1].operation)

            with self.assertRaises(HarnessInteractionNotPendingError):
                await bridge.submissions().respond(
                    InteractionResponse(
                        interaction_id="agent-approval-unknown",
                        response="allow",
                        responded_at="2026-07-13T18:02:00+00:00",
                    )
                )
            # The refused response never reached the adapter.
            self.assertEqual(len(adapter.responses), 1)
        finally:
            await bridge.stop("forced")

    async def test_parent_thread_tuple_entry_gets_the_operation_guard(self) -> None:
        """Parent-ness is decided by the entry's thread, not by which slot carries it.

        Concurrent parent pendings beyond the singular slot's oldest ride the plural
        tuple (the adapter's per-thread pending map); a tuple entry whose threadId is
        the session's vendor thread gets the active-operation guard exactly like the
        singular slot, instead of being answered operation-free like an agent entry.
        """

        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            parent_second = PendingInteraction(
                interaction_id="parent-approval-2",
                kind="permission",
                prompt="Allow the second parent command?",
                created_at="2026-07-13T18:00:00+00:00",
                choices=("allow", "deny"),
                raw={"threadId": "vendor-session-1"},
            )
            agent_pending = PendingInteraction(
                interaction_id="agent-approval-1",
                kind="permission",
                prompt="Allow the sub-agent command?",
                created_at="2026-07-13T18:00:30+00:00",
                choices=("allow", "deny"),
                raw={"threadId": "agent-thread-1", "agentLabel": "agent agent-t"},
            )
            blocked = replace(
                bridge.snapshot(),
                pending_interactions=(parent_second, agent_pending),
            )
            adapter.emit(
                AdapterEvent(1, "state", identity, agent_pending.created_at, snapshot=blocked)
            )
            await _settle_events()

            # No active ordinary operation: the parent-thread tuple entry is guarded...
            with self.assertRaises(HarnessInteractionNotPendingError):
                await bridge.submissions().respond(
                    InteractionResponse(
                        interaction_id="parent-approval-2",
                        response="allow",
                        responded_at="2026-07-13T18:01:00+00:00",
                    )
                )
            # ...while the agent entry still answers without one.
            await bridge.submissions().respond(
                InteractionResponse(
                    interaction_id="agent-approval-1",
                    response="allow",
                    responded_at="2026-07-13T18:02:00+00:00",
                )
            )
            self.assertIsNone(adapter.responses[-1].operation)
        finally:
            await bridge.stop("forced")

    def test_multiplexed_pending_interactions_serialize_through_every_surface(self) -> None:
        """The plural pending tuple survives snapshot/catalog/client wires."""

        identity = _identity()
        parent = PendingInteraction(
            interaction_id="parent-question-1",
            kind="question",
            prompt="Parent asks",
            created_at="2026-07-13T18:00:00+00:00",
        )
        agent = PendingInteraction(
            interaction_id="agent-approval-1",
            kind="permission",
            prompt="Agent asks",
            created_at="2026-07-13T18:00:30+00:00",
            choices=("allow", "deny"),
            raw={"threadId": "agent-thread-1", "agentLabel": "agent agent-t"},
        )
        snapshot = AdapterSnapshot(
            identity=identity,
            control="ready",
            activity="blocked",
            acceptance="queued",
            pending_interaction=parent,
            pending_interactions=(parent, agent),
            raw={},
        )

        wire = snapshot_json(snapshot)
        self.assertEqual(wire["pendingInteraction"], pending_interaction_json(parent))
        self.assertEqual(
            wire["pendingInteractions"],
            [pending_interaction_json(parent), pending_interaction_json(agent)],
        )

        # The control client parses the additive field back (a pre-multiplex bridge omits it).
        parsed = parse_snapshot_wire(wire)
        self.assertEqual(parsed.pending_interactions, (parent, agent))
        legacy = parse_snapshot_wire(
            {key: value for key, value in wire.items() if key != "pendingInteractions"}
        )
        self.assertEqual(legacy.pending_interactions, ())

        # The catalog projection + catalog row round-trip carry it too.
        entry = _catalog_entry(identity)
        projected = control_snapshot_entry(entry, snapshot)
        self.assertIsNotNone(projected.control_pending_interactions)
        assert projected.control_pending_interactions is not None
        self.assertEqual(
            [item["interactionId"] for item in projected.control_pending_interactions],
            ["parent-question-1", "agent-approval-1"],
        )
        restored = TerminalCatalogEntry.from_json(projected.to_json())
        self.assertEqual(
            restored.control_pending_interactions, projected.control_pending_interactions
        )
        # An empty multiplex never writes the additive key.
        empty = control_snapshot_entry(entry, replace(snapshot, pending_interactions=()))
        self.assertIsNone(empty.control_pending_interactions)
        self.assertNotIn("controlPendingInteractions", empty.to_json())
