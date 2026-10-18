"""Actual change-set HTTP reads: byte equality, no unchanged Git, and native freshness."""

from __future__ import annotations

import os
import threading
import time
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


def _reading_children(children):
    """The children that compute a change set, apart from Git's own freshness proofs.

    A working view is uncached (L174-R1-06), and a kept commit range is proved by Git's
    ``cat-file -e <id>^{tree}`` plus the working observation's status child
    (MIK-R176 rule 5); none of those computes a change set.
    """
    return [
        child
        for child in children
        if child[:1] != ["cat-file"] and "--untracked-files=all" not in child
    ]


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
    assert again.content == first.content and not _reading_children(children)
    unchanged = client.get(
        "/api/changeset/task", params=_params(), headers={"If-None-Match": first.headers["etag"]}
    )
    assert (
        unchanged.status_code == 304 and not unchanged.content and not _reading_children(children)
    )
    file = client.get("/api/changeset/file-diff", params={**_params(), "path": FEATURE_PATH})
    assert file.json() == expected_file
    children.clear()
    again = client.get("/api/changeset/file-diff", params={**_params(), "path": FEATURE_PATH})
    assert again.content == file.content and not _reading_children(children)
    master = client.get("/api/changeset/master", params={"repo": REPO, "master": MASTER})
    assert master.status_code == 200 and len(master.json()["leaves"]) == 2
    children.clear()
    again = client.get(
        "/api/changeset/master",
        params={"repo": REPO, "master": MASTER},
        headers={"If-None-Match": master.headers["etag"]},
    )
    assert again.status_code == 304 and not again.content and not _reading_children(children)


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


def test_an_id_keyed_hit_outlives_commit_loss_and_a_refusal_is_not_kept(world):
    """Rule 5's accepted gap: a pair hit observes nothing, so object loss is not seen.

    A hit starts no Git child and returns the same bytes after the tip commit object is
    removed. A cold app on that repository refuses, and the refusal is not kept (a first
    read that fails is never served again).
    """

    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    assert first.status_code == 200
    commit = fixture.tip_one
    (fixture.code_repo / ".git/objects" / commit[:2] / commit[2:]).unlink()

    children.clear()
    kept = client.get("/api/changeset/task", params=_params())
    assert kept.status_code == 200 and kept.content == first.content
    assert children == []

    other = FastAPI()
    register_changeset_routes(other, fixture.config)
    with TestClient(other) as cold:
        for _ in range(2):
            refused = cold.get("/api/changeset/task", params=_params())
            assert refused.status_code == 404 and commit in refused.text
        with pytest.raises(FileNotFoundError, match=commit) as absent:
            leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "committed")
        assert refused.json()["path"] == str(absent.value)


def test_an_id_keyed_hit_does_not_observe_changed_object_bytes(world):
    """The accepted gap names object bytes too: a hit observes no object file.

    The kept answer stays the true diff of the two ids; the damaged repository is what a
    cold read reports, and that refusal is the native one.
    """

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
    kept = client.get("/api/changeset/task", params=_params())
    assert kept.status_code == 200 and kept.content == first.content
    assert children == []

    other = FastAPI()
    register_changeset_routes(other, fixture.config)
    with TestClient(other) as cold:
        damaged = cold.get("/api/changeset/task", params=_params())
        assert damaged.status_code == 404
        with pytest.raises(FileNotFoundError) as native:
            leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "committed")
        assert damaged.json()["path"] == str(native.value)


def test_recorded_range_survives_unrelated_new_objects_and_source_advance(world):
    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    (fixture.code_repo / KEEP_PATH).write_text("later source commit\n")
    _commit(fixture.code_repo, "other source work after this recorded range")
    children.clear()
    same = client.get("/api/changeset/task", params=_params())
    assert same.content == first.content and not _reading_children(children)


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
    assert paired.json()["code"][0]["hasSidecar"] is True and not _reading_children(children)
    sidecar.unlink()
    unpaired = client.get("/api/changeset/task", params=_params())
    assert unpaired.content == first.content and not _reading_children(children)


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


