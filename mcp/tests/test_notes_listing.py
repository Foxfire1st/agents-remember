"""The read-only coordination-notes routes (``serving/notes.py``).

The listing is fetched whenever a task opens, over a notes tree that holds thousands of
files. These cases pin its exact wire bytes on a tree with every entry kind the walk must
classify, prove the walk's filesystem work is independent of how many notes exist and how
large they are, and keep the live-refresh and refusal behaviour the reader relies on.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.serving.notes import register_notes_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = "R"
MASTER = "260101_series"


def _client(tmp_path: Path) -> TestClient:
    code_root = tmp_path / "ws" / REPO
    code_root.mkdir(parents=True)
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coord",
        workspace_root=tmp_path / "ws",
        transcript_root=tmp_path / "logs",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=code_root)},
    )
    app = FastAPI()
    register_notes_routes(app, config)
    return TestClient(app)


def _notes_root(tmp_path: Path) -> Path:
    return tmp_path / "coord" / "tasks" / REPO / MASTER / "notes"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _list(client: TestClient) -> Any:
    return client.get("/api/notes/list", params={"repo": REPO, "master": MASTER})


def _wire(payload: dict[str, Any]) -> bytes:
    """The exact bytes ``JSONResponse`` renders for ``payload``."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _row(path: str, size: int, language: str) -> dict[str, Any]:
    return {"name": path.rsplit("/", 1)[-1], "path": path, "size": size, "language": language}


def _build_classification_tree(tmp_path: Path) -> None:
    """Hidden, binary, non-regular, symlinked (in-root, escaping, broken, looping, traversing a
    file) and over-deep entries."""
    notes = _notes_root(tmp_path)
    outside = tmp_path / "coord" / "outside"
    _write(outside / "secret.md", b"escaped")
    _write(notes / "a.md", b"alpha")
    _write(notes / "B.txt", b"bb")
    _write(notes / ".hidden.md", b"h")
    _write(notes / "blob.bin", b"\x00\xff\x10")
    os.mkfifo(notes / "pipe")
    (notes / "link-in.md").symlink_to("a.md")
    (notes / "escape.md").symlink_to(outside / "secret.md")
    (notes / "broken.md").symlink_to("missing.md")
    (notes / "zdir-escape").symlink_to(outside, target_is_directory=True)
    # Followed stats that fail with ELOOP / ENOTDIR: skipped like a dangling link, never a 500.
    (notes / "loop-self").symlink_to("loop-self")
    (notes / "loop-a").symlink_to("loop-b")
    (notes / "loop-b").symlink_to("loop-a")
    (notes / "x").symlink_to("x/../x")
    (notes / "bad").symlink_to("a.md/child")
    _write(notes / "reports" / "r.md", b"report")
    _write(notes / "reports" / "deep" / "d.json", b"{}")
    _write(notes / "reports" / "deep" / "d2" / "e.md", b"e")
    _write(notes / "reports" / "deep" / "d2" / "d3" / "f.md", b"ffff")
    (notes / "alias").symlink_to(notes / "reports" / "deep" / "d2", target_is_directory=True)


def test_listing_bytes_classify_every_entry_kind_and_prune_at_the_depth_cap(
    tmp_path: Path,
) -> None:
    """Files before folders, case-folded order, in-root symlinks listed under their own path,
    escaping, broken, looping and file-traversing symlinks and non-regular files skipped, the
    fourth folder level pruned and reported as ``truncated`` — byte for byte on the wire."""
    _build_classification_tree(tmp_path)
    expected = {
        "repo": REPO,
        "master": MASTER,
        "notes": [
            _row(".hidden.md", 1, "markdown"),
            _row("a.md", 5, "markdown"),
            _row("B.txt", 2, "text"),
            _row("blob.bin", 3, "text"),
            _row("link-in.md", 5, "markdown"),
            _row("alias/e.md", 1, "markdown"),
            _row("alias/d3/f.md", 4, "markdown"),
            _row("reports/r.md", 6, "markdown"),
            _row("reports/deep/d.json", 2, "json"),
            _row("reports/deep/d2/e.md", 1, "markdown"),
        ],
        "truncated": True,
    }
    with _client(tmp_path) as client:
        response = _list(client)
    assert response.status_code == 200
    assert response.content == _wire(expected)


