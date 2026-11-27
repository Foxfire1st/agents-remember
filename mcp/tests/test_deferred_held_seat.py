"""BUG-R58@v2 tests: deferred held-seat reservation, opening, readers, parent tolerance, and limits."""

from __future__ import annotations

import json
import unittest
import uuid
from typing import Any
from unittest.mock import patch

from agents_remember.cli import (
    provider_limit_drain,
    role_held_messages,
    role_launch_liveness,
    role_launch_routes,
    role_retirement_receipt,
)
from agents_remember.cli.paseo_role_tools import start_leaf_set
from agents_remember.cli.role_held_store import held_seat_ref, open_held_store
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.cli.role_retire import retire_native_seat
from agents_remember.controlplane.held_store_layout import (
    HeldMessage,
    hold_message,
    recount_waiting,
)
from agents_remember.models.role_agents import RoleStartSetCall, SeatLaunchOverride
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.structural.agent import RetireChildRequest
from agents_remember.observer.host_activity import HOST_ACTIVITY_FILE
from agents_remember.serving.paseo.provider_limits import provider_limits_path
from agents_remember.serving.projections.host_activity import activity_directory
from paseo_role_tools_test_support import RoleToolsTestCase, binding, selection_of
from test_paseo_bridge_agents import AgentCommandScriptTests
from test_paseo_launch import ROLE_REFS


class TestDeferredHeldBridgeParent(unittest.TestCase):
    """L58-R6: bridge omits an archived or missing parent when allowMissingParent is true."""

    def setUp(self) -> None:
        self._bridge = AgentCommandScriptTests("runTest")
        self._bridge.setUp()
        self.addCleanup(self._bridge.doCleanups)

    def test_bridge_omits_archived_parent_when_allow_missing_parent_is_true(self) -> None:
        parent_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "agentId": agent_id,
            "idempotencyKey": f"ar-role-launch:{uuid.uuid4()}",
            "workspaceId": "wks_fake",
            "provider": "codex",
            "model": "gpt-5.6-luna",
            "title": "Reviewer · L58",
            "labels": {"ar.role": "reviewer", "ar.request-id": str(uuid.uuid4())},
            "parentAgentId": parent_id,
            "allowMissingParent": True,
        }
        archived_parent = {
            parent_id: {
                "id": parent_id,
                "provider": "codex",
                "model": "gpt-5.6-luna",
                "title": "Manager",
                "labels": {},
                "workspaceId": "wks_fake",
                "cwd": "/tmp",
                "status": "closed",
                "archivedAt": "2026-11-26T20:00:00.000Z",
                "createdAt": "2026-11-26T19:00:00.000Z",
            }
        }
        reply = self._bridge.call("agent-create", payload, agents=archived_parent)
        self.assertEqual(reply["agent"]["id"], agent_id)
        create_row = next(row for row in self._bridge.recorded() if row.get("via") == "public")
        self.assertNotIn("parent", create_row["options"])


