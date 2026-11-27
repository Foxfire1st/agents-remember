"""BUG-R58@v2 Rule 4 integration tests: task-end archive boundaries for deferred held seats."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.application import worktree_tools
from agents_remember.cli import role_launch_archive as archive
from agents_remember.cli.paseo_bridge import DAEMON_UNREACHABLE, PaseoBridgeFailure
from agents_remember.controlplane.held_store_layout import HeldMessage, hold_message, store_path_for
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.services import bind_worktree_services, worktree_services
from test_leaf_agent_archive import LeafAgentArchiveTests
from test_lifecycle_finalize import _payload

pytestmark = pytest.mark.integration


class DeferredHeldSeatArchiveTests(LeafAgentArchiveTests):
    """L58-R4: finalize, cleanup, abandon, preview, and entered-open archive debt."""

    def _convert_to_deferred_held(self, path: Path, *, held_open_pending: bool = False) -> None:
        body = json.loads(path.read_text(encoding="utf-8"))
        body.update(status="held", hostAgentExists=False, execution={})
        if held_open_pending:
            body["heldOpenPending"] = True
        path.write_text(json.dumps(body), encoding="utf-8")

    def test_deferred_held_seat_ends_without_host_on_finalize_cleanup_abandon_and_preview(
        self,
    ) -> None:
        path, body = self.receipt("reviewer")
        self._convert_to_deferred_held(path)
        bind_worktree_services(
            replace(worktree_services(), leaf_agent_archive=archive.LeafAgentArchive(self.config))
        )
        with patch.object(
            archive,
            "bridge_call",
            side_effect=PaseoBridgeFailure(DAEMON_UNREACHABLE, "Host stopped."),
        ) as host:
            finalized = _payload(
                finalize_result(FinalizeArgs(contract_path=self.contract.contract_path))
            )
        host.assert_not_called()
        self.assertEqual(finalized["state"], "finalized")
        self.assertEqual(
            {row["agentId"] for row in finalized["agentArchive"]["gone"]}, {body["agentId"]}
        )
        self.assertEqual(finalized["agentArchive"]["owed"], [])

        for force in (False, True):
            with self.subTest(force=force):
                sub_root = self.tmp / ("abandon-held" if force else "cleanup-held")
                sub_root.mkdir()
                with patch.object(self, "tmp", sub_root):
                    contract, config, receipt_path, agent_id = self._native_terminal_receipt(
                        landed=not force
                    )
                self._convert_to_deferred_held(receipt_path)
                store = store_path_for(contract.worktree_group, agent_id)
                hold_message(
                    contract.worktree_group,
                    agent_id,
                    HeldMessage(
                        sender_line="From manager",
                        sender_agent_id=str(uuid.uuid4()),
                        text="Queued on reserved seat.",
                        reason="held-seat",
                    ),
                )
                with patch.object(
                    archive,
                    "bridge_call",
                    side_effect=PaseoBridgeFailure(DAEMON_UNREACHABLE, "Host stopped."),
                ) as host:
                    if not force:
                        preview = worktree_tools.worktree_cleanup_tool(
                            config, contract_path=str(contract.contract_path), dry_run=True
                        )
                        self.assertTrue(preview["ok"], preview)
                        self.assertFalse(preview.get("blockers"))
                        self.assertTrue(store.is_file())
                        applied = worktree_tools.worktree_cleanup_tool(
                            config, contract_path=str(contract.contract_path)
                        )
                    else:
                        applied = worktree_tools.worktree_abandon_tool(
                            config, contract_path=str(contract.contract_path), force=True
                        )
                host.assert_not_called()
                self.assertTrue(applied["ok"], applied)
                self.assertFalse(contract.worktree_group.exists())
                closed = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertEqual(closed["leafArchive"]["outcomes"][agent_id]["state"], "gone")
                self.assertEqual(closed["retiredBy"]["caller"], "LeafAgentArchive")
                waiting = closed["heldStoreClosures"]["0"]["waitingMessages"]
                self.assertEqual([row["text"] for row in waiting], ["Queued on reserved seat."])

    def test_entered_deferred_held_open_settles_through_host_and_reports_debt(self) -> None:
        contract, config, receipt_path, agent_id = self._native_terminal_receipt(landed=True)
        self._convert_to_deferred_held(receipt_path, held_open_pending=True)
        with patch.object(
            archive,
            "bridge_call",
            side_effect=PaseoBridgeFailure(DAEMON_UNREACHABLE, "Host stopped."),
        ) as host:
            preview = worktree_tools.worktree_cleanup_tool(
                config, contract_path=str(contract.contract_path), dry_run=True
            )
            self.assertFalse(preview["ok"], preview)
            self.assertIn(agent_id, {row.get("agent") for row in preview.get("blockers", [])})
            self.assertEqual([call.args[1] for call in host.call_args_list], ["agent-state"])

            host.reset_mock()
            applied = worktree_tools.worktree_cleanup_tool(
                config, contract_path=str(contract.contract_path)
            )
            self.assertFalse(applied["ok"], applied)
            self.assertIn(agent_id, {row.get("agent") for row in applied.get("blockers", [])})
            self.assertEqual([call.args[1] for call in host.call_args_list], ["agent-archive"])
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["leafArchive"]["outcomes"][agent_id]["state"], "owed")
