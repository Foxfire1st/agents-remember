"""L81 F09/F13: the worklist publisher changes only different text."""

import json
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.knowledge_worklist.leaf import persist_worklist


def test_same_document_retains_inode_and_times(tmp_path: Path) -> None:
    path = tmp_path / "worklist.json"
    persist_worklist(path, {"state": "complete"})
    before = path.stat()
    persist_worklist(path, {"state": "complete"})
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )


@pytest.mark.parametrize(
    "original", [None, b"other text", b"invalid:\xff", b'{\r\n  "state": "complete"\r\n}\r\n']
)
def test_missing_unreadable_or_other_text_is_atomically_replaced(
    tmp_path: Path, original: bytes | None
) -> None:
    path = tmp_path / "worklist.json"
    if original is not None:
        path.write_bytes(original)
    descriptor = path.open("rb") if original is not None else None
    try:
        inode = path.stat().st_ino if original is not None else None
        persist_worklist(path, {"state": "complete"})
        assert path.read_bytes() == b'{\n  "state": "complete"\n}\n'
        assert path.stat().st_ino != inode
        if descriptor is not None:
            assert descriptor.read() == original
    finally:
        if descriptor is not None:
            descriptor.close()


def test_changed_document_replaces_inode_and_publishes_new_text(tmp_path: Path) -> None:
    path = tmp_path / "worklist.json"
    persist_worklist(path, {"state": "complete"})
    with path.open("rb") as previous:
        inode = path.stat().st_ino
        persist_worklist(path, {"state": "incomplete"})
        assert path.stat().st_ino != inode
        assert json.loads(path.read_text()) == {"state": "incomplete"}
        assert json.loads(previous.read()) == {"state": "complete"}


def test_unreadable_existing_file_still_uses_atomic_publication(tmp_path: Path) -> None:
    path = tmp_path / "worklist.json"
    path.write_text("old")
    with mock.patch.object(Path, "read_text", side_effect=PermissionError("unreadable")):
        persist_worklist(path, {"state": "complete"})
    assert path.read_bytes() == b'{\n  "state": "complete"\n}\n'
