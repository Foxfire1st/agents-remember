"""Actual change-set HTTP reads: byte equality, no unchanged Git, and native freshness."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.kernel import git_command
from agents_remember.serving import changeset_cache, changeset_generation, changeset_reads
from agents_remember.serving.changeset import (
    ChangesetFileRef,
    leaf_changeset,
    leaf_file_diff,
    register_changeset_routes,
)
from agents_remember.serving.changeset_cache import ChangesetCache, RepositoryRead, cached_read
from agents_remember.serving.changeset_generation import identity
from agents_remember.worktrees.modules.git import changed_worktree_paths, stage_tree
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_master_net_generation import (
    FEATURE_PATH,
    KEEP_PATH,
    LEAF_ONE,
    MASTER,
    REPO,
    _commit,
    _git,
    _init_repo,
    build_master_fixture,
)

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def world(tmp_path, monkeypatch):
    fixture = build_master_fixture(tmp_path)
    app = FastAPI()
    register_changeset_routes(app, fixture.config)
    children = []
    original = git_command._run_git

    def measured(*args, **kwargs):
        children.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(git_command, "_run_git", measured)
    with TestClient(app) as client:
        yield fixture, client, children


def _params(mode="committed"):
    return {"repo": REPO, "master": MASTER, "leaf": LEAF_ONE, "mode": mode}


def _live(fixture):
    path = (
        fixture.config.coordination_root
        / "tasks"
        / REPO
        / MASTER
        / "enclosures"
        / LEAF_ONE
        / "series-contract.md"
    )
    contract = replace(load_contract(path), code_worktree=fixture.code_repo)
    write_contract(path, contract)
    return contract


def test_landed_list_file_and_master_reuse_native_bytes_without_git(world):
    fixture, client, children = world
    expected = leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "committed")
    expected_file = leaf_file_diff(
        fixture.config,
        ChangesetFileRef(
            repo=REPO, master=MASTER, leaf=LEAF_ONE, mode="committed", path=FEATURE_PATH
        ),
    )
    children.clear()
    first = client.get("/api/changeset/task", params=_params())
    assert first.status_code == 200 and first.json() == expected
    assert children
    children.clear()
    again = client.get("/api/changeset/task", params=_params())
    assert again.content == first.content and not children
    unchanged = client.get(
        "/api/changeset/task", params=_params(), headers={"If-None-Match": first.headers["etag"]}
    )
    assert unchanged.status_code == 304 and not unchanged.content and not children
    file = client.get("/api/changeset/file-diff", params={**_params(), "path": FEATURE_PATH})
    assert file.json() == expected_file
    children.clear()
    again = client.get("/api/changeset/file-diff", params={**_params(), "path": FEATURE_PATH})
    assert again.content == file.content and not children
    master = client.get("/api/changeset/master", params={"repo": REPO, "master": MASTER})
    assert master.status_code == 200 and len(master.json()["leaves"]) == 2
    children.clear()
    again = client.get(
        "/api/changeset/master",
        params={"repo": REPO, "master": MASTER},
        headers={"If-None-Match": master.headers["etag"]},
    )
    assert again.status_code == 304 and not again.content and not children


def test_working_bytes_and_unchanged_poll_then_restored_mtime_edit(world):
    fixture, client, children = world
    _live(fixture)
    file = fixture.code_repo / KEEP_PATH
    file.write_text("changed one\n")
    first = client.get("/api/changeset/task", params=_params("working"))
    assert first.json() == leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "working")
    opened = client.get(
        "/api/changeset/file-diff", params={**_params("working"), "path": KEEP_PATH}
    )
    assert opened.json() == leaf_file_diff(
        fixture.config,
        ChangesetFileRef(repo=REPO, master=MASTER, leaf=LEAF_ONE, mode="working", path=KEEP_PATH),
    )
    children.clear()
    assert (
        client.get(
            "/api/changeset/task",
            params=_params("working"),
            headers={"If-None-Match": first.headers["etag"]},
        ).status_code
        == 304
    )
    assert (
        client.get(
            "/api/changeset/file-diff", params={**_params("working"), "path": KEEP_PATH}
        ).content
        == opened.content
    )
    assert not children
    stamp = file.stat()
    file.write_text("changed two\n")
    os.utime(file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    changed = client.get(
        "/api/changeset/file-diff", params={**_params("working"), "path": KEEP_PATH}
    )
    assert changed.json()["after"]["content"] == "changed two\n"
    # Counts need not change when text changes; an actual fresh native read must still happen.
    children.clear()
    index = fixture.code_repo / ".git/index"
    index_before = (index.read_bytes(), identity(index))
    answer = client.get("/api/changeset/task", params=_params("working"))
    assert children and answer.json() == leaf_changeset(
        fixture.config, REPO, MASTER, LEAF_ONE, "working"
    )
    assert (index.read_bytes(), identity(index)) == index_before


@pytest.mark.parametrize(
    "mutation", ["rename", "delete", "replace", "add", "index", "head", "symlink"]
)
def test_working_generation_covers_each_native_input(world, mutation):
    fixture, client, children = world
    _live(fixture)
    root = fixture.code_repo
    first = client.get("/api/changeset/task", params=_params("working"))
    assert first.status_code == 200
    path = root / KEEP_PATH
    if mutation == "rename":
        path.rename(path.with_name("renamed.py"))
    elif mutation == "delete":
        path.unlink()
    elif mutation == "replace":
        stamp = path.stat()
        replacement = root / "replacement"
        replacement.write_bytes(path.read_bytes())
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        replacement.replace(path)
    elif mutation == "add":
        (root / "new").mkdir()
        (root / "new/tab\tand\nnewline.txt").write_text("new\n")
    elif mutation == "index":
        path.write_text("staged\n")
        _git(root, ["add", KEEP_PATH])
        path.write_text("unstaged\n")
    elif mutation == "head":
        path.write_text("committed\n")
        _commit(root, "advance working head")
    else:
        target = root.parent / "symlink-target"
        target.write_text("linked\n")
        path.unlink()
        path.symlink_to(target)
    children.clear()
    index = root / ".git/index"
    index_before = (index.read_bytes(), identity(index))
    answer = client.get("/api/changeset/task", params=_params("working"))
    assert children and answer.json() == leaf_changeset(
        fixture.config, REPO, MASTER, LEAF_ONE, "working"
    )
    assert (index.read_bytes(), identity(index)) == index_before
    children.clear()
    assert client.get("/api/changeset/task", params=_params("working")).content == answer.content
    assert not children


def test_native_ignored_directory_and_index_change(world):
    fixture, client, children = world
    _live(fixture)
    root = fixture.code_repo
    (root / ".gitignore").write_text("build/\n")
    ignored = root / "build/nested"
    ignored.mkdir(parents=True)
    path = ignored / "file.txt"
    path.write_text("ignored\n")
    first = client.get("/api/changeset/task", params=_params("working"))
    path.write_text("still ignored\n")
    children.clear()
    assert client.get("/api/changeset/task", params=_params("working")).content == first.content
    assert not children
    _git(root, ["add", "-f", "build/nested/file.txt"])
    children.clear()
    tracked = client.get("/api/changeset/task", params=_params("working"))
    assert children and any(
        row["path"] == "build/nested/file.txt" for row in tracked.json()["code"]
    )
    path.write_text("tracked changes inside an ignored directory\n")
    children.clear()
    fresh = client.get("/api/changeset/task", params=_params("working"))
    assert children and fresh.json() == leaf_changeset(
        fixture.config, REPO, MASTER, LEAF_ONE, "working"
    )


def test_native_info_exclude_change_invalidates_untracked_list_and_etag(world):
    fixture, client, children = world
    _live(fixture)
    root = fixture.code_repo
    (root / "scratch-notes.tmp").write_text("untracked\n")
    first = client.get("/api/changeset/task", params=_params("working"))
    assert any(row["path"] == "scratch-notes.tmp" for row in first.json()["code"])
    common = _git(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    (Path(common) / "info/exclude").write_text("*.tmp\n")
    children.clear()
    changed = client.get(
        "/api/changeset/task",
        params=_params("working"),
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert children and changed.status_code == 200
    assert changed.json() == leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "working")
    assert not any(row["path"] == "scratch-notes.tmp" for row in changed.json()["code"])
    children.clear()
    assert client.get("/api/changeset/task", params=_params("working")).content == changed.content
    assert not children


def test_lost_recorded_commit_drops_kept_result_and_preserves_refusal(world):
    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    assert first.status_code == 200
    commit = fixture.tip_one
    (fixture.code_repo / ".git/objects" / commit[:2] / commit[2:]).unlink()
    children.clear()
    lost = client.get("/api/changeset/task", params=_params())
    assert children and lost.status_code == 404 and commit in lost.text
    with pytest.raises(FileNotFoundError, match=commit) as absent:
        leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "committed")
    assert lost.json()["path"] == str(absent.value)


def test_changed_object_bytes_with_restored_mtime_revoke_the_native_proof(world):
    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    assert first.status_code == 200
    object = fixture.code_repo / ".git/objects" / fixture.tip_one[:2] / fixture.tip_one[2:]
    before = object.stat()
    data = object.read_bytes()
    object.chmod(0o644)
    object.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    object.chmod(before.st_mode & 0o7777)
    os.utime(object, ns=(before.st_atime_ns, before.st_mtime_ns))
    children.clear()
    changed = client.get("/api/changeset/task", params=_params())
    assert changed.status_code == 404 and children
    with pytest.raises(FileNotFoundError) as native:
        leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "committed")
    assert changed.json()["path"] == str(native.value)


def test_recorded_range_survives_unrelated_new_objects_and_source_advance(world):
    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    (fixture.code_repo / KEEP_PATH).write_text("later source commit\n")
    _commit(fixture.code_repo, "other source work after this recorded range")
    children.clear()
    same = client.get("/api/changeset/task", params=_params())
    assert same.content == first.content and not children


def test_counter_arithmetic_is_reused_with_the_native_list(world, monkeypatch):
    _, client, _ = world
    calls = []
    measure = changeset_reads._measure_counters

    def observed(values):
        calls.append(values)
        return measure(values)

    monkeypatch.setattr(changeset_reads, "_measure_counters", observed)
    first = client.get("/api/changeset/task", params=_params())
    assert calls
    calls.clear()
    assert client.get("/api/changeset/task", params=_params()).content == first.content
    assert not calls


def test_sidecar_presence_stays_live_around_kept_git_list(world):
    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    assert first.json()["code"][0]["hasSidecar"] is False
    sidecar = fixture.memory_repo / "onboarding" / (FEATURE_PATH + ".md")
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("current sidecar\n")
    children.clear()
    paired = client.get("/api/changeset/task", params=_params())
    assert paired.json()["code"][0]["hasSidecar"] is True and not children
    sidecar.unlink()
    unpaired = client.get("/api/changeset/task", params=_params())
    assert unpaired.content == first.content and not children


def test_a_missing_native_config_include_becoming_present_invalidates_ignore_inputs(world):
    fixture, client, children = world
    _live(fixture)
    root = fixture.code_repo
    include = root.parent / "included-config"
    excludes = root.parent / "external-excludes"
    excludes.write_text("external-ignored.txt\n")
    _git(root, ["config", "include.path", str(include)])
    (root / "external-ignored.txt").write_text("eligible until the include exists\n")
    first = client.get("/api/changeset/task", params=_params("working"))
    assert any(row["path"] == "external-ignored.txt" for row in first.json()["code"])
    include.write_text(f"[core]\n\texcludesFile = {excludes}\n")
    children.clear()
    fresh = client.get("/api/changeset/task", params=_params("working"))
    assert children and not any(
        row["path"] == "external-ignored.txt" for row in fresh.json()["code"]
    )


def test_unreadable_key_and_racing_input_are_computed_uncached(tmp_path, monkeypatch):
    path = tmp_path / "input"
    path.write_text("before")
    cache = ChangesetCache()
    calls = []

    def produce():
        calls.append(1)
        return path.read_text()

    with cache.reading():
        assert cached_read(("test",), (path,), produce) == "before"
        path.write_text("after")
    assert cache.retained[0] == 0
    monkeypatch.setattr(cache, "snapshot", lambda _: None)
    for _ in range(2):
        with cache.reading():
            assert cached_read(("test",), (path,), produce) == "after"
    assert len(calls) == 3 and cache.retained[0] == 0


def test_actual_unreadable_worktree_observation_uses_the_uncached_native_read(world, monkeypatch):
    fixture, client, children = world
    _live(fixture)
    first = client.get("/api/changeset/task", params=_params("working"))
    original = changeset_generation.os.scandir

    def unreadable(path):
        if path == fixture.code_repo:
            raise PermissionError("worktree key unavailable")
        return original(path)

    monkeypatch.setattr(changeset_generation.os, "scandir", unreadable)
    for _ in range(2):
        children.clear()
        answer = client.get("/api/changeset/task", params=_params("working"))
        assert answer.content == first.content and children


@pytest.mark.parametrize("history", [5, 9])
def test_native_repository_and_object_observations_also_evict(tmp_path, history):
    cache = ChangesetCache(capacity=3)
    for index in range(history):
        root = _init_repo(tmp_path / str(index), {"file": "content\n"})
        assert cache.snapshot(RepositoryRead(root, working=True)) is not None
    assert cache.retained == (0, 3, 3)


@pytest.mark.parametrize("history", [1100, 2100])
def test_long_history_reclaims_least_recent_results(tmp_path, history):
    path = tmp_path / "input"
    path.write_text("same")
    cache = ChangesetCache()
    for index in range(history):
        with cache.reading():
            cached_read(("entry", index), (path,), lambda value=index: value)
    assert cache.retained[0] == 1024
    called = []
    with cache.reading():
        assert cached_read(("entry", history - 1), (path,), lambda: called.append(1)) == history - 1
        cached_read(("entry", 0), (path,), lambda: called.append(1))
    assert called == [1] and cache.retained[0] == 1024


@pytest.mark.parametrize("history", [1100, 2100])
def test_one_long_read_also_bounds_its_pending_values_and_input_observations(tmp_path, history):
    cache = ChangesetCache()
    with cache.reading():
        for index in range(history):
            path = tmp_path / str(index)
            path.write_text(str(index))
            assert cached_read(("read", index), (path,), path.read_text) == str(index)
        active = changeset_cache._active.get()
        assert active is not None
        assert len(active.inputs) == len(active.pending) == 1024
    assert cache.retained[0] == 1024


def test_new_nonignored_file_in_a_directory_with_only_ignored_contents_is_seen(world):
    fixture, client, children = world
    _live(fixture)
    root = fixture.code_repo
    (root / ".gitignore").write_text("*.log\n")
    (root / "logs").mkdir()
    (root / "logs/run.log").write_text("ignored\n")
    first = client.get("/api/changeset/task", params=_params("working"))
    children.clear()
    assert client.get("/api/changeset/task", params=_params("working")).content == first.content
    assert not children
    (root / "logs/new_module.py").write_text("new = True\n")
    fresh = client.get("/api/changeset/task", params=_params("working"))
    assert fresh.json() == leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "working")
    assert any(row["path"] == "logs/new_module.py" for row in fresh.json()["code"])


@pytest.mark.parametrize("location", ["worktree", "info", "xdg"])
def test_landed_attributes_use_every_native_source(world, monkeypatch, tmp_path, location):
    fixture, client, children = world
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    first = client.get("/api/changeset/task", params=_params())
    target = {
        "worktree": fixture.code_repo / "src/.gitattributes",
        "info": fixture.code_repo / ".git/info/attributes",
        "xdg": xdg / "git/attributes",
    }[location]
    target.write_text("*.py binary\n")
    fresh = client.get("/api/changeset/task", params=_params())
    assert fresh.json() == leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "committed")
    assert fresh.content != first.content
    assert fresh.json()["code"][0]["insertions"] is None
    children.clear()
    assert client.get("/api/changeset/task", params=_params()).content == fresh.content
    assert not children


def test_default_xdg_ignore_is_a_working_input(world, monkeypatch, tmp_path):
    fixture, client, _ = world
    _live(fixture)
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    (fixture.code_repo / "note.tmp").write_text("untracked\n")
    first = client.get("/api/changeset/task", params=_params("working"))
    (xdg / "git/ignore").write_text("*.tmp\n")
    fresh = client.get("/api/changeset/task", params=_params("working"))
    assert fresh.content != first.content
    assert fresh.json() == leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "working")
    assert not any(row["path"] == "note.tmp" for row in fresh.json()["code"])


def test_lost_recorded_commit_can_be_restored_after_repeated_refusals(world):
    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    path = fixture.code_repo / ".git/objects" / fixture.tip_one[:2] / fixture.tip_one[2:]
    data, mode = path.read_bytes(), path.stat().st_mode
    path.unlink()
    for _ in range(2):
        assert client.get("/api/changeset/task", params=_params()).status_code == 404
    path.write_bytes(data)
    path.chmod(mode)
    restored = client.get("/api/changeset/task", params=_params())
    assert restored.status_code == 200 and restored.content == first.content
    children.clear()
    assert client.get("/api/changeset/task", params=_params()).content == first.content
    assert not children


@pytest.mark.parametrize("size", [40, 4000])
def test_late_ignored_tree_is_not_walked_on_unchanged_reads(world, monkeypatch, size):
    fixture, client, children = world
    _live(fixture)
    root = fixture.code_repo
    (root / ".gitignore").write_text("late/\n")
    client.get("/api/changeset/task", params=_params("working"))
    ignored = root / "late"
    ignored.mkdir()
    for index in range(size):
        (ignored / str(index)).write_text("ignored\n")
    fresh = client.get("/api/changeset/task", params=_params("working"))
    scans = []
    original = changeset_generation.os.scandir

    def counted(path):
        scans.append(path)
        return original(path)

    monkeypatch.setattr(changeset_generation.os, "scandir", counted)
    children.clear()
    for _ in range(2):
        assert client.get("/api/changeset/task", params=_params("working")).content == fresh.content
    assert not children
    assert not any(str(path).startswith(str(ignored)) for path in scans)


def test_sibling_commit_and_unrelated_packed_ref_removal_keep_other_results(world, tmp_path):
    fixture, client, children = world
    _live(fixture)
    root = fixture.code_repo
    _git(root, ["branch", "unrelated"])
    _git(root, ["pack-refs", "--all"])
    working = client.get("/api/changeset/task", params=_params("working"))
    landed = client.get("/api/changeset/task", params=_params())
    sibling = tmp_path / "sibling"
    _git(root, ["worktree", "add", "-q", "-b", "sibling", str(sibling)])
    (sibling / "other").write_text("sibling work\n")
    _commit(sibling, "sibling")
    _git(root, ["branch", "-D", "unrelated"])
    children.clear()
    assert client.get("/api/changeset/task", params=_params("working")).content == working.content
    assert client.get("/api/changeset/task", params=_params()).content == landed.content
    assert not children


def test_native_stage_tree_refreshes_identical_bytes_before_changed_path_read(tmp_path):

    root = _init_repo(tmp_path / "index", {"file": "same\n"})
    path = root / "file"
    path.write_bytes(path.read_bytes())
    before = path.stat()
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))
    assert _git(
        root, ["-c", "diff.autoRefreshIndex=false", "diff", "--name-only", "HEAD"]
    ).splitlines() == ["file"]
    changeset_reads.changed_files_with_counts(root, "HEAD")
    stage_tree(root, _git(root, ["rev-parse", "HEAD^{tree}"]))
    assert changed_worktree_paths(root) == []
