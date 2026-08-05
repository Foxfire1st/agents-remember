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
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.worktrees.modules import abandon
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.worktree_contract import WorktreeContract


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


class TestAbandonReserved:
    def test_guard_enter_raises(self) -> None:
        ctx = mock.MagicMock()
        ctx.__enter__.side_effect = CitationCacheError("live lease busy")
        with (
            mock.patch.object(abandon, "terminal_namespace_guard", return_value=ctx),
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
            mock.patch.object(abandon, "terminal_namespace_guard", return_value=ctx),
            mock.patch.object(abandon, "status_payload", return_value={}),
        ):
            result = abandon._abandon_reserved(_args(), _contract(), TerminalPreflight({}, {}, ()))
        citation_index = result.payload["citation_source_index"]
        assert isinstance(citation_index, dict)
        assert citation_index.get("reason") == "other"


class TestAbandonWithGuard:
    def test_helper_failure(self) -> None:
        guard = cast(TerminalNamespaceGuard, SimpleNamespace(preview=lambda: {"removed": False}))
        with (
            mock.patch.object(
                abandon, "_abandon_terminal_outputs", side_effect=RuntimeError("boom")
            ),
            mock.patch.object(abandon, "status_payload", return_value={}),
        ):
            result = abandon._abandon_with_guard(
                _args(), _contract(), TerminalPreflight({}, {}, ()), guard
            )
        assert result.returncode == 2
        assert "boom" in str(result.payload["blockers"])

    def test_mutation_blocked(self) -> None:
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
                _args(), _contract(), TerminalPreflight({}, {}, ()), guard
            )
        assert result.returncode == 2 and result.payload["state"] == "abandon-blocked"

    def test_dry_run(self) -> None:
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
                _args(dry_run=True), _contract(), TerminalPreflight({}, {}, ()), guard
            )
        assert result.returncode == 0 and result.payload["state"] == "would-abandon"

    def test_publish(self) -> None:
        guard = cast(TerminalNamespaceGuard, SimpleNamespace(preview=lambda: {"removed": True}))
        outputs = ({}, {}, {}, {})
        with (
            mock.patch.object(abandon, "_abandon_terminal_outputs", return_value=outputs),
            mock.patch.object(abandon, "terminal_result_blockers", return_value=[]),
            mock.patch.object(
                abandon, "_publish_abandon", return_value=SimpleNamespace(returncode=0)
            ) as publish,
        ):
            result = abandon._abandon_with_guard(
                _args(), _contract(), TerminalPreflight({}, {}, ()), guard
            )
        assert result.returncode == 0 and publish.called


class TestAbandonBranch:
    def test_branch_refusals_and_unmerged(self) -> None:
        with mock.patch.object(
            abandon,
            "local_branch_presence",
            return_value=SimpleNamespace(state="absent", reason=""),
        ):
            result = abandon._abandon_branch(
                Path("/r"), "ar/leaf", "ar/base", dry_run=False, force=False
            )
            assert result["reason"] == "base branch is missing: ar/base"
        with mock.patch.object(
            abandon,
            "local_branch_presence",
            side_effect=[
                SimpleNamespace(state="present", reason=""),
                SimpleNamespace(state="absent", reason=""),
            ],
        ):
            result = abandon._abandon_branch(
                Path("/r"), "ar/leaf", "ar/base", dry_run=False, force=False
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
            result = abandon._abandon_branch(
                Path("/r"), "ar/leaf", "ar/base", dry_run=False, force=True
            )
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
                Path("/r"), "ar/leaf", "ar/base", dry_run=False, force=False
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
                Path("/r"), "ar/leaf", "ar/base", dry_run=False, force=False
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
                Path("/r"), "ar/leaf", "ar/base", dry_run=False, force=False
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
    def test_external_memory_branches(self) -> None:
        with (
            mock.patch.object(abandon, "_abandon_branch", return_value={"deleted": True}),
            mock.patch.object(abandon, "integration_branch", return_value="ar/integration"),
        ):
            result = abandon._abandon_branches(_contract(), dry_run=True, force=False)
        assert set(result) == {"code", "memory", "memory_integration"}
