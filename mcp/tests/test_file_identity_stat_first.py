"""A file's identity is read again only when its stat moved (D569; M8-73 step 2).

The identity stays the SHA-256 of the file's bytes. What changes is when the bytes are read: a file
whose device, inode, size, mode and times are as they were when its bytes were last read is not read
again, and a file written within the racy window of that read always is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agents_remember.kernel import recorded_reads
from agents_remember.kernel.recorded_reads import bytes_identity, file_identity


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every byte read ``file_identity`` makes, and a held store emptied for this test."""
    monkeypatch.setattr(recorded_reads, "_HELD", type(recorded_reads._HELD)())
    made: list[Path] = []
    original = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        made.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return made


def test_an_unchanged_file_is_read_once_and_any_moved_stat_reads_it_again(
    tmp_path: Path, reads: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recorded_reads, "RACY_NS", -(10**12))  # every write is long settled
    path = tmp_path / "task.json"
    path.write_text("before")
    assert file_identity(path) == file_identity(path) == bytes_identity(b"before")
    assert len(reads) == 1, "an unchanged file was read again"

    times = os.stat(path)
    path.write_text("after!")  # the same size, and its modification time put back
    os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    assert file_identity(path) == bytes_identity(b"after!"), "the change time did not move it"

    replacement = tmp_path / "replacement"
    replacement.write_text("after!")
    replacement.replace(path)  # the same bytes under another inode
    assert file_identity(path) == bytes_identity(b"after!")
    assert len(reads) == 3

    path.unlink()
    assert file_identity(path) == recorded_reads.ABSENT


def test_a_file_written_within_the_racy_window_is_read_every_time(
    tmp_path: Path, reads: list[Path]
) -> None:
    path = tmp_path / "task.json"
    path.write_text("just written")
    file_identity(path)
    file_identity(path)
    assert len(reads) == 2, "a file written a moment ago was held"


def test_at_most_the_bound_of_identities_is_held(
    tmp_path: Path, reads: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recorded_reads, "RACY_NS", -(10**12))
    monkeypatch.setattr(recorded_reads, "HELD_IDENTITIES", 2)
    paths = [tmp_path / f"{name}.json" for name in "abc"]
    for path in paths:
        path.write_text(path.name)
        file_identity(path)
    assert len(recorded_reads._HELD) == 2
    file_identity(paths[0])  # the least recently used was let go: read again
    assert len(reads) == 4
