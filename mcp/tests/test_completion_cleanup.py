"""Failure-containment tests for report-gated completion cleanup."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import completion_cleanup
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    InboxSubject,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RetirementSettings
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _entry(session_id: str, *, role: str) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Seat {session_id}",
        kind="harness",
        harness="claude",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("claude",),
        created_at="2026-07-07T00:00:00+00:00",
        last_attached_at="2026-07-07T00:00:00+00:00",
        status="running",
        leaf_key="repo/master-a/leaf-1",
        spawn_role=role,
    )


class CompletionCleanupContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.contract_path = self.root / "enclosures" / "contract.md"
        self.contract_path.parent.mkdir(parents=True, exist_ok=True)
        self.contract_path.write_text("placeholder", encoding="utf-8")
        self.contract = WorktreeContract(
            task_id="leaf-1",
            task_name="Leaf",
            repo_name="repo",
            workflow_kind="light-task",
            memory_mode="internal",
            coordination_root=self.root,
            task_root=self.root / "tasks" / "repo" / "master-a",
            contract_path=self.contract_path,
            task_artifact=self.contract_path,
            worktree_group=self.root / "worktrees",
            code_repo_path=self.root / "workspace" / "repo",
            code_source_branch="main",
            code_work_branch="leaf-1",
            code_base_commit="abc123",
            code_worktree=self.root / "worktrees" / "leaf-1",
        )

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _config(self, *, auto_close: bool = True) -> McpRuntimeConfig:
        return McpRuntimeConfig(
            config_path=self.root / "settings.json",
            coordination_root=self.root,
            workspace_root=self.root,
            transcript_root=self.root / "logs" / "mcp",
            retirement=RetirementSettings(auto_close_completed_seats=auto_close),
        )

    def _catalog(self) -> TerminalCatalog:
        return TerminalCatalog(self.root / "logs" / "dashboard" / "terminal-sessions.json")

    def _post_report(self, session_id: str) -> None:
        OperatorInboxStore(self.root / "logs" / "observer").append(
            create_operator_inbox_entry(
                InboxMessage(
                    ask="Turn report",
                    response=f"Completed by {session_id}",
                    message_kind="turn-report",
                    subject=InboxSubject(
                        leaf_key="repo/master-a/leaf-1",
                        agent_id=session_id,
                    ),
                ),
                entry_id=f"report-{session_id}",
                now="2026-08-05T10:00:00+00:00",
                routing=InboxRouting(address=InboxAddress(recipient_role="manager")),
                poster=InboxPoster(
                    created_by="model",
                    created_via="cli",
                    sender_agent_id=session_id,
                    sender_role="worker",
                ),
            )
        )

    def _cleanup(self, *, auto_close: bool = True) -> dict[str, list[str]]:
        return completion_cleanup.auto_complete_seats(
            self._config(auto_close=auto_close),
            self.contract_path,
            reason="auto-close: leaf integrated into master",
            edge="leaf-integration",
        )

    def test_unreadable_contract_returns_empty_cleanup_evidence(self) -> None:
        with mock.patch.object(
            completion_cleanup,
            "load_contract",
            side_effect=OSError("gone"),
        ):
            result = self._cleanup()
        self.assertEqual(result["autoClosedSeats"], [])
        self.assertEqual(result["autoCloseDeferredSeats"], [])
        self.assertEqual(result["autoCloseFailedSeats"], [])

    def test_retirement_failure_is_contained_and_attributed_per_seat(self) -> None:
        catalog = self._catalog()
        catalog.upsert(_entry("worker-1", role="worker"))
        self._post_report("worker-1")
        with (
            mock.patch.object(completion_cleanup, "load_contract", return_value=self.contract),
            mock.patch.object(
                completion_cleanup,
                "retire_entry",
                side_effect=OSError("catalog failed"),
            ),
        ):
            result = self._cleanup()
        self.assertEqual(result["autoClosedSeats"], [])
        self.assertEqual(result["autoCloseFailedSeats"], ["worker-1"])
        self.assertEqual(catalog.get("worker-1").status, "running")  # type: ignore[union-attr]

    def test_concurrent_retirement_race_is_not_reported_as_failure(self) -> None:
        self._catalog().upsert(_entry("worker-1", role="worker"))
        self._post_report("worker-1")
        with (
            mock.patch.object(completion_cleanup, "load_contract", return_value=self.contract),
            mock.patch.object(completion_cleanup, "retire_entry", return_value=None),
        ):
            result = self._cleanup()
        self.assertEqual(result["autoClosedSeats"], [])
        self.assertEqual(result["autoCloseFailedSeats"], [])

    def test_landing_opt_out_failure_is_contained(self) -> None:
        self._catalog().upsert(_entry("reviewer", role="reviewer"))
        with (
            mock.patch.object(completion_cleanup, "load_contract", return_value=self.contract),
            mock.patch.object(
                completion_cleanup,
                "land_seats_for_leaf",
                side_effect=RuntimeError("unexpected failure"),
            ),
        ):
            result = self._cleanup(auto_close=False)
        self.assertEqual(result["autoLandedSeats"], [])


if __name__ == "__main__":
    unittest.main()
