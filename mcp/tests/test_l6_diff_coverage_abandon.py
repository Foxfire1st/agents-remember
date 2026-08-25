"""L6 closeout coverage tests for worktree abandon helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations import source_index_cache as citation_cache
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules import abandon
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    write_contract,
)


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _args(**over: object) -> WorktreeArgs:
    base = {"contract_path": Path("/c/series-contract.md"), "dry_run": False, "force": False}
    base.update(over)
    return WorktreeArgs(**base)


def _contract(**over: object) -> WorktreeContract:
    base = {
        "code_repo_path": Path("/repo"),
        "code_work_branch": "ar/leaf",
        "code_source_branch": "ar/base",
        "memory_mode": "external",
        "memory_repo_path": Path("/mem"),
        "memory_work_branch": "ar/leaf-mem",
        "memory_source_branch": "ar/base-mem",
    }
    base.update(over)
    return cast(WorktreeContract, SimpleNamespace(**base))


def _abandon_authority(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "terminal-tests@example.invalid"),
        ("config", "user.name", "Terminal Tests"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "branch", "b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", head], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo,
        check=True,
    )
    contract = default_contract(
        ContractTask("terminal", "repo", tmp_path / "coordination", "light-task", "disabled"),
        leaf=LeafIdentity("terminal", leaf_id="T"),
        code=RepoBranchPlan(repo, "main", "b", head),
    )
    write_contract(contract.contract_path, contract)
    publish_new_lifecycle_operation_location(
        contract,
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    return (
        repo,
        abandon._terminal_mutation_authority(contract, operation="worktree_abandon"),
        contract,
    )


class TestAbandonReserved:
    def test_guard_enter_raises(self) -> None:
        ctx = mock.MagicMock()
        ctx.__enter__.side_effect = CitationCacheError("live lease busy")
        with (
            mock.patch.object(citation_cache, "terminal_namespace_guard", return_value=ctx),
            mock.patch.object(abandon, "status_payload", return_value={}),
        ):
            result = abandon._abandon_reserved(_args(), _contract(), TerminalPreflight({}, {}, ()))
        assert result.returncode == 2
        payload = result.payload
        assert payload["state"] == "abandon-blocked"
        citation_index = payload["citation_source_index"]
        assert isinstance(citation_index, dict)
        assert citation_index.get("reason") == "live-lease-timeout"

    def test_guard_enter_other_error(self) -> None:
        ctx = mock.MagicMock()
        ctx.__enter__.side_effect = CitationCacheError("other")
        with (
            mock.patch.object(citation_cache, "terminal_namespace_guard", return_value=ctx),
            mock.patch.object(abandon, "status_payload", return_value={}),
        ):
            result = abandon._abandon_reserved(_args(), _contract(), TerminalPreflight({}, {}, ()))
        citation_index = result.payload["citation_source_index"]
        assert isinstance(citation_index, dict)
        assert citation_index.get("reason") == "other"


class TestAbandonWithGuard:
    def test_helper_failure(self, tmp_path: Path) -> None:
        _, _, contract = _abandon_authority(tmp_path)
        guard = cast(TerminalNamespaceGuard, SimpleNamespace(preview=lambda: {"removed": False}))
        with (
            mock.patch.object(
                abandon, "_abandon_terminal_outputs", side_effect=RuntimeError("boom")
            ),
            mock.patch.object(abandon, "status_payload", return_value={}),
        ):
            result = abandon._abandon_with_guard(
                _args(contract_path=contract.contract_path),
                contract,
                TerminalPreflight({}, {}, ()),
                guard,
            )
        assert result.returncode == 2
        assert "boom" in str(result.payload["blockers"])

    def test_mutation_blocked(self, tmp_path: Path) -> None:
        _, _, contract = _abandon_authority(tmp_path)
        guard = cast(TerminalNamespaceGuard, SimpleNamespace(preview=lambda: {"removed": False}))
        outputs = ({}, {}, {}, {})
        with (
            mock.patch.object(abandon, "_abandon_terminal_outputs", return_value=outputs),
            mock.patch.object(
                abandon, "terminal_result_blockers", return_value=[{"reason": "busy"}]
            ),
            mock.patch.object(abandon, "status_payload", return_value={}),
        ):
            result = abandon._abandon_with_guard(
                _args(contract_path=contract.contract_path),
                contract,
                TerminalPreflight({}, {}, ()),
                guard,
            )
        assert result.returncode == 2 and result.payload["state"] == "abandon-blocked"

    def test_dry_run(self, tmp_path: Path) -> None:
        _, _, contract = _abandon_authority(tmp_path)
        guard = cast(
            TerminalNamespaceGuard,
            SimpleNamespace(preview=lambda: {"removed": False, "reason": "live"}),
        )
        outputs = ({"p": {}}, {"w": {}}, {"b": {}}, {"d": {}})
        with (
            mock.patch.object(abandon, "_abandon_terminal_outputs", return_value=outputs),
            mock.patch.object(abandon, "terminal_result_blockers", return_value=[]),
            mock.patch.object(abandon, "status_payload", return_value={}),
            mock.patch.object(abandon, "_abandon_summary", return_value="summary"),
        ):
            result = abandon._abandon_with_guard(
                _args(contract_path=contract.contract_path, dry_run=True),
                contract,
                TerminalPreflight({}, {}, ()),
                guard,
            )
        assert result.returncode == 0 and result.payload["state"] == "would-abandon"

    def test_publish(self, tmp_path: Path) -> None:
        _, _, contract = _abandon_authority(tmp_path)
        guard = cast(TerminalNamespaceGuard, SimpleNamespace(preview=lambda: {"removed": True}))
        outputs = ({}, {}, {}, {})
        with (
            mock.patch.object(abandon, "_abandon_terminal_outputs", return_value=outputs),
            mock.patch.object(abandon, "terminal_result_blockers", return_value=[]),
            mock.patch.object(
                abandon,
                "_publish_abandon",
                return_value=SimpleNamespace(returncode=0, payload={"state": "abandoned"}),
            ) as publish,
        ):
            result = abandon._abandon_with_guard(
                _args(contract_path=contract.contract_path),
                contract,
                TerminalPreflight({}, {}, ()),
                guard,
            )
        assert result.returncode == 0 and publish.called


class TestAbandonBranch:
    def test_branch_refusals_and_unmerged(self, tmp_path: Path) -> None:
        repo, authority, _ = _abandon_authority(tmp_path)
        target = abandon._AbandonBranchTarget(repo, "b", "main")
        with mock.patch.object(
            abandon,
            "local_branch_presence",
            return_value=SimpleNamespace(state="absent", reason=""),
        ):
            result = abandon._abandon_branch(
                target, dry_run=False, force=False, authority=authority
            )
            assert result["reason"] == "base branch is missing: main"
        with mock.patch.object(
            abandon,
            "local_branch_presence",
            side_effect=[
                SimpleNamespace(state="present", reason=""),
                SimpleNamespace(state="absent", reason=""),
            ],
        ):
            result = abandon._abandon_branch(
                target, dry_run=False, force=False, authority=authority
            )
            assert result["reason"] == "already-absent"
        with (
            mock.patch.object(
                abandon,
                "local_branch_presence",
                return_value=SimpleNamespace(state="present", reason=""),
            ),
            mock.patch.object(abandon, "delete_branch_force", return_value={"deleted": True}),
        ):
            result = abandon._abandon_branch(target, dry_run=False, force=True, authority=authority)
            assert result["deleted"] is True
        with (
            mock.patch.object(
                abandon,
                "local_branch_presence",
                return_value=SimpleNamespace(state="present", reason=""),
            ),
            mock.patch.object(abandon, "_unmerged_commits", side_effect=RuntimeError("boom")),
        ):
            result = abandon._abandon_branch(
                target, dry_run=False, force=False, authority=authority
            )
            assert result["reason"] == "boom"
        with (
            mock.patch.object(
                abandon,
                "local_branch_presence",
                return_value=SimpleNamespace(state="present", reason=""),
            ),
            mock.patch.object(abandon, "_unmerged_commits", return_value=["abc"]),
        ):
            result = abandon._abandon_branch(
                target, dry_run=False, force=False, authority=authority
            )
            assert result["reason"] == "unmerged"
        with (
            mock.patch.object(
                abandon,
                "local_branch_presence",
                return_value=SimpleNamespace(state="present", reason=""),
            ),
            mock.patch.object(abandon, "_unmerged_commits", return_value=[]),
            mock.patch.object(abandon, "delete_branch_if_merged", return_value={"deleted": True}),
        ):
            result = abandon._abandon_branch(
                target, dry_run=False, force=False, authority=authority
            )
            assert result["deleted"] is True

    def test_branch_presence_refusal(self) -> None:
        with mock.patch.object(
            abandon,
            "local_branch_presence",
            return_value=SimpleNamespace(state="present", reason=""),
        ):
            assert (
                abandon._branch_presence_refusal(
                    Path("/r"), "b", reported_branch="b", absent_reason="absent"
                )
                is None
            )
        with mock.patch.object(
            abandon,
            "local_branch_presence",
            return_value=SimpleNamespace(state="absent", reason=""),
        ):
            result = abandon._branch_presence_refusal(
                Path("/r"), "b", reported_branch="b", absent_reason="absent"
            )
            assert result is not None
            assert result["reason"] == "absent"
        with mock.patch.object(
            abandon,
            "local_branch_presence",
            return_value=SimpleNamespace(state="error", reason="bad"),
        ):
            result = abandon._branch_presence_refusal(
                Path("/r"), "b", reported_branch="b", absent_reason="absent"
            )
            assert result is not None
            assert result["reason"] == "bad"

    def test_unmerged_commits(self) -> None:
        with pytest.raises(RuntimeError, match="base branch is empty"):
            abandon._unmerged_commits(Path("/r"), "", "b")
        with (
            mock.patch.object(abandon, "run_git", return_value=_done(128, stderr="no base")),
            pytest.raises(RuntimeError, match="no base"),
        ):
            abandon._unmerged_commits(Path("/r"), "base", "b")
        with (
            mock.patch.object(
                abandon, "run_git", side_effect=[_done(0), _done(128, stderr="log boom")]
            ),
            pytest.raises(RuntimeError, match="log boom"),
        ):
            abandon._unmerged_commits(Path("/r"), "base", "b")
        with mock.patch.object(
            abandon, "run_git", side_effect=[_done(0), _done(0, stdout="a\nb\n")]
        ):
            assert abandon._unmerged_commits(Path("/r"), "base", "b") == ["a", "b"]


class TestAbandonBranches:
    def test_external_memory_branches(self, tmp_path: Path) -> None:
        _, authority, _ = _abandon_authority(tmp_path)
        with mock.patch.object(abandon, "_abandon_branch", return_value={"deleted": True}):
            result = abandon._abandon_branches(
                _contract(), dry_run=True, force=False, authority=authority
            )
        assert set(result) == {"code", "memory"}
