"""The read-only coordination-notes routes (``serving/notes.py``).

MIK-R126: no route walks the tree. ``GET /api/notes/folder`` answers one folder in a
stable order and in parts; ``POST /api/notes/resolve`` resolves one request's
references; ``GET /api/notes/read`` reads one note. These cases pin the folder's exact
wire bytes on a tree with every entry kind, the paging ceiling, the resolution rule,
and the live-refresh and refusal behaviour the reader relies on.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.serving.notes import _FOLDER_PAGE, register_notes_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = "R"
MASTER = "260101_series"


def _client(tmp_path: Path) -> TestClient:
    code_root = tmp_path / "ws" / REPO
    code_root.mkdir(parents=True, exist_ok=True)
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


def _folder(client: TestClient, params: dict[str, Any]) -> Any:
    return client.get("/api/notes/folder", params=params)


def _wire(payload: dict[str, Any]) -> bytes:
    """The exact bytes ``JSONResponse`` renders for ``payload``."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _file(path: str, size: int, language: str) -> dict[str, Any]:
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "kind": "file",
        "size": size,
        "language": language,
    }


def _dir(path: str) -> dict[str, Any]:
    return {"name": path.rsplit("/", 1)[-1], "path": path, "kind": "dir", "size": None}


def _build_classification_tree(tmp_path: Path) -> None:
    """Hidden, binary, non-regular, symlinked (in-root, escaping, broken, looping,
    traversing a file) entries plus a deep subtree the folder answer never enters."""
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
    (notes / "alias").symlink_to(notes / "reports", target_is_directory=True)


def test_folder_answers_one_level_in_a_stable_order_and_never_descends(tmp_path: Path) -> None:
    """Directories first, then files, case-folded by name; escaping, broken, looping and
    non-regular entries skipped; the deep subtree unentered — byte for byte on the wire."""
    _build_classification_tree(tmp_path)
    expected = {
        "repo": REPO,
        "master": MASTER,
        "path": "",
        "entries": [
            _dir("alias"),
            _dir("reports"),
            _file(".hidden.md", 1, "markdown"),
            _file("a.md", 5, "markdown"),
            _file("B.txt", 2, "text"),
            _file("blob.bin", 3, "text"),
            _file("link-in.md", 5, "markdown"),
        ],
        "total": 7,
        "offset": 0,
        "limit": _FOLDER_PAGE,
        "remaining": 0,
        "exists": True,
        "reason": None,
    }
    with _client(tmp_path) as client:
        response = _folder(client, {"repo": REPO, "master": MASTER})
    assert response.status_code == 200
    assert response.content == _wire(expected)


def test_folder_subfolder_and_missing_paths(tmp_path: Path) -> None:
    """A subfolder answers its own level; a vanished folder and a young series answer
    empty with the server's reason, never an error; a file is not a folder."""
    _build_classification_tree(tmp_path)
    with _client(tmp_path) as client:
        sub = _folder(client, {"repo": REPO, "master": MASTER, "path": "reports"}).json()
        assert sub["entries"] == [_dir("reports/deep"), _file("reports/r.md", 6, "markdown")]
        assert (sub["total"], sub["exists"], sub["reason"]) == (2, True, None)
        gone = _folder(client, {"repo": REPO, "master": MASTER, "path": "reports/gone"}).json()
        assert gone["entries"] == [] and gone["exists"] is False and isinstance(gone["reason"], str)
        young = _folder(client, {"repo": REPO, "master": "young"}).json()
        assert (
            young["entries"] == [] and young["exists"] is False and isinstance(young["reason"], str)
        )
        isfile = _folder(client, {"repo": REPO, "master": MASTER, "path": "a.md"}).json()
        assert (
            isfile["entries"] == []
            and isfile["exists"] is False
            and isinstance(isfile["reason"], str)
        )


