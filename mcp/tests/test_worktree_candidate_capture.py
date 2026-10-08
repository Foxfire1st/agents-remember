"""MIK-R40 rule 1: one capture owner that trusts Git's own evidence, and returns the same tree.

``worktrees/modules/git.py::worktree_candidate_tree`` is the capture every identity rests on: the
review's two candidates and their recheck, the closeout's exact memory tree and its gate, the
preview, the door evidence, the coherence observation, sync and direct landing. It used to start
from an empty private index, so ``git add -A`` hashed every file of the worktree on every capture.
It now starts from a copy of the worktree's own index, brought to ``HEAD``'s entries, so only files
Git cannot prove unchanged are hashed.

The contract these cases hold it to is identity, not speed: for every worktree state the packet
names, the new capture returns **the tree the previous implementation returns**. The previous
implementation is kept here verbatim (:func:`previous_capture`) so that no later change of the
product's fallback can move the reference. Each state also proves that the capture writes its
objects into the repository and leaves the worktree's real index byte-identical, with its time and
with no lock file beside it.

Each state runs in its own linked worktree of one repository, which is the shape the product
captures (a leaf's code and memory worktrees are linked worktrees).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest
from agents_remember.kernel import git_command
from agents_remember.kernel.git_command import run_git_with_index
from agents_remember.models.memory_content_excludes import MEMORY_CONTENT_EXCLUDES
from agents_remember.worktrees.modules import git as capture_owner
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from knowledge_index_test_support import rewrite_in_the_second_of_the_index_write

EXCLUDE_VARIANTS: tuple[tuple[str, ...], ...] = ((), ("memory.md",), MEMORY_CONTENT_EXCLUDES)


def git(root: Path, *args: str, check: bool = True, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        input=stdin,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_AUTHOR_NAME": "capture fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "capture fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        },
    )
    if check and result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def previous_capture(repo: Path, index_path: Path, exclude_paths: tuple[str, ...] = ()) -> str:
    """The capture as it was before MIK-R40, kept verbatim as the reference of every state.

    An empty private index is seeded with ``read-tree HEAD``; such entries carry no file times, so
    ``git add -A`` hashes every file of the worktree.
    """

    index_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{index_path.name}-", dir=index_path.parent) as temporary:
        isolated_index = Path(temporary) / "index"
        add_args = ["add", "-A"]
        if exclude_paths:
            add_args.extend(["--", ".", *map(capture_owner._excluded_pathspec, exclude_paths)])
        actions = [("seed candidate index", ["read-tree", "HEAD"])]
        if exclude_paths:
            actions.append(
                ("exclude derived files", ["update-index", "--force-remove", "--", *exclude_paths])
            )
        actions.append(("materialize candidate tree", add_args))
        for action, args in actions:
            result = run_git_with_index(repo, args, isolated_index)
            if result.returncode != 0:
                raise RuntimeError(f"could not {action}: {result.stderr.strip()}")
        result = run_git_with_index(repo, ["write-tree"], isolated_index)
        if result.returncode != 0:
            raise RuntimeError(f"could not resolve candidate tree: {result.stderr.strip()}")
        return result.stdout.strip()


# -- the fixture: one repository, one linked worktree per state ---------------------------------------

HEAD_FILES: dict[str, str] = {
    ".gitignore": "*.log\nignored/\n",
    "a.txt": "alpha one\n",
    "dir/b.txt": "bravo one\n",
    "dir/sub/c.py": "def c():\n    return 1\n",
    "dir/sub/d.py": "def d():\n    return 2\n",
    "odd name.txt": "a name with a space\n",
    "uni/ü.txt": "a name outside ASCII\n",
    "memory.md": "# ledger cache\n",
    "bootstrap/plan.md": "# scaffolding\n",
    "knowledge/layout.json": '{"schema": "ar-memory-layout/v2"}\n',
    "onboarding/card.md": "# a card\n",
}


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("capture") / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "commit.gpgsign", "false")
    for relative, content in HEAD_FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (root / "tool.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (root / "tool.sh").chmod(0o755)
    (root / "link").symlink_to("a.txt")
    # A file HEAD tracks although an ignore rule matches it: tracked files stay in the tree.
    (root / "tracked.log").write_text("tracked although ignored\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "add", "-f", "tracked.log")
    git(root, "commit", "-q", "-m", "fixture")
    return root


@dataclass
class Worktree:
    root: Path
    scratch: Path

    def write(self, relative: str, content: str) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def index(self) -> Path:
        return Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-path", "index"))


def _tracked(root: Path) -> list[Path]:
    return [root / name for name in git(root, "ls-files", "-z").split("\0") if name]


def settle(root: Path) -> None:
    """The ordinary state of a worktree someone works in: every index entry's recorded file time is
    the file's, and older than the index file itself, so Git trusts the entries it has."""

    past = time.time() - 60
    for path in _tracked(root):
        if path.is_symlink() or path.exists():
            os.utime(path, (past, past), follow_symlinks=False)
    git(root, "update-index", "-q", "--refresh")
    assert int(Path(git(root, "rev-parse", "--git-path", "index")).stat().st_mtime) > int(past)


