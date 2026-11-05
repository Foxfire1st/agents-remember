"""D641 retained isolation failures: provisioning only, no native actor or live home."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from agent_evals.adapters.execution import (
    CODEX_ACTOR,
    HERMES_ACTOR,
    HERMES_GPT_ACTOR,
    HERMES_KIMI_ACTOR,
)
from agent_evals.adapters.native_trials import TrialSources, capture_trial_actor, native_candidates
from agent_evals.artifacts import EvidenceFile, json_digest, read_object, write_object
from agent_evals.fixtures import CaseBundle, release_fixture, reset_case
from agent_evals.trial_isolation import (
    hermes_home,
    path_snapshot,
    private_build,
    purge_hermes_credentials,
    sweep_hermes_credentials,
    trial_environment,
)
from agent_evals.trial_native_setup import import_native_sides
from agent_evals.trial_setup import _git_baseline, provision_batch, provision_trial
from agents_remember.errors import AuthorityError
from test_agent_eval_trial_setup import case_bundle


class IsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case = case_bundle(self.root / "case")
        self.candidate = self.root / "shared-candidate"
        python = self.candidate / "mcp/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        source = self.candidate / "mcp/src/agents_remember/source.py"
        source.parent.mkdir(parents=True)
        source.write_text("shared = True\n")
        self.hermes = self.root / "developer-hermes"
        self.hermes.mkdir()
        (self.hermes / "config.yaml").write_text(
            '{"model":{"provider":"opencode-go"},"memory":{"enabled":true}}'
        )
        (self.hermes / "skills").mkdir()
        (self.hermes / "skills/do-not-copy.md").write_text("developer skill")

    def provision(self):
        def info(definition):
            settings = read_object(Path(definition["args"][-1]))
            candidate = Path(definition["command"]).parents[3]
            return {
                "coordinationRoot": settings["coordinationRoot"],
                "workspaceRoot": settings["workspaceRoot"],
                "servingBuild": {
                    "sourceDigest": "sha256:real-test-binding",
                    "packageRoot": str(candidate / "mcp/src/agents_remember"),
                },
                "allowedRepoIds": ["code"],
            }

        with (
            patch.dict("os.environ", {"HERMES_HOME": str(self.hermes)}),
            patch("agent_evals.trial_setup.probe_tool_server", side_effect=info),
            patch("agent_evals.trial_setup._free_port", side_effect=[43201, 43202]),
        ):
            batch = provision_batch(self.root / "batch", self.candidate)
            trial = provision_trial(
                self.case,
                self.root / "trials",
                self.candidate,
                batch_host=batch,
            )
        self.addCleanup(lambda: trial.cleanup() if trial.fixture.root.exists() else None)
        return trial

    def test_retained_home_alias_is_replaced_by_trial_home(self):
        witness = read_object(Path(__file__).parent / "fixtures/d641-isolation-retained.json")[
            "rows"
        ][0]
        self.assertEqual(witness["binding"]["nativeHomes"]["hermes"], "/home/test/.hermes")
        self.assertEqual(witness["call"]["payload"]["name"], "skill_manage")
        self.assertTrue(json.loads(witness["results"][0]["payload"]["output"])["success"])
        trial = self.provision()
        self.assertNotEqual(trial.native_capture_roots["hermes"], self.hermes)
        self.assertTrue(trial.native_capture_roots["hermes"].is_relative_to(trial.fixture.root))
        self.assertEqual(
            Path(trial.environment()["HERMES_HOME"]), trial.native_capture_roots["hermes"]
        )
        self.assertFalse((trial.native_capture_roots["hermes"] / "skills/do-not-copy.md").exists())

    def test_retained_shared_checkout_write_cannot_change_candidate(self):
        witness = read_object(Path(__file__).parent / "fixtures/d641-isolation-retained.json")[
            "rows"
        ][1]
        result = json.loads(witness["results"][0]["payload"]["output"])
        self.assertIn(
            "/eval-code/d9e92fe7/mcp/src/agents_remember/reports/", result["resolved_path"]
        )
        self.assertFalse(
            Path(result["resolved_path"]).is_relative_to(witness["binding"]["projectRoot"])
        )
        trial = self.provision()
        self.assertNotEqual(trial.candidate_code_root, self.candidate)
        (trial.candidate_code_root / "mcp/src/agents_remember/source.py").write_text(
            "actor = True\n"
        )
        self.assertEqual(
            (self.candidate / "mcp/src/agents_remember/source.py").read_text(), "shared = True\n"
        )

    def test_provider_copy_excludes_developer_state_and_receipt_secrets(self):
        # Entirely synthetic: exercise preservation without reading a real home
        # or copying, printing, or hashing any real configuration credentials.
        developer: dict[str, Any] = {
            "model": {
                "provider": "opencode-go",
                "default": "different-model",
                "base_url": "https://stale-provider.invalid/v1",
                "api_key": "config-canary",
                "context_length": 400000,
                "future_model_option": {"endpoint": "https://future-provider.invalid/v1"},
            },
            "agent": {
                "max_turns": 150,
                "reasoning_effort": "low",
                "reasoning_overrides": {"fake-model": "high"},
                "personalities": {"fake-persona": "persona-canary"},
            },
            "compression": {"threshold": 0.75, "summary_model": "compression-canary"},
            "memory": {"enabled": True},
            "terminal": {"cwd": "/developer/checkout", "timeout": 91, "shell": "fake-shell"},
            "custom_providers": [{"name": "unrelated", "api_key": "unrelated-canary"}],
            "platform_toolsets": {
                "acp": ["terminal", "file", "skills"],
                "cli": ["delegation"],
            },
            "toolsets": ["delegation"],
            "delegation": {"max_iterations": 37},
            "lsp": {"enabled": False},
            "auxiliary": {"api_key": "auxiliary-canary"},
            "image_gen": {"api_key": "image-canary"},
            "stt": {"api_key": "stt-canary"},
            "web": {"api_key": "web-canary"},
            "mcp_servers": {"unrelated": {"command": "not-copied"}},
            "plugins": {"unrelated": "plugin-canary"},
        }
        (self.hermes / "config.yaml").write_text(json.dumps(developer))
        before = (self.hermes / "config.yaml").read_bytes()
        (self.hermes / ".env").write_text(
            "OPENCODE_GO_API_KEY=env-canary\n"
            "OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1\n"
            "OPENROUTER_API_KEY=other-route-canary\nOTHER_SECRET=excluded-canary\n"
        )
        (self.hermes / "state.db").write_text("developer sessions")
        target = self.root / "isolated-hermes"
        receipt = hermes_home(self.hermes, target, self.root / "actor")
        config = read_object(target / "config.yaml")
        provider, separator, model = HERMES_ACTOR.model.partition(":")
        self.assertEqual(separator, ":")
        expected = {key: value for key, value in developer.items() if key != "mcp_servers"}
        expected.update(
            model={"provider": provider, "default": model},
            terminal={**developer["terminal"], "cwd": str(self.root / "actor")},
            agent={**developer["agent"], "reasoning_effort": "max"},
            compression={**developer["compression"], "threshold": 0.40},
        )
        for key, value in expected.items():
            with self.subTest(setting=key):
                self.assertEqual(config.get(key), value)
        # Changing provider can make a resolver's provider-equality guard accept
        # an inherited foreign address. Rebuild the block; do not trim one key.
        for key in ("base_url", "api_key", "context_length", "future_model_option"):
            with self.subTest(inheritedModelKey=key):
                self.assertNotIn(key, config["model"])
        self.assertEqual(set(config), set(expected) | {"hooks", "hooks_auto_accept"})
        self.assertNotIn("mcp_servers", config)
        self.assertTrue(config["hooks_auto_accept"])
        self.assertEqual(config["hooks"]["pre_tool_call"][0]["matcher"], "^skill_manage$")
        self.assertEqual(receipt["skillPolicy"]["seedConfigException"], 1)
        self.assertEqual(
            receipt["modelBinding"],
            {
                "key": "model.default",
                "value": model,
                "provider": provider,
                "selectedModel": HERMES_ACTOR.model,
                "origin": "agent_evals.adapters.execution.HERMES_ACTOR",
            },
        )
        self.assertEqual(receipt["acpToolsets"]["seedConfigException"], 2)
        self.assertEqual(receipt["acpToolsets"]["key"], "platform_toolsets.acp")
        self.assertEqual(receipt["acpToolsets"]["value"], config["platform_toolsets"]["acp"])
        self.assertEqual(
            (target / ".env").read_text(),
            "OPENCODE_GO_API_KEY=env-canary\nOPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1\n",
        )
        self.assertEqual(sorted(p.name for p in target.iterdir()), [".env", "config.yaml", "hooks"])
        self.assertNotIn("canary", json.dumps(receipt))
        declarations = receipt["seatSettings"]["declaredFacts"]
        self.assertEqual(declarations["effort"]["outboundField"], "reasoning_effort")
        self.assertEqual(declarations["effort"]["value"], "max")
        self.assertEqual(declarations["maxTurns"], 150)
        self.assertEqual(declarations["compaction"]["sourceDefaultWindow"], 1_000_000)
        self.assertEqual(declarations["compaction"]["sourceInitialTriggerTokens"], 400_000)
        self.assertFalse(declarations["compaction"]["runtimeWindowObserved"])
        self.assertIn("no context", declarations["compaction"]["scope"])
        self.assertIsNone(declarations["personality"]["resolved"])
        self.assertTrue(declarations["delegateTask"]["absent"])
        self.assertEqual((self.hermes / "config.yaml").read_bytes(), before)
        self.assertEqual((self.hermes / "state.db").read_text(), "developer sessions")

    def test_kimi_environment_uses_go_seed_and_leaves_effort_to_provider(self):
        (self.hermes / ".env").write_text(
            "export OPENCODE_GO_API_KEY=kimi-route-canary\n"
            "OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1\n"
            "OPENROUTER_API_KEY=other-route-canary\n"
        )
        (self.hermes / "config.yaml").write_text(
            json.dumps(
                {
                    "model": {"provider": "opencode-go", "base_url": "https://wrong.invalid"},
                    "agent": {
                        "max_turns": 150,
                        "reasoning_effort": "max",
                        "reasoning_overrides": {"kimi-k3.1-pro": "max", "kimi": "max"},
                    },
                    "compression": {"threshold": 0.75, "max_attempts": 3},
                }
            )
        )
        fixture = reset_case(self.case, self.root / "kimi-trials")
        self.addCleanup(release_fixture, fixture)
        batch_environment = {
            key: str(self.root / "batch" / key)
            for key in (
                "XDG_DATA_HOME",
                "XDG_CACHE_HOME",
                "NPM_CONFIG_USERCONFIG",
                "NPM_CONFIG_GLOBALCONFIG",
                "NPM_CONFIG_CACHE",
            )
        }
        with patch.dict("os.environ", {"HERMES_HOME": str(self.hermes)}):
            environment, receipt = trial_environment(
                fixture, batch_environment, self.root / "batch", profile=HERMES_KIMI_ACTOR
            )
        target = Path(environment["HERMES_HOME"])
        config = read_object(target / "config.yaml")
        self.assertEqual(config["model"], {"provider": "opencode-go", "default": "kimi-k3.1-pro"})
        self.assertEqual(
            (target / ".env").read_text(),
            "OPENCODE_GO_API_KEY=kimi-route-canary\n"
            "OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1\n",
        )
        self.assertEqual(receipt["provider"], "opencode-go")
        self.assertEqual(receipt["credentialBorrowing"]["provider"], "opencode-go")
        self.assertEqual(receipt["modelBinding"]["selectedModel"], HERMES_KIMI_ACTOR.model)
        self.assertEqual(
            receipt["modelBinding"]["origin"],
            "agent_evals.adapters.execution.HERMES_KIMI_ACTOR",
        )
        self.assertEqual(config["agent"], {"max_turns": 150})
        self.assertEqual(config["compression"], {"threshold": 0.40, "max_attempts": 3})
        self.assertTrue(config["hooks_auto_accept"])
        self.assertEqual(config["hooks"]["pre_tool_call"][0]["matcher"], "^skill_manage$")
        facts = receipt["seatSettings"]["declaredFacts"]
        self.assertNotIn("catalogDeclaration", facts)
        self.assertFalse(facts["compaction"]["runtimeWindowObserved"])
        self.assertEqual(facts["compaction"]["absoluteTargetTokens"], 400_000)
        self.assertTrue(facts["compaction"]["nativeBindingPending"])
        self.assertNotIn("sourceInitialTriggerTokens", facts["compaction"])
        self.assertNotIn("outboundField", facts["effort"])
        self.assertEqual(facts["effort"]["policy"], "provider-default")
        self.assertEqual(facts["effort"]["parity"], "unresolved")
        self.assertEqual(receipt["seatSettings"]["reasoningEffort"], "provider-default")
        self.assertNotIn("canary", json.dumps(receipt))

    def test_configured_auxiliary_tasks_seed_only_their_required_provider_key(self):
        (self.hermes / ".env").write_text(
            "OPENCODE_GO_API_KEY=actor-canary\n"
            "OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1\n"
            "DEEPSEEK_API_KEY=guardian-canary\n"
            "DEEPSEEK_BASE_URL=https://foreign.invalid\n"
            "OTHER_SECRET=excluded-canary\n"
        )
        configurations = [
            {task: {"provider": "deepseek", "reasoning_effort": "low"}}
            for task in ("approval", "compression", "mcp")
        ]
        configurations.extend(
            [
                {
                    task: {"provider": "deepseek", "reasoning_effort": "high"}
                    for task in ("approval", "compression", "mcp")
                },
                {
                    "approval": {"provider": "other", "key_env": "OTHER_SECRET"},
                    "unrelated": {"provider": "deepseek"},
                },
            ]
        )
        for profile in (HERMES_ACTOR, HERMES_KIMI_ACTOR):
            for index, auxiliary in enumerate(configurations):
                with self.subTest(profile=profile.model, configuration=index):
                    (self.hermes / "config.yaml").write_text(json.dumps({"auxiliary": auxiliary}))
                    target = self.root / f"auxiliary-{profile.model}-{index}"
                    receipt = hermes_home(self.hermes, target, self.root, profile=profile)
                    expected_tasks = sorted(
                        task
                        for task in ("approval", "compression", "mcp")
                        if auxiliary.get(task, {}).get("provider") == "deepseek"
                    )
                    expected_names = ["OPENCODE_GO_API_KEY", "OPENCODE_GO_BASE_URL"]
                    if expected_tasks:
                        expected_names.append("DEEPSEEK_API_KEY")
                    self.assertEqual(
                        [
                            line.split("=", 1)[0]
                            for line in (target / ".env").read_text().splitlines()
                        ],
                        expected_names,
                    )
                    seed = receipt["credentialSeed"]
                    self.assertEqual(seed["configuredAuxiliaryTasks"], expected_tasks)
                    self.assertEqual(seed["requiredNames"], expected_names)
                    self.assertEqual(seed["copiedNames"], expected_names)
                    self.assertIn("provider selection, not file scope", seed["boundary"])
                    config = read_object(target / "config.yaml")
                    self.assertEqual(config["auxiliary"], auxiliary)
                    self.assertEqual(config["model"]["provider"], "opencode-go")
                    self.assertEqual(receipt["modelBinding"]["selectedModel"], profile.model)
                    self.assertTrue(config["hooks_auto_accept"])
                    self.assertEqual(
                        config["hooks"]["pre_tool_call"][0]["matcher"], "^skill_manage$"
                    )
                    self.assertNotIn("canary", json.dumps(receipt))

    def test_only_hermes_gpt_receives_an_empty_private_codex_home(self):
        real_codex = self.root / "developer-codex"
        real_codex.mkdir()
        (real_codex / "auth.json").write_text("synthetic-external-refresh-capability")
        batch_environment = {
            key: str(self.root / "batch" / key)
            for key in (
                "XDG_DATA_HOME",
                "XDG_CACHE_HOME",
                "NPM_CONFIG_USERCONFIG",
                "NPM_CONFIG_GLOBALCONFIG",
                "NPM_CONFIG_CACHE",
            )
        }
        profiles = (
            HERMES_ACTOR,
            HERMES_KIMI_ACTOR,
            HERMES_GPT_ACTOR,
            CODEX_ACTOR,
        )
        for index, profile in enumerate(profiles):
            with self.subTest(profile=profile.model):
                fixture = reset_case(self.case, self.root / f"codex-isolation-{index}")
                self.addCleanup(release_fixture, fixture)
                with (
                    patch.dict(
                        "os.environ",
                        {"HERMES_HOME": str(self.hermes), "CODEX_HOME": str(real_codex)},
                    ),
                    patch("agent_evals.trial_isolation.hermes_home", return_value={}),
                    patch("agent_evals.trial_isolation.credential_borrowing", return_value={}),
                ):
                    environment, _ = trial_environment(
                        fixture, batch_environment, self.root / "batch", profile=profile
                    )
                selected = Path(environment["CODEX_HOME"])
                if profile == HERMES_GPT_ACTOR:
                    self.assertEqual(selected, fixture.root / "code-home")
                    self.assertTrue(selected.is_dir())
                    self.assertEqual(list(selected.iterdir()), [])
                    self.assertEqual(selected.stat().st_mode & 0o777, 0o700)
                else:
                    self.assertEqual(selected, real_codex)
                    self.assertFalse((fixture.root / "code-home").exists())
        self.assertEqual(
            (real_codex / "auth.json").read_text(), "synthetic-external-refresh-capability"
        )

    def test_hermes_gpt_high_overlay_beats_inherited_model_override(self):
        (self.hermes / "config.yaml").write_text(
            json.dumps(
                {
                    "agent": {
                        "max_turns": 150,
                        "reasoning_effort": "max",
                        "reasoning_overrides": {
                            "gpt-6.1-sol-900k": "max",
                            "unrelated-model": "max",
                        },
                    },
                    "auxiliary": {"approval": {"provider": "deepseek", "reasoning_effort": "low"}},
                }
            )
        )
        (self.hermes / ".env").write_text(
            "OPENCODE_GO_API_KEY=unrelated-actor-canary\nDEEPSEEK_API_KEY=guardian-canary\n"
        )
        target = self.root / "gpt-home"
        receipt = hermes_home(self.hermes, target, self.root, profile=HERMES_GPT_ACTOR)
        config = read_object(target / "config.yaml")
        self.assertEqual(config["agent"]["reasoning_effort"], "high")
        self.assertEqual(
            config["agent"]["reasoning_overrides"],
            {"gpt-6.1-sol-900k": "high", "unrelated-model": "max"},
        )
        self.assertEqual(config["agent"]["max_turns"], 150)
        self.assertEqual(config["auxiliary"]["approval"]["reasoning_effort"], "low")
        self.assertEqual(
            config["model"], {"provider": "openai-codex", "default": "gpt-6.1-sol-900k"}
        )
        self.assertEqual((target / ".env").read_text(), "DEEPSEEK_API_KEY=guardian-canary\n")
        self.assertEqual(receipt["credentialSeed"]["requiredNames"], ["DEEPSEEK_API_KEY"])
        self.assertTrue(config["hooks_auto_accept"])
        self.assertEqual(config["hooks"]["pre_tool_call"][0]["matcher"], "^skill_manage$")
        facts = receipt["seatSettings"]["declaredFacts"]
        self.assertEqual(facts["effort"]["value"], "high")
        self.assertFalse(facts["effort"]["runtimeRequestObserved"])
        self.assertTrue(facts["compaction"]["nativeBindingPending"])
        self.assertNotIn("canary", json.dumps(receipt))

    def credential_home(self, trials, identity):
        home = trials / identity / "hermes"
        home.mkdir(parents=True)
        for name in (".env", "auth.json", "auth.lock", "config.yaml"):
            (home / name).write_bytes(b"fake-private-credential-canary")
        (home / "state.db").write_bytes(b"fake-private-session")
        return home

    def test_purge_removes_credentials_before_fixture_can_be_deferred(self):
        trials = self.root / "trials"
        home = self.credential_home(trials, "agent-create-failure")
        (home / ".env").write_bytes(
            b"OPENCODE_GO_API_KEY=actor-canary\nDEEPSEEK_API_KEY=guardian-canary\n"
        )
        self.assertTrue((home / ".env").is_file())
        with (
            patch.object(Path, "read_bytes", side_effect=AssertionError("no content reads")),
            patch("hashlib.sha256", side_effect=AssertionError("no content hashes")),
        ):
            receipt = purge_hermes_credentials(home, trials_root=trials)
        self.assertEqual(receipt["visitedHomes"], [str(home)])
        self.assertEqual(
            [
                name
                for name in (".env", "auth.json", "auth.lock", "config.yaml")
                if (home / name).exists()
            ],
            [],
        )
        self.assertEqual(
            receipt["removedPaths"],
            [str(home / name) for name in (".env", "auth.json", "auth.lock", "config.yaml")],
        )
        self.assertEqual(receipt["remainingPaths"], [])
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["state.db"])
        self.assertNotIn("canary", json.dumps(receipt))
        self.assertEqual(purge_hermes_credentials(home, trials_root=trials)["removedPaths"], [])

    def test_sweep_visits_only_one_trial_level_and_never_source_home(self):
        trials = self.root / "trials"
        homes = [self.credential_home(trials, name) for name in ("b", "a")]
        nested = self.credential_home(trials / "nested" / "deeper", "not-a-trial")
        (self.hermes / ".env").write_bytes(b"fake-source-secret-canary")
        receipt = sweep_hermes_credentials(trials)
        self.assertEqual(receipt["visitedHomes"], sorted(str(home) for home in homes))
        self.assertEqual(
            [
                str(home / name)
                for home in homes
                for name in (".env", "auth.json", "auth.lock", "config.yaml")
                if (home / name).exists()
            ],
            [],
        )
        self.assertEqual(len(receipt["removedPaths"]), 8)
        self.assertEqual(receipt["remainingPaths"], [])
        self.assertTrue((nested / ".env").is_file())
        self.assertTrue((nested / "config.yaml").is_file())
        self.assertTrue((self.hermes / "config.yaml").is_file())
        self.assertEqual((self.hermes / ".env").read_bytes(), b"fake-source-secret-canary")
        self.assertNotIn("canary", json.dumps(receipt))

    def test_missing_home_or_batch_is_an_empty_path_observation(self):
        trials = self.root / "missing-trials"
        home = trials / "missing" / "hermes"
        self.assertEqual(sweep_hermes_credentials(trials)["visitedHomes"], [])
        receipt = purge_hermes_credentials(home, trials_root=trials)
        self.assertEqual(receipt["visitedHomes"], [str(home)])
        self.assertEqual(receipt["removedPaths"], [])
        self.assertEqual(receipt["remainingPaths"], [])
        self.assertFalse(trials.exists())

    def test_purge_rejects_outside_shape_and_relative_paths(self):
        trials = self.root / "trials"
        home = self.credential_home(trials, "safe")
        for target, scope in (
            (self.hermes, trials),
            (home / "nested", trials),
            (home, self.root / "elsewhere"),
            (Path("trials/safe/hermes"), Path("trials")),
        ):
            with self.subTest(target=str(target)), self.assertRaises(AuthorityError):
                purge_hermes_credentials(target, trials_root=scope)
        self.assertTrue((home / ".env").exists())
        self.assertTrue((home / "config.yaml").exists())

    def test_purge_rejects_symlinks_and_directories_before_any_delete(self):
        trials = self.root / "trials"
        home = self.credential_home(trials, "safe")
        names = (".env", "auth.json", "auth.lock", "config.yaml")
        for name in names:
            invalid = home / name
            invalid.unlink()
            invalid.symlink_to(self.hermes / "config.yaml")
            with self.subTest(name=name, kind="symlink"):
                with self.assertRaisesRegex(AuthorityError, "symlink"):
                    purge_hermes_credentials(home, trials_root=trials)
                self.assertTrue(all((home / other).exists() for other in names))
            invalid.unlink()
            invalid.mkdir()
            with self.subTest(name=name, kind="directory"):
                with self.assertRaisesRegex(AuthorityError, "regular file"):
                    purge_hermes_credentials(home, trials_root=trials)
                self.assertTrue(all((home / other).exists() for other in names))
            invalid.rmdir()
            invalid.write_bytes(b"fake-private-credential-canary")
        alias = self.root / "trials-alias"
        alias.symlink_to(trials, target_is_directory=True)
        with self.assertRaisesRegex(AuthorityError, "symlink"):
            sweep_hermes_credentials(alias)

    def test_sweep_rejects_symlinked_trial_and_home(self):
        trials = self.root / "trials"
        safe = self.credential_home(trials, "a-safe")
        (trials / "z-linked").symlink_to(self.hermes, target_is_directory=True)
        with self.assertRaisesRegex(AuthorityError, "symlink"):
            sweep_hermes_credentials(trials)
        self.assertTrue((safe / ".env").exists())
        self.assertTrue((safe / "config.yaml").exists())
        (trials / "z-linked").unlink()
        (trials / "z-home").mkdir()
        (trials / "z-home" / "hermes").symlink_to(self.hermes, target_is_directory=True)
        with self.assertRaisesRegex(AuthorityError, "symlink"):
            sweep_hermes_credentials(trials)
        self.assertTrue((safe / ".env").exists())
        self.assertTrue((safe / "config.yaml").exists())

    def test_cleanup_filesystem_errors_do_not_expose_exception_contents(self):
        trials = self.root / "trials"
        home = self.credential_home(trials, "safe")
        for operation, target in (
            (lambda: purge_hermes_credentials(home, trials_root=trials), "os.unlink"),
            (lambda: sweep_hermes_credentials(trials), "os.listdir"),
        ):
            with (
                self.subTest(target=target),
                patch(target, side_effect=PermissionError("fake-secret-error-canary")),
                self.assertRaises(AuthorityError) as caught,
            ):
                operation()
            self.assertIn("PermissionError", str(caught.exception))
            self.assertNotIn("canary", str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertTrue((home / ".env").exists())
            self.assertTrue((home / "config.yaml").exists())

    def test_retained_empty_model_gets_explicit_admitted_model_binding(self):
        witness = read_object(Path(__file__).parent / "fixtures/d641-isolation-retained.json")[
            "modelResolution"
        ]
        isolated, old = witness["isolated"], witness["oldHome"]
        self.assertEqual(isolated["requestedModel"], HERMES_ACTOR.model)
        self.assertEqual(old["requestedModel"], HERMES_ACTOR.model)
        self.assertEqual(isolated["session"]["model"], "")
        self.assertEqual(isolated["session"]["api_call_count"], 0)
        self.assertEqual(old["session"]["model"], "deepseek-v4.1-flash")
        self.assertGreater(old["session"]["api_call_count"], 0)
        # The real developer default belongs to a different provider. It is not
        # the runtime selected for this trial and must not be inherited.
        developer = {
            "model": {"provider": "nous", "default": "stealth/missingno"},
            "memory": {"enabled": True},
        }
        (self.hermes / "config.yaml").write_text(json.dumps(developer))
        before = (self.hermes / "config.yaml").read_bytes()
        target = self.root / "model-bound-home"
        receipt = hermes_home(self.hermes, target, self.root / "actor")
        config = read_object(target / "config.yaml")
        provider, separator, model = HERMES_ACTOR.model.partition(":")
        self.assertEqual(separator, ":")
        self.assertEqual(config["model"], {"provider": provider, "default": model})
        self.assertEqual(
            receipt["modelBinding"],
            {
                "key": "model.default",
                "value": model,
                "provider": provider,
                "selectedModel": HERMES_ACTOR.model,
                "origin": "agent_evals.adapters.execution.HERMES_ACTOR",
            },
        )
        self.assertEqual((self.hermes / "config.yaml").read_bytes(), before)
        self.assertFalse((target / "skills").exists())
        self.assertFalse((target / "state.db").exists())

    def test_removing_only_explicit_default_restores_empty_model_config(self):
        target = self.root / "model-negative-control"
        hermes_home(self.hermes, target, self.root / "actor")
        config = read_object(target / "config.yaml")
        # Config-only negative control of ACP session.py:_make_agent's
        # `str(model_cfg.get("default") or "")` expression. This creates no
        # session and makes no claim to satisfy the live trial acceptance.
        self.assertEqual(str(config["model"].get("default") or ""), "deepseek-v4.1-flash")
        del config["model"]["default"]
        write_object(target / "config.yaml", config)
        absent = read_object(target / "config.yaml")
        self.assertEqual(absent["model"]["provider"], "opencode-go")
        self.assertEqual(str(absent["model"].get("default") or ""), "")

    def test_provider_yaml_interpreter_is_independent_of_actor_path(self):
        shadow = self.root / "hermes-bundled-bin"
        shadow.mkdir()
        python = shadow / "python3"
        python.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"ModuleNotFoundError: No module named 'yaml'\" >&2\nexit 1\n"
        )
        python.chmod(0o700)
        with patch.dict(
            "os.environ", {"PATH": str(shadow) + os.pathsep + os.environ.get("PATH", "")}
        ):
            try:
                receipt = hermes_home(self.hermes, self.root / "yaml-home", self.root / "actor")
            except AuthorityError as error:
                self.fail("PATH-shadowed provider parser refused valid config: " + str(error))
        self.assertEqual(receipt["parser"]["path"], str(Path("/usr/bin/python3").resolve()))
        self.assertTrue(receipt["parser"]["yamlVersion"])

    def test_provider_yaml_verification_failure_names_interpreter_without_secrets(self):
        (self.hermes / "config.yaml").write_text("model: {api_key: config-secret-canary}")
        stderr = (
            "source excerpt config-secret-canary\nModuleNotFoundError: No module named 'yaml'\n"
        )
        with (
            patch(
                "agent_evals.trial_isolation.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "", stderr),
            ) as run,
            self.assertRaises(AuthorityError) as raised,
        ):
            hermes_home(self.hermes, self.root / "unverified-yaml", self.root / "actor")
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertNotIn(str(self.hermes / "config.yaml"), command)
        message = str(raised.exception)
        self.assertIn("/usr/bin/python3", message)
        self.assertIn("ModuleNotFoundError: No module named 'yaml'", message)
        self.assertNotIn("config-secret-canary", message)

    def test_malformed_provider_yaml_failure_does_not_quote_config_values(self):
        (self.hermes / "config.yaml").write_text("model: [api_key: malformed-secret-canary\n")
        with self.assertRaises(AuthorityError) as raised:
            hermes_home(self.hermes, self.root / "malformed-yaml", self.root / "actor")
        message = str(raised.exception)
        self.assertIn("/usr/bin/python3", message)
        self.assertIn("ParserError: provider config rejected", message)
        self.assertNotIn("malformed-secret-canary", message)
        self.assertNotIn("api_key", message)

    def test_provider_yaml_unknown_stderr_last_line_is_withheld(self):
        stderr = "ModuleNotFoundError: No module named 'yaml'\nunknown: config-secret-canary\n"
        with (
            patch(
                "agent_evals.trial_isolation.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "", stderr),
            ),
            self.assertRaises(AuthorityError) as raised,
        ):
            hermes_home(self.hermes, self.root / "unknown-stderr-yaml", self.root / "actor")
        message = str(raised.exception)
        self.assertIn("/usr/bin/python3", message)
        self.assertIn("stderr detail withheld", message)
        self.assertNotIn("config-secret-canary", message)
        self.assertNotIn("ModuleNotFoundError", message)

    def test_provider_yaml_interpreter_launch_failure_reports_path_and_errno_only(self):
        with (
            patch(
                "agent_evals.trial_isolation.subprocess.run",
                side_effect=PermissionError(13, "config-secret-canary", "secret-provider-file"),
            ),
            self.assertRaises(AuthorityError) as raised,
        ):
            hermes_home(self.hermes, self.root / "unlaunchable-yaml", self.root / "actor")
        message = str(raised.exception)
        self.assertIn("/usr/bin/python3", message)
        self.assertIn("PermissionError (errno 13)", message)
        self.assertNotIn("config-secret-canary", message)
        self.assertNotIn("secret-provider-file", message)

    def test_serving_copy_rebinds_imports_and_entrypoints_without_shared_inodes(self):
        original = self.candidate / "mcp/.venv/lib/python3.14/site-packages/source.pth"
        original.parent.mkdir(parents=True)
        original.write_text(str(self.candidate / "mcp/src"))
        entry = self.candidate / "mcp/.venv/bin/tool"
        entry.write_text("#!" + str(self.candidate / "mcp/.venv/bin/python") + "\n")
        (self.candidate / ".env").write_text("not copied")
        copy = private_build(self.candidate, self.root / "copy")
        self.assertEqual(
            (copy / original.relative_to(self.candidate)).read_text(), str(copy / "mcp/src")
        )
        self.assertEqual(
            (copy / entry.relative_to(self.candidate)).read_text(),
            "#!" + str(copy / "mcp/.venv/bin/python") + "\n",
        )
        self.assertNotEqual(
            original.stat().st_ino, (copy / original.relative_to(self.candidate)).stat().st_ino
        )
        self.assertFalse((copy / ".env").exists())

    def test_snapshot_uses_actor_path_order_not_worker_resolution(self):
        first, second = self.root / "first", self.root / "second"
        for directory in (first, second):
            directory.mkdir()
            (directory / "same-tool").write_text("tool")
            (directory / "same-tool").chmod(0o700)
        snapshot = path_snapshot({"PATH": str(first) + os.pathsep + str(second)}, self.root)
        self.assertEqual(snapshot["executables"]["same-tool"]["path"], str(first / "same-tool"))
        self.assertNotIn("python", snapshot["executables"])
        self.assertEqual(
            snapshot["sha256"], json_digest({k: v for k, v in snapshot.items() if k != "sha256"})
        )

    def test_capture_rejects_foreign_hermes_home_before_session_or_database_reads(self):
        project = self.root / "trial/actor"
        project.mkdir(parents=True)
        provisioned = SimpleNamespace(
            project_root=project, native_capture_roots={"hermes": self.hermes}
        )
        with patch("agent_evals.adapters.native_trials.paseo_session") as discovery:
            capture = capture_trial_actor(cast(TrialSources, provisioned), "hermes", "host-id", 0)
        discovery.assert_not_called()
        self.assertIn("Hermes capture home is not trial-owned", capture.unknowns)
        own = project.parent / "hermes"
        own.mkdir()
        self.assertEqual(native_candidates("hermes", own, 0), (own / "state.db",))

    def test_native_bundle_hook_accepts_a_candidate_tree_and_preserves_material(self):
        environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")

        def git(root, *args):
            return subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", *args],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        metadata: dict[str, Any] = {
            "schema": "ar-curator-eval-native-worklist/v1",
            "authority": "D641",
            "expectedWorklistDigest": "sha256:test",
        }
        files = list(self.case.files)
        manifest = self.case.manifest
        for name, filename, material in (
            ("code", "answer.py", "code.txt"),
            ("memory", "onboarding/answer.py.md", "memory.txt"),
        ):
            repository = self.root / (name + "-bundle-source")
            path = repository / filename
            path.parent.mkdir(parents=True)
            path.write_text("base\n")
            _git_baseline(repository, environment)
            base = git(repository, "rev-parse", "HEAD")
            path.write_bytes((self.case.path / material).read_bytes())
            git(repository, "add", "--all")
            git(
                repository,
                "-c",
                "user.name=Eval",
                "-c",
                "user.email=eval@invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                "candidate",
            )
            bundle = self.case.path / (name + ".bundle")
            git(repository, "bundle", "create", str(bundle), "refs/heads/eval-fixture")
            files.append(EvidenceFile.capture(self.case.path, bundle.name))
            destination = "fixture/git/" + bundle.name
            manifest["actorInputs"].append({"source": bundle.name, "destination": destination})
            metadata[name] = {
                "repository": "projects/" + name,
                "bundle": destination,
                "bundleRef": "refs/heads/eval-fixture",
                "base": base,
                "candidate": git(repository, "rev-parse", "HEAD^{tree}"),
            }
        manifest["setup"]["nativeWorklist"] = metadata
        manifest["files"] = [item.to_dict() for item in files]
        write_object(self.case.path / "case.json", manifest)
        case = CaseBundle(self.case.path, self.case.case_id, manifest, tuple(files))
        fixture = reset_case(case, self.root / "native-resets")
        self.addCleanup(release_fixture, fixture)
        for name in ("code", "memory"):
            _git_baseline(fixture.actor_root / "projects" / name, environment)
        receipt = import_native_sides(case, fixture, environment)
        assert receipt is not None
        for name in ("code", "memory"):
            self.assertEqual(receipt["sides"][name]["candidateTree"], metadata[name]["candidate"])
            self.assertEqual(
                git(fixture.actor_root / "projects" / name, "status", "--porcelain"), ""
            )
