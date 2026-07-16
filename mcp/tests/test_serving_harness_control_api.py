"""HTTP contract tests for live harness capability, set, submit, and reconcile routes."""

from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.serving import harness_control_api
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_capability_catalog import CapabilityCatalogResult
from agents_remember.serving.harness_control_api import register_harness_control_routes
from agents_remember.serving.harness_control_models import (
    ReconciliationResult,
    SubmissionReceipt,
)
from agents_remember.serving.harnesses import HARNESSES
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalLivenessObservation,
)

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


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
        app = FastAPI()
        register_harness_control_routes(
            app,
            workspace_root=self.tmp,
            harness_registry=lambda: HARNESSES,
            catalog=self.catalog,
            host=mock.Mock(),
            liveness_clock=lambda: NOW,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=self.capabilities,  # type: ignore[arg-type]
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
                json={"requestId": "request-7", "text": "one complete\nmessage"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["requestId"], "request-7")
        self.assertEqual(response.json()["vendorCorrelationId"], "vendor-7")
        self.assertNotIn("raw", response.json())
        self.assertNotIn("argv", str(response.json()))
        self.assertNotIn("VENDOR_AUTH_TOKEN", str(response.json()))
        self.assertEqual(submit.call_args.args[0].id, self.live.id)
        self.assertEqual(submit.call_args.args[1], "one complete\nmessage")
        self.assertEqual(
            submit.call_args.kwargs,
            {"source": "terminal", "request_id": "request-7"},
        )

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
                "/api/terminal/live/reconcile", json={"requestId": "request-7"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "accepted")
        self.assertEqual(response.json()["vendorCorrelationId"], "vendor-7")
        self.assertNotIn("raw", response.json())
        self.assertNotIn("vendorThread", str(response.json()))
        self.assertNotIn("private", str(response.json()))

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


if __name__ == "__main__":
    unittest.main()
