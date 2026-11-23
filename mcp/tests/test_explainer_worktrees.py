"""F9: native detached worktrees, exact trees, and confined Git-owned removal.

Draft for the parent to apply alongside the checkout owner. These seven cases replace
snapshot batching/read-only/old-generation cases rather than adding parallel coverage.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO

import pytest
from agents_remember.canvas import explainer_target
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.canvas.explainer_target import ExplainerTargetReads
from agents_remember.canvas.explainer_worktrees import checkout_tree, remove_checkout
from agents_remember.canvas.paths import PathRefused
from agents_remember.kernel import git_command
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.read_cancellation import ReadCancelled, cancellable_read
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)

pytestmark = pytest.mark.evidence_unit


def _git(repository: Path, *arguments: str) -> str:
    result = run_git(repository, list(arguments))
    result.check_returncode()
    return result.stdout.strip()


def _repository(base: Path) -> Path:
    repository = base / "source"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "test")
    (repository / "ordinary.txt").write_text("ordinary\n", encoding="utf-8")
    executable = repository / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (repository / 'tab\tquote"café.txt').write_text("unusual\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", "one")
    return repository


def _destination(base: Path) -> tuple[Path, Path]:
    root = base / "environment"
    root.mkdir()
    return root, root / "trees" / "code"


def _comparison_reads(repository: Path, tree: str) -> ExplainerTargetReads:
    side = ReviewTreeSide(repository=repository.as_posix(), tree=tree)
    record = ReviewTreeComparisonRecord(
        task_id="TEST",
        leaf_id="TEST-L1",
        number=1,
        code_base=side,
        code_candidate=side,
        memory_base=side,
        memory_candidate=side,
        recorded_at="2026-01-01T00:00:00Z",
    )
    binding = FrameBinding.from_record(
        record, comparison_owner="recorded", comparison_reference="review-comparison:TEST/TEST-L1/1"
    )
    return ExplainerTargetReads(binding=binding)


@pytest.fixture
def comparison_spools(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[BinaryIO]]:
    spools: list[BinaryIO] = []

    with ExitStack() as cleanup:

        def create() -> BinaryIO:
            spool = cleanup.enter_context(TemporaryFile())
            spools.append(spool)
            metadata = os.fstat(spool.fileno())
            assert stat.S_IMODE(metadata.st_mode) == 0o600
            assert metadata.st_nlink == 0
            return spool

        monkeypatch.setattr(explainer_target, "TemporaryFile", create, raising=False)
        yield spools


def test_comparison_blob_spools_exact_bytes_and_memoizes_success_and_absence(
    tmp_path: Path, comparison_spools: list[BinaryIO]
) -> None:
    repository = _repository(tmp_path)
    binary = b"\r\nfirst\x00\xff\rsecond\n"
    (repository / "binary").write_bytes(binary)
    (repository / "empty").touch()
    _git(repository, "add", "binary", "empty")
    tree = _git(repository, "write-tree")
    reads = _comparison_reads(repository, tree)
    invalid_repo = tmp_path / "not-a-repository"
    invalid_repo.mkdir()

    assert reads._blob(repository, tree, "binary") == binary
    assert reads._blob(repository, tree, "empty") == b""
    assert reads._blob(repository, tree, "missing") is None
    assert reads._blob(invalid_repo, tree, "binary") is None

    assert reads._blob(repository, tree, "binary") == binary
    assert reads._blob(repository, tree, "missing") is None
    assert len(comparison_spools) == 4
    assert all(spool.closed for spool in comparison_spools)


class _ChildStarted:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def is_set(self) -> bool:
        return self.marker.exists()


def test_comparison_blob_closes_its_spool_on_git_launch_error_timeout_and_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    comparison_spools: list[BinaryIO],
    subtests: pytest.Subtests,
) -> None:
    repository = _repository(tmp_path)
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    reads = _comparison_reads(repository, tree)
    missing_executable = tmp_path / "git-missing"
    started = tmp_path / "child-started"
    blocking_process = [
        sys.executable,
        "-c",
        "import os, pathlib, sys, threading\n"
        "os.write(2, b'git stderr\\n')\n"
        "pathlib.Path(sys.argv[1]).touch()\n"
        "threading.Event().wait(30)",
        str(started),
    ]

    with subtests.test(outcome="launch-error"):
        monkeypatch.setattr(git_command, "_git_argv", lambda *_: [str(missing_executable)])
        with pytest.raises(FileNotFoundError) as launch_error:
            reads._blob(repository, tree, "ordinary.txt")
        assert launch_error.value.filename == str(missing_executable)
        assert len(comparison_spools) == 1 and comparison_spools[-1].closed

    with subtests.test(outcome="timeout"):
        monkeypatch.setattr(git_command, "_git_argv", lambda *_: blocking_process)
        monkeypatch.setattr(explainer_target, "GIT_METADATA_TIMEOUT_SECONDS", 2)
        with pytest.raises(subprocess.TimeoutExpired) as timeout:
            reads._blob(repository, tree, "ordinary.txt")
        assert timeout.value.cmd == blocking_process
        assert timeout.value.timeout == 2
        assert timeout.value.stderr == b"git stderr\n"
        assert len(comparison_spools) == 2 and comparison_spools[-1].closed

    with subtests.test(outcome="read-cancellation"):
        started.unlink(missing_ok=True)
        monkeypatch.setattr(explainer_target, "GIT_METADATA_TIMEOUT_SECONDS", 5)
        with cancellable_read(_ChildStarted(started)), pytest.raises(ReadCancelled):
            reads._blob(repository, tree, "ordinary.txt")
        assert started.exists()
        assert len(comparison_spools) == 3 and comparison_spools[-1].closed


def test_commit_checkout_is_detached_exact_and_ordinary_writable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    commit = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    root, destination = _destination(tmp_path)

    totals = checkout_tree(repository, commit, destination, root=root)

    assert totals.commit == commit
    assert totals.files == 3
    assert totals.bytes == len(b"ordinary\n#!/bin/sh\nexit 0\nunusual\n")
    assert _git(destination, "rev-parse", "HEAD^{tree}") == tree
    assert run_git(destination, ["symbolic-ref", "-q", "HEAD"]).returncode != 0
    assert str(destination) in _git(repository, "worktree", "list", "--porcelain")
    assert (destination / "ordinary.txt").stat().st_mode & 0o200
    assert (destination / "run.sh").stat().st_mode & 0o111
    assert (destination / 'tab\tquote"café.txt').read_text(encoding="utf-8") == "unusual\n"
    (destination / "ordinary.txt").write_text("normal writable checkout\n", encoding="utf-8")
    assert _git(repository, "rev-parse", "HEAD") == commit
    assert _git(repository, "status", "--porcelain") == ""
    remove_checkout(destination, root=root)


def test_tree_only_checkout_creates_no_branch_and_needs_no_user_identity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    original_head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "config", "--unset", "user.name")
    _git(repository, "config", "--unset", "user.email")
    (repository / "ordinary.txt").write_text("tree without commit\n", encoding="utf-8")
    _git(repository, "add", "-A")
    tree = _git(repository, "write-tree")
    refs = _git(repository, "show-ref")
    index = (repository / ".git" / "index").read_bytes()
    root, destination = _destination(tmp_path)

    totals = checkout_tree(repository, tree, destination, root=root)

    assert _git(repository, "cat-file", "-t", totals.commit) == "commit"
    assert _git(destination, "rev-parse", "HEAD^{tree}") == tree
    assert (destination / "ordinary.txt").read_text(encoding="utf-8") == "tree without commit\n"
    assert _git(repository, "show-ref") == refs
    assert _git(repository, "rev-parse", "HEAD") == original_head
    assert (repository / ".git" / "index").read_bytes() == index
    remove_checkout(destination, root=root)


def test_update_reuses_the_registered_worktree_and_replaces_dirty_tracked_files(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    root, destination = _destination(tmp_path)
    checkout_tree(repository, _git(repository, "rev-parse", "HEAD"), destination, root=root)
    git_file = (destination / ".git").read_bytes()
    registry = _git(repository, "worktree", "list", "--porcelain")
    (destination / "ordinary.txt").write_text("local change\n", encoding="utf-8")
    (repository / "ordinary.txt").write_text("new bound content\n", encoding="utf-8")
    _git(repository, "rm", "--", 'tab\tquote"café.txt')
    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", "two")
    target = _git(repository, "rev-parse", "HEAD")

    result = checkout_tree(repository, target, destination, root=root)

    assert result.commit == target
    assert (destination / ".git").read_bytes() == git_file
    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == registry.count(
        "worktree "
    )
    assert (destination / "ordinary.txt").read_text(encoding="utf-8") == "new bound content\n"
    assert not (destination / 'tab\tquote"café.txt').exists()
    assert _git(destination, "rev-parse", "HEAD") == target
    remove_checkout(destination, root=root)


def test_removing_checkout_unlinks_tracked_symlink_without_touching_its_target(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    (repository / "outside-link").symlink_to(outside, target_is_directory=True)
    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", "tracked symlink")
    root, destination = _destination(tmp_path)
    checkout_tree(repository, _git(repository, "rev-parse", "HEAD"), destination, root=root)
    assert (destination / "outside-link").is_symlink()
    other = tmp_path / "unrelated-worktree"
    _git(repository, "worktree", "add", "--detach", str(other), "HEAD")

    remove_checkout(destination, root=root)

    assert not destination.exists()
    registered = _git(repository, "worktree", "list", "--porcelain")
    assert str(destination) not in registered
    assert str(other) in registered
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert (repository / "ordinary.txt").is_file()
    _git(repository, "worktree", "remove", "--force", str(other))


def test_missing_object_refuses_without_new_checkout_or_registration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    root, destination = _destination(tmp_path)
    before = _git(repository, "worktree", "list", "--porcelain")

    with pytest.raises(ExplainerRefused):
        checkout_tree(repository, "0" * 40, destination, root=root)

    assert not destination.exists()
    assert _git(repository, "worktree", "list", "--porcelain") == before


def test_destination_escape_or_ancestor_link_refuses_checkout_and_remove(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    commit = _git(repository, "rev-parse", "HEAD")
    root, destination = _destination(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    (root / "trees").symlink_to(outside, target_is_directory=True)
    before = _git(repository, "worktree", "list", "--porcelain")

    for target in (outside, destination):
        with pytest.raises(PathRefused):
            checkout_tree(repository, commit, target, root=root)
        with pytest.raises(PathRefused):
            remove_checkout(target, root=root)

    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (outside / "code").exists()
    assert _git(repository, "worktree", "list", "--porcelain") == before


def test_replaced_git_metadata_link_is_refused_before_checkout_or_removal(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    root, destination = _destination(tmp_path)
    commit = _git(repository, "rev-parse", "HEAD")
    checkout_tree(repository, commit, destination, root=root)
    git_file = destination / ".git"
    original = git_file.read_bytes()
    foreign = tmp_path / "foreign-git-file"
    foreign.write_bytes(original)
    git_file.unlink()
    git_file.symlink_to(foreign)

    try:
        with pytest.raises(PathRefused):
            checkout_tree(repository, commit, destination, root=root)
        with pytest.raises(PathRefused):
            remove_checkout(destination, root=root)
        assert foreign.read_bytes() == original
        assert (destination / "ordinary.txt").read_text(encoding="utf-8") == "ordinary\n"
    finally:
        git_file.unlink()
        git_file.write_bytes(original)
        remove_checkout(destination, root=root)
