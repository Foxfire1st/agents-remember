"""The first message of every allowed start, role by role (MIK-R193).

The combinations all come from the code rather than from a list here:

* role: every key of ``ROLE_START_OPERATIONS``;
* harness: every id of ``HARNESS_IDS`` and every id the settings fixture adds;
* parentless start: the selections of ``PARENTLESS_START_SELECTIONS``;
* parented start: the pairs of ``MAY_START``;
* seat set and held seat opening: for ``LEAF_ROLES``;
* successor: for every role outside ``TASKLESS_ROLES``;
* step-up: for every role, with a ``stepUp`` row for it in the fixture's settings;
* restore of a saved launch: for every role.

The built combinations are counted in each test and asserted against the code's own tables; a
failure names the role, the harness, the kind and the broken check. The world and the checks are
in :mod:`role_first_message_support`.
"""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.role_capsules import launch as role_capsules_launch
from agents_remember.application.role_launch_context import LEAF_ROLES, TASKLESS_ROLES
from agents_remember.application.skill_resources import capsule as skill_resources_capsule
from agents_remember.cli import role_launch_routes
from agents_remember.cli.paseo_role_tools import MAY_START, start_leaf_set
from agents_remember.cli.role_brief_message import SHARED_BLOCK_HEADING
from agents_remember.cli.role_launch_preparation import ROLE_START_OPERATIONS
from agents_remember.models.role_agents import RoleStartSetCall, SeatLaunchOverride
from agents_remember.models.role_launcher import RoleDispatchRequest
from role_first_message_support import FIXTURE_HARNESSES, FirstMessageMatrixBase


