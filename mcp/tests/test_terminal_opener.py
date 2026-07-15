"""Tests for the shared hosted-session opener (``serving.terminal_opener``, slice L2).

The opener is the ONE spawn path both the dashboard route and the agent-facing ``spawn_agent_session``
MCP tool compose over. These tests drive it against a fake host (records the ``ensure`` call, no real
tmux) + a real JSON catalog, asserting the leaf-claim / provenance / env-seed behaviour that both call
paths inherit.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.harness_control_runner import parse_runner_config
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.harnesses import Harness
from agents_remember.serving.terminal import TerminalSessionBinding
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_opener import open_terminal_session


class _FakeHost:
    """A `TerminalHost` duck-type: ``ensure`` records its call and creates a detached-session binding."""

    def __init__(self) -> None:
        self.ensured: list[dict[str, object]] = []
        self.known: set[str] = set()

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known

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
        self.ensured.append(
            {
                "sid": sid,
                "cwd": Path(cwd),
                "command": tuple(command),
                "lifecycle_id": lifecycle_id,
                "suspend_unsafe": suspend_unsafe,
                "env": dict(env or {}),
            }
        )
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=Path(cwd),
            command=tuple(command),
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"


def _runner_config(host: _FakeHost):
    command = host.ensured[0]["command"]
    assert isinstance(command, tuple)
    assert command[1:3] == ("-m", "agents_remember.serving.harness_control_runner")
    return parse_runner_config(command[3])


def _running_chat(
    session_id: str,
    *,
    leaf_key: str,
    spawn_role: str | None = None,
) -> TerminalCatalogEntry:
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
        spawn_role=spawn_role,
    )


class OpenTerminalSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = _FakeHost()

    def _open(self, **kwargs: object):
        base: dict[str, object] = {
            "catalog": self.catalog,
            "host": self.host,
            "session_id": "worker-1",
            "kind": "harness",
            "workspace_root": self.tmp,
            "shell": "/bin/bash",
            "harness": "claude",
            "which": _detected,
        }
        base.update(kwargs)
        return open_terminal_session(**base)  # type: ignore[arg-type]

    def test_opened_records_provenance_env_and_leaf(self) -> None:
        result = self._open(
            leaf_key="repo/master/leaf-1",
            env={"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "high", "AR_SPAWN_ROLE": "worker"},
            spawned_by_session="manager-9",
            spawned_by_lifecycle="LC-manager",
        )
        self.assertEqual(result.status, "opened")
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.leaf_key, "repo/master/leaf-1")
        self.assertEqual(entry.spawned_by_session, "manager-9")
        self.assertEqual(entry.spawned_by_lifecycle, "LC-manager")
        self.assertEqual(entry.harness, "claude")
        # The AR_SPAWN_ROLE riding the spawn env is recorded on the durable row (L14).
        self.assertEqual(entry.spawn_role, "worker")
        self.assertEqual(entry.binding_role, "worker")
        # The knob env was seeded into the detached tmux spawn.
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "high", "AR_SPAWN_ROLE": "worker"},
        )
        # Provenance survives the catalog round-trip (migration-safe camelCase keys).
        self.assertEqual(entry.to_json()["spawnedBySession"], "manager-9")
        self.assertEqual(entry.to_json()["spawnedByLifecycle"], "LC-manager")
        self.assertEqual(entry.to_json()["spawnRole"], "worker")
        self.assertEqual(entry.to_json()["seatRole"], "worker")
        self.assertEqual(entry.control_state, "starting")
        self.assertIsNotNone(entry.control_endpoint)
        self.assertEqual(entry.control_protocol, "ar-harness-control/v1")

    def test_future_bridge_endpoint_is_additive_control_metadata(self) -> None:
        endpoint = self.tmp / "control" / "worker.sock"
        self._open(control_endpoint=endpoint)
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.control_endpoint, endpoint)
        self.assertEqual(entry.to_json()["controlEndpoint"], str(endpoint))

    def test_reopen_preserves_spawn_role_and_hand_open_records_none(self) -> None:
        # Role provenance is set once at first spawn and survives a role-less re-open (the same
        # `replace`-preserving rule as leaf_key); a hand-opened session records no role at all.
        self._open(env={"AR_SPAWN_ROLE": "manager"})
        self._open()  # re-open with no env — must not drop the recorded role
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.spawn_role, "manager")

        self._open(session_id="hand-opened")
        hand_opened = self.catalog.get("hand-opened")
        assert hand_opened is not None
        self.assertIsNone(hand_opened.spawn_role)
        self.assertNotIn("spawnRole", hand_opened.to_json())

    def test_leaf_taken_surfaces_owner_without_spawning(self) -> None:
        self.catalog.upsert(_running_chat("owner-1", leaf_key="repo/master/leaf-1"))
        self.host.known.add("ar-owner-1")
        result = self._open(session_id="intruder", leaf_key="repo/master/leaf-1")
        self.assertEqual(result.status, "leaf-taken")
        self.assertEqual(result.owner_session_id, "owner-1")
        # Never spawned, never upserted the intruder.
        self.assertEqual(self.host.ensured, [])
        self.assertIsNone(self.catalog.get("intruder"))

    def test_different_roles_share_leaf_and_dead_same_role_is_replaced(self) -> None:
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(_running_chat("worker", leaf_key=leaf, spawn_role="worker"))
        self.host.known.add("ar-worker")

        reviewer = self._open(
            session_id="reviewer",
            leaf_key=leaf,
            env={"AR_SPAWN_ROLE": "reviewer"},
        )
        self.assertEqual(reviewer.status, "opened")

        self.host.known.discard("ar-worker")
        replacement = self._open(
            session_id="worker-2",
            leaf_key=leaf,
            env={"AR_SPAWN_ROLE": "worker"},
        )
        self.assertEqual(replacement.status, "opened")
        prior_worker = self.catalog.get("worker")
        next_worker = self.catalog.get("worker-2")
        reviewer_entry = self.catalog.get("reviewer")
        assert prior_worker is not None and next_worker is not None and reviewer_entry is not None
        self.assertEqual(prior_worker.status, "exited")
        self.assertEqual(next_worker.binding_role, "worker")
        self.assertEqual(reviewer_entry.binding_role, "reviewer")

    def test_pipeline_roles_and_manager_anchor_share_one_canonical_leaf(self) -> None:
        leaf = "repo/master/leaf-1"
        first_worker = self._open(
            session_id="worker-1",
            leaf_key=leaf,
            env={"AR_SPAWN_ROLE": "worker"},
        )
        self.assertEqual(first_worker.status, "opened")
        self.catalog.mark_exited("worker-1")

        for session_id, role in (
            ("curator", "curator"),
            ("worker-2", "worker"),
            ("reviewer", "reviewer"),
            ("manager", "manager"),
        ):
            result = self._open(
                session_id=session_id,
                leaf_key=leaf,
                env={"AR_SPAWN_ROLE": role},
            )
            self.assertEqual(result.status, "opened")

        live = {
            entry.id: entry.binding_role
            for entry in self.catalog.list()
            if entry.leaf_key == leaf and entry.status == "running"
        }
        self.assertEqual(
            live,
            {
                "curator": "curator",
                "worker-2": "worker",
                "reviewer": "reviewer",
                "manager": "manager",
            },
        )

    def test_bad_kind_reports_detail(self) -> None:
        result = self._open(kind="bogus")
        self.assertEqual(result.status, "bad-kind")
        self.assertIsNotNone(result.detail)
        self.assertEqual(self.host.ensured, [])

    def test_undetected_harness_is_bad_kind(self) -> None:
        result = self._open(which=lambda _cmd: None)
        self.assertEqual(result.status, "bad-kind")
        self.assertEqual(self.host.ensured, [])


class KnobApplicationTests(unittest.TestCase):
    """The opener carries one typed launch to the runner and preserves spawn provenance."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = _FakeHost()

    def _open(self, **kwargs: object):
        base: dict[str, object] = {
            "catalog": self.catalog,
            "host": self.host,
            "session_id": "worker-1",
            "kind": "harness",
            "workspace_root": self.tmp,
            "shell": "/bin/bash",
            "harness": "claude",
            "which": _detected,
        }
        base.update(kwargs)
        return open_terminal_session(**base)  # type: ignore[arg-type]

    def test_claude_resolved_launch_rides_the_runner_payload(self) -> None:
        resolved = ResolvedLaunch("claude", "claude-fable-5", "max", self.tmp)
        result = self._open(
            env={"AR_SPAWN_MODEL": "claude-fable-5", "AR_SPAWN_EFFORT": "max"},
            resolved_launch=resolved,
        )
        self.assertEqual(result.status, "opened")
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude",))
        self.assertEqual(runner.resolved_launch, resolved)
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_MODEL": "claude-fable-5", "AR_SPAWN_EFFORT": "max"},
        )

    def test_no_effort_is_synthesized_as_a_session_command(self) -> None:
        resolved = ResolvedLaunch("claude", "claude-fable-5", "ultracode", self.tmp)
        result = self._open(resolved_launch=resolved)
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).session_commands, ())

    def test_codex_selection_is_structured_not_a_tui_argv_override(self) -> None:
        resolved = ResolvedLaunch("codex", "gpt-5.6-sol", "xhigh", self.tmp)
        result = self._open(
            harness="codex",
            env={"AR_SPAWN_MODEL": "gpt-5.6-sol", "AR_SPAWN_EFFORT": "xhigh"},
            resolved_launch=resolved,
        )
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).argv, ("codex",))
        self.assertEqual(_runner_config(self.host).resolved_launch, resolved)
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_MODEL": "gpt-5.6-sol", "AR_SPAWN_EFFORT": "xhigh"},
        )

    def test_launch_args_remain_on_the_base_command_before_adapter_preparation(self) -> None:
        result = self._open(
            launch_args=["--dangerously-skip-permissions", "--foo", "bar"],
        )
        self.assertEqual(result.status, "opened")
        self.assertEqual(
            _runner_config(self.host).argv,
            ("claude", "--dangerously-skip-permissions", "--foo", "bar"),
        )

    def test_free_form_provenance_is_recorded_and_round_trips(self) -> None:
        self._open(
            launch_args=["--foo"],
            prompt_keywords=["ultracode"],
            session_commands=["/effort ultracode"],
        )
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.launch_args, ("--foo",))
        self.assertEqual(entry.prompt_keywords, ("ultracode",))
        self.assertEqual(entry.session_commands, ("/effort ultracode",))
        as_json = entry.to_json()
        self.assertEqual(as_json["launchArgs"], ["--foo"])
        self.assertEqual(as_json["promptKeywords"], ["ultracode"])
        self.assertEqual(as_json["sessionCommands"], ["/effort ultracode"])
        # Preserved across a free-form-less re-open (the write-once-preserve provenance rule).
        self._open()
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.launch_args, ("--foo",))
        # A hand-opened row records none of them (migration-safe absent keys).
        self._open(session_id="hand-opened")
        hand_opened = self.catalog.get("hand-opened")
        assert hand_opened is not None
        self.assertIsNone(hand_opened.launch_args)
        self.assertNotIn("launchArgs", hand_opened.to_json())

    def test_effective_registry_resolves_settings_defined_harness(self) -> None:
        hermes = Harness(
            id="hermes",
            name="Hermes",
            command="hermes",
            argv=("hermes", "--tui"),
            defined_in="settings",
        )
        result = self._open(harness="hermes", harnesses=(hermes,))
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).argv, ("hermes", "--tui"))
        self.assertEqual(result.entry.control_state if result.entry else None, "unsupported")

    def test_unknown_everywhere_harness_refuses_pointing_at_the_manual(self) -> None:
        result = self._open(harness="hermes")
        self.assertEqual(result.status, "bad-kind")
        assert result.detail is not None
        self.assertIn("'hermes'", result.detail)
        self.assertIn("claude, codex, pi", result.detail)
        self.assertIn("orchestration.harnesses", result.detail)
        self.assertIn("docs/reference/harnesses.md", result.detail)
        self.assertEqual(self.host.ensured, [])

    def test_custom_harness_launch_mapping_is_not_guessed_by_the_native_opener(self) -> None:
        hermes = Harness(
            id="hermes", name="Hermes", command="hermes", argv=("hermes",), defined_in="settings"
        )
        result = self._open(
            harness="hermes", harnesses=(hermes,), env={"AR_SPAWN_EFFORT": "high"}
        )
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).argv, ("hermes",))


if __name__ == "__main__":
    unittest.main()
