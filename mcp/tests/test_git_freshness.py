from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.git_freshness import (
    ahead_behind,
    fetch_remote,
    read_branch_freshness,
    upstream_ref,
)


class GitFreshnessTests(unittest.TestCase):
    def test_unavailable_for_non_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            freshness = read_branch_freshness(Path(tmp_dir))
            self.assertIn(freshness.state, {"unavailable", "no-branch"})

    def test_no_upstream_without_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = make_repo(Path(tmp_dir) / "repo")
            freshness = read_branch_freshness(repo)
            self.assertEqual(freshness.state, "no-upstream")
            self.assertIsNone(freshness.upstream)
            self.assertTrue(freshness.branch)

    def test_no_branch_when_detached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = make_repo(Path(tmp_dir) / "repo")
            head = git_output(repo, ["rev-parse", "HEAD"])
            run_git(repo, ["checkout", "--detach", head])
            freshness = read_branch_freshness(repo)
            self.assertEqual(freshness.state, "no-branch")

    def test_current_when_matching_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            clone, _ = make_clone_pair(Path(tmp_dir))
            freshness = read_branch_freshness(clone)
            self.assertEqual(freshness.state, "current")
            self.assertEqual((freshness.ahead, freshness.behind), (0, 0))
            self.assertTrue(freshness.fetched)

    def test_behind_when_remote_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            clone, other = make_clone_pair(Path(tmp_dir))
            commit_file(other, "remote.txt", "remote change")
            run_git(other, ["push", "origin", "HEAD"])
            freshness = read_branch_freshness(clone)
            self.assertEqual(freshness.state, "behind")
            self.assertEqual((freshness.ahead, freshness.behind), (0, 1))

    def test_ahead_when_local_unpushed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            clone, _ = make_clone_pair(Path(tmp_dir))
            commit_file(clone, "local.txt", "local change")
            freshness = read_branch_freshness(clone)
            self.assertEqual(freshness.state, "ahead")
            self.assertEqual((freshness.ahead, freshness.behind), (1, 0))

    def test_diverged_when_both_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            clone, other = make_clone_pair(Path(tmp_dir))
            commit_file(other, "remote.txt", "remote change")
            run_git(other, ["push", "origin", "HEAD"])
            commit_file(clone, "local.txt", "local change")
            freshness = read_branch_freshness(clone)
            self.assertEqual(freshness.state, "diverged")
            self.assertEqual((freshness.ahead, freshness.behind), (1, 1))

    def test_unknown_with_error_when_fetch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            clone, _ = make_clone_pair(Path(tmp_dir))
            missing = (Path(tmp_dir) / "missing-origin.git").as_posix()
            run_git(clone, ["remote", "set-url", "origin", missing])
            freshness = read_branch_freshness(clone)
            self.assertEqual(freshness.state, "unknown")
            self.assertFalse(freshness.fetched)
            self.assertTrue(freshness.error)
            # counts remain computable from the stale tracking ref
            self.assertEqual((freshness.ahead, freshness.behind), (0, 0))

    def test_skipping_fetch_still_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            clone, _ = make_clone_pair(Path(tmp_dir))
            freshness = read_branch_freshness(clone, fetch=False)
            self.assertEqual(freshness.state, "current")
            self.assertFalse(freshness.fetched)

    def test_helper_primitives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            clone, _ = make_clone_pair(Path(tmp_dir))
            branch = git_output(clone, ["branch", "--show-current"])
            self.assertEqual(upstream_ref(clone, branch), f"origin/{branch}")
            self.assertIsNone(upstream_ref(clone, "no-such-branch"))
            self.assertIsNone(fetch_remote(clone, "origin"))
            self.assertEqual(ahead_behind(clone, branch, f"origin/{branch}"), (0, 0))
            self.assertIsNone(ahead_behind(clone, branch, "no-such-ref"))


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, ["init"])
    run_git(path, ["config", "user.email", "agents-remember@example.invalid"])
    run_git(path, ["config", "user.name", "Agents Remember"])
    commit_file(path, "README.md", "# Fixture")
    return path


def make_clone_pair(root: Path) -> tuple[Path, Path]:
    """A bare origin with two clones; returns (clone, other_clone)."""
    seed = make_repo(root / "seed")
    bare = root / "origin.git"
    run_git(root, ["clone", "--bare", str(seed), str(bare)])
    clone = root / "clone"
    other = root / "other"
    run_git(root, ["clone", str(bare), str(clone)])
    run_git(root, ["clone", str(bare), str(other)])
    for repo in (clone, other):
        run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
        run_git(repo, ["config", "user.name", "Agents Remember"])
    return clone, other


def commit_file(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content + "\n", encoding="utf-8")
    run_git(repo, ["add", name])
    run_git(repo, ["commit", "-m", f"add {name}"])


def git_output(repo: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def run_git(repo: Path, args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
