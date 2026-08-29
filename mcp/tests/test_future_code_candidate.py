"""Mutation-matrix checks for isolated future-code candidate identity."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from agents_remember.errors import FutureCodeCandidateError
from agents_remember.worktrees.closeout_input import capture_closeout_candidate
from agents_remember.worktrees.integration.closeout.future_code_candidate import (
    FutureCodeCandidateIdentity,
    capture_future_code_candidate,
    require_current_future_code_candidate,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from pydantic import ValidationError


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_with_index(repo: Path, index: Path, *args: str) -> str:
    environment = {**os.environ, "GIT_INDEX_FILE": index.as_posix()}
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()


def _candidate_tree(repo: Path, index: Path) -> str:
    _git_with_index(repo, index, "read-tree", "HEAD")
    _git_with_index(repo, index, "add", "-A")
    return _git_with_index(repo, index, "write-tree")


def _initialized_contract(tmp_path: Path) -> WorktreeContract:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "candidate@example.invalid")
    _git(repo, "config", "user.name", "Candidate Test")
    (repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (repo / "renamed.txt").write_text("rename me\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return cast(
        WorktreeContract,
        SimpleNamespace(
            kind="leaf",
            code_worktree=repo,
            worktree_group=tmp_path / "worktree-group",
            code_base_commit=_git(repo, "rev-parse", "HEAD"),
        ),
    )


def test_capture_uses_an_isolated_full_add_all_tree_without_mutating_the_real_index(
    tmp_path: Path,
) -> None:
    contract = _initialized_contract(tmp_path)
    repo = contract.code_worktree
    (repo / "tracked.txt").write_text("staged version\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    index_path = Path(_git(repo, "rev-parse", "--git-path", "index"))
    real_index = index_path if index_path.is_absolute() else repo / index_path
    staged_tree_before = _git(repo, "write-tree")
    real_index_before = real_index.read_bytes()

    (repo / "tracked.txt").write_text("working-tree version\n", encoding="utf-8")
    (repo / "deleted.txt").unlink()
    (repo / "renamed.txt").rename(repo / "renamed-target.txt")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (repo / "ignored.log").write_text("ignore me\n", encoding="utf-8")

    identity = capture_future_code_candidate(contract)
    expected = _candidate_tree(repo, tmp_path / "expected.index")

    assert identity.model_dump() == {
        "observedCodeHead": _git(repo, "rev-parse", "HEAD"),
        "codeBaseCommit": contract.code_base_commit,
        "codeCandidateTree": expected,
    }
    assert identity.codeCandidateTree == expected
    assert real_index.read_bytes() == real_index_before
    assert _git(repo, "write-tree") == staged_tree_before
    assert _git(repo, "show", f"{expected}:tracked.txt") == "working-tree version"
    assert _git(repo, "ls-tree", "-r", "--name-only", expected).splitlines() == [
        ".gitignore",
        "renamed-target.txt",
        "tracked.txt",
        "untracked.txt",
    ]
    assert list((contract.worktree_group / "reports").iterdir()) == []


def test_leaf_closeout_snapshot_consumes_the_exact_future_code_identity(tmp_path: Path) -> None:
    contract = _initialized_contract(tmp_path)
    (contract.code_worktree / "new-candidate.py").write_text("VALUE = 1\n", encoding="utf-8")

    identity = capture_future_code_candidate(contract)
    snapshot = capture_closeout_candidate(contract)

    assert snapshot.candidate_tree == identity.codeCandidateTree
    assert snapshot.head_commit == identity.observedCodeHead
    assert snapshot.head_tree == _git(contract.code_worktree, "rev-parse", "HEAD^{tree}")


def test_concurrent_capture_uses_distinct_temporary_indexes(tmp_path: Path) -> None:
    contract = _initialized_contract(tmp_path)
    (contract.code_worktree / "new-candidate.py").write_text("VALUE = 1\n", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(executor.map(lambda _: capture_future_code_candidate(contract), range(8)))

    assert len(set(identity.codeCandidateTree for identity in identities)) == 1
    assert list((contract.worktree_group / "reports").iterdir()) == []


def test_recomputation_invalidates_changed_candidate_content(tmp_path: Path) -> None:
    contract = _initialized_contract(tmp_path)
    accepted = capture_future_code_candidate(contract)
    (contract.code_worktree / "new-candidate.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(FutureCodeCandidateError) as raised:
        require_current_future_code_candidate(contract, accepted)

    assert raised.value.status == "future-code-candidate-stale"
    assert accepted.codeCandidateTree in str(raised.value)


@pytest.mark.parametrize("field", ["observedCodeHead", "codeBaseCommit"])
def test_recomputation_invalidates_changed_bound_route_identity(
    tmp_path: Path,
    field: str,
) -> None:
    contract = _initialized_contract(tmp_path)
    accepted = capture_future_code_candidate(contract).model_copy(update={field: "f" * 40})

    with pytest.raises(FutureCodeCandidateError) as raised:
        require_current_future_code_candidate(contract, accepted)

    assert raised.value.status == "future-code-candidate-stale"
    assert field in str(raised.value)


def test_capture_refuses_when_head_moves_during_observation(tmp_path: Path) -> None:
    contract = _initialized_contract(tmp_path)
    observed = _git(contract.code_worktree, "rev-parse", "HEAD")
    with (
        mock.patch(
            "agents_remember.worktrees.integration.closeout.future_code_candidate.head_commit",
            side_effect=[observed, "f" * 40],
        ),
        mock.patch(
            "agents_remember.worktrees.integration.closeout.future_code_candidate."
            "worktree_candidate_tree",
            return_value="e" * 40,
        ),
        pytest.raises(FutureCodeCandidateError) as raised,
    ):
        capture_future_code_candidate(contract)

    assert raised.value.status == "future-code-candidate-head-moved"


def test_capture_translates_candidate_tree_failures_to_one_public_status(tmp_path: Path) -> None:
    contract = _initialized_contract(tmp_path)
    with (
        mock.patch(
            "agents_remember.worktrees.integration.closeout.future_code_candidate."
            "worktree_candidate_tree",
            side_effect=OSError("isolated index unavailable"),
        ),
        pytest.raises(FutureCodeCandidateError) as raised,
    ):
        capture_future_code_candidate(contract)

    assert raised.value.status == "future-code-candidate-unavailable"
    assert "isolated index unavailable" in str(raised.value)


def test_current_candidate_returns_the_recomputed_exact_identity(tmp_path: Path) -> None:
    contract = _initialized_contract(tmp_path)
    accepted = capture_future_code_candidate(contract)

    with mock.patch(
        "agents_remember.worktrees.integration.closeout.future_code_candidate."
        "capture_future_code_candidate",
        return_value=accepted,
    ):
        assert require_current_future_code_candidate(contract, accepted) is accepted


def test_future_code_capture_refuses_the_existing_commit_route() -> None:
    direct_landing = cast(WorktreeContract, SimpleNamespace(kind="series"))

    with pytest.raises(FutureCodeCandidateError) as raised:
        capture_future_code_candidate(direct_landing)

    assert raised.value.status == "future-code-candidate-not-applicable"


def test_future_code_schema_forbids_missing_identity_fields() -> None:
    with pytest.raises(ValidationError):
        FutureCodeCandidateIdentity.model_validate(
            {
                "observedCodeHead": "a" * 40,
                "codeCandidateTree": "c" * 40,
            }
        )


def test_future_code_schema_forbids_caller_extra_identity_fields() -> None:
    with pytest.raises(ValidationError):
        FutureCodeCandidateIdentity.model_validate(
            {
                "observedCodeHead": "a" * 40,
                "codeBaseCommit": "b" * 40,
                "codeCandidateTree": "c" * 40,
                "callerSuppliedTree": "c" * 40,
            }
        )


def test_future_code_identity_is_immutable() -> None:
    identity = FutureCodeCandidateIdentity(
        observedCodeHead="a" * 40,
        codeBaseCommit="b" * 40,
        codeCandidateTree="c" * 40,
    )

    with pytest.raises(ValidationError):
        identity.codeCandidateTree = "d" * 40
