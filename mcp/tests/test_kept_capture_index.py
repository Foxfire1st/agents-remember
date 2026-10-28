"""A worktree's kept capture index names the fresh capture's tree and writes only what moved (D569).

A capture from a fresh copy of the worktree's index writes again every object it already holds for
a changed path, and Git freshens such an object's file -- the loose file or its whole pack -- which
moves the storage witnesses a review's closure proofs hold. The kept index writes nothing while
nothing moved, and while the worktree's status witness proves the last capture, no capture runs.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import mock

import pytest
from agents_remember.kernel import git_command
from agents_remember.kernel.git_command import run_git_with_index
from agents_remember.worktrees.modules import kept_capture
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.modules.kept_capture import kept_candidate_tree


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_AUTHOR_NAME": "kept capture fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "kept capture fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        },
    )
    assert result.returncode == 0, f"fixture git {' '.join(args)} failed: {result.stderr.strip()}"
    return result.stdout.strip()


FILES = {
    ".gitignore": "*.log\n",
    "a.txt": "alpha one\n",
    "dir/b.txt": "bravo one\n",
    "dir/sub/c.py": "def c():\n    return 1\n",
    "odd name.txt": "a name with a space\n",
}


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "repo"
    for relative, text in FILES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    git(root.parent, "init", "-q", "-b", "main", str(root))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    git(root, "repack", "-adq")  # the store's objects live in one pack, as a used repository's do
    kept_capture._HELD.clear()
    yield root
    kept_capture._HELD.clear()


def reference(repo: Path) -> str:
    """The full capture: an empty private index seeded from ``HEAD``, every file hashed."""
    with TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        for args in (["read-tree", "HEAD"], ["add", "-A"]):
            assert run_git_with_index(repo, args, index).returncode == 0
        return run_git_with_index(repo, ["write-tree"], index).stdout.strip()


def kept(repo: Path, coordination: Path, *, held: bool = False) -> str:
    if not held:
        kept_capture._HELD.clear()  # take the kept-index capture, not the held witness
    return kept_candidate_tree(coordination, repo, head=git(repo, "rev-parse", "HEAD"))


@contextmanager
def verbs() -> Iterator[Counter[str]]:
    seen: Counter[str] = Counter()
    run = subprocess.run

    def counting(argv, *args, **kwargs):
        if argv and str(argv[0]) == "git":
            words = [str(word) for word in argv[1:]]
            while words and (words[0].startswith("-") or words[0] in ("-c", "-C")):
                skip = 2 if words[0] in ("-c", "-C") else 1
                words = words[skip:]
            seen[words[0] if words else "?"] += 1
        return run(argv, *args, **kwargs)

    with mock.patch.object(git_command.subprocess, "run", counting):
        yield seen


def store_files(repo: Path) -> dict[str, tuple[int, int, int]]:
    """Every object file of the store -- loose, pack and pack index -- with its size and times."""
    objects = repo / ".git" / "objects"
    found = {}
    for path in sorted(objects.rglob("*")):
        if path.is_file() and (path.parent.name == "pack" or len(path.parent.name) == 2):
            info = path.stat()
            found[str(path.relative_to(objects))] = (
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
    return found


def backdated(repo: Path) -> None:
    """Every object file's times a minute back, so any later write of one is seen."""
    for name in store_files(repo):
        path = repo / ".git" / "objects" / name
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns - 60 * 10**9, info.st_mtime_ns - 60 * 10**9))


def settled(repo: Path) -> None:
    """Every worktree file last written two minutes ago, as on a quiet worktree.

    Git compares by content an entry written in the second its index file was written ("racily
    clean") and smudges it when that index is written; such an entry is hashed and written again
    by the next capture. A worktree file edited a moment before a capture is one such entry, once.
    """
    for path in repo.rglob("*"):
        if ".git" not in path.relative_to(repo).parts and path.is_file():
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns - 120 * 10**9, info.st_mtime_ns - 120 * 10**9))