@pytest.fixture
def worktree(repository: Path, tmp_path: Path) -> Worktree:
    root = tmp_path / "wt"
    git(repository, "worktree", "add", "-q", "--detach", str(root), "main")
    settle(root)
    return Worktree(root, tmp_path / "scratch")


# -- what a capture may not change ------------------------------------------------------------------


def _digest(path: Path) -> str:
    if path.is_symlink():
        return f"link:{os.readlink(path)}"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observed(worktree: Worktree) -> dict[str, object]:
    """The worktree's real index (bytes and time), every lock beside it, and every working file."""

    index = worktree.index()
    files = {
        str(path.relative_to(worktree.root)): (_digest(path), path.lstat().st_mtime_ns)
        for path in sorted(worktree.root.rglob("*"))
        if (path.is_symlink() or path.is_file()) and ".git" not in path.parts
    }
    return {
        "index": index.read_bytes() if index.is_file() else None,
        "index time": index.stat().st_mtime_ns if index.is_file() else None,
        "locks": sorted(path.name for path in index.parent.glob("*.lock")),
        "files": files,
    }


def holds_every_object(repository: Path, tree: str) -> bool:
    """Whether the repository holds ``tree`` and every tree and blob it names (gitlinks excepted)."""

    listed = subprocess.run(
        ["git", "ls-tree", "-r", "-t", tree],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        return False
    names = [row.split()[2] for row in listed.stdout.splitlines() if row.split()[1] != "commit"]
    checked = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
        input="".join(f"{name}\n" for name in [tree, *names]),
    )
    return "missing" not in checked.stdout and checked.returncode == 0


@dataclass
class Captured:
    tree: str
    fell_back: bool


def capture(worktree: Worktree, exclude_paths: tuple[str, ...] = ()) -> Captured:
    """The product capture, with whether it had to fall back to the full capture."""

    full = capture_owner._tree_from_head
    with mock.patch.object(capture_owner, "_tree_from_head", wraps=full) as fallback:
        tree = worktree_candidate_tree(
            worktree.root, worktree.scratch / "new" / "index", exclude_paths=exclude_paths
        )
    return Captured(tree, fallback.call_count > 0)


def assert_same_tree_as_before(worktree: Worktree, *, fallback: bool = False) -> dict[str, str]:
    """The new capture returns the previous implementation's tree, for every exclusion a caller
    passes, writes its objects, and leaves the real index and the working files as they were."""

    trees: dict[str, str] = {}
    for exclude_paths in EXCLUDE_VARIANTS:
        before = observed(worktree)
        captured = capture(worktree, exclude_paths)
        assert observed(worktree) == before, "the capture changed the real index or a file"
        assert holds_every_object(worktree.root, captured.tree)
        assert captured.fell_back is fallback
        reference = previous_capture(
            worktree.root, worktree.scratch / "old" / "index", exclude_paths
        )
        assert captured.tree == reference, f"the trees differ with exclude_paths={exclude_paths}"
        trees[",".join(exclude_paths)] = captured.tree
    assert not list(worktree.scratch.rglob("index"))  # every private index is gone again
    return trees


# -- the states --------------------------------------------------------------------------------------


