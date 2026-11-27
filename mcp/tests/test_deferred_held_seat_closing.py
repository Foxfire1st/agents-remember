"""BUG-R58@v2 Rule 4 & D1046 tests: retirement, closing marks, mid-retry race, and reconciliation."""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import patch

from agents_remember.cli import role_held_messages, role_launch_confirmation
from agents_remember.cli.paseo_role_tools import start_leaf_set
from agents_remember.cli.role_held_store import held_seat_ref, open_held_store
from agents_remember.cli.role_launch_archive import refuse_retired_start
from agents_remember.cli.role_launch_receipts import _public_execution, _read_receipt, _receipt_path
from agents_remember.cli.role_retire import retire_native_seat
from agents_remember.cli.shared_held_store_reconciliation import _reconcile_agent
from agents_remember.controlplane.held_store_layout import (
    HeldMessage,
    hold_message,
    recount_waiting,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import RoleStartSetCall
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.structural.agent import RetireChildRequest
from agents_remember.serving.server_bindings import bindings_directory
from paseo_role_tools_test_support import RoleToolsTestCase, binding, selection_of
from test_paseo_launch import ROLE_REFS


class TestDeferredHeldSeatRetirementAndClosing(RoleToolsTestCase):
    """L58-R4 & Sites 7/8: host-free retirement, reconciliation, and closing-mark boundaries."""

    def receipt_for(self, role: str) -> dict[str, Any]:
        selection = RoleSelection.model_validate({"role": role, **ROLE_REFS[role]})
        loaded = _read_receipt(_receipt_path(self.config, selection))
        assert loaded is not None
        return loaded

    def test_retire_deferred_held_seat_sends_zero_host_calls_and_records_caller_and_reason(
        self,
    ) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        curator_id = started["seats"]["curator"]["agentId"]
        self.runtime.calls.clear()

        retired = retire_native_seat(
            self.config,
            RetireChildRequest(role="curator", agent_id=curator_id, reason="Not needed."),
            environment=self.architect.environment(),
        )
        self.assertEqual((retired["ok"], retired["status"]), (True, "retired"))
        self.assertEqual(self.runtime.calls, [])
        receipt = self.receipt_for("curator")
        self.assertEqual(receipt["retiredBy"]["callerAgentId"], self.architect.agent_id)
        self.assertEqual(receipt["retiredBy"]["reason"], "Not needed.")
        self.assertEqual(receipt["status"], "stopped")
        self.assertIs(receipt["hostAgentExists"], False)

    def test_d1046_site7_site8_and_closing_retry_race(self) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        selection = RoleSelection.model_validate({"role": "reviewer", **ROLE_REFS["reviewer"]})
        path = _receipt_path(self.config, selection)
        receipt = self.receipt_for("reviewer")

        public = _public_execution(receipt)
        self.assertEqual(public["agentId"], reviewer_id)
        self.assertEqual(public["holderAgentId"], reviewer_id)

        self.runtime.calls.clear()
        reconciled = _reconcile_agent(self.config, reviewer_id, [(path, receipt)], dry_run=False)
        self.assertEqual(reconciled["state"], "preserved")
        self.assertEqual(self.runtime.calls, [])

        retried_id = str(uuid.uuid4())
        self.runtime.agents[retried_id] = {
            "id": retried_id,
            "provider": "codex",
            "status": "idle",
            "archivedAt": None,
            "received": [],
        }
        marked = {
            **receipt,
            "heldOpenPending": True,
            "leafArchive": {
                "taskDocumentRef": receipt["selection"]["taskDocumentRef"],
                "closing": True,
                "creationPending": True,
                "outcomes": {},
            },
        }
        path.write_text(json.dumps(marked), encoding="utf-8")
        self.runtime.calls.clear()
        with patch(
            "agents_remember.cli.role_launch_archive.bridge_call",
            side_effect=lambda cfg, cmd, payload, **_kw: self.runtime(cfg, cmd, payload),
        ):
            refused = refuse_retired_start(
                self.config,
                path,
                marked,
                creation_returned=True,
                retried_agent_id=retried_id,
            )
        archived_ids = [
            payload["agentId"] for cmd, payload in self.runtime.calls if cmd == "agent-archive"
        ]
        self.assertIn(retried_id, archived_ids)
        self.assertIsNotNone(self.runtime.agents[retried_id]["archivedAt"])
        self.assertEqual(refused["leafArchive"]["outcomes"][retried_id]["state"], "archived")

    def test_rule4_unentered_opening_meets_closing_mark_and_closing_during_retry_archives_orphan(
        self,
    ) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        curator_id = started["seats"]["curator"]["agentId"]

        # 4a: Unentered opening meets a closing mark -> 0 host creates, messages settled undelivered.
        rev_sel = RoleSelection.model_validate({"role": "reviewer", **ROLE_REFS["reviewer"]})
        rev_path = _receipt_path(self.config, rev_sel)
        rev_receipt = self.receipt_for("reviewer")
        rev_ref = held_seat_ref(rev_path, rev_receipt)
        assert rev_ref.workspace is not None
        with open_held_store(rev_ref):
            hold_message(
                rev_ref.workspace,
                reviewer_id,
                HeldMessage(
                    sender_line="From manager · M1 · agent m",
                    sender_agent_id=manager.agent_id,
                    text="Queued before close.",
                    reason="held-seat",
                ),
            )
        closed_rev = {
            **rev_receipt,
            "leafArchive": {
                "taskDocumentRef": rev_receipt["selection"]["taskDocumentRef"],
                "closing": True,
                "creationPending": False,
                "outcomes": {},
            },
        }
        rev_path.write_text(json.dumps(closed_rev), encoding="utf-8")
        self.runtime.calls.clear()
        drained = role_held_messages.drain_held(self.config, rev_path, rev_receipt)
        self.assertEqual(drained["outcome"], "recipient-archived")
        self.assertNotIn("agent-create", [cmd for cmd, _ in self.runtime.calls])
        settled_rev = self.receipt_for("reviewer")
        recount_waiting(settled_rev)
        self.assertEqual(settled_rev.get("heldWaiting", 0), 0)

        # 4b: Closing mark lands mid-BUG-R51 retry during _open_held_seat -> retried agent archived.
        cur_sel = RoleSelection.model_validate({"role": "curator", **ROLE_REFS["curator"]})
        cur_path = _receipt_path(self.config, cur_sel)
        cur_receipt = self.receipt_for("curator")
        cur_ref = held_seat_ref(cur_path, cur_receipt)
        assert cur_ref.workspace is not None
        with open_held_store(cur_ref):
            hold_message(
                cur_ref.workspace,
                curator_id,
                HeldMessage(
                    sender_line="From manager · M1 · agent m",
                    sender_agent_id=manager.agent_id,
                    text="Open curator while leaf closes mid-retry.",
                    reason="held-seat",
                ),
            )
        real_confirm = role_launch_confirmation.confirm_seat_binding
        retried_ids: list[str] = []

        def confirm_with_mid_retry_close(
            config: McpRuntimeConfig, agent_id: str, request_id: str, **kw: Any
        ):
            if agent_id == curator_id:
                (bindings_directory(self.config) / f"fake-{curator_id}.json").unlink(
                    missing_ok=True
                )
            else:
                retried_ids.append(agent_id)
                latest = _read_receipt(cur_path)
                assert latest is not None
                latest["leafArchive"] = {
                    "taskDocumentRef": latest["selection"]["taskDocumentRef"],
                    "closing": True,
                    "creationPending": True,
                    "outcomes": {},
                }
                cur_path.write_text(json.dumps(latest), encoding="utf-8")
            return real_confirm(config, agent_id, request_id, **kw)

        self.replace(role_launch_confirmation, "confirm_seat_binding", confirm_with_mid_retry_close)
        with patch(
            "agents_remember.cli.role_launch_archive.bridge_call",
            side_effect=lambda cfg, cmd, payload, **_kw: self.runtime(cfg, cmd, payload),
        ):
            result = role_held_messages.drain_held(self.config, cur_path, cur_receipt)
        self.assertEqual(result["outcome"], "recipient-archived")
        self.assertEqual(len(retried_ids), 1)
        retried_id = retried_ids[0]
        self.assertIsNotNone(self.runtime.agents[curator_id]["archivedAt"])
        self.assertIsNotNone(self.runtime.agents[retried_id]["archivedAt"])
