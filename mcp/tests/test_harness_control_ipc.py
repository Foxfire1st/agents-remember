from __future__ import annotations

import asyncio
import json
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    InteractionQuestion,
    InteractionQuestionOption,
    PendingInteraction,
    SubmissionReceipt,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.conversation.authorization import LocalOperatorAuthorizationResolver
from agents_remember.serving.conversation.runtime import ConversationRuntime, ConversationScope
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_api import register_harness_control_routes
from agents_remember.serving.harness_control_bridge import BridgeLimits, HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlPlaneClient,
    ControlSubmission,
    read_control_capabilities,
    read_control_snapshot,
    read_submission_authority,
    read_submission_status,
    reconcile_control_prompt,
    request_control,
    set_control_effort,
    set_control_model,
    submit_control_prompt,
    withdraw_control_submission,
)
from agents_remember.serving.harness_control_ipc import (
    HarnessControlClient,
    HarnessControlServer,
    LocalControlEndpoint,
)
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    AdapterEvent,
    ReconciliationResult,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import InboxDeliveryLog, deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost, TerminalHostSeams
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalLivenessObservation,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_harness_control import (
    _BlockingSubmitAdapter,
    _ControlledEntry,
    _DropFirstSubmitResponseServer,
    _FakeAdapter,
    _identity,
    _launch,
    _ObservedHarnessControlServer,
    _settle_events,
)


