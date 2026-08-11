from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.observer import reset_ambient
from agents_remember.serving.app import ServingCollaborators, create_app
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from fastapi.testclient import TestClient
from test_spawn_agent_session import (
    _config,
    _detected,
    _FakeHost,
    _FakePaster,
    _write_leaf_task,
    call_spawn,
)


class SpawnHarnessResolutionTests(unittest.TestCase):
    """The spawn seam: repo-local settings > global settings >
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
            repositories={"repo-a": RepositoryScope(repo_id="repo-a", path=self.repo_root)},
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_spawn_agent_session_resolution.py:57).
    def _spawn(self, **kwargs: object) -> dict:  # pragma: no cover
        base: dict[str, object] = {
            "session_id": "worker-1",
            "host": self.host,
            "which": _detected,
        }
        base.update(kwargs)
        paster = base.get("paster")
        if "session_log" not in base and isinstance(paster, _FakePaster):
            base["session_log"] = paster.log
        return call_spawn(self.config, **base)

    def test_omitted_harness_uses_the_global_settings_preference(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        payload = self._spawn()
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(payload["harness"], "codex")

    def test_repo_local_settings_override_global_via_the_task_document_ref(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        self._write_settings(self.repo_root, "pi")
        payload = self._spawn(
            task_document_ref=TaskDocumentRef(repository="repo-a", path="master/leaf-1.json")
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(payload["harness"], "pi")

    def test_leafless_spawn_reads_the_global_layer_only(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        self._write_settings(self.repo_root, "pi")
        payload = self._spawn()
        self.assertEqual(payload["harness"], "codex")

    def test_legacy_harness_argument_is_refused_instead_of_beating_settings(self) -> None:
        self._write_settings(self.coordination_root, "codex")
        self._write_settings(self.repo_root, "pi")
        payload = self._spawn(
            harness="claude",
            task_document_ref=TaskDocumentRef(repository="repo-a", path="master/leaf-1.json"),
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "spend-override-unsupported")
        self.assertIn("harness", payload["detail"])
        self.assertEqual(self.host.ensured, [])

    def test_no_settings_falls_back_to_the_first_detected_registry_harness(self) -> None:
        payload = self._spawn(which=lambda cmd: "/usr/bin/codex" if cmd == "codex" else None)
        self.assertEqual(payload["status"], "spawned-unbriefed")
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
        payload = self._spawn(
            task_document_ref=TaskDocumentRef(repository="not-a-repo", path="master/leaf-9.json")
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
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
                label="Terminal",
                kind="terminal",
                harness=None,
                lifecycle_id=None,
                cwd=self.tmp,
                tmux_name="ar-live",
                command=("/bin/bash",),
                created_at="2026-07-04T00:00:00Z",
                last_attached_at="2026-07-04T00:00:00Z",
                status="running",
            )
        )
        self._current_paster = _FakePaster()

    def _client(self, paster: _FakePaster) -> TestClient:
        self._current_paster = paster
        app = create_app(
            self.config,
            cadence=ProjectionCadence(interval=100),
            collaborators=ServingCollaborators(
                terminal_host=self.host,  # type: ignore[arg-type]
                terminal_catalog=self.catalog,
                terminal_paster=paster,  # type: ignore[arg-type]
            ),
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

    def test_plain_terminal_submit_uses_transport_evidence_without_harness_logs(self) -> None:
        paster = _FakePaster(delivered=True, submitted=False, capture="claude> draft sitting")
        with self._client(paster) as client:
            response = client.post(
                "/api/terminal/live/paste", json={"text": "hello", "submit": True}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["delivered"])
        self.assertTrue(body["submitted"])
        self.assertNotIn("capture", body)

    def test_paste_endpoint_unconfirmed_ships_the_pane_capture(self) -> None:
        # Loud failure at the HTTP seam too: an unconfirmed paste carries the pane
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

    def test_legacy_harness_never_falls_back_to_raw_paste(self) -> None:
        entry = self.catalog.get("live")
        assert entry is not None
        self.catalog.upsert(
            replace(
                entry,
                kind="harness",
                harness="claude",
                command=("claude",),
                control_state="unsupported",
                control_endpoint=None,
            )
        )
        paster = _FakePaster()
        with self._client(paster) as client:
            response = client.post(
                "/api/terminal/live/paste", json={"text": "do work", "submit": True}
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "unsupported")
        self.assertEqual(paster.calls, [])
