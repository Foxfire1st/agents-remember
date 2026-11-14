"""The bytes routes' one media table, range policy and binary-aware diff sides.

Pure tests: no HTTP, no repository catalog. A file's bytes are read through a
:class:`MediaSource`; a diff side is classified before it is decoded, so a picture or
video is named as media and another binary file is named as binary, never mojibake.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import AgentsRememberError
from agents_remember.serving.changeset_media import SideLocation, media_for_location
from agents_remember.serving.diff_side import committed_side, working_side
from agents_remember.serving.knowledge_reader import register_knowledge_bytes_route
from agents_remember.serving.media import (
    MEDIA_BYTE_LIMIT,
    MediaTooLarge,
    MediaValidationError,
    RangeNotSatisfiable,
    UnsupportedMedia,
    media_error_response,
    media_response,
    media_type_for,
    parse_range,
    working_media,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TableTests(unittest.TestCase):
    def test_only_the_fixed_table_is_media(self) -> None:
        for name, expected in (
            ("a.png", "image/png"),
            ("a.JPEG", "image/jpeg"),
            ("a.jpg", "image/jpeg"),
            ("a.gif", "image/gif"),
            ("a.webp", "image/webp"),
            ("a.svg", "image/svg+xml"),
            ("a.mp4", "video/mp4"),
            ("a.webm", "video/webm"),
        ):
            self.assertEqual(media_type_for(name), expected, name)
        for name in ("a.py", "a.txt", "a.pdf", "a.mov", "a", "a.png.txt"):
            self.assertIsNone(media_type_for(name), name)


class RangeTests(unittest.TestCase):
    def test_one_range_or_the_whole_file(self) -> None:
        self.assertIsNone(parse_range(None, 10))
        self.assertIsNone(parse_range("  ", 10))
        self.assertEqual(parse_range("bytes=2-4", 10), (2, 4))
        self.assertEqual(parse_range("bytes=2-", 10), (2, 9))
        self.assertEqual(parse_range("bytes=-3", 10), (7, 9))
        # An end past the last byte is clipped, as HTTP allows.
        self.assertEqual(parse_range("bytes=7-99", 10), (7, 9))

    def test_a_malformed_or_out_of_bounds_range_is_refused(self) -> None:
        for header in (
            "2-4",
            "items=2-4",
            "bytes=2-4,6-8",
            "bytes=abc-4",
            "bytes=2-x",
            "bytes=10-",
            "bytes=5-4",
            "bytes=-0",
            "bytes=-",
        ):
            with self.assertRaises(RangeNotSatisfiable, msg=header):
                parse_range(header, 10)
        with self.assertRaises(RangeNotSatisfiable):
            parse_range("bytes=0-", 0)


class RefusalMappingTests(unittest.TestCase):
    def test_each_typed_refusal_maps_to_its_status(self) -> None:
        too_large = media_error_response(MediaTooLarge(20))
        assert too_large is not None
        self.assertEqual(too_large.status_code, 413)
        self.assertEqual(too_large.body, b'{"status":"too-large","size":20,"limit":16777216}')
        unsupported = media_error_response(UnsupportedMedia(".pdf"))
        assert unsupported is not None
        self.assertEqual(unsupported.status_code, 415)
        self.assertEqual(unsupported.body, b'{"status":"unsupported-type","detail":".pdf"}')
        unsatisfiable = media_error_response(RangeNotSatisfiable("bytes=9-", size=9))
        assert unsatisfiable is not None
        self.assertEqual(unsatisfiable.status_code, 416)
        self.assertEqual(unsatisfiable.headers["content-range"], "bytes */9")
        self.assertIsNone(media_error_response(ValueError("other")))


class WorkingMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_reads_exactly_the_requested_bytes_and_no_cache(self) -> None:
        path = self.root / "pic.png"
        path.write_bytes(b"0123456789")
        source = working_media(path)
        self.assertEqual(source.media_type, "image/png")
        self.assertEqual(source.size, 10)
        self.assertEqual(source.read_range(3, 5), b"345")
        # A later write is what the next read answers: nothing was cached.
        path.write_bytes(b"abcdefghij")
        self.assertEqual(source.read_range(3, 5), b"def")

    def test_refuses_an_unserved_suffix_and_an_oversize_file(self) -> None:
        text = self.root / "notes.txt"
        text.write_text("text\n", encoding="utf-8")
        with self.assertRaises(UnsupportedMedia):
            working_media(text)
        big = self.root / "big.png"
        big.write_bytes(b"\0" * (MEDIA_BYTE_LIMIT + 1))
        with self.assertRaises(MediaTooLarge) as caught:
            working_media(big)
        self.assertEqual(caught.exception.size, MEDIA_BYTE_LIMIT + 1)

    def test_an_empty_media_file_answers_an_empty_200(self) -> None:
        path = self.root / "empty.gif"
        path.write_bytes(b"")
        response = media_response(working_media(path), None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["content-length"], "0")

    def test_a_range_is_a_206_with_its_content_range(self) -> None:
        path = self.root / "pic.png"
        path.write_bytes(b"0123456789")
        response = media_response(working_media(path), "bytes=2-4")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.body, b"234")
        self.assertEqual(response.headers["content-range"], "bytes 2-4/10")
        self.assertEqual(response.headers["cache-control"], "no-store")


class KnowledgeBytesRouteTests(unittest.TestCase):
    """The knowledge bytes route serves a fixed media source and refuses the rest by name."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.png = self.root / "pic.png"
        self.png.write_bytes(b"0123456789")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _client(self, port) -> TestClient:
        app = FastAPI()
        register_knowledge_bytes_route(app, port)
        return TestClient(app)

    def test_serves_the_source_and_one_range(self) -> None:
        port = lambda query: working_media(self.png)  # noqa: E731
        with self._client(port) as client:
            whole = client.get(
                "/api/knowledge/bytes", params={"repo": "R", "commit": "a" * 40, "path": "pic.png"}
            )
            part = client.get(
                "/api/knowledge/bytes",
                params={"repo": "R", "commit": "a" * 40, "path": "pic.png"},
                headers={"range": "bytes=-3"},
            )
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole.content, b"0123456789")
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, b"789")

    def test_maps_a_typed_refusal_and_an_unwired_host(self) -> None:
        def refuse(query):
            raise UnsupportedMedia("pic.pdf")

        with self._client(refuse) as client:
            refused = client.get("/api/knowledge/bytes", params={"repo": "R", "path": "pic.pdf"})
        self.assertEqual(refused.status_code, 415)
        self.assertEqual(refused.json()["status"], "unsupported-type")
        with self._client(None) as client:
            unwired = client.get("/api/knowledge/bytes", params={"repo": "R", "path": "pic.png"})
        self.assertEqual(unwired.status_code, 503)
        self.assertEqual(unwired.json()["status"], "unavailable")

    def test_a_malformed_range_is_a_typed_416_not_a_crash(self) -> None:
        port = lambda query: working_media(self.png)  # noqa: E731
        with self._client(port) as client:
            answer = client.get(
                "/api/knowledge/bytes",
                params={"repo": "R", "path": "pic.png"},
                headers={"range": "bytes=-"},
            )
        self.assertEqual(answer.status_code, 416)
        self.assertEqual(answer.json()["status"], "range-not-satisfiable")
        self.assertEqual(answer.headers["content-range"], "bytes */10")

    def test_a_reader_answer_dict_is_a_404_fact(self) -> None:
        port = lambda query: {"state": "absent", "path": "pic.png", "detail": "no blob"}  # noqa: E731
        with self._client(port) as client:
            answer = client.get("/api/knowledge/bytes", params={"repo": "R", "path": "pic.png"})
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer.json()["state"], "absent")


class DiffSideTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.root, check=True)
        self.commit = None

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _commit(self) -> str:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    def test_a_picture_side_names_media_and_a_nul_binary_side_names_binary(self) -> None:
        (self.root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\0")
        (self.root / "raw.bin").write_bytes(b"abc\0def")
        (self.root / "text.txt").write_text("hello\n", encoding="utf-8")
        commit = self._commit()
        picture = committed_side(self.root, commit, "pic.png")
        assert picture is not None
        self.assertEqual(
            picture.to_wire(),
            {"binary": True, "mediaType": "image/png", "size": 9},
        )
        raw = committed_side(self.root, commit, "raw.bin")
        assert raw is not None
        self.assertEqual(
            raw.to_wire(),
            {"binary": True, "mediaType": None, "size": 7},
        )
        text = committed_side(self.root, commit, "text.txt")
        assert text is not None
        self.assertEqual(text.to_wire(), {"content": "hello\n"})
        self.assertIsNone(committed_side(self.root, commit, "missing.png"))

    def test_a_working_picture_side_names_media(self) -> None:
        path = self.root / "pic.webp"
        path.write_bytes(b"RIFF\0\0\0\0WEBP")
        working = working_side(path, "pic.webp")
        assert working is not None
        self.assertEqual(
            working.to_wire(),
            {"binary": True, "mediaType": "image/webp", "size": 12},
        )
        self.assertIsNone(working_side(self.root / "gone.png", "gone.png"))

    def test_a_media_side_is_metadata_without_loading_the_blob(self) -> None:
        (self.root / "clip.mp4").write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 1024)
        commit = self._commit()
        with patch(
            "agents_remember.serving.diff_side.run_git_bytes",
            side_effect=AssertionError("media metadata must not read the blob"),
        ):
            side = committed_side(self.root, commit, "clip.mp4")
        assert side is not None
        self.assertEqual(
            side.to_wire(),
            {"binary": True, "mediaType": "video/mp4", "size": 1036},
        )
        with patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("working media metadata must not read the file"),
        ):
            working = working_side(self.root / "clip.mp4", "clip.mp4")
        assert working is not None
        self.assertEqual(working.media_type, "video/mp4")
        large = self.root / "large.mp4"
        with large.open("wb") as handle:
            handle.seek(MEDIA_BYTE_LIMIT)
            handle.write(b"x")
        with patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("an oversized media side must not be loaded either"),
        ):
            oversized = working_side(large, "large.mp4")
        assert oversized is not None
        self.assertEqual(oversized.size, MEDIA_BYTE_LIMIT + 1)

    def test_the_new_media_errors_are_in_the_typed_family(self) -> None:
        for error in (
            MediaValidationError("bad"),
            UnsupportedMedia("pic.pdf"),
            MediaTooLarge(1),
            RangeNotSatisfiable("bytes=-"),
        ):
            self.assertIsInstance(error, AgentsRememberError)
            self.assertIsInstance(error, ValueError)

    def test_a_historical_side_is_confined_lexically_not_through_today_s_symlink(self) -> None:
        (self.root / "dir").mkdir()
        (self.root / "dir" / "pic.png").write_bytes(b"png")
        commit = self._commit()
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir()
        (outside / "pic.png").write_bytes(b"outside")
        (self.root / "dir").rename(self.root / "real-dir")
        (self.root / "dir").symlink_to(outside, target_is_directory=True)
        with patch(
            "agents_remember.serving.changeset_media.confine_rel",
            side_effect=AssertionError("historical path must not follow a working-tree symlink"),
        ):
            source = media_for_location(SideLocation(self.root, commit, "dir/pic.png"))
        self.assertEqual(source.media_type, "image/png")
        self.assertEqual(source.size, 3)

    def test_a_ref_shaped_revision_is_refused_before_git(self) -> None:
        (self.root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\0")
        self._commit()
        for revision in ("HEAD", "main", "--output=/tmp/x", "abc1234"):
            with self.assertRaises(ValueError):
                committed_side(self.root, revision, "pic.png")


if __name__ == "__main__":
    unittest.main()