def test_a_directory_swapped_for_an_escaping_symlink_before_it_is_opened_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk observes ``d`` as a real directory; just before the walk opens it, ``d`` is
    replaced by a symlink to a directory outside the notes root. Nothing under the outside
    directory is listed: the swapped directory is refused, not followed."""
    notes = _notes_root(tmp_path)
    outside = tmp_path / "coord" / "outside"
    _write(outside / "SECRET-NAME.key", b"x" * 1234)
    _write(outside / "sub" / "deeper-secret.pem", b"y" * 99)
    _write(notes / "keep.md", b"keep")
    _write(notes / "d" / "ok.md", b"ok")
    swapped: list[str] = []

    def swap_once(path: Any) -> None:
        # Any directory-open spelling of notes/d: relative to a parent descriptor, or by path.
        if not swapped and os.fspath(path) in ("d", os.fspath(notes / "d")):
            swapped.append(os.fspath(path))
            (notes / "d" / "ok.md").unlink()
            (notes / "d").rmdir()
            (notes / "d").symlink_to(outside, target_is_directory=True)

    real_open, real_scandir = os.open, os.scandir

    def opening(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        swap_once(path)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def scanning(path: Any = ".") -> Any:
        if not isinstance(path, int):
            swap_once(path)
        return real_scandir(path)

    with _client(tmp_path) as client:
        monkeypatch.setattr(os, "open", opening)
        monkeypatch.setattr(os, "scandir", scanning)
        response = _list(client)
    assert swapped, "the swap was never injected"
    assert response.status_code == 200
    assert response.json()["notes"] == [_row("keep.md", 4, "markdown")]


def _count_realpath(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = os.path.realpath

    def counting(path: Any, *args: Any, **kwargs: Any) -> str:
        calls.append(os.fspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", counting)
    return calls


def _refuse_file_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the notes listing must never open a note's content")

    monkeypatch.setattr(io, "open", refuse)


def _build_scaled_tree(root: Path, files: int, file_size: int) -> None:
    """``files`` sparse notes of ``file_size`` bytes over three folder levels, plus one
    in-root file symlink, so the only per-entry work left is metadata."""
    for index in range(files):
        path = root / f"lvl{index % 3}" / f"sub{index % 2}" / f"note-{index:04d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.truncate(file_size)
    (root / "lvl0" / "link.md").symlink_to(root / "lvl0" / "sub0" / "note-0000.md")


@pytest.mark.parametrize(
    ("files", "file_size"), [(6, 1), (240, 64 * 1024 * 1024)], ids=["6x1B", "240x64MiB"]
)
def test_listing_work_does_not_grow_with_note_count_or_content_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: int, file_size: int
) -> None:
    """Path resolution is paid once for the root and once per symlink, never per note, and
    no note content is ever opened: the same count at 6 one-byte notes and at 240 notes of
    64 MiB each (sparse, so the sizes are real metadata)."""
    _build_scaled_tree(_notes_root(tmp_path), files, file_size)
    with _client(tmp_path) as client:
        realpath_calls = _count_realpath(monkeypatch)
        _refuse_file_opens(monkeypatch)
        response = _list(client)
    assert response.status_code == 200
    listed = response.json()["notes"]
    assert len(listed) == files + 1
    assert {row["size"] for row in listed} == {file_size}
    # root resolve (1) + the one symlink's confinement (resolve + path_is_relative_to's own
    # resolve of the entry and of the root = 3): 4 whatever the tree's size.
    assert len(realpath_calls) == 4, realpath_calls


def test_listing_reflects_a_new_or_grown_note_on_the_next_request(tmp_path: Path) -> None:
    """No listing state outlives a request: a note written after one listing, and a note
    that grows, appear exactly in the next one."""
    notes = _notes_root(tmp_path)
    _write(notes / "reports" / "one.md", b"1")
    with _client(tmp_path) as client:
        first = _list(client).json()["notes"]
        _write(notes / "reports" / "two.md", b"22")
        _write(notes / "reports" / "one.md", b"1111")
        second = _list(client).json()["notes"]
    assert first == [_row("reports/one.md", 1, "markdown")]
    assert second == [_row("reports/one.md", 4, "markdown"), _row("reports/two.md", 2, "markdown")]


_Case = tuple[str, dict[str, str], int, Callable[[Any], bool]]
_SERIES = {"repo": REPO, "master": MASTER}
_REFUSALS: list[_Case] = [
    ("list", {"repo": "nope", "master": MASTER}, 404, lambda b: b["status"] == "unknown-repo"),
    ("list", {"repo": REPO, "master": "a/b"}, 400, lambda b: b["status"] == "bad-request"),
    ("list", {"repo": REPO, "master": "young"}, 200, lambda b: b["notes"] == []),
    ("read", {**_SERIES, "path": "a.md"}, 200, lambda b: b["content"] == "alpha"),
    ("read", {**_SERIES, "path": "../x"}, 400, lambda b: b["status"] == "bad-path"),
    ("read", {**_SERIES, "path": "escape.md"}, 400, lambda b: b["status"] == "bad-path"),
    ("read", {**_SERIES, "path": "gone.md"}, 404, lambda b: b["status"] == "not-found"),
    ("read", {**_SERIES, "path": "blob.bin"}, 200, lambda b: b["language"] == "binary"),
]


def test_scoped_refusals_and_note_reads_keep_their_wire_answers(tmp_path: Path) -> None:
    """Unknown repo, multi-segment master, traversal and symlink escape refuse; a young
    series lists empty; a note reads as text or as binary."""
    _build_classification_tree(tmp_path)
    with _client(tmp_path) as client:
        for route, params, status, check in _REFUSALS:
            response = client.get(f"/api/notes/{route}", params=params)
            assert (response.status_code, check(response.json())) == (status, True), params
