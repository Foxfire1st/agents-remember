from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer import reset_ambient
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from test_spawn_agent_session import (
    _detected,
    _FakeHost,
    _FakePaster,
    _ObservedPaster,
    _runner_config,
    _write_leaf_task,
    call_spawn,
)
from test_worktree_support import write_current_task_lineage

SPRINT_REF = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
MASTER_REF = TaskDocumentRef(repository="repo-a", path="master/task.json")
LEAF_REF = TaskDocumentRef(repository="repo-a", path="master/leaf-1.json")


def _role_ref(kwargs: dict[str, object]) -> TaskDocumentRef | None:
    env = kwargs.get("env")
    role = str(env.get("AR_SPAWN_ROLE", "")) if isinstance(env, dict) else ""
    return (
        dict.fromkeys(
            ("architect", "orchestrator", "strategist", "designer", "system-specialist"),
            SPRINT_REF,
        )
        | {"manager": MASTER_REF}
        | dict.fromkeys(("worker", "reviewer", "curator"), LEAF_REF)
    ).get(role)


class SettingsDefinedHarnessTests(unittest.TestCase):
    """Registry openness: orchestration.harnesses entries ADD new harnesses or OVERRIDE
    builtin defaults; unknown-everywhere ids refuse pointing at the manual; vocab-less user
    harnesses refuse the model/effort knobs with guidance."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.coordination_root = self.tmp / "ar-coordination"
        self.repo_root = self.tmp / "workspace" / "repo-a"
        self.repo_root.mkdir(parents=True)
        _write_leaf_task(self.coordination_root, repo="repo-a")
        write_current_task_lineage(
            self.coordination_root, repo_name="repo-a", master_name="master", leaf_id="leaf-1"
        )
        self.config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.coordination_root,
            workspace_root=self.tmp / "workspace",
            transcript_root=self.tmp / "logs" / "mcp",
            repositories={"repo-a": RepositoryScope(repo_id="repo-a", path=self.repo_root)},
        )
        self.catalog = TerminalCatalog(
            self.coordination_root / "logs" / "dashboard" / "terminal-sessions.json"
        )
        self.host = _FakeHost()
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _write_settings(self, root: Path, orchestration: dict) -> None:
        path = agentic_settings_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"orchestration": orchestration}), encoding="utf-8")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_spawn_agent_session_settings.py:57).
    def _spawn(self, **kwargs: object) -> dict:  # pragma: no cover
        base: dict[str, object] = {
            "session_id": "worker-1",
            "host": self.host,
            "which": _detected,
        }
        base.update(kwargs)
        if "task_document_ref" not in base and (task_ref := _role_ref(base)) is not None:
            base["task_document_ref"] = task_ref
        paster = base.get("paster")
        if "session_log" not in base and isinstance(paster, _FakePaster):
            base["session_log"] = paster.log
        return call_spawn(self.config, **base)

    def test_settings_defined_harness_spawns_with_its_argv(self) -> None:
        self._write_settings(
            self.coordination_root,
            {
                "harnesses": {"hermes": {"command": "hermes", "argv": ["hermes", "--tui"]}},
                "roles": {"worker": {"harness": "hermes"}},
            },
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(payload["harness"], "hermes")
        self.assertEqual(_runner_config(self.host).argv, ("hermes", "--tui"))

    def test_builtin_override_replaces_the_argv(self) -> None:
        self._write_settings(
            self.coordination_root,
            {
                "harnesses": {"claude": {"argv": ["claude", "--continue"]}},
                "roles": {
                    "worker": {
                        "harness": "claude",
                        "model": "claude-fable-5",
                        "effort": "max",
                    }
                },
            },
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertEqual(payload["status"], "spawned-unbriefed")
        # The user's command array replaces ours; the native adapter applies the structured knobs
        # after dynamic validation.
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude", "--continue"))
        self.assertEqual(
            runner.resolved_launch,
            ResolvedLaunch("claude", "claude-fable-5", "max", self.config.workspace_root),
        )

    def test_vocab_less_settings_harness_refuses_effort_with_guidance(self) -> None:
        self._write_settings(
            self.coordination_root,
            {
                "harnesses": {"hermes": {"command": "hermes"}},
                "roles": {"worker": {"harness": "hermes", "effort": "high"}},
            },
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "effort-invalid")
        self.assertIn("effortFlag", payload["detail"])
        self.assertIn("launchArgs", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_vocab_less_settings_harness_refuses_model_with_guidance(self) -> None:
        self._write_settings(
            self.coordination_root,
            {
                "harnesses": {"hermes": {"command": "hermes"}},
                "roles": {"worker": {"harness": "hermes", "model": "opus"}},
            },
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "model-invalid")
        self.assertIn("modelFlag", payload["detail"])
        self.assertIn("launchArgs", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_settings_harness_with_declared_vocabulary_maps_the_knobs(self) -> None:
        self._write_settings(
            self.coordination_root,
            {
                "harnesses": {
                    "hermes": {
                        "command": "hermes",
                        "modelFlag": "--model",
                        "effortFlag": "--reasoning",
                        "effortFlagValues": ["low", "high"],
                    }
                },
                "roles": {"worker": {"harness": "hermes", "model": "h-1", "effort": "high"}},
            },
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(
            _runner_config(self.host).argv,
            ("hermes", "--model", "h-1", "--reasoning", "high"),
        )

    def test_repo_local_harness_entry_overrides_global(self) -> None:
        self._write_settings(
            self.coordination_root,
            {
                "harnesses": {"hermes": {"command": "hermes", "argv": ["hermes", "--global"]}},
                "spawn": {"harness": "hermes"},
            },
        )
        self._write_settings(
            self.repo_root,
            {"harnesses": {"hermes": {"argv": ["hermes", "--local"]}}},
        )
        payload = self._spawn(task_document_ref=LEAF_REF)
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(_runner_config(self.host).argv, ("hermes", "--local"))
        # A leafless spawn resolves the GLOBAL layer only (no repo-local override).
        payload = self._spawn(session_id="worker-2")
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(_runner_config(self.host, -1).argv, ("hermes", "--global"))


class SpawnLevelResolutionTests(unittest.TestCase):
    """The dispatch level parameter + rolesPerLevel
    resolution -- repo-local level override > global level override > repo-local role default >
    global role default > detection-gated default -- with the resolved level
    recorded in spawn provenance."""

    # The canonical reviewer economics (docs/reference/harnesses.md walks this).
    ECONOMICS: ClassVar[dict] = {
        "roles": {"reviewer": {"harness": "claude", "model": "sonnet", "effort": "high"}},
        "rolesPerLevel": {
            "master": {"reviewer": {"model": "opus", "effort": "xhigh"}},
            "portfolio": {"reviewer": {"model": "fable", "effort": "ultracode"}},
        },
    }

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.coordination_root = self.tmp / "ar-coordination"
        self.repo_root = self.tmp / "workspace" / "repo-a"
        self.repo_root.mkdir(parents=True)
        _write_leaf_task(self.coordination_root, repo="repo-a")
        write_current_task_lineage(
            self.coordination_root, repo_name="repo-a", master_name="master", leaf_id="leaf-1"
        )
        self.config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.coordination_root,
            workspace_root=self.tmp / "workspace",
            transcript_root=self.tmp / "logs" / "mcp",
            repositories={"repo-a": RepositoryScope(repo_id="repo-a", path=self.repo_root)},
        )
        self.catalog = TerminalCatalog(
            self.coordination_root / "logs" / "dashboard" / "terminal-sessions.json"
        )
        self.host = _FakeHost()
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _write_settings(self, root: Path, orchestration: dict) -> None:
        path = agentic_settings_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"orchestration": orchestration}), encoding="utf-8")

    def _spawn(self, **kwargs: object) -> dict:
        base: dict[str, object] = {
            "session_id": "seat-1",
            "host": self.host,
            "which": _detected,
        }
        base.update(kwargs)
        if "task_document_ref" not in base and (task_ref := _role_ref(base)) is not None:
            base["task_document_ref"] = task_ref
        paster = base.get("paster")
        if "session_log" not in base and isinstance(paster, _FakePaster):
            base["session_log"] = paster.log
        return call_spawn(self.config, **base)

    def test_level_override_deep_merges_harness_inherited(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        payload = self._spawn(env={"AR_SPAWN_ROLE": "reviewer"}, level="master")
        self.assertEqual(payload["status"], "spawned-unbriefed")
        # harness inherited from the flat default; model/effort from the master override.
        self.assertEqual(payload["harness"], "claude")
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude",))
        self.assertEqual(
            runner.resolved_launch,
            ResolvedLaunch("claude", "opus", "xhigh", self.config.workspace_root),
        )
        # The resolved knobs also ride the env for session-start visibility.
        spawn_env = self.host.ensured[0]["env"]
        assert isinstance(spawn_env, dict)
        self.assertEqual(spawn_env["AR_SPAWN_MODEL"], "opus")
        self.assertEqual(spawn_env["AR_SPAWN_EFFORT"], "xhigh")

    def test_leaf_default_uses_the_flat_role_knobs(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        payload = self._spawn(env={"AR_SPAWN_ROLE": "reviewer"})
        self.assertEqual(payload["status"], "spawned-unbriefed")
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude",))
        self.assertEqual(
            runner.resolved_launch,
            ResolvedLaunch("claude", "sonnet", "high", self.config.workspace_root),
        )
        self.assertEqual(payload["spawnLevel"], "leaf")
        self.assertEqual(payload["spawnLevelSource"], "default")

    def test_portfolio_ultracode_is_carried_without_a_paste_substitution(self) -> None:
        # The stale portfolio setting reaches dynamic validation exactly as authored. It never
        # becomes a synthesized /effort command.
        self._write_settings(self.coordination_root, self.ECONOMICS)
        paster = _FakePaster()
        payload = self._spawn(env={"AR_SPAWN_ROLE": "reviewer"}, level="portfolio", paster=paster)
        self.assertEqual(payload["status"], "spawned-unbriefed")
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude",))
        self.assertEqual(
            runner.resolved_launch,
            ResolvedLaunch("claude", "fable", "ultracode", self.config.workspace_root),
        )
        self.assertEqual(paster.calls, [])
        self.assertEqual(runner.session_commands, ())
        self.assertNotIn("sessionCommands", payload)

    def test_legacy_model_effort_args_are_refused_instead_of_beating_settings(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "reviewer"}, level="master", model="haiku", effort="low"
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "spend-override-unsupported")
        self.assertIn("model", payload["detail"])
        self.assertIn("effort", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_codex_configured_worker_rejects_attempted_claude_override(self) -> None:
        self._write_settings(
            self.coordination_root,
            {
                "roles": {
                    "manager": {"harness": "codex", "effort": "medium"},
                    "worker": {"harness": "codex", "effort": "medium"},
                }
            },
        )
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "worker"},
            harness="claude",
            model="opus",
            effort="high",
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "spend-override-unsupported")
        self.assertIn("harness", payload["detail"])
        self.assertIn("model", payload["detail"])
        self.assertIn("effort", payload["detail"])
        self.assertIn("orchestration.rolesPerLevel", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_spend_env_keys_are_refused_instead_of_overriding_settings(self) -> None:
        self._write_settings(
            self.coordination_root,
            {"roles": {"worker": {"harness": "codex", "model": "gpt-5", "effort": "medium"}}},
        )
        payload = self._spawn(
            env={
                "AR_SPAWN_ROLE": "worker",
                "AR_SPAWN_MODEL": "opus",
                "AR_SPAWN_EFFORT": "high",
            }
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "spend-override-unsupported")
        self.assertIn("env.AR_SPAWN_MODEL", payload["detail"])
        self.assertIn("env.AR_SPAWN_EFFORT", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_harness_native_spend_env_keys_are_refused(self) -> None:
        self._write_settings(
            self.coordination_root,
            {"roles": {"worker": {"harness": "codex", "model": "gpt-5", "effort": "medium"}}},
        )
        payload = self._spawn(
            env={
                "AR_SPAWN_ROLE": "worker",
                "ANTHROPIC_MODEL": "opus",
                "ANTHROPIC_SMALL_FAST_MODEL": "haiku",
                "ANTHROPIC_DEFAULT_FABLE_MODEL": "fable",
                "MAX_THINKING_TOKENS": "64000",
                "DISABLE_PROMPT_CACHING": "1",
                "ANTHROPIC_BASE_URL": "https://example.invalid",
                "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock.example.invalid",
                "ANTHROPIC_VERTEX_BASE_URL": "https://vertex.example.invalid",
                "AWS_BEARER_TOKEN_BEDROCK": "token",
            }
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "spend-override-unsupported")
        self.assertIn("env.ANTHROPIC_MODEL", payload["detail"])
        self.assertIn("env.ANTHROPIC_SMALL_FAST_MODEL", payload["detail"])
        self.assertIn("env.ANTHROPIC_DEFAULT_FABLE_MODEL", payload["detail"])
        self.assertIn("env.MAX_THINKING_TOKENS", payload["detail"])
        self.assertIn("env.DISABLE_PROMPT_CACHING", payload["detail"])
        self.assertIn("env.ANTHROPIC_BASE_URL", payload["detail"])
        self.assertIn("env.ANTHROPIC_BEDROCK_BASE_URL", payload["detail"])
        self.assertIn("env.ANTHROPIC_VERTEX_BASE_URL", payload["detail"])
        self.assertIn("env.AWS_BEARER_TOKEN_BEDROCK", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_openai_family_spend_env_keys_are_refused(self) -> None:
        self._write_settings(
            self.coordination_root,
            {"roles": {"worker": {"harness": "codex", "model": "gpt-5", "effort": "medium"}}},
        )
        payload = self._spawn(
            env={
                "AR_SPAWN_ROLE": "worker",
                "OPENAI_MODEL": "gpt-5-high",
                "OPENAI_BASE_URL": "https://example.invalid",
                "OPENAI_API_KEY": "sk-test",
            }
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "spend-override-unsupported")
        self.assertIn("env.OPENAI_MODEL", payload["detail"])
        self.assertIn("env.OPENAI_BASE_URL", payload["detail"])
        self.assertIn("env.OPENAI_API_KEY", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_repo_local_level_override_beats_global_level_override(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        self._write_settings(
            self.repo_root,
            {"rolesPerLevel": {"master": {"reviewer": {"model": "fable"}}}},
        )
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "reviewer"},
            level="master",
            task_document_ref=LEAF_REF,
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
        # model: repo-local master override; effort: global master override; harness: flat default.
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude",))
        self.assertEqual(
            runner.resolved_launch,
            ResolvedLaunch("claude", "fable", "xhigh", self.config.workspace_root),
        )

    def test_provenance_records_the_resolved_level_and_source(self) -> None:
        payload = self._spawn(level="master")
        self.assertEqual(payload["spawnLevel"], "master")
        self.assertEqual(payload["spawnLevelSource"], "explicit")
        row = self.catalog.get("seat-1")
        assert row is not None
        self.assertEqual(row.spawn_level, "master")
        self.assertEqual(row.spawn_level_source, "explicit")
        self.assertEqual(row.to_json()["spawnLevel"], "master")

    def test_default_level_dispatch_is_unchanged_for_existing_callers(self) -> None:
        # No settings file, no level, no role env: exactly the pre-level-feature dispatch (plain argv),
        # with the defaulted level recorded as provenance.
        payload = self._spawn()
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(_runner_config(self.host).argv, ("claude",))
        self.assertEqual(payload["spawnLevel"], "leaf")
        self.assertEqual(payload["spawnLevelSource"], "default")

    def test_unknown_level_refuses_before_spawning(self) -> None:
        payload = self._spawn(level="epic")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "level-invalid")
        self.assertIn("'epic'", payload["detail"])
        self.assertIn("leaf, master, portfolio", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_role_free_form_knobs_flow_from_settings(self) -> None:
        # Free-form settings keep riding the dispatch, but model/effort remain a complete native
        # launch selection and are never translated into a session command.
        self._write_settings(
            self.coordination_root,
            {
                "roles": {
                    "strategist": {
                        "harness": "claude",
                        "model": "claude-fable-5",
                        "effort": "max",
                        "promptKeywords": ["ultracode"],
                    }
                }
            },
        )
        paster = _ObservedPaster(
            capture="Fable 5 with ultracode effort · Claude Max\n◉ ultracode · /effort\n"
        )
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "strategist"},
            paster=paster,
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(_runner_config(self.host).argv, ("claude",))
        self.assertEqual(paster.calls, [])
        self.assertEqual(_runner_config(self.host).session_commands, ())
        row = self.catalog.get("seat-1")
        assert row is not None
        self.assertEqual(row.prompt_keywords, ("ultracode",))
        self.assertIsNone(row.session_commands)
