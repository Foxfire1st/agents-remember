from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock
from unittest.mock import patch

from agents_remember.serving.app import _MAX_IMAGE_BYTES, ServingCollaborators, create_app
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_opener import (
    SpawnKnobs,
    TerminalLaunchRequest,
    resolve_terminal_launch,
)
from fastapi.testclient import TestClient
from test_terminal_ws import _catalog_entry, _config, _FakeTerminalHost, _which


class TerminalImageEndpointTests(unittest.TestCase):
    """`POST /api/terminal/{session}/image` (slice 6f): save a pasted image under the session cwd."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.host = _FakeTerminalHost(cwd=self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.app = create_app(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=100),
            collaborators=ServingCollaborators(
                terminal_host=cast(TerminalHost, self.host), terminal_catalog=self.catalog
            ),
        )

    def tearDown(self) -> None:
        self.host.close()
        self._dir.cleanup()

    def test_saves_under_session_cwd_and_returns_path(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-body"
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("shot.png", png, "image/png")}
            )
        self.assertEqual(response.status_code, 200)
        path = Path(response.json()["path"])
        self.assertEqual(path.parent.name, ".dashboard-pastes")
        self.assertTrue(path.is_relative_to(self.tmp.resolve()))
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(path.read_bytes(), png)  # flushed to disk before the path is injected

    def test_hostile_filename_yields_uuid_name_under_cwd(self) -> None:
        # The client filename is used ONLY to derive the extension; the basename is always a uuid, so a
        # path-bearing name cannot escape the session cwd. Pins the no-traversal contract.
        png = b"\x89PNG\r\n\x1a\n" + b"body"
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image",
                files={"file": ("../../etc/passwd.png", png, "image/png")},
            )
        self.assertEqual(response.status_code, 200)
        path = Path(response.json()["path"])
        self.assertTrue(path.is_relative_to(self.tmp.resolve()))
        self.assertEqual(path.parent.name, ".dashboard-pastes")
        stem = path.stem  # the uuid hex, never the client-supplied name
        self.assertEqual(len(stem), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in stem))

    def test_rejects_non_image_type(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("notes.txt", b"hello", "text/plain")}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-type")

    def test_rejects_non_image_content_for_image_extension(self) -> None:
        # Magic-byte sniff: an image extension with non-image bytes is rejected, not saved as a .png.
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image",
                files={"file": ("shot.png", b"not a real png", "image/png")},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-type")

    def test_rejects_empty_body(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("shot.png", b"", "image/png")}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-type")

    def test_rejects_oversize_post_read(self) -> None:
        with (
            patch("agents_remember.serving._app_terminal_routes._MAX_IMAGE_BYTES", 8),
            TestClient(self.app) as client,
        ):
            response = client.post(
                "/api/terminal/live/image",
                files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"xx", "image/png")},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["status"], "too-large")

    def test_rejects_oversize_via_content_length(self) -> None:
        # A blatantly oversize upload is rejected fast on Content-Length, without patching the cap down.
        big = b"\x00" * (_MAX_IMAGE_BYTES + 10_000)
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/image", files={"file": ("big.png", big, "image/png")}
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["status"], "too-large")

    def test_unknown_session_is_404(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/ghost/image", files={"file": ("shot.png", b"\x89PNG", "image/png")}
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-session")

    def test_saves_under_catalog_cwd_after_dashboard_restart(self) -> None:
        restored_cwd = self.tmp / "restored"
        restored_cwd.mkdir()
        self.catalog.upsert(_catalog_entry("restored", cwd=restored_cwd, tmux_name="ar-restored"))
        png = b"\x89PNG\r\n\x1a\n" + b"catalog-body"
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/restored/image", files={"file": ("shot.png", png, "image/png")}
            )
        self.assertEqual(response.status_code, 200)
        path = Path(response.json()["path"])
        self.assertTrue(path.is_relative_to(restored_cwd.resolve()))
        self.assertEqual(path.read_bytes(), png)


class MalformedSettingsScratchTerminalTests(unittest.TestCase):
    """L16 review follow-up (L16R-1): a malformed agentic settings file must fail the
    launches that USE the registry (harness opens), never a plain scratch terminal --
    the /api/terminal route loads the effective registry only when the request
    resolves a harness."""

    def test_plain_terminal_open_skips_the_settings_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            system = tmp / "system"
            system.mkdir(parents=True)
            (system / "settings.json").write_text("{not json", encoding="utf-8")
            config = _config(tmp)
            app = create_app(config)
            seen: dict[str, object] = {}

            def fake_open(**kwargs: object) -> object:
                seen.update(kwargs)
                raise RuntimeError("stop-before-tmux")

            with mock.patch(
                "agents_remember.serving._app_terminal_routes.open_terminal_session", fake_open
            ):
                client = TestClient(app, raise_server_exceptions=False)
                client.post("/api/terminal/scratch-1", json={"kind": "terminal"})
            # The registry load never ran for a scratch terminal: the launch request carries
            # harnesses=None (builtin fallback) despite the malformed settings file on disk.
            launch = seen["launch"]
            assert isinstance(launch, TerminalLaunchRequest)
            self.assertIsNone(launch.harnesses)


class ResolveTerminalLaunchTests(unittest.TestCase):
    @staticmethod
    def _launch(kind: str, **fields: object) -> TerminalLaunchRequest:
        return TerminalLaunchRequest(
            kind=kind,
            workspace_root=Path("/ws"),
            shell=fields.pop("shell", "/bin/sh"),  # type: ignore[arg-type]
            **fields,  # type: ignore[arg-type]
        )

    def test_terminal_kind_is_shell_at_workspace_root(self) -> None:
        cwd, argv = resolve_terminal_launch(self._launch("terminal", shell="/bin/zsh"))
        self.assertEqual((cwd, argv), (Path("/ws"), ("/bin/zsh",)))

    def test_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch(self._launch("nope"))

    def test_harness_kind_resolves_registry_argv_at_workspace_root(self) -> None:
        cwd, argv = resolve_terminal_launch(
            self._launch("harness", harness="claude", which=_which("claude"))
        )
        self.assertEqual((cwd, argv), (Path("/ws"), ("claude",)))

    def test_harness_kind_without_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch(self._launch("harness", which=_which("claude")))

    def test_harness_kind_unknown_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch(
                self._launch("harness", harness="gemini", which=_which("gemini"))
            )

    def test_harness_kind_not_installed_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_terminal_launch(self._launch("harness", harness="claude", which=_which()))

    def test_launch_args_ride_the_resolved_argv(self) -> None:
        _cwd, argv = resolve_terminal_launch(
            self._launch(
                "harness",
                harness="claude",
                which=_which("claude"),
                knobs=SpawnKnobs(launch_args=["--flag"]),
            )
        )
        self.assertEqual(argv, ("claude", "--flag"))
