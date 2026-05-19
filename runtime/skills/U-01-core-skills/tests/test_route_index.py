from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = CORE_ROOT / "_shared"
sys.path.insert(0, str(SHARED_ROOT))

from agents_remember.route_index import build_route_indexes, sidecar_status  # noqa: E402


class RouteIndexTests(unittest.TestCase):
    def test_builds_route_indexes_from_overviews_and_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repo"
            onboarding_root = root / "memory" / "onboarding"

            (code_root / "src" / "app").mkdir(parents=True)
            (code_root / "src" / "app" / "service.py").write_text("def run(): pass\n", encoding="utf-8")
            (code_root / "src" / "app" / "missing.py").write_text("def gap(): pass\n", encoding="utf-8")
            (code_root / "README.md").write_text("# Repo\n", encoding="utf-8")

            (onboarding_root / "src" / "app").mkdir(parents=True)
            (onboarding_root / "overview.md").write_text("# Repo Overview\n", encoding="utf-8")
            (onboarding_root / "src" / "app" / "overview.md").write_text(
                (
                    "# App Route\n"
                    "Handles service routing.\n\n"
                    "## Hot Path Summary\n"
                    "Routes service requests through `ServiceRunner`, `APP_MODE`, and service.py.\n"
                    "Use `run_service` when confirming source behavior.\n\n"
                    "## What This Area Is\n"
                    "Detailed route prose.\n"
                ),
                encoding="utf-8",
            )
            (onboarding_root / "src" / "app" / "service.py.md").write_text(
                "# service.py\n",
                encoding="utf-8",
            )

            result = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
            )

            self.assertEqual(result.routes, 2)
            self.assertEqual(result.written, 2)

            root_index = json.loads((onboarding_root / "overview.index.json").read_text(encoding="utf-8"))
            self.assertEqual(root_index["route"], "")
            self.assertEqual(root_index["childRoutes"][0]["route"], "src/app")

            route_index = json.loads((onboarding_root / "src" / "app" / "overview.index.json").read_text(encoding="utf-8"))
            self.assertEqual(route_index["sourceScope"], ["src/app/**"])
            self.assertEqual(route_index["coveredFiles"], ["src/app/service.py"])
            self.assertEqual(route_index["coverageCounts"]["fileSidecars"], 1)
            self.assertGreaterEqual(route_index["coverageCounts"]["sourceFilesInScope"], 2)
            self.assertEqual(route_index["fallback"]["governingOverview"], "src/app/overview.md")
            self.assertIn("app", route_index["routingTerms"])
            self.assertNotIn("handles", route_index["routingTerms"])
            self.assertEqual(
                route_index["hotPath"]["summary"],
                "Routes service requests through `ServiceRunner`, `APP_MODE`, and service.py. "
                "Use `run_service` when confirming source behavior.",
            )
            self.assertIn("src/app", route_index["hotPath"]["candidateHints"])
            self.assertIn("src/app/service.py", route_index["hotPath"]["candidateHints"])
            self.assertIn("ServiceRunner", route_index["hotPath"]["anchorHints"])
            self.assertIn("APP_MODE", route_index["hotPath"]["anchorHints"])
            self.assertIn("service.py", route_index["hotPath"]["anchorHints"])
            self.assertIn("run_service", route_index["hotPath"]["anchorHints"])

    def test_sidecar_status_uses_scope_and_covered_files(self) -> None:
        route_index = {
            "sourceScope": ["src/app/**"],
            "coveredFiles": ["src/app/service.py"],
        }

        self.assertEqual(sidecar_status("src/app/service.py", route_index), "present")
        self.assertEqual(sidecar_status("src/app/missing.py", route_index), "absent")
        self.assertEqual(sidecar_status("src/other/file.py", route_index), "out-of-scope")

    def test_overview_only_route_still_indexes_source_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repo"
            onboarding_root = root / "memory" / "onboarding"

            (code_root / "pkg").mkdir(parents=True)
            (code_root / "pkg" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            (onboarding_root / "pkg").mkdir(parents=True)
            (onboarding_root / "overview.md").write_text("# Repo Overview\n", encoding="utf-8")
            (onboarding_root / "pkg" / "overview.md").write_text("# Package\n", encoding="utf-8")

            build_route_indexes(code_root=code_root, onboarding_root=onboarding_root)

            route_index = json.loads((onboarding_root / "pkg" / "overview.index.json").read_text(encoding="utf-8"))
            self.assertEqual(route_index["coveredFiles"], [])
            self.assertEqual(route_index["coverageCounts"]["fileSidecars"], 0)
            self.assertEqual(route_index["hotPath"]["summary"], "")
            self.assertEqual(route_index["hotPath"]["candidateHints"], ["pkg"])
            self.assertEqual(sidecar_status("pkg/module.py", route_index), "absent")


if __name__ == "__main__":
    unittest.main()
