"""Crash and revalidation edges for journaled atomic-series bootstrap."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.modules.git import branch_exists
from agents_remember.worktrees.modules.startup import start_contract
from agents_remember.worktrees.worktree_contract import write_contract
from integration_branch_authority_test_support import (
    _add_atomic_master_to_sprint,
    _atomic_three_spec,
    _authority_fixture,
)
from test_source_lineage import _commit_on, _git


class IntegrationBranchAuthorityBootstrapEdgeTests(unittest.TestCase):
    def test_bootstrap_record_revalidates_repository_path_and_memory_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            spec = _atomic_three_spec(fixture, task_root)
            contract = start_contract._new_master_series_contract(spec)
            record = start_contract._bootstrap_record(spec, contract)
            cases = (
                (
                    record.model_copy(update={"codeRepository": "/foreign-code"}),
                    "code repository identity changed",
                ),
                (
                    record.model_copy(update={"contractPath": "/foreign-contract"}),
                    "contract path changed",
                ),
                (
                    record.model_copy(update={"memoryRepository": "/unexpected-memory"}),
                    "memory edge changed",
                ),
            )
            for changed, reason in cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                    start_contract._contract_from_bootstrap_record(spec, changed)

    def test_bootstrap_journal_refuses_changed_sprint_branch_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            spec = start_contract.MasterSeriesContractSpec(
                coordination_root=fixture.coordination,
                repo_name="repo",
                code_repo=fixture.code_repo,
                memory_root=None,
                task_root=task_root,
                task_name="atomic-three",
                parent_task_name="sprint",
                protected_branch="super",
            )
            with (
                mock.patch.object(
                    start_contract,
                    "publish_new_lifecycle_operation_location",
                    side_effect=RuntimeError("crash after ref publication"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash after ref publication"),
            ):
                start_contract.ensure_master_series_contract(spec)
            journaled = _git(fixture.code_repo, "rev-parse", "ar/atomic-three")
            sprint_path = fixture.coordination / "tasks" / "repo" / "sprint" / "task.json"
            sprint = read_task_doc(sprint_path)
            write_task_doc(
                sprint_path.parent,
                sprint.model_copy(update={"integrationBranch": "main"}),
            )
            changed = replace(spec, protected_branch="main")

            with self.assertRaisesRegex(RuntimeError, "branch authority changed"):
                start_contract.ensure_master_series_contract(changed)
            self.assertEqual(
                _git(fixture.code_repo, "rev-parse", "ar/atomic-three"),
                journaled,
            )
            self.assertFalse((task_root / "series-contract.md").exists())

    def test_bootstrap_revalidates_source_tip_before_first_ref_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            spec = _atomic_three_spec(fixture, task_root)
            publish = start_contract._publish_master_series_contract

            def advance_source(specification, contract) -> None:
                _commit_on(fixture.code_repo, "super", "concurrent-super.txt")
                publish(specification, contract)

            with (
                mock.patch.object(
                    start_contract,
                    "_publish_master_series_contract",
                    side_effect=advance_source,
                ),
                self.assertRaisesRegex(RuntimeError, "source moved"),
            ):
                start_contract.ensure_master_series_contract(spec)

            self.assertFalse(branch_exists(fixture.code_repo, "ar/atomic-three"))
            self.assertFalse((task_root / "series-contract.md").exists())
            self.assertFalse(start_contract._master_series_bootstrap_record_path(spec).exists())

    def test_bootstrap_revalidates_atomic_topology_before_first_ref_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            spec = _atomic_three_spec(fixture, task_root)
            publish = start_contract._publish_master_series_contract

            def reclassify_master(specification, contract) -> None:
                document = read_task_doc(task_root / "task.json")
                write_task_doc(
                    task_root,
                    document.model_copy(update={"executionNature": "organizational"}),
                )
                publish(specification, contract)

            with (
                mock.patch.object(
                    start_contract,
                    "_publish_master_series_contract",
                    side_effect=reclassify_master,
                ),
                self.assertRaisesRegex(RuntimeError, "effective atomic master nature"),
            ):
                start_contract.ensure_master_series_contract(spec)

            self.assertFalse(branch_exists(fixture.code_repo, "ar/atomic-three"))
            self.assertFalse((task_root / "series-contract.md").exists())
            self.assertFalse(start_contract._master_series_bootstrap_record_path(spec).exists())

    def test_bootstrap_recovery_refuses_invalid_journal_and_mismatched_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            spec = _atomic_three_spec(fixture, task_root)
            path = start_contract._master_series_bootstrap_record_path(spec)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid master-series bootstrap record"):
                start_contract._recover_master_series_bootstrap(spec)

            contract = start_contract._new_master_series_contract(spec)
            record = start_contract._bootstrap_record(spec, contract)
            path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
            write_contract(
                contract.contract_path,
                replace(contract, code_base_commit="f" * 40),
            )
            with self.assertRaisesRegex(RuntimeError, "published with different"):
                start_contract._recover_master_series_bootstrap(spec)

    def test_partial_ref_rollback_requires_memory_authority_and_reports_git_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            spec = _atomic_three_spec(fixture, task_root)
            contract = start_contract._new_master_series_contract(spec)
            record = start_contract._bootstrap_record(spec, contract)
            with self.assertRaisesRegex(RuntimeError, "requires the external memory repository"):
                start_contract._rollback_partial_bootstrap_refs(
                    spec,
                    record.model_copy(
                        update={
                            "memoryRepository": "/memory",
                            "memoryWorkBranch": "ar/atomic-three",
                            "memoryBaseCommit": record.codeBaseCommit,
                        }
                    ),
                    authority=start_contract._BOOTSTRAP_REF_AUTHORITY,
                )

            _git(
                fixture.code_repo,
                "branch",
                record.codeWorkBranch,
                record.codeBaseCommit,
            )
            with (
                mock.patch.object(
                    start_contract,
                    "run_git",
                    return_value=mock.Mock(returncode=1, stderr="cannot delete", stdout=""),
                ),
                self.assertRaisesRegex(RuntimeError, "could not retire stale partial"),
            ):
                start_contract._rollback_partial_bootstrap_refs(
                    spec,
                    record,
                    authority=start_contract._BOOTSTRAP_REF_AUTHORITY,
                )

    def test_bootstrap_ref_refuses_existing_mismatch_and_transaction_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            source = _git(fixture.code_repo, "rev-parse", "super")
            with self.assertRaisesRegex(RuntimeError, "expected journaled"):
                start_contract._require_bootstrap_ref(
                    start_contract._BootstrapRef(
                        fixture.code_repo,
                        "super",
                        "f" * 40,
                        "super",
                        source,
                    ),
                    authority=start_contract._BOOTSTRAP_REF_AUTHORITY,
                )

            with (
                mock.patch.object(start_contract, "branch_exists", return_value=False),
                mock.patch.object(
                    start_contract,
                    "run_git",
                    return_value=mock.Mock(returncode=1, stderr="transaction raced", stdout=""),
                ),
                self.assertRaisesRegex(RuntimeError, "could not create journaled"),
            ):
                start_contract._require_bootstrap_ref(
                    start_contract._BootstrapRef(
                        fixture.code_repo,
                        "ar/new",
                        source,
                        "super",
                        source,
                    ),
                    authority=start_contract._BOOTSTRAP_REF_AUTHORITY,
                )

    def test_bootstrap_record_refuses_external_identity_branch_and_disabled_memory_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external_fixture = _authority_fixture(root / "external", external_memory=True)
            task_root = external_fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(external_fixture, task_root)
            memory_root = external_fixture.leaf_contract.memory_repo_path
            assert memory_root is not None
            spec = replace(_atomic_three_spec(external_fixture, task_root), memory_root=memory_root)
            contract = start_contract._new_master_series_contract(spec)
            record = start_contract._bootstrap_record(spec, contract)
            cases = (
                (
                    record.model_copy(update={"memoryRepository": "/foreign-memory"}),
                    "memory repository identity changed",
                ),
                (
                    record.model_copy(update={"memorySourceBranch": "wrong"}),
                    "memory branch authority changed",
                ),
            )
            for changed, reason in cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                    start_contract._contract_from_bootstrap_record(spec, changed)

            internal_fixture = _authority_fixture(root / "internal")
            internal_task = internal_fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(internal_fixture, internal_task)
            internal_spec = _atomic_three_spec(internal_fixture, internal_task)
            internal_contract = start_contract._new_master_series_contract(internal_spec)
            internal_record = start_contract._bootstrap_record(internal_spec, internal_contract)
            with self.assertRaisesRegex(RuntimeError, "carries a memory edge"):
                start_contract._contract_from_bootstrap_record(
                    internal_spec,
                    internal_record.model_copy(update={"memorySourceBranch": "super"}),
                )