class TestDeferredHeldReservationAndOpening(RoleToolsTestCase):
    """L58-R1/R2/R3/R5/R6/R7/R8: deferred held reservation, opening, readers, and limits."""

    def receipt_for(self, role: str) -> dict[str, Any]:
        selection = RoleSelection.model_validate({"role": role, **ROLE_REFS[role]})
        loaded = _read_receipt(_receipt_path(self.config, selection))
        assert loaded is not None
        return loaded

    def test_rule9_occupancy_and_replacement_on_deferred_held_seat_uses_zero_host_calls(
        self,
    ) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        self.runtime.calls.clear()

        duplicate = self.start(manager, "reviewer", **selection_of("reviewer"))
        self.assertIs(duplicate["ok"], False)
        self.assertIn(reviewer_id, duplicate["detail"])
        self.assertEqual([cmd for cmd, _ in self.runtime.calls if cmd != "catalog"], [])
        receipt = self.receipt_for("reviewer")
        self.assertEqual((receipt["agentId"], receipt["status"]), (reviewer_id, "held"))
        self.assertIs(receipt["hostAgentExists"], False)

        replaced = self.start(
            self.architect,
            "reviewer",
            replaces_agent_id=reviewer_id,
            **selection_of("reviewer"),
        )
        self.assertIs(replaced["ok"], True)
        self.assertNotEqual(replaced["agentId"], reviewer_id)
        self.assertNotIn("agent-archive", [cmd for cmd, _ in self.runtime.calls])

    def test_role_message_by_role_and_task_opens_deferred_held_seat_and_sets_allow_missing_parent(
        self,
    ) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        self.runtime.calls.clear()

        opened = self.message(
            manager,
            text="Review by role and task reference.",
            role="reviewer",
            **selection_of("reviewer"),
        )
        self.assertEqual((opened["ok"], opened["openedHeldSeat"]), (True, True))
        self.assertEqual(opened["recipientAgentId"], reviewer_id)
        self.assertIs(opened["bindingConfirmation"]["retried"], False)
        creates = [payload for cmd, payload in self.runtime.calls if cmd == "agent-create"]
        self.assertEqual(len(creates), 2)
        self.assertIs(creates[0]["allowMissingParent"], True)
        self.assertIs(creates[1]["allowMissingParent"], True)
        self.assertEqual(creates[0]["parentAgentId"], manager.agent_id)
        self.assertNotIn("agent-state", [cmd for cmd, _ in self.runtime.calls])

    def test_rule2_opening_runs_stored_replay_request_verbatim_and_delivers_preopen_batch(
        self,
    ) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(
                request_id=uuid.uuid4(),
                reviewer=SeatLaunchOverride(agent="codex", model="gpt-a", effort="high"),
                **selection_of("worker"),
            ),
            environment=manager.environment(),
        )
        self.assertTrue(started["ok"], started)
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        rev_sel = RoleSelection.model_validate({"role": "reviewer", **ROLE_REFS["reviewer"]})
        rev_path = _receipt_path(self.config, rev_sel)
        rev_receipt = self.receipt_for("reviewer")
        stored_model = rev_receipt["replayRequest"]["agent"]["model"]
        stored_effort = rev_receipt["replayRequest"]["agent"]["thinkingOptionId"]
        self.assertEqual((stored_model, stored_effort), ("gpt-a", "high"))

        settings_data = json.loads(self.config.config_path.read_text(encoding="utf-8"))
        settings_data.setdefault("roleLaunch", {})["defaultModelId"] = "changed-model"
        settings_data["roleLaunch"]["defaultEffort"] = "low"
        self.config.config_path.write_text(json.dumps(settings_data), encoding="utf-8")

        rev_ref = held_seat_ref(rev_path, rev_receipt)
        assert rev_ref.workspace is not None
        with open_held_store(rev_ref):
            hold_message(
                rev_ref.workspace,
                reviewer_id,
                HeldMessage(
                    sender_line="From manager · M1 · agent m",
                    sender_agent_id=manager.agent_id,
                    text="Pre-open queued note.",
                    reason="held-seat",
                ),
            )
        self.runtime.calls.clear()
        opened = self.message(manager, agent_id=reviewer_id, text="Triggering open note.")
        self.assertEqual((opened["ok"], opened["openedHeldSeat"]), (True, True))
        self.assertEqual(opened["recipientAgentId"], reviewer_id)
        self.assertIs(opened["bindingConfirmation"]["retried"], False)
        creates = [payload for cmd, payload in self.runtime.calls if cmd == "agent-create"]
        self.assertEqual(len(creates), 2)
        self.assertEqual((creates[0]["model"], creates[0]["thinkingOptionId"]), ("gpt-a", "high"))
        self.assertIn("Pre-open queued note.", creates[1]["prompt"])
        self.assertIn("Triggering open note.", creates[1]["prompt"])

    def test_rule3_readers_keep_deferred_held_seat_live(self) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        curator_id = started["seats"]["curator"]["agentId"]
        selection = RoleSelection.model_validate({"role": "reviewer", **ROLE_REFS["reviewer"]})
        path = _receipt_path(self.config, selection)
        receipt = self.receipt_for("reviewer")

        self.runtime.calls.clear()
        refreshed = role_launch_liveness._refresh_execution(self.config, path, dict(receipt))
        self.assertEqual((refreshed["status"], refreshed["agentId"]), ("held", reviewer_id))
        self.assertEqual(self.runtime.calls, [])

        ref = held_seat_ref(path, receipt)
        assert ref.workspace is not None
        with open_held_store(ref):
            hold_message(
                ref.workspace,
                reviewer_id,
                HeldMessage(
                    sender_line="From manager · M1 · agent m",
                    sender_agent_id=manager.agent_id,
                    text="Queued while reserved.",
                    reason="held-seat",
                ),
            )
        recounted = dict(receipt)
        recount_waiting(recounted)
        self.assertEqual(recounted["heldWaiting"], 1)

        mgr_selection = RoleSelection.model_validate({"role": "manager", **ROLE_REFS["manager"]})
        mgr_path = _receipt_path(self.config, mgr_selection)
        mgr_receipt = {
            "schema": "ar-role-execution/v1",
            "requestId": str(uuid.uuid4()),
            "role": "manager",
            "agentId": manager.agent_id,
            "status": "running",
            "selection": mgr_selection.model_dump(mode="json", by_alias=True, exclude_none=True),
            "execution": {"kind": "paseo-agent", "agentId": manager.agent_id},
        }
        mgr_path.parent.mkdir(parents=True, exist_ok=True)
        mgr_path.write_text(json.dumps(mgr_receipt), encoding="utf-8")
        self.runtime.agents[manager.agent_id] = {
            "id": manager.agent_id,
            "provider": "codex",
            "status": "idle",
            "archivedAt": None,
            "received": [],
        }
        worker_id = started["seats"]["worker"]["agentId"]
        self.runtime.agents[worker_id]["archivedAt"] = "2026-11-26T20:00:00Z"
        self.runtime.calls.clear()
        mgr_retire = retire_native_seat(
            self.config,
            RetireChildRequest(role="manager", agent_id=manager.agent_id, reason="Test children."),
            environment=self.architect.environment(),
        )
        self.assertEqual((mgr_retire["ok"], mgr_retire["status"]), (False, "open-children"))
        open_ids = {row["agentId"] for row in mgr_retire["openChildren"]}
        self.assertEqual(open_ids, {reviewer_id, curator_id})
        read_ids = [p["agentId"] for cmd, p in self.runtime.calls if cmd == "agent-state"]
        self.assertNotIn(reviewer_id, read_ids)
        self.assertNotIn(curator_id, read_ids)

        self._write_exhausted_limit(reviewer_id)
        act_dir = activity_directory(self.config)
        assert act_dir is not None
        act_dir.mkdir(parents=True, exist_ok=True)
        (act_dir / HOST_ACTIVITY_FILE).write_text(
            json.dumps(
                {
                    "bootId": "boot-l58",
                    "complete": True,
                    "ignoredEvents": 0,
                    "agents": {
                        reviewer_id: {"state": "ended", "eventAt": "2026-11-26T20:01:00Z"},
                    },
                }
            ),
            encoding="utf-8",
        )
        batch = role_held_messages._DrainBatch(
            config=self.config,
            path=path,
            receipt=receipt,
            folder=str(ref.workspace),
            agent_id=reviewer_id,
            messages=[{"id": "m1", "text": "Queued while reserved."}],
            claim="claim-1",
        )
        self.runtime.calls.clear()
        provider_limit_drain.pause_for_provider_limit(batch)
        self.assertEqual(self.runtime.calls, [])

    def _write_exhausted_limit(self, agent_id: str) -> None:
        _write_limit_file(self.config, agent_id)


