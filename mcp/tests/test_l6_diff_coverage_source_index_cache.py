"""L6 closeout coverage tests for managed citation cache authority branches."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations import source_index_cache
from agents_remember.memory_quality.style.citations.source_index_cache import (
    CacheControlState,
    ContractCacheFacts,
    ManagedCacheAuthority,
    TerminalNamespaceGuard,
    _absent_result,
    _acquisition_transition,
    _base_result,
    _lease_timeout,
    _read_control_state,
    _remove_tree,
    _resolved_authority,
    managed_cache_authority,
    open_shared_namespace,
    reclaim_managed_namespace,
)


@pytest.fixture
def authority(tmp_path: Path) -> ManagedCacheAuthority:
    coordination = tmp_path / "coordination"
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    code.mkdir()
    memory.mkdir()
    contract = coordination / "tasks" / "leaf" / "enclosures" / "leaf" / "series-contract.md"
    return managed_cache_authority(
        coordination_root=coordination,
        contract_path=contract,
        code_root=code,
        memory_root=memory,
        lifecycle_id="L1",
    )


class TestAuthorityResolution:
    def test_missing_roots(self, tmp_path: Path) -> None:
        coordination = tmp_path / "coordination"
        with pytest.raises(CitationCacheError, match="citation cache code root does not exist"):
            managed_cache_authority(
                coordination_root=coordination,
                contract_path=coordination / "c.md",
                code_root=tmp_path / "missing-code",
                memory_root=tmp_path / "missing-memory",
            )

    def test_resolved_authority_errors(self, tmp_path: Path) -> None:
        code = tmp_path / "code"
        memory = tmp_path / "memory"
        code.mkdir()
        memory.mkdir()
        with pytest.raises(CitationCacheError, match="outside coordination root"):
            _resolved_authority(
                coordination_root=tmp_path,
                contract_path=tmp_path.parent / "outside.md",
                code_root=code,
                memory_root=memory,
                lifecycle_id=None,
            )
        with pytest.raises(CitationCacheError, match="must be distinct"):
            _resolved_authority(
                coordination_root=tmp_path,
                contract_path=tmp_path / "c.md",
                code_root=code,
                memory_root=code,
                lifecycle_id=None,
            )
        with pytest.raises(CitationCacheError, match="must stay outside"):
            _resolved_authority(
                coordination_root=code,
                contract_path=code / "c.md",
                code_root=code,
                memory_root=memory,
                lifecycle_id=None,
            )

    def test_contract_cache_authority_no_memory(self) -> None:
        facts = cast(ContractCacheFacts, SimpleNamespace(memory_worktree=None))
        assert source_index_cache.contract_cache_authority(facts) is None


class TestOpenSharedNamespace:
    def test_create_false_when_absent(self, tmp_path: Path) -> None:
        (tmp_path / "code").mkdir()
        (tmp_path / "memory").mkdir()
        unbound = source_index_cache.managed_cache_authority(
            coordination_root=tmp_path / "coordination",
            contract_path=tmp_path / "coordination" / "c.md",
            code_root=tmp_path / "code",
            memory_root=tmp_path / "memory",
        )
        with pytest.raises(CitationCacheError, match="is not published"):
            open_shared_namespace(unbound, create=False)

    def test_capacity_full(self, tmp_path: Path) -> None:
        (tmp_path / "code").mkdir()
        (tmp_path / "memory").mkdir()
        unbound = source_index_cache.managed_cache_authority(
            coordination_root=tmp_path / "coordination",
            contract_path=tmp_path / "coordination" / "c.md",
            code_root=tmp_path / "code",
            memory_root=tmp_path / "memory",
        )
        root = unbound.managed_root
        root.mkdir(parents=True, exist_ok=True)
        for i in range(source_index_cache.MANAGED_NAMESPACE_LIMIT):
            (root / f"{i:040x}").mkdir()
        with pytest.raises(CitationCacheError, match="capacity is full"):
            open_shared_namespace(unbound, create=True)


class TestTerminalGuard:
    def test_without_namespace_both_fail(self) -> None:
        guard = TerminalNamespaceGuard(None, None, None)

        def publish() -> None:
            raise RuntimeError("publish boom")

        def rollback() -> None:
            raise RuntimeError("rollback boom")

        with pytest.raises(CitationCacheError, match="publication and rollback both failed"):
            guard.complete(outcome="completed", publish=publish, rollback_publish=rollback)

    def test_without_namespace_publish_fail_rollback_ok(self) -> None:
        guard = TerminalNamespaceGuard(None, None, None)

        def publish() -> None:
            raise RuntimeError("publish boom")

        with pytest.raises(RuntimeError, match="publish boom"):
            guard.complete(outcome="completed", publish=publish, rollback_publish=lambda: None)

    def test_without_namespace_success(self) -> None:
        guard = TerminalNamespaceGuard(None, None, None)
        result = guard.complete(
            outcome="completed", publish=lambda: None, rollback_publish=lambda: None
        )
        assert result["reason"] == "no-external-memory-worktree"

    def test_quarantine_already_absent(self, authority: ManagedCacheAuthority) -> None:
        guard = TerminalNamespaceGuard(authority, None, None)
        result: dict[str, object] = {}
        guard._quarantine_live_namespace(authority, result)
        assert result["reason"] == "already-absent"

    def test_move_tombstone_error(self, authority: ManagedCacheAuthority, tmp_path: Path) -> None:
        guard = TerminalNamespaceGuard(authority, None, None)
        guard.tombstone = tmp_path / "tomb"
        retired = tmp_path / "retired"
        retired.write_text("x", encoding="utf-8")
        result: dict[str, object] = {}
        with mock.patch.object(source_index_cache, "atomic_replace", side_effect=OSError("boom")):
            guard._move_tombstone_to_retired(authority, retired, result)
        commit = result["retirement_commit"]
        assert isinstance(commit, dict) and commit.get("durable") is False

    def test_cleanup_retired_error(self, tmp_path: Path) -> None:
        result: dict[str, object] = {}
        TerminalNamespaceGuard._cleanup_retired_namespace(tmp_path / "missing", result)
        cleanup = result["retired_cleanup"]
        assert isinstance(cleanup, dict) and cleanup.get("removed") is False

    def test_restore_namespace_branches(
        self, authority: ManagedCacheAuthority, tmp_path: Path
    ) -> None:
        guard = TerminalNamespaceGuard(None, None, None)
        guard._restore_namespace()
        guard = TerminalNamespaceGuard(authority, None, None)
        guard._restore_namespace()
        guard.tombstone = tmp_path / "tomb"
        guard._restore_namespace()


class TestReclaimAndAcquisition:
    def test_reclaim_managed_namespace_branches(self, authority: ManagedCacheAuthority) -> None:
        with mock.patch.object(
            source_index_cache, "_exclusive_before_deadline", return_value=False
        ):
            result = reclaim_managed_namespace(authority, dry_run=False)
            assert result.get("reason") == "live-lease-timeout"
        with mock.patch.object(source_index_cache, "_exclusive_before_deadline", return_value=True):
            result = reclaim_managed_namespace(authority, dry_run=False)
            assert result.get("reason") == "already-absent"
            authority.namespace.mkdir(parents=True, exist_ok=True)
            result = reclaim_managed_namespace(authority, dry_run=True)
            assert result.get("would_remove") is True

    def test_acquisition_transition_branches(self, authority: ManagedCacheAuthority) -> None:
        unbound = cast(ManagedCacheAuthority, SimpleNamespace(lifecycle_id=None))
        assert _acquisition_transition(unbound, None) is None
        with pytest.raises(CitationCacheError, match="cannot cross a lifecycle fence"):
            _acquisition_transition(unbound, CacheControlState("L", "active"))
        with mock.patch.object(
            source_index_cache, "_require_current_active_contract", return_value=None
        ):
            state = _acquisition_transition(authority, None)
            assert state is not None and state.phase == "active"
            assert _acquisition_transition(authority, CacheControlState("L1", "active")) is None
        with pytest.raises(CitationCacheError, match="stale for the active lifecycle"):
            _acquisition_transition(authority, CacheControlState("OTHER", "active"))
        with pytest.raises(CitationCacheError, match="is terminal"):
            _acquisition_transition(authority, CacheControlState("L1", "terminal"))
        with mock.patch.object(
            source_index_cache, "_require_current_active_contract", return_value=None
        ):
            state = _acquisition_transition(authority, CacheControlState("OTHER", "terminal"))
            assert state is not None and state.lifecycle_id == "L1"

    def test_read_control_state(self, authority: ManagedCacheAuthority) -> None:
        assert _read_control_state(authority) is None
        state = CacheControlState("L1", "active")
        source_index_cache._write_control_state(authority, state)
        assert _read_control_state(authority) == state
        authority.control_state.write_text("{bad", encoding="utf-8")
        with pytest.raises(CitationCacheError, match="control state is invalid"):
            _read_control_state(authority)

    def test_base_and_remove_tree(self, authority: ManagedCacheAuthority, tmp_path: Path) -> None:
        assert "namespace" in _base_result(authority)
        assert _absent_result(authority)["reason"] == "already-absent"
        assert _lease_timeout(authority)["reason"] == "live-lease-timeout"
        directory = tmp_path / "d"
        directory.mkdir()
        _remove_tree(directory)
        assert not directory.exists()
        with mock.patch.object(source_index_cache.shutil, "rmtree", side_effect=OSError("boom")):
            directory.mkdir()
            with pytest.raises(CitationCacheError, match="cannot reclaim managed citation cache"):
                _remove_tree(directory)
