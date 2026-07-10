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
        # The knob env was seeded into the detached tmux spawn.
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "high", "AR_SPAWN_ROLE": "worker"},
        )
        # Provenance survives the catalog round-trip (migration-safe camelCase keys).
        self.assertEqual(entry.to_json()["spawnedBySession"], "manager-9")
        self.assertEqual(entry.to_json()["spawnedByLifecycle"], "LC-manager")
        self.assertEqual(entry.to_json()["spawnRole"], "worker")

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
        result = self._open(session_id="intruder", leaf_key="repo/master/leaf-1")
        self.assertEqual(result.status, "leaf-taken")
        self.assertEqual(result.owner_session_id, "owner-1")
        # Never spawned, never upserted the intruder.
        self.assertEqual(self.host.ensured, [])
        self.assertIsNone(self.catalog.get("intruder"))

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
    """260703-L16: the opener maps the env-riding knobs onto the harness argv per-harness via the
    registry (the env keeps riding for session-start visibility), applies launch_args verbatim, and
    records the free-form escape hatch on the durable row as spawn provenance."""

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

    def test_claude_env_knobs_ride_the_argv_as_flags(self) -> None:
        result = self._open(env={"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "max"})
        self.assertEqual(result.status, "opened")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--model", "opus", "--effort", "max"),
        )
        # The env vars KEEP riding for session-start visibility.
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "max"},
        )

    def test_session_vocabulary_effort_stays_off_the_flag(self) -> None:
        result = self._open(env={"AR_SPAWN_EFFORT": "ultracode"})
        self.assertEqual(result.status, "opened")
        # No --effort flag: the claude CLI would warn-and-degrade on it; the session vehicle
        # ("/effort ultracode") is delivered by the dispatch layer, not the launch argv.
        self.assertEqual(self.host.ensured[0]["command"], ("claude",))
        self.assertEqual(self.host.ensured[0]["env"], {"AR_SPAWN_EFFORT": "ultracode"})

    def test_unknown_effort_refuses_naming_harness_and_both_sets(self) -> None:
        result = self._open(env={"AR_SPAWN_EFFORT": "turbo"})
        self.assertEqual(result.status, "bad-kind")
        assert result.detail is not None
        self.assertIn("'turbo'", result.detail)
        self.assertIn("'claude'", result.detail)
        self.assertIn("low, medium, high, xhigh, max", result.detail)
        self.assertIn("ultracode", result.detail)
        # Refused BEFORE spawning anything.
        self.assertEqual(self.host.ensured, [])
        self.assertIsNone(self.catalog.get("worker-1"))

    def test_codex_knobs_are_explicit_argv(self) -> None:
        result = self._open(
            harness="codex", env={"AR_SPAWN_MODEL": "gpt-5.6-sol", "AR_SPAWN_EFFORT": "xhigh"}
        )
        self.assertEqual(result.status, "opened")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("codex", "--model", "gpt-5.6-sol", "--config", "model_reasoning_effort=xhigh"),
        )
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_MODEL": "gpt-5.6-sol", "AR_SPAWN_EFFORT": "xhigh"},
        )

    def test_launch_args_append_verbatim_after_the_knob_flags(self) -> None:
        result = self._open(
            env={"AR_SPAWN_EFFORT": "max"},
            launch_args=["--dangerously-skip-permissions", "--foo", "bar"],
        )
        self.assertEqual(result.status, "opened")
        self.assertEqual(
            self.host.ensured[0]["command"],
            ("claude", "--effort", "max", "--dangerously-skip-permissions", "--foo", "bar"),
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
        self.assertEqual(self.host.ensured[0]["command"], ("hermes", "--tui"))

    def test_unknown_everywhere_harness_refuses_pointing_at_the_manual(self) -> None:
        result = self._open(harness="hermes")
        self.assertEqual(result.status, "bad-kind")
        assert result.detail is not None
        self.assertIn("'hermes'", result.detail)
        self.assertIn("claude, codex, pi", result.detail)
        self.assertIn("orchestration.harnesses", result.detail)
        self.assertIn("docs/reference/harnesses.md", result.detail)
        self.assertEqual(self.host.ensured, [])

    def test_vocab_less_settings_harness_refuses_the_effort_knob_with_guidance(self) -> None:
        hermes = Harness(
            id="hermes", name="Hermes", command="hermes", argv=("hermes",), defined_in="settings"
        )
        result = self._open(
            harness="hermes", harnesses=(hermes,), env={"AR_SPAWN_EFFORT": "high"}
        )
        self.assertEqual(result.status, "bad-kind")
        assert result.detail is not None
        self.assertIn("'hermes'", result.detail)
        self.assertIn("effortFlag", result.detail)
        self.assertIn("launchArgs", result.detail)
        self.assertEqual(self.host.ensured, [])


if __name__ == "__main__":
    unittest.main()
