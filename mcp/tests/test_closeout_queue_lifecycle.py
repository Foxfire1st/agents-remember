"""L3 forcing for door-source retry and transition semantics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.models.lifecycles.door import CloseoutDoorRequest
from agents_remember.worktrees.integration.closeout.door import (
    _require_door_transition,
    successor_waiting_door,
)
from agents_remember.worktrees.integration.closeout.door_control import (
    DoorActor,
    closeout_door_tool,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_closeout_queue import LEAF_A, MASTER_A, SPRINT, QueueFixture
from test_closeout_queue_models import door_generation


class CloseoutDoorTransitionTests(unittest.TestCase):
    def test_same_generation_transition_map_is_narrow(self) -> None:
        waiting = door_generation()
        for target in ("waiting", "deferred", "withdrawn", "claimed"):
            update: dict[str, object] = {"disposition": target}
            if target == "claimed":
                update.update(
                    {
                        "operationKind": "closeout",
                        "operationFingerprint": "3" * 64,
                        "claimedOperationKey": "4" * 64,
                    }
                )
            _require_door_transition(waiting, waiting.model_copy(update=update))
        deferred = waiting.model_copy(update={"disposition": "deferred"})
        for target in ("waiting", "deferred", "withdrawn"):
            _require_door_transition(deferred, deferred.model_copy(update={"disposition": target}))
        withdrawn = waiting.model_copy(update={"disposition": "withdrawn"})
        _require_door_transition(withdrawn, withdrawn)
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            _require_door_transition(
                withdrawn, withdrawn.model_copy(update={"disposition": "waiting"})
            )

    def test_claimed_same_generation_cannot_rewrite_operation_owner(self) -> None:
        claimed = door_generation(
            disposition="claimed",
            operationKind="closeout",
            operationFingerprint="3" * 64,
            claimedOperationKey="4" * 64,
        )
        _require_door_transition(claimed, claimed)
        for field, value in (
            ("operationFingerprint", "5" * 64),
            ("claimedOperationKey", "6" * 64),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "immutable"):
                _require_door_transition(claimed, claimed.model_copy(update={field: value}))

    def test_cross_generation_map_is_exact(self) -> None:
        for source, target in (
            ("waiting", "waiting"),
            ("deferred", "deferred"),
            ("withdrawn", "waiting"),
            ("claimed", "waiting"),
        ):
            current_updates: dict[str, object] = {"disposition": source}
            if source == "claimed":
                current_updates.update(
                    {
                        "operationKind": "closeout",
                        "operationFingerprint": "3" * 64,
                        "claimedOperationKey": "4" * 64,
                    }
                )
            current = door_generation(**current_updates)
            successor = current.model_copy(
                update={
                    "generationId": "7" * 64,
                    "predecessorGenerationId": current.generationId,
                    "disposition": target,
                    "operationKind": None,
                    "operationFingerprint": "",
                    "claimedOperationKey": "",
                }
            )
            _require_door_transition(current, successor)
        current = door_generation(disposition="deferred")
        illegal = current.model_copy(
            update={
                "generationId": "8" * 64,
                "predecessorGenerationId": current.generationId,
                "disposition": "waiting",
            }
        )
        with self.assertRaisesRegex(RuntimeError, "predecessor"):
            _require_door_transition(current, illegal)

    def test_claimed_successor_has_new_generation_and_no_operation_cells(self) -> None:
        claimed = door_generation(
            disposition="claimed",
            operationKind="closeout",
            operationFingerprint="3" * 64,
            claimedOperationKey="4" * 64,
        )
        successor = successor_waiting_door(
            claimed,
            declared_by="journal@root",
            declared_at="2026-08-24T00:01:00+00:00",
        )
        self.assertNotEqual(successor.generationId, claimed.generationId)
        self.assertEqual(successor.predecessorGenerationId, claimed.generationId)
        self.assertEqual(successor.disposition, "waiting")
        self.assertIsNone(successor.operationKind)


class CloseoutDoorRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(
            Path(self.temporary.name),
            memory_mode="internal",
        )
        self.fixture.enable_direct_execution()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_same_intent_declare_retry_converges_on_one_generation(self) -> None:
        self.fixture.declare(MASTER_A)
        first = load_contract(self.fixture.contracts[MASTER_A].contract_path).closeout_door
        assert first is not None
        self.fixture.declare(MASTER_A)
        retried = load_contract(self.fixture.contracts[MASTER_A].contract_path).closeout_door
        assert retried is not None
        self.assertEqual(retried.generationId, first.generationId)

    def test_defer_resume_withdraw_are_idempotent_for_exact_generation(self) -> None:
        self.fixture.declare(MASTER_A)
        for action in ("defer", "defer", "resume", "resume", "withdraw", "withdraw"):
            self.fixture.mutate(action, candidate=LEAF_A)
        current = load_contract(self.fixture.contracts[MASTER_A].contract_path).closeout_door
        assert current is not None
        self.assertEqual(current.disposition, "withdrawn")
        self.assertEqual(self.fixture.status()["members"], [])

    def test_series_status_requires_no_candidate_assertion(self) -> None:
        series = load_contract(self.fixture.tasks / "master-a" / "series-contract.md")
        response = closeout_door_tool(
            self.fixture.cfg,
            CloseoutDoorRequest(action="status", contract_path=series.contract_path.as_posix()),
            actor=DoorActor(role="orchestrator", task_document_ref=SPRINT),
            admitted_contract=series,
        )
        self.assertEqual(response["state"], "absent")
