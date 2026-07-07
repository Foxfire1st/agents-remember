"""Tests for the agent-facing ``spawn_agent_session`` MCP tool + the serving paste endpoint (slice L2).

The tool composes the EXISTING session primitives (opener + leaf claim + echo-confirmed paste + submit).
These tests inject a fake host + fake paster + a fake ``which`` so the composition is exercised without a
real tmux server, and drive the ``POST /api/terminal/{session}/paste`` endpoint through ``TestClient``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.tools.terminal import spawn_agent_session_payload
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    observer_root,
    reset_ambient,
)
from agents_remember.observer.ambient import ambient
from agents_remember.serving.app import create_app
from agents_remember.serving.terminal import TerminalSessionBinding
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult
from agents_remember.tasks import TaskDocument, write_task_doc


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"


def _write_leaf_task(
    coordination_root: Path,
    *,
    repo: str = "repo",
    master: str = "master",
    doc_id: str = "leaf-1",
    slug: str = "leaf-1",
) -> None:
    task_root = coordination_root / "tasks" / repo / master
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": master.upper(),
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": repo,
                "createdAt": "2026-07-07T10:00",
                "subTasks": [
                    {
                        "number": doc_id,
                        "name": "Leaf",
                        "file": f"{slug}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": doc_id,
                "slug": slug,
                "title": "Leaf",
                "kind": "subTask",
                "repo": repo,
                "createdAt": "2026-07-07T10:01",
                "master": "task.md",
            }
        ),
    )


class _FakeHost:
    def __init__(self) -> None:
        self.ensured: list[dict[str, object]] = []
        self.known: set[str] = set()

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known

    def shutdown(self) -> None:
        # The create_app lifespan calls this on teardown; the fake has nothing to reap.
        return None

    def ensure(
        self,
        sid: str,
        *,
        cwd: Path,
        command: Sequence[str],
        lifecycle_id: str | None = None,
        name: str | None = None,
        suspend_unsafe: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> TerminalSessionBinding:
        tmux_name = name or f"ar-{sid}"
        self.ensured.append({"sid": sid, "env": dict(env or {}), "command": tuple(command)})
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=Path(cwd),
            command=tuple(command),
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )


class _FakePaster:
    def __init__(
        self, *, delivered: bool = True, submitted: bool = True, capture: str = ""
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._delivered = delivered
        self._submitted = submitted
        self._capture = capture

    def paste(self, tmux_name: str, text: str, *, submit: bool = False, **_kwargs: object) -> PasteResult:
        self.calls.append({"tmux": tmux_name, "text": text, "submit": submit})
        return PasteResult(
            delivered=self._delivered,
            submitted=self._submitted if submit else False,
            capture=self._capture,
        )


def _running_chat(session_id: str, *, leaf_key: str) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label="Claude Code",
        kind="harness",
        harness="claude",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("claude",),
        created_at="2026-07-04T00:00:00Z",
        last_attached_at="2026-07-04T00:00:00Z",
        status="running",
        leaf_key=leaf_key,
    )


class SpawnAgentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _config(self.tmp)
        _write_leaf_task(self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "logs" / "dashboard" / "terminal-sessions.json")
        self.host = _FakeHost()
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _spawn(self, **kwargs: object) -> dict:
        base: dict[str, object] = {
            "harness": "claude",
            "session_id": "worker-1",
            "host": self.host,
            "which": _detected,
        }
        base.update(kwargs)
        return spawn_agent_session_payload(self.config, **base)  # type: ignore[arg-type]

    def test_spawns_and_delivers_context_with_submit(self) -> None:
        paster = _FakePaster(delivered=True, submitted=True)
        payload = self._spawn(
            leaf_key="repo/master/leaf-1",
            context="You are the worker for leaf-1.",
            submit=True,
            model="opus",
            effort="high",
            spawned_by_session="manager-9",
            spawned_by_lifecycle="LC-manager",
            paster=paster,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["session"], "worker-1")
        self.assertEqual(payload["leafKey"], "repo/master/leaf-1")
        self.assertEqual(payload["spawnedBySession"], "manager-9")
        self.assertEqual(payload["spawnedByLifecycle"], "LC-manager")
        self.assertTrue(payload["contextDelivered"])
        self.assertTrue(payload["submitted"])
        # Knobs seeded into the spawn env.
        self.assertEqual(
            self.host.ensured[0]["env"], {"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "high"}
        )
        # Provenance persisted on the catalog row.
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.spawned_by_session, "manager-9")
        self.assertEqual(row.spawned_by_lifecycle, "LC-manager")
        # The packet was pasted-and-submitted into this session's tmux pane.
        self.assertEqual(paster.calls[0]["tmux"], "ar-worker-1")
        self.assertTrue(paster.calls[0]["submit"])

    def test_spawn_records_role_from_env_and_reports_it(self) -> None:
        # L14: the AR_SPAWN_ROLE riding the caller's env is persisted on the catalog row and
        # reported in the payload — the Chats command tree groups command chats by it.
        payload = self._spawn(env={"AR_SPAWN_ROLE": "manager"})
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["spawnRole"], "manager")
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.spawn_role, "manager")

    def test_spawn_normalizes_legacy_leaf_slug_before_persisting(self) -> None:
        payload = self._spawn(leaf_key="leaf-1")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["leafKey"], "repo/master/leaf-1")
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.leaf_key, "repo/master/leaf-1")

    def test_spawn_rejects_unmatchable_leaf_ref_before_spawning(self) -> None:
        payload = self._spawn(leaf_key="missing-leaf", paster=_FakePaster())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "leaf-ref-not-found")
        self.assertIn("<repo>/<master-folder>/<doc-id>", payload["detail"])
        self.assertEqual(self.host.ensured, [])
        self.assertIsNone(self.catalog.get("worker-1"))

    def test_draft_paste_does_not_submit(self) -> None:
        paster = _FakePaster(delivered=True, submitted=True)
        payload = self._spawn(context="draft packet", submit=False, paster=paster)
        self.assertEqual(payload["status"], "spawned")
        self.assertTrue(payload["contextDelivered"])
        self.assertNotIn("submitted", payload)  # omitted (None) when not submitting
        self.assertFalse(paster.calls[0]["submit"])

    def test_spawn_without_context_skips_paste(self) -> None:
        paster = _FakePaster()
        payload = self._spawn(paster=paster)
        self.assertEqual(payload["status"], "spawned")
        self.assertNotIn("contextDelivered", payload)
        self.assertEqual(paster.calls, [])

    def test_verified_delivery_omits_the_failure_capture(self) -> None:
        paster = _FakePaster(delivered=True, submitted=True, capture="worker> [Pasted text #1]")
        payload = self._spawn(context="brief", submit=True, paster=paster)
        self.assertTrue(payload["contextDelivered"])
        # 260707-HFX-L3: the capture is failure evidence -- a verified delivery ships none.
        self.assertNotIn("deliveryCapture", payload)

    def test_unverified_delivery_reports_false_with_the_pane_capture_attached(self) -> None:
        # 260707-HFX-L3 / SF-1: reviewer 46b2e267 got contextDelivered:true over a codex pane that
        # booted clean with no payload. The loud-failure contract: delivered false + the capture.
        paster = _FakePaster(delivered=False, submitted=False, capture="codex> \n(clean boot)")
        payload = self._spawn(
            leaf_key="repo/master/leaf-1", context="the brief", submit=True, paster=paster
        )
        self.assertEqual(payload["status"], "spawned")
        self.assertFalse(payload["contextDelivered"])
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["deliveryCapture"], "codex> \n(clean boot)")

    def test_unconfirmed_submit_attaches_the_capture_even_when_delivered(self) -> None:
        # Review N3: a delivered draft whose requested submit did NOT advance the pane is still a
        # failed outcome -- the capture rides along so the caller can see the stuck draft.
        paster = _FakePaster(delivered=True, submitted=False, capture="claude> draft sitting")
        payload = self._spawn(context="brief", submit=True, paster=paster)
        self.assertTrue(payload["contextDelivered"])
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["deliveryCapture"], "claude> draft sitting")

    def test_failed_delivery_with_empty_capture_ships_an_explicit_marker(self) -> None:
        # Review N3 alignment with inbox_delivery: a False outcome never goes evidence-less. A
        # vanished pane yields an empty capture -- the payload carries the explicit marker instead
        # of silently omitting deliveryCapture.
        paster = _FakePaster(delivered=False, submitted=False, capture="")
        payload = self._spawn(context="brief", submit=True, paster=paster)
        self.assertFalse(payload["contextDelivered"])
        self.assertEqual(payload["deliveryCapture"], "(empty pane capture)")

    def test_leaf_taken_is_surfaced_never_overridden(self) -> None:
        self.catalog.upsert(_running_chat("owner-1", leaf_key="repo/master/leaf-1"))
        paster = _FakePaster()
        payload = self._spawn(session_id="intruder", leaf_key="repo/master/leaf-1", paster=paster)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "leaf-taken")
        self.assertEqual(payload["ownerSession"], "owner-1")
        # Never spawned or pasted.
        self.assertEqual(self.host.ensured, [])
        self.assertEqual(paster.calls, [])
        self.assertIsNone(self.catalog.get("intruder"))

    def test_unknown_harness_refused_before_spawn(self) -> None:
        payload = self._spawn(harness="not-a-harness")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "harness-unknown")
        self.assertEqual(self.host.ensured, [])

    def test_undetected_harness_refused_before_spawn(self) -> None:
        payload = self._spawn(which=lambda _cmd: None)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "harness-not-detected")
        self.assertEqual(self.host.ensured, [])

    def test_spawned_by_lifecycle_defaults_to_active_ambient(self) -> None:
        install_ambient(AmbientLifecycle(EventStore(observer_root(self.config))))
        amb = ambient()
        assert amb is not None
        started = amb.start()
        payload = self._spawn(paster=_FakePaster())
        self.assertEqual(payload["spawnedByLifecycle"], started.id)
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.spawned_by_lifecycle, started.id)


class SpawnKnobApplicationTests(unittest.TestCase):
    """260703-L16: per-harness knob application at the dispatch seam -- flags on the argv, the
    two-vehicle claude effort vocabulary, dispatch-time refusals, and the free-form escape hatch
    (launchArgs / promptKeywords / sessionCommands) riding spawn + paste + provenance."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _config(self.tmp)
        _write_leaf_task(self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "logs" / "dashboard" / "terminal-sessions.json")
        self.host = _FakeHost()
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _spawn(self, **kwargs: object) -> dict:
        base: dict[str, object] = {
            "harness": "claude",
            "session_id": "worker-1",
            "host": self.host,
            "which": _detected,
        }
        base.update(kwargs)
        return spawn_agent_session_payload(self.config, **base)  # type: ignore[arg-type]

    def test_flag_vocabulary_effort_rides_the_argv_with_no_session_command(self) -> None:
        paster = _FakePaster()
        payload = self._spawn(effort="max", model="opus", context="brief", paster=paster)
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--model", "opus", "--effort", "max"),
        )
        # Flag-vehicle effort: exactly ONE paste (the brief), no session command.
        self.assertEqual(len(paster.calls), 1)
        self.assertEqual(paster.calls[0]["text"], "brief")
        self.assertNotIn("sessionCommands", payload)
        self.assertNotIn("sessionCommandsDelivered", payload)

    def test_ultracode_rides_a_session_command_before_the_brief_not_the_flag(self) -> None:
        paster = _FakePaster()
        payload = self._spawn(effort="ultracode", context="brief", paster=paster)
        self.assertEqual(payload["status"], "spawned")
        # NO --effort flag (the CLI would warn-and-degrade); the env still rides the value.
        self.assertEqual(self.host.ensured[0]["command"], ("claude",))
        self.assertEqual(self.host.ensured[0]["env"], {"AR_SPAWN_EFFORT": "ultracode"})
        # The session command is the FIRST paste (submitted), the brief follows.
        self.assertEqual(
            [(call["text"], call["submit"]) for call in paster.calls],
            [("/effort ultracode", True), ("brief", False)],
        )
        self.assertEqual(payload["sessionCommands"], ["/effort ultracode"])
        self.assertTrue(payload["sessionCommandsDelivered"])

    def test_unknown_effort_refuses_at_dispatch_naming_both_sets(self) -> None:
        payload = self._spawn(effort="turbo")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "effort-invalid")
        self.assertIn("'claude'", payload["detail"])
        self.assertIn("low, medium, high, xhigh, max", payload["detail"])
        self.assertIn("ultracode", payload["detail"])
        # Nothing spawned, nothing upserted.
        self.assertEqual(self.host.ensured, [])
        self.assertIsNone(self.catalog.get("worker-1"))

    def test_mapping_less_builtin_harness_stays_env_only(self) -> None:
        payload = self._spawn(harness="codex", model="gpt-5", effort="whatever")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(self.host.ensured[0]["command"], ("codex",))
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_MODEL": "gpt-5", "AR_SPAWN_EFFORT": "whatever"},
        )

    def test_launch_args_ride_the_argv_verbatim_and_are_recorded(self) -> None:
        payload = self._spawn(effort="max", launch_args=["--dangerously-skip-permissions"])
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--effort", "max", "--dangerously-skip-permissions"),
        )
        self.assertEqual(payload["launchArgs"], ["--dangerously-skip-permissions"])
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.launch_args, ("--dangerously-skip-permissions",))

    def test_prompt_keywords_ride_the_brief_paste_and_are_recorded(self) -> None:
        # The original L16 acceptance case: strategist as effort:max + promptKeywords:["ultracode"]
        # dispatches claude with --effort max and the keyword riding the paste.
        paster = _FakePaster()
        payload = self._spawn(
            effort="max",
            prompt_keywords=["ultracode"],
            context="You are the strategist.",
            paster=paster,
        )
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            self.host.ensured[0]["command"], ("claude", "--effort", "max")
        )
        self.assertEqual(paster.calls[0]["text"], "ultracode\n\nYou are the strategist.")
        self.assertEqual(payload["promptKeywords"], ["ultracode"])
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.prompt_keywords, ("ultracode",))

    def test_prompt_keywords_without_a_brief_are_still_delivered(self) -> None:
        paster = _FakePaster()
        payload = self._spawn(prompt_keywords=["ultracode"], paster=paster)
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(paster.calls[0]["text"], "ultracode")
        self.assertTrue(payload["contextDelivered"])

    def test_session_command_order_is_effort_vehicle_then_free_form_then_brief(self) -> None:
        paster = _FakePaster()
        payload = self._spawn(
            effort="ultracode",
            session_commands=["/statusline off"],
            prompt_keywords=["ultracode"],
            context="brief",
            paster=paster,
        )
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            [call["text"] for call in paster.calls],
            ["/effort ultracode", "/statusline off", "ultracode\n\nbrief"],
        )
        # The RESOLVED session-command list (effort vehicle first) is the recorded provenance.
        self.assertEqual(payload["sessionCommands"], ["/effort ultracode", "/statusline off"])
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.session_commands, ("/effort ultracode", "/statusline off"))
        self.assertEqual(row.prompt_keywords, ("ultracode",))

    def test_undelivered_session_command_is_reported_with_capture(self) -> None:
        paster = _FakePaster(delivered=False, submitted=False, capture="claude> (booting)")
        payload = self._spawn(effort="ultracode", paster=paster)
        self.assertEqual(payload["status"], "spawned")
        self.assertFalse(payload["sessionCommandsDelivered"])
        # 260707-HFX-L3: the failing paste's pane capture rides the payload as evidence.
        self.assertEqual(payload["deliveryCapture"], "claude> (booting)")


