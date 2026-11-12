"""Physical targets survive ancestor replacement at the actual native IO boundary."""

from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

import pytest
from agents_remember.canvas import presenter_files as files
from agents_remember.kernel.file_lock import exclusive_file_lock, lock_held


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("operation", ["read", "write", "mkdir", "unlink", "remove", "lock"])
def test_native_io_keeps_the_admitted_physical_parent(tmp_path, monkeypatch, depth, operation):
    root = tmp_path / "owned" / "parent" / "child"
    root.mkdir(parents=True)
    (root / "value.txt").write_bytes(b"owned")
    (root / "branch").mkdir()
    (root / "branch" / "value.txt").write_bytes(b"owned")
    swapped = root.parents[depth - 1]
    foreign = tmp_path / "foreign"
    shutil.copytree(swapped, foreign)
    foreign_leaf = foreign / root.relative_to(swapped)
    (foreign_leaf / "value.txt").write_bytes(b"foreign")
    before = {p.relative_to(foreign): p.read_bytes() for p in foreign.rglob("*") if p.is_file()}
    fired = False

    def swap():
        nonlocal fired
        if not fired:
            swapped.rename(swapped.with_name(swapped.name + "-held"))
            swapped.symlink_to(foreign, target_is_directory=True)
            fired = True

    owner, name, accepts = {
        "read": (os, "open", lambda path: Path(path).name == "value.txt"),
        "write": (io, "open", lambda path: Path(path).name.startswith(".value.txt.")),
        "mkdir": (os, "mkdir", lambda path: str(path) == "new"),
        "unlink": (os, "unlink", lambda path: str(path) == "value.txt"),
        "remove": (shutil, "rmtree", lambda path: str(path) == "branch"),
        "lock": (os, "open", lambda path: Path(path).name == "resource.lock"),
    }[operation]
    native = getattr(owner, name)

    def interleave(path, *args, **kwargs):
        if accepts(path):
            swap()
        return native(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(owner, name, interleave)
        _act(operation, root)
    assert fired, "the interleave must occur at the real native operation"
    after = {p.relative_to(foreign): p.read_bytes() for p in foreign.rglob("*") if p.is_file()}
    assert after == before


def _read(root):
    with files.open_binary(root / "value.txt") as handle:
        assert handle.read() == b"owned"


def _lock(root):
    resource = root / "resource"
    with files.directory_fd(root) as parent:
        target = files.descriptor_path(parent, resource.name)
        with exclusive_file_lock(resource, "fixture", io_path=target):
            assert lock_held(resource)
            with exclusive_file_lock(resource, "fixture", io_path=target):
                assert lock_held(resource)
    assert not lock_held(resource)


def _act(operation, root):
    return {
        "read": lambda: _read(root),
        "write": lambda: files.write_file(root / "value.txt", b"updated"),
        "mkdir": lambda: files.create_directory(root, root / "new", exist_ok=False),
        "unlink": lambda: files.unlink_file(root / "value.txt"),
        "remove": lambda: files.remove_directory(root / "branch"),
        "lock": lambda: _lock(root),
    }[operation]()
