"""Altitude routing for the quality gate on integration (260731-EFA-L17-R2/R5)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.code_quality_gate import (
    GATE_FULL,
    GATE_TARGETED,
    QualityGatePlan,
    QualityGateTarget,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def integration_contract(root: Path, *, kind: str = "leaf") -> WorktreeContract:
    task_root = root / "tasks" / "agents-remember" / "master-task"
    return WorktreeContract(
        task_id="MASTER-L1",
        task_name="master-task",
        repo_name="agents-remember",
        workflow_kind="light-task",
        memory_mode="internal",
        coordination_root=root / "ar-coordination",
        task_root=task_root,
        contract_path=task_root / "enclosures" / "l1" / "series-contract.md",
        task_artifact=task_root / "task.md",
        worktree_group=root / "worktrees" / "agents-remember" / "l1-ar",
        code_repo_path=root / "repo",
        code_source_branch="ar/master",
        code_work_branch="ar/l1",
        code_base_commit="c0",
        code_worktree=root / "worktrees" / "agents-remember" / "l1-ar" / "l1",
        leaf_id="l1",
        kind=kind,
    )


class IntegrationQualityGateAltitudeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_leaf_integration_runs_the_targeted_contract(self) -> None:
        contract = integration_contract(self.root, kind="leaf")

        with (
            mock.patch.object(
                integrate_mod, "requires_strict_code_quality", return_value=True
            ) as requires,
            mock.patch.object(
                integrate_mod, "run_strict_code_quality_gate", return_value={"passed": True}
            ) as gate,
        ):
            result, blocked = integrate_mod._run_integration_quality_gate(contract)

        self.assertIsNone(blocked)
        self.assertEqual(result, {"passed": True})
        requires.assert_called_once_with(contract.code_worktree, code_would_commit=True)
        (target,), kwargs = gate.call_args
        self.assertEqual(
            target,
            QualityGateTarget(
                code_worktree=contract.code_worktree,
                worktree_group=contract.worktree_group,
            ),
        )
        plan = kwargs["plan"]
        assert isinstance(plan, QualityGatePlan)
        self.assertEqual(plan.mode, GATE_TARGETED)
        self.assertEqual(kwargs["invocation"], "leaf-integration")
        self.assertEqual(kwargs["diff_base"], "c0")

    def test_series_integration_runs_the_full_capped_gate(self) -> None:
        contract = integration_contract(self.root, kind="series")

        with (
            mock.patch.object(integrate_mod, "requires_strict_code_quality", return_value=True),
            mock.patch.object(integrate_mod, "_quality_gate_memory_cap", return_value=2147483648),
            mock.patch.object(
                integrate_mod, "run_strict_code_quality_gate", return_value={"passed": True}
            ) as gate,
        ):
            result, blocked = integrate_mod._run_integration_quality_gate(contract)

        self.assertIsNone(blocked)
        self.assertEqual(result, {"passed": True})
        (target,), kwargs = gate.call_args
        self.assertEqual(
            target,
            QualityGateTarget(
                code_worktree=contract.code_worktree,
                worktree_group=contract.worktree_group,
            ),
        )
        plan = kwargs["plan"]
        assert isinstance(plan, QualityGatePlan)
        self.assertEqual(plan.mode, GATE_FULL)
        self.assertEqual(plan.memory_cap_bytes, 2147483648)
        self.assertEqual(kwargs["invocation"], "master-integration")

    def test_altitude_routing_is_kind_based(self) -> None:
        leaf = integration_contract(self.root, kind="leaf")
        series = integration_contract(self.root, kind="series")

        self.assertEqual(integrate_mod.quality_gate_mode(leaf), GATE_TARGETED)
        self.assertEqual(integrate_mod.quality_gate_mode(series), GATE_FULL)

    def test_quality_gate_memory_cap_reads_the_settings_owned_value(self) -> None:
        contract = integration_contract(self.root, kind="series")
        settings = contract.coordination_root / "system" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {"orchestration": {"qualityGate": {"memoryCapBytes": 123456}}},
                indent=2,
            ),
            encoding="utf-8",
        )

        self.assertEqual(integrate_mod._quality_gate_memory_cap(contract), 123456)

    def test_quality_gate_memory_is_host_managed_when_the_cap_is_absent(self) -> None:
        contract = integration_contract(self.root, kind="series")

        self.assertIsNone(integrate_mod._quality_gate_memory_cap(contract))

    def test_a_refused_gate_blocks_integration_without_merging(self) -> None:
        contract = integration_contract(self.root, kind="leaf")

        def failing_gate(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("strict code-quality gate failed before code commit")

        with (
            mock.patch.object(integrate_mod, "requires_strict_code_quality", return_value=True),
            mock.patch.object(integrate_mod, "run_strict_code_quality_gate", failing_gate),
            mock.patch.object(integrate_mod, "write_contract"),
            mock.patch.object(
                integrate_mod,
                "_integrated_code_commit",
                return_value=("c1", None),
            ),
            mock.patch.object(integrate_mod, "_merge_integrated_commits") as merge,
        ):
            result = integrate_mod._apply_integration(
                contract,
                WorktreeArgs(strategy="ff-only"),
                integrate_mod.IntegrationSources(
                    current_code_source="c1",
                    current_memory_source="",
                    code_replay_required=False,
                    memory_replay_required=False,
                ),
                handover_warning=None,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked-quality-gate")
        merge.assert_not_called()

    def test_dry_run_reports_the_planned_gate_without_running_it(self) -> None:
        contract = integration_contract(self.root, kind="series")
        wrapper = contract.code_worktree / "mcp/src/agents_remember/code_quality/check.py"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("# marker\n", encoding="utf-8")

        with (
            mock.patch.object(integrate_mod, "load_contract", return_value=contract),
            mock.patch.object(integrate_mod, "validate_integrate_contract"),
            mock.patch.object(
                integrate_mod,
                "_integration_replay_requirements",
                return_value=integrate_mod.IntegrationSources(
                    current_code_source="c1",
                    current_memory_source="",
                    code_replay_required=False,
                    memory_replay_required=False,
                ),
            ),
            mock.patch.object(integrate_mod, "_quality_gate_memory_cap", return_value=999),
            mock.patch.object(integrate_mod, "run_strict_code_quality_gate") as gate,
            mock.patch.object(integrate_mod, "write_contract"),
        ):
            result = integrate_mod.integrate_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    strategy="ff-only",
                    dry_run=True,
                )
            )

        gate.assert_not_called()
        self.assertEqual(result.returncode, 0)
        quality_gate = result.payload["quality_gate"]
        assert isinstance(quality_gate, dict)
        self.assertEqual(quality_gate["mode"], GATE_FULL)
        memory_cap = quality_gate["memoryCap"]
        assert isinstance(memory_cap, dict)
        self.assertEqual(memory_cap["capBytes"], 999)
