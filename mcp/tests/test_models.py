from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.tool_response import complete_tool_response
from agents_remember.mcp.tools import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.worktrees.activation.atomic_series_activation_terminal import (
    with_terminal_atomic_series_release,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _series_contract(root: Path) -> WorktreeContract:
    """The minimum contract ``with_terminal_atomic_series_release`` reads: identity, kind, path."""

    return WorktreeContract(
        kind="series",
        task_id="T",
        task_name="t",
        repo_name="r",
        workflow_kind="light-task",
        memory_mode="disabled",
        coordination_root=root,
        task_root=root / "tasks" / "r" / "t",
        contract_path=root / "tasks" / "r" / "t" / "series-contract.md",
        task_artifact=root / "tasks" / "r" / "t" / "task.md",
        worktree_group=root / "worktrees" / "r" / "t-ar",
        code_repo_path=root / "repo",
        code_source_branch="main",
        code_work_branch="ar/t",
        code_base_commit="a" * 40,
        code_worktree=root / "repo",
    )


class PublicToolResponseModelTests(unittest.TestCase):
    def test_every_public_tool_has_a_schema_generating_response_model(self) -> None:
        """One subject, two halves: the registry is total, and every model it names is usable.

        These were two cases and are one: both iterate the same registry over the same set, and
        neither can be true while the other is false. The consolidation is what kept the unit lane
        inside its declared 2300 cases on the line this leaf certifies.
        """

        self.assertEqual(set(PUBLIC_TOOLS), set(PUBLIC_TOOL_RESPONSE_MODELS))
        for tool_name, model in PUBLIC_TOOL_RESPONSE_MODELS.items():
            with self.subTest(tool=tool_name):
                self.assertTrue(issubclass(model, BaseModel))
                schema = model.model_json_schema()
                self.assertEqual(schema["type"], "object")
                self.assertIn("properties", schema)

    def test_the_series_finalize_success_payload_validates_at_its_registered_model(self) -> None:
        """D-47: the terminal operation's own success payload must survive the wire boundary.

        ``with_terminal_atomic_series_release`` writes ``atomicSeriesActivation`` and
        ``atomicSeriesActivationRelease`` onto the SUCCESS path of a real series finalize, and
        ``finalize.py`` forwards both into the payload. ``LifecycleFinalizeTaskResponse`` declares
        neither and inherits ``extra="forbid"``, and ``tool_response.py`` runs ``model_validate``
        with no ``except`` -- so every atomic-series promotion validated a payload its own model
        rejected, AFTER branch retirement, task updates and enclosure cleanup had committed, and
        the caller was told a successful promotion had failed. Nothing validated that model at all,
        which is how the emitter and the model drifted; this case drives the emitter and pushes its
        exact output through ``complete_tool_response``, so they cannot drift again.
        """

        with tempfile.TemporaryDirectory() as tmp:
            contract = _series_contract(Path(tmp))
            emitted = with_terminal_atomic_series_release(
                contract,
                WorktreeCommandResult(
                    0,
                    {
                        # The keys ``worktrees/modules/finalize.py::_finalized_result`` emits for
                        # this edge; the emitter under test adds the two release keys to them.
                        "taskId": contract.task_id,
                        "taskName": contract.task_name,
                        "state": "finalized",
                        "dryRun": False,
                        "contractPath": contract.contract_path.as_posix(),
                        "enclosurePath": contract.contract_path.as_posix(),
                        "landedCommit": contract.code_base_commit,
                        "targetBranch": contract.code_source_branch,
                        "cleanup": {"state": "cleanup-completed"},
                        "taskUpdates": {},
                        "projectionEffects": [],
                        "taskArchive": {"state": "archived"},
                        "summary": "Task lifecycle finalized.",
                        # ``worktree_tools._worktree_result`` adds the terminal pair before the
                        # payload reaches the wire boundary, so the real payload carries them.
                        "ok": True,
                        "operation": "lifecycle_finalize_task",
                    },
                ),
                dry_run=False,
            )

            self.assertEqual(emitted.returncode, 0, emitted.payload)
            self.assertIn("atomicSeriesActivation", emitted.payload)
            self.assertIn("atomicSeriesActivationRelease", emitted.payload)

            finalized = complete_tool_response("lifecycle_finalize_task", dict(emitted.payload))

            self.assertEqual(finalized["operation"], "lifecycle_finalize_task")
            self.assertEqual(finalized["state"], "finalized")
            self.assertIn("atomicSeriesActivation", finalized)
            self.assertIn("atomicSeriesActivationRelease", finalized)


if __name__ == "__main__":
    unittest.main()