def test_the_kept_capture_names_the_fresh_capture_tree_through_every_change(
    repo: Path, tmp_path: Path
) -> None:
    coordination = tmp_path / "coordination"
    assert kept(repo, coordination) == reference(repo)
    steps = [
        lambda: (repo / "a.txt").write_text("alpha two\n", encoding="utf-8"),
        lambda: (repo / "new.txt").write_text("an untracked file\n", encoding="utf-8"),
        (repo / "dir" / "b.txt").unlink,
        # An earlier capture added new.txt; now it is ignored, so the fresh capture leaves it out.
        lambda: (repo / ".gitignore").write_text("*.log\nnew.txt\n", encoding="utf-8"),
        lambda: (repo / "debug.log").write_text("ignored\n", encoding="utf-8"),
        lambda: git(repo, "add", "a.txt"),  # a staged change in the worktree's own index
        lambda: git(repo, "commit", "-q", "-am", "moves HEAD"),
        lambda: (repo / "dir" / "sub" / "c.py").write_text("def c():\n    return 9\n"),
    ]
    for step in steps:
        step()
        assert kept(repo, coordination) == reference(repo)
    # A same-size rewrite right after a capture, in the second its kept index was written.
    tree = kept(repo, coordination)
    (repo / "a.txt").write_text("alpha six\n", encoding="utf-8")
    assert kept(repo, coordination) == reference(repo) != tree
    assert git(repo, "status", "--porcelain") != ""  # the worktree's own index was not changed


def test_a_quiet_second_capture_writes_no_object_and_moves_no_store_file(
    repo: Path, tmp_path: Path
) -> None:
    coordination = tmp_path / "coordination"
    (repo / "a.txt").write_text("alpha two\n", encoding="utf-8")
    (repo / "new.txt").write_text("an untracked file\n", encoding="utf-8")
    settled(repo)
    first = kept(repo, coordination)
    backdated(repo)
    before, census = store_files(repo), git(repo, "count-objects", "-v")
    with verbs() as seen:
        second = kept(repo, coordination)
    assert seen["add"] == 1 and seen["write-tree"] == 1, "the capture did not run"
    assert store_files(repo) == before, "a quiet capture rewrote an object file"
    assert git(repo, "count-objects", "-v") == census
    assert second == first == reference(repo)  # last: the full capture writes every object


def test_a_capture_from_a_fresh_index_copy_freshens_the_objects_it_holds(repo: Path) -> None:
    """Guard: the cause the kept index removes (passes before and after)."""
    (repo / "a.txt").write_text("alpha two\n", encoding="utf-8")
    with TemporaryDirectory() as scratch:
        first = worktree_candidate_tree(repo, Path(scratch) / "index")
    backdated(repo)
    before = store_files(repo)
    with TemporaryDirectory() as scratch:
        assert worktree_candidate_tree(repo, Path(scratch) / "index") == first
    assert store_files(repo) != before


def test_while_its_status_witness_holds_no_capture_runs_and_a_change_captures_again(
    repo: Path, tmp_path: Path
) -> None:
    coordination = tmp_path / "coordination"
    first = kept(repo, coordination)
    with verbs() as seen:
        assert kept(repo, coordination, held=True) == first
    assert seen["add"] == seen["write-tree"] == seen["read-tree"] == 0
    assert seen["status"] == 1
    (repo / "dir" / "b.txt").write_text("bravo two\n", encoding="utf-8")
    with verbs() as seen:
        moved = kept(repo, coordination, held=True)
    assert moved == reference(repo) != first
    assert seen["add"] == 1


def test_concurrent_captures_of_one_worktree_take_turns_and_name_one_tree(
    repo: Path, tmp_path: Path
) -> None:
    coordination = tmp_path / "coordination"
    (repo / "a.txt").write_text("alpha two\n", encoding="utf-8")
    trees: list[str] = []
    head = git(repo, "rev-parse", "HEAD")
    threads = [
        Thread(target=lambda: trees.append(kept_candidate_tree(coordination, repo, head=head)))
        for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert trees == [reference(repo)] * 6
