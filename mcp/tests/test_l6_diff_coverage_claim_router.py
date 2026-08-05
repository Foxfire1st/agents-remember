"""L6 closeout coverage tests for the claim-change router."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import create_initial_ledger, ledger_to_text
from agents_remember.memory_quality.style.citations import (
    claim_change_router,
    model,
    provenance,
)
from agents_remember.memory_quality.style.citations.claim_change_router import (
    ClaimChangeRouter,
    RepositoryChanges,
    classify_citation,
    partition_citations,
)
from agents_remember.memory_quality.style.citations.resolution import Trees


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


class RepoPair:
    def __init__(self, root: Path) -> None:
        self.code = root / "code"
        self.memory = root / "memory"
        for repository in (self.code, self.memory):
            repository.mkdir(parents=True)
            git(repository, "init", "--quiet")
            git(repository, "config", "user.email", "agents-remember@example.invalid")
            git(repository, "config", "user.name", "Agents Remember")

    def write(self, root: Path, relative: str, body: str) -> Path:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def code_file(self, relative: str, body: str) -> Path:
        return self.write(self.code, relative, body)

    def memory_file(self, relative: str, body: str) -> Path:
        return self.write(self.memory, relative, body)

    def commit(self, repository: Path, message: str) -> str:
        git(repository, "add", "-A")
        git(repository, "commit", "--quiet", "-m", message)
        return git(repository, "rev-parse", "HEAD")

    def map_memory(self, code_commit: str, memory_commit: str) -> None:
        ledger = create_initial_ledger("agents-remember", code_commit, memory_commit)
        self.memory_file("memory.md", ledger_to_text(ledger))
        self.commit(self.memory, "record ledger")


def citation(path: str) -> model.Citation:
    return model.Citation(text=f"{path}:1-1", path=path, start=1, end=1)


class TestNulAndStatusParsers:
    def test_nul_fields(self) -> None:
        assert claim_change_router._nul_fields("a\0b\0") == ["a", "b"]
        with pytest.raises(ValueError, match="empty NUL-delimited field"):
            claim_change_router._nul_fields("a\0\0b")

    def test_status_paths(self) -> None:
        raw = " M src/a.py\0R  src/old.py\0src/new.py\0"
        paths = claim_change_router._status_paths(raw)
        assert paths == {"src/a.py", "src/old.py", "src/new.py"}
        with pytest.raises(ValueError, match="malformed porcelain record"):
            claim_change_router._status_paths("xx\0")
        with pytest.raises(ValueError, match="rename/copy record has no source path"):
            claim_change_router._status_paths("R  dst.py\0")

    def test_name_status_paths(self) -> None:
        raw = "M\0src/a.py\0R100\0src/old.py\0src/new.py\0"
        paths = claim_change_router._name_status_paths(raw)
        assert paths == {"src/a.py", "src/old.py", "src/new.py"}
        with pytest.raises(ValueError, match="has no path"):
            claim_change_router._name_status_paths("M\0")
        with pytest.raises(ValueError, match="has no second path"):
            claim_change_router._name_status_paths("M\0a.py\0R100\0b.py\0")


class TestClassifyAndPartition:
    def test_classify_resolved_code_and_memory(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        pair.code_file("pkg/a.py", "x = 1\n")
        pair.memory_file("onboarding/p.md", "# p\n")
        trees = Trees(pair.code, pair.memory)
        local, error = classify_citation(trees, citation("pkg/a.py"))
        assert error is None and local is not None and local.repository == "code"
        local, error = classify_citation(trees, citation("onboarding/p.md"))
        assert error is None and local is not None and local.repository == "memory"

    def test_classify_absent_top_level_ownership(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        (pair.code / "codeonly").mkdir()
        (pair.memory / "memonly").mkdir()
        (pair.code / "both").mkdir()
        (pair.memory / "both").mkdir()
        trees = Trees(pair.code, pair.memory)
        local, error = classify_citation(trees, citation("codeonly/gone.py"))
        assert local is not None and local.repository == "code"
        local, error = classify_citation(trees, citation("memonly/gone.py"))
        assert local is not None and local.repository == "memory"
        local, error = classify_citation(trees, citation("both/gone.py"))
        assert local is None and error is not None and "ambiguous" in error
        local, error = classify_citation(trees, citation("dep/gone.py"))
        assert local is None and error is None

    def test_partition_citations(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        pair.code_file("pkg/a.py", "x = 1\n")
        trees = Trees(pair.code, pair.memory)
        partition = partition_citations(trees, (citation("pkg/a.py"), citation("dep/gone.py")))
        assert len(partition.local) == 1 and len(partition.dependencies) == 1
        (pair.code / "both").mkdir()
        (pair.memory / "both").mkdir()
        bad = partition_citations(trees, (citation("both/gone.py"),))
        assert bad.error is not None


class TestRepositoryChanges:
    def test_route_states(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        pair.code_file("pkg/a.py", "x = 1\n")
        first = pair.commit(pair.code, "first")
        pair.code_file("pkg/a.py", "x = 2\n")
        second = pair.commit(pair.code, "second")
        changes = RepositoryChanges(pair.code, "code")
        # unchanged relative to first and HEAD (second) history: a.py changed between commits
        assert changes.route(first, "pkg/a.py") == (False, None)
        changes = RepositoryChanges(pair.code, "code")
        assert changes.route(second, "pkg/a.py") == (True, None)
        pair.code_file("pkg/a.py", "x = 3\n")
        assert RepositoryChanges(pair.code, "code").route(second, "pkg/a.py") == (False, None)
        changes = RepositoryChanges(pair.code, "code")
        assert changes.route(second, "missing.py") == (False, None)

    def test_route_git_failures(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        pair.code_file("pkg/a.py", "x = 1\n")
        pair.commit(pair.code, "first")
        failed = subprocess.CompletedProcess(["git"], 128, stdout="", stderr="boom")
        with mock.patch.object(claim_change_router, "run_git", return_value=failed) as run:
            changes = RepositoryChanges(pair.code, "code")
            ok, error = changes.route("first", "pkg/a.py")
            assert ok is False and error is not None
            run.assert_called()

    def test_historical_diff_failure(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        pair.code_file("pkg/a.py", "x = 1\n")
        pair.commit(pair.code, "first")
        real = claim_change_router.run_git

        def fake_run_git(root: Path, args: list[str], **kwargs):
            if args and args[0] == "diff-tree":
                return subprocess.CompletedProcess([], 128, stdout="", stderr="boom")
            return real(root, args, **kwargs)

        with mock.patch.object(claim_change_router, "run_git", side_effect=fake_run_git):
            changes = RepositoryChanges(pair.code, "code")
            ok, error = changes.route("first", "pkg/a.py")
            assert ok is False and error is not None


class TestClaimChangeRouterRoutes:
    def test_proven_unchanged_and_semantic_required(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        pair.code_file("pkg/a.py", "x = 1\n")
        code_commit = pair.commit(pair.code, "code baseline")
        pair.memory_file("memory.md", "")
        memory_commit = pair.commit(pair.memory, "memory baseline")
        pair.map_memory(code_commit, memory_commit)
        trees = Trees(pair.code, pair.memory)
        histories = provenance.Histories(pair.code, pair.memory)
        router = ClaimChangeRouter(trees, histories)
        route = router.route_claim((citation("pkg/a.py"),), code_commit)
        assert route.status == "proven-unchanged"
        pair.code_file("pkg/a.py", "x = 2\n")
        route = ClaimChangeRouter(trees, histories).route_claim(
            (citation("pkg/a.py"),), code_commit
        )
        assert route.status == "semantic-required"

    def test_memory_mapping_missing(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        pair.code_file("pkg/a.py", "x = 1\n")
        code_commit = pair.commit(pair.code, "code baseline")
        pair.memory_file("onboarding/p.md", "# p\n")
        pair.memory_file("memory.md", "")
        pair.commit(pair.memory, "memory baseline")
        trees = Trees(pair.code, pair.memory)
        histories = provenance.Histories(pair.code, pair.memory)
        router = ClaimChangeRouter(trees, histories)
        route = router.route_claim((citation("onboarding/p.md"),), code_commit)
        assert route.status == "error" and route.error is not None

    def test_ambiguous_classify_error_route(self, tmp_path: Path) -> None:
        pair = RepoPair(tmp_path)
        (pair.code / "both").mkdir()
        (pair.memory / "both").mkdir()
        trees = Trees(pair.code, pair.memory)
        histories = provenance.Histories(pair.code, pair.memory)
        router = ClaimChangeRouter(trees, histories)
        route = router.route_claim((citation("both/gone.py"),), "a" * 40)
        assert route.status == "error" and "ambiguous" in (route.error or "")
