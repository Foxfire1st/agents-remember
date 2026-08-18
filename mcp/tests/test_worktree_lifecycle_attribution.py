"""worktree_start promotes / worktree_attach resumes the lifecycle (slice 2c).

These unit-test the application-layer attribution helpers against a real ambient
lifecycle without standing up git worktrees: the helpers read the (snake_case)
result payload and drive promote/adopt/save-gate on the process singleton.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.worktree_tools import (
    TaskIdentity,
    _attribute_attach,
    _attribute_start,
    worktree_start_tool,
)
from agents_remember.observer.ambient import AmbientLifecycle, AmbientTiming
from agents_remember.observer.save_gate import SaveGateRequired
from agents_remember.observer.store import EventStore


class _AttributionCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = EventStore(Path(self._dir.name))
        self.amb = AmbientLifecycle(self.store, timing=AmbientTiming(heartbeat_seconds=3600))
        self.addCleanup(self.amb.shutdown)

    def kinds(self, lifecycle_id: str) -> list[str]:
        return [event.kind for event in self.store.read(lifecycle_id)]


class AttributeStartTests(_AttributionCase):
    def _started(self, lifecycle_id: str) -> dict[str, Any]:
        return {
            "state": "started",
            "contract_path": "/c/series-contract.md",
            "lifecycle_id": lifecycle_id,
        }

    def test_started_promotes_the_active_lifecycle(self) -> None:
        lc = self.amb.start()
        _attribute_start(self.amb, self._started(lc.id), "repo-a")
        self.assertTrue(self.amb.current is not None and not self.amb.current.fleeting)
        self.assertEqual(self.amb.current and self.amb.current.scope, "repo-a")
        self.assertIn("lifecycle.promoted", self.kinds(lc.id))

    def test_started_with_no_active_adopts_the_minted_id(self) -> None:
        _attribute_start(self.amb, self._started("LC-MINTED"), "repo-a")
        self.assertEqual(self.amb.current and self.amb.current.id, "LC-MINTED")
        self.assertIn("lifecycle.resumed", self.kinds("LC-MINTED"))

    def test_non_started_result_does_not_attribute(self) -> None:
        lc = self.amb.start()
        _attribute_start(self.amb, {"state": "blocked"}, "repo-a")
        self.assertTrue(self.amb.current is not None and self.amb.current.fleeting)
        self.assertNotIn("lifecycle.promoted", self.kinds(lc.id))

    def test_start_preview_does_not_attribute(self) -> None:
        lc = self.amb.start()
        _attribute_start(self.amb, {"state": "would-start"}, "repo-a")
        self.assertTrue(self.amb.current is not None and self.amb.current.fleeting)
        self.assertNotIn("lifecycle.promoted", self.kinds(lc.id))

    def test_none_ambient_is_a_noop(self) -> None:
        _attribute_start(None, self._started("X"), "repo-a")  # must not raise

    def test_start_refuses_to_repoint_an_unrelated_persistent_lifecycle(self) -> None:
        lifecycle = self.amb.start()
        self.amb.promote(enclosure="/other/task.md", repo_id="repo-a", scope="repo-a")
        with (
            mock.patch(
                "agents_remember.application.worktree_tools.require_repo",
                return_value=mock.Mock(repo_id="repo-a"),
            ),
            mock.patch("agents_remember.application.worktree_tools.ambient", return_value=self.amb),
            mock.patch(
                "agents_remember.application.worktree_tools.git_worktree_manager.start_result"
            ) as start,
        ):
            result = worktree_start_tool(
                mock.Mock(),
                TaskIdentity("repo-a", "new-task", "new-task", leaf_id="NEW"),
            )

        self.assertEqual(result["state"], "lifecycle-switch-required")
        self.assertEqual(result["nextOperation"], "switch_task_lifecycle")
        self.assertEqual(result["nextTool"], "switch_lifecycle")
        self.assertEqual(result["nextArgs"], {})
        self.assertEqual(result["nextStep"]["nextTool"], "switch_lifecycle")
        self.assertEqual(self.amb.current and self.amb.current.id, lifecycle.id)
        start.assert_not_called()


class AttributeAttachTests(_AttributionCase):
    def _attached(self, lifecycle_id: str) -> dict[str, Any]:
        return {
            "state": "attached",
            "contract_path": "/c/series-contract.md",
            "lifecycle_id": lifecycle_id,
        }

    def test_attach_adopts_when_none_active(self) -> None:
        _attribute_attach(self.amb, self._attached("LC-A"), "repo-a", None)
        self.assertEqual(self.amb.current and self.amb.current.id, "LC-A")

    def test_attach_over_fleeting_requires_a_decision(self) -> None:
        self.amb.start()
        with self.assertRaises(SaveGateRequired):
            _attribute_attach(self.amb, self._attached("LC-A"), "repo-a", None)

    def test_attach_discard_resolves_the_gate(self) -> None:
        self.amb.start()
        _attribute_attach(self.amb, self._attached("LC-A"), "repo-a", "discard")
        self.assertEqual(self.amb.current and self.amb.current.id, "LC-A")

    def test_non_attached_result_does_not_attribute(self) -> None:
        lc = self.amb.start()
        _attribute_attach(self.amb, {"state": "blocked"}, "repo-a", None)
        self.assertEqual(self.amb.current and self.amb.current.id, lc.id)

    def test_missing_lifecycle_id_is_a_noop(self) -> None:
        lc = self.amb.start()
        _attribute_attach(
            self.amb, {"state": "attached", "contract_path": "/c/x.md"}, "repo-a", None
        )
        self.assertEqual(self.amb.current and self.amb.current.id, lc.id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
