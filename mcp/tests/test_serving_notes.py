"""Tests for the read-only coordination-notes API (serving/notes.py, L9 friction F-M).

All coverage drives the real HTTP surface (``/api/notes/*`` through ``create_app``) so
the confinement, the status idiom, and the missing-folder degrade are proven at the API
layer, not against hand-aligned internals: the repo allow-list, the single-segment
``master`` confinement, ``confine_rel`` traversal + symlink-escape rejection, the
missing-notes-folder empty list, subfolder listing with the honest depth cap, and the
binary-tolerant size-capped read.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.serving.app import create_app
from agents_remember.serving.notes import _MAX_FILE_BYTES

_MASTER = "260703_agent-orchestration"


class NotesRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.code_root = self.tmp / "ws" / "R"
        self.code_root.mkdir(parents=True)
        (self.code_root / "README.md").write_text("# repo\n", encoding="utf-8")
        self.coord = self.tmp / "coord"
        self.notes = self.coord / "tasks" / "R" / _MASTER / "notes"

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _client(self) -> TestClient:
        config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.coord,
            workspace_root=self.tmp / "ws",
            transcript_root=self.tmp / "logs",
            repositories={"R": RepositoryScope(repo_id="R", path=self.code_root)},
        )
        return TestClient(create_app(config, interval=100))

    def _seed_notes(self) -> None:
        (self.notes / "reports").mkdir(parents=True)
        (self.notes / "friction-ledger.md").write_text("# ledger\n\nF-M row.\n", encoding="utf-8")
        (self.notes / "design.md").write_text("# design\n", encoding="utf-8")
        (self.notes / "reports" / "260703-L1-worker-report.md").write_text(
            "# L1 report\n", encoding="utf-8"
        )

    # --- listing -------------------------------------------------------------

    def test_missing_notes_folder_is_an_empty_list_not_an_error(self) -> None:
        with self._client() as client:
            response = client.get("/api/notes/list", params={"repo": "R", "master": _MASTER})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["notes"], [])
        self.assertFalse(body["truncated"])

    def test_list_includes_subfolder_reports_with_root_relative_paths(self) -> None:
        self._seed_notes()
        with self._client() as client:
            response = client.get("/api/notes/list", params={"repo": "R", "master": _MASTER})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        paths = [entry["path"] for entry in body["notes"]]
        self.assertEqual(
            paths, ["design.md", "friction-ledger.md", "reports/260703-L1-worker-report.md"]
        )
        by_path = {entry["path"]: entry for entry in body["notes"]}
        self.assertEqual(by_path["friction-ledger.md"]["language"], "markdown")
        self.assertEqual(
            by_path["reports/260703-L1-worker-report.md"]["name"], "260703-L1-worker-report.md"
        )
        self.assertFalse(body["truncated"])

    def test_list_depth_cap_prunes_and_reports_truncated(self) -> None:
        deep = self.notes / "a" / "b" / "c"
        (deep / "d").mkdir(parents=True)
        (deep / "at-cap.md").write_text("kept\n", encoding="utf-8")
        (deep / "d" / "beyond-cap.md").write_text("pruned\n", encoding="utf-8")
        with self._client() as client:
            response = client.get("/api/notes/list", params={"repo": "R", "master": _MASTER})
        body = response.json()
        paths = {entry["path"] for entry in body["notes"]}
        self.assertIn("a/b/c/at-cap.md", paths)
        self.assertNotIn("a/b/c/d/beyond-cap.md", paths)
        self.assertTrue(body["truncated"])

    def test_list_skips_a_symlink_escaping_the_notes_tree(self) -> None:
        self.notes.mkdir(parents=True)
        outside = self.tmp / "outside-secret.md"
        outside.write_text("secret\n", encoding="utf-8")
        os.symlink(outside, self.notes / "link.md")
        (self.notes / "real.md").write_text("real\n", encoding="utf-8")
        with self._client() as client:
            response = client.get("/api/notes/list", params={"repo": "R", "master": _MASTER})
        paths = {entry["path"] for entry in response.json()["notes"]}
        self.assertEqual(paths, {"real.md"})

    # --- reading -------------------------------------------------------------

    def test_read_serves_a_markdown_note(self) -> None:
        self._seed_notes()
        with self._client() as client:
            response = client.get(
                "/api/notes/read",
                params={"repo": "R", "master": _MASTER, "path": "friction-ledger.md"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["content"], "# ledger\n\nF-M row.\n")
        self.assertEqual(body["language"], "markdown")
        self.assertFalse(body["truncated"])

    def test_read_serves_a_nested_report(self) -> None:
        self._seed_notes()
        with self._client() as client:
            response = client.get(
                "/api/notes/read",
                params={
                    "repo": "R",
                    "master": _MASTER,
                    "path": "reports/260703-L1-worker-report.md",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "# L1 report\n")

    def test_read_binary_is_marked_not_decoded(self) -> None:
        self.notes.mkdir(parents=True)
        (self.notes / "blob.bin").write_bytes(b"\x00\x01\xff\xfe")
        with self._client() as client:
            response = client.get(
                "/api/notes/read", params={"repo": "R", "master": _MASTER, "path": "blob.bin"}
            )
        body = response.json()
        self.assertEqual(body["language"], "binary")
        self.assertEqual(body["content"], "")
        self.assertEqual(body["size"], 4)

    def test_read_oversize_truncates(self) -> None:
        self.notes.mkdir(parents=True)
        (self.notes / "big.md").write_text("a" * (_MAX_FILE_BYTES + 10), encoding="utf-8")
        with self._client() as client:
            response = client.get(
                "/api/notes/read", params={"repo": "R", "master": _MASTER, "path": "big.md"}
            )
        body = response.json()
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["content"]), _MAX_FILE_BYTES)
        self.assertEqual(body["size"], _MAX_FILE_BYTES + 10)

    def test_read_oversize_multibyte_boundary_returns_text_not_binary(self) -> None:
        # FINDING 5 (260703-L18): a multi-byte UTF-8 char straddling the 2-MiB cap must NOT make an
        # oversize markdown note misdecode into an empty "binary" (markdown is the dominant note
        # type). "é" is two bytes, placed so its continuation byte falls just past the cap; the cut
        # backward-scans to the codepoint boundary and returns the first ~2 MiB with truncated=True.
        self.notes.mkdir(parents=True)
        (self.notes / "big.md").write_text("a" * (_MAX_FILE_BYTES - 1) + "é", encoding="utf-8")
        with self._client() as client:
            response = client.get(
                "/api/notes/read", params={"repo": "R", "master": _MASTER, "path": "big.md"}
            )
        body = response.json()
        self.assertEqual(body["language"], "markdown")  # NOT "binary"
        self.assertTrue(body["truncated"])
        self.assertNotEqual(body["content"], "")
        self.assertEqual(body["content"], "a" * (_MAX_FILE_BYTES - 1))
        self.assertEqual(body["size"], _MAX_FILE_BYTES + 1)

    def test_read_absent_note_is_404_not_found(self) -> None:
        self._seed_notes()
        with self._client() as client:
            response = client.get(
                "/api/notes/read", params={"repo": "R", "master": _MASTER, "path": "ghost.md"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "not-found")

    # --- confinement + selector validation ------------------------------------

    def test_dotdot_traversal_is_400_bad_path(self) -> None:
        self._seed_notes()
        secret = self.coord / "tasks" / "R" / _MASTER / "task.json"
        secret.write_text("{}\n", encoding="utf-8")
        with self._client() as client:
            response = client.get(
                "/api/notes/read", params={"repo": "R", "master": _MASTER, "path": "../task.json"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_absolute_path_is_400_bad_path(self) -> None:
        self._seed_notes()
        with self._client() as client:
            response = client.get(
                "/api/notes/read", params={"repo": "R", "master": _MASTER, "path": "/etc/passwd"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_null_byte_path_is_400_bad_path(self) -> None:
        self._seed_notes()
        with self._client() as client:
            response = client.get(
                "/api/notes/read",
                params={"repo": "R", "master": _MASTER, "path": "design\x00.md"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_symlink_escape_is_400_bad_path(self) -> None:
        self.notes.mkdir(parents=True)
        outside = self.tmp / "outside-secret.md"
        outside.write_text("secret\n", encoding="utf-8")
        os.symlink(outside, self.notes / "link.md")
        with self._client() as client:
            response = client.get(
                "/api/notes/read", params={"repo": "R", "master": _MASTER, "path": "link.md"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_unknown_repo_is_404(self) -> None:
        with self._client() as client:
            response = client.get("/api/notes/list", params={"repo": "nope", "master": _MASTER})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-repo")

    def test_multi_segment_master_is_400_bad_request(self) -> None:
        for master in ("a/b", "..", ".hidden", ""):
            with self.subTest(master=master), self._client() as client:
                response = client.get("/api/notes/list", params={"repo": "R", "master": master})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["status"], "bad-request")

    def test_routes_are_get_only(self) -> None:
        self._seed_notes()
        with self._client() as client:
            response = client.post("/api/notes/list", params={"repo": "R", "master": _MASTER})
        self.assertEqual(response.status_code, 405)


if __name__ == "__main__":
    unittest.main()
