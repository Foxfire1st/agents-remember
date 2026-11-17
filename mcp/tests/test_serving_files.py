"""Tests for the read-only files API (serving/files.py, L1 of operations-integration).

Pure tests build a :class:`FileScope` directly over a temp code+onboarding tree (so
onboarding behaviour is fully controlled); route tests drive ``/api/files/*`` through
the real scope resolver to cover the catalog, the allow-list, the traversal guard, and
the memory-less degrade path.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.serving.app import create_app
from agents_remember.serving.projector import ProjectionCadence

_SIDECAR = (
    "# mod\n\n"
    "| Field | Value |\n| --- | --- |\n"
    "| lastVerifiedCommitHash | `abc1234` |\n"
    "| lastVerifiedCommitDate | `2026-06-28` |\n\n"
    "The module body.\n"
)


def _make_repo(code_root: Path, memory_root: Path, *, with_memory: bool) -> None:
    """A small code tree; one file has a sidecar, one does not.

    Memory is external: the configured memory root for a repository is the coordination
    tree's ``memory-repos/ar-<repo>``, and a repo-sidecar ``ar-memory/`` root is refused.
    """
    (code_root / "pkg").mkdir(parents=True)
    (code_root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (code_root / "pkg" / "other.py").write_text("y = 2\n", encoding="utf-8")
    (code_root / "README.md").write_text("# readme\n", encoding="utf-8")
    # A picture: eight bytes of PNG signature and a NUL, drawn from the bytes route.
    (code_root / "pkg" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\0")
    # A tiny video: only its suffix selects the served type; its bytes are the fixture.
    (code_root / "pkg" / "clip.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    )
    # Valid UTF-8 with a NUL: a binary file, not a text read with control characters.
    (code_root / "pkg" / "nul.txt").write_bytes(b"abc\x00def")
    if with_memory:
        onboarding = memory_root / "onboarding"
        (onboarding / "pkg").mkdir(parents=True)
        (onboarding / "pkg" / "mod.py.md").write_text(_SIDECAR, encoding="utf-8")
        (onboarding / "overview.md").write_text("# repo overview\n", encoding="utf-8")


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _client(self, *, with_memory: bool) -> TestClient:
        code_root = self.tmp / "ws" / "R"
        memory_root = self.tmp / "coord" / "memory-repos" / "ar-R"
        _make_repo(code_root, memory_root, with_memory=with_memory)
        config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.tmp / "coord",
            workspace_root=self.tmp / "ws",
            transcript_root=self.tmp / "logs",
            repositories={"R": RepositoryScope(repo_id="R", path=code_root)},
        )
        return TestClient(create_app(config, cadence=ProjectionCadence(interval=100)))

    def test_read_serves_code_content(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/read", params={"repo": "R", "path": "pkg/mod.py"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "x = 1\n")

    def test_traversal_is_400_bad_path(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get(
                "/api/files/read", params={"repo": "R", "path": "../../etc/passwd"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_a_nul_bearing_text_suffix_is_binary_not_control_text(self) -> None:
        with self._client(with_memory=True) as client:
            body = client.get("/api/files/read", params={"repo": "R", "path": "pkg/nul.txt"}).json()
        self.assertEqual(body["language"], "binary")
        self.assertEqual(body["content"], "")
        self.assertEqual(body["size"], 7)

    def test_read_names_a_picture_without_reading_its_bytes(self) -> None:
        with self._client(with_memory=True) as client:
            body = client.get("/api/files/read", params={"repo": "R", "path": "pkg/pic.png"}).json()
        self.assertEqual(body["mediaType"], "image/png")
        self.assertEqual(body["language"], "binary")
        self.assertEqual(body["content"], "")
        self.assertEqual(body["size"], 9)
        self.assertFalse(body["tooLarge"])

    def test_bytes_serves_the_picture_and_one_range(self) -> None:
        with self._client(with_memory=True) as client:
            whole = client.get("/api/files/bytes", params={"repo": "R", "path": "pkg/pic.png"})
            part = client.get(
                "/api/files/bytes",
                params={"repo": "R", "path": "pkg/pic.png"},
                headers={"range": "bytes=2-4"},
            )
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole.headers["content-type"], "image/png")
        self.assertEqual(whole.headers["accept-ranges"], "bytes")
        self.assertEqual(whole.content, b"\x89PNG\r\n\x1a\n\0")
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, b"NG\r")
        self.assertEqual(part.headers["content-range"], "bytes 2-4/9")

    def test_bytes_serves_a_range_of_a_video(self) -> None:
        with self._client(with_memory=True) as client:
            whole = client.get("/api/files/bytes", params={"repo": "R", "path": "pkg/clip.mp4"})
            part = client.get(
                "/api/files/bytes",
                params={"repo": "R", "path": "pkg/clip.mp4"},
                headers={"range": "bytes=4-11"},
            )
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole.headers["content-type"], "video/mp4")
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, whole.content[4:12])
        self.assertEqual(part.headers["content-range"], f"bytes 4-11/{len(whole.content)}")

    def test_bytes_refuses_a_range_past_the_end(self) -> None:
        with self._client(with_memory=True) as client:
            answers = [
                client.get(
                    "/api/files/bytes",
                    params={"repo": "R", "path": "pkg/pic.png"},
                    headers={"range": header},
                )
                for header in ("bytes=90-", "bytes=-", "bytes=5-4")
            ]
        for response in answers:
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response.json()["status"], "range-not-satisfiable")
            self.assertEqual(response.headers["content-range"], "bytes */9")

    def test_bytes_refuses_a_suffix_outside_the_media_table(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/bytes", params={"repo": "R", "path": "pkg/mod.py"})
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["status"], "unsupported-type")

    def test_bytes_keeps_the_path_confinement(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get(
                "/api/files/bytes", params={"repo": "R", "path": "../../etc/passwd"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_resolve_answers_a_file_a_dir_and_a_missing_path(self) -> None:
        with self._client(with_memory=True) as client:
            file = client.get(
                "/api/files/resolve", params={"repo": "R", "path": "pkg/pic.png"}
            ).json()
            directory = client.get("/api/files/resolve", params={"repo": "R", "path": "pkg"}).json()
            missing = client.get(
                "/api/files/resolve", params={"repo": "R", "path": "pkg/none.png"}
            ).json()
        self.assertEqual(
            file,
            {
                "exists": True,
                "kind": "file",
                "path": "pkg/pic.png",
                "language": "binary",
                "mediaType": "image/png",
            },
        )
        self.assertEqual(directory["kind"], "dir")
        self.assertFalse(missing["exists"])


if __name__ == "__main__":
    unittest.main()