class SettingsDefinedHarnessTests(unittest.TestCase):
    """260703-L16 registry openness: orchestration.harnesses entries ADD new harnesses or OVERRIDE
    builtin defaults; unknown-everywhere ids refuse pointing at the manual; vocab-less user
    harnesses refuse the model/effort knobs with guidance."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.coordination_root = self.tmp / "ar-coordination"
        self.repo_root = self.tmp / "workspace" / "repo-a"
        self.repo_root.mkdir(parents=True)
        _write_leaf_task(self.coordination_root, repo="repo-a")
        self.config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.coordination_root,
            workspace_root=self.tmp / "workspace",
            transcript_root=self.tmp / "logs" / "mcp",
            repositories={
                "repo-a": RepositoryScope(repo_id="repo-a", path=self.repo_root)
            },
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
            "session_id": "worker-1",
            "host": self.host,
            "which": _detected,
        }
        base.update(kwargs)
        return spawn_agent_session_payload(self.config, **base)  # type: ignore[arg-type]

    def test_settings_defined_harness_spawns_with_its_argv(self) -> None:
        self._write_settings(
            self.coordination_root,
            {"harnesses": {"hermes": {"command": "hermes", "argv": ["hermes", "--tui"]}}},
        )
        payload = self._spawn(harness="hermes")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["harness"], "hermes")
        self.assertEqual(self.host.ensured[0]["command"], ("hermes", "--tui"))

    def test_builtin_override_replaces_the_argv(self) -> None:
        self._write_settings(
            self.coordination_root,
            {"harnesses": {"claude": {"argv": ["claude", "--continue"]}}},
        )
        payload = self._spawn(harness="claude", effort="max")
        self.assertEqual(payload["status"], "spawned")
        # The user's command array replaces ours; the builtin knob mapping survives the override.
        self.assertEqual(
            self.host.ensured[0]["command"], ("claude", "--continue", "--effort", "max")
        )

    def test_unknown_everywhere_harness_refuses_naming_the_manual(self) -> None:
        payload = self._spawn(harness="hermes")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "harness-unknown")
        self.assertIn("'hermes'", payload["detail"])
        self.assertIn("claude, codex, pi", payload["detail"])
        self.assertIn("orchestration.harnesses", payload["detail"])
        self.assertIn("docs/reference/harnesses.md", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_vocab_less_settings_harness_refuses_effort_with_guidance(self) -> None:
        self._write_settings(
            self.coordination_root, {"harnesses": {"hermes": {"command": "hermes"}}}
        )
        payload = self._spawn(harness="hermes", effort="high")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "effort-invalid")
        self.assertIn("effortFlag", payload["detail"])
        self.assertIn("launchArgs", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_vocab_less_settings_harness_refuses_model_with_guidance(self) -> None:
        self._write_settings(
            self.coordination_root, {"harnesses": {"hermes": {"command": "hermes"}}}
        )
        payload = self._spawn(harness="hermes", model="opus")
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
                }
            },
        )
        payload = self._spawn(harness="hermes", model="h-1", effort="high")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("hermes", "--model", "h-1", "--reasoning", "high"),
        )

    def test_repo_local_harness_entry_overrides_global(self) -> None:
        self._write_settings(
            self.coordination_root,
            {"harnesses": {"hermes": {"command": "hermes", "argv": ["hermes", "--global"]}}},
        )
        self._write_settings(
            self.repo_root,
            {"harnesses": {"hermes": {"argv": ["hermes", "--local"]}}},
        )
        payload = self._spawn(harness="hermes", leaf_key="repo-a/master/leaf-1")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(self.host.ensured[0]["command"], ("hermes", "--local"))
        # A leafless spawn resolves the GLOBAL layer only (no repo-local override).
        payload = self._spawn(harness="hermes", session_id="worker-2")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(self.host.ensured[-1]["command"], ("hermes", "--global"))


class SpawnLevelResolutionTests(unittest.TestCase):
    """260703-L16 (ruling 2026-07-07T08:15): the dispatch level parameter + rolesPerLevel
    resolution -- explicit args > repo-local level override > global level override > repo-local
    role default > global role default > detection-gated default -- with the resolved level
    recorded in spawn provenance."""

    # The developer's canonical reviewer economics (docs/reference/harnesses.md walks this).
    ECONOMICS: ClassVar[dict] = {
        "roles": {
            "reviewer": {"harness": "claude", "model": "sonnet", "effort": "high"}
        },
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
        self.config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.coordination_root,
            workspace_root=self.tmp / "workspace",
            transcript_root=self.tmp / "logs" / "mcp",
            repositories={
                "repo-a": RepositoryScope(repo_id="repo-a", path=self.repo_root)
            },
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
        return spawn_agent_session_payload(self.config, **base)  # type: ignore[arg-type]

    def test_level_override_deep_merges_harness_inherited(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        payload = self._spawn(env={"AR_SPAWN_ROLE": "reviewer"}, level="master")
        self.assertEqual(payload["status"], "spawned")
        # harness inherited from the flat default; model/effort from the master override.
        self.assertEqual(payload["harness"], "claude")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--model", "opus", "--effort", "xhigh"),
        )
        # The resolved knobs also ride the env for session-start visibility.
        spawn_env = self.host.ensured[0]["env"]
        assert isinstance(spawn_env, dict)
        self.assertEqual(spawn_env["AR_SPAWN_MODEL"], "opus")
        self.assertEqual(spawn_env["AR_SPAWN_EFFORT"], "xhigh")

    def test_leaf_default_uses_the_flat_role_knobs(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        payload = self._spawn(env={"AR_SPAWN_ROLE": "reviewer"})
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--model", "sonnet", "--effort", "high"),
        )
        self.assertEqual(payload["spawnLevel"], "leaf")
        self.assertEqual(payload["spawnLevelSource"], "default")

    def test_portfolio_ultracode_rides_the_session_vehicle(self) -> None:
        # The economics end-to-end: portfolio reviewer = fable + ultracode -- the session-value
        # effort must stay OFF the flag and arrive as the /effort session command.
        self._write_settings(self.coordination_root, self.ECONOMICS)
        paster = _FakePaster()
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "reviewer"}, level="portfolio", paster=paster
        )
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            self.host.ensured[0]["command"], ("claude", "--model", "fable")
        )
        self.assertEqual(paster.calls[0]["text"], "/effort ultracode")
        self.assertEqual(payload["sessionCommands"], ["/effort ultracode"])

    def test_explicit_args_beat_every_settings_rung(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "reviewer"}, level="master", model="haiku", effort="low"
        )
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--model", "haiku", "--effort", "low"),
        )

    def test_repo_local_level_override_beats_global_level_override(self) -> None:
        self._write_settings(self.coordination_root, self.ECONOMICS)
        self._write_settings(
            self.repo_root,
            {"rolesPerLevel": {"master": {"reviewer": {"model": "fable"}}}},
        )
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "reviewer"},
            level="master",
            leaf_key="repo-a/master/leaf-1",
        )
        self.assertEqual(payload["status"], "spawned")
        # model: repo-local master override; effort: global master override; harness: flat default.
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--model", "fable", "--effort", "xhigh"),
        )

    def test_provenance_records_the_resolved_level_and_source(self) -> None:
        payload = self._spawn(harness="claude", level="master")
        self.assertEqual(payload["spawnLevel"], "master")
        self.assertEqual(payload["spawnLevelSource"], "explicit")
        row = self.catalog.get("seat-1")
        assert row is not None
        self.assertEqual(row.spawn_level, "master")
        self.assertEqual(row.spawn_level_source, "explicit")
        self.assertEqual(row.to_json()["spawnLevel"], "master")

    def test_default_level_dispatch_is_unchanged_for_existing_callers(self) -> None:
        # No settings file, no level, no role env: exactly the pre-L16 dispatch (plain argv),
        # with the defaulted level recorded as provenance.
        payload = self._spawn(harness="claude")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(self.host.ensured[0]["command"], ("claude",))
        self.assertEqual(payload["spawnLevel"], "leaf")
        self.assertEqual(payload["spawnLevelSource"], "default")

    def test_unknown_level_refuses_before_spawning(self) -> None:
        payload = self._spawn(harness="claude", level="epic")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "level-invalid")
        self.assertIn("'epic'", payload["detail"])
        self.assertIn("leaf, master, portfolio", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_role_free_form_knobs_flow_from_settings(self) -> None:
        # The strategist acceptance case expressed purely in settings: effort ultracode +
        # promptKeywords ride the dispatch with NO explicit knob arguments.
        self._write_settings(
            self.coordination_root,
            {
                "roles": {
                    "strategist": {
                        "harness": "claude",
                        "effort": "ultracode",
                        "promptKeywords": ["ultracode"],
                    }
                }
            },
        )
        paster = _FakePaster()
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "strategist"},
            context="You are the strategist.",
            paster=paster,
        )
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(self.host.ensured[0]["command"], ("claude",))
        self.assertEqual(
            [call["text"] for call in paster.calls],
            ["/effort ultracode", "ultracode\n\nYou are the strategist."],
        )
        row = self.catalog.get("seat-1")
        assert row is not None
        self.assertEqual(row.prompt_keywords, ("ultracode",))
        self.assertEqual(row.session_commands, ("/effort ultracode",))


class SpawnHarnessResolutionTests(unittest.TestCase):
    """The L13 spawn seam: explicit arg > repo-local settings > global settings >
    detection-gated default, read per-use through the agentic-settings loader."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.coordination_root = self.tmp / "ar-coordination"
        self.repo_root = self.tmp / "workspace" / "repo-a"
        self.repo_root.mkdir(parents=True)
        _write_leaf_task(self.coordination_root, repo="repo-a")
        _write_leaf_task(self.coordination_root, repo="not-a-repo", doc_id="leaf-9", slug="leaf-9")
        self.config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.coordination_root,
            workspace_root=self.tmp / "workspace",
            transcript_root=self.tmp / "logs" / "mcp",
            repositories={
                "repo-a": RepositoryScope(repo_id="repo-a", path=self.repo_root)
            },
        )
        self.host = _FakeHost()
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _write_settings(self, root: Path, harness: str) -> None:
        path = agentic_settings_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'{{"orchestration": {{"spawn": {{"harness": "{harness}"}}}}}}',
            encoding="utf-8",
        )

    def _spawn(self, **kwargs: object) -> dict:
        base: dict[str, object] = {
            "session_id": "worker-1",
            "host": self.host,
            "which": _detected,
        }
        base.update(kwargs)
        return spawn_agent_session_payload(self.config, **base)  # type: ignore[arg-type]

    def test_omitted_harness_uses_the_global_settings_preference(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        payload = self._spawn()
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["harness"], "codex")

    def test_repo_local_settings_override_global_via_the_qualified_leaf_key(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        self._write_settings(self.repo_root, "pi")
        payload = self._spawn(leaf_key="repo-a/master/leaf-1")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["harness"], "pi")

    def test_leafless_spawn_reads_the_global_layer_only(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        self._write_settings(self.repo_root, "pi")
        payload = self._spawn()
        self.assertEqual(payload["harness"], "codex")

    def test_explicit_argument_beats_every_settings_layer(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        self._write_settings(self.repo_root, "pi")
        payload = self._spawn(harness="claude", leaf_key="repo-a/master/leaf-1")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["harness"], "claude")

    def test_no_settings_falls_back_to_the_first_detected_registry_harness(self) -> None:
        payload = self._spawn(
            which=lambda cmd: "/usr/bin/codex" if cmd == "codex" else None
        )
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["harness"], "codex")

    def test_nothing_detected_is_a_refusal_not_a_silent_default(self) -> None:
        payload = self._spawn(which=lambda _cmd: None)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "harness-not-detected")
        self.assertIn("none detected", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_configured_but_undetected_preference_is_refused_naming_the_source(self) -> None:
        self._write_settings(self.coordination_root, "pi")
        payload = self._spawn(which=lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "harness-not-detected")
        self.assertIn("orchestration.spawn.harness", payload["detail"])
        self.assertIn(str(agentic_settings_path(self.coordination_root)), payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_unconfigured_leaf_repo_segment_resolves_globally(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        payload = self._spawn(leaf_key="not-a-repo/master/leaf-9")
        self.assertEqual(payload["status"], "spawned")
        self.assertEqual(payload["harness"], "codex")


class TerminalPasteEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _config(self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "logs" / "dashboard" / "terminal-sessions.json")
        self.host = _FakeHost()
        self.host.known.add("ar-live")
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="live",
                label="Claude Code",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=self.tmp,
                tmux_name="ar-live",
                command=("claude",),
                created_at="2026-07-04T00:00:00Z",
                last_attached_at="2026-07-04T00:00:00Z",
                status="running",
            )
        )

    def _client(self, paster: _FakePaster) -> TestClient:
        app = create_app(
            self.config,
            interval=100,
            terminal_host=self.host,  # type: ignore[arg-type]
            terminal_catalog=self.catalog,
            terminal_paster=paster,  # type: ignore[arg-type]
        )
        return TestClient(app)

    def test_paste_endpoint_delivers_and_submits(self) -> None:
        paster = _FakePaster(delivered=True, submitted=True)
        with self._client(paster) as client:
            response = client.post(
                "/api/terminal/live/paste", json={"text": "hello worker", "submit": True}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "delivered")
        self.assertTrue(body["delivered"])
        self.assertTrue(body["submitted"])
        self.assertNotIn("capture", body)  # full success ships no failure evidence
        self.assertEqual(paster.calls[0]["tmux"], "ar-live")

    def test_paste_endpoint_unconfirmed_submit_ships_the_pane_capture(self) -> None:
        # Review N3: delivered but the REQUESTED submit did not advance the pane -- still a failed
        # outcome, so the capture rides the response (parity with the spawn seam).
        paster = _FakePaster(delivered=True, submitted=False, capture="claude> draft sitting")
        with self._client(paster) as client:
            response = client.post(
                "/api/terminal/live/paste", json={"text": "hello", "submit": True}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["delivered"])
        self.assertFalse(body["submitted"])
        self.assertEqual(body["capture"], "claude> draft sitting")

    def test_paste_endpoint_unconfirmed_ships_the_pane_capture(self) -> None:
        # 260707-HFX-L3 loud failure at the HTTP seam too: an unconfirmed paste carries the pane
        # capture so the caller can see what the target composer actually showed.
        paster = _FakePaster(delivered=False, submitted=False, capture="claude> (still booting)")
        with self._client(paster) as client:
            response = client.post("/api/terminal/live/paste", json={"text": "hello"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "unconfirmed")
        self.assertFalse(body["delivered"])
        self.assertEqual(body["capture"], "claude> (still booting)")

    def test_paste_endpoint_delivered_omits_the_capture(self) -> None:
        paster = _FakePaster(delivered=True, submitted=False, capture="claude> [Pasted text #1]")
        with self._client(paster) as client:
            response = client.post("/api/terminal/live/paste", json={"text": "hello"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("capture", response.json())

    def test_paste_endpoint_unknown_session_is_404(self) -> None:
        paster = _FakePaster()
        with self._client(paster) as client:
            response = client.post("/api/terminal/ghost/paste", json={"text": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-session")
        self.assertEqual(paster.calls, [])


if __name__ == "__main__":
    unittest.main()