def _clean(_: Worktree) -> None:
    return None


def _edited(worktree: Worktree) -> None:
    worktree.write("a.txt", "alpha two, a longer line\n")
    worktree.write("dir/sub/c.py", "def c():\n    return 3\n")


def _added(worktree: Worktree) -> None:
    worktree.write("new.txt", "a new file\n")
    worktree.write("fresh/deep/e.py", "E = 1\n")
    worktree.write("bootstrap/untracked.md", "scaffolding nobody commits\n")


def _deleted(worktree: Worktree) -> None:
    (worktree.root / "dir/b.txt").unlink()
    shutil.rmtree(worktree.root / "uni")


def _staged(worktree: Worktree) -> None:
    worktree.write("a.txt", "alpha staged\n")
    worktree.write("staged-new.txt", "staged and new\n")
    git(worktree.root, "add", "a.txt", "staged-new.txt")
    git(worktree.root, "rm", "-q", "dir/b.txt")


def _staged_then_edited_again(worktree: Worktree) -> None:
    _staged(worktree)
    worktree.write("a.txt", "alpha after the staging\n")
    worktree.write("staged-new.txt", "changed after it was staged\n")
    worktree.write("dir/b.txt", "back again, untracked now\n")


def _staged_ignored_not_in_head(worktree: Worktree) -> None:
    # In the real index, not in HEAD, and ignored: `git add -A` on a copy of the index would keep
    # it, the capture from HEAD never had it. The one-way merge of HEAD drops it from the copy.
    worktree.write("debug.log", "an ignored file\n")
    worktree.write("ignored/inside.txt", "inside an ignored directory\n")
    git(worktree.root, "add", "-f", "debug.log", "ignored/inside.txt")


def _tracked_ignored_removed_from_index(worktree: Worktree) -> None:
    # In HEAD, ignored, removed from the real index, still on disk: the capture from HEAD has the
    # entry and updates it; the one-way merge brings the entry back into the copy.
    git(worktree.root, "rm", "-q", "--cached", "tracked.log")
    worktree.write("tracked.log", "edited while out of the index\n")


def _untracked_ignored(worktree: Worktree) -> None:
    worktree.write("build.log", "ignored, never added\n")
    worktree.write("ignored/cache.bin", "ignored directory\n")


def _assume_unchanged(worktree: Worktree) -> None:
    git(worktree.root, "update-index", "--assume-unchanged", "a.txt", "dir/sub/c.py")
    worktree.write("a.txt", "edited behind assume-unchanged\n")
    (worktree.root / "dir/sub/c.py").unlink()


def _skip_worktree(worktree: Worktree) -> None:
    git(worktree.root, "update-index", "--skip-worktree", "a.txt", "dir/b.txt", "odd name.txt")
    worktree.write("a.txt", "edited behind skip-worktree\n")
    (worktree.root / "dir/b.txt").unlink()


def _both_flags(worktree: Worktree) -> None:
    git(worktree.root, "update-index", "--assume-unchanged", "a.txt")
    git(worktree.root, "update-index", "--skip-worktree", "a.txt")
    worktree.write("a.txt", "edited behind both flags\n")


def _stale_file_times(worktree: Worktree) -> None:
    # Every file is touched after the index recorded it: no recorded time matches, so every file
    # is hashed, as the full capture hashes it. One of them really changed.
    now = time.time()
    for path in _tracked(worktree.root):
        os.utime(path, (now, now), follow_symlinks=False)
    worktree.write("dir/b.txt", "bravo two\n")


def _fresh_checkout_in_one_second(worktree: Worktree) -> None:
    # A checkout writes the files and the index in the same second: every entry may have been
    # rewritten in that second, so Git compares each by content.
    git(worktree.root, "checkout", "-q", "-f", "HEAD", "--", ".")
    git(worktree.root, "read-tree", "--reset", "-u", "HEAD")


def _mode_change(worktree: Worktree) -> None:
    (worktree.root / "a.txt").chmod(0o755)
    (worktree.root / "tool.sh").chmod(0o644)


