"""Wave 2 (L16-R6/R7/R8/R9): branch-addressed direct execution and error dialect.

Covers the policy-gated series-contract binding for ``record_route_review``
(R6), the lock-serialized ``direct_landing`` operation with its pre-commit staged-
candidate gate (R7/R8), and the contract-bound refusal dialect (R9). Uses real
scratch git repos and a synthetic coordination root -- never the live tree.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.kernel.memory_ledger import LedgerError, create_initial_ledger, write_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.lifecycles.door import CloseoutDoorRequest
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError, resolve_terminal_leaf_doc
from agents_remember.worktrees.direct_landing import (
    DirectLandingError,
    DirectLandingRequest,
    direct_landing as _production_direct_landing,
)
from agents_remember.worktrees.integration.closeout_door_source import door_task_context
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    head_commit,
    require_git,
)
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
    load_contract,
    write_contract,
)
from closeout_input_test_support import _ensure_fixture_waiting_door
from test_worktree_support import git, init_repo


def direct_landing(*args, **kwargs):
    """Exercise direct landing below the independently covered scheduling fence."""

    with mock.patch(
        "agents_remember.worktrees.direct_landing.require_first_ready_generation"
    ):
        return _production_direct_landing(*args, **kwargs)


def _without_projection_effects(result: dict) -> dict:
    """Compare durable operation output without per-call projection telemetry."""

    durable = dict(result)
    durable.pop("projectionEffects", None)
    return durable


def _scratch_config(
    root: Path,
    code: Path,
    memory: Path | None,
    *,
    direct_execution_enabled: bool = True,
) -> McpRuntimeConfig:
    configured_code = root / "repo-a"
    if not configured_code.exists():
        configured_code.symlink_to(code, target_is_directory=True)
    if memory is not None:
        configured_memory = root / "coord" / "memory-repos" / "ar-repo-a"
        configured_memory.parent.mkdir(parents=True, exist_ok=True)
        if not configured_memory.exists():
            configured_memory.symlink_to(memory, target_is_directory=True)
    config_path = root / (
        "settings.json" if direct_execution_enabled else "settings-direct-disabled.json"
    )
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coord").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo-a": {}},
                "directExecutionEnabled": direct_execution_enabled,
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _series_fixture(root: Path, *, code_commit_message: str = "code commit") -> dict:
    """A task-root series contract over a real code + memory repo pair."""
    coord = root / "coord"
    tasks = coord / "tasks" / "repo-a" / "direct-task"
    tasks.mkdir(parents=True)
    code = root / "code"
    memory = coord / "memory-repos" / "ar-repo-a"
    code_base = init_repo(code, "main")
    git(code, "checkout", "-b", "ar/direct-task", "main")
    (code / "feature.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-m", code_commit_message)
    code_head = git(code, "rev-parse", "HEAD")
    git(code, "checkout", "main")
    git(code, "branch", "super", "main")

    init_repo(memory, "main")
    git(memory, "checkout", "-b", "ar/direct-task", "main")
    write_ledger(
        memory / "memory.md",
        create_initial_ledger("repo-a", code_base, head_commit(memory)),
    )
    git(memory, "add", "memory.md")
    git(memory, "commit", "-m", "seed ledger")
    memory_base = head_commit(memory)

    task = ContractTask(
        name="direct-task",
        repo_name="repo-a",
        coordination_root=coord,
        workflow_kind="light-task",
        memory_mode="external",
    )
    contract = default_series_contract(
        task,
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/direct-task",
            base_commit=code_base,
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="main",
            work_branch="ar/direct-task",
            base_commit=memory_base,
        ),
    )
    write_contract(contract.contract_path, contract)
    contract, _fixture_bypass = _ensure_fixture_waiting_door(contract)
    publish_new_lifecycle_operation_location(
        contract,
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    return {
        "config": _scratch_config(root, code, memory),
        "contract": contract,
        "code": code,
        "memory": memory,
        "code_head": code_head,
        "candidate_tree": require_git(code, ["rev-parse", f"{code_head}^{{tree}}"]),
        "tasks": tasks,
    }


def _byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class DirectLandingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_direct_landing_is_policy_gated(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = _scratch_config(
            root / "fx",
            fixture["code"],
            fixture["memory"],
            direct_execution_enabled=False,
        )
        with self.assertRaisesRegex(DirectLandingError, "disabled by policy"):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=fixture["contract"].contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                fixture["contract"],
            )

    def test_direct_landing_refuses_leaf_contracts(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        leaf = replace(
            fixture["contract"],
            kind="leaf",
            contract_path=Path(fixture["contract"].contract_path.as_posix() + ".leaf"),
        )
        with self.assertRaisesRegex(DirectLandingError, "series contract"):
            direct_landing(
                fixture["config"],
                DirectLandingRequest(
                    contract_path=fixture["contract"].contract_path.as_posix() + ".leaf",
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                leaf,
            )

    def test_direct_landing_verifies_code_commit_then_ledger(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]

        # A mismatch between the requested commit and the series branch HEAD refuses.
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.require_git",
                side_effect=AssertionError("foreign commit must not be dereferenced"),
            ) as tree_read,
            self.assertRaisesRegex(DirectLandingError, "not the current series branch HEAD"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit="0" * 40,
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                contract,
            )
        tree_read.assert_not_called()

        # Preview reports the would-land facts without mutating.
        before_preview = _byte_tree(root)
        preview = direct_landing(
            config,
            DirectLandingRequest(
                contract_path=contract.contract_path.as_posix(),
                code_commit=fixture["code_head"],
                candidate_tree=fixture["candidate_tree"],
                memory_commit_message="direct memory content",
                ledger_commit_message="direct ledger mapping",
                intent_note="approve",
                dry_run=True,
            ),
            contract,
        )
        self.assertEqual(preview["state"], "would-land")
        self.assertEqual(preview["codeCommit"], fixture["code_head"])
        self.assertEqual(_byte_tree(root), before_preview)
        before = git(memory, "rev-parse", "HEAD")

        # Add dirty memory content, then land: code verified + memory + ledger row.
        (memory / "onboarding").mkdir(exist_ok=True)
        (memory / "onboarding" / "feature.py.md").write_text("# feature\n", encoding="utf-8")
        landed = direct_landing(
            config,
            DirectLandingRequest(
                contract_path=contract.contract_path.as_posix(),
                code_commit=fixture["code_head"],
                candidate_tree=fixture["candidate_tree"],
                memory_commit_message="direct memory",
                ledger_commit_message="direct ledger",
                intent_note="approved by owner",
            ),
            contract,
        )
        self.assertEqual(landed["state"], "landed")
        self.assertEqual(landed["codeCommit"], fixture["code_head"])
        self.assertTrue(landed["memoryContentCommit"])
        self.assertTrue(landed["ledgerCommit"])
        after = git(memory, "rev-parse", "HEAD")
        self.assertNotEqual(before, after)
        self.assertEqual(git(memory, "show", "-s", "--format=%s", after), "direct ledger")
        self.assertEqual(
            git(memory, "show", "-s", "--format=%s", str(landed["memoryContentCommit"])),
            "direct memory",
        )
        ledger_text = git(memory, "show", f"{after}:memory.md")
        self.assertIn(fixture["code_head"], ledger_text)
        self.assertIn(landed["memoryContentCommit"], ledger_text)

    def test_direct_landing_precommit_gate_refuses_moved_candidate(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        gated_tree = require_git(code, ["rev-parse", f"{fixture['code_head']}^{{tree}}"])
        # Move the series branch after the gate.
        git(code, "checkout", "ar/direct-task")
        (code / "feature.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        git(code, "add", "-A")
        git(code, "commit", "-m", "post-gate change")
        with self.assertRaisesRegex(DirectLandingError, "moved after the staged candidate"):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=branch_commit(code, "ar/direct-task"),
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    candidate_tree=gated_tree,
                    intent_note="approve",
                ),
                contract,
            )

    def test_direct_landing_requires_intent_note(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        with self.assertRaisesRegex(DirectLandingError, "intent note"):
            direct_landing(
                fixture["config"],
                DirectLandingRequest(
                    contract_path=fixture["contract"].contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                ),
                fixture["contract"],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class DirectLandingBranchTests(unittest.TestCase):
    """Branch coverage for ``worktrees/direct_landing.py`` (gate round 2 rail 3)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_refuses_a_leaf_contract(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        coord = root / "fx" / "coord"
        code = fixture["code"]
        memory = fixture["memory"]
        task = ContractTask(
            name="leaf-task",
            repo_name="repo-a",
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        )
        leaf = default_contract(
            task,
            leaf=LeafIdentity(worktree_name="leaf-1", leaf_id="L1", lifecycle_id="LC"),
            code=RepoBranchPlan(
                repo_path=code,
                source_branch="super",
                work_branch="leaf-1",
                base_commit=fixture["code_head"],
            ),
            memory=RepoBranchPlan(
                repo_path=memory,
                source_branch="main",
                work_branch="leaf-1",
                base_commit=head_commit(memory),
            ),
        )
        write_contract(leaf.contract_path, leaf)
        with self.assertRaisesRegex(DirectLandingError, "direct-landing-series-required"):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=leaf.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                leaf,
            )

    def test_blank_code_commit_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        with self.assertRaisesRegex(DirectLandingError, "direct-landing-code-commit-required"):
            direct_landing(
                fixture["config"],
                DirectLandingRequest(
                    contract_path=fixture["contract"].contract_path.as_posix(),
                    code_commit="   ",
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                fixture["contract"],
            )

    def test_contract_changed_under_the_lock_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        changed = replace(contract, task_name="moved")
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.reread_configured_contract",
                return_value=(changed, mock.sentinel.location),
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-contract-changed"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                contract,
            )

    def test_unresolvable_commit_tree_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.require_git",
                return_value="",
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-code-commit-invalid"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                contract,
            )

    def test_preview_with_internal_memory_reports_mode(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = replace(fixture["contract"], memory_mode="internal")
        with mock.patch(
            "agents_remember.worktrees.direct_landing.reread_configured_contract",
            return_value=(contract, mock.sentinel.location),
        ):
            preview = direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    intent_note="approve",
                    dry_run=True,
                ),
                contract,
            )
        self.assertEqual(preview["memory"], {"memoryMode": "internal"})
        effective_input = cast(dict[str, dict[str, str]], preview["effectiveInput"])
        self.assertEqual(effective_input["memory"]["state"], "not-applicable")
        self.assertEqual(effective_input["ledger"]["state"], "not-applicable")

    def test_preview_refuses_external_memory_without_authority_paths(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = replace(fixture["contract"], memory_repo_path=None, ledger_path=None)
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.reread_configured_contract",
                return_value=(contract, mock.sentinel.location),
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-memory-authority-missing"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                    dry_run=True,
                ),
                contract,
            )

    def test_invalid_ledger_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.load_ledger",
                side_effect=LedgerError("bad ledger"),
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-ledger-invalid"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                    dry_run=True,
                ),
                contract,
            )

    def test_apply_requires_exact_gated_candidate_tree(self) -> None:
        fixture = _series_fixture(Path(self.temp.name) / "fx")
        with self.assertRaisesRegex(DirectLandingError, "direct-landing-candidate-tree-required"):
            direct_landing(
                fixture["config"],
                DirectLandingRequest(
                    contract_path=fixture["contract"].contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                fixture["contract"],
            )

    def test_apply_with_internal_memory_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = replace(fixture["contract"], memory_mode="internal")
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.reread_configured_contract",
                return_value=(contract, mock.sentinel.location),
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-memory-required"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    intent_note="approve",
                ),
                contract,
            )

    def test_apply_with_missing_memory_paths_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = replace(fixture["contract"], memory_repo_path=None, ledger_path=None)
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.reread_configured_contract",
                return_value=(contract, mock.sentinel.location),
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-memory-authority-missing"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                contract,
            )

    def test_apply_refuses_memory_branch_mismatch(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]
        git(memory, "checkout", "main")
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.load_ledger",
                side_effect=AssertionError("ledger must follow memory branch authority"),
            ) as ledger_read,
            self.assertRaisesRegex(DirectLandingError, "direct-landing-memory-branch-mismatch"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approve",
                ),
                contract,
            )
        ledger_read.assert_not_called()

    def test_reland_with_matching_memory_commit_is_idempotent(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]
        (memory / "onboarding").mkdir(exist_ok=True)
        (memory / "onboarding" / "feature.py.md").write_text("# feature\n", encoding="utf-8")
        landed = direct_landing(
            config,
            DirectLandingRequest(
                contract_path=contract.contract_path.as_posix(),
                code_commit=fixture["code_head"],
                candidate_tree=fixture["candidate_tree"],
                memory_commit_message="direct memory content",
                ledger_commit_message="direct ledger mapping",
                intent_note="approved by owner",
            ),
            contract,
        )
        again = direct_landing(
            config,
            DirectLandingRequest(
                contract_path=contract.contract_path.as_posix(),
                code_commit=fixture["code_head"],
                candidate_tree=fixture["candidate_tree"],
                memory_commit_message="direct memory content",
                ledger_commit_message="direct ledger mapping",
                intent_note="approved by owner",
            ),
            load_contract(contract.contract_path),
        )
        self.assertEqual(
            _without_projection_effects(again),
            _without_projection_effects(landed),
        )

    def test_reland_with_conflicting_ledger_mapping_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]
        (memory / "onboarding").mkdir(exist_ok=True)
        (memory / "onboarding" / "feature.py.md").write_text("# feature\n", encoding="utf-8")
        landed = direct_landing(
            config,
            DirectLandingRequest(
                contract_path=contract.contract_path.as_posix(),
                code_commit=fixture["code_head"],
                candidate_tree=fixture["candidate_tree"],
                memory_commit_message="direct memory content",
                ledger_commit_message="direct ledger mapping",
                intent_note="approved by owner",
            ),
            contract,
        )
        (memory / "onboarding" / "feature.py.md").write_text("# changed\n", encoding="utf-8")
        observed = direct_landing(
            config,
            DirectLandingRequest(
                contract_path=contract.contract_path.as_posix(),
                code_commit=fixture["code_head"],
                candidate_tree=fixture["candidate_tree"],
                memory_commit_message="direct memory content",
                ledger_commit_message="direct ledger mapping",
                intent_note="approved by owner",
            ),
            load_contract(contract.contract_path),
        )
        self.assertEqual(
            _without_projection_effects(observed),
            _without_projection_effects(landed),
        )
        self.assertEqual(
            (memory / "onboarding" / "feature.py.md").read_text(encoding="utf-8"),
            "# changed\n",
        )

    def test_unreachable_ledger_commit_is_refused(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]
        (memory / "onboarding").mkdir(exist_ok=True)
        (memory / "onboarding" / "feature.py.md").write_text("# feature\n", encoding="utf-8")
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing.direct_landing_execution.is_ancestor",
                return_value=False,
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-ledger-unreachable"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    ledger_commit_message="direct ledger mapping",
                    intent_note="approved by owner",
                ),
                contract,
            )