class HarnessControlIpcTests(unittest.IsolatedAsyncioTestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_ipc.py:78).
    async def test_private_lifecycle_status_and_withdraw_round_trip(
        self,
    ) -> None:  # pragma: no cover
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity("lifecycle-ipc")
            adapter = _BlockingSubmitAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(
                identity.ar_session_id,
                identity.tmux_name,
                identity.created_at,
                endpoint.path,
            )
            first_task: asyncio.Task[SubmissionReceipt] | None = None
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                first_task = asyncio.create_task(
                    asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "hold the ordinary lane",
                        ControlSubmission(source="durable", request_id="active-durable"),
                    )
                )
                await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
                queued = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "withdraw this exact text",
                    ControlSubmission(
                        source="cockpit",
                        request_id="cockpit-queued",
                        expected_bridge_epoch=descriptor.bridge_epoch,
                    ),
                )
                self.assertEqual(queued.acceptance, "queued")

                status = await asyncio.to_thread(
                    read_submission_status,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                    request_ids=("cockpit-queued", "missing"),
                )
                queued_status = status.submissions[0].submission
                self.assertIsNotNone(queued_status)
                assert queued_status is not None
                self.assertEqual(
                    (queued_status.state, queued_status.withdrawable), ("queued", True)
                )
                self.assertEqual(status.submissions[1].outcome, "not-found")

                withdrawn = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                    request_id="cockpit-queued",
                )
                self.assertEqual((withdrawn.outcome, withdrawn.state), ("withdrawn", "withdrawn"))
            finally:
                adapter.release_submit.set()
                if first_task is not None:
                    await first_task
                await server.close()
                await bridge.stop("forced")

    async def test_exact_session_ipc_advertises_and_returns_set_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _FakeAdapter()
            adapter.set_results.extend(
                (
                    SetResult(True, "queued", "model-b", detail="next turn"),
                    SetResult(False, "unsupported", "max", detail="model gated"),
                )
            )
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(
                identity.ar_session_id,
                identity.tmux_name,
                identity.created_at,
                endpoint.path,
            )
            try:
                capabilities = await asyncio.to_thread(read_control_capabilities, entry)
                model = await asyncio.to_thread(set_control_model, entry, "model-b")
                effort = await asyncio.to_thread(set_control_effort, entry, "max")
                self.assertEqual(capabilities.models, ())
                self.assertEqual((model.ok, model.acceptance), (True, "queued"))
                self.assertEqual((effort.ok, effort.acceptance), (False, "unsupported"))
                self.assertEqual(
                    adapter.control_log[-2:], [("model", "model-b"), ("effort", "max")]
                )
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_outer_socket_lost_receipt_reconciles_retained_known_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity("outer-loss")
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = _DropFirstSubmitResponseServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(
                identity.ar_session_id,
                identity.tmux_name,
                identity.created_at,
                endpoint.path,
            )
            try:
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "one complete message",
                    ControlSubmission(source="durable", request_id="outer-loss-request"),
                )
                self.assertEqual(receipt.acceptance, "unknown")
                self.assertEqual(receipt.request_id, "outer-loss-request")
                await asyncio.wait_for(server.dropped.wait(), timeout=1.0)

                reconciled = await asyncio.to_thread(
                    reconcile_control_prompt, entry, "outer-loss-request"
                )

                self.assertEqual(reconciled.state, "accepted")
                self.assertEqual(
                    reconciled.vendor_correlation_id,
                    "vendor-outer-loss-request",
                )
                self.assertEqual(len(adapter.submissions), 1)
                self.assertEqual(adapter.reconciliation_requests, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_durable_inbox_outer_loss_converges_by_reconcile_without_resend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            root = Path(tmp_str)
            identity = _identity("durable-outer-loss")
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(root / "control", identity)
            server = _DropFirstSubmitResponseServer(endpoint, bridge)
            await server.start()
            catalog = TerminalCatalog(root / "terminal-sessions.json")
            catalog.upsert(
                TerminalCatalogEntry(
                    id=identity.ar_session_id,
                    label="Worker",
                    kind="harness",
                    harness="claude",
                    lifecycle_id="L1",
                    cwd=root,
                    tmux_name=identity.tmux_name,
                    command=("claude",),
                    created_at=identity.created_at,
                    last_attached_at=identity.created_at,
                    status="running",
                    control_state="ready",
                    control_endpoint=endpoint.path,
                    control_protocol=CONTROL_PROTOCOL_VERSION,
                )
            )
            store = OperatorInboxStore(root)
            inbox = create_operator_inbox_entry(
                InboxMessage(ask="Continue", response="Review the result"),
                entry_id="durable-request",
                now=identity.created_at,
                routing=InboxRouting(
                    address=InboxAddress(lifecycle_id="L1", agent_id=identity.ar_session_id)
                ),
                poster=InboxPoster(created_by="manager", created_via="cli"),
            )
            store.append(inbox)
            paster = mock.Mock()
            host = TerminalHost(TerminalHostSeams(tmux_probe=lambda _name: True))
            try:
                first = await asyncio.to_thread(
                    deliver_inbox_entry,
                    InboxDeliveryLog(store=store, entry=inbox),
                    sessions=HostedSessionRuntime(catalog=catalog, host=host),
                    paster=paster,
                )
                self.assertEqual(first.adapterDeliveryState, "unknown")
                self.assertEqual(first.adapterRequestId, "durable-request")

                recovered = await asyncio.to_thread(
                    deliver_inbox_entry,
                    InboxDeliveryLog(store=store, entry=first),
                    sessions=HostedSessionRuntime(catalog=catalog, host=host),
                    paster=paster,
                )

                self.assertEqual(recovered.deliveryState, "delivered")
                self.assertEqual(recovered.adapterDeliveryState, "accepted")
                self.assertEqual(
                    recovered.adapterVendorCorrelationId,
                    "vendor-durable-request",
                )
                self.assertEqual(len(adapter.submissions), 1)
                self.assertEqual(adapter.reconciliation_requests, [])
                paster.paste.assert_not_called()
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_public_duplicate_returns_retained_result_with_one_adapter_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            root = Path(tmp_str)
            identity = _identity("api-idempotent")
            adapter = _BlockingSubmitAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            bridge_epoch = bridge.submissions().bridge_epoch
            endpoint = LocalControlEndpoint.for_session(root / "control", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            catalog = TerminalCatalog(root / "terminal-sessions.json")
            entry = TerminalCatalogEntry(
                id=identity.ar_session_id,
                label="Worker",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=root,
                tmux_name=identity.tmux_name,
                command=("claude",),
                created_at=identity.created_at,
                last_attached_at=identity.created_at,
                status="running",
                control_state="ready",
                control_endpoint=endpoint.path,
                control_protocol=CONTROL_PROTOCOL_VERSION,
            )
            catalog.upsert(entry)
            app = FastAPI()
            register_harness_control_routes(
                app,
                ConversationRuntime(
                    scope=ConversationScope(workspace_root=root, coordination_root=root),
                    harness_registry=lambda: (),
                    catalog=catalog,
                    control_plane=ControlPlaneClient(),
                    host=TerminalHost(TerminalHostSeams(tmux_probe=lambda _name: True)),
                    liveness_clock=lambda: datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
                    liveness_config=TerminalCatalogLivenessConfig(),
                    capability_catalog=HarnessCapabilityCatalog(root),
                    authorization=LocalOperatorAuthorizationResolver.for_workspace(root),
                ),
            )
            try:
                with (
                    mock.patch(
                        "agents_remember.serving.harness_control_api.observe_terminal_liveness",
                        return_value=TerminalLivenessObservation(entry, True),
                    ),
                    TestClient(app) as client,
                ):
                    first_call = asyncio.create_task(
                        asyncio.to_thread(
                            client.post,
                            f"/api/terminal/{identity.ar_session_id}/submit",
                            json={
                                "requestId": "same-id",
                                "text": "first payload",
                                "expectedBridgeEpoch": bridge_epoch,
                            },
                        )
                    )
                    await asyncio.wait_for(adapter.submit_started.wait(), timeout=5.0)
                    duplicate_call = asyncio.create_task(
                        asyncio.to_thread(
                            client.post,
                            f"/api/terminal/{identity.ar_session_id}/submit",
                            json={
                                "requestId": "same-id",
                                "text": "first payload",
                                "expectedBridgeEpoch": bridge_epoch,
                            },
                        )
                    )
                    duplicate = await asyncio.wait_for(duplicate_call, timeout=5.0)
                    adapter.release_submit.set()
                    first = await first_call
                    reconciled = await asyncio.to_thread(
                        client.post,
                        f"/api/terminal/{identity.ar_session_id}/reconcile",
                        json={
                            "requestId": "same-id",
                            "expectedBridgeEpoch": bridge_epoch,
                        },
                    )

                self.assertEqual((first.status_code, duplicate.status_code), (200, 200))
                self.assertEqual(first.json()["acceptance"], "immediate")
                self.assertEqual(duplicate.json()["acceptance"], "unknown")
                self.assertEqual(reconciled.status_code, 200)
                self.assertEqual(reconciled.json()["state"], "accepted")
                self.assertEqual(reconciled.json()["vendorCorrelationId"], "vendor-same-id")
                self.assertEqual(len(adapter.submissions), 1)
                self.assertEqual(adapter.submissions[0].text, "first payload")
                self.assertEqual(adapter.reconciliation_requests, [])
            finally:
                adapter.release_submit.set()
                await server.close()
                await bridge.stop("forced")

    async def test_session_direct_interaction_response_round_trips_without_a_lifecycle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            root = Path(tmp_str)
            identity = _identity("api-interaction")
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            bridge_epoch = bridge.submissions().bridge_epoch
            endpoint = LocalControlEndpoint.for_session(root / "control", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            catalog = TerminalCatalog(root / "terminal-sessions.json")
            entry = TerminalCatalogEntry(
                id=identity.ar_session_id,
                label="Worker",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=root,
                tmux_name=identity.tmux_name,
                command=("claude",),
                created_at=identity.created_at,
                last_attached_at=identity.created_at,
                status="running",
                control_state="ready",
                control_endpoint=endpoint.path,
                control_protocol=CONTROL_PROTOCOL_VERSION,
            )
            catalog.upsert(entry)
            app = FastAPI()
            register_harness_control_routes(
                app,
                ConversationRuntime(
                    scope=ConversationScope(workspace_root=root, coordination_root=root),
                    harness_registry=lambda: (),
                    catalog=catalog,
                    control_plane=ControlPlaneClient(),
                    host=TerminalHost(TerminalHostSeams(tmux_probe=lambda _name: True)),
                    liveness_clock=lambda: datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
                    liveness_config=TerminalCatalogLivenessConfig(),
                    capability_catalog=HarnessCapabilityCatalog(root),
                    authorization=LocalOperatorAuthorizationResolver.for_workspace(root),
                ),
            )
            try:
                with (
                    mock.patch(
                        "agents_remember.serving.harness_control_api.observe_terminal_liveness",
                        return_value=TerminalLivenessObservation(entry, True),
                    ),
                    TestClient(app) as client,
                ):
                    await bridge.submissions().submit(
                        bridge.prompt("ask", source="terminal", request_id="question-operation")
                    )
                    pending = PendingInteraction(
                        interaction_id="question-1",
                        kind="user-input",
                        prompt="Mode: Which mode should be used?",
                        created_at="2026-07-16T08:00:01+00:00",
                        choices=("Safe", "Fast"),
                        questions=(
                            InteractionQuestion(
                                text="Which mode should be used?",
                                header="Mode",
                                options=(
                                    InteractionQuestionOption(
                                        label="Safe", description="Use safe mode"
                                    ),
                                    InteractionQuestionOption(label="Fast"),
                                ),
                            ),
                        ),
                    )
                    adapter.emit(
                        AdapterEvent(
                            1,
                            "state",
                            identity,
                            pending.created_at,
                            snapshot=replace(
                                bridge.snapshot(),
                                activity="blocked",
                                acceptance="rejected",
                                pending_interaction=pending,
                            ),
                        )
                    )
                    await _settle_events()

                    # The structured pages survive the IPC snapshot read toward the surfaces.
                    snapshot = await asyncio.to_thread(read_control_snapshot, entry)
                    assert snapshot.pending_interaction is not None
                    self.assertEqual(snapshot.pending_interaction.questions, pending.questions)

                    answers = {"Which mode should be used?": "Safe"}
                    answered = await asyncio.to_thread(
                        client.post,
                        f"/api/terminal/{identity.ar_session_id}/interaction-response",
                        json={
                            "interactionId": "question-1",
                            "expectedBridgeEpoch": bridge_epoch,
                            "answers": answers,
                        },
                    )
                    self.assertEqual(answered.status_code, 200)
                    self.assertEqual(answered.json()["status"], "accepted")
                    self.assertIsNone(answered.json()["pendingInteraction"])
                    self.assertEqual(adapter.responses[-1].interaction_id, "question-1")
                    self.assertEqual(adapter.responses[-1].response, json.dumps(answers))

                    stale = await asyncio.to_thread(
                        client.post,
                        f"/api/terminal/{identity.ar_session_id}/interaction-response",
                        json={
                            "interactionId": "question-1",
                            "expectedBridgeEpoch": bridge_epoch,
                            "answers": answers,
                        },
                    )
                    self.assertEqual(stale.status_code, 409)
                    self.assertEqual(stale.json()["status"], "not-pending")

                    permission = PendingInteraction(
                        interaction_id="permission-1",
                        kind="permission",
                        prompt="Allow git status?",
                        created_at="2026-07-16T08:00:02+00:00",
                        choices=("allow", "deny"),
                    )
                    adapter.emit(
                        AdapterEvent(
                            2,
                            "state",
                            identity,
                            permission.created_at,
                            snapshot=replace(
                                bridge.snapshot(),
                                activity="blocked",
                                pending_interaction=permission,
                            ),
                        )
                    )
                    await _settle_events()
                    wrong_epoch = await asyncio.to_thread(
                        client.post,
                        f"/api/terminal/{identity.ar_session_id}/interaction-response",
                        json={
                            "interactionId": "permission-1",
                            "expectedBridgeEpoch": "stale-epoch",
                            "response": "allow",
                        },
                    )
                    self.assertEqual(wrong_epoch.status_code, 409)
                    self.assertEqual(wrong_epoch.json()["status"], "bridge-epoch-mismatch")

                    allowed = await asyncio.to_thread(
                        client.post,
                        f"/api/terminal/{identity.ar_session_id}/interaction-response",
                        json={
                            "interactionId": "permission-1",
                            "expectedBridgeEpoch": bridge_epoch,
                            "response": "allow",
                        },
                    )
                    self.assertEqual(allowed.status_code, 200)
                    self.assertEqual(allowed.json()["status"], "accepted")
                    self.assertEqual(adapter.responses[-1].response, "allow")
            finally:
                await server.close()
                await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_ipc.py:566).
    async def test_operator_resolution_crosses_the_socket_for_both_wire_actions(
        self,
    ) -> None:  # pragma: no cover
        """`resolve` and `resolve-operation` are the operator's half of the ambiguity barrier.

        An ambiguous send parks the timeline: the authority cannot say whether the prompt
        landed, so nothing else is admitted until a human answers. Both actions are declared
        in `_CONTROL_ACTIONS`, and until now neither had crossed the socket in a test -- so
        the two things asserted here are that the payload reaches the authority intact and
        that the barrier is actually lifted, which is the whole point of the round trip.
        """
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity("operator-resolution")
            adapter = _FakeAdapter()
            adapter.disconnects.extend((True, True))
            bridge = HarnessControlBridge(
                identity, adapter, limits=BridgeLimits(queue=4, submission=4)
            )
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(
                identity.ar_session_id,
                identity.tmux_name,
                identity.created_at,
                endpoint.path,
            )
            try:
                ambiguous = await bridge.submissions().submit(
                    bridge.prompt("first", source="durable", request_id="unknown-1")
                )
                queued = await bridge.submissions().submit(
                    bridge.prompt("second", source="durable", request_id="unknown-2")
                )
                self.assertEqual((ambiguous.acceptance, queued.acceptance), ("unknown", "queued"))
                self.assertIsNotNone(bridge.submissions().active_operation)

                resolved = await asyncio.to_thread(
                    request_control,
                    entry,
                    "resolve",
                    {
                        "requestId": "unknown-1",
                        "state": "rejected",
                        "detail": "operator saw no turn in the pane",
                    },
                )
                assert isinstance(resolved, dict)
                self.assertEqual(resolved["state"], "rejected")
                self.assertEqual(resolved["requestId"], "unknown-1")
                self.assertEqual(resolved["detail"], "operator saw no turn in the pane")
                # The barrier is gone: the parked timeline is free again.
                self.assertIsNone(bridge.submissions().active_operation)

                # A refusal the handler itself owns, so a malformed state never reaches
                # the authority as an unknown reconciliation word.
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        request_control,
                        entry,
                        "resolve",
                        {"requestId": "unknown-1", "state": "maybe", "detail": "d"},
                    )

                # The prompt queued behind the barrier now dispatches, and disconnects the
                # same way -- so there is a second ambiguous head for the other action.
                assert adapter.current is not None
                adapter.emit(
                    AdapterEvent(
                        sequence=1,
                        kind="state",
                        identity=identity,
                        created_at="runner-ready-again",
                        snapshot=adapter.current,
                    )
                )
                for _ in range(200):
                    if bridge.submissions().active_operation is not None:
                        break
                    await _settle_events()
                active = bridge.submissions().active_operation
                assert active is not None
                self.assertEqual(active.operation_id, "unknown-2")

                acknowledged = await asyncio.to_thread(
                    request_control,
                    entry,
                    "resolve-operation",
                    {
                        "operationId": "unknown-2",
                        "operationKind": "prompt",
                        "resolution": "applied",
                        "detail": "operator saw the turn start",
                    },
                )
                self.assertEqual(acknowledged, {"resolved": True})
                self.assertIsNone(bridge.submissions().active_operation)
                status = await bridge.submissions().ledger.status(
                    bridge.submissions().bridge_epoch, ("unknown-2",), cockpit_only=False
                )
                settled = status.submissions[0].submission
                assert settled is not None
                self.assertEqual(settled.detail, "operator saw the turn start")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_peer_timeout_after_submit_preserves_reconciliation_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _BlockingSubmitAdapter()
            adapter.disconnects.append(True)
            adapter.reconciliations["ipc-timeout"] = ReconciliationResult(
                request_id="ipc-timeout",
                state="accepted",
                reconciled_at="2026-07-14T17:36:00+02:00",
                vendor_correlation_id="vendor-ipc-timeout",
            )
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = _ObservedHarnessControlServer(endpoint, bridge)
            await server.start()
            loop = asyncio.get_running_loop()
            prior_exception_handler = loop.get_exception_handler()
            callback_exceptions: list[dict[str, object]] = []
            loop.set_exception_handler(lambda _loop, context: callback_exceptions.append(context))
            try:
                _, writer = await asyncio.open_unix_connection(endpoint.path)
                request = {
                    "protocol": CONTROL_PROTOCOL_VERSION,
                    "identity": identity.to_json(),
                    "action": "submit",
                    "payload": {
                        "requestId": "ipc-timeout",
                        "source": "durable",
                        "text": "hello",
                        "submittedAt": "2026-07-14T17:36:00+02:00",
                    },
                }
                writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
                await writer.drain()
                await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
                writer.transport.abort()
                adapter.release_submit.set()

                await asyncio.wait_for(server.connection_finished.wait(), timeout=1.0)
                await asyncio.sleep(0)
                self.assertEqual(callback_exceptions, [])
                reconciled = await asyncio.wait_for(
                    bridge.submissions().reconcile("ipc-timeout"), timeout=1.0
                )
                self.assertEqual(reconciled.state, "accepted")
                self.assertEqual(reconciled.vendor_correlation_id, "vendor-ipc-timeout")
                self.assertEqual(
                    [submission.request_id for submission in adapter.submissions],
                    ["ipc-timeout"],
                )
            finally:
                loop.set_exception_handler(prior_exception_handler)
                adapter.release_submit.set()
                await server.close()
                await bridge.stop("forced")

    async def test_private_endpoint_exact_identity_and_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str) / "control", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            try:
                self.assertEqual(stat.S_IMODE(endpoint.path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(endpoint.path.stat().st_mode), 0o600)

                client = HarnessControlClient(endpoint)
                handshake = await client.request("handshake")
                assert isinstance(handshake, dict)
                self.assertEqual(handshake["protocol"], CONTROL_PROTOCOL_VERSION)
                result = await client.request(
                    "submit",
                    {
                        "requestId": "ipc-request-1",
                        "source": "durable",
                        "text": "hello",
                        "submittedAt": "2026-07-13T18:00:00+00:00",
                    },
                )
                assert isinstance(result, dict)
                self.assertEqual(result["acceptance"], "immediate")

                wrong = HarnessControlClient(
                    LocalControlEndpoint(path=endpoint.path, identity=_identity("wrong"))
                )
                with self.assertRaisesRegex(HarnessControlError, "identity"):
                    await wrong.request("snapshot")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_malformed_ipc_request_is_rejected_without_control_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            try:
                reader, writer = await asyncio.open_unix_connection(endpoint.path)
                writer.write(b"not-json\n")
                await writer.drain()
                response = json.loads(await reader.readline())
                writer.close()
                await writer.wait_closed()
                self.assertFalse(response["ok"])
                self.assertEqual(adapter.submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")
