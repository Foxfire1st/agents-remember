from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
)
from agents_remember.application.structural.reviewer_parent import (
    AmbientReviewerParentError,
    ambient_reviewer_parent,
)
from agents_remember.application.terminal_tools import SpawnOverrides
from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.models.structural.agent import DispatchAgentRequest
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from test_dispatch_agent_ambient import _config, _detected, _FakeHost, _write_topology
from test_worktree_support import write_current_task_lineage


def _write_reviewer_settings(root: Path) -> None:
    path = agentic_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "orchestration": {
                    "roles": {
                        "reviewer": {
                            "harness": "claude",
                            "model": "claude-fable-5",
                            "effort": "max",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


class AmbientReviewerDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sprint, self.master, self.leaf = _write_topology(self.root)
        write_current_task_lineage(
            self.root,
            repo_name="repo",
            master_name="master",
            leaf_id="leaf-1",
        )
        readiness_wait = mock.patch(
            "agents_remember.serving.dispatch_brief.DISPATCH_BRIEF_READINESS_WAIT_SECONDS",
            0.0,
        )
        readiness_wait.start()
        self.addCleanup(readiness_wait.stop)
        self.config = _config(self.root)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.root))
        self.host = _FakeHost()
        _write_reviewer_settings(self.root)
        self.runtime = StructuralAgentRuntime(
            host=self.host,  # type: ignore[arg-type]
            spawn_overrides=SpawnOverrides(
                host=self.host,  # type: ignore[arg-type]
                which=_detected,
            ),
            environ={},
        )

    def _dispatch(self, document):
        return dispatch_agent_tool(
            self.config,
            DispatchAgentRequest(
                task_document_ref=document,
                role="reviewer",
                brief="Review the exact candidate.",
            ),
            self.runtime,
        )

    def test_leaf_and_master_reviewer_get_the_only_unambiguous_manager_parent(self) -> None:
        leaf_result = self._dispatch(self.leaf)
        master_result = self._dispatch(self.master)

        self.assertTrue(leaf_result["ok"])
        self.assertTrue(master_result["ok"])
        rows = {row.task_document_ref: row for row in self.catalog.list()}
        self.assertEqual(rows[self.leaf].structural_parent_task_document_ref, self.master)
        self.assertEqual(rows[self.leaf].structural_parent_role, "manager")
        self.assertEqual(rows[self.master].structural_parent_task_document_ref, self.master)
        self.assertEqual(rows[self.master].structural_parent_role, "manager")

    def test_same_parent_repeat_reuses_the_live_reviewer_generation(self) -> None:
        first = self._dispatch(self.leaf)
        ensured_after_first = list(self.host.ensured)
        first_row = self.catalog.list()[0]

        second = self._dispatch(self.leaf)
        second_row = self.catalog.list()[0]

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(second_row.id, first_row.id)
        self.assertEqual(self.host.ensured, ensured_after_first)
        self.assertEqual(second_row.structural_parent_task_document_ref, self.master)

    def test_sprint_reviewer_refuses_ambiguous_parent_before_host_effects(self) -> None:
        result = self._dispatch(self.sprint)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "structural-parent-ambiguous")
        self.assertIn("architect or orchestrator", result["detail"])
        self.assertEqual(self.host.ensured, [])
        self.assertEqual(self.catalog.list(), [])

    def test_leaf_reviewer_refuses_when_canonical_parent_is_missing(self) -> None:
        topology = mock.Mock()
        topology.altitude.return_value = "leaf"
        topology.parent.return_value = None

        with self.assertRaisesRegex(
            AmbientReviewerParentError,
            "has no canonical owning master",
        ):
            ambient_reviewer_parent(topology, self.leaf)


if __name__ == "__main__":
    unittest.main()
