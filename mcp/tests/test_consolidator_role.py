"""The Consolidator as a role: who starts it, its settings rows, and its leaf's seats.

A Consolidator is started where a Worker may be: by the Architect, by an Orchestrator under its
own sprint and by the Manager of its master. The settings take its row and the optional
``consolidation-reviewer`` row, which the Reviewer of a consolidation task lays over its own row
field by field. These cases run the product's start path against the fake host the role-tool
tests use; the capsule compile is the fixture's, so no instruction text is read.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from _user_settings_guard import user_settings_path_for_write
from agents_remember.cli import role_launch_preparation
from agents_remember.cli.leaf_seat_handover import leaf_seats
from agents_remember.cli.role_launch_defaults import role_defaults
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.serving.paseo.paseo_plugin_files import read_role_agents
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from paseo_launch_test_support import CATALOG
from test_paseo_role_tools import OTHER_SPRINT, RoleToolsTestCase, binding

# Every row the settings took before the Consolidator, and the two it adds.
ROLE_ROWS = (
    "architect",
    "orchestrator",
    "manager",
    "worker",
    "curator",
    "reviewer",
    "investigator",
    "bootstrap",
    "review-presenter",
    "consolidator",
    "consolidation-reviewer",
)


REVIEWER_ROW = {"harness": "codex", "model": "gpt-a", "effort": "low"}


def write_user_settings(orchestration: dict[str, Any]) -> None:
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"orchestration": orchestration}), encoding="utf-8")


class ConsolidatorTestCase(RoleToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Two more models, so the rows and a start can each name a model of their own.
        catalog = deepcopy(CATALOG)
        models = catalog["providers"][0]["models"]
        models.extend({**models[0], "id": name, "label": name} for name in ("gpt-b", "gpt-c"))
        self.runtime._catalog = lambda _payload: catalog

    def make_consolidation_task(self) -> None:
        """The leaf's document names the list of a pass before any seat starts."""

        listed = TaskDocument.model_validate(
            {
                **self.leaf.document.model_dump(mode="json", by_alias=True, exclude_none=True),
                "consolidationList": {"rules": ["INV-KYKYPR"]},
            }
        )
        self.leaf = ResolvedTaskDocument(ref=self.leaf.ref, path=self.leaf.path, document=listed)
        write_task_doc(self.leaf.path.parent, listed)

    def created(self) -> dict[str, Any]:
        """The newest agent-create request the host received."""

        return [payload for command, payload in self.runtime.calls if command == "agent-create"][-1]


class ConsolidatorStartTests(ConsolidatorTestCase):
    def assert_starts_a_consolidator(self, caller: str) -> None:
        started = self.start(binding(caller), "consolidator")
        self.assertEqual((started["ok"], started["status"]), (True, "running"))
        runtime = self.config.paseo_runtime
        assert runtime is not None
        self.assertEqual(read_role_agents(runtime.home)[started["agentId"]]["role"], "consolidator")

    def test_the_architect_starts_a_consolidator(self) -> None:
        self.assert_starts_a_consolidator("architect")

    def test_an_orchestrator_starts_a_consolidator_under_its_own_sprint(self) -> None:
        self.assert_starts_a_consolidator("orchestrator")

    def test_the_manager_of_its_master_starts_a_consolidator(self) -> None:
        self.assert_starts_a_consolidator("manager")

    def test_a_role_that_may_not_start_a_worker_may_not_start_a_consolidator(self) -> None:
        cases = {
            "worker": "A worker may start no role; it may not start a consolidator.",
            "reviewer": "A reviewer may start no role; it may not start a consolidator.",
            "orchestrator of another sprint": "An orchestrator may start roles only on "
            f"selections under its own sprint ({OTHER_SPRINT.key})",
        }
        callers = {
            "worker": binding("worker"),
            "reviewer": binding("reviewer"),
            "orchestrator of another sprint": binding("orchestrator", sprint_ref=OTHER_SPRINT),
        }
        for name, detail in cases.items():
            with self.subTest(caller=name):
                refused = self.start(callers[name], "consolidator")
                self.assertFalse(refused["ok"])
                self.assertIn(detail, refused["detail"])
        self.assertEqual(self.runtime.agents, {})


