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
            env={"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "high"},
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
        # The knob env was seeded into the detached tmux spawn.
        self.assertEqual(
            self.host.ensured[0]["env"], {"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "high"}
        )
        # Provenance survives the catalog round-trip (migration-safe camelCase keys).
        self.assertEqual(entry.to_json()["spawnedBySession"], "manager-9")
        self.assertEqual(entry.to_json()["spawnedByLifecycle"], "LC-manager")

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


if __name__ == "__main__":
    unittest.main()