def _symlinks(worktree: Worktree) -> None:
    (worktree.root / "link").unlink()
    (worktree.root / "link").symlink_to("dir/b.txt")
    (worktree.root / "another").symlink_to("missing-target")
    (worktree.root / "dir/sub/d.py").unlink()
    (worktree.root / "dir/sub/d.py").symlink_to("c.py")


def _nested_repository(worktree: Worktree) -> None:
    nested = worktree.root / "vendor" / "nested"
    nested.mkdir(parents=True)
    git(nested, "init", "-q", "-b", "main")
    (nested / "inner.txt").write_text("inside another repository\n", encoding="utf-8")
    git(nested, "add", "-A")
    git(nested, "commit", "-q", "-m", "nested")


def _submodule_moved(worktree: Worktree) -> None:
    # HEAD records the nested repository as a gitlink; its own HEAD then moves and it gains a file.
    _nested_repository(worktree)
    git(worktree.root, "add", "vendor/nested")
    git(worktree.root, "commit", "-q", "-m", "record the nested repository")
    settle(worktree.root)
    nested = worktree.root / "vendor" / "nested"
    (nested / "inner.txt").write_text("the nested repository moved on\n", encoding="utf-8")
    git(nested, "commit", "-q", "-am", "nested, second commit")
    (nested / "untracked.txt").write_text("not committed inside the nested one\n", encoding="utf-8")
    worktree.write("a.txt", "edited beside a gitlink\n")


def _intent_to_add(worktree: Worktree) -> None:
    worktree.write("planned.txt", "added with -N\n")
    worktree.write("planned.log", "ignored and added with -N\n")
    git(worktree.root, "add", "-N", "planned.txt")
    git(worktree.root, "add", "-N", "-f", "planned.log")


def _renamed(worktree: Worktree) -> None:
    git(worktree.root, "mv", "a.txt", "renamed.txt")
    (worktree.root / "dir/sub/c.py").rename(worktree.root / "dir/sub/moved.py")


def _file_and_directory_swap(worktree: Worktree) -> None:
    (worktree.root / "a.txt").unlink()
    worktree.write("a.txt/inside.txt", "a.txt is a directory now\n")
    shutil.rmtree(worktree.root / "dir")
    worktree.write("dir", "dir is a file now\n")


def _excluded_paths_changed(worktree: Worktree) -> None:
    # What the closeout excludes: the ledger cache and the bootstrap scaffolding, edited, staged
    # and added, beside one ordinary edit.
    worktree.write("memory.md", "# ledger cache, rewritten\n")
    worktree.write("bootstrap/plan.md", "# scaffolding, edited\n")
    worktree.write("bootstrap/more.md", "# more scaffolding\n")
    git(worktree.root, "add", "bootstrap/more.md")
    worktree.write("onboarding/card.md", "# a card, edited\n")


def _ledger_ignored_and_untracked(worktree: Worktree) -> None:
    # The real memory worktree's shape: the ledger cache is ignored and not in the index.
    git(worktree.root, "rm", "-q", "--cached", "memory.md")
    worktree.write(".gitignore", "*.log\nignored/\n/memory.md\n")
    worktree.write("memory.md", "# ledger cache, local\n")


