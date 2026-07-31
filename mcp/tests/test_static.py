"""The static surface in both of its legitimate states: built bundle, or honest absence.

The cockpit bundle is a generated Vite build shipped inside the wheel and no longer
committed, so "no bundle" is a normal state for a source checkout rather than a broken
install. These tests pin both halves deterministically -- they never read the repository's
own bundle, so they give the same verdict before and after a frontend build.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.serving.static import BUILD_COMMAND, dashboard_static_dir, mount_static
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _bundle(root: Path) -> Path:
    """A minimal stand-in for what ``dashboard/dist`` puts on disk."""
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("export default 1;\n", encoding="utf-8")
    return root


class DashboardStaticDirTests(unittest.TestCase):
    def test_absent_bundle_resolves_to_none_rather_than_raising(self) -> None:
        # importlib.resources happily hands back a path that does not exist; the resolver is
        # what turns that into an answer the caller can act on.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "package_data" / "dashboard"
            with mock.patch(
                "agents_remember.serving.static.resources.files",
                return_value=Path(tmp),
            ):
                self.assertFalse(missing.exists())
                self.assertIsNone(dashboard_static_dir())

    def test_built_bundle_resolves_to_its_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _bundle(Path(tmp) / "package_data" / "dashboard")
            with mock.patch(
                "agents_remember.serving.static.resources.files",
                return_value=Path(tmp),
            ):
                resolved = dashboard_static_dir()
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "dashboard")


class MountedBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _app(self, static_dir: Path | None) -> FastAPI:
        app = FastAPI()

        @app.get("/api/state")
        async def _state() -> dict[str, str]:  # the routes registered ahead of the mount
            return {"ok": "yes"}

        with mock.patch(
            "agents_remember.serving.static.dashboard_static_dir", return_value=static_dir
        ):
            mount_static(app)
        return app

    def test_built_bundle_is_served_with_revalidated_html(self) -> None:
        with TestClient(self._app(_bundle(self.tmp / "dashboard"))) as client:
            root = client.get("/")
            asset = client.get("/assets/index-abc123.js")
        self.assertEqual(root.status_code, 200)
        self.assertIn('<div id="root">', root.text)
        self.assertEqual(root.headers["cache-control"], "no-cache")  # entry HTML revalidates
        self.assertEqual(asset.status_code, 200)
        # Hashed assets keep StaticFiles' own caching semantics -- only HTML is rewritten.
        self.assertNotEqual(asset.headers.get("cache-control"), "no-cache")

    def test_missing_bundle_answers_503_with_the_build_command(self) -> None:
        with TestClient(self._app(None)) as client:
            root = client.get("/")
        self.assertEqual(root.status_code, 503)  # unavailable, not "not found"
        self.assertTrue(root.headers["content-type"].startswith("text/plain"))
        self.assertEqual(root.headers["cache-control"], "no-store")  # must not outlive the fix
        self.assertIn(BUILD_COMMAND, root.text)
        self.assertIn("npm --prefix dashboard run build", BUILD_COMMAND)

    def test_missing_bundle_names_where_the_bundle_was_expected(self) -> None:
        # The operator must be able to check the location, not just be told to run a build.
        with mock.patch(
            "agents_remember.serving.static._bundle_location",
            return_value="/somewhere/package_data/dashboard",
        ):
            app = self._app(None)
        with TestClient(app) as client:
            root = client.get("/")
        self.assertIn("Expected at: /somewhere/package_data/dashboard", root.text)
        self.assertIn("not committed to the repository", root.text)  # why it is missing
        self.assertIn("/api", root.text)  # and what still works, so the state is diagnosable

    def test_missing_bundle_does_not_fabricate_a_cockpit(self) -> None:
        with TestClient(self._app(None)) as client:
            root = client.get("/")
        # No placeholder UI: a stand-in cockpit would misreport a broken install as working.
        self.assertNotIn('<div id="root">', root.text)
        self.assertNotIn("<html", root.text.lower())

    def test_missing_bundle_leaves_the_api_alone(self) -> None:
        with TestClient(self._app(None)) as client:
            self.assertEqual(client.get("/api/state").status_code, 200)

    def test_missing_bundle_does_not_turn_a_method_error_into_an_outage(self) -> None:
        # Regression: the mount at "/" outranks an API route that matched the path but not
        # the method, so a POST to a GET-only /api route reached the mount. Answering 503
        # there contradicted this very response's "the API is unaffected" and made the API's
        # method semantics depend on whether a frontend build happened to be present.
        # StaticFiles raises 405 for anything but GET/HEAD; the absent-bundle mount must too.
        with TestClient(self._app(None)) as missing, TestClient(
            self._app(_bundle(self.tmp / "dashboard"))
        ) as built:
            for verb in ("post", "put", "delete", "patch"):
                with self.subTest(verb=verb):
                    self.assertEqual(
                        getattr(missing, verb)("/api/state").status_code,
                        getattr(built, verb)("/api/state").status_code,
                    )
            self.assertEqual(missing.post("/api/state").status_code, 405)

    def test_missing_bundle_covers_every_path_the_bundle_would_have(self) -> None:
        # The mount is as greedy as StaticFiles was, so a deep-linked cockpit route explains
        # itself too instead of returning a bare 404 from an unrouted path.
        with TestClient(self._app(None)) as client:
            deep = client.get("/sessions/L1/inspector")
        self.assertEqual(deep.status_code, 503)
        self.assertIn("no built cockpit bundle", deep.text)


if __name__ == "__main__":
    unittest.main()
