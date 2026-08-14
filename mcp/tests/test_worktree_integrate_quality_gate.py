"""Altitude routing for the quality gate on integration (260731-EFA-L17-R2/R5)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import (
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.code_quality_gate import (
    GATE_FULL,
    GATE_TARGETED,
    QualityGatePlan,
    QualityGateTarget,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_worktree_support import open_external_contract_fixture


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


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


def external_recovery_contract(root: Path) -> WorktreeContract:
    return replace(
        integration_contract(root),
        memory_mode="external",
        memory_repo_path=root / "memory-repo",
        memory_source_branch="ar/master",
        memory_work_branch="ar/l1",
        memory_worktree=root / "worktrees/agents-remember/l1-ar/memory-l1",
        ledger_path=root / "worktrees/agents-remember/l1-ar/memory-l1/memory.md",
    )


def closed_external_contract_with_commits(
    root: Path,
) -> tuple[WorktreeContract, str, str, str]:
    contract = open_external_contract_fixture(root)
    assert contract.memory_worktree is not None
    assert contract.ledger_path is not None
    (contract.code_worktree / "leaf.txt").write_text("leaf\n", encoding="utf-8")
    git(contract.code_worktree, "add", "leaf.txt")
    git(contract.code_worktree, "commit", "-m", "leaf code")
    code_commit = git(contract.code_worktree, "rev-parse", "HEAD")
    (contract.memory_worktree / "leaf-memory.md").write_text("leaf memory\n", encoding="utf-8")
    git(contract.memory_worktree, "add", "leaf-memory.md")
    git(contract.memory_worktree, "commit", "-m", "leaf memory")
    memory_content = git(contract.memory_worktree, "rev-parse", "HEAD")
    ledger = load_ledger(contract.ledger_path)
    write_ledger(
        contract.ledger_path,
        prepend_mapping(ledger, code_commit, memory_content),
    )
    git(contract.memory_worktree, "add", "memory.md")
    git(contract.memory_worktree, "commit", "-m", "leaf ledger")
    ledger_commit = git(contract.memory_worktree, "rev-parse", "HEAD")
    closed = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_content,
        ledger_commit=ledger_commit,
    )
    write_contract(closed.contract_path, closed)
    return closed, code_commit, memory_content, ledger_commit


class IntegrationQualityGateAltitudeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_git_fixture_helper_surfaces_command_failures(self) -> None:
        with self.assertRaises(AssertionError):
            git(self.root, "definitely-not-a-git-command")

    def test_replay_contract_finalization_retry_proves_landed_git_and_ledger(self) -> None:
        closed, _code_commit, _memory_content, _ledger_commit = (
            closed_external_contract_with_commits(self.root)
        )
        assert closed.memory_repo_path is not None

        (closed.code_repo_path / "parallel.txt").write_text("parallel\n", encoding="utf-8")
        git(closed.code_repo_path, "add", "parallel.txt")
        git(closed.code_repo_path, "commit", "-m", "parallel code")
        (closed.memory_repo_path / "parallel-memory.md").write_text(
            "parallel memory\n", encoding="utf-8"
        )
        git(closed.memory_repo_path, "add", "parallel-memory.md")
        git(closed.memory_repo_path, "commit", "-m", "parallel memory")

        sources = integrate_mod._integration_replay_requirements(closed)
        self.assertTrue(sources.replay_required)
        captured: dict[str, str] = {}

        def progress(phase: str, evidence: Mapping[str, object]) -> None:
            if phase == "source-merge":
                value = evidence.get("recovery_commits")
                assert isinstance(value, dict)
                captured.update({str(key): str(item) for key, item in value.items()})

        first = WorktreeArgs(
            contract_path=closed.contract_path,
            strategy="replay",
            approved=True,
            ledger_commit_message="replay ledger",
            operation_key="b" * 64,
            operation_progress=progress,
        )
        with (
            mock.patch.object(
                integrate_mod,
                "_run_integration_quality_gate",
                return_value=({"passed": True}, None),
            ),
            mock.patch.object(integrate_mod, "_integration_source_state_block", return_value=None),
            mock.patch.object(
                integrate_mod,
                "write_contract",
                side_effect=RuntimeError("contract write interrupted"),
            ),
            self.assertRaisesRegex(RuntimeError, "contract write interrupted"),
        ):
            integrate_mod._apply_integration(
                closed,
                first,
                sources,
                handover_warning=None,
            )

        code_source_head = git(closed.code_repo_path, "rev-parse", closed.code_source_branch)
        memory_source_head = git(
            closed.memory_repo_path,
            "rev-parse",
            closed.memory_source_branch,
        )
        ledger_after_first = (closed.memory_repo_path / "memory.md").read_bytes()
        self.assertEqual(captured["codeCommit"], code_source_head)
        self.assertEqual(captured["ledgerCommit"], memory_source_head)
        self.assertEqual(load_contract(closed.contract_path).integration_status, "not-started")

        recovered = integrate_mod.integrate_result(
            replace(
                first,
                recovery_commits=LifecycleOperationRecoveryCommits.model_validate(captured),
                operation_progress=None,
            )
        )

        self.assertEqual(recovered.payload["state"], "integrated")
        self.assertTrue(recovered.payload["recovered"])
        self.assertEqual(
            git(closed.code_repo_path, "rev-parse", closed.code_source_branch),
            code_source_head,
        )
        self.assertEqual(
            git(closed.memory_repo_path, "rev-parse", closed.memory_source_branch),
            memory_source_head,
        )
        self.assertEqual((closed.memory_repo_path / "memory.md").read_bytes(), ledger_after_first)
        updated = load_contract(closed.contract_path)
        self.assertEqual(updated.integrated_code_commit, captured["codeCommit"])
        self.assertEqual(
            updated.integrated_memory_content_commit,
            captured["memoryContentCommit"],
        )
        self.assertEqual(updated.integrated_ledger_commit, captured["ledgerCommit"])

    def test_external_recovery_requires_a_wholly_landed_source_pair(self) -> None:
        contract = external_recovery_contract(self.root)
        commits = LifecycleOperationRecoveryCommits(
            codeCommit="a" * 40,
            memoryContentCommit="b" * 40,
            ledgerCommit="c" * 40,
        )
        with (
            mock.patch.object(integrate_mod, "head_commit", return_value=commits.codeCommit),
            self.assertRaisesRegex(RuntimeError, "requires a memory repo"),
        ):
            integrate_mod._recovery_sources_landed(
                replace(contract, memory_repo_path=None), commits
            )
        with mock.patch.object(integrate_mod, "head_commit", side_effect=["d" * 40, "e" * 40]):
            self.assertFalse(integrate_mod._recovery_sources_landed(contract, commits))
        for observed in ((commits.codeCommit, "e" * 40), ("d" * 40, commits.ledgerCommit)):
            with (
                self.subTest(observed=observed),
                mock.patch.object(integrate_mod, "head_commit", side_effect=observed),
                self.assertRaisesRegex(RuntimeError, "requires manual reconciliation"),
            ):
                integrate_mod._recovery_sources_landed(contract, commits)
        with mock.patch.object(
            integrate_mod,
            "head_commit",
            side_effect=[commits.codeCommit, commits.ledgerCommit],
        ):
            self.assertTrue(integrate_mod._recovery_sources_landed(contract, commits))

    def test_internal_recovery_refuses_external_commit_fields(self) -> None:
        contract = integration_contract(self.root)
        code_commit = "a" * 40
        internal = LifecycleOperationRecoveryCommits(codeCommit=code_commit)
        with mock.patch.object(integrate_mod, "head_commit", return_value="d" * 40):
            self.assertFalse(integrate_mod._recovery_sources_landed(contract, internal))
        with mock.patch.object(integrate_mod, "head_commit", return_value=code_commit):
            self.assertTrue(integrate_mod._recovery_sources_landed(contract, internal))
            with self.assertRaisesRegex(RuntimeError, "recorded external-memory commits"):
                integrate_mod._recovery_sources_landed(
                    contract,
                    LifecycleOperationRecoveryCommits(
                        codeCommit=code_commit,
                        memoryContentCommit="b" * 40,
                        ledgerCommit="c" * 40,
                    ),
                )

    def test_external_recovery_proves_task_head_mapping_and_ancestry(self) -> None:
        contract = external_recovery_contract(self.root)
        commits = LifecycleOperationRecoveryCommits(
            codeCommit="a" * 40,
            memoryContentCommit="b" * 40,
            ledgerCommit="c" * 40,
        )
        with (
            mock.patch.object(integrate_mod, "require_clean"),
            mock.patch.object(integrate_mod, "head_commit", return_value="d" * 40),
            self.assertRaisesRegex(RuntimeError, "found task memory HEAD"),
        ):
            integrate_mod._prove_external_memory_recovery(contract, commits)
        for mapping in (None, SimpleNamespace(memory_commit="e" * 40)):
            with (
                self.subTest(mapping=mapping),
                mock.patch.object(integrate_mod, "require_clean"),
                mock.patch.object(integrate_mod, "head_commit", return_value=commits.ledgerCommit),
                mock.patch.object(integrate_mod, "load_ledger"),
                mock.patch.object(integrate_mod, "find_mapping", return_value=mapping),
                self.assertRaisesRegex(RuntimeError, "landed ledger mapping"),
            ):
                integrate_mod._prove_external_memory_recovery(contract, commits)
        with (
            mock.patch.object(integrate_mod, "require_clean"),
            mock.patch.object(integrate_mod, "head_commit", return_value=commits.ledgerCommit),
            mock.patch.object(integrate_mod, "load_ledger"),
            mock.patch.object(
                integrate_mod,
                "find_mapping",
                return_value=SimpleNamespace(memory_commit=commits.memoryContentCommit),
            ),
            mock.patch.object(integrate_mod, "is_ancestor", return_value=False),
            self.assertRaisesRegex(RuntimeError, "not reachable"),
        ):
            integrate_mod._prove_external_memory_recovery(contract, commits)

    def test_integration_recovery_proof_permits_untouched_retry_and_exact_head(self) -> None:
        contract = integration_contract(self.root)
        commits = LifecycleOperationRecoveryCommits(codeCommit="a" * 40)
        with mock.patch.object(integrate_mod, "_recovery_sources_landed", return_value=False):
            self.assertIsNone(integrate_mod._prove_integration_recovery_commits(contract, commits))
        with (
            mock.patch.object(integrate_mod, "_recovery_sources_landed", return_value=True),
            mock.patch.object(integrate_mod, "require_clean"),
            mock.patch.object(integrate_mod, "head_commit", return_value="d" * 40),
            self.assertRaisesRegex(RuntimeError, "found task HEAD"),
        ):
            integrate_mod._prove_integration_recovery_commits(contract, commits)
        with (
            mock.patch.object(integrate_mod, "_recovery_sources_landed", return_value=True),
            mock.patch.object(integrate_mod, "require_clean"),
            mock.patch.object(integrate_mod, "head_commit", return_value=commits.codeCommit),
        ):
            proven = integrate_mod._prove_integration_recovery_commits(contract, commits)
        assert proven is not None
        self.assertEqual(proven.code, commits.codeCommit)

    def test_completed_integration_recovery_must_match_exactly(self) -> None:
        contract = replace(
            integration_contract(self.root),
            integration_status="completed",
            integrated_code_commit="a" * 40,
        )
        commits = LifecycleOperationRecoveryCommits(codeCommit="a" * 40)
        args = WorktreeArgs(recovery_commits=commits)
        with (
            mock.patch.object(
                integrate_mod,
                "_prove_integration_recovery_commits",
                return_value=integrate_mod.IntegratedCommits("a" * 40, "", ""),
            ),
            mock.patch.object(integrate_mod, "status_payload", return_value={}),
        ):
            recovered = integrate_mod._recover_integration_finalization(contract, args)
        assert recovered is not None
        self.assertEqual(recovered.payload["state"], "already-integrated")
        with (
            mock.patch.object(
                integrate_mod,
                "_prove_integration_recovery_commits",
                return_value=integrate_mod.IntegratedCommits("a" * 40, "", ""),
            ),
            self.assertRaisesRegex(RuntimeError, "does not match"),
        ):
            integrate_mod._recover_integration_finalization(
                replace(contract, integrated_code_commit="d" * 40), args
            )
        with mock.patch.object(
            integrate_mod, "_prove_integration_recovery_commits", return_value=None
        ):
            self.assertIsNone(integrate_mod._recover_integration_finalization(contract, args))
        self.assertIsNone(integrate_mod._recover_integration_finalization(contract, WorktreeArgs()))

    def test_integrate_result_reports_an_already_completed_contract(self) -> None:
        contract = replace(integration_contract(self.root), integration_status="completed")
        with (
            mock.patch.object(integrate_mod, "load_contract", return_value=contract),
            mock.patch.object(
                integrate_mod, "_recover_integration_finalization", return_value=None
            ),
            mock.patch.object(integrate_mod, "status_payload", return_value={}),
        ):
            result = integrate_mod.integrate_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.payload["state"], "already-integrated")

    def test_integration_refusal_carries_one_explicit_recovery_command(self) -> None:
        contract = integration_contract(self.root, kind="leaf")
        recovery = {
            "nextOperation": "sync_source_lineage",
            "nextTool": "worktree_sync",
            "nextArgs": {"contract_path": contract.contract_path.as_posix()},
        }
        with (
            mock.patch.object(integrate_mod, "amend_contract", return_value=contract),
            mock.patch.object(integrate_mod, "write_contract"),
            mock.patch.object(integrate_mod, "status_payload", return_value={}),
        ):
            payload = integrate_mod.blocked_integration_payload(
                contract,
                "source-lineage-stale",
                "Sync before retrying integration.",
                **recovery,
            )

        self.assertEqual(
            payload["nextStep"],
            {"summary": "Sync before retrying integration.", **recovery},
        )

    def test_leaf_integration_reuses_closeout_acceptance_without_running_a_gate(self) -> None:
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
        self.assertFalse(result["required"])
        self.assertEqual(result["status"], "certified-at-leaf-closeout")
        self.assertEqual(result["mode"], GATE_TARGETED)
        requires.assert_not_called()
        gate.assert_not_called()

    def test_agents_remember_master_integration_refuses_a_missing_self_owned_wrapper(
        self,
    ) -> None:
        contract = integration_contract(self.root, kind="series")

        with (
            mock.patch.object(
                integrate_mod,
                "_integrated_code_commit",
                return_value=("c1", None),
            ),
            mock.patch.object(integrate_mod, "write_contract"),
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
        self.assertIn("self-owned wrapper", str(result.payload["reason"]))
        merge.assert_not_called()

    def test_consumer_master_without_a_wrapper_reports_unavailable_without_blocking(
        self,
    ) -> None:
        contract = replace(
            integration_contract(self.root, kind="series"),
            repo_name="consumer-repo",
        )

        result, blocked = integrate_mod._run_integration_quality_gate(contract)

        self.assertIsNone(blocked)
        self.assertFalse(result["required"])
        self.assertEqual(result["status"], "wrapper-unavailable")

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

        with self.assertRaisesRegex(ValueError, "reuses the exact leaf-closeout acceptance"):
            integrate_mod.quality_gate_mode(leaf)
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

    def test_a_refused_master_gate_blocks_integration_without_merging(self) -> None:
        contract = integration_contract(self.root, kind="series")

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

    def test_replayed_candidate_is_restored_when_quality_refuses_and_retry_stays_eligible(
        self,
    ) -> None:
        repo = self.root / "repo"
        repo.mkdir()
        git(repo, "init", "-b", "ar/master")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Test")
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        code_worktree = self.root / "worktrees/agents-remember/l1-ar/l1"
        code_worktree.parent.mkdir(parents=True)
        git(repo, "worktree", "add", "-b", "ar/l1", code_worktree.as_posix(), "ar/master")
        (code_worktree / "leaf.txt").write_text("leaf\n", encoding="utf-8")
        git(code_worktree, "add", "-A")
        git(code_worktree, "commit", "-m", "leaf")
        leaf_commit = git(code_worktree, "rev-parse", "HEAD")
        (repo / "source.txt").write_text("source\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "source moved")
        source_commit = git(repo, "rev-parse", "HEAD")
        contract = replace(
            integration_contract(self.root, kind="leaf"),
            code_repo_path=repo,
            code_worktree=code_worktree,
            code_base_commit=base,
            code_commit=leaf_commit,
        )
        sources = integrate_mod.IntegrationSources(
            current_code_source=source_commit,
            current_memory_source="",
            code_replay_required=True,
            memory_replay_required=False,
        )

        with mock.patch.object(
            integrate_mod,
            "_run_integration_quality_gate",
            return_value=({}, {"state": "blocked-quality-gate"}),
        ):
            first = integrate_mod._apply_integration(
                contract,
                WorktreeArgs(strategy="replay"),
                sources,
                handover_warning=None,
            )
            self.assertEqual(first.payload["state"], "blocked-quality-gate")
            self.assertEqual(git(code_worktree, "rev-parse", "HEAD"), leaf_commit)
            self.assertEqual(git(code_worktree, "status", "--porcelain"), "")

            second = integrate_mod._apply_integration(
                contract,
                WorktreeArgs(strategy="replay"),
                sources,
                handover_warning=None,
            )

        self.assertEqual(second.payload["state"], "blocked-quality-gate")
        self.assertEqual(git(code_worktree, "rev-parse", "HEAD"), leaf_commit)
        self.assertEqual(git(code_worktree, "branch", "--show-current"), "ar/l1")

    def test_premerge_blockers_return_without_crossing_the_source_boundary(self) -> None:
        contract = integration_contract(self.root, kind="leaf")
        sources = integrate_mod.IntegrationSources("c0", "", False, False)
        code_block = {"state": "blocked-code-replay"}
        with (
            mock.patch.object(
                integrate_mod, "_integrated_code_commit", return_value=("", code_block)
            ),
            mock.patch.object(integrate_mod, "_merge_integrated_commits") as merge,
        ):
            result = integrate_mod._apply_integration(
                contract, WorktreeArgs(strategy="ff-only"), sources, handover_warning=None
            )
        self.assertEqual(result.payload, code_block)
        merge.assert_not_called()

        memory_block = {"state": "blocked-memory-replay"}
        with (
            mock.patch.object(integrate_mod, "_integrated_code_commit", return_value=("c1", None)),
            mock.patch.object(
                integrate_mod,
                "_run_integration_quality_gate",
                return_value=({"passed": True}, None),
            ),
            mock.patch.object(integrate_mod, "_integration_source_state_block", return_value=None),
            mock.patch.object(
                integrate_mod,
                "_integrated_memory_commits",
                return_value=("", "", memory_block),
            ),
            mock.patch.object(integrate_mod, "_merge_integrated_commits") as merge,
        ):
            result = integrate_mod._apply_integration(
                contract, WorktreeArgs(strategy="ff-only"), sources, handover_warning=None
            )
        self.assertEqual(result.payload, memory_block)
        merge.assert_not_called()

    def test_replay_restore_is_noop_at_original_head_and_fails_closed_on_reset_error(self) -> None:
        contract = integration_contract(self.root, kind="leaf")
        with (
            mock.patch.object(integrate_mod, "head_commit", return_value="original"),
            mock.patch.object(integrate_mod, "run_git") as run_git,
        ):
            integrate_mod._restore_replayed_code_worktree(contract, "original")
        run_git.assert_not_called()

        passed = SimpleNamespace(returncode=0, stdout="", stderr="")
        failed = SimpleNamespace(returncode=1, stdout="", stderr="reset red")
        with (
            mock.patch.object(integrate_mod, "head_commit", return_value="replayed"),
            mock.patch.object(integrate_mod, "run_git", side_effect=[passed, failed]),
            self.assertRaisesRegex(RuntimeError, "could not restore.*reset red"),
        ):
            integrate_mod._restore_replayed_code_worktree(contract, "original")

    def test_source_movement_after_quality_refuses_before_memory_or_merge(self) -> None:
        contract = integration_contract(self.root, kind="leaf")
        moved = integrate_mod.WorktreeCommandResult(2, {"state": "source-moved-during-quality"})

        with (
            mock.patch.object(integrate_mod, "_integrated_code_commit", return_value=("c1", None)),
            mock.patch.object(
                integrate_mod,
                "_run_integration_quality_gate",
                return_value=({"passed": True}, None),
            ),
            mock.patch.object(integrate_mod, "_integration_lineage_block", return_value=None),
            mock.patch.object(
                integrate_mod, "_integration_sources_moved_block", return_value=moved
            ) as source_check,
            mock.patch.object(integrate_mod, "_integrated_memory_commits") as memory,
            mock.patch.object(integrate_mod, "_merge_integrated_commits") as merge,
        ):
            result = integrate_mod._apply_integration(
                contract,
                WorktreeArgs(strategy="ff-only"),
                integrate_mod.IntegrationSources(
                    current_code_source="c0",
                    current_memory_source="",
                    code_replay_required=False,
                    memory_replay_required=False,
                ),
                handover_warning=None,
            )

        self.assertEqual(result.payload["state"], "source-moved-during-quality")
        source_check.assert_called_once()
        memory.assert_not_called()
        merge.assert_not_called()

    def test_source_movement_after_memory_resolution_refuses_before_merge(self) -> None:
        contract = integration_contract(self.root, kind="leaf")
        moved = integrate_mod.WorktreeCommandResult(2, {"state": "source-moved-during-quality"})

        with (
            mock.patch.object(integrate_mod, "_integrated_code_commit", return_value=("c1", None)),
            mock.patch.object(
                integrate_mod,
                "_run_integration_quality_gate",
                return_value=({"passed": True}, None),
            ),
            mock.patch.object(
                integrate_mod, "_integration_source_state_block", side_effect=[None, moved]
            ) as source_check,
            mock.patch.object(
                integrate_mod,
                "_integrated_memory_commits",
                return_value=("", "", None),
            ),
            mock.patch.object(integrate_mod, "_merge_integrated_commits") as merge,
        ):
            result = integrate_mod._apply_integration(
                contract,
                WorktreeArgs(strategy="ff-only"),
                integrate_mod.IntegrationSources(
                    current_code_source="c0",
                    current_memory_source="",
                    code_replay_required=False,
                    memory_replay_required=False,
                ),
                handover_warning=None,
            )

        self.assertEqual(result.payload["state"], "source-moved-during-quality")
        self.assertEqual(source_check.call_count, 2)
        merge.assert_not_called()

    def test_memory_replay_rolls_back_exactly_when_source_moves_after_replay(self) -> None:
        contract = open_external_contract_fixture(self.root)
        assert contract.memory_worktree is not None
        assert contract.memory_repo_path is not None
        assert contract.ledger_path is not None
        memory_worktree = contract.memory_worktree
        memory_repo = contract.memory_repo_path
        ledger_path = contract.ledger_path
        (memory_worktree / "onboarding.md").write_text("candidate\n", encoding="utf-8")
        git(memory_worktree, "add", "-A")
        git(memory_worktree, "commit", "-m", "memory candidate")
        memory_commit = git(memory_worktree, "rev-parse", "HEAD")
        original_ledger = ledger_path.read_bytes()
        contract = replace(
            contract,
            code_commit="code-before-replay",
            memory_content_commit=memory_commit,
            ledger_commit=memory_commit,
        )
        sources = integrate_mod.IntegrationSources(
            current_code_source="code-source",
            current_memory_source=git(memory_repo, "rev-parse", "main"),
            code_replay_required=False,
            memory_replay_required=True,
        )
        moved = integrate_mod.WorktreeCommandResult(2, {"state": "source-moved-during-quality"})

        for attempt in range(2):
            with (
                self.subTest(attempt=attempt),
                mock.patch.object(
                    integrate_mod, "_integrated_code_commit", return_value=("code-replayed", None)
                ),
                mock.patch.object(
                    integrate_mod,
                    "_run_integration_quality_gate",
                    return_value=({"passed": True}, None),
                ),
                mock.patch.object(
                    integrate_mod, "_integration_source_state_block", side_effect=[None, moved]
                ),
            ):
                result = integrate_mod._apply_integration(
                    contract,
                    WorktreeArgs(strategy="replay", ledger_commit_message="replay ledger"),
                    sources,
                    handover_warning=None,
                )

            self.assertEqual(result.payload["state"], "source-moved-during-quality")
            self.assertEqual(
                git(memory_worktree, "branch", "--show-current"),
                contract.memory_work_branch,
            )
            self.assertEqual(git(memory_worktree, "rev-parse", "HEAD"), memory_commit)
            self.assertEqual(ledger_path.read_bytes(), original_ledger)
            self.assertNotIn(
                integrate_mod.integration_branch(contract),
                git(memory_repo, "branch", "--format=%(refname:short)").splitlines(),
            )

    def test_code_only_replay_that_rewrites_ledger_is_rolled_back_on_late_refusal(self) -> None:
        contract = open_external_contract_fixture(self.root)
        assert contract.memory_worktree is not None
        assert contract.memory_repo_path is not None
        assert contract.ledger_path is not None
        memory_worktree = contract.memory_worktree
        memory_repo = contract.memory_repo_path
        ledger_path = contract.ledger_path
        (memory_worktree / "onboarding.md").write_text("candidate\n", encoding="utf-8")
        git(memory_worktree, "add", "-A")
        git(memory_worktree, "commit", "-m", "memory candidate")
        memory_commit = git(memory_worktree, "rev-parse", "HEAD")
        original_ledger = ledger_path.read_bytes()
        contract = replace(
            contract,
            code_commit="code-before-replay",
            memory_content_commit=memory_commit,
            ledger_commit=memory_commit,
        )
        sources = integrate_mod.IntegrationSources(
            current_code_source="code-source",
            current_memory_source=git(memory_repo, "rev-parse", "main"),
            code_replay_required=True,
            memory_replay_required=False,
        )
        moved = integrate_mod.WorktreeCommandResult(2, {"state": "source-moved-during-quality"})

        with (
            mock.patch.object(
                integrate_mod, "_integrated_code_commit", return_value=("code-replayed", None)
            ),
            mock.patch.object(
                integrate_mod,
                "_run_integration_quality_gate",
                return_value=({"passed": True}, None),
            ),
            mock.patch.object(
                integrate_mod, "_integration_source_state_block", side_effect=[None, moved]
            ),
        ):
            result = integrate_mod._apply_integration(
                contract,
                WorktreeArgs(strategy="replay", ledger_commit_message="replay ledger"),
                sources,
                handover_warning=None,
            )

        self.assertEqual(result.payload["state"], "source-moved-during-quality")
        self.assertEqual(
            git(memory_worktree, "branch", "--show-current"), contract.memory_work_branch
        )
        self.assertEqual(git(memory_worktree, "rev-parse", "HEAD"), memory_commit)
        self.assertEqual(ledger_path.read_bytes(), original_ledger)
        self.assertNotIn(
            integrate_mod.integration_branch(contract),
            git(memory_repo, "branch", "--format=%(refname:short)").splitlines(),
        )

    def test_source_tip_comparison_distinguishes_unchanged_and_moved(self) -> None:
        contract = integration_contract(self.root, kind="leaf")
        sources = integrate_mod.IntegrationSources(
            current_code_source="c0",
            current_memory_source="",
            code_replay_required=False,
            memory_replay_required=False,
        )
        with mock.patch.object(integrate_mod, "head_commit", return_value="c0"):
            self.assertIsNone(integrate_mod._integration_sources_moved_block(contract, sources))
        with (
            mock.patch.object(integrate_mod, "head_commit", return_value="c1"),
            mock.patch.object(integrate_mod, "write_contract"),
        ):
            result = integrate_mod._integration_sources_moved_block(contract, sources)
        assert result is not None
        self.assertEqual(result.payload["state"], "source-moved-during-quality")


class MemoryReplayBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.contract = replace(
            integration_contract(root),
            memory_mode="external",
            memory_repo_path=root / "memory-repo",
            memory_source_branch="ar/master",
            memory_work_branch="ar/leaf",
            memory_base_commit="m0",
            memory_worktree=root / "memory-worktree",
            memory_content_commit="m1",
            ledger_commit="m2",
            ledger_path=root / "memory-worktree" / "memory.md",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_existing_scratch_branch_refuses_before_checkout(self) -> None:
        with (
            mock.patch.object(integrate_mod, "branch_exists", return_value=True),
            mock.patch.object(
                integrate_mod,
                "blocked_integration_payload",
                side_effect=lambda _contract, state, _reason, **_extra: {"state": state},
            ),
            mock.patch.object(integrate_mod, "run_git") as run_git,
        ):
            _content, _ledger, blocked = integrate_mod.replay_memory_content(
                self.contract, "c1", "ledger"
            )

        assert blocked is not None
        self.assertEqual(blocked["state"], "blocked-existing-integration-branch")
        run_git.assert_not_called()

    def test_restore_memory_replay_fails_closed_for_each_git_boundary(self) -> None:
        original = integrate_mod._MemoryReplayState(
            branch="ar/leaf",
            head="m1",
            scratch_branch="ar/master-integration",
            scratch_existed=False,
        )
        passed = SimpleNamespace(returncode=0, stdout="", stderr="")
        failed_checkout = SimpleNamespace(returncode=1, stdout="", stderr="checkout red")
        failed_reset = SimpleNamespace(returncode=1, stdout="", stderr="reset red")
        failed_delete = SimpleNamespace(returncode=1, stdout="", stderr="delete red")

        cases = (
            (
                "checkout",
                "ar/master-integration",
                [passed, failed_checkout],
                "restore.*checkout red",
            ),
            ("reset", "ar/leaf", [passed, failed_reset], "restore.*reset red"),
            (
                "delete",
                "ar/leaf",
                [passed, passed, failed_delete],
                "remove.*delete red",
            ),
        )
        for name, branch, git_results, message in cases:
            with (
                self.subTest(name=name),
                mock.patch.object(integrate_mod, "current_branch", return_value=branch),
                mock.patch.object(integrate_mod, "run_git", side_effect=git_results),
                mock.patch.object(
                    integrate_mod,
                    "branch_exists",
                    return_value=name == "delete",
                ),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                integrate_mod._restore_replayed_memory_worktree(self.contract, original)

        retained_scratch = replace(original, scratch_existed=True)
        with (
            mock.patch.object(integrate_mod, "current_branch", return_value="ar/leaf"),
            mock.patch.object(integrate_mod, "run_git", side_effect=[passed, passed]) as run_git,
        ):
            integrate_mod._restore_replayed_memory_worktree(self.contract, retained_scratch)
        self.assertEqual(run_git.call_count, 2)

    def test_checkout_and_rebase_failures_are_bounded(self) -> None:
        failed = SimpleNamespace(returncode=1, stdout="out", stderr="err")
        passed = SimpleNamespace(returncode=0, stdout="", stderr="")
        for name, results, state in (
            ("checkout", [failed], "blocked-memory-replay"),
            ("rebase", [passed, failed], "blocked-memory-conflict"),
        ):
            with (
                self.subTest(name=name),
                mock.patch.object(integrate_mod, "branch_exists", return_value=False),
                mock.patch.object(integrate_mod, "run_git", side_effect=results),
                mock.patch.object(
                    integrate_mod,
                    "blocked_integration_payload",
                    side_effect=lambda _contract, value, _reason, **_extra: {"state": value},
                ),
            ):
                _content, _ledger, blocked = integrate_mod.replay_memory_content(
                    self.contract, "c1", "ledger"
                )
                assert blocked is not None
                self.assertEqual(blocked["state"], state)

    def test_success_rewrites_the_mapping_and_commits_the_ledger(self) -> None:
        passed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(integrate_mod, "branch_exists", return_value=False),
            mock.patch.object(integrate_mod, "run_git", side_effect=[passed, passed]),
            mock.patch.object(integrate_mod, "head_commit", return_value="m3"),
            mock.patch.object(integrate_mod, "load_ledger", return_value="old") as load,
            mock.patch.object(integrate_mod, "prepend_mapping", return_value="new") as prepend,
            mock.patch.object(integrate_mod, "write_ledger") as write,
            mock.patch.object(integrate_mod, "require_git") as require_git,
            mock.patch.object(integrate_mod, "commit_if_dirty", return_value="m4"),
        ):
            content, ledger, blocked = integrate_mod.replay_memory_content(
                self.contract, "c1", "ledger"
            )

        self.assertIsNone(blocked)
        self.assertEqual((content, ledger), ("m3", "m4"))
        load.assert_called_once_with(self.contract.ledger_path)
        prepend.assert_called_once_with("old", "c1", "m3")
        write.assert_called_once_with(self.contract.ledger_path, "new")
        require_git.assert_called_once_with(self.contract.memory_worktree, ["add", "memory.md"])

    def test_dry_run_reports_the_planned_gate_without_running_it(self) -> None:
        contract = integration_contract(self.root, kind="series")
        wrapper = contract.code_worktree / "mcp/src/agents_remember/code_quality/check.py"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("# marker\n", encoding="utf-8")

        with (
            mock.patch.object(integrate_mod, "load_contract", return_value=contract),
            mock.patch.object(integrate_mod, "validate_integrate_contract"),
            mock.patch.object(integrate_mod, "_integration_lineage_block", return_value=None),
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
