"""Tests for the lifecycle next-step engine (task 27).

Covers the pure ``compute_next_step`` state machine (front-half prose pointer,
the decide -> worktree-intent hint, linear delegation to ``lifecycle_guidance``,
and the gate-raise overlay), plus the exception-contained edge ``next_step_for``
and the ``_tool_payload`` choke-point attachment + ``lifecycle_start`` rundown.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.tools.lifecycle import lifecycle_start_payload
from agents_remember.mcp.tools.next_step import (
    FRONT_HALF_RUNDOWN,
    _from_guidance,
    compute_next_step,
    next_step_for,
)
from agents_remember.observer.ambient import (
    AmbientLifecycle,
    install_ambient,
    reset_ambient,
)
from agents_remember.observer.lifecycle_state import LifecycleState
from agents_remember.observer.store import EventStore
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract

_GUIDANCE = {
    "phase": "worktree-started",
    "summary": "Worktrees are ready; continue the wrapped workflow and close out after review.",
    "nextOperation": "continue_work",
    "nextTool": "worktree_status",
    "nextArgs": {"contract_path": "/x"},
}


def _state(
    phase: str = "reframe-research",
    *,
    enclosure: str | None = None,
    state: str = "running",
) -> LifecycleState:
    return LifecycleState(
        id="01TESTLIFECYCLE",
        state=state,  # type: ignore[arg-type]
        phase=phase,  # type: ignore[arg-type]
        fleeting=enclosure is None,
        started_at="",
        enclosure=enclosure,
    )


def _contract(tmp: Path, **overrides: object) -> WorktreeContract:
    base: dict[str, object] = dict(
        task_id="T",
        task_name="t",
        repo_name="r",
        workflow_kind="light-task",
        memory_mode="internal",
        coordination_root=tmp,
        task_root=tmp,
        contract_path=tmp / "series-contract.md",
        task_artifact=tmp / "task.md",
        worktree_group=tmp / "wt",
        code_repo_path=tmp / "repo",
        code_source_branch="main",
        code_work_branch="feat",
        code_base_commit="abc123",
        code_worktree=tmp / "wt" / "code",
        leaf_id="t-leaf",
    )
    base.update(overrides)
    return WorktreeContract(**base)  # type: ignore[arg-type]


class PureEngineTests(unittest.TestCase):
    def test_no_hint_outside_a_lifecycle(self) -> None:
        self.assertIsNone(compute_next_step(None, None, "ping"))

    def test_no_hint_when_terminal(self) -> None:
        self.assertIsNone(
            compute_next_step(_state("close", state="completed"), None, "worktree_status")
        )

    def test_lifecycle_end_hints_the_loop_back(self) -> None:
        step = compute_next_step(None, None, "lifecycle_end")
        assert step is not None
        self.assertIn("lifecycle_start", step.summary)
        self.assertIn("worktree_attach", step.summary)
        # any other lifecycle-less call stays silent
        self.assertIsNone(compute_next_step(None, None, "ping"))

    def test_front_half_generic_points_back_to_the_rundown(self) -> None:
        step = compute_next_step(_state("reframe-research"), None, "read_ar_files")
        assert step is not None
        self.assertIn("rundown", step.summary)
        self.assertEqual(step.nextTool, "lifecycle_gate")
        self.assertEqual(step.nextArgs, {"kind": "plan-approval"})

    def test_decide_points_to_the_worktree_intent_gate(self) -> None:
        step = compute_next_step(_state("decide"), None, "worktree_start")
        assert step is not None
        self.assertEqual(step.nextArgs, {"kind": "worktree-intent"})

    def test_linear_delegates_to_guidance_when_no_gate_moment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _contract(Path(tmp))
            step = compute_next_step(
                _state("build", enclosure=str(contract.contract_path)),
                contract,
                "worktree_status",
                guidance=_GUIDANCE,
            )
        assert step is not None
        self.assertEqual(step.nextOperation, "continue_work")
        self.assertEqual(step.nextTool, "worktree_status")
        self.assertIsNone(step.nextArgs and step.nextArgs.get("kind"))

    def test_closeout_preview_hints_the_closeout_gate_until_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            unapproved = _contract(base, approved_for_commit=False)
            approved = _contract(base, approved_for_commit=True)
            st = _state("build", enclosure=str(unapproved.contract_path))

            pending = compute_next_step(st, unapproved, "worktree_closeout_preview")
            assert pending is not None
            self.assertEqual(pending.nextArgs, {"kind": "closeout-approval"})

            # Once approved, the preview is no longer a gate moment -> guidance.
            after = compute_next_step(st, approved, "worktree_closeout_preview", guidance=_GUIDANCE)
            assert after is not None
            self.assertEqual(after.nextOperation, "continue_work")

    def test_integrate_dry_run_hints_the_integration_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _contract(
                Path(tmp), closeout_status="completed", integration_status="not-started"
            )
            step = compute_next_step(
                _state("close", enclosure=str(contract.contract_path)),
                contract,
                "worktree_integrate",
            )
        assert step is not None
        self.assertEqual(step.nextArgs, {"kind": "integration-approval"})

    def test_finalize_dry_run_hints_the_cleanup_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _contract(Path(tmp), integration_status="completed", cleanup="pending")
            step = compute_next_step(
                _state("close", enclosure=str(contract.contract_path)),
                contract,
                "lifecycle_finalize_task",
            )
        assert step is not None
        self.assertEqual(step.nextArgs, {"kind": "cleanup-approval"})

    def test_from_guidance_maps_the_guidance_shape(self) -> None:
        step = _from_guidance(_GUIDANCE)
        self.assertEqual(step.summary, _GUIDANCE["summary"])
        self.assertEqual(step.nextOperation, "continue_work")
        self.assertEqual(step.nextTool, "worktree_status")
        self.assertEqual(step.nextArgs, {"contract_path": "/x"})


class EdgeAndChokePointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.amb = AmbientLifecycle(EventStore(self.root), heartbeat_seconds=3600)
        install_ambient(self.amb)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(reset_ambient)
        self.addCleanup(self.amb.shutdown)

    def test_next_step_for_returns_none_without_an_active_lifecycle(self) -> None:
        self.assertIsNone(next_step_for(self.amb, "ping"))

    def test_next_step_for_front_half(self) -> None:
        self.amb.start()
        step = next_step_for(self.amb, "read_ar_files")
        assert step is not None
        self.assertEqual(step["nextArgs"], {"kind": "plan-approval"})

    def test_missing_contract_degrades_to_front_half_hint(self) -> None:
        # A promoted lifecycle whose contract file is not on disk yet (the
        # worktree_start --dry-run window) must still hint — the front-half
        # fallback — never go silent, and never raise into the tool path.
        self.amb.start()
        self.amb.promote(enclosure=str(self.root / "missing.md"), repo_id="r", scope="r")
        step = next_step_for(self.amb, "worktree_status")
        assert step is not None
        self.assertEqual(step["nextArgs"], {"kind": "plan-approval"})

    def test_dry_run_window_in_decide_shows_worktree_intent(self) -> None:
        # The live worktree_start --dry-run case: promoted (enclosure set) +
        # contract not yet written + phase 'decide' -> the worktree-intent hint.
        self.amb.start()
        self.amb.phase("decide")
        self.amb.promote(enclosure=str(self.root / "missing.md"), repo_id="r", scope="r")
        step = next_step_for(self.amb, "worktree_start")
        assert step is not None
        self.assertEqual(step["nextArgs"], {"kind": "worktree-intent"})

    def test_corrupt_contract_degrades_gracefully(self) -> None:
        # A contract file that EXISTS but is unparseable (torn by a racing
        # closeout) degrades to the front-half hint, never raising.
        torn = self.root / "torn.md"
        torn.write_text("}{ not a contract", encoding="utf-8")
        self.amb.start()
        self.amb.promote(enclosure=str(torn), repo_id="r", scope="r")
        step = next_step_for(self.amb, "worktree_status")
        assert step is not None
        self.assertEqual(step["nextArgs"], {"kind": "plan-approval"})

    def test_next_step_for_linear_delegates_to_guidance(self) -> None:
        contract_path = self.root / "series-contract.md"
        contract = _contract(self.root, contract_path=contract_path)
        write_contract(contract_path, contract)
        self.amb.start()
        self.amb.promote(enclosure=str(contract_path), repo_id="r", scope="r")
        step = next_step_for(self.amb, "worktree_status")
        assert step is not None
        self.assertEqual(step["nextOperation"], "continue_work")

    def test_tool_payload_attaches_next_step_and_lifecycle_start_emits_rundown(self) -> None:
        payload = lifecycle_start_payload()
        self.assertEqual(payload["frontHalfRundown"], FRONT_HALF_RUNDOWN)
        self.assertIn("nextStep", payload)
        self.assertEqual(payload["nextStep"]["nextArgs"], {"kind": "plan-approval"})


if __name__ == "__main__":
    unittest.main()
