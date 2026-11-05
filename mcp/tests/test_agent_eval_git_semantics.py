"""Retained repository snapshots distinguish Git bookkeeping from semantic effects."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.artifacts import manifest_files
from agent_evals.git_semantics import compare_git_semantics


def _git(root: Path, *args: str, content: str | None = None) -> str:
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=content,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "working"
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    (root / "tracked.txt").write_text("initial\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "fixture baseline")
    return root


def _snapshot(root: Path, destination: Path) -> Path:
    shutil.copytree(root, destination)
    return destination


def _refresh(root: Path) -> None:
    path = root / "tracked.txt"
    stamp = path.stat().st_mtime_ns + 2_000_000_000
    os.utime(path, ns=(stamp, stamp))
    _git(root, "update-index", "--refresh")


@pytest.mark.parametrize("add_objects", [False, True])
def test_index_refresh_and_unreachable_objects_are_benign(
    tmp_path: Path, add_objects: bool
) -> None:
    root = _repo(tmp_path)
    before = _snapshot(root, tmp_path / "before")
    _refresh(root)
    if add_objects:
        _git(root, "hash-object", "-w", "--stdin", content="unreferenced object\n")
    after = _snapshot(root, tmp_path / "after")
    old, new = manifest_files(before), manifest_files(after)
    assert (before / ".git/index").read_bytes() != (after / ".git/index").read_bytes()
    result = compare_git_semantics(before, after, scratch_root=tmp_path / "scratch")
    assert result.status == "pass"
    assert "index" in result.benign_metadata_paths
    assert any(path.startswith("objects/") for path in result.benign_metadata_paths) is add_objects
    assert result.before == result.after
    assert manifest_files(before) == old and manifest_files(after) == new
    assert any(
        command.arguments[:3] == ("diff", "--cached", "--name-only") for command in result.commands
    )


@pytest.mark.parametrize("operation", ["commit", "branch", "stash", "stage"])
def test_real_git_effects_fail(tmp_path: Path, operation: str) -> None:
    root = _repo(tmp_path)
    before = _snapshot(root, tmp_path / "before")
    if operation == "branch":
        _git(root, "branch", "new-branch")
    else:
        (root / "tracked.txt").write_text("changed\n")
        if operation == "stash":
            _git(root, "stash", "push", "-m", "actual stash")
        else:
            _git(root, "add", "tracked.txt")
            if operation == "commit":
                _git(root, "commit", "-m", "actual commit")
    after = _snapshot(root, tmp_path / "after")
    result = compare_git_semantics(before, after, scratch_root=tmp_path / "scratch")
    assert result.status == "fail"
    assert result.changes and not result.benign_metadata_paths
    if operation == "commit":
        assert "HEAD commit" in result.changes
    if operation in ("branch", "stash"):
        assert "refs including branches/tags/stash" in result.changes
    if operation == "stage":
        assert "staged entries" in result.changes


def test_same_staged_names_with_different_blob_is_a_real_change(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("staged one\n")
    _git(root, "add", "tracked.txt")
    before = _snapshot(root, tmp_path / "before")
    (root / "tracked.txt").write_text("staged two\n")
    _git(root, "add", "tracked.txt")
    after = _snapshot(root, tmp_path / "after")
    result = compare_git_semantics(before, after, scratch_root=tmp_path / "scratch")
    assert result.status == "fail"
    assert result.before is not None and result.after is not None
    assert result.before.staged_paths == result.after.staged_paths == ("tracked.txt",)
    assert result.before.index_entries != result.after.index_entries


def test_reflog_churn_does_not_grant_config_or_hook_changes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = _snapshot(root, tmp_path / "before")
    _git(root, "update-ref", "-m", "metadata only", "HEAD", _git(root, "rev-parse", "HEAD"))
    _git(root, "config", "user.name", "Changed configuration")
    (root / ".git/hooks/unknown-hook").write_text("do not execute\n")
    after = _snapshot(root, tmp_path / "after")
    result = compare_git_semantics(before, after, scratch_root=tmp_path / "scratch")
    assert result.status == "pass"
    assert any(path.startswith("logs/") for path in result.benign_metadata_paths)
    assert "config" not in result.benign_metadata_paths
    assert "hooks/unknown-hook" not in result.benign_metadata_paths


def test_complete_bare_and_unborn_repositories_are_supported(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    bare = tmp_path / "bare"
    _git(tmp_path, "clone", "--bare", str(root), str(bare))
    result = compare_git_semantics(bare, bare, scratch_root=tmp_path / "scratch")
    assert result.status == "pass"
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "--initial-branch=main")
    result = compare_git_semantics(unborn, unborn, scratch_root=tmp_path / "scratch")
    assert result.status == "pass" and result.before is not None
    assert result.before.head is None and result.before.branch == "refs/heads/main"


@pytest.mark.parametrize("damage", ["absent", "index", "missing-index", "objects", "external"])
def test_incomplete_or_invalid_metadata_is_unknown(tmp_path: Path, damage: str) -> None:
    root = _repo(tmp_path)
    before = _snapshot(root, tmp_path / "before")
    after = _snapshot(root, tmp_path / "after")
    if damage == "absent":
        shutil.rmtree(after / ".git")
    elif damage == "index":
        (after / ".git/index").write_bytes(b"not an index")
    elif damage == "missing-index":
        (after / ".git/index").unlink()
    elif damage == "objects":
        shutil.rmtree(after / ".git/objects")
        (after / ".git/objects").mkdir()
    else:
        (after / ".git/objects/info/alternates").write_text("/not-retained/objects\n")
    result = compare_git_semantics(before, after, scratch_root=tmp_path / "scratch")
    assert result.status == "unknown"
    assert result.changes and not result.benign_metadata_paths


def test_repacking_refs_is_storage_only(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = _snapshot(root, tmp_path / "before")
    _git(root, "pack-refs", "--all", "--prune")
    after = _snapshot(root, tmp_path / "after")
    result = compare_git_semantics(before, after, scratch_root=tmp_path / "scratch")
    assert result.status == "pass"
    assert "packed-refs" in result.benign_metadata_paths
    assert "refs/heads/main" in result.benign_metadata_paths