def test_folder_parts_above_the_ceiling(tmp_path: Path) -> None:
    """The ceiling is fixed at 200: the first part holds 200 stable rows and says how
    many remain; the next offset holds the rest; past the end is empty."""
    notes = _notes_root(tmp_path)
    for index in range(_FOLDER_PAGE + 50):
        _write(notes / f"note-{index:04d}.md", b"x")
    with _client(tmp_path) as client:
        first = _folder(client, {"repo": REPO, "master": MASTER}).json()
        assert first["limit"] == 200
        assert (len(first["entries"]), first["total"], first["remaining"]) == (200, 250, 50)
        assert [entry["path"] for entry in first["entries"]] == sorted(
            f"note-{index:04d}.md" for index in range(200)
        )
        second = _folder(client, {"repo": REPO, "master": MASTER, "offset": 200}).json()
        assert (len(second["entries"]), second["remaining"]) == (50, 0)
        assert second["entries"][0]["path"] == "note-0200.md"
        past = _folder(client, {"repo": REPO, "master": MASTER, "offset": 250}).json()
        assert past["entries"] == [] and past["remaining"] == 0


def test_folder_work_does_not_grow_with_tree_size(tmp_path: Path) -> None:
    """Two thousand notes nested three levels deep: the root answer holds only the
    root's own entries, so no request grows with the files under the notes root."""
    notes = _notes_root(tmp_path)
    for index in range(2000):
        path = notes / f"lvl{index % 3}" / f"sub{index % 2}" / f"note-{index:04d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"n")
    _write(notes / "top.md", b"t")
    with _client(tmp_path) as client:
        body = _folder(client, {"repo": REPO, "master": MASTER}).json()
    assert body["total"] == 4
    assert [entry["path"] for entry in body["entries"]] == ["lvl0", "lvl1", "lvl2", "top.md"]


def test_folder_reflects_a_new_or_grown_note_on_the_next_request(tmp_path: Path) -> None:
    """No folder state outlives a request: a note written after one listing, and a note
    that grows, appear exactly in the next one."""
    notes = _notes_root(tmp_path)
    _write(notes / "reports" / "one.md", b"1")
    with _client(tmp_path) as client:
        first = _folder(client, {"repo": REPO, "master": MASTER, "path": "reports"}).json()[
            "entries"
        ]
        _write(notes / "reports" / "two.md", b"22")
        _write(notes / "reports" / "one.md", b"1111")
        second = _folder(client, {"repo": REPO, "master": MASTER, "path": "reports"}).json()[
            "entries"
        ]
    assert first == [_file("reports/one.md", 1, "markdown")]
    assert second == [
        _file("reports/one.md", 4, "markdown"),
        _file("reports/two.md", 2, "markdown"),
    ]


def test_resolve_answers_each_reference_in_one_request(tmp_path: Path) -> None:
    """Notes-relative paths (with or without the notes/ prefix) and root-level bare
    names resolve; missing, escaping and deep bare names stay null — never a guess."""
    _build_classification_tree(tmp_path)
    references = [
        "see notes/reports/r.md for the report",
        "reports/r.md",
        "a.md",
        "notes/a.md",
        "nope/missing.md",
        "deep/d.json",
        "../outside/secret.md",
        "no path-like token here",
    ]
    with _client(tmp_path) as client:
        response = client.post(
            "/api/notes/resolve", json={"repo": REPO, "master": MASTER, "references": references}
        )
    assert response.status_code == 200
    assert response.json() == {
        "repo": REPO,
        "master": MASTER,
        "resolutions": [
            {"reference": references[0], "path": "reports/r.md"},
            {"reference": references[1], "path": "reports/r.md"},
            {"reference": references[2], "path": "a.md"},
            {"reference": references[3], "path": "a.md"},
            {"reference": references[4], "path": None},
            {"reference": references[5], "path": None},
            {"reference": references[6], "path": None},
            {"reference": references[7], "path": None},
        ],
    }


def test_the_whole_tree_answer_is_gone(tmp_path: Path) -> None:
    """The endpoint that walked the tree is removed: its old route answers 404."""
    with _client(tmp_path) as client:
        assert (
            client.get("/api/notes/list", params={"repo": REPO, "master": MASTER}).status_code
            == 404
        )


