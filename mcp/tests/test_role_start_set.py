"""The Manager starts a leaf's Worker, Reviewer and Curator in one call (MIK-R100 rule 1).

Operation-level proof for L100-CODE-01/02/10: three real receipts through the
registered set path, repeat reconciliation, partial failure recovery, held
opening, and set authority. The fake bridge stands in for the host.
"""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path

from agents_remember.application.agent_binding import launch_server_exclusion
from agents_remember.cli.paseo_role_tools import start_leaf_set
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.cli.role_launch_routes import seat_request_id
from agents_remember.models.role_agents import RoleStartSetCall
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for
from test_paseo_launch import LEAF_REF, MASTER_REF, SPRINT_REF
from test_paseo_role_tools import OTHER_MASTER, RoleToolsTestCase, binding, selection_of


def loaded(answer: dict) -> dict[str, bool]:
    """What a set's answer says of each seat's second copy."""

    return {role: seat["secondCopyLoaded"] for role, seat in answer["seats"].items()}


def set_call(request_id: uuid.UUID, **fields) -> RoleStartSetCall:
    """One set call for the worker leaf's references."""

    return RoleStartSetCall(request_id=request_id, **selection_of("worker"), **fields)


class SetStartTests(RoleToolsTestCase):
    """Rule 1: one call starts three seats; only the owning Manager starts a set."""

    def manager_set(self, **fields):
        manager = binding("manager")
        result = start_leaf_set(
            self.config, set_call(uuid.uuid4(), **fields), environment=manager.environment()
        )
        return manager, result

    def test_the_manager_set_creates_three_agents_with_held_seats(self) -> None:
        _, result = self.manager_set()

        self.assertEqual((result["ok"], result["status"]), (True, "completed"))
        seats = result["seats"]
        worker, reviewer, curator = (seats[role] for role in ("worker", "reviewer", "curator"))
        self.assertEqual(
            (worker["status"], worker["opened"], worker["held"]),
            ("running", True, False),
        )
        for seat in (reviewer, curator):
            with self.subTest(seat=seat["requestId"]):
                self.assertEqual(
                    (seat["status"], seat["opened"], seat["held"]),
                    ("held", False, True),
                )
                self.assertFalse(seat["hasTurn"])
        agent_ids = {seat["agentId"] for seat in (worker, reviewer, curator)}
        self.assertEqual(len(agent_ids), 3)
        self.assertEqual(set(self.runtime.agents), agent_ids)
        # The held seats exist on the host with no turn taken.
        for seat in (reviewer, curator):
            agent = self.runtime.agents[seat["agentId"]]
            self.assertIsNone(agent["archivedAt"])
            self.assertEqual(agent.get("received", []), [])

    def test_a_repeat_reconciles_the_same_three_agents(self) -> None:
        manager = binding("manager")
        first = start_leaf_set(
            self.config, set_call(uuid.uuid4()), environment=manager.environment()
        )
        request_id = uuid.UUID(first["requestId"])
        before = {
            role: first["seats"][role]["agentId"] for role in ("worker", "reviewer", "curator")
        }

        again = start_leaf_set(self.config, set_call(request_id), environment=manager.environment())

        self.assertEqual((again["ok"], again["status"]), (True, "completed"))
        after = {
            role: again["seats"][role]["agentId"] for role in ("worker", "reviewer", "curator")
        }
        self.assertEqual(after, before)
        self.assertEqual(len(self.runtime.agents), 3)
        self.assertEqual(
            {role: again["seats"][role]["status"] for role in before},
            {"worker": "running", "reviewer": "held", "curator": "held"},
        )

    def test_a_failed_seat_leaves_the_others_and_a_repeat_completes(self) -> None:
        # A transient creation failure (timeout: unknown outcome) leaves a retryable
        # seat; the repeat completes it without opening a held seat or a fourth agent.
        manager = binding("manager")
        request_id = uuid.uuid4()
        self.runtime.fail("agent-create", "paseo_bridge_timeout")

        partial = start_leaf_set(
            self.config, set_call(request_id), environment=manager.environment()
        )

        self.assertEqual((partial["ok"], partial["status"]), (False, "partial"))
        seats = partial["seats"]
        self.assertIsNone(seats["worker"]["agentId"])
        self.assertTrue(seats["reviewer"]["agentId"])
        self.assertTrue(seats["curator"]["agentId"])
        self.assertEqual(len(self.runtime.agents), 2)
        self.assertIn("Repeat role_start_set with the same request_id", partial["nextAction"])

        completed = start_leaf_set(
            self.config, set_call(request_id), environment=manager.environment()
        )

        self.assertEqual((completed["ok"], completed["status"]), (True, "completed"))
        agent_ids = {
            completed["seats"][role]["agentId"] for role in ("worker", "reviewer", "curator")
        }
        self.assertEqual(len(agent_ids), 3)
        # No fourth agent: the repeat completed the failed seat, and the held
        # seats never opened in between.
        self.assertEqual(len(self.runtime.agents), 3)
        self.assertTrue(completed["seats"]["reviewer"]["held"])
        self.assertTrue(completed["seats"]["curator"]["held"])
        self.assertEqual(self.received(completed["seats"]["reviewer"]["agentId"]), [])
        self.assertEqual(self.received(completed["seats"]["curator"]["agentId"]), [])

    def test_a_reviewer_message_opens_the_reviewer_at_once(self) -> None:
        _, result = self.manager_set(reviewer_message="Judge the first plan.")

        reviewer, curator = result["seats"]["reviewer"], result["seats"]["curator"]
        self.assertEqual(
            (reviewer["status"], reviewer["opened"], reviewer["held"]),
            ("running", True, False),
        )
        self.assertEqual(
            (curator["status"], curator["opened"], curator["held"]),
            ("held", False, True),
        )

    def test_a_first_message_opens_a_held_seat_and_leaves_the_other_held(self) -> None:
        manager, result = self.manager_set()
        reviewer_id = result["seats"]["reviewer"]["agentId"]
        curator_id = result["seats"]["curator"]["agentId"]

        opened = self.message(manager, agent_id=reviewer_id, text="Judge the first plan.")

        self.assertEqual((opened["ok"], opened["status"]), (True, "accepted"))
        self.assertTrue(opened["openedHeldSeat"])
        self.assertEqual(self.received(curator_id), [])
        curator = self.receipt_for("curator")
        self.assertEqual(curator["status"], "held")

    def test_a_single_role_repeat_of_a_held_seat_reports_held_and_opens_no_turn(self) -> None:
        manager, result = self.manager_set()
        reviewer_id = result["seats"]["reviewer"]["agentId"]
        seat_id = seat_request_id(uuid.UUID(result["requestId"]), "reviewer")
        self.runtime.calls.clear()

        repeat = self.start(manager, "reviewer", request_id=seat_id, **selection_of("reviewer"))

        self.assertEqual((repeat["ok"], repeat["status"]), (False, "held"))
        self.assertEqual(repeat["agentId"], reviewer_id)
        self.assertNotIn("agent-create", [command for command, _ in self.runtime.calls])
        self.assertEqual(self.received(reviewer_id), [])

    def test_the_set_says_for_each_seat_whether_the_second_copy_is_loaded(self) -> None:
        """MIK-R132 rule 5 for a set: read from each seat's receipt, as a single start reads it."""
        manager, first = self.manager_set()

        # The fixture's harness is handed no exclusion by the launch: every seat carries both.
        self.assertEqual(loaded(first), {"worker": True, "reviewer": True, "curator": True})
        # A seat whose receipt holds the record of the host's mode carries one copy. The repeat
        # reads it there and starts nothing.
        path = self.receipt_path_for("reviewer")
        receipt = {
            **self.receipt_for("reviewer"),
            "serverExclusion": launch_server_exclusion("claude"),
        }
        path.write_text(json.dumps(receipt), encoding="utf-8")

        again = start_leaf_set(
            self.config, set_call(uuid.UUID(first["requestId"])), environment=manager.environment()
        )

        self.assertEqual(loaded(again), {"worker": True, "reviewer": False, "curator": True})
        self.assertEqual(len(self.runtime.agents), 3)
        self.assertLessEqual(count_response_tokens(again), bound_for("role_start_set"))

    def test_an_architect_set_is_refused_with_no_effects(self) -> None:
        architect = binding("architect")

        refused = start_leaf_set(
            self.config, set_call(uuid.uuid4()), environment=architect.environment()
        )

        self.assertEqual(
            (refused["ok"], refused["status"], refused["refusal"]),
            (False, "refused", "role-may-not-start-role"),
        )
        self.assertNotIn("seats", refused)
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(self.runtime.agents, {})
        # Single-role permission is unchanged: the Architect still starts a Worker.
        single = self.start(architect, "worker")
        self.assertEqual(single["status"], "running")

    def test_a_wrong_master_set_is_refused_with_no_effects(self) -> None:
        stranger = binding("manager", master_ref=OTHER_MASTER)

        refused = start_leaf_set(
            self.config, set_call(uuid.uuid4()), environment=stranger.environment()
        )

        self.assertEqual((refused["ok"], refused["status"]), (False, "refused"))
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(self.runtime.agents, {})

    def receipt_path_for(self, role) -> Path:
        """Where the receipt of one seat of this line's leaf selection lies."""

        selection = RoleSelection(
            role=role,
            sprintDocumentRef=SPRINT_REF,
            masterDocumentRef=MASTER_REF,
            taskDocumentRef=LEAF_REF,
        )
        return _receipt_path(self.config, selection)

    def receipt_for(self, role) -> dict:
        """The current receipt of one seat of this line's leaf selection."""

        receipt = _read_receipt(self.receipt_path_for(role))
        assert receipt is not None
        return receipt


if __name__ == "__main__":
    unittest.main()