def test_working_view_freshness_comes_from_git_status_with_no_worktree_walk(world, monkeypatch):
    """MIK-R176 v2 rule 6 (fifth proof): Git answers the working view, and nothing walks.

    Rule 5 decides that a working change set is read natively — its freshness check
    would cost as much as the read (L174-R1-06) — so the proof here is that the whole
    path uses Git's own commands and no walk of the worktree, and that the working
    observation which can still be retained (the review summary's) is keyed by Git's
    own status signal. The base's manifest walk fails both halves.
    """
    fixture, client, children = world
    _live(fixture)
    scans: list[Path] = []
    walks: list[Path] = []
    original_scandir = changeset_generation.os.scandir
    original_walk = changeset_generation.os.walk

    def counted_scandir(path):
        scans.append(Path(path))
        return original_scandir(path)

    def counted_walk(path, *args, **kwargs):
        walks.append(Path(path))
        return original_walk(path, *args, **kwargs)

    monkeypatch.setattr(changeset_generation.os, "scandir", counted_scandir)
    monkeypatch.setattr(changeset_generation.os, "walk", counted_walk)
    identities: list[Path] = []
    original_identity = changeset_generation.tree_identity

    def counted_identity(path, *, skip=frozenset()):
        identities.append(Path(path))
        return original_identity(path, skip=skip)

    monkeypatch.setattr(changeset_generation, "tree_identity", counted_identity)
    monkeypatch.setattr(changeset_cache, "tree_identity", counted_identity)
    first = client.get("/api/changeset/task", params=_params("working"))
    assert first.status_code == 200
    assert walks == []
    # Contract discovery lists the enclosures directory; no part of the worktree is walked.
    assert not any(path.is_relative_to(fixture.code_repo) for path in scans)
    worktree_identities = [
        path
        for path in identities
        if path.is_relative_to(fixture.code_repo)
        and not path.is_relative_to(fixture.code_repo / ".git")
    ]
    assert worktree_identities == []
    assert not hasattr(changeset_generation, "working_manifest")
    # The working diff is Git's own command, computed for this request.
    assert any("--name-status" in child and "diff" in child for child in children)

    # The working observation that can still be retained is keyed by Git's status.
    children.clear()
    snapshot = ChangesetCache().snapshot(RepositoryRead(fixture.code_repo, working=True))
    assert snapshot is not None
    assert any("--untracked-files=all" in child for child in children)


@pytest.mark.parametrize("history", [5, 9])
def test_native_repository_observations_also_evict(tmp_path, history):
    cache = ChangesetCache(capacity=3)
    for index in range(history):
        root = _init_repo(tmp_path / str(index), {"file": "content\n"})
        assert cache.snapshot(RepositoryRead(root, working=True)) is not None
    assert cache.retained == (0, 3)


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
    (root / "logs/new_module.py").write_text("new = True\n")
    fresh = client.get("/api/changeset/task", params=_params("working"))
    assert fresh.json() == leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "working")
    assert any(row["path"] == "logs/new_module.py" for row in fresh.json()["code"])


@pytest.mark.parametrize("location", ["worktree", "info", "xdg"])
def test_landed_attributes_are_not_observed_on_a_pair_hit(world, monkeypatch, tmp_path, location):
    """Rule 5's named gap: a repository-level attribute file is not part of the pair's key.

    A hit serves the pair's own diff with no Git child; a cold app on the same repository
    sees the attribute change, which is what the gap names rather than hides.
    Amendment b84bf836 requires the full hit to take no snapshot of any kind and
    start no Git process, including when a worktree attribute file changes.
    """

    fixture, client, children = world
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    first = client.get("/api/changeset/task", params=_params())
    assert first.status_code == 200, first.text
    target = {
        "worktree": fixture.code_repo / ".gitattributes",
        "info": fixture.code_repo / ".git/info/attributes",
        "xdg": xdg / "git/attributes",
    }[location]
    target.write_text("*.py binary\n")

    snapshots = []
    original_snapshot = ChangesetCache.snapshot

    def measured_snapshot(self, source):
        snapshots.append(source)
        return original_snapshot(self, source)

    monkeypatch.setattr(ChangesetCache, "snapshot", measured_snapshot)
    children.clear()
    kept = client.get("/api/changeset/task", params=_params())
    assert children == [], children
    assert snapshots == [], snapshots
    assert kept.content == first.content

    other = FastAPI()
    register_changeset_routes(other, fixture.config)
    with TestClient(other) as cold:
        fresh = cold.get("/api/changeset/task", params=_params())
        assert fresh.json() == leaf_changeset(fixture.config, REPO, MASTER, LEAF_ONE, "committed")
        assert fresh.content != first.content
        assert fresh.json()["code"][0]["insertions"] is None


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


