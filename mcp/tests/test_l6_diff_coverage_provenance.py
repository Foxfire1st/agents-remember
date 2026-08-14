"""L6 closeout coverage tests for citation provenance helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.memory_quality.style.citations import provenance
from agents_remember.memory_quality.style.citations.provenance import (
    GitHistory,
    Read,
    ecosystem_from_path,
    manifest_error,
    normalised_package,
    package_candidate_for,
    package_from_path,
    package_lock_versions,
    requirement_candidate_for,
    requirement_versions,
)


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", repo.as_posix(), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "agents-remember@example.invalid")
    git(root, "config", "user.name", "Agents Remember")
    (root / "mcp").mkdir()
    (root / "mcp" / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "-m", "base")
    return root


class TestGitHistory:
    def test_commit_success(self, repo: Path) -> None:
        history = GitHistory(repo, "code")
        stamp = git(repo, "rev-parse", "HEAD")
        read = history.commit(stamp)
        assert read.text == stamp and read.error is None

    def test_commit_rev_parse_failure(self, repo: Path) -> None:
        history = GitHistory(repo, "code")
        read = history.commit("deadbeef" * 5)
        assert read.text is None and read.error is not None

    def test_commit_not_reachable(self, repo: Path) -> None:
        history = GitHistory(repo, "code")
        # create a commit on a detached orphan that is not reachable from HEAD
        orphan = repo / "orphan"
        orphan.mkdir()
        (orphan / "x.txt").write_text("x", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "--quiet", "-m", "orphan")
        stamp = git(repo, "rev-parse", "HEAD")
        git(repo, "reset", "--hard", "HEAD~1")
        read = history.commit(stamp)
        assert read.text is None and "not reachable" in (read.error or "")

    def test_file_success_and_missing(self, repo: Path) -> None:
        history = GitHistory(repo, "code")
        stamp = git(repo, "rev-parse", "HEAD")
        read = history.file(stamp, "mcp/requirements.txt")
        assert read.text is not None and read.error is None
        read = history.file(stamp, "missing.txt")
        assert read.text is None and read.error is not None


class TestRequirements:
    def test_requirement_candidate(self) -> None:
        candidate, permissive, error = requirement_candidate_for(
            "fastapi", Read("fastapi==0.115.0\n")
        )
        assert candidate is not None and candidate.version == "0.115.0"
        assert permissive is False and error is None
        candidate, permissive, error = requirement_candidate_for("missing", Read(""))
        assert candidate is None and error is None
        candidate, permissive, error = requirement_candidate_for("x", Read(None, "boom"))
        assert candidate is None and error == "boom"
        candidate, permissive, error = requirement_candidate_for("x", Read(None, "did not exist"))
        assert candidate is None and error is None

    def test_requirement_versions(self) -> None:
        text = "\n".join(
            [
                "fastapi==0.115.0",
                "requests>=2",
                "bad!!name",
                "-e .",
                "http://example.invalid/x",
                "pkg@ git+https://example.invalid/pkg",
                "star==1.*",
                "dup==1.0",
                "dup==2.0",
                "https://example.invalid/y",
            ]
        )
        exact, permissive = requirement_versions(text)
        assert exact.get("fastapi") == "0.115.0"
        assert {"requests", "bad", "pkg", "star", "dup"} <= permissive

    def test_package_candidate_and_lock(self) -> None:
        lock = (
            '{"packages": {"": {}, "node_modules/react": {"version": "18.0.0"}, '
            '"node_modules/@scope/pkg": {"version": "1.0.0"}, '
            '"node_modules/a/node_modules/b": {"version": "2.0.0"}, '
            '"node_modules/no-version": {}, "node_modules/deep": "x"}}'
        )
        versions = package_lock_versions(lock)
        assert versions == {"react": "18.0.0", "@scope/pkg": "1.0.0"}
        candidate, error = package_candidate_for("react", Read(lock))
        assert candidate is not None and error is None
        candidate, error = package_candidate_for("missing", Read(lock))
        assert candidate is None and error is None
        candidate, error = package_candidate_for("x", Read("not json"))
        assert candidate is None and error is not None
        candidate, error = package_candidate_for("x", Read(None, "did not exist"))
        assert candidate is None and error is None

    def test_package_lock_bad_packages(self) -> None:
        with pytest.raises(ValueError, match="packages must be an object"):
            package_lock_versions('{"packages": []}')
        candidate, error = package_candidate_for("x", Read('{"packages": []}'))
        assert candidate is None and error is not None

    def test_package_from_path(self) -> None:
        assert package_from_path("@scope/pkg/file.js") == "@scope/pkg"
        assert package_from_path("plain/file.js") == "plain"

    def test_ecosystem_and_normalised(self) -> None:
        assert ecosystem_from_path("a/b.py") == "python"
        assert ecosystem_from_path("a/b.tsx") == "npm"
        assert ecosystem_from_path("package.json") == "npm"
        assert ecosystem_from_path("a/b.txt") is None
        assert normalised_package("My_Pkg.Name") == "my-pkg-name"

    def test_manifest_error(self) -> None:
        assert manifest_error(Read(None, "did not exist")) is None
        assert manifest_error(Read(None, "boom")) == "boom"
        assert manifest_error(Read("text")) is None


class TestHistoriesVersions:
    def test_locked_version_python_and_npm(self, repo: Path) -> None:
        histories = provenance.Histories(repo, repo)
        stamp = git(repo, "rev-parse", "HEAD")
        read = histories._locked_version("fastapi", "python", stamp)
        assert read.version is not None and read.version.version == "0.115.0"
        read = histories._locked_version("requests", "python", stamp)
        assert read.version is None and read.error is not None
        read = histories._locked_version("anything", "ruby", stamp)
        assert read.error is not None and "unsupported" in read.error

    def test_locked_version_working_tree_and_errors(self, repo: Path) -> None:
        histories = provenance.Histories(repo, repo)
        read = histories._locked_version("fastapi", "python", None)
        assert read.version is not None
        (repo / "mcp" / "requirements.txt").unlink()
        read = histories._locked_version("fastapi", "python", None)
        assert read.error is not None and "no resolved python version" in read.error
        (repo / "mcp" / "requirements.txt").write_text("[[[", encoding="utf-8")
        read = histories._locked_version("fastapi", "python", None)
        assert read.error is not None
