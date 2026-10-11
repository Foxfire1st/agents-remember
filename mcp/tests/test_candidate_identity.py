"""L116-F01 tests for authority-resolved candidate identity.

The identity binds a worktree's HEAD tree, its staged and unstaged tracked
modifications, and its untracked bytes. ``None`` means unproven, never equal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import agents_remember.worktrees.candidate_identity as identity
from agents_remember.worktrees.candidate_identity import resolve_candidate_identity


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)
    return path


def test_clean_worktree_identity_is_stable(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    first = resolve_candidate_identity(repo)
    assert first is not None and first.startswith("candidate-v1:")
    assert resolve_candidate_identity(repo) == first


def test_tracked_modification_changes_the_identity(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    before = resolve_candidate_identity(repo)
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    assert resolve_candidate_identity(repo) != before


def test_staged_modification_changes_the_identity(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    before = resolve_candidate_identity(repo)
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    assert resolve_candidate_identity(repo) != before


def test_untracked_addition_changes_the_identity(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    before = resolve_candidate_identity(repo)
    (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    assert resolve_candidate_identity(repo) != before


def test_untracked_content_change_changes_the_identity(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    (repo / "scratch.txt").write_text("one\n", encoding="utf-8")
    before = resolve_candidate_identity(repo)
    (repo / "scratch.txt").write_text("two\n", encoding="utf-8")
    assert resolve_candidate_identity(repo) != before


def test_non_repository_resolves_to_none(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert resolve_candidate_identity(plain) is None
    assert resolve_candidate_identity(None) is None
    assert resolve_candidate_identity(tmp_path / "missing") is None
    assert resolve_candidate_identity(tmp_path / "plain" / "base.txt") is None


def test_failed_diff_read_propagates_unproven(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path / "repo")
    assert resolve_candidate_identity(repo) is not None
    real_git = identity._git

    def fail_diff(root, *args):
        if args and args[0] == "diff":
            return None
        return real_git(root, *args)

    monkeypatch.setattr(identity, "_git", fail_diff)
    assert resolve_candidate_identity(repo) is None


def test_failed_status_read_propagates_unproven(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path / "repo")
    assert resolve_candidate_identity(repo) is not None
    real_git = identity._git

    def fail_status(root, *args):
        if args and args[0] == "status":
            return None
        return real_git(root, *args)

    monkeypatch.setattr(identity, "_git", fail_status)
    assert resolve_candidate_identity(repo) is None


def test_unreadable_regular_bytes_propagate_unproven(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    locked = repo / "locked.txt"
    locked.write_text("bytes\n", encoding="utf-8")
    assert resolve_candidate_identity(repo) is not None
    locked.chmod(0o000)
    try:
        assert resolve_candidate_identity(repo) is None
    finally:
        locked.chmod(0o644)


def test_failed_head_tree_read_with_real_head_is_unproven(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path / "repo")
    assert resolve_candidate_identity(repo) is not None
    real_git = identity._git

    def fail_tree(root, *args):
        if list(args) == ["rev-parse", "HEAD^{tree}"]:
            return None
        return real_git(root, *args)

    monkeypatch.setattr(identity, "_git", fail_tree)
    assert resolve_candidate_identity(repo) is None


def test_broken_config_under_phrase_named_folder_is_unproven(tmp_path: Path, monkeypatch) -> None:
    """The round-five witness as a test of the task: real git fails the tree
    lookup with a diagnostic whose pathname carries git's phrase, and no state
    is read from that text."""
    repo = _git_repo(tmp_path / "repo")
    assert resolve_candidate_identity(repo) is not None
    config_dir = tmp_path / "unknown revision"
    config_dir.mkdir()
    broken = config_dir / "invalid-global-config"
    broken.write_text("[[[broken\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(broken))
    assert resolve_candidate_identity(repo) is None


def test_unborn_head_is_unproven(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    assert resolve_candidate_identity(repo) is None


def _linked_repo(tmp_path: Path) -> Path:
    repo = _git_repo(tmp_path / "repo")
    (repo / "target-a.txt").write_text("equal-content-bytes\n", encoding="utf-8")
    (repo / "target-b.txt").write_text("equal-content-bytes\n", encoding="utf-8")
    return repo


def test_link_to_equal_content_file_differs_from_file(tmp_path: Path) -> None:
    repo = _linked_repo(tmp_path)
    (repo / "entry").symlink_to("target-a.txt")
    as_link = resolve_candidate_identity(repo)
    assert as_link is not None
    (repo / "entry").unlink()
    (repo / "entry").write_text("equal-content-bytes\n", encoding="utf-8")
    as_file = resolve_candidate_identity(repo)
    assert as_file is not None
    assert as_link != as_file


def test_link_target_change_differs(tmp_path: Path) -> None:
    repo = _linked_repo(tmp_path)
    (repo / "entry").symlink_to("target-a.txt")
    first = resolve_candidate_identity(repo)
    assert first is not None
    (repo / "entry").unlink()
    (repo / "entry").symlink_to("target-b.txt")
    second = resolve_candidate_identity(repo)
    assert second is not None
    assert first != second


def test_executable_bit_change_differs(tmp_path: Path) -> None:
    repo = _linked_repo(tmp_path)
    plain = repo / "entry.txt"
    plain.write_text("equal-content-bytes\n", encoding="utf-8")
    plain.chmod(0o644)
    before = resolve_candidate_identity(repo)
    assert before is not None
    plain.chmod(0o755)
    after = resolve_candidate_identity(repo)
    assert after is not None
    assert before != after


def test_missing_link_target_is_its_own_kind(tmp_path: Path) -> None:
    repo = _linked_repo(tmp_path)
    (repo / "entry").symlink_to("no-such-target.txt")
    first = resolve_candidate_identity(repo)
    assert first is not None
    assert resolve_candidate_identity(repo) == first
    (repo / "entry").unlink()
    (repo / "entry").symlink_to("other-missing.txt")
    assert resolve_candidate_identity(repo) != first