def test_lost_recorded_commit_is_not_observed_on_a_hit_and_a_cold_refusal_recovers(world):
    """The gap and its edge: a hit never observes the object; a cold refusal is not kept."""

    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    path = fixture.code_repo / ".git/objects" / fixture.tip_one[:2] / fixture.tip_one[2:]
    data, mode = path.read_bytes(), path.stat().st_mode
    path.unlink()

    children.clear()
    kept = client.get("/api/changeset/task", params=_params())
    assert kept.status_code == 200 and kept.content == first.content
    assert children == []

    other = FastAPI()
    register_changeset_routes(other, fixture.config)
    with TestClient(other) as cold:
        for _ in range(2):
            assert cold.get("/api/changeset/task", params=_params()).status_code == 404
        path.write_bytes(data)
        path.chmod(mode)
        restored = cold.get("/api/changeset/task", params=_params())
        assert restored.status_code == 200 and restored.content == first.content


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


def test_working_content_edit_with_the_same_status_letter_is_seen(world):
    """MIK-R176 rule 5: the working check moves with the bytes, not only the status letter."""
    fixture, client, children = world
    _live(fixture)
    path = fixture.code_repo / KEEP_PATH
    path.write_text("one line\n")
    first = client.get("/api/changeset/task", params=_params("working"))
    assert first.json()["counters"]["code"]["insertions"] == 1
    path.write_text("one\ntwo\nthree\n")
    children.clear()
    fresh = client.get("/api/changeset/task", params=_params("working"))
    assert fresh.json()["counters"]["code"]["insertions"] == 3
    assert any("--numstat" in child for child in children)


def test_commit_range_reuse_observes_nothing_and_starts_no_git(world, monkeypatch):
    """MIK-R176 rule 5: an id-keyed pair is served from its key with no observation.

    Architect ruling 2026-10-18: on a hit there is no Git child, no layout read and no
    stat of an object file — not merely no walk.
    """
    _, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    assert first.status_code == 200
    walked: list[object] = []
    original = changeset_generation.os.walk

    def counted(path, *args, **kwargs):
        walked.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(changeset_generation.os, "walk", counted)
    children.clear()
    again = client.get("/api/changeset/task", params=_params())
    assert again.content == first.content
    assert walked == []
    assert children == []  # no Git process is started for a hit


def test_one_read_does_not_wait_on_another_cache_read(tmp_path, monkeypatch):
    """MIK-R176 rule 3: the lock covers the cache's maps, never an observation."""
    cache = ChangesetCache()
    slow_input = tmp_path / "slow"
    fast_input = tmp_path / "fast"
    slow_input.write_text("slow")
    fast_input.write_text("fast")
    observing = threading.Event()
    release = threading.Event()
    original = cache.snapshot

    def blocking(source):
        if isinstance(source, Path) and source == slow_input:
            observing.set()
            assert release.wait(5)
        return original(source)

    monkeypatch.setattr(cache, "snapshot", blocking)
    produced: list[str] = []

    def slow_read():
        with cache.reading():
            produced.append(cached_read(("slow",), (slow_input,), lambda: "slow"))

    thread = threading.Thread(target=slow_read)
    thread.start()
    assert observing.wait(5)
    started = time.perf_counter()
    with cache.reading():
        assert cached_read(("fast",), (fast_input,), lambda: "fast") == "fast"
    elapsed = time.perf_counter() - started
    release.set()
    thread.join(5)
    assert elapsed < 1.0
    assert produced == ["slow"]


def test_the_admitted_input_carrier_does_not_change_what_a_read_produces(world, monkeypatch):
    """M4-27 (2): the carrier file chooses freshness inputs only; an un-abandoned read is unchanged.

    Same fixture, two apps: the warm one serves the route from its cache, the cold one computes
    the same request natively. Return bytes, status, and the Git commands with their order must
    match, and a revision that is not an object id must keep the general repository observation.
    """

    fixture, client, children = world
    warm = client.get("/api/changeset/task", params=_params())
    warm_children = list(children)

    app = FastAPI()
    register_changeset_routes(app, fixture.config)
    cold_children: list[list[str]] = []
    original = git_command._run_git

    def measured(*args, **kwargs):
        cold_children.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(git_command, "_run_git", measured)
    with TestClient(app) as cold:
        native = cold.get("/api/changeset/task", params=_params())

    assert warm.status_code == native.status_code == 200
    assert warm.content == native.content  # the answer is the same answer
    assert warm_children == cold_children  # same commands, same order, same count

    ids = changeset_reads._range_inputs(fixture.code_repo, "a" * 40, "b" * 40)
    assert ids == ()  # served from its key: no observation at all
    refs = changeset_reads._range_inputs(fixture.code_repo, "HEAD~1", "HEAD")
    assert [type(source).__name__ for source in refs] == ["RepositoryRead"]


