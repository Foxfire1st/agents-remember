"""L6 closeout diff-coverage batch NW1: source_index_cache residual branches.

Covers the exact lines/branches listed in /tmp/l6-cov-NW1.json for
``mcp/src/agents_remember/memory_quality/style/citations/source_index_cache.py``.
Every test runs against tmp_path-only state; the shared frozen citation index
under ``ar-coordination/temp/citation-source-index`` is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations import source_index_cache
from agents_remember.memory_quality.style.citations.source_index_cache import (
    CacheControlState,
    ContractCacheFacts,
    ManagedCacheAuthority,
    TerminalNamespaceGuard,
    _acquisition_transition,
    _current_contract,
)


@pytest.fixture
def authority(tmp_path: Path) -> ManagedCacheAuthority:
    coordination = tmp_path / "coordination"
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    code.mkdir()
    memory.mkdir()
    contract = coordination / "tasks" / "leaf" / "contract.md"
    return source_index_cache.managed_cache_authority(
        coordination_root=coordination,
        contract_path=contract,
        code_root=code,
        memory_root=memory,
        lifecycle_id="L6",
    )


def _unbound_authority(tmp_path: Path) -> ManagedCacheAuthority:
    coordination = tmp_path / "coordination"
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    code.mkdir()
    memory.mkdir()
    return source_index_cache.managed_cache_authority(
        coordination_root=coordination,
        contract_path=coordination / "c.md",
        code_root=code,
        memory_root=memory,
    )


def _write_state(authority: ManagedCacheAuthority, payload: dict[str, object]) -> None:
    authority.control_dir.mkdir(parents=True, exist_ok=True)
    authority.control_state.write_text(json.dumps(payload), encoding="utf-8")


def _matching_state_payload(authority: ManagedCacheAuthority) -> dict[str, object]:
    return {
        "schema": source_index_cache.CONTROL_STATE_SCHEMA,
        "namespace": authority.namespace_id,
        "contract": authority.contract_path.as_posix(),
        "codeRoot": authority.code_root.as_posix(),
        "memoryRoot": authority.memory_root.as_posix(),
        "lifecycleId": "L6",
        "phase": "active",
        "outcome": "",
    }


class TestOpenSharedNamespace:
    def test_rejects_non_directory_namespace(self, tmp_path: Path) -> None:
        authority = _unbound_authority(tmp_path)
        authority.namespace.parent.mkdir(parents=True, exist_ok=True)
        authority.namespace.write_text("not a directory", encoding="utf-8")
        with pytest.raises(CitationCacheError, match="namespace is not a directory"):
            source_index_cache.open_shared_namespace(authority, create=True)

    def test_rejects_symlink_namespace(self, tmp_path: Path) -> None:
        authority = _unbound_authority(tmp_path)
        authority.namespace.parent.mkdir(parents=True, exist_ok=True)
        target = tmp_path / "target-dir"
        target.mkdir()
        authority.namespace.symlink_to(target, target_is_directory=True)
        with pytest.raises(CitationCacheError, match="namespace is not a directory"):
            source_index_cache.open_shared_namespace(authority, create=True)


class TestTerminalGuard:
    def test_preview_without_namespace_authority(self) -> None:
        guard = TerminalNamespaceGuard(None, None, None)
        assert guard.preview() == {
            "removed": False,
            "reason": "no-external-memory-worktree",
        }

    def test_complete_twice_rejected(self) -> None:
        guard = TerminalNamespaceGuard(None, None, None)
        guard.complete(
            outcome="completed",
            publish=lambda: None,
            rollback_publish=lambda: None,
        )
        with pytest.raises(CitationCacheError, match="already completed"):
            guard.complete(
                outcome="completed",
                publish=lambda: None,
                rollback_publish=lambda: None,
            )

    def test_move_tombstone_raises_when_tombstone_survives(
        self, authority: ManagedCacheAuthority, tmp_path: Path
    ) -> None:
        guard = TerminalNamespaceGuard(authority, None, None)
        guard.tombstone = tmp_path / "tomb"
        guard.tombstone.mkdir()
        result: dict[str, object] = {}
        with (
            mock.patch.object(source_index_cache, "atomic_replace", side_effect=OSError("boom")),
            pytest.raises(OSError, match="boom"),
        ):
            guard._move_tombstone_to_retired(authority, tmp_path / "retired", result)

    def test_restore_namespace_rejects_recreated_namespace(
        self, authority: ManagedCacheAuthority, tmp_path: Path
    ) -> None:
        guard = TerminalNamespaceGuard(authority, None, None)
        guard.tombstone = tmp_path / "tomb"
        guard.tombstone.mkdir()
        authority.namespace.mkdir(parents=True, exist_ok=True)
        with pytest.raises(
            CitationCacheError, match="cannot restore terminal citation cache quarantine"
        ):
            guard._restore_namespace()


class TestTerminalNamespaceGuardContext:
    def test_contract_path_mismatch(self, tmp_path: Path) -> None:
        contract = cast(ContractCacheFacts, SimpleNamespace(contract_path=tmp_path / "contract.md"))
        with (
            pytest.raises(CitationCacheError, match="does not match the requested contract"),
            source_index_cache.terminal_namespace_guard(
                contract, requested_contract_path=tmp_path / "requested.md"
            ),
        ):
            pass

    def test_finally_restores_tombstone(self, tmp_path: Path) -> None:
        coordination = tmp_path / "coordination"
        contract_path = coordination / "tasks" / "leaf" / "contract.md"
        code = tmp_path / "code"
        memory = tmp_path / "memory"
        code.mkdir()
        memory.mkdir()
        contract = cast(
            ContractCacheFacts,
            SimpleNamespace(
                coordination_root=coordination,
                contract_path=contract_path,
                code_worktree=code,
                memory_worktree=memory,
                lifecycle_id="L6",
                cleanup="active",
            ),
        )
        tombstone = tmp_path / "tomb"
        with (
            mock.patch.object(
                source_index_cache, "_validate_terminal_contract", return_value=None
            ) as _,
            source_index_cache.terminal_namespace_guard(
                contract, requested_contract_path=contract_path
            ) as guard,
        ):
            assert guard.authority is not None
            assert not guard.authority.namespace.exists()
            guard.tombstone = tombstone
            tombstone.mkdir()
        assert not tombstone.exists()
        assert guard.authority is not None
        assert guard.tombstone is None
        assert guard.authority.namespace.is_dir()


class TestAcquisitionAndValidation:
    def test_legacy_transition_cannot_cross_fence(self) -> None:
        legacy = cast(ManagedCacheAuthority, SimpleNamespace(lifecycle_id=""))
        with pytest.raises(CitationCacheError, match="cannot cross a lifecycle fence"):
            _acquisition_transition(legacy, CacheControlState("L", "active"))

    def test_validate_active_state_lost_authority(self, authority: ManagedCacheAuthority) -> None:
        with pytest.raises(CitationCacheError, match="lost active authority"):
            source_index_cache._validate_active_state(authority, None)

    def test_validate_terminal_contract_stale(self, authority: ManagedCacheAuthority) -> None:
        current = SimpleNamespace(lifecycle_id="OTHER", cleanup="active")
        with (
            mock.patch.object(source_index_cache, "_current_contract", return_value=current),
            pytest.raises(CitationCacheError, match="terminal citation cache authority is stale"),
        ):
            source_index_cache._validate_terminal_contract(authority, None)

    def test_validate_terminal_contract_missing_fence(
        self, authority: ManagedCacheAuthority
    ) -> None:
        current = SimpleNamespace(lifecycle_id="L6", cleanup="completed")
        with (
            mock.patch.object(source_index_cache, "_current_contract", return_value=current),
            pytest.raises(
                CitationCacheError, match="no matching persistent citation cache fence state"
            ),
        ):
            source_index_cache._validate_terminal_contract(authority, None)

    def test_validate_terminal_contract_wrong_active_owner(
        self, authority: ManagedCacheAuthority
    ) -> None:
        current = SimpleNamespace(lifecycle_id="L6", cleanup="active")
        with (
            mock.patch.object(source_index_cache, "_current_contract", return_value=current),
            pytest.raises(CitationCacheError, match="does not own the active lifecycle"),
        ):
            source_index_cache._validate_terminal_contract(
                authority, CacheControlState("OTHER", "active")
            )

    def test_require_current_active_contract_stale(self, authority: ManagedCacheAuthority) -> None:
        current = SimpleNamespace(lifecycle_id="OTHER", cleanup="active")
        with (
            mock.patch.object(source_index_cache, "_current_contract", return_value=current),
            pytest.raises(CitationCacheError, match="stale for the current contract"),
        ):
            source_index_cache._require_current_active_contract(authority)

    def test_require_current_active_contract_terminal(
        self, authority: ManagedCacheAuthority
    ) -> None:
        current = SimpleNamespace(lifecycle_id="L6", cleanup="completed")
        with (
            mock.patch.object(source_index_cache, "_current_contract", return_value=current),
            pytest.raises(CitationCacheError, match="is terminal"),
        ):
            source_index_cache._require_current_active_contract(authority)

    def test_require_current_legacy_contract_stale(self, authority: ManagedCacheAuthority) -> None:
        current = SimpleNamespace(lifecycle_id="L6", cleanup="active")
        with (
            mock.patch.object(source_index_cache, "_current_contract", return_value=current),
            pytest.raises(CitationCacheError, match="stale for a lifecycle-bound contract"),
        ):
            source_index_cache._require_current_legacy_active_contract(authority)

    def test_require_current_legacy_contract_terminal(
        self, authority: ManagedCacheAuthority
    ) -> None:
        current = SimpleNamespace(lifecycle_id="", cleanup="completed")
        with (
            mock.patch.object(source_index_cache, "_current_contract", return_value=current),
            pytest.raises(CitationCacheError, match="cannot open a terminal contract"),
        ):
            source_index_cache._require_current_legacy_active_contract(authority)

    def test_current_contract_authority_mismatch(self, authority: ManagedCacheAuthority) -> None:
        current = SimpleNamespace(
            coordination_root=authority.coordination_root,
            contract_path=authority.contract_path,
            code_worktree=authority.code_root,
            memory_worktree=None,
            lifecycle_id="",
            cleanup="",
        )
        with (
            mock.patch.object(source_index_cache, "load_contract", return_value=current),
            pytest.raises(CitationCacheError, match="does not authorize"),
        ):
            _current_contract(authority)


class TestControlStateValidation:
    def test_state_exceeds_fixed_bound(self, authority: ManagedCacheAuthority) -> None:
        authority.control_dir.mkdir(parents=True, exist_ok=True)
        authority.control_state.write_text(
            "x" * (source_index_cache.MAX_CONTROL_STATE_BYTES + 1), encoding="utf-8"
        )
        with pytest.raises(CitationCacheError, match="record exceeds its fixed size bound"):
            source_index_cache._read_control_state(authority)

    def test_state_not_an_object(self, authority: ManagedCacheAuthority) -> None:
        _write_state(authority, cast(dict[str, object], json.loads("[]")))
        with pytest.raises(CitationCacheError, match="record is not an object"):
            source_index_cache._read_control_state(authority)

    def test_state_authority_mismatch(self, authority: ManagedCacheAuthority) -> None:
        payload = _matching_state_payload(authority)
        payload["schema"] = 2
        _write_state(authority, payload)
        with pytest.raises(CitationCacheError, match="schema does not match authority"):
            source_index_cache._read_control_state(authority)

    def test_state_empty_lifecycle(self, authority: ManagedCacheAuthority) -> None:
        payload = _matching_state_payload(authority)
        payload["lifecycleId"] = ""
        _write_state(authority, payload)
        with pytest.raises(CitationCacheError, match="lifecycleId is empty"):
            source_index_cache._read_control_state(authority)

    def test_state_invalid_phase(self, authority: ManagedCacheAuthority) -> None:
        payload = _matching_state_payload(authority)
        payload["phase"] = "paused"
        _write_state(authority, payload)
        with pytest.raises(CitationCacheError, match="phase is invalid"):
            source_index_cache._read_control_state(authority)

    def test_state_invalid_outcome(self, authority: ManagedCacheAuthority) -> None:
        payload = _matching_state_payload(authority)
        payload["outcome"] = 123
        _write_state(authority, payload)
        with pytest.raises(CitationCacheError, match="outcome is invalid"):
            source_index_cache._read_control_state(authority)

    def test_write_control_state_exceeds_fixed_bound(
        self, authority: ManagedCacheAuthority
    ) -> None:
        state = CacheControlState("x" * 5000, "active")
        with pytest.raises(CitationCacheError, match="exceeds its fixed bound"):
            source_index_cache._write_control_state(authority, state)