class ConsolidationSettingsRowTests(ConsolidatorTestCase):
    """The rows a start reads, through the product's own role defaults and launch choice."""

    def setUp(self) -> None:
        super().setUp()
        self.replace(
            role_launch_preparation,
            "_role_defaults",
            side_effect=lambda config, context: role_defaults(config, context, {}),
        )

    def test_the_settings_take_every_row_they_took_and_the_two_new_ones(self) -> None:
        rows: dict[str, dict[str, Any]] = {
            row: {"harness": "codex", "model": "gpt-a"} for row in ROLE_ROWS
        }
        # The Reviewer's refinement is no confined capability: it takes extra arguments too.
        rows["consolidation-reviewer"] = {"harness": "claude", "extraArgs": {"trace": "on"}}
        rows["consolidator"]["stepUp"] = {"model": "gpt-b", "afterTries": 2}
        write_user_settings(
            {
                "roles": rows,
                "rolesPerLevel": {"leaf": {"consolidation-reviewer": {"effort": "high"}}},
            }
        )

        settings = load_agentic_settings(self.config.coordination_root)

        self.assertEqual(set(settings.roles), set(ROLE_ROWS))
        consolidator_step = settings.roles["consolidator"].step_up
        assert consolidator_step is not None
        self.assertEqual(consolidator_step.after_tries, 2)
        self.assertEqual(settings.resolved_role_knobs("consolidation-reviewer").effort, "high")
        write_user_settings({"roles": {"consolidation-reviewr": {"model": "gpt-a"}}})
        with self.assertRaisesRegex(AgenticSettingsError, "consolidation-reviewr"):
            load_agentic_settings(self.config.coordination_root)

    def test_a_consolidator_takes_its_own_row(self) -> None:
        write_user_settings(
            {
                "roles": {
                    "worker": {"harness": "codex", "model": "gpt-a", "effort": "low"},
                    "consolidator": {"harness": "codex", "model": "gpt-b", "effort": "high"},
                }
            }
        )
        self.make_consolidation_task()

        self.start(self.architect, "consolidator")

        self.assertEqual(
            (self.created()["model"], self.created()["thinkingOptionId"]), ("gpt-b", "high")
        )

    def reviewer_start(self, rows: dict[str, Any], **choices: Any) -> tuple[str, str | None]:
        """Start the leaf's Reviewer under these rows; the model and effort the host was given."""

        write_user_settings({"roles": rows})
        started = self.start(self.architect, "reviewer", **choices)
        self.assertEqual(started["status"], "running")
        return self.created()["model"], self.created().get("thinkingOptionId")

    def test_an_ordinary_tasks_reviewer_reads_its_own_row_only(self) -> None:
        rows = {"reviewer": REVIEWER_ROW, "consolidation-reviewer": {"effort": "high"}}
        self.assertEqual(self.reviewer_start(rows), ("gpt-a", "low"))

    def test_the_reviewer_of_a_consolidation_task_takes_the_new_row_field_by_field(self) -> None:
        # Started before any Consolidator: the document alone says the task is a consolidation.
        # The row names an effort only, so the model is the Reviewer's.
        self.make_consolidation_task()
        rows = {"reviewer": REVIEWER_ROW, "consolidation-reviewer": {"effort": "high"}}
        self.assertEqual(self.reviewer_start(rows), ("gpt-a", "high"))

    def test_an_empty_row_gives_the_reviewers_row(self) -> None:
        self.make_consolidation_task()
        rows = {"reviewer": REVIEWER_ROW, "consolidation-reviewer": {}}
        self.assertEqual(self.reviewer_start(rows), ("gpt-a", "low"))

    def test_a_model_named_at_the_start_wins_over_both_rows(self) -> None:
        self.make_consolidation_task()
        rows = {"reviewer": REVIEWER_ROW, "consolidation-reviewer": {"model": "gpt-b"}}
        self.assertEqual(self.reviewer_start(rows, agent="codex", model="gpt-c")[0], "gpt-c")


def test_a_consolidation_tasks_seats_message_only_each_other(tmp_path) -> None:
    """The hand-over's ``leafSeats`` name the seats the leaf's document gives it."""

    from test_paseo_launch import LEAF_REF, MASTER_REF, SPRINT_REF  # noqa: PLC0415

    base = {
        "id": "01_LEAF",
        "slug": "01_leaf",
        "title": "Pass",
        "kind": "subTask",
        "repo": "agents-remember",
        "createdAt": "2026-10-02T00:00:00+00:00",
        "status": "inProgress",
        "master": "task.json",
    }
    selection = {
        "sprintDocumentRef": SPRINT_REF.model_dump(mode="json"),
        "masterDocumentRef": MASTER_REF.model_dump(mode="json"),
    }
    path = tmp_path / "01_leaf.json"
    ordinary = ResolvedTaskDocument(LEAF_REF, path, TaskDocument.model_validate(base))
    listed = ResolvedTaskDocument(
        LEAF_REF,
        path,
        TaskDocument.model_validate({**base, "consolidationList": {"families": ["FAM-0BVSZB"]}}),
    )

    seats = leaf_seats("consolidator", listed, selection)

    assert seats["roles"] == ["consolidator", "reviewer"]
    assert list(seats["roleMessageArguments"]) == ["reviewer"]
    assert seats["roleMessageArguments"]["reviewer"]["task_document_ref"] == {
        "repository": LEAF_REF.repository,
        "path": LEAF_REF.path,
    }
    assert leaf_seats("reviewer", listed, selection)["roleMessageArguments"].keys() == {
        "consolidator"
    }
    assert leaf_seats("worker", ordinary, selection)["roles"] == ["worker", "reviewer", "curator"]