def _line_endings_and_binary(worktree: Worktree) -> None:
    (worktree.root / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    (worktree.root / "binary.bin").write_bytes(bytes(range(256)) * 8)
    (worktree.root / "a.txt").write_bytes(b"alpha\r\n")


def _split_index(worktree: Worktree) -> None:
    git(worktree.root, "update-index", "--split-index")
    worktree.write("a.txt", "edited with a split index\n")
    git(worktree.root, "add", "a.txt")
    worktree.write("dir/b.txt", "edited after the split\n")


def _untracked_cache_and_version_4(worktree: Worktree) -> None:
    git(worktree.root, "update-index", "--index-version", "4")
    git(worktree.root, "update-index", "--untracked-cache")
    git(worktree.root, "status", "--porcelain")
    worktree.write("new.txt", "added after the untracked cache was written\n")
    worktree.write("a.txt", "edited under index version 4\n")


# The product creates no sparse worktree; a user may still have made one of a leaf's worktree. The
# paths outside the sparse definition carry ``skip-worktree`` and are absent from the working tree.
def _sparse_checkout(worktree: Worktree) -> None:
    git(worktree.root, "sparse-checkout", "set", "--no-cone", "/*", "!/dir/sub/")
    assert not (worktree.root / "dir/sub/c.py").exists()
    worktree.write("a.txt", "edited in a sparse worktree\n")
    worktree.write("new.txt", "added in a sparse worktree\n")


def _sparse_checkout_cone(worktree: Worktree) -> None:
    git(worktree.root, "sparse-checkout", "set", "--cone", "dir/sub")
    assert not (worktree.root / "uni").exists()
    worktree.write("dir/sub/c.py", "def c():\n    return 5\n")
    (worktree.root / "a.txt").unlink()


def _sparse_index(worktree: Worktree) -> None:
    git(worktree.root, "sparse-checkout", "set", "--cone", "--sparse-index", "dir/sub")
    worktree.write("dir/sub/c.py", "def c():\n    return 6\n")
    worktree.write("dir/sub/new.py", "NEW = 1\n")


STATES: dict[str, Callable[[Worktree], None]] = {
    "clean": _clean,
    "edited": _edited,
    "added": _added,
    "deleted": _deleted,
    "staged": _staged,
    "staged, then edited again": _staged_then_edited_again,
    "staged, ignored and not in HEAD": _staged_ignored_not_in_head,
    "in HEAD, ignored, removed from the index": _tracked_ignored_removed_from_index,
    "untracked ignored file": _untracked_ignored,
    "assume-unchanged entries": _assume_unchanged,
    "skip-worktree entries": _skip_worktree,
    "both flags on one entry": _both_flags,
    "stale file times": _stale_file_times,
    "a checkout in the second of its index write": _fresh_checkout_in_one_second,
    "file mode change": _mode_change,
    "symlinks": _symlinks,
    "nested repository": _nested_repository,
    "a recorded gitlink whose repository moved": _submodule_moved,
    "intent to add": _intent_to_add,
    "renamed": _renamed,
    "file and directory swapped": _file_and_directory_swap,
    "the closeout's excluded paths changed": _excluded_paths_changed,
    "the ledger cache ignored and untracked": _ledger_ignored_and_untracked,
    "line endings and a binary file": _line_endings_and_binary,
    "split index": _split_index,
    "untracked cache and index version 4": _untracked_cache_and_version_4,
    "sparse checkout": _sparse_checkout,
    "sparse checkout, cone": _sparse_checkout_cone,
    "sparse index": _sparse_index,
}


@pytest.fixture
def worktrees(repository: Path, tmp_path: Path) -> Iterator[Callable[[str], Worktree]]:
    made = 0

    def make(_name: str) -> Worktree:
        nonlocal made
        made += 1
        root = tmp_path / f"wt-{made}"
        git(repository, "worktree", "add", "-q", "--detach", str(root), "main")
        settle(root)
        return Worktree(root, tmp_path / f"scratch-{made}")

    yield make


def test_every_worktree_state_is_captured_as_the_tree_the_previous_capture_returns(
    worktrees: Callable[[str], Worktree],
) -> None:
    """The equality matrix: one linked worktree per state, every exclusion variant, both sides'
    shapes (source files, and a memory tree's records, cards, ledger cache and scaffolding)."""

    seen: dict[str, dict[str, str]] = {}
    for name, change in STATES.items():
        worktree = worktrees(name)
        change(worktree)
        try:
            seen[name] = assert_same_tree_as_before(worktree)
        except AssertionError as error:
            raise AssertionError(f"state {name!r}: {error}") from error
    head = git(worktrees("head").root, "rev-parse", "HEAD^{tree}")
    # The states are real: a clean worktree is HEAD's tree, and the others are other trees.
    assert seen["clean"][""] == head == seen["untracked ignored file"][""]
    assert seen["a checkout in the second of its index write"][""] == head
    for name in ("edited", "added", "deleted", "staged", "assume-unchanged entries", "symlinks"):
        assert seen[name][""] != head, name
    # A staged path that is ignored and not in HEAD is in neither capture's tree.
    assert seen["staged, ignored and not in HEAD"][""] == head
    # The closeout's exclusions really exclude: the ledger and the scaffolding are not in its tree.
    excluded = seen["the closeout's excluded paths changed"]
    assert (
        len({excluded[""], excluded["memory.md"], excluded[",".join(MEMORY_CONTENT_EXCLUDES)]}) == 3
    )


def test_the_flags_that_trust_the_index_are_cleared_on_the_copy_only(worktree: Worktree) -> None:
    """A capture that kept ``assume-unchanged`` or ``skip-worktree`` would return HEAD's blob for a
    file that was edited. The real index keeps both flags."""

    _assume_unchanged(worktree)
    git(worktree.root, "update-index", "--skip-worktree", "odd name.txt", "uni/ü.txt")
    worktree.write("odd name.txt", "edited behind skip-worktree\n")
    flags = git(worktree.root, "ls-files", "-v")
    captured = capture(worktree)
    assert not captured.fell_back
    assert git(worktree.root, "ls-files", "-v") == flags and "h a.txt" in flags and "S odd" in flags
    assert git(worktree.root, "rev-parse", f"{captured.tree}:a.txt") == git(
        worktree.root, "hash-object", "a.txt"
    )
    assert git(worktree.root, "rev-parse", f"{captured.tree}:odd name.txt") == git(
        worktree.root, "hash-object", "odd name.txt"
    )
    listed = git(worktree.root, "ls-tree", "-r", "--name-only", captured.tree)
    assert "dir/sub/c.py" not in listed.splitlines()  # deleted behind assume-unchanged
    assert captured.tree == previous_capture(worktree.root, worktree.scratch / "old" / "index")


def test_a_capture_taken_in_a_subdirectory_clears_the_flags_of_the_whole_worktree(
    worktree: Worktree,
) -> None:
    """``git add -A`` updates the whole worktree from any directory, so an entry outside the
    directory the capture runs in must lose its flag too, or its edit would be left out."""

    _assume_unchanged(worktree)
    git(worktree.root, "update-index", "--skip-worktree", "odd name.txt")
    worktree.write("odd name.txt", "edited behind skip-worktree, outside the directory\n")
    worktree.write("dir/b.txt", "bravo, edited inside the directory\n")
    inside = worktree.root / "dir"
    for exclude_paths in EXCLUDE_VARIANTS:
        full = capture_owner._tree_from_head
        with mock.patch.object(capture_owner, "_tree_from_head", wraps=full) as fallback:
            tree = worktree_candidate_tree(
                inside, worktree.scratch / "sub" / "index", exclude_paths=exclude_paths
            )
        assert fallback.call_count == 0
        assert tree == previous_capture(inside, worktree.scratch / "old" / "index", exclude_paths)
    whole = worktree_candidate_tree(inside, worktree.scratch / "sub" / "index")
    assert whole == capture(worktree).tree
    assert git(worktree.root, "rev-parse", f"{whole}:a.txt") == git(
        worktree.root, "hash-object", "a.txt"
    )


def test_a_same_size_rewrite_in_the_second_of_the_index_write_changes_the_tree(
    worktree: Worktree,
) -> None:
    """Git compares by content every entry recorded in the second its index was written, and knows
    those entries by the index file's own time. A copy that loses that time keeps the old blob."""

    target = worktree.root / "dir/b.txt"
    before = capture(worktree).tree
    rewrite_in_the_second_of_the_index_write(worktree.root, target, "bravo two\n")
    captured = capture(worktree)
    assert not captured.fell_back and captured.tree != before
    assert git(worktree.root, "rev-parse", f"{captured.tree}:dir/b.txt") == git(
        worktree.root, "hash-object", "dir/b.txt"
    )
    assert captured.tree == previous_capture(worktree.root, worktree.scratch / "old" / "index")


def test_a_clean_file_is_not_read_and_a_changed_one_is(worktree: Worktree) -> None:
    """Work in proportion to what changed: a file whose recorded times match is not opened at all,
    so the capture succeeds on a worktree holding a file it could not read. A changed file is read:
    made unreadable, it fails the capture as it failed the previous one."""

    if os.geteuid() == 0:
        pytest.skip("root reads every file")
    blocked = worktree.root / "dir/sub/d.py"
    mode = stat.S_IMODE(blocked.stat().st_mode)
    worktree.write("a.txt", "the one changed file\n")
    blocked.chmod(0)
    git(
        worktree.root, "update-index", "-q", "--refresh", check=False
    )  # the mode change is recorded
    try:
        with pytest.raises(RuntimeError, match="materialize candidate tree"):
            previous_capture(worktree.root, worktree.scratch / "old" / "index")
        captured = capture(worktree)
        assert not captured.fell_back
        worktree.write("dir/sub/c.py", "def c():\n    return 4\n")
        (worktree.root / "dir/sub/c.py").chmod(0)
        with pytest.raises(RuntimeError, match="materialize candidate tree"):
            capture(worktree)
    finally:
        blocked.chmod(mode)
        (worktree.root / "dir/sub/c.py").chmod(0o644)
    assert git(worktree.root, "rev-parse", f"{captured.tree}:a.txt") == git(
        worktree.root, "hash-object", "a.txt"
    )


def test_a_changed_attribute_alone_does_not_convert_an_unchanged_file_again(
    worktree: Worktree,
) -> None:
    """The one state found in which the two captures differ, pinned so that it stays a decision.

    A file committed with CRLF line endings is unchanged; an uncommitted ``.gitattributes`` now asks
    for it to be normalized. Git cannot see that from the file: its recorded times still match, so
    ``git add -A`` on the worktree's own index keeps the committed blob, and a commit made from that
    index records it. The capture from the index copy returns exactly that tree. The previous
    capture hashed every file again and so converted the file, returning a tree that the worktree's
    own index does not stage (the closeout's judged-tree check would then refuse the commit).
    """

    (worktree.root / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    git(worktree.root, "add", "crlf.txt")
    git(worktree.root, "commit", "-q", "-m", "a file with CRLF line endings")
    settle(worktree.root)
    worktree.write(".gitattributes", "*.txt text\n")
    captured = capture(worktree)
    previous = previous_capture(worktree.root, worktree.scratch / "old" / "index")
    assert not captured.fell_back and captured.tree != previous
    committed = git(worktree.root, "rev-parse", "HEAD:crlf.txt")
    assert git(worktree.root, "rev-parse", f"{captured.tree}:crlf.txt") == committed
    assert git(worktree.root, "rev-parse", f"{previous}:crlf.txt") != committed
    # What the worktree's own index stages, and so what a commit made from it records.
    git(worktree.root, "add", "-A")
    assert git(worktree.root, "write-tree") == captured.tree
    # Once the file itself is touched, Git reads it, and both captures convert it.
    now = time.time()
    os.utime(worktree.root / "crlf.txt", (now, now))
    assert capture(worktree).tree == previous


def test_the_capture_writes_its_objects_into_the_repository(worktree: Worktree) -> None:
    """An uncommitted candidate is pinned by a ref naming its tree, so the tree and every new blob
    must be in the repository's own object store, not in a scratch one."""

    unique = f"content no other object holds {time.time_ns()}\n"
    worktree.write("fresh/unique.txt", unique)
    blob = git(worktree.root, "hash-object", "fresh/unique.txt")
    assert git(worktree.root, "cat-file", "-t", blob, check=False) == ""
    captured = capture(worktree)
    assert git(worktree.root, "cat-file", "-t", blob) == "blob"
    assert git(worktree.root, "cat-file", "-t", captured.tree) == "tree"
    assert holds_every_object(worktree.root, captured.tree)
    git(worktree.root, "update-ref", "refs/ar/review/t/l/1", captured.tree)  # and can be pinned


# -- the fallback: slower, the same tree, never a guess -------------------------------------------------


def _conflicted_index(worktree: Worktree) -> None:
    git(worktree.root, "checkout", "-q", "-b", "theirs")
    worktree.write("a.txt", "alpha theirs\n")
    git(worktree.root, "commit", "-q", "-am", "theirs")
    git(worktree.root, "checkout", "-q", "--detach", "main")
    worktree.write("a.txt", "alpha ours\n")
    git(worktree.root, "commit", "-q", "-am", "ours")
    git(worktree.root, "merge", "theirs", check=False)
    assert "UU a.txt" in git(worktree.root, "status", "--porcelain")


def _no_index_file(worktree: Worktree) -> None:
    worktree.index().unlink()
    worktree.write("a.txt", "edited with no index file\n")


def _unreadable_index(worktree: Worktree) -> None:
    # Not an index at all: Git refuses the copy by its signature.
    index = worktree.index()
    index.write_bytes(b"JUNK" + index.read_bytes()[4:])
    worktree.write("a.txt", "edited beside a damaged index\n")


def test_a_copy_git_cannot_use_falls_back_to_the_full_capture_and_the_same_tree(
    worktrees: Callable[[str], Worktree],
) -> None:
    """Failure and Recovery: when the index copy cannot be made, or Git reports an error on the
    way, the capture is taken again in full. It succeeds slowly and never answers from a guess."""

    for name, change in (
        ("an index with unmerged entries", _conflicted_index),
        ("no index file", _no_index_file),
        ("an index file Git cannot read", _unreadable_index),
    ):
        worktree = worktrees(name)
        change(worktree)
        try:
            assert_same_tree_as_before(worktree, fallback=True)
        except AssertionError as error:
            raise AssertionError(f"state {name!r}: {error}") from error

    # Every step of the copy's capture hands over on a Git error, and the tree is the full one's.
    worktree = worktrees("failing steps")
    _edited(worktree)
    expected = previous_capture(worktree.root, worktree.scratch / "old" / "index")
    real = capture_owner.run_git_with_index
    for step in ("read-tree", "ls-files", "add", "write-tree"):
        full = capture_owner._tree_from_head

        def failing(
            repo: Path, args: list[str], index: Path, *, input_text: str | None = None, _step=step
        ) -> subprocess.CompletedProcess[str]:
            if args[0] == _step and index.name == "index":
                return subprocess.CompletedProcess(args, 128, "", f"fatal: {_step} refused")
            return real(repo, args, index, input_text=input_text)

        with (
            mock.patch.object(capture_owner, "run_git_with_index", failing),
            mock.patch.object(capture_owner, "_tree_from_head", wraps=full) as fallback,
        ):
            tree = worktree_candidate_tree(worktree.root, worktree.scratch / "new" / "index")
        assert (tree, fallback.call_count) == (expected, 1), step

    # A copy that cannot be read (the index is locked away) is the same hand-over.
    with (
        mock.patch.object(capture_owner, "copy_git_index", side_effect=PermissionError("denied")),
        mock.patch.object(capture_owner, "_tree_from_head", wraps=full) as fallback,
    ):
        assert worktree_candidate_tree(worktree.root, worktree.scratch / "n" / "index") == expected
    assert fallback.call_count == 1


def test_a_worktree_no_capture_can_read_raises_as_before(repository: Path, tmp_path: Path) -> None:
    """A repository with no commit has no ``HEAD`` to capture over: both captures refuse, and the
    refusal is the full capture's own, so every caller's handling of it is unchanged."""

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    git(unborn, "init", "-q", "-b", "main")
    (unborn / "a.txt").write_text("never committed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="could not seed candidate index") as new:
        worktree_candidate_tree(unborn, tmp_path / "scratch" / "index")
    with pytest.raises(RuntimeError, match="could not seed candidate index") as old:
        previous_capture(unborn, tmp_path / "scratch-old" / "index")
    assert str(new.value) == str(old.value)


def test_the_index_copy_keeps_the_time_git_reads_from_the_index_file(worktree: Worktree) -> None:
    """The copy is made by the one owner that keeps the index file's time, from one open file."""

    index = worktree.index()
    target = worktree.scratch / "copy"
    worktree.scratch.mkdir(parents=True, exist_ok=True)
    git_command.copy_git_index(index, target)
    assert target.read_bytes() == index.read_bytes()
    assert target.stat().st_mtime_ns == index.stat().st_mtime_ns
    copies: list[tuple[Path, Path]] = []
    real = capture_owner.copy_git_index

    def recording(source: Path, destination: Path) -> None:
        copies.append((source, destination))
        real(source, destination)

    with mock.patch.object(capture_owner, "copy_git_index", recording):
        capture(worktree)
    assert [source for source, _ in copies] == [index]