def _write_limit_file(config: Any, agent_id: str) -> None:
    limits_file = provider_limits_path(config)
    assert limits_file is not None
    limits_file.parent.mkdir(parents=True, exist_ok=True)
    limits_file.write_text(
        json.dumps(
            {
                "schema": "ar-provider-limits/v1",
                "reports": {
                    "codex:test": {
                        "until": "2099-01-01T00:00:00Z",
                        "sourceLabel": "Codex",
                        "accountLabel": "test",
                        "windows": [
                            {
                                "id": "weekly",
                                "label": "Weekly",
                                "resetsAt": "2099-01-01T00:00:00Z",
                            }
                        ],
                        "observedAt": "2026-11-26T20:00:00Z",
                        "lastReadAttemptAt": None,
                    }
                },
                "agents": {agent_id: ["codex:test"]},
                "clearedSeats": {},
                "updatedAt": "2026-11-26T20:00:00Z",
            }
        ),
        encoding="utf-8",
    )


class TestDeferredHeldReplacementAndLimits(RoleToolsTestCase):
    """L58-R5 & L58-R7: replacing-start predecessor debt and post-open provider limits."""

    def receipt_for(self, role: str) -> dict[str, Any]:
        selection = RoleSelection.model_validate({"role": role, **ROLE_REFS[role]})
        loaded = _read_receipt(_receipt_path(self.config, selection))
        assert loaded is not None
        return loaded

    def test_rule5_replacing_held_start_keeps_debt_and_reserves_nothing_when_archive_fails(
        self,
    ) -> None:
        started_live = self.start(self.architect, "reviewer", **selection_of("reviewer"))
        self.assertTrue(started_live["ok"])
        live_id = started_live["agentId"]
        self.runtime.agents[live_id]["status"] = "idle"
        self.runtime.calls.clear()

        self.runtime.fail("agent-archive", "paseo_bridge_timeout")
        request = role_launch_routes.RoleDispatchRequest.model_validate(
            {
                "role": "reviewer",
                "requestId": uuid.uuid4(),
                "replacesAgentId": live_id,
                **ROLE_REFS["reviewer"],
            }
        )
        response = role_launch_routes._start_execution(self.config, request, held=True)
        self.assertEqual(response.status_code, 202)
        receipt = self.receipt_for("reviewer")
        self.assertEqual(receipt["status"], "unknown")
        self.assertEqual(receipt["pendingArchiveAgentId"], live_id)
        self.assertIsNone(self.runtime.agents[live_id]["archivedAt"])

        response_retry = role_launch_routes._start_execution(self.config, request, held=True)
        self.assertEqual(response_retry.status_code, 200)
        reserved = self.receipt_for("reviewer")
        self.assertEqual(reserved["status"], "held")
        self.assertIs(reserved["hostAgentExists"], False)
        self.assertNotIn("pendingArchiveAgentId", reserved)
        self.assertIsNotNone(self.runtime.agents[live_id]["archivedAt"])

    def test_rule5_replacing_held_start_keeps_debt_when_predecessor_settlement_write_fails(
        self,
    ) -> None:
        started_live = self.start(self.architect, "reviewer", **selection_of("reviewer"))
        self.assertTrue(started_live["ok"])
        live_id = started_live["agentId"]
        live_req = started_live["requestId"]
        self.runtime.agents[live_id]["status"] = "idle"
        self.runtime.calls.clear()

        request = role_launch_routes.RoleDispatchRequest.model_validate(
            {
                "role": "reviewer",
                "requestId": uuid.uuid4(),
                "replacesAgentId": live_id,
                **ROLE_REFS["reviewer"],
            }
        )
        with patch.object(
            role_retirement_receipt,
            "settle_confirmed_replacement",
            side_effect=OSError("disk full"),
        ):
            response = role_launch_routes._start_execution(self.config, request, held=True)

        self.assertEqual(response.status_code, 202)
        receipt = self.receipt_for("reviewer")
        self.assertEqual(receipt["status"], "unknown")
        self.assertEqual(receipt["pendingArchiveAgentId"], live_id)
        self.assertIn("predecessor_retirement_write_failed", receipt["detail"])
        self.assertIsNotNone(self.runtime.agents[live_id]["archivedAt"])
        sel = RoleSelection.model_validate({"role": "reviewer", **ROLE_REFS["reviewer"]})
        history_path = _receipt_path(self.config, sel).parent / "history" / f"{live_req}.json"
        prior_before = _read_receipt(history_path)
        assert prior_before is not None
        self.assertNotIn("retiredBy", prior_before)
        self.assertIn("retireIntent", prior_before)

        # Retrying once the filesystem fault clears settles the predecessor and reserves the seat.
        response_retry = role_launch_routes._start_execution(self.config, request, held=True)
        self.assertEqual(response_retry.status_code, 200)
        reserved = self.receipt_for("reviewer")
        self.assertEqual(reserved["status"], "held")
        self.assertIs(reserved["hostAgentExists"], False)
        self.assertNotIn("pendingArchiveAgentId", reserved)
        prior_after = _read_receipt(history_path)
        assert prior_after is not None
        self.assertIn("retiredBy", prior_after)
        self.assertNotIn("retireIntent", prior_after)

    def test_rule7_reserved_seat_not_held_by_provider_limit_but_opened_seat_is(self) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        _write_limit_file(self.config, reviewer_id)

        opened = self.message(manager, agent_id=reviewer_id, text="First message opens seat.")
        self.assertEqual(
            (opened["ok"], opened["status"], opened["openedHeldSeat"]), (True, "accepted", True)
        )
        self.assertEqual(opened["recipientAgentId"], reviewer_id)

        self.runtime.agents[reviewer_id]["status"] = "idle"
        held = self.message(manager, agent_id=reviewer_id, text="Second message hits limit.")
        self.assertEqual(
            (held["ok"], held["status"], held["heldReason"]), (True, "held", "provider-limit")
        )
