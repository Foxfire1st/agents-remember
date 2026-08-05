"""HTTP contract tests for live harness capability, set, submit, and reconcile routes."""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlClientError,
    HarnessControlError,
    HarnessInteractionNotPendingError,
    HarnessRequestConflictError,
)
from agents_remember.kernel.harnesses import HARNESSES
from agents_remember.serving import harness_control_api
from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    ConversationScope,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_capability_catalog import CapabilityCatalogResult
from agents_remember.serving.harness_control_api import register_harness_control_routes
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    ReconciliationResult,
    SubmissionAuthorityDescriptor,
    SubmissionLookup,
    SubmissionReceipt,
    SubmissionStatus,
    SubmissionStatusBatch,
    WithdrawalResult,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalLivenessObservation,
)

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
BRIDGE_EPOCH = "bridge-epoch-1"


class _CapabilityCatalog:
    def __init__(self) -> None:
        self.refreshes: list[bool] = []
        self.error: HarnessControlError | None = None

    async def get(self, harness: str, *, registry, refresh: bool = False):
        del registry
        self.refreshes.append(refresh)
        if self.error is not None:
            raise self.error
        return CapabilityCatalogResult(
            harness_id=harness,
            cache_status="refreshed" if refresh else "miss",
            install_fingerprint="abc123",
            snapshot=CapabilitySnapshot((), None, None),
        )


class HarnessControlApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.capabilities = _CapabilityCatalog()
        self.live = TerminalCatalogEntry(
            id="live",
            label="Claude",
            kind="harness",
            harness="claude",
            lifecycle_id=None,
            cwd=self.tmp,
            tmux_name="ar-live",
            command=("claude",),
            created_at="2026-07-16T08:00:00+00:00",
            last_attached_at="2026-07-16T08:00:00+00:00",
            status="running",
            control_endpoint=self.tmp / "control.sock",
        )
        self.catalog.upsert(self.live)
        self.moment = NOW
        app = FastAPI()
        register_harness_control_routes(
            app,
            ConversationRuntime(
                scope=ConversationScope(workspace_root=self.tmp, coordination_root=self.tmp),
                harness_registry=lambda: HARNESSES,
                catalog=self.catalog,
                host=mock.Mock(),
                liveness_clock=lambda: self.moment,
                liveness_config=TerminalCatalogLivenessConfig(),
                capability_catalog=self.capabilities,  # type: ignore[arg-type]
                authorization=LocalOperatorAuthorizationResolver.for_workspace(self.tmp),
            ),
        )
        self.client = TestClient(app)
        self.alive = mock.patch(
            "agents_remember.serving.harness_control_api.observe_terminal_liveness",
            side_effect=lambda _catalog, _host, entry, **_kwargs: TerminalLivenessObservation(
                entry, True
            ),
        )
        self.observe = self.alive.start()

    def tearDown(self) -> None:
        self.alive.stop()
        self.client.close()
        self._dir.cleanup()

    def test_pre_session_capabilities_freeze_envelope_and_refresh(self) -> None:
        response = self.client.get("/api/harnesses/claude/capabilities?refresh=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "schema": "ar-harness-capabilities/v1",
                "harness": "claude",
                "cacheStatus": "refreshed",
                "installFingerprint": "abc123",
                "capabilities": {
                    "models": [],
                    "selectedModelKey": None,
                    "selectedEffort": None,
                    "configOptions": [],
                },
            },
        )
        self.assertEqual(self.capabilities.refreshes, [True])

    def test_route_module_has_no_terminal_paste_dependency(self) -> None:
        source = inspect.getsource(harness_control_api)
        self.assertNotIn("TerminalPaster", source)
        self.assertNotIn("terminal_paster", source)

    def test_failed_refresh_is_503_not_a_stale_envelope(self) -> None:
        self.capabilities.error = HarnessControlError("auth discovery failed")
        response = self.client.get("/api/harnesses/claude/capabilities?refresh=true")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "control-unavailable")
        self.assertNotIn("capabilities", response.json())

    def test_live_capabilities_are_vendor_neutral(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_api.read_control_capabilities",
            return_value=CapabilitySnapshot((), None, None),
        ):
            response = self.client.get("/api/terminal/live/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["configOptions"], [])
        self.assertNotIn("vendor", response.json())

    def test_set_routes_pass_through_honest_results(self) -> None:
        with (
            mock.patch(
                "agents_remember.serving.harness_control_api.set_control_model",
                return_value=SetResult(True, "queued", "model-b"),
            ) as set_model,
            mock.patch(
                "agents_remember.serving.harness_control_api.set_control_effort",
                return_value=SetResult(False, "unsupported", "max"),
            ) as set_effort,
        ):
            model = self.client.post("/api/terminal/live/set-model", json={"model": "model-b"})
            effort = self.client.post("/api/terminal/live/set-effort", json={"effort": "max"})
        self.assertEqual(model.json()["acceptance"], "queued")
        self.assertEqual(effort.json()["acceptance"], "unsupported")
        self.assertEqual((model.status_code, effort.status_code), (200, 200))
        self.assertEqual(set_model.call_args.args[0].id, self.live.id)
        self.assertEqual(set_model.call_args.args[1], "model-b")
        self.assertEqual(set_effort.call_args.args[0].id, self.live.id)
        self.assertEqual(set_effort.call_args.args[1], "max")

    def test_submit_preserves_whole_message_request_and_vendor_correlation(self) -> None:
        receipt = SubmissionReceipt(
            request_id="request-7",
            acceptance="immediate",
            submitted_at="2026-07-16T08:00:00+00:00",
            vendor_correlation_id="vendor-7",
            accepted_at="2026-07-16T08:00:01+00:00",
            raw={
                "argv": ["vendor", "--secret"],
                "env": {"VENDOR_AUTH_TOKEN": "sensitive"},
            },
        )
        with mock.patch(
            "agents_remember.serving.harness_control_api.submit_control_prompt",
            return_value=receipt,
        ) as submit:
            response = self.client.post(
                "/api/terminal/live/submit",
                json={
                    "requestId": "request-7",
                    "text": "one complete\nmessage",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["requestId"], "request-7")
        self.assertEqual(response.json()["vendorCorrelationId"], "vendor-7")
        self.assertNotIn("raw", response.json())
        self.assertNotIn("argv", str(response.json()))
        self.assertNotIn("VENDOR_AUTH_TOKEN", str(response.json()))
        self.assertEqual(submit.call_args.args[0].id, self.live.id)
        self.assertEqual(submit.call_args.args[1], "one complete\nmessage")
        submission = submit.call_args.args[2]
        self.assertEqual(submission.source, "cockpit")
        self.assertEqual(submission.request_id, "request-7")
        self.assertEqual(submission.expected_bridge_epoch, BRIDGE_EPOCH)

    def test_authority_status_and_withdraw_routes_are_epoch_bound_and_raw_free(self) -> None:
        status = SubmissionStatusBatch(
            bridge_epoch=BRIDGE_EPOCH,
            submissions=(
                SubmissionLookup(
                    request_id="request-queued",
                    outcome="found",
                    submission=SubmissionStatus(
                        request_id="request-queued",
                        state="queued",
                        submitted_at="2026-07-16T08:00:00+00:00",
                        updated_at="2026-07-16T08:00:01+00:00",
                        accepted_at=None,
                        withdrawable=True,
                        detail="queued in authority",
                    ),
                ),
                SubmissionLookup(request_id="missing", outcome="not-found"),
            ),
        )
        withdrawn = WithdrawalResult(
            request_id="request-queued",
            outcome="withdrawn",
            state="withdrawn",
            withdrawn_at="2026-07-16T08:00:02+00:00",
            detail="withdrawn before dispatch",
        )
        with (
            mock.patch(
                "agents_remember.serving.harness_control_api.read_submission_authority",
                return_value=SubmissionAuthorityDescriptor(bridge_epoch=BRIDGE_EPOCH),
            ) as descriptor_call,
            mock.patch(
                "agents_remember.serving.harness_control_api.read_submission_status",
                return_value=status,
            ) as status_call,
            mock.patch(
                "agents_remember.serving.harness_control_api.withdraw_control_submission",
                return_value=withdrawn,
            ) as withdraw_call,
        ):
            descriptor = self.client.get("/api/terminal/live/submission-authority")
            status_response = self.client.post(
                "/api/terminal/live/submission-status",
                json={
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                    "requestIds": ["request-queued", "missing"],
                },
            )
            withdraw_response = self.client.post(
                "/api/terminal/live/withdraw",
                json={
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                    "requestId": "request-queued",
                },
            )

        self.assertEqual(descriptor.json(), {"bridgeEpoch": BRIDGE_EPOCH})
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["submissions"][0]["submission"]["state"], "queued")
        self.assertEqual(status_response.json()["submissions"][1]["outcome"], "not-found")
        self.assertEqual(withdraw_response.json()["outcome"], "withdrawn")

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value))
            return set()

        public_keys = keys([descriptor.json(), status_response.json(), withdraw_response.json()])
        self.assertNotIn("text", public_keys)
        self.assertNotIn("raw", public_keys)
        self.assertEqual(descriptor_call.call_args.args[0].id, self.live.id)
        self.assertEqual(
            status_call.call_args.kwargs,
            {
                "expected_bridge_epoch": BRIDGE_EPOCH,
                "request_ids": ("request-queued", "missing"),
            },
        )
        self.assertEqual(
            withdraw_call.call_args.kwargs,
            {"expected_bridge_epoch": BRIDGE_EPOCH, "request_id": "request-queued"},
        )

    def test_epoch_mismatch_is_409_for_every_epoch_bound_route(self) -> None:
        cases = (
            (
                "submit_control_prompt",
                "/api/terminal/live/submit",
                {"requestId": "r", "text": "message", "expectedBridgeEpoch": "old"},
            ),
            (
                "read_submission_status",
                "/api/terminal/live/submission-status",
                {"requestIds": ["r"], "expectedBridgeEpoch": "old"},
            ),
            (
                "withdraw_control_submission",
                "/api/terminal/live/withdraw",
                {"requestId": "r", "expectedBridgeEpoch": "old"},
            ),
            (
                "reconcile_control_prompt",
                "/api/terminal/live/reconcile",
                {"requestId": "r", "expectedBridgeEpoch": "old"},
            ),
        )
        for function, path, body in cases:
            with (
                self.subTest(path=path),
                mock.patch(
                    f"agents_remember.serving.harness_control_api.{function}",
                    side_effect=HarnessBridgeEpochMismatchError("old", BRIDGE_EPOCH),
                ),
            ):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json()["status"],
                    "bridge-epoch-mismatch",
                )
                self.assertEqual(response.json()["actualBridgeEpoch"], BRIDGE_EPOCH)

    def test_submit_request_id_conflict_is_409(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_api.submit_control_prompt",
            side_effect=HarnessRequestConflictError("request id belongs to another payload"),
        ):
            response = self.client.post(
                "/api/terminal/live/submit",
                json={
                    "requestId": "conflict",
                    "text": "different",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "request-id-conflict")

    def test_submission_status_rejects_invalid_batches_before_control_ipc(self) -> None:
        bodies = (
            {"expectedBridgeEpoch": BRIDGE_EPOCH, "requestIds": []},
            {"expectedBridgeEpoch": BRIDGE_EPOCH, "requestIds": ["same", "same"]},
            {
                "expectedBridgeEpoch": BRIDGE_EPOCH,
                "requestIds": [f"request-{index}" for index in range(65)],
            },
        )
        with mock.patch(
            "agents_remember.serving.harness_control_api.read_submission_status"
        ) as status_call:
            for body in bodies:
                with self.subTest(size=len(body["requestIds"])):
                    response = self.client.post("/api/terminal/live/submission-status", json=body)
                    self.assertEqual(response.status_code, 422)
        status_call.assert_not_called()

    def test_submit_exposes_retry_safety_only_for_certified_zero_control_socket_bytes(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_api.submit_control_prompt",
            side_effect=HarnessControlClientError(
                "control socket refused before write", may_have_sent=False
            ),
        ):
            response = self.client.post(
                "/api/terminal/live/submit",
                json={
                    "requestId": "request-prewrite",
                    "text": "one message",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "pre-dispatch-failed",
                "detail": "control socket refused before write",
                "retrySafe": True,
                "stage": "control-ipc",
            },
        )

    def test_submit_generic_503_has_no_retry_safe_claim(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_api.submit_control_prompt",
            side_effect=HarnessControlError("generic control outage"),
        ):
            response = self.client.post(
                "/api/terminal/live/submit",
                json={
                    "requestId": "request-outage",
                    "text": "one message",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "control-unavailable", "detail": "generic control outage"},
        )
        self.assertNotIn("retrySafe", response.json())

    def test_submit_client_error_that_may_have_sent_has_no_retry_safe_claim(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_api.submit_control_prompt",
            side_effect=HarnessControlClientError(
                "control response lost after write", may_have_sent=True
            ),
        ):
            response = self.client.post(
                "/api/terminal/live/submit",
                json={
                    "requestId": "request-may-have-sent",
                    "text": "one message",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "control-unavailable",
                "detail": "control response lost after write",
            },
        )
        self.assertNotIn("retrySafe", response.json())

    def test_post_write_unknown_stays_a_receipt_without_retry_safe_claim(self) -> None:
        receipt = SubmissionReceipt(
            request_id="request-postwrite",
            acceptance="unknown",
            submitted_at="2026-07-16T08:00:00+00:00",
            detail="control response lost after request bytes were sent",
        )
        with mock.patch(
            "agents_remember.serving.harness_control_api.submit_control_prompt",
            return_value=receipt,
        ):
            response = self.client.post(
                "/api/terminal/live/submit",
                json={
                    "requestId": "request-postwrite",
                    "text": "one message",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["acceptance"], "unknown")
        self.assertNotIn("retrySafe", response.json())

    def test_reconcile_keeps_the_same_request_correlation(self) -> None:
        result = ReconciliationResult(
            request_id="request-7",
            state="accepted",
            reconciled_at="2026-07-16T08:00:02+00:00",
            vendor_correlation_id="vendor-7",
            raw={"vendorThread": "thread-secret", "auth": {"account": "private"}},
        )
        with mock.patch(
            "agents_remember.serving.harness_control_api.reconcile_control_prompt",
            return_value=result,
        ):
            response = self.client.post(
                "/api/terminal/live/reconcile",
                json={
                    "requestId": "request-7",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "accepted")
        self.assertEqual(response.json()["vendorCorrelationId"], "vendor-7")
        self.assertNotIn("raw", response.json())
        self.assertNotIn("vendorThread", str(response.json()))
        self.assertNotIn("private", str(response.json()))

    def _interaction_snapshot(self) -> AdapterSnapshot:
        return AdapterSnapshot(
            identity=ControlIdentity(
                ar_session_id=self.live.id,
                tmux_name=self.live.tmux_name,
                created_at=self.live.created_at,
            ),
            control="ready",
            activity="settling",
            acceptance="queued",
        )

    def test_interaction_response_sends_the_structured_answers_map_without_a_lifecycle(
        self,
    ) -> None:
        # The catalog row has lifecycle_id=None: this route is the lifecycle-free answer channel.
        self.assertIsNone(self.live.lifecycle_id)
        answers = {"Which mode should be used?": "Safe"}
        with (
            mock.patch(
                "agents_remember.serving.harness_control_api.read_submission_authority",
                return_value=SubmissionAuthorityDescriptor(bridge_epoch=BRIDGE_EPOCH),
            ),
            mock.patch(
                "agents_remember.serving.harness_control_api.respond_control_interaction",
                return_value=self._interaction_snapshot(),
            ) as respond,
        ):
            response = self.client.post(
                "/api/terminal/live/interaction-response",
                json={
                    "interactionId": "question-1",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                    "answers": answers,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(response.json()["activity"], "settling")
        self.assertIsNone(response.json()["pendingInteraction"])
        self.assertEqual(respond.call_args.args[0].id, self.live.id)
        self.assertEqual(
            respond.call_args.kwargs,
            {"interaction_id": "question-1", "response": json.dumps(answers)},
        )

    def test_interaction_response_permission_kind_still_passes_the_plain_response(self) -> None:
        with (
            mock.patch(
                "agents_remember.serving.harness_control_api.read_submission_authority",
                return_value=SubmissionAuthorityDescriptor(bridge_epoch=BRIDGE_EPOCH),
            ),
            mock.patch(
                "agents_remember.serving.harness_control_api.respond_control_interaction",
                return_value=self._interaction_snapshot(),
            ) as respond,
        ):
            response = self.client.post(
                "/api/terminal/live/interaction-response",
                json={
                    "interactionId": "permission-1",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                    "response": "allow",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(
            respond.call_args.kwargs,
            {"interaction_id": "permission-1", "response": "allow"},
        )

    def test_interaction_response_wrong_interaction_id_is_an_honest_not_pending(self) -> None:
        with (
            mock.patch(
                "agents_remember.serving.harness_control_api.read_submission_authority",
                return_value=SubmissionAuthorityDescriptor(bridge_epoch=BRIDGE_EPOCH),
            ),
            mock.patch(
                "agents_remember.serving.harness_control_api.respond_control_interaction",
                side_effect=HarnessInteractionNotPendingError(
                    "interaction response does not match the pending interaction"
                ),
            ),
        ):
            response = self.client.post(
                "/api/terminal/live/interaction-response",
                json={
                    "interactionId": "question-2",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                    "answers": {"Which mode should be used?": "Safe"},
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "not-pending")
        self.assertIn("does not match the pending interaction", response.json()["detail"])

    def test_interaction_response_is_epoch_guarded_like_the_other_control_routes(self) -> None:
        with (
            mock.patch(
                "agents_remember.serving.harness_control_api.read_submission_authority",
                return_value=SubmissionAuthorityDescriptor(bridge_epoch=BRIDGE_EPOCH),
            ),
            mock.patch(
                "agents_remember.serving.harness_control_api.respond_control_interaction"
            ) as respond,
        ):
            response = self.client.post(
                "/api/terminal/live/interaction-response",
                json={
                    "interactionId": "question-1",
                    "expectedBridgeEpoch": "stale-epoch",
                    "answers": {"Which mode should be used?": "Safe"},
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "bridge-epoch-mismatch")
        self.assertEqual(response.json()["expectedBridgeEpoch"], "stale-epoch")
        self.assertEqual(response.json()["actualBridgeEpoch"], BRIDGE_EPOCH)
        respond.assert_not_called()

    def test_interaction_response_requires_exactly_one_payload_shape(self) -> None:
        bodies = (
            {"interactionId": "q", "expectedBridgeEpoch": BRIDGE_EPOCH},
            {
                "interactionId": "q",
                "expectedBridgeEpoch": BRIDGE_EPOCH,
                "response": "allow",
                "answers": {"Q?": "A"},
            },
            {"interactionId": "q", "expectedBridgeEpoch": BRIDGE_EPOCH, "answers": {}},
            {
                "interactionId": "q",
                "expectedBridgeEpoch": BRIDGE_EPOCH,
                "answers": {"Q?": " "},
            },
        )
        with mock.patch(
            "agents_remember.serving.harness_control_api.respond_control_interaction"
        ) as respond:
            for body in bodies:
                with self.subTest(body=body):
                    response = self.client.post(
                        "/api/terminal/live/interaction-response", json=body
                    )
                    self.assertEqual(response.status_code, 422)
        respond.assert_not_called()

    def test_unknown_and_non_control_sessions_are_distinct(self) -> None:
        unknown = self.client.post("/api/terminal/ghost/set-model", json={"model": "model-b"})
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="plain",
                label="Terminal",
                kind="terminal",
                harness=None,
                lifecycle_id=None,
                cwd=self.tmp,
                tmux_name="ar-plain",
                command=("bash",),
                created_at="2026-07-16T08:00:00+00:00",
                last_attached_at="2026-07-16T08:00:00+00:00",
                status="running",
            )
        )
        unsupported = self.client.post("/api/terminal/plain/set-model", json={"model": "model-b"})
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unsupported.status_code, 409)

    def test_status_order_is_unknown_or_dead_then_live_unsupported_then_native(self) -> None:
        stopped = replace(self.live, id="stopped", tmux_name="ar-stopped", status="exited")
        plain = replace(
            self.live,
            id="plain-status",
            tmux_name="ar-plain-status",
            kind="terminal",
            harness=None,
            control_endpoint=None,
        )
        legacy = replace(
            self.live,
            id="legacy-status",
            tmux_name="ar-legacy-status",
            control_endpoint=None,
        )
        dead = replace(
            plain,
            id="dead-status",
            tmux_name="ar-dead-status",
        )
        for entry in (stopped, plain, legacy, dead):
            self.catalog.upsert(entry)

        unknown = self.client.get("/api/terminal/missing/capabilities")
        stopped_response = self.client.get("/api/terminal/stopped/capabilities")
        plain_response = self.client.get("/api/terminal/plain-status/capabilities")
        legacy_response = self.client.get("/api/terminal/legacy-status/capabilities")
        observed_before_dead = self.observe.call_count
        self.observe.side_effect = lambda _catalog, _host, entry, **_kwargs: (
            TerminalLivenessObservation(replace(entry, status="exited"), False)
        )
        dead_response = self.client.get("/api/terminal/dead-status/capabilities")

        self.assertEqual((unknown.status_code, stopped_response.status_code), (404, 404))
        self.assertEqual((plain_response.status_code, legacy_response.status_code), (409, 409))
        self.assertEqual(dead_response.status_code, 404)
        self.assertEqual(self.observe.call_count, observed_before_dead + 1)

        self.observe.side_effect = lambda _catalog, _host, entry, **_kwargs: (
            TerminalLivenessObservation(entry, True)
        )
        with mock.patch(
            "agents_remember.serving.harness_control_api.read_control_capabilities",
            return_value=CapabilitySnapshot((), None, None),
        ) as read:
            native = self.client.get("/api/terminal/live/capabilities")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(read.call_args.args[0].id, self.live.id)

    def test_control_routes_reuse_a_fresh_liveness_observation_within_the_memo_ttl(self) -> None:
        # A submit cluster (authority -> submit -> status) re-probed tmux, the
        # pane, and the bridge snapshot before every call; a fresh alive observation is now reused
        # for CONTROL_LIVENESS_MEMO_TTL_SECONDS instead.
        with mock.patch(
            "agents_remember.serving.harness_control_api.read_control_capabilities",
            return_value=CapabilitySnapshot((), None, None),
        ):
            first = self.client.get("/api/terminal/live/capabilities")
            second = self.client.get("/api/terminal/live/capabilities")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(self.observe.call_count, 1)

    def test_memoized_liveness_still_fails_a_dead_bridge_with_a_typed_503(self) -> None:
        # The memo only skips the pre-work observation; the control IPC itself is the stronger
        # liveness probe, so a bridge that dies inside the TTL still surfaces a typed 503.
        with mock.patch(
            "agents_remember.serving.harness_control_api.read_control_capabilities",
            return_value=CapabilitySnapshot((), None, None),
        ):
            alive = self.client.get("/api/terminal/live/capabilities")
        self.assertEqual(alive.status_code, 200)
        with mock.patch(
            "agents_remember.serving.harness_control_api.submit_control_prompt",
            side_effect=HarnessControlError("bridge died after the memoized observation"),
        ):
            response = self.client.post(
                "/api/terminal/live/submit",
                json={
                    "requestId": "request-dead-bridge",
                    "text": "one message",
                    "expectedBridgeEpoch": BRIDGE_EPOCH,
                },
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "control-unavailable")
        self.assertEqual(self.observe.call_count, 1)

    def test_control_routes_re_observe_after_the_memo_ttl_expires(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_api.read_control_capabilities",
            return_value=CapabilitySnapshot((), None, None),
        ):
            first = self.client.get("/api/terminal/live/capabilities")
            self.moment = NOW + timedelta(
                seconds=harness_control_api.CONTROL_LIVENESS_MEMO_TTL_SECONDS + 0.1
            )
            second = self.client.get("/api/terminal/live/capabilities")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(self.observe.call_count, 2)


class ControlLivenessMemoRetentionTests(unittest.TestCase):
    """The control-route liveness memo has to stay bounded.

    A seat that leaves ``running`` 404s inside ``_running_control_entry`` before the memo's ``get``
    runs, so the memo's own expiry branch never sees that seat again. These tests pin the reclaim
    that replaces it, and assert on ``_entries`` directly because retention -- not any response
    body -- is the property under test (same idiom as the contract-cache retention test).
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.moment = NOW
        memos: list[harness_control_api._ControlLivenessMemo] = []
        real_memo_class = harness_control_api._ControlLivenessMemo

        def _capture_memo() -> harness_control_api._ControlLivenessMemo:
            memo = real_memo_class()
            memos.append(memo)
            return memo

        app = FastAPI()
        # The memo is an app-scoped local of the registrar, so capture the instance it builds.
        with mock.patch.object(harness_control_api, "_ControlLivenessMemo", _capture_memo):
            register_harness_control_routes(
                app,
                ConversationRuntime(
                    scope=ConversationScope(workspace_root=self.tmp, coordination_root=self.tmp),
                    harness_registry=lambda: HARNESSES,
                    catalog=self.catalog,
                    host=mock.Mock(),
                    liveness_clock=lambda: self.moment,
                    liveness_config=TerminalCatalogLivenessConfig(),
                    capability_catalog=_CapabilityCatalog(),  # type: ignore[arg-type]
                    authorization=LocalOperatorAuthorizationResolver.for_workspace(self.tmp),
                ),
            )
        (self.memo,) = memos
        self.client = TestClient(app)
        self.alive = mock.patch(
            "agents_remember.serving.harness_control_api.observe_terminal_liveness",
            side_effect=lambda _catalog, _host, entry, **_kwargs: TerminalLivenessObservation(
                entry, True
            ),
        )
        self.alive.start()

    def tearDown(self) -> None:
        self.alive.stop()
        self.client.close()
        self._dir.cleanup()

    def _seat(self, session: str) -> TerminalCatalogEntry:
        return TerminalCatalogEntry(
            id=session,
            label="Claude",
            kind="harness",
            harness="claude",
            lifecycle_id=None,
            cwd=self.tmp,
            tmux_name=f"ar-{session}",
            command=("claude",),
            created_at="2026-07-16T08:00:00+00:00",
            last_attached_at="2026-07-16T08:00:00+00:00",
            status="running",
            control_endpoint=self.tmp / "control.sock",
        )

    def test_memo_reclaims_seats_that_left_running_without_ever_being_read_again(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_api.read_control_capabilities",
            return_value=CapabilitySnapshot((), None, None),
        ):
            for index in range(8):
                seat = self._seat(f"seat-{index}")
                self.catalog.upsert(seat)
                served = self.client.get(f"/api/terminal/{seat.id}/capabilities")
                self.assertEqual(served.status_code, 200)
                self.catalog.upsert(replace(seat, status="terminated"))
            self.assertEqual(len(self.memo._entries), 8)  # every seat memoized while running

            # The terminated seats are now unreachable for the memo: the route 404s on catalog
            # status before ``get`` -- the only pre-fix eviction path -- could ever run again.
            gone = self.client.get("/api/terminal/seat-0/capabilities")
            self.assertEqual(gone.status_code, 404)
            self.assertEqual(len(self.memo._entries), 8)

            self.moment = NOW + timedelta(
                seconds=harness_control_api.CONTROL_LIVENESS_MEMO_TTL_SECONDS + 0.1
            )
            survivor = self._seat("still-running")
            self.catalog.upsert(survivor)
            still_served = self.client.get(f"/api/terminal/{survivor.id}/capabilities")
            self.assertEqual(still_served.status_code, 200)

        # Only the one seat whose observation is still fresh survives; the eight stranded
        # ``TerminalCatalogEntry`` rows are reclaimed instead of held for the daemon's lifetime.
        self.assertEqual(list(self.memo._entries), ["still-running"])

    def test_memo_caps_the_seats_memoized_inside_a_single_ttl_window(self) -> None:
        # Expiry cannot help while every observation is still fresh, so the cap is the backstop.
        memo = harness_control_api._ControlLivenessMemo(max_entries=3)
        entry = self._seat("burst")
        for index in range(5):
            memo.put(f"burst-{index}", at=NOW + timedelta(milliseconds=index), entry=entry)

        newest = NOW + timedelta(milliseconds=4)
        self.assertEqual(sorted(memo._entries), ["burst-2", "burst-3", "burst-4"])
        # A put never evicts its own observation, so the TTL reuse still holds for the newest.
        self.assertIsNotNone(memo.get("burst-4", at=newest))
        self.assertIsNone(memo.get("burst-0", at=newest))


if __name__ == "__main__":
    unittest.main()
