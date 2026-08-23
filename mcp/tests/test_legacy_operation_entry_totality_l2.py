"""Public totality forcing for legacy bridge confinement, reads, and reloads."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.lifecycle import legacy_operation_tool as legacy_app
from agents_remember.application.lifecycle.legacy_operation_tool import LegacyOperationRequest
from agents_remember.application.lifecycle.lifecycle_enclosure_tools import (
    EnclosureAdoptionRequest,
    worktree_enclosure_adopt_tool,
)
from agents_remember.mcp.tools.worktree import worktree_legacy_operation_payload
from test_lifecycle_enclosure_adoption_l2 import _legacy_enclosure, byte_tree


def _inspect() -> LegacyOperationRequest:
    return LegacyOperationRequest(operation_kind="closeout", action="inspect")


def _migrate() -> LegacyOperationRequest:
    return LegacyOperationRequest(
        operation_kind="closeout",
        action="migrate",
        expected_digest="a" * 64,
        memory_commit_message="finish exact legacy memory",
        ledger_commit_message="finish exact legacy ledger",
        audit_reason="exercise apply-time contract revalidation",
    )


def _adopt(config, request: EnclosureAdoptionRequest) -> None:
    preview = worktree_enclosure_adopt_tool(config, request)
    applied = worktree_enclosure_adopt_tool(
        config,
        EnclosureAdoptionRequest(**preview["nextArgs"]),
    )
    assert applied["state"] == "enclosure-adopted"


def test_legacy_public_entry_refuses_unconfined_contract_before_any_read(tmp_path: Path) -> None:
    config, contract, _source, _original, _request = _legacy_enclosure(tmp_path)
    before = byte_tree(contract.coordination_root)

    result = worktree_legacy_operation_payload(
        config,
        (tmp_path.parent / "PRIVATE-OUTSIDE-CONTRACT").as_posix(),
        _inspect(),
    )

    assert result["status"] == "legacy-contract-address-invalid"
    assert result["nextAction"] == "developer-decision"
    assert result["observed"]["failure"] == {
        "stage": "legacy-contract-confinement",
        "side": "contract",
        "name": "configured-contract-address",
        "errorType": "AuthorityError",
        "observed": {"state": "invalid"},
    }
    assert "PRIVATE-OUTSIDE-CONTRACT" not in repr(result)
    assert byte_tree(contract.coordination_root) == before


def test_preadoption_unreadable_initial_contract_never_inferrs_report_target(
    tmp_path: Path,
) -> None:
    config, contract, source, _original, _request = _legacy_enclosure(tmp_path)
    before = byte_tree(contract.coordination_root)
    sentinel = "PRIVATE-PREADOPTION-READ-/untrusted/path"

    with (
        mock.patch.object(legacy_app, "load_contract", side_effect=OSError(sentinel)),
        mock.patch(
            "agents_remember.worktrees.integration.legacy.legacy_operation_bridge."
            "_legacy_operation_action_locked",
            side_effect=AssertionError("raw legacy target must remain unread"),
        ),
    ):
        result = worktree_legacy_operation_payload(
            config,
            contract.contract_path.as_posix(),
            _inspect(),
        )

    assert result["status"] == "legacy-contract-unreadable-before-adoption"
    assert result["expected"] == {
        "contractPath": contract.contract_path.as_posix(),
        "locatorState": "explicit-adoption-required",
    }
    assert result["nextAction"] == "developer-decision"
    assert sentinel not in repr(result)
    assert source.exists()
    assert byte_tree(contract.coordination_root) == before


def test_addressable_unreadable_initial_contract_uses_locator_publication_authority(
    tmp_path: Path,
) -> None:
    config, contract, source, _original, request = _legacy_enclosure(tmp_path)
    _adopt(config, request)
    source = contract.worktree_group / ".lifecycle" / source.name
    before = byte_tree(contract.coordination_root)
    sentinel = "PRIVATE-ADDRESSABLE-READ-/untrusted/path"

    with mock.patch.object(legacy_app, "load_contract", side_effect=OSError(sentinel)):
        result = worktree_legacy_operation_payload(
            config,
            contract.contract_path.as_posix(),
            _inspect(),
        )

    assert result["ok"] is False
    assert result["nextAction"] == "developer-decision"
    assert result["expected"]["route"] == "locator -> root manifest -> strict journal"
    assert result["expected"]["locatorId"]
    assert result["expected"]["publicationRequestId"]
    assert result["expected"]["bindingFingerprint"]
    assert sentinel not in repr(result)
    assert source.exists()
    assert byte_tree(contract.coordination_root) == before


@pytest.mark.parametrize("addressable", [False, True])
def test_legacy_reload_failure_refuses_before_raw_record_access(
    tmp_path: Path,
    addressable: bool,
) -> None:
    config, contract, source, _original, request = _legacy_enclosure(tmp_path)
    if addressable:
        _adopt(config, request)
        source = contract.worktree_group / ".lifecycle" / source.name
    source_before = source.read_bytes()
    sentinel = "PRIVATE-LEGACY-RELOAD-/untrusted/path"

    with (
        mock.patch.object(
            legacy_app,
            "load_contract",
            side_effect=[contract, OSError(sentinel)],
        ),
        mock.patch(
            "agents_remember.worktrees.integration.legacy.legacy_operation_bridge."
            "_legacy_operation_action_locked",
            side_effect=AssertionError("raw legacy target must remain unread"),
        ),
    ):
        result = worktree_legacy_operation_payload(
            config,
            contract.contract_path.as_posix(),
            _migrate(),
        )

    expected_status = (
        "legacy-contract-unreadable-before-adoption"
        if not addressable
        else "closeout-lifecycle-journal-unreadable"
    )
    assert result["status"] == expected_status
    assert result["nextAction"] == "developer-decision"
    assert sentinel not in repr(result)
    assert source.exists()
    assert source.read_bytes() == source_before


@pytest.mark.parametrize("reload", [False, True])
def test_legacy_repository_binding_failure_is_typed_before_record_access(
    tmp_path: Path,
    reload: bool,
) -> None:
    config, contract, source, _original, _request = _legacy_enclosure(tmp_path)
    source_before = source.read_bytes()
    sentinel = "PRIVATE-REPOSITORY-BINDING-/untrusted/path"
    side_effect = [None, RuntimeError(sentinel)] if reload else RuntimeError(sentinel)

    with (
        mock.patch.object(
            legacy_app,
            "require_configured_contract_repositories",
            side_effect=side_effect,
        ),
        mock.patch(
            "agents_remember.worktrees.integration.legacy.legacy_operation_bridge."
            "_legacy_operation_action_locked",
            side_effect=AssertionError("raw legacy target must remain unread"),
        ),
    ):
        result = worktree_legacy_operation_payload(
            config,
            contract.contract_path.as_posix(),
            _migrate() if reload else _inspect(),
        )

    assert result["status"] == "legacy-contract-authority-invalid"
    assert result["observed"]["failure"]["stage"] == (
        "legacy-contract-reload-authority" if reload else "legacy-contract-authority"
    )
    assert result["nextAction"] == "developer-decision"
    assert sentinel not in repr(result)
    assert source.exists()
    assert source.read_bytes() == source_before
