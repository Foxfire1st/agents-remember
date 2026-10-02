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

from agents_remember.serving.static import BUILD_COMMAND, mount_static
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _bundle(root: Path) -> Path:
    """A minimal stand-in for what ``dashboard/dist`` puts on disk."""
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("export default 1;\n", encoding="utf-8")
    return root


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

    def test_missing_bundle_leaves_the_api_alone(self) -> None:
        with TestClient(self._app(None)) as client:
            self.assertEqual(client.get("/api/state").status_code, 200)

    def test_an_unknown_api_path_is_not_found_for_every_method_with_or_without_a_bundle(
        self,
    ) -> None:
        """A path under ``/api/`` that no route has never reaches the static surface."""

        for label, static_dir in (("bundle", _bundle(self.tmp / "dashboard")), ("no bundle", None)):
            with TestClient(self._app(static_dir)) as client:
                for path in ("/api/removed/route", "/api/state/deeper", "/api", "/api/"):
                    for method in ("GET", "HEAD", "POST", "PUT", "DELETE"):
                        with self.subTest(label, path=path, method=method):
                            answer = client.request(method, path)
                            self.assertEqual(answer.status_code, 404)
                            if method != "HEAD":
                                self.assertEqual(answer.json(), {"detail": "Not Found"})
                with self.subTest(label, case="a registered route still answers"):
                    state = client.get("/api/state")
                    self.assertEqual((state.status_code, state.json()), (200, {"ok": "yes"}))
                with self.subTest(
                    label, case="a known path with another method is refused as before"
                ):
                    self.assertEqual(client.post("/api/state").status_code, 405)
                with self.subTest(
                    label, case="paths outside the API still reach the static surface"
                ):
                    # The rule is for /api/ alone: a path that only begins with those letters,
                    # and any other path no route has, is the static surface's to answer.
                    served = 404 if static_dir else 503
                    self.assertEqual(client.get("/apiary").status_code, served)
                    self.assertEqual(client.get("/assets/missing.js").status_code, served)
                    self.assertEqual(client.post("/assets/missing.js").status_code, 405)

    def test_the_index_and_an_asset_still_load_behind_the_api_rule(self) -> None:
        with TestClient(self._app(_bundle(self.tmp / "dashboard"))) as client:
            index = client.get("/")
            asset = client.get("/assets/index-abc123.js")
            deep = client.get("/index.html")
        self.assertEqual((index.status_code, asset.status_code, deep.status_code), (200, 200, 200))
        self.assertIn('<div id="root">', index.text)
        self.assertEqual(asset.text, "export default 1;\n")
        with TestClient(self._app(None)) as client:
            notice = client.get("/")
        self.assertEqual(notice.status_code, 503)
        self.assertIn(BUILD_COMMAND, notice.text)


if __name__ == "__main__":
    unittest.main()
