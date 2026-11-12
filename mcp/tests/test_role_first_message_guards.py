"""The negative controls of the first-message checks themselves (MIK-R193 rules 1, 6, 7).

Each case fails the check under test when the check is blind: a duplicate the projection keeps,
a name nothing holds, a path cut at a space, and a held delivery whose snapshot is missing. The
world and the checks are in :mod:`role_first_message_support`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agents_remember.application.role_launch_context import LEAF_ROLES
from agents_remember.cli import role_held_messages, role_launch_routes
from agents_remember.cli.role_held_messages import HeldMessage
from role_first_message_support import (
    FIXTURE_HARNESSES,
    FirstMessageMatrixBase,
    _message_key,
    _missing_paths,
    _projection_of,
    _unreachable,
)


class FirstMessageMatrixTests(FirstMessageMatrixBase):
    def test_the_repeat_check_exempts_only_the_bootstrap_pair(self) -> None:
        """B21: only the admitted Bootstrap role/operation pair may hold one value twice."""
        pair = {"role": "bootstrap", "operation": "bootstrap"}
        self.check_nothing_twice("", pair, "the admitted pair")
        controls = (pair | {"task": "bootstrap"}, {"role": "worker", "operation": "worker"})
        for control in controls:
            self.assertRaises(AssertionError, self.check_nothing_twice, "", control, "control")

    def test_the_reachability_check_refuses_a_name_that_is_nowhere(self) -> None:
        """Rule 7's oracle answers for a file-only root, an index and a name that is nowhere.

        A check that only recognised projected roots would pass a brief naming a field nothing
        holds; these controls fail that check and pass the two the rule allows.
        """

        request, receipt = self.start("worker", FIXTURE_HARNESSES[0])
        handover = self.recorded.of(receipt)
        message = self.sent_prompt(request)
        _head, _heading, projection_part = message.rpartition(
            "AR owner assignment and canonical task handover:\n"
        )
        projection, _end = json.JSONDecoder().raw_decode(projection_part)
        self.release(receipt)
        # A name that no field of the hand-over object starts: not a hand-over reference at all.
        self.assertEqual(_unreachable("`role_start` and `stop`", projection, handover), [])
        # A field of the file the projection leaves out: reachable through the file.
        self.assertEqual(_unreachable("`contextPacket`", projection, handover), [])
        # An indexed path through the projected documents list: reachable in the projection.
        self.assertEqual(_unreachable("`documents[0].taskDocReadArgs`", projection, handover), [])
        # A file-only root with a part the object does not have, and a projected root likewise.
        for name in ("capsule.missing", "contextPacket.missing", "workspace.missing"):
            self.assertEqual(
                _unreachable(f"see `{name}`", projection, handover),
                [name],
                f"{name}: the reachability check did not report a name that is nowhere",
            )

    def test_the_path_check_reads_a_whitespace_path_whole_and_runs_at_send(self) -> None:
        """B07: a path holding a space is checked as written, and the check runs at the send.

        The negative control the Reviewer reproduced: a message says to read a backticked path
        whose *prefix* exists on disk and whose complete text does not. A check that stops at the
        space accepts it; this one reports the whole path. The second control shows the same check
        running on the message handed to the host, not on a copy read back afterwards.
        """

        folder = self.root / "whitespace"
        folder.mkdir(parents=True, exist_ok=True)
        prefix = folder / "report"
        prefix.write_text("a real file at the prefix only\n", encoding="utf-8")
        complete = f"{prefix} long name.md"
        self.assertTrue(Path(str(prefix)).exists())
        self.assertFalse(Path(complete).exists())
        message = f"Read `{complete}` before you answer.\n"
        self.assertEqual(
            _missing_paths(message, {}),
            [complete],
            "the path check cut a whitespace path at the space and accepted its prefix",
        )
        # The same message, handed to the host: the check records it at the send boundary.
        self._check_paths_at_send("agent-create", {"prompt": message})
        self.assertEqual(
            self.send_boundary_missing.get(_message_key(message)),
            [complete],
            "the path check did not run on the message the host was handed",
        )
        # And a launch with no snapshot at all is refused rather than read as clean.
        with self.assertRaises(AssertionError):
            self.check_paths("no such launch\n", {"requestId": "no-such-request"}, "control")

        # A held delivery is sent as the saved opening plus the waiting work. The check of the
        # opening must find the snapshot of that actual send — so a required path missing from
        # the work, or from the opening, is a failure of the delivery and not a clean read.
        request = self.request("worker", FIXTURE_HARNESSES[0])
        role_launch_routes._start_execution(self.config, request, held=True)
        receipt = self.receipt(request)
        opening = receipt["heldOpeningPrompt"]
        missing_task = str(self.root / "no-such-task-document.json")
        sent = f"{opening}\nReport your role, operation and first read.\nRead `{missing_task}` before you answer.\n"
        self._check_paths_at_send("agent-create", {"prompt": sent})
        projection = _projection_of(opening)
        self.assertIn(projection["requestId"], self.send_boundary_missing)
        with self.assertRaises(AssertionError):
            self.check_paths(opening, projection, "held delivery control")
        # The clean delivery of the same opening passes, which keeps the control negative.
        self._check_paths_at_send(
            "agent-create",
            {"prompt": f"{opening}\nReport your role, operation and first read.\nGo.\n"},
        )
        self.check_paths(opening, projection, "held delivery positive control")
        self.release(self.receipt(request))

    def test_a_held_seat_opens_through_the_delivery_path(self) -> None:
        """Rule 7's held-opening cells drive the real opener, not a stored string.

        For every leaf role and harness the seat is started held, one message is taken into its
        store, and the delivery path opens it. What the host receives is the saved opening plus
        the waiting work, so the opening itself is what rules 1, 3, 5, 6 and 7 are checked on,
        and rule 6's duplicate check covers that opening alone, as it does for any first message.
        """

        built = 0
        for role in sorted(LEAF_ROLES):
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    where = f"{role}, {harness}, held delivery"
                    request = self.request(role, harness)
                    role_launch_routes._start_execution(self.config, request, held=True)
                    receipt_path = self.receipt_path(request)
                    receipt = self.receipt(request)
                    self.assertIs(receipt.get("heldSeat"), True)
                    role_held_messages.hold_message(
                        receipt["workspace"]["path"],
                        receipt["agentId"],
                        HeldMessage(
                            sender_line="From manager · fixture · agent sender-fixture",
                            sender_agent_id="sender-fixture",
                            text="Report your role, operation and first read.",
                            reason="not-running",
                        ),
                    )
                    outcome = role_held_messages.drain_held(self.config, receipt_path, receipt)
                    self.assertEqual(outcome["outcome"], "opened", f"{where}: {outcome}")
                    # The held start creates the seat without a message; the open is the
                    # create that carries one.
                    opened = [
                        call[1]["prompt"]
                        for call in self.runtime.calls
                        if call[0] == "agent-create" and "prompt" in call[1]
                    ][-1]
                    opening = receipt["heldOpeningPrompt"]
                    self.assertTrue(
                        opened.startswith(opening),
                        f"{where}: the delivered turn does not begin with the saved opening",
                    )
                    self.assertEqual(
                        opened.count("Report your role, operation and first read."),
                        1,
                        f"{where}: the waiting work does not stand once in the delivered turn",
                    )
                    self.check_message(
                        opening,
                        self.receipt(request),
                        role=role,
                        harness=harness,
                        kind="held delivery",
                    )
                    self.release(self.receipt(request))
                    built += 1
        self.assertEqual(built, len(LEAF_ROLES) * len(FIXTURE_HARNESSES))


if __name__ == "__main__":
    unittest.main()
