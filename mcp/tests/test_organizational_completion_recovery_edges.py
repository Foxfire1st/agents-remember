from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import test_organizational_completion_integration as fixture_mod
from agents_remember.worktrees.integration import organizational_completion_repair as repair
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    contract_queue_binding,
)
from agents_remember.worktrees.worktree_contract import write_contract
from lifecycle_control_test_support import cancel_current_generation


class OrganizationalCompletionRecoveryEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = fixture_mod.OrganizationalCompletionIntegrationTests(
            "test_nonfinal_leaf_reuses_targeted_closeout_without_full_gate"
        )
        self.owner.setUp()
        self.fixture = self.owner.fixture

    def tearDown(self) -> None:
        try:
            self.owner.doCleanups()
        finally:
            self.owner.tearDown()

    def _repair_fixture(self):
        contract = self.owner._certified_contract(final=True)
        store, _runtime, record = self.owner._integration_runtime(contract)
        authority = record.integrationAuthority
        assert authority is not None
        failure = {
            **fixture_mod.quality_mod.organizational_quality_failure_payload(
                contract,
                fixture_mod.quality_mod.integration_quality_failure(
                    RuntimeError("private-quality-sentinel"),
                    stage="integration-quality-execution",
                    organizational_completion=True,
                ),
                expected_generation=record.generation,
            ),
            "ok": False,
            "operation": "worktree_integrate",
        }
        failed = record.model_copy(update={"result": failure})
        evidence = repair.organizational_completion_repair_evidence(contract, failed)
        failed = failed.model_copy(update={"organizationalRepair": evidence})
        binding = contract_queue_binding(contract)
        assert binding is not None
        expected = repair._repair_commits(contract, authority)
        reset = repair._quality_repair_contract(contract, expected_commits=expected)
        return SimpleNamespace(
            contract=contract,
            store=store,
            record=record,
            authority=authority,
            failed=failed,
            evidence=evidence,
            binding=binding,
            reset=reset,
        )

    def _coordination_bytes(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.fixture.coord).as_posix(): path.read_bytes()
            for path in self.fixture.coord.rglob("*")
            if path.is_file()
        }

    def test_durable_repair_generation_is_immutable(self) -> None:
        facts = self._repair_fixture()
        facts.store.update(
            lambda current: current.model_copy(
                update={
                    "organizationalRepair": facts.evidence,
                    "result": facts.failed.result,
                }
            )
        )
        changed = facts.evidence.model_copy(update={"resetContractSha256": "f" * 64})
        with self.assertRaisesRegex(RuntimeError, "repair evidence is immutable"):
            facts.store.update(
                lambda current: current.model_copy(update={"organizationalRepair": changed})
            )
        for status in ("queued", "cancelled"):
            with (
                self.subTest(status=status),
                self.assertRaisesRegex(ValueError, "canonical cancellation handoff"),
            ):
                type(facts.record).model_validate(
                    {
                        **facts.record.model_dump(mode="json"),
                        "status": status,
                        "organizationalRepair": facts.evidence.model_dump(mode="json"),
                        "result": {"state": "organizational-completion-gate-failed"},
                    }
                )
        with self.assertRaisesRegex(ValueError, "invalid lifecycle status"):
            type(facts.failed).model_validate(
                {
                    **facts.failed.model_dump(mode="json"),
                    "status": "completed",
                }
            )

    def test_repair_mutation_requires_the_canonical_cancelled_wal(self) -> None:
        facts = self._repair_fixture()
        coordination_before = self._coordination_bytes()

        with self.assertRaisesRegex(CloseoutQueueError, "durable cancelled"):
            repair.prepare_organizational_completion_repair(facts.contract)

        self.assertEqual(facts.store.read(), facts.record)
        self.assertEqual(self._coordination_bytes(), coordination_before)

    def test_gate_failure_wal_crash_recovers_without_rerunning_or_moving_refs(self) -> None:
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        code_before = fixture_mod.git(
            contract.code_repo_path, "rev-parse", contract.code_source_branch
        )
        assert contract.memory_repo_path is not None
        memory_before = fixture_mod.git(
            contract.memory_repo_path, "rev-parse", contract.memory_source_branch
        )
        with mock.patch.object(
            fixture_mod.quality_mod,
            "run_strict_code_quality_gate",
            side_effect=RuntimeError("full Dagger failure"),
        ) as full_gate:
            with (
                mock.patch.object(
                    runtime,
                    "finish",
                    side_effect=RuntimeError("crash after gate-failure WAL"),
                ),
                self.assertRaisesRegex(RuntimeError, "gate-failure WAL"),
            ):
                fixture_mod.lifecycle_operation_worker.execute_operation(record, runtime)
            pending = store.read()
            assert pending is not None
            assert pending.status == "running"
            assert pending.organizationalRepair is not None
            assert pending.result is not None
            assert pending.result["state"] == "organizational-completion-gate-failed"
            assert pending.result["ok"] is False
            assert pending.result["operation"] == "worktree_integrate"
            assert self.owner._candidate_projection(fixture_mod.LEAF_A)["candidateState"] == (
                "integration-in-flight"
            )
            store.update(lambda current: current.model_copy(update={"workerPid": 99_999_999}))

            def recover(_contract, queued):
                recovered_runtime = fixture_mod.lifecycle_operation_worker.OperationRuntime(store)
                running = recovered_runtime.start()
                assert running.operationKey == queued.operationKey
                fixture_mod.lifecycle_operation_worker.execute_operation(running, recovered_runtime)

            projection = fixture_mod.start_or_observe_operation(
                record.input,
                contract,
                launcher=recover,
                now=datetime.now(UTC) + timedelta(minutes=1),
            )
        self.assertEqual(projection.status, "input-required")
        full_gate.assert_called_once()
        assert self.owner._candidate_projection(fixture_mod.LEAF_A)["candidateState"] == (
            "integration-in-flight"
        )
        self.assertEqual(
            fixture_mod.git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            code_before,
        )
        self.assertEqual(
            fixture_mod.git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            memory_before,
        )
        self.assertEqual(
            cancel_current_generation(
                contract.contract_path,
                "integrate",
            ).status,
            "cancelled",
        )
        reset = fixture_mod.load_contract(contract.contract_path)
        self.assertEqual((reset.closeout_status, reset.code_commit), ("not-started", ""))

    def test_repair_contract_publication_refuses_a_third_byte_state(self) -> None:
        facts = self._repair_fixture()
        contract, reset = facts.contract, facts.reset
        changed = replace(contract, commit_approval_note=f"{contract.commit_approval_note} changed")
        write_contract(contract.contract_path, changed)
        with self.assertRaisesRegex(
            repair.OrganizationalRepairPublicationError,
            "neither the accepted failed generation nor its exact reset",
        ):
            repair._publish_reset(
                contract=contract,
                reset=reset,
                evidence=facts.evidence,
                record=facts.failed,
            )

    def test_repair_refuses_foreign_reset_binding_and_candidate_identity(self) -> None:
        facts = self._repair_fixture()
        contract = facts.contract
        facts.store.update(
            lambda current: current.model_copy(
                update={
                    "status": "cancelled",
                    "phase": "cancelled",
                    "cancelRequested": True,
                    "finishedAt": "2026-08-22T12:00:00+00:00",
                    "result": facts.failed.result,
                    "organizationalRepair": facts.evidence,
                    "workerPid": None,
                }
            )
        )
        foreign_contract = replace(
            contract, code_repo_path=self.fixture.root / "foreign-code-repair"
        )
        write_contract(contract.contract_path, foreign_contract)
        foreign_target = SimpleNamespace(
            side="code",
            kind=facts.authority.targetKind,
            repository=foreign_contract.code_repo_path,
            branch=facts.authority.codeSourceBranch,
            owner="owner",
        )
        with (
            mock.patch.object(repair, "integration_targets", return_value=(foreign_target,)),
            self.assertRaisesRegex(CloseoutQueueError, "code integration authority"),
        ):
            repair.prepare_organizational_completion_repair(foreign_contract)

    def test_repair_authority_generation_and_source_guards(self) -> None:
        contract = self.owner._certified_contract(final=True)
        _store, _runtime, record = self.owner._integration_runtime(contract)
        authority = record.integrationAuthority
        assert authority is not None
        with self.assertRaisesRegex(CloseoutQueueError, "sprint-super"):
            repair._repair_commits(
                contract,
                authority.model_copy(update={"targetKind": "atomic-integration"}),
            )
        commits = (contract.code_commit, contract.memory_content_commit, contract.ledger_commit)
        with self.assertRaisesRegex(CloseoutQueueError, "exact closed"):
            repair._quality_repair_contract(
                replace(contract, kind="series"),
                expected_commits=commits,
            )
        with (
            mock.patch.object(repair, "branch_commit", return_value="0" * 40),
            self.assertRaisesRegex(CloseoutQueueError, "code super moved"),
        ):
            repair._require_sources_unmoved(contract)
        with mock.patch.object(repair, "branch_commit", return_value=contract.code_base_commit):
            repair._require_sources_unmoved(replace(contract, memory_mode="disabled"))
        with (
            mock.patch.object(
                repair,
                "branch_commit",
                side_effect=[contract.code_base_commit, "0" * 40],
            ),
            self.assertRaisesRegex(CloseoutQueueError, "memory super moved"),
        ):
            repair._require_sources_unmoved(contract)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
