"""Regression coverage for sprint-local named role seats."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.application.terminal_tools import _open_terminal_refusal
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_architect_owner,
    derive_row_owner,
)
from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.serving.sprint_role_binding import (
    SprintOpenBindingRequest,
    sprint_binding_for_attachment,
    sprint_binding_for_reopen,
    sprint_binding_for_spawn,
    sprint_binding_from_leaf,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_opener import OpenTerminalResult
from test_spawn_agent_session import _config, _detected, _FakeHost, _write_leaf_task, call_spawn

STAMP = "2026-08-10T00:00:00+00:00"


class SprintLocalRoleSeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _write_leaf_task(self.root, repo="repo-a", master="sprint-a")
        _write_leaf_task(self.root, repo="repo-b", master="sprint-b")
        self.config = _config(self.root)
        self.host = _FakeHost()
        self.catalog = TerminalCatalog(self.root / "logs" / "dashboard" / "terminal-sessions.json")
        settings_path = agentic_settings_path(self.root)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            '{"orchestration":{"roles":{"architect":{"harness":"claude","model":"claude-fable-5","effort":"max"},"orchestrator":{"harness":"claude","model":"claude-fable-5","effort":"max"},"manager":{"harness":"claude","model":"claude-fable-5","effort":"max"}}}}',
            encoding="utf-8",
        )

    def _spawn(self, session_id: str, *, role: str, **kwargs: object) -> dict[str, object]:
        return call_spawn(
            self.config,
            session_id=session_id,
            host=self.host,
            which=_detected,
            env={"AR_SPAWN_ROLE": role},
            **kwargs,
        )

    def test_two_live_sprints_keep_named_seats_and_custody_separate(self) -> None:
        architect_a = self._spawn(
            "architect-a", role="architect", leaf_key="repo-a/sprint-a/leaf-1"
        )
        architect_b = self._spawn(
            "architect-b", role="architect", leaf_key="repo-b/sprint-b/leaf-1"
        )
        orchestrator_a = self._spawn(
            "orchestrator-a",
            role="orchestrator",
            spawned_by_session="architect-a",
        )
        orchestrator_b = self._spawn(
            "orchestrator-b",
            role="orchestrator",
            spawned_by_session="architect-b",
        )

        for payload, repo, sprint in (
            (architect_a, "repo-a", "sprint-a"),
            (architect_b, "repo-b", "sprint-b"),
            (orchestrator_a, "repo-a", "sprint-a"),
            (orchestrator_b, "repo-b", "sprint-b"),
        ):
            self.assertEqual(payload["status"], "spawned-unbriefed")
            self.assertEqual(payload["spawnRepo"], repo)
            self.assertEqual(payload["spawnSprint"], sprint)

        self.assertEqual(
            derive_architect_owner(self.catalog, leaf_key="repo-a/sprint-a/leaf-1"),
            RoutedOwner(role="architect", agent_id="architect-a"),
        )
        self.assertEqual(
            derive_architect_owner(self.catalog, leaf_key="repo-b/sprint-b/leaf-1"),
            RoutedOwner(role="architect", agent_id="architect-b"),
        )

    def test_named_roles_require_or_inherit_a_sprint_binding(self) -> None:
        for role in ("architect", "orchestrator", "manager"):
            with self.subTest(role=role):
                payload = self._spawn(f"unbound-{role}", role=role)
                self.assertEqual(payload["status"], "sprint-binding-required")
        self.assertEqual(self.host.ensured, [])

        missing_parent = self._spawn(
            "missing-parent-manager", role="manager", spawned_by_session="missing-parent"
        )
        self.assertEqual(missing_parent["status"], "sprint-binding-required")
        self.assertEqual(self.host.ensured, [])

        self._spawn("architect-a", role="architect", leaf_key="repo-a/sprint-a/leaf-1")
        manager = self._spawn("manager-a", role="manager", spawned_by_session="architect-a")
        self.assertEqual(manager["spawnRepo"], "repo-a")
        self.assertEqual(manager["spawnSprint"], "sprint-a")

        conflict = self._spawn(
            "manager-b",
            role="manager",
            spawned_by_session="architect-a",
            leaf_key="repo-b/sprint-b/leaf-1",
        )
        self.assertEqual(conflict["status"], "sprint-binding-conflict")

    def test_invalid_leaf_key_cannot_supply_a_sprint_binding(self) -> None:
        self.assertIsNone(sprint_binding_from_leaf("repo-a/sprint-a"))

    def test_policy_refuses_partial_unknown_and_conflicting_scope_inputs(self) -> None:
        unbound = TerminalCatalogEntry(
            id="legacy",
            label="Legacy",
            kind="harness",
            harness="claude",
            lifecycle_id=None,
            cwd=Path("/workspace"),
            tmux_name="ar-legacy",
            command=("claude",),
            created_at=STAMP,
            last_attached_at=STAMP,
            status="running",
            spawn_role="architect",
        )
        bound = unbound.with_leaf_binding(
            "repo-a/sprint-a/leaf-1", "architect", spawn_repo="repo-a", spawn_sprint="sprint-a"
        )
        self.assertEqual(
            sprint_binding_for_spawn(
                "manager",
                leaf_key=None,
                replacement_for_leaf=None,
                parent=unbound,
                parent_session_id="legacy",
            )[1],
            "sprint-binding-required",
        )
        self.assertEqual(
            sprint_binding_for_attachment("architect", leaf_key="not-qualified", entry=unbound)[1],
            "sprint-binding-required",
        )
        self.assertEqual(
            sprint_binding_for_attachment(
                "architect", leaf_key="repo-a/sprint-a/leaf-2", entry=bound
            )[0],
            sprint_binding_from_leaf("repo-a/sprint-a/leaf-2"),
        )
        self.assertEqual(
            sprint_binding_for_attachment(
                "architect", leaf_key="repo-b/sprint-b/leaf-2", entry=bound
            )[1],
            "sprint-binding-conflict",
        )
        self.assertEqual(
            sprint_binding_for_reopen(
                SprintOpenBindingRequest("architect", None, None, None, None, "repo-a", None)
            )[1],
            "sprint-binding-required",
        )
        self.assertEqual(
            sprint_binding_for_reopen(
                SprintOpenBindingRequest(
                    "architect", "repo-b/sprint-b/leaf", None, bound, None, "repo-a", "sprint-a"
                )
            )[1],
            "sprint-binding-conflict",
        )
        self.assertEqual(
            sprint_binding_for_reopen(
                SprintOpenBindingRequest("architect", None, None, bound, None, None, None)
            )[0],
            sprint_binding_from_leaf("repo-a/sprint-a/leaf-1"),
        )
        refusal = _open_terminal_refusal(
            OpenTerminalResult(status="sprint-binding-required"),
            harness="claude",
            kind="harness",
            session_id="architect-a",
            leaf_key=None,
        )
        assert refusal is not None
        self.assertEqual(refusal["status"], "sprint-binding-required")
        self.assertEqual(
            sprint_binding_for_reopen(
                SprintOpenBindingRequest(
                    "architect", "repo-b/sprint-b/leaf", None, bound, None, None, None
                )
            )[1],
            "sprint-binding-conflict",
        )

    def test_spawn_scope_is_write_once_across_a_respawn(self) -> None:
        self._spawn("architect-a", role="architect", leaf_key="repo-a/sprint-a/leaf-1")
        self.host.known.remove("ar-architect-a")

        reopened = self._spawn("architect-a", role="architect", leaf_key="repo-b/sprint-b/leaf-1")
        row = self.catalog.get("architect-a")
        assert row is not None
        self.assertEqual(reopened["status"], "sprint-binding-conflict")
        self.assertEqual((row.spawn_repo, row.spawn_sprint), ("repo-a", "sprint-a"))

    def test_scope_binding_routes_a_leafless_architect_without_global_fallback(self) -> None:
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="architect-a",
                label="Architect A",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=Path("/workspace"),
                tmux_name="ar-architect-a",
                command=("claude",),
                created_at=STAMP,
                last_attached_at=STAMP,
                status="running",
                spawn_role="architect",
                seat_role="architect",
                spawn_repo="repo-a",
                spawn_sprint="sprint-a",
            )
        )
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="architect-b",
                label="Architect B",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=Path("/workspace"),
                tmux_name="ar-architect-b",
                command=("claude",),
                created_at=STAMP,
                last_attached_at=STAMP,
                status="running",
                spawn_role="architect",
                seat_role="architect",
                spawn_repo="repo-b",
                spawn_sprint="sprint-b",
            )
        )

        self.assertEqual(
            derive_architect_owner(self.catalog, leaf_key="repo-a/sprint-a/leaf-1"),
            RoutedOwner(role="architect", agent_id="architect-a"),
        )
        self.assertEqual(
            derive_architect_owner(self.catalog, leaf_key="repo-b/sprint-b/leaf-1"),
            RoutedOwner(role="architect", agent_id="architect-b"),
        )

    def test_rebind_resolves_only_the_orchestrator_in_the_row_sprint(self) -> None:
        self._spawn("architect-a", role="architect", leaf_key="repo-a/sprint-a/leaf-1")
        self._spawn("architect-b", role="architect", leaf_key="repo-b/sprint-b/leaf-1")
        self._spawn("orchestrator-a", role="orchestrator", spawned_by_session="architect-a")
        self._spawn("orchestrator-b", role="orchestrator", spawned_by_session="architect-b")
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="orchestrator-old",
                label="Old orchestrator",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=Path("/workspace"),
                tmux_name="ar-orchestrator-old",
                command=("claude",),
                created_at=STAMP,
                last_attached_at=STAMP,
                status="terminated",
                spawn_role="orchestrator",
                seat_role="orchestrator",
                spawn_repo="repo-a",
                spawn_sprint="sprint-a",
            )
        )
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="manager-old",
                label="Old manager",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=Path("/workspace"),
                tmux_name="ar-manager-old",
                command=("claude",),
                created_at=STAMP,
                last_attached_at=STAMP,
                status="terminated",
                leaf_key="repo-a/sprint-a/leaf-1",
                spawn_role="manager",
                seat_role="manager",
                spawned_by_session="orchestrator-old",
            )
        )
        row = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="response", message_kind="escalation"),
            entry_id="entry-1",
            now=STAMP,
            routing=InboxRouting(address=InboxAddress(agent_id="manager-old")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        ).model_copy(
            update={
                "leafKey": "repo-a/sprint-a/leaf-1",
                "seatRole": "manager",
                "subjectAgentId": "manager-old",
            }
        )

        self.assertEqual(
            derive_row_owner(self.catalog, row),
            RoutedOwner(role="orchestrator", agent_id="orchestrator-a"),
        )
