"""MIK-R202@v2 rule 13: the landing-workflow setting and its one reader.

The value lives in a repository's memory settings, ``system/settings.json``, at the **root** - the
scope the settings contract already reads policy blocks from (``crossRepo``), storage being the only
block it normalizes through ``onboarding``. ``local`` is today's behavior and what a missing key
means; ``pull-request`` is the other implemented workflow; any other value is refused by name at the
parse, where it is read, so no later stage acts on a workflow nobody defined. This file also pins
that the storage/cross-repo funnel keeps its two-value contract: the reader is additive, not a
widening of the shape every existing caller unpacks.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.coordination_context.json_settings import (
    parse_json_landing_settings,
)
from agents_remember.kernel.coordination_context.models import (
    LANDING_WORKFLOW_LOCAL,
    LANDING_WORKFLOW_PULL_REQUEST,
)
from agents_remember.kernel.coordination_context.settings import (
    parse_coordination_settings,
    parse_landing_settings,
)
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.series_closeout import _landing_workflow
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _series_contract(memory_worktree: Path | None = None) -> WorktreeContract:
    """The real series type the closeout reader consumes, with no memory side by default."""

    root = Path("/nonexistent/l194-series")
    return WorktreeContract(
        kind="series",
        task_id="260928-MIK-L194",
        task_name="260928_maintained-invariant-knowledge",
        repo_name="agents-remember",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "enclosures" / "260928-mik-l194" / "series-contract.md",
        task_artifact=root / "tasks" / "260928_maintained-invariant-knowledge.md",
        worktree_group=root / "worktrees",
        code_repo_path=root / "repo",
        code_source_branch="main",
        code_work_branch="ar/194_probe",
        code_base_commit="a" * 40,
        code_worktree=root / "repo",
        memory_worktree=memory_worktree,
    )


class LandingWorkflowSettingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.settings = self.root / "system" / "settings.json"
        self.settings.parent.mkdir(parents=True, exist_ok=True)

    def write(self, data: dict, name: str = "settings.json") -> Path:
        path = self.settings.parent / name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def test_absent_settings_file_lands_locally(self) -> None:
        self.assertEqual(parse_landing_settings(self.settings).workflow, LANDING_WORKFLOW_LOCAL)

    def test_missing_key_lands_locally(self) -> None:
        self.write({"version": 2, "onboarding": {"storage": {"mode": "memory-repo"}}})
        self.assertEqual(parse_landing_settings(self.settings).workflow, LANDING_WORKFLOW_LOCAL)

    def test_pull_request_is_read_from_the_root(self) -> None:
        self.write({"version": 2, "landing": {"workflow": LANDING_WORKFLOW_PULL_REQUEST}})
        self.assertEqual(
            parse_landing_settings(self.settings).workflow, LANDING_WORKFLOW_PULL_REQUEST
        )

    def test_the_onboarding_wrapper_is_not_read_for_landing(self) -> None:
        # Held question, not an oversight: the settings contract reads policy blocks at the root
        # (crossRepo does), and only storage is normalized through onboarding. Accepting a second
        # location would add a precedence this contract never defined, so a value written inside
        # onboarding is not honoured - the repository lands locally, as it did before the setting.
        self.write({"version": 2, "onboarding": {"landing": {"workflow": "pull-request"}}})
        self.assertEqual(parse_landing_settings(self.settings).workflow, LANDING_WORKFLOW_LOCAL)

    def test_the_root_wins_over_a_wrapped_value_and_that_is_the_whole_rule(self) -> None:
        self.write(
            {
                "version": 2,
                "onboarding": {"landing": {"workflow": "local"}},
                "landing": {"workflow": "pull-request"},
            }
        )
        self.assertEqual(
            parse_landing_settings(self.settings).workflow, LANDING_WORKFLOW_PULL_REQUEST
        )

    def test_unknown_value_is_refused_by_name(self) -> None:
        # The underscore spelling is the one a reader would guess; it is not the implemented name.
        self.write({"version": 2, "landing": {"workflow": "pull_request"}})
        with self.assertRaises(ValueError) as caught:
            parse_landing_settings(self.settings)
        message = str(caught.exception)
        self.assertIn("pull_request", message)
        self.assertIn("local", message)
        self.assertIn("pull-request", message)

    def test_a_workflow_that_is_not_a_string_is_refused(self) -> None:
        self.write({"version": 2, "landing": {"workflow": 3}})
        with self.assertRaises(ValueError) as caught:
            parse_landing_settings(self.settings)
        self.assertIn("must be a string", str(caught.exception))

    def test_the_path_settings_file_supplies_the_value(self) -> None:
        # A coordination-rooted repository keeps its resolved settings beside the onboarding file;
        # that file is where the value is chosen, so it wins over the onboarding settings.
        self.write({"version": 2, "onboarding": {"storage": {"mode": "memory-repo"}}})
        self.write({"version": 2, "landing": {"workflow": "pull-request"}}, name="settings.json")
        onboarding = self.settings.parent / "settings.md"
        onboarding.write_text("# settings\n", encoding="utf-8")
        self.assertTrue(onboarding.with_suffix(".json").is_file())
        self.assertEqual(parse_landing_settings(onboarding).workflow, LANDING_WORKFLOW_PULL_REQUEST)

    def test_markdown_settings_alone_land_locally(self) -> None:
        onboarding = self.settings.parent / "settings.md"
        onboarding.write_text("# settings\n", encoding="utf-8")
        self.assertEqual(parse_landing_settings(onboarding).workflow, LANDING_WORKFLOW_LOCAL)

    def test_the_storage_and_cross_repo_funnel_keeps_its_two_value_contract(self) -> None:
        self.write({"version": 2, "landing": {"workflow": "pull-request"}})
        parsed = parse_coordination_settings(self.settings)
        self.assertIsInstance(parsed, tuple)
        self.assertEqual(len(parsed), 2)


class LandingWorkflowRefusalTests(unittest.TestCase):
    """L194-V2-002: an absent workflow defaults; a present-but-invalid one is refused by name.

    The defect this pins: reading ``landing.workflow`` with ``.get()`` made an explicit ``null``
    indistinguishable from an absent key, and the chain consumer caught the parser's refusal and
    substituted ``local``. Both turned a configured mistake into silent policy.
    """

    def _refuses(self, root: dict[str, object], *words: str) -> str:
        with self.assertRaises(ValueError) as caught:
            parse_json_landing_settings(root)
        message = str(caught.exception)
        for word in words:
            self.assertIn(word, message)
        return message

    def test_an_explicit_null_is_a_configured_value_and_is_refused(self) -> None:
        message = self._refuses(
            {"version": 1, "landing": {"workflow": None}}, "null", "pull-request"
        )
        self.assertIn("landing.workflow", message)

    def test_a_landing_block_without_the_key_still_means_local(self) -> None:
        self.assertEqual(parse_json_landing_settings({"landing": {}}).workflow, "local")

    def test_an_empty_string_is_refused_by_name_rather_than_read_as_absent(self) -> None:
        self._refuses({"landing": {"workflow": ""}}, "landing.workflow")

    def test_a_non_string_names_its_type(self) -> None:
        self._refuses({"landing": {"workflow": 7}}, "int", "pull-request")

    def test_the_chain_consumer_refuses_instead_of_substituting_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "system").mkdir()
            (root / "system" / "settings.json").write_text(
                json.dumps({"landing": {"workflow": None}}), encoding="utf-8"
            )
            series = _series_contract(root)
            with self.assertRaises(CloseoutQueueError) as caught:
                _landing_workflow(series)
        self.assertIn("landing-workflow-invalid", str(caught.exception))
        self.assertIn("pull-request", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