def test_a_directory_swapped_for_an_escaping_link_mid_request_stays_confined(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A folder swapped for an outside link between confinement and opening reads as
    gone with a reason — no outside entry or metadata is ever returned (L126-CODE-01)."""
    real_open = os.open
    slot = _notes_root(tmp_path) / "slot"
    outside = tmp_path / "coord" / "outside"
    _write(slot / "inner.md", b"in")
    _write(outside / "secret.md", b"escaped-bytes")

    def swap_then_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(path, str) and path == "slot" and "dir_fd" in kwargs:
            slot.rename(slot.parent / "slot-real")
            slot.symlink_to(outside, target_is_directory=True)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_then_open)
    with _client(tmp_path) as client:
        body = _folder(client, {"repo": REPO, "master": MASTER, "path": "slot"}).json()
    assert body["entries"] == [] and body["exists"] is False
    assert isinstance(body["reason"], str)
    assert "secret" not in json.dumps(body) and "escaped-bytes" not in json.dumps(body)


def test_an_escaping_bare_link_never_resolves_while_an_in_root_alias_does(
    tmp_path: Path,
) -> None:
    """escape.md points outside the root so it stays null; link-in.md points inside so
    it still resolves (L126-CODE-01, both entry points)."""
    _build_classification_tree(tmp_path)
    references = ["escape.md", "link-in.md", "see link-in.md for the alias"]
    with _client(tmp_path) as client:
        response = client.post(
            "/api/notes/resolve", json={"repo": REPO, "master": MASTER, "references": references}
        )
    assert response.status_code == 200
    assert [item["path"] for item in response.json()["resolutions"]] == [
        None,
        "link-in.md",
        "link-in.md",
    ]


def test_case_fold_ties_order_identically_from_any_enumeration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A.md/a.md straddle the part boundary: reversed readdir orders answer the same
    ordered, nonduplicated parts (L126-CODE-02)."""
    notes_root = _notes_root(tmp_path)
    for index in range(199):
        _write(notes_root / f"a{index:03d}.md", b"x")
    _write(notes_root / "M.md", b"upper")
    _write(notes_root / "m.md", b"lower")
    real_listdir = os.listdir

    def reversed_listdir(*args: Any, **kwargs: Any) -> Any:
        return list(reversed(real_listdir(*args, **kwargs)))

    with _client(tmp_path) as client:
        first_head = _folder(client, {"repo": REPO, "master": MASTER}).json()
        first_tail = _folder(client, {"repo": REPO, "master": MASTER, "offset": 200}).json()
        monkeypatch.setattr(os, "listdir", reversed_listdir)
        second_head = _folder(client, {"repo": REPO, "master": MASTER}).json()
        second_tail = _folder(client, {"repo": REPO, "master": MASTER, "offset": 200}).json()
    assert first_head["total"] == 201
    assert [entry["path"] for entry in first_head["entries"]][-1] == "M.md"
    assert [entry["path"] for entry in first_tail["entries"]] == ["m.md"]
    assert second_head == first_head and second_tail == first_tail
    union = [entry["path"] for entry in first_head["entries"] + first_tail["entries"]]
    assert len(set(union)) == 201


_CASE = tuple[str, str, dict[str, Any], int, Any]
_SERIES = {"repo": REPO, "master": MASTER}
_REFUSALS: list[_CASE] = [
    (
        "get",
        "folder",
        {"repo": "nope", "master": MASTER},
        404,
        lambda b: b["status"] == "unknown-repo",
    ),
    ("get", "folder", {"repo": REPO, "master": "a/b"}, 400, lambda b: b["status"] == "bad-request"),
    ("get", "folder", {**_SERIES, "path": "../x"}, 400, lambda b: b["status"] == "bad-path"),
    ("get", "folder", {**_SERIES, "offset": -1}, 400, lambda b: b["status"] == "bad-path"),
    ("get", "read", {**_SERIES, "path": "a.md"}, 200, lambda b: b["content"] == "alpha"),
    ("get", "read", {**_SERIES, "path": "../x"}, 400, lambda b: b["status"] == "bad-path"),
    ("get", "read", {**_SERIES, "path": "escape.md"}, 400, lambda b: b["status"] == "bad-path"),
    ("get", "read", {**_SERIES, "path": "gone.md"}, 404, lambda b: b["status"] == "not-found"),
    ("get", "read", {**_SERIES, "path": "blob.bin"}, 200, lambda b: b["language"] == "binary"),
    (
        "post",
        "resolve",
        {"repo": "nope", "master": MASTER, "references": []},
        404,
        lambda b: b["status"] == "unknown-repo",
    ),
    (
        "post",
        "resolve",
        {"repo": REPO, "master": MASTER, "references": "nope"},
        400,
        lambda b: b["status"] == "bad-request",
    ),
]


def test_scoped_refusals_and_note_reads_keep_their_wire_answers(tmp_path: Path) -> None:
    """Unknown repo, multi-segment master, traversal and symlink escape refuse; a note
    reads as text or as binary; the resolve body is validated."""
    _build_classification_tree(tmp_path)
    with _client(tmp_path) as client:
        for method, route, payload, status, check in _REFUSALS:
            if method == "get":
                response = client.get(f"/api/notes/{route}", params=payload)
            else:
                response = client.post(f"/api/notes/{route}", json=payload)
            assert (response.status_code, check(response.json())) == (status, True), payload


def test_parent_ruling_reference_format_nested_bare_is_text_pathed_is_link(tmp_path: Path) -> None:
    """Parent ruling L126-REFERENCE-FORMAT-RULING §4: a bare name existing only in a
    sub-folder stays plain text (r.md lives only under reports/), the same name given
    with its path is a link, and the one request carries only the document's references
    (ordered 1:1, no tree fetch)."""
    _build_classification_tree(tmp_path)
    references = ["see r.md for the nested-only name", "reports/r.md"]
    with _client(tmp_path) as client:
        response = client.post(
            "/api/notes/resolve", json={"repo": REPO, "master": MASTER, "references": references}
        )
    assert response.status_code == 200
    assert response.json() == {
        "repo": REPO,
        "master": MASTER,
        "resolutions": [
            {"reference": references[0], "path": None},
            {"reference": references[1], "path": "reports/r.md"},
        ],
    }


def test_searchable_unreadable_folder_keeps_baseline_500(tmp_path: Path) -> None:
    """L126-REPRODUCE-FIRST + baseline-stays ruling, search-without-read case: INV-413DC8XE
    pins readable-but-unsearchable only, so a folder that can be searched but not read
    keeps the baseline answer (the server raises EACCES → HTTP 500). Mode 0o111 is
    searchable but not readable. No product change carries this pin."""
    target = _notes_root(tmp_path) / "locked"
    target.mkdir(parents=True)
    (target / "f.md").write_bytes(b"filler\n")
    os.chmod(target, 0o111)
    try:
        code_root = tmp_path / "ws" / REPO
        code_root.mkdir(parents=True, exist_ok=True)
        config = McpRuntimeConfig(
            config_path=tmp_path / "settings.json",
            coordination_root=tmp_path / "coord",
            workspace_root=tmp_path / "ws",
            transcript_root=tmp_path / "logs",
            repositories={REPO: RepositoryScope(repo_id=REPO, path=code_root)},
        )
        app = FastAPI()
        register_notes_routes(app, config)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = _folder(client, {"repo": REPO, "master": MASTER, "path": "locked"})
    finally:
        os.chmod(target, 0o755)
    assert response.status_code == 500


def test_readable_unsearchable_folder_answers_200_without_children(tmp_path: Path) -> None:
    """L126-REPRODUCE-FIRST, read-without-search case: a folder that can be read but
    not searched answers HTTP 200 with no children (per-entry stat failures are
    skipped, never listed). Mode 0o444 is readable but not searchable."""
    target = _notes_root(tmp_path) / "readable"
    target.mkdir(parents=True)
    (target / "f.md").write_bytes(b"filler\n")
    os.chmod(target, 0o444)
    try:
        with _client(tmp_path) as client:
            response = _folder(client, {"repo": REPO, "master": MASTER, "path": "readable"})
    finally:
        os.chmod(target, 0o755)
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True and body["entries"] == [] and body["total"] == 0


def test_neither_read_nor_search_folder_keeps_500(tmp_path: Path) -> None:
    """L126-REPRODUCE-FIRST, excluded neither case: a folder with neither permission
    keeps its 500 (the server raises EACCES); INV-413DC8XE does not cover it."""
    target = _notes_root(tmp_path) / "dark"
    target.mkdir(parents=True)
    (target / "f.md").write_bytes(b"filler\n")
    os.chmod(target, 0o000)
    try:
        code_root = tmp_path / "ws" / REPO
        code_root.mkdir(parents=True, exist_ok=True)
        config = McpRuntimeConfig(
            config_path=tmp_path / "settings.json",
            coordination_root=tmp_path / "coord",
            workspace_root=tmp_path / "ws",
            transcript_root=tmp_path / "logs",
            repositories={REPO: RepositoryScope(repo_id=REPO, path=code_root)},
        )
        app = FastAPI()
        register_notes_routes(app, config)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = _folder(client, {"repo": REPO, "master": MASTER, "path": "dark"})
    finally:
        os.chmod(target, 0o755)
    assert response.status_code == 500
