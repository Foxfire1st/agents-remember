"""Tests for the two-layer agentic settings loader (260703-L13).

Covers the merge semantics (global-only, local-only, override, array-replace),
the fail-loud unknown-key rule scoped to ``orchestration.*`` (naming the
offending file; unknown TOP-LEVEL families are tolerated so future families --
contextProviders first in line -- are not foreclosed), absent-file defaults,
malformed-JSON refusal, and the typed models (loops / roles / concurrency /
spawn harness preference / gateDelegation).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    RoleKnobs,
    agentic_settings_path,
    default_agentic_settings_seed,
    load_agentic_settings,
    merge_settings,
)


def write_settings(root: Path, data: dict) -> Path:
    path = agentic_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


class MergePrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        self.repo_root = root / "workspace" / "repo-a"
        self.coordination_root.mkdir(parents=True)
        self.repo_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_global_only(self) -> None:
        write_settings(
            self.coordination_root,
            {"orchestration": {"spawn": {"harness": "codex"}}},
        )

        settings = load_agentic_settings(self.coordination_root, self.repo_root)

        self.assertEqual(settings.spawn_harness, "codex")
        self.assertEqual(
            settings.sources, (agentic_settings_path(self.coordination_root),)
        )

    def test_local_only(self) -> None:
        write_settings(
            self.repo_root,
            {"orchestration": {"roles": {"worker": {"harness": "pi"}}}},
        )

        settings = load_agentic_settings(self.coordination_root, self.repo_root)

        self.assertEqual(settings.roles["worker"].harness, "pi")
        self.assertEqual(settings.sources, (agentic_settings_path(self.repo_root),))

    def test_local_leaf_overrides_global_leaf_and_siblings_survive(self) -> None:
        write_settings(
            self.coordination_root,
            {
                "orchestration": {
                    "spawn": {"harness": "codex"},
                    "loops": {"defaults": {"maxRounds": 5, "reviewerReuse": "delta-verify"}},
                    "concurrency": {"maxParallelLeaves": 3},
                }
            },
        )
        write_settings(
            self.repo_root,
            {
                "orchestration": {
                    "spawn": {"harness": "pi"},
                    "loops": {"defaults": {"maxRounds": 2}},
                }
            },
        )

        settings = load_agentic_settings(self.coordination_root, self.repo_root)

        # Leaf-key granularity: the local maxRounds wins while the global
        # reviewerReuse sibling and the untouched concurrency family survive.
        self.assertEqual(settings.spawn_harness, "pi")
        self.assertEqual(settings.loops.defaults.max_rounds, 2)
        self.assertEqual(settings.loops.defaults.reviewer_reuse, "delta-verify")
        self.assertEqual(settings.concurrency.max_parallel_leaves, 3)
        self.assertEqual(
            settings.sources,
            (
                agentic_settings_path(self.coordination_root),
                agentic_settings_path(self.repo_root),
            ),
        )

    def test_arrays_replace_never_concatenate(self) -> None:
        merged = merge_settings(
            {"kinds": {"a": [1, 2, 3]}, "other": [1]},
            {"kinds": {"a": [9]}},
        )

        self.assertEqual(merged, {"kinds": {"a": [9]}, "other": [1]})

    def test_absent_files_mean_documented_defaults(self) -> None:
        settings = load_agentic_settings(self.coordination_root, self.repo_root)

        self.assertIsNone(
            settings.gate_policy.rule_for("closeout-approval").delegated_role
        )
        self.assertFalse(settings.gate_delegation_configured)
        self.assertEqual(settings.loops.defaults.max_rounds, 3)
        self.assertEqual(settings.loops.defaults.reviewer_reuse, "delta-verify")
        self.assertEqual(settings.loops.defaults.complexity.full_loop_at, "high")
        self.assertEqual(settings.loops.defaults.complexity.builder_at, "medium")
        self.assertEqual(
            settings.loops.per_level,
            {"leaf": "scored", "master": "seam-required", "portfolio": "strategist"},
        )
        self.assertEqual(settings.loops.per_master, {})
        self.assertEqual(settings.roles, {})
        self.assertIsNone(settings.concurrency.max_parallel_masters)
        self.assertIsNone(settings.concurrency.max_parallel_leaves)
        self.assertIsNone(settings.concurrency.max_sub_agents)
        self.assertIsNone(settings.spawn_harness)
        self.assertEqual(settings.sources, ())

    def test_repo_root_is_optional(self) -> None:
        write_settings(
            self.coordination_root, {"orchestration": {"spawn": {"harness": "claude"}}}
        )

        settings = load_agentic_settings(self.coordination_root)

        self.assertEqual(settings.spawn_harness, "claude")


class FailLoudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        self.repo_root = root / "workspace" / "repo-a"
        self.coordination_root.mkdir(parents=True)
        self.repo_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unknown_orchestration_key_names_the_offending_file(self) -> None:
        path = write_settings(
            self.coordination_root, {"orchestration": {"lopps": {}}}
        )

        with self.assertRaisesRegex(AgenticSettingsError, "lopps") as caught:
            load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertIn(str(path), str(caught.exception))

    def test_unknown_key_in_local_file_names_the_local_file(self) -> None:
        write_settings(
            self.coordination_root, {"orchestration": {"spawn": {"harness": "codex"}}}
        )
        local_path = write_settings(
            self.repo_root, {"orchestration": {"spawn": {"harnes": "pi"}}}
        )

        with self.assertRaisesRegex(AgenticSettingsError, "harnes") as caught:
            load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertIn(str(local_path), str(caught.exception))

    def test_local_gate_delegation_is_refused_global_layer_only(self) -> None:
        """L13 review follow-up (L13R-2): a repo-local gateDelegation would validate
        and then silently do nothing (the boot snapshot reads the global file only)
        -- a fail-open shape. The loader refuses it loudly, naming the local file."""
        write_settings(
            self.coordination_root,
            {"orchestration": {"gateDelegation": {"policy": "all-human"}}},
        )
        local_path = write_settings(
            self.repo_root,
            {"orchestration": {"gateDelegation": {"policy": "all-human"}}},
        )

        with self.assertRaisesRegex(AgenticSettingsError, "global-layer only") as caught:
            load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertIn(str(local_path), str(caught.exception))

    def test_unknown_top_level_family_is_tolerated_not_parsed(self) -> None:
        """The fail-loud scope is orchestration.* ONLY (gate amendment 2026-07-06).

        A future top-level family in the same file -- contextProviders is
        earmarked to return here -- must not be foreclosed, while a typo'd
        orchestration.* key still fails loud.
        """
        write_settings(
            self.coordination_root,
            {
                "$comment": "header",
                "version": 1,
                "contextProviders": {"enabled": True},
                "onboarding": {"storage": {"mode": "memory-repo"}},
                "orchestration": {"spawn": {"harness": "codex"}},
            },
        )

        settings = load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertEqual(settings.spawn_harness, "codex")

        write_settings(
            self.coordination_root,
            {
                "contextProviders": {"enabled": True},
                "orchestration": {"spwan": {"harness": "codex"}},
            },
        )
        with self.assertRaisesRegex(AgenticSettingsError, "spwan"):
            load_agentic_settings(self.coordination_root, self.repo_root)

    def test_malformed_json_fails_loud_with_path(self) -> None:
        path = agentic_settings_path(self.coordination_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(
            AgenticSettingsError, "cannot parse agentic settings JSON"
        ) as caught:
            load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertIn(str(path), str(caught.exception))

    def test_non_object_root_fails_loud(self) -> None:
        path = agentic_settings_path(self.coordination_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2]", encoding="utf-8")

        with self.assertRaisesRegex(AgenticSettingsError, "must be a JSON object"):
            load_agentic_settings(self.coordination_root)

    def test_unknown_gate_delegation_key_fails_loud(self) -> None:
        write_settings(
            self.coordination_root,
            {"orchestration": {"gateDelegation": {"polcy": "all-human"}}},
        )

        with self.assertRaisesRegex(AgenticSettingsError, "polcy"):
            load_agentic_settings(self.coordination_root)

    def test_unknown_loop_defaults_key_fails_loud(self) -> None:
        write_settings(
            self.coordination_root,
            {"orchestration": {"loops": {"defaults": {"maxRound": 3}}}},
        )

        with self.assertRaisesRegex(AgenticSettingsError, "maxRound"):
            load_agentic_settings(self.coordination_root)

    def test_unknown_role_name_fails_loud(self) -> None:
        write_settings(
            self.coordination_root,
            {"orchestration": {"roles": {"wroker": {"harness": "codex"}}}},
        )

        with self.assertRaisesRegex(AgenticSettingsError, "wroker"):
            load_agentic_settings(self.coordination_root)

    def test_unknown_concurrency_cap_fails_loud(self) -> None:
        write_settings(
            self.coordination_root,
            {"orchestration": {"concurrency": {"maxParallelWorkers": 4}}},
        )

        with self.assertRaisesRegex(AgenticSettingsError, "maxParallelWorkers"):
            load_agentic_settings(self.coordination_root)


class TypedModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.coordination_root = Path(self.tmp.name) / "ar-coordination"
        self.coordination_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _load(self, orchestration: dict):
        write_settings(self.coordination_root, {"orchestration": orchestration})
        return load_agentic_settings(self.coordination_root)

    def test_parses_the_full_l12_loop_schema(self) -> None:
        settings = self._load(
            {
                "loops": {
                    "defaults": {
                        "maxRounds": 4,
                        "reviewerReuse": "delta-verify",
                        "complexity": {"fullLoopAt": "medium", "builderAt": "low"},
                    },
                    "perLevel": {
                        "leaf": {"loop": "scored"},
                        "master": {"loop": "none"},
                        "portfolio": {"loop": "strategist"},
                    },
                    "perMaster": {
                        "260703_agent-orchestration": {"leaf": {"loop": "builder-verified"}}
                    },
                }
            }
        )

        self.assertEqual(settings.loops.defaults.max_rounds, 4)
        self.assertEqual(settings.loops.defaults.complexity.full_loop_at, "medium")
        self.assertEqual(settings.loops.defaults.complexity.builder_at, "low")
        self.assertEqual(settings.loops.per_level["master"], "none")
        self.assertEqual(
            settings.loops.per_master,
            {"260703_agent-orchestration": {"leaf": "builder-verified"}},
        )

    def test_partial_per_level_keeps_the_other_level_defaults(self) -> None:
        settings = self._load({"loops": {"perLevel": {"master": {"loop": "none"}}}})

        self.assertEqual(settings.loops.per_level["master"], "none")
        self.assertEqual(settings.loops.per_level["leaf"], "scored")
        self.assertEqual(settings.loops.per_level["portfolio"], "strategist")

    def test_max_rounds_must_be_a_positive_integer(self) -> None:
        for bad in (0, -1, True, "3"):
            with self.assertRaisesRegex(AgenticSettingsError, "maxRounds"):
                self._load({"loops": {"defaults": {"maxRounds": bad}}})

    def test_complexity_thresholds_are_scale_validated(self) -> None:
        with self.assertRaisesRegex(AgenticSettingsError, "fullLoopAt"):
            self._load(
                {"loops": {"defaults": {"complexity": {"fullLoopAt": "extreme"}}}}
            )

    def test_role_knobs_parse_and_default(self) -> None:
        settings = self._load(
            {
                "roles": {
                    "worker": {"harness": "codex", "model": "gpt-5", "effort": "medium"},
                    "orchestrator": {"effort": "high"},
                }
            }
        )

        self.assertEqual(
            settings.roles["worker"],
            RoleKnobs(harness="codex", model="gpt-5", effort="medium"),
        )
        self.assertEqual(settings.roles["orchestrator"], RoleKnobs(effort="high"))
        # Unconfigured roles resolve to empty knobs (role-file defaults apply).
        self.assertEqual(settings.role_knobs("manager"), RoleKnobs())

    def test_role_harness_must_be_a_registry_id(self) -> None:
        with self.assertRaisesRegex(
            AgenticSettingsError, "harness registry id.*claude-code"
        ):
            self._load({"roles": {"worker": {"harness": "claude-code"}}})

    def test_spawn_harness_must_be_a_registry_id(self) -> None:
        with self.assertRaisesRegex(AgenticSettingsError, "harness registry id"):
            self._load({"spawn": {"harness": "gemini"}})

    def test_concurrency_caps_parse(self) -> None:
        settings = self._load(
            {
                "concurrency": {
                    "maxParallelMasters": 2,
                    "maxParallelLeaves": 3,
                    "maxSubAgents": 4,
                }
            }
        )

        self.assertEqual(settings.concurrency.max_parallel_masters, 2)
        self.assertEqual(settings.concurrency.max_parallel_leaves, 3)
        self.assertEqual(settings.concurrency.max_sub_agents, 4)

    def test_concurrency_caps_must_be_positive_integers(self) -> None:
        with self.assertRaisesRegex(AgenticSettingsError, "maxSubAgents"):
            self._load({"concurrency": {"maxSubAgents": 0}})

    def test_gate_delegation_parses_in_its_new_home(self) -> None:
        settings = self._load(
            {
                "gateDelegation": {
                    "policy": "manager-decides-leaf-gates",
                    "requireReviewerVerdictAtSeams": True,
                }
            }
        )

        self.assertTrue(settings.gate_delegation_configured)
        self.assertTrue(settings.require_reviewer_verdict_at_seams)
        policy = settings.gate_policy
        self.assertEqual(policy.rule_for("plan-approval").delegated_role, "manager")
        handover = policy.rule_for("master-handover-approval")
        self.assertEqual(handover.delegated_role, "orchestrator")
        self.assertTrue(handover.require_reviewer_verdict)

    def test_human_pinned_gate_kind_cannot_be_delegated(self) -> None:
        with self.assertRaisesRegex(AgenticSettingsError, "human-pinned"):
            self._load(
                {"gateDelegation": {"kinds": {"push-approval": {"role": "manager"}}}}
            )

    def test_unsupported_gate_kind_delegation_is_rejected(self) -> None:
        with self.assertRaisesRegex(AgenticSettingsError, "cannot be delegated"):
            self._load(
                {"gateDelegation": {"kinds": {"agent-question": {"role": "manager"}}}}
            )


class SeedTests(unittest.TestCase):
    def test_seed_parses_to_the_documented_defaults(self) -> None:
        """The runtime_install seed must round-trip through the loader to the
        same posture an ABSENT file yields (all-human gates, L12 loop defaults,
        no caps, no spawn preference)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            coordination_root = Path(tmp_dir) / "ar-coordination"
            coordination_root.mkdir(parents=True)
            write_settings(coordination_root, default_agentic_settings_seed())

            seeded = load_agentic_settings(coordination_root)
            absent = load_agentic_settings(Path(tmp_dir) / "elsewhere")

        self.assertEqual(seeded.gate_policy, absent.gate_policy)
        self.assertEqual(seeded.loops, absent.loops)
        self.assertEqual(seeded.roles, absent.roles)
        self.assertEqual(seeded.concurrency, absent.concurrency)
        self.assertIsNone(seeded.spawn_harness)
        # The seed EXPLICITLY configures gateDelegation (all-human) so the
        # boot-snapshot consumer treats the file as the key's home.
        self.assertTrue(seeded.gate_delegation_configured)


if __name__ == "__main__":
    unittest.main()
