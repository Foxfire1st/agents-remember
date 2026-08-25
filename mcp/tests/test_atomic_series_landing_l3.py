"""L3 forcing for contract/ref-owned atomic landing exclusion."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.worktrees.integration import atomic_series_landing as landing
from agents_remember.worktrees.integration import atomic_series_terminal as terminal
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_closeout_queue import MASTER_B, QueueFixture


class AtomicSeriesLandingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(self.root, atomic_b=True, memory_mode="internal")
        self.current = self.fixture.contracts[MASTER_B]
        self.series_path = self.fixture.tasks / "master-b" / "series-contract.md"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_declared_parent_same_target_permits_leaf_landing(self) -> None:
        landing.require_atomic_landing_authority(self.current)

    def test_live_nonterminal_unrelated_same_target_contract_blocks_landing(self) -> None:
        unrelated = replace(
            self.current,
            parent_task_name="",
            parent_contract_path=None,
        )
        with self.assertRaises(landing.AtomicLandingBlocked) as raised:
            landing.require_atomic_landing_authority(unrelated)
        self.assertEqual(raised.exception.blocker.state, "live-nonterminal")

    def test_live_owner_survives_task_detach_and_malformed_topology(self) -> None:
        (self.fixture.tasks / "master-b" / "task.json").unlink()
        (self.fixture.tasks / "sprint" / "task.json").write_text("{malformed", encoding="utf-8")

        unrelated = replace(
            self.current,
            parent_task_name="",
            parent_contract_path=None,
        )
        with self.assertRaises(landing.AtomicLandingBlocked) as raised:
            landing.require_atomic_landing_authority(unrelated)

        self.assertEqual(raised.exception.blocker.contract_path, self.series_path)
        self.assertEqual(raised.exception.blocker.state, "live-nonterminal")

    def test_normal_completion_cleanup_and_abandon_release_owner(self) -> None:
        unrelated = replace(
            self.current,
            parent_task_name="",
            parent_contract_path=None,
        )
        for updates in (
            {"integration_status": "completed"},
            {"cleanup": "completed"},
            {"cleanup": "abandoned"},
        ):
            with self.subTest(updates=updates):
                owner = load_contract(self.series_path)
                write_contract(self.series_path, replace(owner, **updates))
                landing.require_atomic_landing_authority(unrelated)
                write_contract(
                    self.series_path,
                    replace(owner, integration_status="not-started", cleanup="pending"),
                )

    def test_present_unreadable_series_authority_fails_closed(self) -> None:
        self.series_path.write_text("not a contract\n", encoding="utf-8")
        with self.assertRaises(landing.AtomicLandingBlocked) as raised:
            landing.require_atomic_landing_authority(self.current)
        self.assertEqual(raised.exception.blocker.state, "present-unreadable")

    def test_nonregular_series_authority_is_present_unreadable_not_absent(self) -> None:
        self.series_path.unlink()
        self.series_path.mkdir()
        with self.assertRaises(landing.AtomicLandingBlocked) as raised:
            landing.require_atomic_landing_authority(self.current)
        self.assertEqual(raised.exception.blocker.state, "present-unreadable")

    def test_distinct_protected_target_does_not_block(self) -> None:
        unrelated = replace(self.current, code_source_branch="unrelated-target")
        landing.require_atomic_landing_authority(unrelated)


class AtomicSeriesTerminalCapabilityTests(unittest.TestCase):
    def test_terminal_release_does_not_reread_mutable_task_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = QueueFixture(Path(temporary), atomic_b=True, memory_mode="internal")
            series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
            (fixture.tasks / "master-b" / "task.json").unlink()
            with mock.patch.object(terminal, "require_series_children_retired") as retired:
                terminal.require_atomic_series_terminal_release(series)
            retired.assert_called_once_with(series)

    def test_capability_is_live_only_inside_exact_publication_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = QueueFixture(Path(temporary), atomic_b=True, memory_mode="internal")
            contract = load_contract(fixture.tasks / "master-b" / "series-contract.md")
            observed: list[terminal.AtomicSeriesTerminalPermit] = []

            def publication(permit: terminal.AtomicSeriesTerminalPermit) -> str:
                terminal.require_atomic_series_terminal_permit(
                    contract,
                    "worktree_cleanup",
                    permit,
                )
                observed.append(permit)
                return "published"

            with (
                mock.patch.object(terminal, "_require_atomic_series"),
                mock.patch.object(terminal, "require_series_children_retired"),
            ):
                self.assertEqual(
                    terminal.publish_atomic_series_terminal_under_authority(
                        contract,
                        "worktree_cleanup",
                        publication,
                    ),
                    "published",
                )
            with self.assertRaisesRegex(RuntimeError, "live contract-derived authority"):
                terminal.require_atomic_series_terminal_permit(
                    contract,
                    "worktree_cleanup",
                    observed[0],
                )
