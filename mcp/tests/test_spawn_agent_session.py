"""Tests for the agent-facing ``spawn_agent_session`` MCP tool + the serving paste endpoint (slice L2).

The tool composes the EXISTING session primitives (opener + leaf claim + echo-confirmed paste + submit).
These tests inject a fake host + fake paster + a fake ``which`` so the composition is exercised without a
real tmux server, and drive the ``POST /api/terminal/{session}/paste`` endpoint through ``TestClient``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig
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


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"


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
    def __init__(self, *, delivered: bool = True, submitted: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self._delivered = delivered
        self._submitted = submitted

    def paste(self, tmux_name: str, text: str, *, submit: bool = False, **_kwargs: object) -> PasteResult:
        self.calls.append({"tmux": tmux_name, "text": text, "submit": submit})
        return PasteResult(
            delivered=self._delivered, submitted=self._submitted if submit else False
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
        self.assertEqual(paster.calls[0]["tmux"], "ar-live")

    def test_paste_endpoint_unknown_session_is_404(self) -> None:
        paster = _FakePaster()
        with self._client(paster) as client:
            response = client.post("/api/terminal/ghost/paste", json={"text": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-session")
        self.assertEqual(paster.calls, [])


if __name__ == "__main__":
    unittest.main()
