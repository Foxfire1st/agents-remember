from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import test_organizational_completion_integration as fixture_mod
from agents_remember.models.lifecycles.operation import IntegrationQualityCertification
from agents_remember.models.queue.closeout_queue import AppliedQueueRequest
from agents_remember.worktrees.integration import integration_quality as quality
from agents_remember.worktrees.integration import (
    organizational_completion_integration as integration,
)
from agents_remember.worktrees.integration import organizational_completion_repair as repair
from agents_remember.worktrees.modules.code_quality_gate import QualityGatePlan
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    _integration_boundary_context,
    _integration_completion_event,
    _operation_owner,
    _queue_candidate_integration_was_completed,
    contract_queue_binding,
    integration_queue_completion_evidence,
)
from agents_remember.worktrees.worktree_contract import write_contract


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
            **fixture_mod.quality_mod.organizational_quality_failure_payload(contract, "test"),
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
        topology = repair.TaskDocumentTopology(contract.coordination_root)
        owner = _operation_owner(record.operationKey)
        context = repair._RepairContext(
            contract,
            reset,
            binding,
            owner,
            topology,
            expected,
            failed,
            evidence,
        )
        return SimpleNamespace(
            contract=contract,
            store=store,
            record=record,
            authority=authority,
            failed=failed,
            evidence=evidence,
            binding=binding,
            reset=reset,
            topology=topology,
            owner=owner,
            context=context,
        )

    def _store_repair_variant(
        self,
        facts,
        *,
        result=None,
        repair_evidence=None,
        status="cancelled",
    ) -> None:
        updates = {  # pragma: no cover
            "status": status,
            "phase": status,
            "cancelRequested": True,
            "finishedAt": repair.now_iso(),
            "workerPid": None,
        }
        if result is not None:  # pragma: no cover
            updates["result"] = result
        if repair_evidence is not None:  # pragma: no cover
            updates["organizationalRepair"] = repair_evidence
        facts.store.update(lambda current: current.model_copy(update=updates))  # pragma: no cover

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
        graph = repair._graph_context(facts.topology, facts.binding.sprint_ref)
        initial = repair._initial_state(facts.binding.sprint_ref, graph.revision, repair.now_iso())
        queue_store = repair.CloseoutQueueStore(
            facts.contract.coordination_root,
            facts.binding.sprint_ref,
        )
        queue_before = queue_store.read(initial)
        contract_before = facts.contract.contract_path.read_bytes()

        with self.assertRaisesRegex(CloseoutQueueError, "durable cancelled"):
            repair.prepare_organizational_completion_repair(facts.contract)

        self.assertEqual(facts.store.read(), facts.record)
        self.assertEqual(queue_store.read(initial), queue_before)
        self.assertEqual(facts.contract.contract_path.read_bytes(), contract_before)

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
            fixture_mod.cancel_operation(contract.contract_path, "integrate").status,
            "cancelled",
        )
        reset = fixture_mod.load_contract(contract.contract_path)
        self.assertEqual((reset.closeout_status, reset.code_commit), ("not-started", ""))

    def _boundary(self, contract, operation_key):
        binding = contract_queue_binding(contract)
        assert binding is not None
        commits = (
            contract.code_commit,
            contract.memory_content_commit,
            contract.ledger_commit,
        )
        context, initial = _integration_boundary_context(
            contract,
            binding,
            operation_key=operation_key,
            commits=commits,
        )
        state = integration.CloseoutQueueStore(
            contract.coordination_root,
            binding.sprint_ref,
        ).read(initial)
        graph = integration._graph_context(context.topology, binding.sprint_ref)
        return context, state, graph, commits

    def _certificate(self, contract) -> IntegrationQualityCertification:
        completion = integration.preview_organizational_completion(contract)
        assert completion is not None
        plan = QualityGatePlan(mode="full", executor="dagger")
        attestation = quality._quality_attestation(completion, contract, plan)
        return quality._certification(
            completion,
            fixture_mod._full_gate(contract),
            attestation=attestation,
        )

    def test_preview_ignores_a_candidate_that_has_not_reached_certification(self) -> None:
        contract = self.owner._certified_contract(final=True)
        _context, state, _graph, _commits = self._boundary(contract, "a" * 64)
        binding = contract_queue_binding(contract)
        assert binding is not None
        candidate = state.candidates[binding.candidate_ref.key]
        declared = state.model_copy(
            update={
                "candidates": {
                    binding.candidate_ref.key: candidate.model_copy(update={"state": "declared"})
                }
            }
        )
        store = SimpleNamespace(inspect=lambda _initial, reader: reader(declared))
        with mock.patch.object(integration, "CloseoutQueueStore", return_value=store):
            self.assertIsNone(integration.preview_organizational_completion(contract))

    def test_completed_recovery_contract_and_quality_matrix(self) -> None:
        contract = self.owner._certified_contract(final=True)
        _store, _runtime, record = self.owner._integration_runtime(contract)
        context, state, graph, commits = self._boundary(contract, record.operationKey)
        completed = replace(
            contract,
            integration_status="completed",
            integrated_code_commit=commits[0],
            integrated_memory_content_commit=commits[1],
            integrated_ledger_commit=commits[2],
        )
        context = replace(context, contract=completed)
        absent = state.model_copy(update={"candidates": {}})

        with self.assertRaisesRegex(CloseoutQueueError, "exact finalized"):
            integration._require_completed_integration_recovery(
                replace(context, contract=contract),
                absent,
                graph=graph,
            )

        queue_completion = integration_queue_completion_evidence(
            completed,
            operation_key=record.operationKey,
            commits=commits,
        )
        assert queue_completion is not None
        exact_record = record.model_copy(update={"queueCompletion": queue_completion})
        exact_store = SimpleNamespace(read=lambda: exact_record)
        with mock.patch.object(integration, "LifecycleOperationStore", return_value=exact_store):
            integration._require_completed_integration_recovery(context, absent, graph=graph)

        master_ref = context.topology.parent(context.binding.candidate_ref)
        assert master_ref is not None
        master = graph.masters[master_ref]
        completed_graph = replace(
            graph,
            masters={
                **graph.masters,
                master_ref: replace(
                    master,
                    document=master.document.model_copy(update={"status": "Completed"}),
                ),
            },
        )
        with (
            mock.patch.object(integration, "LifecycleOperationStore", return_value=exact_store),
            self.assertRaisesRegex(CloseoutQueueError, "no durable full-gate"),
        ):
            integration._require_completed_integration_recovery(
                context,
                absent,
                graph=completed_graph,
            )

        wrong_certificate = self._certificate(contract).model_copy(update={"codeCommit": "0" * 40})
        wrong_record = exact_record.model_copy(update={"qualityCertification": wrong_certificate})
        wrong_store = SimpleNamespace(read=lambda: wrong_record)
        with (
            mock.patch.object(integration, "LifecycleOperationStore", return_value=wrong_store),
            self.assertRaisesRegex(CloseoutQueueError, "does not name this final candidate"),
        ):
            integration._require_completed_integration_recovery(
                context,
                absent,
                graph=graph,
            )

    def test_operation_certification_and_recovery_claim_guards(self) -> None:
        contract = self.owner._certified_contract(final=True)
        _store, _runtime, record = self.owner._integration_runtime(contract)
        context, state, graph, _commits = self._boundary(contract, record.operationKey)
        missing = SimpleNamespace(read=lambda: None)
        with (
            mock.patch.object(integration, "LifecycleOperationStore", return_value=missing),
            self.assertRaisesRegex(CloseoutQueueError, "durable full-gate proof"),
        ):
            integration._operation_completion_fingerprint(
                contract,
                operation_key=record.operationKey,
                expected="0" * 64,
            )
        with (
            mock.patch.object(integration, "LifecycleOperationStore", return_value=missing),
            self.assertRaisesRegex(RuntimeError, "not durably certified"),
        ):
            integration.recorded_organizational_quality_certification(
                contract,
                operation_key=record.operationKey,
            )

        binding = context.binding
        candidate = state.candidates[binding.candidate_ref.key]
        wrong_owner_state = state.model_copy(
            update={
                "candidates": {
                    binding.candidate_ref.key: candidate.model_copy(
                        update={
                            "state": "integration-in-flight",
                            "inFlightOwnerFingerprint": "f" * 64,
                        }
                    )
                }
            }
        )
        with self.assertRaisesRegex(CloseoutQueueError, "exact candidate claimed"):
            integration._require_integration_recovery_candidate(
                context,
                wrong_owner_state,
                graph=graph,
            )

        claimed = candidate.model_copy(
            update={
                "state": "integration-in-flight",
                "inFlightOwnerFingerprint": context.owner,
                "closeoutCodeCommit": "0" * 40,
            }
        )
        claimed_state = wrong_owner_state.model_copy(
            update={"candidates": {binding.candidate_ref.key: claimed}}
        )
        with self.assertRaisesRegex(
            CloseoutQueueError,
            "integration-code-commit-not-certified",
        ):
            integration._require_integration_recovery_candidate(
                context,
                claimed_state,
                graph=graph,
            )

        event = _integration_completion_event(context.binding, context.owner, context.commits)
        receipt_state = state.model_copy(
            update={
                "appliedRequests": [
                    AppliedQueueRequest(
                        requestId=event.request_id,
                        fingerprint=event.fingerprint,
                        revision=state.revision,
                    )
                ]
            }
        )
        self.assertTrue(
            _queue_candidate_integration_was_completed(
                receipt_state,
                context.binding,
                owner=context.owner,
                commits=context.commits,
            )
        )

    def test_repair_contract_publication_and_candidate_guards(self) -> None:
        facts = self._repair_fixture()
        contract, binding, reset = facts.contract, facts.binding, facts.reset
        topology, owner, context = facts.topology, facts.owner, facts.context
        changed = replace(contract, commit_approval_note=f"{contract.commit_approval_note} changed")
        write_contract(contract.contract_path, changed)
        with self.assertRaisesRegex(CloseoutQueueError, "changed before repair"):
            repair._publish_reset(context=context)
        write_contract(contract.contract_path, contract)

        graph = repair._graph_context(topology, binding.sprint_ref)
        initial = repair._initial_state(binding.sprint_ref, graph.revision, repair.now_iso())
        state = repair.CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).read(
            initial
        )
        absent = state.model_copy(update={"candidates": {}})
        write_contract(contract.contract_path, reset)
        self.assertEqual(repair._retire_candidate(absent, context=context), absent)
        write_contract(contract.contract_path, contract)
        with self.assertRaisesRegex(CloseoutQueueError, "candidate disappeared"):
            repair._retire_candidate(absent, context=context)

        candidate = state.candidates[binding.candidate_ref.key]
        wrong_owner = state.model_copy(
            update={
                "candidates": {
                    binding.candidate_ref.key: candidate.model_copy(update={"state": "certified"})
                }
            }
        )
        with self.assertRaisesRegex(CloseoutQueueError, "integration owner"):
            repair._retire_candidate(wrong_owner, context=context)

        claimed = candidate.model_copy(
            update={
                "state": "integration-in-flight",
                "inFlightOwnerFingerprint": owner,
            }
        )
        claimed_state = state.model_copy(
            update={"candidates": {binding.candidate_ref.key: claimed}}
        )
        master_ref = claimed.owningMaster
        master = graph.masters[master_ref]
        atomic_graph = replace(
            graph,
            masters={
                **graph.masters,
                master_ref: replace(
                    master,
                    document=master.document.model_copy(update={"executionNature": "atomic"}),
                ),
            },
        )
        with (
            mock.patch.object(repair, "_graph_context", return_value=atomic_graph),
            self.assertRaisesRegex(CloseoutQueueError, "organizational master"),
        ):
            repair._retire_candidate(claimed_state, context=context)

        drifted = claimed.model_copy(update={"closeoutCodeCommit": "0" * 40})
        drifted_state = state.model_copy(
            update={"candidates": {binding.candidate_ref.key: drifted}}
        )
        with self.assertRaisesRegex(CloseoutQueueError, "no longer matches"):
            repair._retire_candidate(drifted_state, context=context)

    def test_repair_refuses_foreign_reset_binding_and_candidate_identity(self) -> None:
        facts = self._repair_fixture()
        contract = facts.contract
        facts.store.update(
            lambda current: current.model_copy(
                update={
                    "status": "cancelled",
                    "phase": "cancelled",
                    "cancelRequested": True,
                    "finishedAt": repair.now_iso(),
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