def test_only_immutable_object_ids_are_treated_as_ids(tmp_path):
    """M4-27-A1 (2): a ref, a revision expression or a short name is never an object id.

    The admitted carrier classifies the revisions a read names; anything that is not one
    complete lowercase object id must keep the general observation path.
    """

    full = "a" * 40
    longer = "b" * 64
    assert changeset_reads._object_ids([full]) == (full,)
    assert changeset_reads._object_ids([longer]) == (longer,)
    assert changeset_reads._object_ids([f"{full}^{{commit}}"]) == (full,)
    assert changeset_reads._object_ids([f"{full}:path/to/file"]) == (full,)

    for spelling in (
        "HEAD",
        "HEAD~1",
        "origin/main",
        "HEAD~1..HEAD",
        "abc1234",
        "a" * 39,
        "A" * 40,
        "",
    ):
        assert changeset_reads._object_ids([spelling]) == (), spelling

    ref = changeset_reads._revision_inputs(tmp_path, "HEAD")
    assert [type(source).__name__ for source in ref] == ["RepositoryRead"]
    assert changeset_reads._revision_inputs(tmp_path, full) == ()
    assert changeset_reads._revision_inputs(tmp_path, longer) == ()


def test_a_working_view_has_no_cache_and_no_freshness_check(world):
    """L174-R1-06: the working view is read natively; nothing is retained for it."""

    fixture, _client, children = world
    _live(fixture)
    app = FastAPI()
    cache = register_changeset_routes(app, fixture.config)
    with TestClient(app) as client:
        first = client.get("/api/changeset/task", params=_params("working"))
        children.clear()
        again = client.get("/api/changeset/task", params=_params("working"))

    assert first.status_code == again.status_code == 200
    assert again.content == first.content
    assert any("--name-status" in child and "diff" in child for child in children)
    assert not any("--untracked-files=all" in child for child in children)
    assert not any(key[0] == "changed-files" for key in cache._values)


def test_a_failed_status_makes_no_successful_result_reusable(world, monkeypatch):
    """L174-R1-04: an unknown working stamp admits a refusal, never a success."""

    fixture, _client, _children = world
    cache = ChangesetCache(capacity=8)
    produced: list[int] = []

    def unavailable(*_args, **_kwargs):
        raise OSError("error: bad tree object HEAD")

    monkeypatch.setattr(changeset_cache, "working_generation", unavailable)
    source = RepositoryRead(fixture.code_repo, working=True)
    for _ in range(2):
        with cache.reading():
            assert cached_read(
                ("count",), (source,), lambda: produced.append(1) or {"count": 1}
            ) == {"count": 1}
    assert len(produced) == 2  # both reads computed; nothing was kept
    assert cache.retained[0] == 0


def test_a_commit_pair_hit_survives_object_loss_and_starts_no_git(world):
    """Rule 5's accepted gap, as ruled: a kept pair outlives loss of its objects.

    A hit starts no Git process and answers the same bytes after the tip tree is removed.
    A fresh app on the same repository fails natively, which is the damage the gap names.
    """

    fixture, client, children = world
    first = client.get("/api/changeset/task", params=_params())
    assert first.status_code == 200
    tree = _git(fixture.code_repo, ["rev-parse", "HEAD^{tree}"]).strip()
    loose = fixture.code_repo / ".git" / "objects" / tree[:2] / tree[2:]
    assert loose.is_file()
    loose.unlink()

    children.clear()
    kept = client.get("/api/changeset/task", params=_params())
    assert kept.status_code == 200
    assert children == [], children
    assert kept.content == first.content

    other = FastAPI()
    register_changeset_routes(other, fixture.config)
    with TestClient(other) as cold, pytest.raises(RuntimeError):
        cold.get("/api/changeset/task", params=_params())


def test_the_request_object_memo_is_bounded(tmp_path, monkeypatch):
    """L174-R1-07: the per-read census memo respects the cache's capacity."""

    cache = ChangesetCache(capacity=2)
    monkeypatch.setattr(cache, "_object_revision", lambda root, **_kwargs: f"revision:{root}")
    with cache.reading():
        read = changeset_cache._active.get()
        assert read is not None
        for index in range(6):
            read.object_revision(tmp_path / f"root-{index}", tolerate_additions=False)
        assert len(read.objects) <= cache.capacity