class BranchAddressedRouteReviewTests(unittest.TestCase):
    """R6: record_route_review binds the task-root series contract in direct mode."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _leaf_doc(self, tasks: Path, *, leaf_id: str = "260815-DIRECT-L1") -> Path:
        tasks.mkdir(parents=True, exist_ok=True)
        path = tasks / f"{leaf_id.lower()}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "id": leaf_id,
                    "slug": leaf_id.lower(),
                    "title": "Direct Leaf",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": "repo-a",
                    "createdAt": "2026-08-20T00:00:00+00:00",
                    "steps": [{"id": "S1", "title": "Ready", "status": "done"}],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_record_route_review_branch_addressed_stamps_branch_head(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        tasks = fixture["tasks"]
        leaf_path = self._leaf_doc(tasks)
        code = fixture["code"]
        report = tasks / "notes" / "reports" / "direct-review.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Review\n\nPass.\n", encoding="utf-8")

        result = task_doc_tool(
            config,
            TaskDocTarget(
                repo_id="repo-a",
                task_name="direct-task",
                slug="260815-direct-l1",
            ),
            operation="record_route_review",
            edit=TaskDocEdit(
                review={
                    "verdict": "pass",
                    "verdictRef": "notes/reports/direct-review.md",
                    "routes": [
                        {
                            "route": "260815-direct-l1",
                            "verdict": "pass",
                            "evidenceRef": "notes/reports/direct-review.md",
                        }
                    ],
                }
            ),
            call=TaskDocCall(dry_run=False, branch_addressed=True),
        )
        self.assertEqual(result["operation"], "task_doc.record_route_review")
        branch_tree = require_git(code, ["rev-parse", "refs/heads/ar/direct-task^{tree}"])
        stamped = json.loads(leaf_path.read_text(encoding="utf-8"))["routeReview"]
        self.assertEqual(stamped["candidateTree"], branch_tree)
        self.assertEqual(stamped["verdict"], "pass")

    def test_record_route_review_branch_addressed_is_policy_gated(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = _scratch_config(
            root / "fx",
            fixture["code"],
            fixture["memory"],
            direct_execution_enabled=False,
        )
        tasks = fixture["tasks"]
        self._leaf_doc(tasks)

        with self.assertRaisesRegex(TaskDocError, "disabled by policy"):
            task_doc_tool(
                config,
                TaskDocTarget(
                    repo_id="repo-a",
                    task_name="direct-task",
                    slug="260815-direct-l1",
                ),
                operation="record_route_review",
                edit=TaskDocEdit(review={"verdict": "pass"}),
                call=TaskDocCall(branch_addressed=True),
            )

    def test_record_route_review_without_binding_names_recovery(self) -> None:
        """R9: the master-doc and missing-contract refusals name the recovery."""
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        tasks = fixture["tasks"]
        self._leaf_doc(tasks)

        # No contract binding at all (task root without a series contract): a valid
        # standalone task doc with no series-contract.md anywhere in the root.
        bare = root / "bare"
        bare.mkdir(parents=True)
        (bare / "task.json").write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "id": "L1",
                    "slug": "task",
                    "title": "Bare leaf",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": "repo-a",
                    "createdAt": "2026-08-20T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(TaskDocError, "re-stamp the series contract"):
            task_doc_tool(
                config,
                TaskDocTarget(repo_id="repo-a", contract_path=(bare / "task.json").as_posix()),
                operation="record_route_review",
                edit=TaskDocEdit(review={"verdict": "pass"}),
            )

    def test_terminal_leaf_resolution_names_missing_binding(self) -> None:
        """R9: leaf_doc's blank-leaf-id refusal names the recovery."""

    def test_branch_addressed_mode_is_only_for_record_route_review(self) -> None:
        """task_doc_route_review.py:59 -- the branch-addressed flag is refused on
        any operation that resolves its own contract binding."""
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        with self.assertRaisesRegex(TaskDocError, "only defined for record_route_review"):
            task_doc_tool(
                fixture["config"],
                TaskDocTarget(repo_id="repo-a", task_name="direct-task"),
                operation="set_status",
                edit=TaskDocEdit(fields={"status": "inProgress"}),
                call=TaskDocCall(branch_addressed=True),
            )

    def test_bound_form_success_records_branch_addressed_review(self) -> None:
        """The bound form's success path (build_route_review + _validate)."""
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        tasks = fixture["tasks"]
        leaf_path = self._leaf_doc(tasks)
        report = tasks / "notes" / "reports" / "direct-review.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Review\n\nPass.\n", encoding="utf-8")
        result = task_doc_tool(
            config,
            TaskDocTarget(
                repo_id="repo-a",
                task_name="direct-task",
                slug="260815-direct-l1",
            ),
            operation="record_route_review",
            edit=TaskDocEdit(
                review={
                    "verdict": "pass",
                    "verdictRef": "notes/reports/direct-review.md",
                    "routes": [
                        {
                            "route": "260815-direct-l1",
                            "verdict": "pass",
                            "evidenceRef": "notes/reports/direct-review.md",
                        }
                    ],
                }
            ),
            call=TaskDocCall(dry_run=False, branch_addressed=True),
        )
        self.assertEqual(result["operation"], "task_doc.record_route_review")
        stamped = json.loads(leaf_path.read_text(encoding="utf-8"))["routeReview"]
        self.assertEqual(stamped["verdict"], "pass")

        root = Path(self.temp.name)
        with self.assertRaisesRegex(TerminalLeafResolutionError, "re-stamp the series contract"):
            resolve_terminal_leaf_doc(root, "  ")

    def test_closeout_door_declare_refusal_names_direct_landing_recovery(self) -> None:
        """R9: a series door without its branch-addressed candidate names the route."""
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        contract = replace(fixture["contract"], closeout_door=None)
        request = CloseoutDoorRequest.model_validate(
            {
                "action": "declare",
                "contract_path": contract.contract_path.as_posix(),
                "grade": {"priority": "normal", "judgmentId": "J-direct"},
                "admission": {},
            }
        )
        with self.assertRaisesRegex(CloseoutQueueError, "direct landing"):
            door_task_context(fixture["config"], contract, request)
