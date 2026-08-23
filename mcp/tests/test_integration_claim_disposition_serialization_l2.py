"""Cross-kind serialization forcing for integration claim and closeout disposition."""

from __future__ import annotations

import threading
import unittest
from dataclasses import replace
from unittest import mock

import test_organizational_completion_integration as fixture_mod
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import (
    worktree_integrate_tool,
    worktree_status_tool,
)
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.integration import integration_claim_transfer as claim_mod
from agents_remember.worktrees.integration.integration_publication_fence import (
    IntegrationDoorAuthorityConflict,
)
from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_controls as controls_mod,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    LifecycleControlCommand,
    LifecycleControlError,
    control_operation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.organizational_completion_integration import (
    prepare_integration_publication_intent,
    preview_integration_boundary,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
)
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_closeout_queue import SPRINT
from test_lifecycle_operation_controls_l2 import _public_control


def _recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | {
            nested for item in value.values() for nested in _recursive_keys(item)
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _recursive_keys(item)}
    return set()


class IntegrationClaimDispositionSerializationL2Tests(unittest.TestCase):
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

    @staticmethod
    def _disposition_command(contract, record) -> LifecycleControlCommand:
        return LifecycleControlCommand(
            admitted_contract=contract,
            admitted_location=require_matching_lifecycle_operation_location(contract),
            configured_authority=record.input.configPath,
            kind="closeout",
            action="retire",
            expected_generation=record.generation,
            intent_note="serialize exact completed closeout disposition",
            allow_completed_disposition=True,
        )

    @staticmethod
    def _closeout_store(contract) -> LifecycleOperationStore:
        return LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))

    def _claim_inputs(self, contract):
        store, runtime, record = self.owner._integration_runtime(contract)
        intent = prepare_integration_publication_intent(
            contract,
            operation_key=record.operationKey,
            generation=record.generation,
            facts=preview_integration_boundary(contract),
            certification=None,
        )
        commits = (
            contract.code_commit,
            contract.memory_content_commit,
            contract.ledger_commit,
        )
        return store, record, self.owner._args(contract, runtime, record), intent, commits

    def _status(self, contract):
        config = load_config(self.fixture.config_path)
        with mock.patch(
            "agents_remember.application.worktree_tools.git_worktree_manager.status_result",
            return_value=WorktreeCommandResult(
                0,
                {
                    "contract_path": contract.contract_path.as_posix(),
                    "task_name": contract.task_name,
                },
            ),
        ):
            status = worktree_status_tool(
                config,
                TaskRef(
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                ),
            )
        return config, next(
            row for row in status["lifecycleOperations"] if row["kind"] == "integrate"
        )

    def test_disposition_wins_before_claim_and_claim_mutates_neither_journal_nor_queue(
        self,
    ) -> None:
        contract = self.owner._certified_contract(final=False)
        integration_store, _record, args, intent, commits = self._claim_inputs(contract)
        closeout_store = self._closeout_store(contract)
        closeout = closeout_store.read()
        assert closeout is not None
        queue = CloseoutQueueStore(self.fixture.coord, SPRINT)
        queue_before = queue.state_path.read_bytes()
        disposition_inside = threading.Event()
        release_disposition = threading.Event()
        claim_reached_locked_body = threading.Event()
        outcomes: dict[str, object] = {}
        original_publish = controls_mod.publish_door_intent
        original_claim_load = claim_mod.load_contract

        def pause_disposition(path, publication):
            disposition_inside.set()
            assert release_disposition.wait(5)
            return original_publish(path, publication)

        def observe_claim_lock(path):
            claim_reached_locked_body.set()
            return original_claim_load(path)

        def dispose() -> None:
            try:
                outcomes["disposition"] = control_operation(
                    self._disposition_command(contract, closeout)
                )
            except BaseException as error:  # pragma: no cover - asserted below
                outcomes["disposition"] = error

        def claim() -> None:
            try:
                outcomes["claim"] = claim_mod.transfer_and_publish_integration_claim(
                    contract,
                    args,
                    intent,
                    commits=commits,
                )
            except BaseException as error:
                outcomes["claim"] = error

        with (
            mock.patch.object(controls_mod, "publish_door_intent", side_effect=pause_disposition),
            mock.patch.object(claim_mod, "load_contract", side_effect=observe_claim_lock),
        ):
            disposition_thread = threading.Thread(target=dispose)
            disposition_thread.start()
            self.assertTrue(disposition_inside.wait(5))
            claim_thread = threading.Thread(target=claim)
            claim_thread.start()
            self.assertFalse(claim_reached_locked_body.wait(0.2))
            release_disposition.set()
            disposition_thread.join(5)
            claim_thread.join(5)

        self.assertFalse(disposition_thread.is_alive())
        self.assertFalse(claim_thread.is_alive())
        self.assertNotIsInstance(outcomes["disposition"], BaseException)
        self.assertIsInstance(outcomes["claim"], IntegrationDoorAuthorityConflict)
        untouched = integration_store.read()
        assert untouched is not None
        self.assertIsNone(untouched.integrationPublication)
        self.assertEqual(queue.state_path.read_bytes(), queue_before)
        retired_door = load_contract(contract.contract_path).closeout_door
        assert retired_door is not None
        self.assertEqual(retired_door.disposition, "retired")

    def test_claim_wins_and_waiting_disposition_refuses_before_door_mutation(self) -> None:
        contract = self.owner._certified_contract(final=False)
        integration_store, _record, args, intent, commits = self._claim_inputs(contract)
        closeout = self._closeout_store(contract).read()
        assert closeout is not None
        claim_inside = threading.Event()
        release_claim = threading.Event()
        disposition_done = threading.Event()
        outcomes: dict[str, object] = {}
        original_transfer = claim_mod.transfer_integration_claim

        def pause_claim(*values, **options):
            claim_inside.set()
            assert release_claim.wait(5)
            return original_transfer(*values, **options)

        def claim() -> None:
            try:
                outcomes["claim"] = claim_mod.transfer_and_publish_integration_claim(
                    contract,
                    args,
                    intent,
                    commits=commits,
                )
            except BaseException as error:  # pragma: no cover - asserted below
                outcomes["claim"] = error

        def dispose() -> None:
            try:
                outcomes["disposition"] = control_operation(
                    self._disposition_command(contract, closeout)
                )
            except BaseException as error:
                outcomes["disposition"] = error
            finally:
                disposition_done.set()

        with mock.patch.object(claim_mod, "transfer_integration_claim", side_effect=pause_claim):
            claim_thread = threading.Thread(target=claim)
            claim_thread.start()
            self.assertTrue(claim_inside.wait(5))
            disposition_thread = threading.Thread(target=dispose)
            disposition_thread.start()
            self.assertFalse(disposition_done.wait(0.2))
            release_claim.set()
            claim_thread.join(5)
            disposition_thread.join(5)

        self.assertFalse(claim_thread.is_alive())
        self.assertFalse(disposition_thread.is_alive())
        accepted = integration_store.read()
        assert accepted is not None and accepted.integrationPublication is not None
        self.assertEqual(accepted.integrationPublication.claimState, "proven")
        self.assertNotIsInstance(outcomes["claim"], BaseException)
        refusal = outcomes["disposition"]
        self.assertIsInstance(refusal, LifecycleControlError)
        assert isinstance(refusal, LifecycleControlError)
        self.assertEqual(refusal.status, "lifecycle-integration-claim-active")
        claimed_door = load_contract(contract.contract_path).closeout_door
        assert claimed_door is not None
        self.assertEqual(claimed_door.disposition, "claimed")

    def test_retired_preclaim_public_admission_refuses_before_journal_or_queue_mutation(
        self,
    ) -> None:
        contract = self.owner._certified_contract(final=False)
        closeout = self._closeout_store(contract).read()
        assert closeout is not None
        control_operation(self._disposition_command(contract, closeout))
        queue = CloseoutQueueStore(self.fixture.coord, SPRINT)
        queue_before = queue.state_path.read_bytes()
        integration_path = operation_record_path(contract.worktree_group, "integrate")

        refused = worktree_integrate_tool(
            load_config(self.fixture.config_path),
            contract_path=contract.contract_path.as_posix(),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "integration-closeout-door-not-claimed")
        self.assertTrue(refused["developerDecisionRequired"])
        self.assertEqual(refused["observed"]["disposition"], "retired")
        self.assertFalse(integration_path.exists())
        self.assertEqual(queue.state_path.read_bytes(), queue_before)

    def test_residual_external_door_contradiction_has_no_control_and_stale_row_refuses(
        self,
    ) -> None:
        contract = self.owner._certified_contract(final=False)
        store, _record, args, intent, commits = self._claim_inputs(contract)
        proven = claim_mod.transfer_and_publish_integration_claim(
            contract,
            args,
            intent,
            commits=commits,
        )
        accepted = store.read()
        assert accepted is not None and accepted.integrationPublication == proven
        config, before = self._status(contract)
        advertised = next(row for row in before["legalControls"] if row["action"] == "recover")
        live = load_contract(contract.contract_path)
        door = live.closeout_door
        assert door is not None
        retired = door.model_copy(
            update={
                "disposition": "retired",
                "operationKind": None,
                "operationFingerprint": "",
                "claimedOperationKey": "",
            }
        )
        write_contract(live.contract_path, replace(live, closeout_door=retired))

        _config, projected = self._status(load_contract(contract.contract_path))
        self.assertEqual(projected["legalControls"], [])
        result = projected["result"]
        self.assertEqual(result["state"], "integration-closeout-door-conflict")
        self.assertTrue(result["developerDecisionRequired"])
        self.assertEqual(result["expected"]["generationId"], intent.closeoutDoorGenerationId)
        self.assertEqual(result["observed"]["disposition"], "retired")
        refused = _public_control(config, advertised)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "integration-closeout-door-conflict")
        self.assertEqual(refused["nextAction"], "developer-decision")
        self.assertEqual(refused["expected"], result["expected"])
        self.assertEqual(refused["observed"], result["observed"])
        self.assertFalse({"operationKey", "claimedOperationKey"} & _recursive_keys(projected))
        self.assertFalse({"operationKey", "claimedOperationKey"} & _recursive_keys(refused))

    def test_queue_door_failure_never_formats_private_operation_identity(self) -> None:
        contract = self.owner._certified_contract(final=False)
        closeout = self._closeout_store(contract).read()
        live = load_contract(contract.contract_path)
        assert closeout is not None and live.closeout_door is not None
        mismatched = live.closeout_door.model_copy(update={"claimedOperationKey": "f" * 64})
        write_contract(live.contract_path, replace(live, closeout_door=mismatched))

        with self.assertRaises(CloseoutQueueError) as caught:
            certify_queue_candidate_closeout(
                load_contract(live.contract_path),
                closeout.operationKey,
            )

        detail = str(caught.exception)
        self.assertNotIn("operationKey", detail)
        self.assertNotIn("claimedOperationKey", detail)
        self.assertIn("operationIdentityDigests", detail)
