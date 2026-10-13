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
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, cast
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import terminal_tools
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    RoleKnobs,
    agentic_settings_path,
    load_agentic_settings,
    merge_settings,
    user_agentic_settings_path,
)
from agents_remember.kernel.coordination_context.settings import parse_coordination_settings
from agents_remember.kernel.harnesses import HARNESSES
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.recorded_reads import bytes_identity, recorded_reads


def write_settings(root: Path, data: dict, *, user: bool = True) -> Path:
    path = user_agentic_settings_path() if user else agentic_settings_path(root)
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

    def test_local_leaf_overrides_global_leaf_and_siblings_survive(self) -> None:
        write_settings(
            self.coordination_root,
            {
                "orchestration": {
                    "spawn": {"harness": "codex"},
                    "loops": {"defaults": {"maxRounds": 3, "reviewerReuse": "delta-verify"}},
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
            user=False,
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
                user_agentic_settings_path(),
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

        self.assertIsNone(settings.gate_policy.rule_for("closeout-approval").delegated_role)
        self.assertFalse(settings.gate_delegation_configured)
        self.assertEqual(settings.loops.defaults.max_rounds, 5)
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
        self.assertIsNone(settings.quality_gate.memory_cap_bytes)
        self.assertEqual(settings.role_message_limit, 1000)
        self.assertEqual(settings.sources, ())


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
            user=False,
        )

        with self.assertRaisesRegex(AgenticSettingsError, "global-layer only") as caught:
            load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertIn(str(local_path), str(caught.exception))

    def test_malformed_json_fails_loud_with_path(self) -> None:
        path = user_agentic_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(
            AgenticSettingsError, "cannot parse agentic settings JSON"
        ) as caught:
            load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertIn(str(path), str(caught.exception))

    def test_review_round_limit_takes_any_positive_setting_and_refuses_non_positive(
        self,
    ) -> None:
        """MIK-R116: the settings name the review limit; no hard cap stays.

        A file with ``maxRounds`` 4 loads (global and local layers); zero, a
        negative number and a fraction are refused at load, naming the field.
        """
        global_path = write_settings(
            self.coordination_root,
            {"orchestration": {"loops": {"defaults": {"maxRounds": 4}}}},
        )
        self.assertEqual(load_agentic_settings(self.coordination_root).loops.defaults.max_rounds, 4)

        global_path.unlink()
        write_settings(
            self.repo_root,
            {"orchestration": {"loops": {"defaults": {"maxRounds": 99}}}},
            user=False,
        )
        self.assertEqual(
            load_agentic_settings(self.coordination_root, self.repo_root).loops.defaults.max_rounds,
            99,
        )

        for bad in (0, -2, 2.5):
            with self.subTest(maxRounds=bad):
                write_settings(
                    self.coordination_root,
                    {"orchestration": {"loops": {"defaults": {"maxRounds": bad}}}},
                )
                with self.assertRaisesRegex(
                    AgenticSettingsError,
                    r"orchestration\.loops\.defaults\.maxRounds",
                ):
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

    def test_human_pinned_gate_kind_cannot_be_delegated(self) -> None:
        with self.assertRaisesRegex(AgenticSettingsError, "human-pinned"):
            self._load({"gateDelegation": {"kinds": {"push-approval": {"role": "manager"}}}})

    def test_investigator_settings_aliases_and_equal_dual_keys_preserve_file(self) -> None:
        for family in ("roles", "rolesPerLevel"):
            for equal_dual_keys in (False, True):
                with self.subTest(family=family, equal_dual_keys=equal_dual_keys):
                    roles = {"system-specialist": {"harness": "codex", "model": "selected"}}
                    if equal_dual_keys:
                        roles["investigator"] = dict(roles["system-specialist"])
                    value = roles if family == "roles" else {"portfolio": roles}
                    path = write_settings(
                        self.coordination_root, {"orchestration": {family: value}}
                    )
                    before = path.read_bytes()
                    with warnings.catch_warnings(record=True) as notices:
                        warnings.simplefilter("always")
                        settings = load_agentic_settings(self.coordination_root)
                    self.assertEqual(len(notices), 1)
                    notice = str(notices[0].message)
                    owner = (
                        "orchestration.roles"
                        if family == "roles"
                        else "orchestration.rolesPerLevel.portfolio"
                    )
                    self.assertIn(f"{owner}.system-specialist", notice)
                    self.assertIn(str(path), notice)
                    self.assertIn("investigator", notice)
                    for role in ("investigator", "system-specialist"):
                        self.assertEqual(
                            settings.resolved_role_knobs(role, "portfolio"),
                            RoleKnobs(harness="codex", model="selected"),
                        )
                    self.assertEqual(path.read_bytes(), before)

    def test_different_dual_role_keys_refuse_with_both_keys_and_source(self) -> None:
        for family in ("roles", "rolesPerLevel"):
            with self.subTest(family=family):
                roles = {"system-specialist": {"model": "old"}, "investigator": {"model": "new"}}
                value = roles if family == "roles" else {"master": roles}
                path = write_settings(self.coordination_root, {"orchestration": {family: value}})
                with self.assertRaises(AgenticSettingsError) as refused:
                    load_agentic_settings(self.coordination_root)
                owner = (
                    "orchestration.roles"
                    if family == "roles"
                    else "orchestration.rolesPerLevel.master"
                )
                for text in (f"{owner}.system-specialist", f"{owner}.investigator", str(path)):
                    self.assertIn(text, str(refused.exception))

    def test_alias_layers_merge_by_knob_with_one_notice_per_load(self) -> None:
        repo_root = self.coordination_root.parent / "repo"
        global_path = write_settings(
            self.coordination_root,
            {
                "orchestration": {
                    "roles": {"system-specialist": {"harness": "codex", "model": "global"}}
                }
            },
        )
        local_path = write_settings(
            repo_root,
            {
                "orchestration": {
                    "roles": {"investigator": {"model": "local"}},
                    "rolesPerLevel": {"portfolio": {"system-specialist": {"effort": "high"}}},
                }
            },
            user=False,
        )
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always")
            settings = load_agentic_settings(self.coordination_root, repo_root)
        self.assertEqual(len(notices), 1)
        self.assertIn(str(global_path), str(notices[0].message))
        self.assertIn(str(local_path), str(notices[0].message))
        self.assertEqual(set(settings.roles), {"investigator"})
        self.assertEqual(
            settings.resolved_role_knobs("system-specialist", "portfolio"),
            RoleKnobs(harness="codex", model="local", effort="high"),
        )


class RolesPerLevelTests(unittest.TestCase):
    """260703-L16 (ruling 2026-07-07T08:15): orchestration.rolesPerLevel -- per-LEVEL role knob
    overrides deep-merging over the flat orchestration.roles defaults at leaf-key granularity,
    with the loops.perLevel level vocabulary (leaf|master|portfolio)."""

    # The developer's canonical reviewer economics: cheap per leaf, smarter at the master seam,
    # smartest + workflows for the portfolio end-to-end.
    ECONOMICS: ClassVar[dict] = {
        "roles": {"reviewer": {"harness": "claude", "model": "sonnet", "effort": "high"}},
        "rolesPerLevel": {
            "master": {"reviewer": {"model": "opus", "effort": "xhigh"}},
            "portfolio": {"reviewer": {"model": "fable", "effort": "ultracode"}},
        },
    }

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.coordination_root = Path(self.tmp.name) / "ar-coordination"
        self.coordination_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _load(self, orchestration: dict):
        write_settings(self.coordination_root, {"orchestration": orchestration})
        return load_agentic_settings(self.coordination_root)

    def test_level_override_deep_merges_over_the_flat_default(self) -> None:
        settings = self._load(self.ECONOMICS)
        # leaf: no override -- the flat default applies untouched.
        self.assertEqual(
            settings.resolved_role_knobs("reviewer", "leaf"),
            RoleKnobs(harness="claude", model="sonnet", effort="high"),
        )
        # master: model/effort overridden, harness INHERITED from the flat default.
        self.assertEqual(
            settings.resolved_role_knobs("reviewer", "master"),
            RoleKnobs(harness="claude", model="opus", effort="xhigh"),
        )
        # portfolio: the smartest set, harness still inherited.
        self.assertEqual(
            settings.resolved_role_knobs("reviewer", "portfolio"),
            RoleKnobs(harness="claude", model="fable", effort="ultracode"),
        )


class HarnessesFamilyTests(unittest.TestCase):
    """260703-L16 registry openness: orchestration.harnesses extends/overrides the builtin registry
    (new ids add, builtin ids pre-customize), with fail-loud shapes, delivery-vehicle pair rules,
    and harness references validated against the EFFECTIVE id set."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        self.repo_root = root / "workspace" / "repo-a"
        self.coordination_root.mkdir(parents=True)
        self.repo_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _load(self, orchestration: dict, local: dict | None = None):
        write_settings(self.coordination_root, {"orchestration": orchestration})
        if local is not None:
            write_settings(self.repo_root, {"orchestration": local}, user=False)
        return load_agentic_settings(
            self.coordination_root, self.repo_root if local is not None else None
        )

    def test_new_id_adds_a_harness_with_defaults_derived(self) -> None:
        settings = self._load({"harnesses": {"hermes": {"command": "hermes"}}})
        hermes = settings.find_harness("hermes")
        assert hermes is not None
        self.assertEqual(hermes.argv, ("hermes",))
        self.assertEqual(hermes.name, "hermes")
        self.assertEqual(hermes.defined_in, "settings")
        # Builtin order is preserved; new ids append. The builtin prefix is read from the curated
        # table rather than transcribed, so the assertion cannot pass after a silent reorder.
        expected = [harness.id for harness in HARNESSES] + ["hermes"]
        self.assertEqual([h.id for h in settings.harnesses], expected)

    def test_a_builtin_override_keeps_its_runtime_readiness_probe(self) -> None:
        # eve is detected through a runtime readiness probe, not a PATH lookup. A settings override
        # customizes the launch mapping; it must not downgrade detection to ``which`` over a command
        # that was never a PATH program.
        settings = self._load({"harnesses": {"eve": {"argv": ["custom-eve", "--flag"]}}})
        eve = settings.find_harness("eve")
        assert eve is not None
        builtin = next(harness for harness in HARNESSES if harness.id == "eve")
        self.assertIsNotNone(builtin.runtime_probe)
        self.assertEqual(eve.runtime_probe, builtin.runtime_probe)
        self.assertEqual(eve.argv, ("custom-eve", "--flag"))
        # A settings-defined id declares no runtime of its own, so it has no probe to inherit.
        added = self._load({"harnesses": {"hermes": {"command": "hermes"}}}).find_harness("hermes")
        assert added is not None
        self.assertIsNone(added.runtime_probe)

    def test_cross_layer_reference_and_partial_override_merge(self) -> None:
        # The GLOBAL layer declares hermes; the LOCAL layer may reference it and override a single
        # leaf (per-file validation is shape-only; cross-references bind on the MERGED block).
        settings = self._load(
            {"harnesses": {"hermes": {"command": "hermes", "argv": ["hermes", "--global"]}}},
            local={
                "harnesses": {"hermes": {"argv": ["hermes", "--local"]}},
                "spawn": {"harness": "hermes"},
            },
        )
        hermes = settings.find_harness("hermes")
        assert hermes is not None
        self.assertEqual(hermes.argv, ("hermes", "--local"))
        self.assertEqual(settings.spawn_harness, "hermes")

    def test_reference_to_an_id_known_nowhere_fails_naming_the_manual(self) -> None:
        with self.assertRaisesRegex(AgenticSettingsError, "harnesses.md"):
            self._load({"spawn": {"harness": "hermes"}})


class RoleMessageLimitSettingsTests(unittest.TestCase):
    """``orchestration.roleMessageLimit``: the bound on one message text (MIK-R117)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        self.repo_root = root / "workspace" / "repo-a"
        self.coordination_root.mkdir(parents=True)
        self.repo_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_absent_number_means_one_thousand(self) -> None:
        write_settings(self.coordination_root, {"orchestration": {}})
        settings = load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertEqual(settings.role_message_limit, 1000)

    def test_a_positive_whole_number_is_taken_and_the_local_layer_wins(self) -> None:
        write_settings(self.coordination_root, {"orchestration": {"roleMessageLimit": 600}})
        write_settings(self.repo_root, {"orchestration": {"roleMessageLimit": 700}}, user=False)
        settings = load_agentic_settings(self.coordination_root, self.repo_root)
        self.assertEqual(settings.role_message_limit, 700)

    def test_zero_negative_or_no_whole_number_is_refused_naming_the_field(self) -> None:
        for bad in (0, -3, 1.5, "many", True):
            with self.subTest(bad=bad):
                write_settings(
                    self.coordination_root,
                    {"orchestration": {"roleMessageLimit": bad}},
                )
                with self.assertRaisesRegex(AgenticSettingsError, "orchestration.roleMessageLimit"):
                    load_agentic_settings(self.coordination_root, self.repo_root)


class QualityGateSettingsTests(unittest.TestCase):
    """``orchestration.qualityGate``: optional hard cap inside the Dagger graph."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        self.coordination_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_executor_authority_is_not_accepted_in_agentic_settings(self) -> None:
        path = write_settings(
            self.coordination_root,
            {"orchestration": {"qualityGate": {"executor": "dagger"}}},
        )

        with self.assertRaisesRegex(AgenticSettingsError, "executor") as caught:
            load_agentic_settings(self.coordination_root)
        self.assertIn(str(path), str(caught.exception))


class ServiceTierSettingsTests(unittest.TestCase):
    def test_tier_inherits_independently_of_model_and_effort(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            global_root, local_root = Path(folder) / "global", Path(folder) / "local"
            write_settings(
                global_root,
                {
                    "orchestration": {
                        "roles": {
                            "architect": {
                                "harness": "codex",
                                "model": "gpt-6.1-sol",
                                "effort": "xhigh",
                                "serviceTier": "fast",
                            }
                        }
                    }
                },
            )
            write_settings(
                local_root,
                {
                    "orchestration": {
                        "roles": {"architect": {"effort": "max"}},
                        "rolesPerLevel": {"portfolio": {"architect": {"serviceTier": "default"}}},
                    }
                },
                user=False,
            )
            settings = load_agentic_settings(global_root, local_root)
            role = settings.role_knobs("architect")
            self.assertEqual(
                (role.model, role.effort, role.service_tier), ("gpt-6.1-sol", "max", "fast")
            )
            scoped = settings.resolved_role_knobs("architect", "portfolio")
            self.assertEqual(
                (scoped.model, scoped.effort, scoped.service_tier),
                ("gpt-6.1-sol", "max", "default"),
            )
            self.assertIsNone(settings.role_knobs("worker").service_tier)

    def test_tier_refuses_invalid_shapes_and_fast_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for value in [True, False, 1, [], {}, None, "", " "]:
                with self.subTest(value=value):
                    write_settings(
                        root, {"orchestration": {"roles": {"architect": {"serviceTier": value}}}}
                    )
                    with self.assertRaisesRegex(AgenticSettingsError, "serviceTier"):
                        load_agentic_settings(root)
            write_settings(root, {"orchestration": {"roles": {"architect": {"fast": True}}}})
            with self.assertRaisesRegex(AgenticSettingsError, "fast"):
                load_agentic_settings(root)

    def test_legacy_terminal_route_refuses_configured_tier_instead_of_dropping_it(self) -> None:

        settings = SimpleNamespace(
            resolved_role_knobs=lambda *_args: RoleKnobs(service_tier="fast")
        )
        with patch.object(terminal_tools, "load_agentic_settings", return_value=settings):
            dispatch, refusal = terminal_tools._resolve_harness_dispatch(
                cast(McpRuntimeConfig, SimpleNamespace(coordination_root=Path("/unused"))),
                task_document_ref=None,
                level=None,
                env={"AR_SPAWN_ROLE": "architect"},
                which=None,
            )
        self.assertIsNone(dispatch)
        self.assertIn("serviceTier", cast(dict[str, str], refusal)["detail"])


class UserSettingsLocationTests(unittest.TestCase):
    def test_user_file_is_read_per_use_and_repository_file_keeps_its_location(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            coordination, repo = Path(folder) / "coord", Path(folder) / "repo"
            user = write_settings(
                coordination,
                {
                    "orchestration": {
                        "roles": {
                            "worker": {
                                "harness": "codex",
                                "mode": "auto",
                                "notes": "user instructions",
                            }
                        }
                    }
                },
            )
            local = write_settings(
                repo, {"orchestration": {"roles": {"worker": {"effort": "high"}}}}, user=False
            )
            first = load_agentic_settings(coordination, repo)
            self.assertEqual(first.sources, (user, local))
            self.assertEqual(
                (
                    first.role_knobs("worker").mode,
                    first.role_knobs("worker").notes,
                    first.role_knobs("worker").effort,
                ),
                ("auto", "user instructions", "high"),
            )
            user.write_text('{"orchestration":{"roles":{"worker":{"mode":"read-only"}}}}')
            self.assertEqual(
                load_agentic_settings(coordination, repo).role_knobs("worker").mode, "read-only"
            )
            self.assertFalse(agentic_settings_path(coordination).exists())

    def test_default_home_and_explicit_xdg_select_only_their_own_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "sandbox-home"
            inherited = user_agentic_settings_path()
            inherited.parent.mkdir(parents=True, exist_ok=True)
            inherited.write_text('{"orchestration":{"roles":{"worker":{"model":"outside"}}}}')
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("pathlib.Path.home", return_value=home),
            ):
                path = user_agentic_settings_path()
                self.assertEqual(path, home / ".config/agents-remember/settings.json")
                path.parent.mkdir(parents=True)
                path.write_text('{"orchestration":{"roles":{"worker":{"model":"sandbox"}}}}')
                self.assertEqual(
                    load_agentic_settings(Path(folder) / "coord").role_knobs("worker").model,
                    "sandbox",
                )
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(folder) / "xdg")}):
                    self.assertEqual(
                        user_agentic_settings_path(),
                        Path(folder) / "xdg/agents-remember/settings.json",
                    )
                    self.assertIsNone(
                        load_agentic_settings(Path(folder) / "coord").role_knobs("worker").model
                    )

    def test_old_only_and_both_files_refuse_before_any_settings_content_read(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            coordination = Path(folder) / "coord"
            old = agentic_settings_path(coordination)
            new = user_agentic_settings_path()
            old.parent.mkdir(parents=True)
            old.write_text("not JSON and never parsed")
            for both in (False, True):
                if both:
                    new.parent.mkdir(parents=True, exist_ok=True)
                    new.write_text("{}")
                with (
                    self.subTest(both=both),
                    patch("agents_remember.kernel.agentic_settings._read_settings_file") as read,
                ):
                    with self.assertRaisesRegex(
                        AgenticSettingsError, "location-upgrade-required"
                    ) as caught:
                        load_agentic_settings(coordination)
                    self.assertIn(str(old), str(caught.exception))
                    self.assertIn(str(new), str(caught.exception))
                    self.assertIn(
                        "agents-remember upgrade-user-settings --coordination-root",
                        str(caught.exception),
                    )
                    read.assert_not_called()
            self.assertEqual(old.read_text(), "not JSON and never parsed")

    def test_global_topology_fallback_reads_user_bytes_and_memory_repo_bytes_stay_local(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            coordination = Path(folder) / "coord"
            notes = coordination / "system/settings.md"
            new = user_agentic_settings_path()
            new.parent.mkdir(parents=True, exist_ok=True)
            data = b'{"storage":{"mode":"memory-repo"},"crossRepo":{"allow":[]}}'
            new.write_bytes(data)
            with recorded_reads() as reads:
                global_storage, _ = parse_coordination_settings(
                    notes, coordination_root=coordination
                )
            self.assertEqual(global_storage.mode, "memory-repo")
            self.assertEqual(reads[str(new)], bytes_identity(data))
            self.assertNotIn(str(agentic_settings_path(coordination)), reads)
            memory = Path(folder) / "memory-repos/ar-repo/system/settings.md"
            memory.parent.mkdir(parents=True)
            memory.with_suffix(".json").write_text('{"storage":{"mode":"disabled"}}')
            local_storage, _ = parse_coordination_settings(memory, coordination_root=coordination)
            self.assertEqual(local_storage.mode, "disabled")


if __name__ == "__main__":
    unittest.main()