class FirstMessageMatrixTests(FirstMessageMatrixBase):
    # One test per kind of start.

    def test_the_shared_block_comes_from_the_compilation_on_both_start_paths(self) -> None:
        """An admitted core source reaches both real first-message paths exactly once."""

        block = (
            "Every role reads the charter, the invariants and the route index first.\n"
            "Keep the task's authority and evidence together.\n"
        )
        heading = f"## {SHARED_BLOCK_HEADING}\n\n{block}\n\n"
        with self.composition_corpus(block):
            for role, title, kind in (
                ("worker", "# Worker", "task-bound"),
                ("architect", "# Architect", "taskless"),
            ):
                harness = FIXTURE_HARNESSES[0]
                with self.subTest(role=role, kind=kind):
                    request = self.request(role, harness)
                    role_launch_routes._start_execution(self.config, request)
                    receipt = self.receipt(request)
                    message = self.sent_prompt(request)
                    self.check_message(message, receipt, role=role, harness=harness, kind=kind)
                    self.release(receipt)
                    self.assertEqual(message.count(f"## {SHARED_BLOCK_HEADING}\n\n"), 1)
                    self.assertEqual(message.count(heading), 1)
                    self.assertEqual(message.count(block), 1)
                    self.assertIn(f"{heading}{title}", message)

    def test_an_undeclared_shared_source_writes_no_block_on_both_start_paths(self) -> None:
        """A legacy manifest with no shared source retains the existing first message."""

        with self.composition_corpus(None):
            for role, title, kind in (
                ("worker", "# Worker", "task-bound"),
                ("architect", "# Architect", "taskless"),
            ):
                harness = FIXTURE_HARNESSES[0]
                with self.subTest(role=role, kind=kind):
                    request = self.request(role, harness)
                    role_launch_routes._start_execution(self.config, request)
                    receipt = self.receipt(request)
                    message = self.sent_prompt(request)
                    self.check_message(message, receipt, role=role, harness=harness, kind=kind)
                    self.release(receipt)
                    self.assertNotIn(f"## {SHARED_BLOCK_HEADING}\n\n", message)
                    self.assertIn(title, message)

    @contextmanager
    def composition_corpus(self, block: str | None) -> Iterator[None]:
        """Supply real corpus bytes, leaving admission and compilation untouched."""

        with TemporaryDirectory(dir=self.root) as temporary:
            corpus = Path(temporary) / "corpus"
            with role_capsules_launch.shipped_composition_corpus() as (root, manifest):
                shutil.copytree(root, corpus)
            manifest_path = corpus / manifest
            composition = json.loads(manifest_path.read_text(encoding="utf-8"))
            for role in composition["roles"].values():
                role["core"] = [name for name in role["core"] if name != "every-role"]
            source = corpus / "core/every-role.md"
            if block is None:
                composition["core"].pop("every-role", None)
                source.unlink(missing_ok=True)
            else:
                source.write_text(block, encoding="utf-8")
                composition["core"]["every-role"] = {
                    "source": "core/every-role.md",
                    "purpose": "The shared obligations each role receives in its first message.",
                }
                for role in composition["roles"].values():
                    role["core"].append("every-role")
            manifest_path.write_text(json.dumps(composition), encoding="utf-8")

            @contextmanager
            def shipped_corpus() -> Iterator[tuple[Path, str]]:
                yield corpus, manifest

            with (
                patch.object(role_capsules_launch, "shipped_composition_corpus", shipped_corpus),
                patch.object(skill_resources_capsule, "shipped_composition_corpus", shipped_corpus),
            ):
                yield

    def test_the_system_specialist_alias_carries_the_projection(self) -> None:
        """The alias role starts for a taskless seat, and its projection is the investigator's."""

        built = 0
        for harness in FIXTURE_HARNESSES:
            with self.subTest(harness=harness):
                request = RoleDispatchRequest.model_validate(
                    {
                        "role": "system-specialist",
                        "requestId": uuid.uuid4(),
                        "sprintDocumentRef": self.sprint_ref,
                        "agentOverride": {"agentId": harness},
                    }
                )
                role_launch_routes._start_execution(self.config, request)
                receipt = self.receipt(request)
                self.check_message(
                    self.sent_prompt(request),
                    receipt,
                    role="system-specialist",
                    harness=harness,
                    kind="parentless",
                )
                self.release(receipt)
                built += 1
        self.assertEqual(built, len(FIXTURE_HARNESSES))

    def test_parentless_starts_carry_the_projection(self) -> None:
        built = 0
        for role in ROLE_START_OPERATIONS:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request, receipt = self.start(role, harness)
                    self.check_message(
                        self.sent_prompt(request),
                        receipt,
                        role=role,
                        harness=harness,
                        kind="parentless",
                    )
                    self.release(receipt)
                    built += 1
        self.assertEqual(built, len(ROLE_START_OPERATIONS) * len(FIXTURE_HARNESSES))

    def test_parented_starts_carry_the_projection(self) -> None:
        built = 0
        for caller, targets in MAY_START.items():
            for role in targets:
                for harness in FIXTURE_HARNESSES:
                    with self.subTest(caller=caller, role=role, harness=harness):
                        request = self.request(role, harness)
                        role_launch_routes._start_execution(
                            self.config, request, self.starter(caller)
                        )
                        self.check_message(
                            self.sent_prompt(request),
                            self.receipt(request),
                            role=role,
                            harness=harness,
                            kind=(
                                "parented leaf seat"
                                if role in LEAF_ROLES
                                else "parented above leaf"
                            ),
                        )
                        self.release(self.receipt(request))
                        built += 1
        self.assertGreater(built, 0)

    def test_a_held_seat_opens_with_a_sane_first_message(self) -> None:
        built = 0
        for role in sorted(LEAF_ROLES):
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request = self.request(role, harness)
                    role_launch_routes._start_execution(self.config, request, held=True)
                    receipt = self.receipt(request)
                    self.assertIs(receipt.get("heldSeat"), True)
                    # A held seat's opening is not sent by its start; the delivery is what
                    # carries it, and that test owns the snapshot.
                    self.check_message(
                        receipt["heldOpeningPrompt"],
                        receipt,
                        role=role,
                        harness=harness,
                        kind="held seat opening",
                        expect_snapshot=False,
                    )
                    self.release(receipt)
                    built += 1
        self.assertEqual(built, len(LEAF_ROLES) * len(FIXTURE_HARNESSES))

    def test_a_started_set_gives_each_seat_a_sane_first_message(self) -> None:
        built = 0
        manager = AgentBinding(
            agent_id=str(uuid.uuid4()),
            role="manager",
            request_id=str(uuid.uuid4()),
            report_path="/reports/manager.md",
            sprint_ref=self.sprint_ref,
            master_ref=self.master_ref,
        )
        for harness in FIXTURE_HARNESSES:
            with self.subTest(harness=harness):
                call = RoleStartSetCall(
                    request_id=uuid.uuid4(),
                    sprint_document_ref=self.sprint_ref,
                    master_document_ref=self.master_ref,
                    task_document_ref=self.leaf_ref,
                    worker=SeatLaunchOverride(agent=harness),
                    reviewer=SeatLaunchOverride(agent=harness),
                    curator=SeatLaunchOverride(agent=harness),
                )
                answer = start_leaf_set(self.config, call, environment=manager.environment())
                self.assertTrue(answer["ok"], answer)
                for role in sorted(LEAF_ROLES):
                    request = RoleDispatchRequest.model_validate(
                        {
                            "role": role,
                            "requestId": role_launch_routes.seat_request_id(call.request_id, role),
                            **self.refs_of(role),
                            "agentOverride": {"agentId": harness},
                        }
                    )
                    receipt = self.receipt(request)
                    held = receipt.get("heldSeat") is True
                    message = receipt["heldOpeningPrompt"] if held else self.sent_prompt(request)
                    # A held seat has not been sent to, so it has no send to verify here; its
                    # delivery is the check that owns its snapshot.
                    self.check_message(
                        message,
                        receipt,
                        role=role,
                        harness=harness,
                        kind="seat set",
                        expect_snapshot=not held,
                    )
                    self.release(receipt)
                    built += 1
        self.assertGreater(built, 0)

    def test_a_successor_names_the_seat_it_takes_over(self) -> None:
        built = 0
        for role in [name for name in ROLE_START_OPERATIONS if name not in TASKLESS_ROLES]:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    _first_request, first = self.start(role, harness)
                    self.runtime.agents[first["execution"]["agentId"]]["archivedAt"] = (
                        "2026-10-21T10:30:00.000Z"
                    )
                    request, receipt = self.start(role, harness)
                    message = self.sent_prompt(request)
                    self.assertIn("Successor start:", message)
                    self.check_message(
                        message, receipt, role=role, harness=harness, kind="successor"
                    )
                    self.release(receipt)
                    built += 1
        self.assertGreater(built, 0)

    def test_a_step_up_names_the_material_it_takes_over(self) -> None:
        built = 0
        for role in ROLE_START_OPERATIONS:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request, receipt = self.start(
                        role, harness, stepUp="The last round needs a stronger model"
                    )
                    message = self.sent_prompt(request)
                    self.assertIn("Step-up:", message)
                    self.check_message(message, receipt, role=role, harness=harness, kind="step-up")
                    self.release(receipt)
                    built += 1
        self.assertGreater(built, 0)

    def test_a_restored_launch_sends_the_saved_message_and_files(self) -> None:
        built = 0
        for role in ROLE_START_OPERATIONS:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request = self.request(role, harness)
                    self.runtime.fail("agent-create", "paseo_daemon_unreachable")
                    role_launch_routes._start_execution(self.config, request)
                    saved = self.receipt(request)["replayRequest"]["agent"]["prompt"]
                    for name in ("handoverArtifact", "handoverFile"):
                        Path(self.receipt(request)[name]["canonicalPath"]).unlink(missing_ok=True)
                    role_launch_routes._start_execution(self.config, request)
                    resent = [
                        call[1]["prompt"]
                        for call in self.runtime.calls
                        if call[0] == "agent-create"
                    ][-1]
                    self.assertEqual(resent, saved)
                    self.check_message(
                        saved,
                        self.receipt(request),
                        role=role,
                        harness=harness,
                        kind="restore",
                    )
                    self.release(self.receipt(request))
                    built += 1
        self.assertGreater(built, 0)


if __name__ == "__main__":
    unittest.main()
