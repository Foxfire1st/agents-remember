"""Focused proof for schema-1 archive evidence and crash convergence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.worktrees.integration.legacy import legacy_operation_archive as archive
from agents_remember.worktrees.integration.legacy.legacy_operation_failures import LegacyBridgeError


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _commits(**overrides: object) -> Any:
    fields: dict[str, object] = {
        "codeCommit": "code",
        "memoryContentCommit": "memory",
        "ledgerCommit": "ledger",
    }
    fields.update(overrides)
    value = _value(**fields)
    value.model_dump = lambda mode=None: fields.copy()
    return value


def _contract(**overrides: object) -> Any:
    fields: dict[str, object] = {
        "kind": "leaf",
        "closeout_status": "completed",
        "integration_status": "completed",
        "code_repo_path": Path("/tmp/code"),
        "code_worktree": Path("/tmp/code-worktree"),
        "code_work_branch": "work",
        "code_source_branch": "super",
        "code_commit": "code",
        "memory_content_commit": "memory",
        "ledger_commit": "ledger",
        "integrated_code_commit": "code",
        "integrated_memory_content_commit": "memory",
        "integrated_ledger_commit": "ledger",
        "memory_mode": "external",
        "memory_repo_path": Path("/tmp/memory"),
        "memory_source_branch": "memory-super",
    }
    fields.update(overrides)
    return _value(**fields)


def _archive_model(original: bytes = b"original") -> Any:
    digest = hashlib.sha256(original).hexdigest()
    payload = '{\n  "receipt": "exact"\n}'
    return _value(
        originalSha256=digest,
        model_dump_json=lambda indent=None: payload,
    )


def test_terminal_and_operation_specific_evidence_fail_closed() -> None:
    commits = _commits()
    legacy = _value(
        workerPid=None,
        status="completed",
        phase="completed",
        recoveryCommits=commits,
    )
    archive._require_terminal_state(legacy)
    assert archive._required_recovery_commits(legacy) is commits
    with pytest.raises(LegacyBridgeError, match="terminal completed"):
        archive._require_terminal_state(_value(workerPid=1, status="running", phase="quality"))
    with pytest.raises(LegacyBridgeError, match="recovery commits"):
        archive._required_recovery_commits(_value(recoveryCommits=None))

    contract = _contract()
    with mock.patch.object(archive, "_closeout_archive_evidence", return_value="closeout"):
        assert archive._archive_output_evidence(contract, "closeout", commits) == "closeout"
    with mock.patch.object(archive, "_integration_archive_evidence", return_value="integrate"):
        assert archive._archive_output_evidence(contract, "integrate", commits) == "integrate"
    with pytest.raises(LegacyBridgeError, match="cannot be archived"):
        archive._archive_output_evidence(contract, cast(Any, "direct-landing"), commits)


def test_closeout_archive_binds_contract_commits_and_live_ancestry() -> None:
    contract = _contract()
    commits = _commits()
    with (
        mock.patch.object(archive, "head_commit", return_value="live"),
        mock.patch.object(archive, "is_ancestor", return_value=True),
    ):
        assert archive._closeout_archive_evidence(contract, commits) == "live"

    series = _contract(kind="series")
    with (
        mock.patch.object(archive, "branch_commit", return_value="live"),
        mock.patch.object(archive, "is_ancestor", return_value=True),
    ):
        assert archive._closeout_archive_evidence(series, commits) == "live"

    with (
        mock.patch.object(archive, "head_commit", return_value="live"),
        mock.patch.object(archive, "is_ancestor", return_value=False),
        pytest.raises(LegacyBridgeError, match="terminal closeout output"),
    ):
        archive._closeout_archive_evidence(contract, commits)


def test_integration_archive_binds_code_and_external_memory_refs() -> None:
    contract = _contract()
    commits = _commits()
    with (
        mock.patch.object(archive, "branch_commit", side_effect=("code", "ledger")),
    ):
        assert archive._integration_archive_evidence(contract, commits) == "code"

    with (
        mock.patch.object(archive, "branch_commit", return_value="other"),
        pytest.raises(LegacyBridgeError, match="protected refs"),
    ):
        archive._integration_archive_evidence(contract, commits)

    archive._require_external_memory_archive_evidence(_contract(memory_mode="disabled"))
    with pytest.raises(LegacyBridgeError, match="no memory repository"):
        archive._require_external_memory_archive_evidence(_contract(memory_repo_path=None))
    with (
        mock.patch.object(archive, "branch_commit", return_value="other"),
        pytest.raises(LegacyBridgeError, match="terminal ledger output"),
    ):
        archive._require_external_memory_archive_evidence(contract)
    with mock.patch.object(archive, "branch_commit", return_value="ledger"):
        archive._require_external_memory_archive_evidence(contract)


def test_archive_publication_converges_for_new_and_existing_receipts(tmp_path: Path) -> None:
    original = b"original"
    model = _archive_model(original)
    source = tmp_path / "legacy.json"
    receipt = tmp_path / "legacy.archive.json"

    source.write_bytes(original)
    archive.publish_archive(source, receipt, model, original=original)
    assert not source.exists()
    payload = model.model_dump_json(indent=2) + "\n"
    assert receipt.read_text(encoding="utf-8") == payload

    source.write_bytes(original)
    archive.publish_archive(source, receipt, model, original=original)
    assert not source.exists()
    archive._require_existing_archive(receipt, payload)
    receipt.write_text("different", encoding="utf-8")
    with pytest.raises(LegacyBridgeError, match="different evidence"):
        archive._require_existing_archive(receipt, payload)
    with pytest.raises(LegacyBridgeError, match="unreadable"):
        archive._require_existing_archive(tmp_path, payload)


def test_receipt_write_failure_classifies_all_durable_byte_states(tmp_path: Path) -> None:
    original = b"original"
    model = _archive_model(original)
    request = archive._ReceiptWrite(
        tmp_path / "source",
        tmp_path / "receipt",
        model,
        original,
        "payload",
    )
    error = OSError("interrupted")

    with mock.patch.object(archive, "read_publication_bytes", side_effect=(b"payload", original)):
        archive._resolve_receipt_write_failure(request, error)
    with (
        mock.patch.object(archive, "read_publication_bytes", side_effect=(None, original)),
        pytest.raises(LegacyBridgeError, match="original bytes unchanged"),
    ):
        archive._resolve_receipt_write_failure(request, error)
    with (
        mock.patch.object(archive, "read_publication_bytes", side_effect=(b"other", b"changed")),
        pytest.raises(LegacyBridgeError, match="contradictory durable bytes"),
    ):
        archive._resolve_receipt_write_failure(request, error)

    with (
        mock.patch.object(archive, "atomic_write_text", side_effect=error),
        mock.patch.object(archive, "_resolve_receipt_write_failure") as resolve,
    ):
        archive._write_archive_receipt(
            request.source_path,
            request.archive_path,
            request.archive,
            request.original,
            request.payload,
        )
    resolve.assert_called_once()


def test_unlink_failure_classifies_pending_converged_and_conflicting_states(
    tmp_path: Path,
) -> None:
    original = b"original"
    model = _archive_model(original)
    expected_receipt = (model.model_dump_json(indent=2) + "\n").encode()
    error = OSError("interrupted")
    source = tmp_path / "source"
    receipt = tmp_path / "receipt"

    with (
        mock.patch.object(
            archive, "read_publication_bytes", side_effect=(original, expected_receipt)
        ),
        pytest.raises(LegacyBridgeError, match="original unlink was interrupted"),
    ):
        archive._resolve_archive_unlink_failure(source, model, receipt, error)
    with mock.patch.object(archive, "read_publication_bytes", side_effect=(None, expected_receipt)):
        archive._resolve_archive_unlink_failure(source, model, receipt, error)
    with (
        mock.patch.object(archive, "read_publication_bytes", side_effect=(b"other", None)),
        pytest.raises(LegacyBridgeError, match="contradictory source"),
    ):
        archive._resolve_archive_unlink_failure(source, model, receipt, error)

    archive.finish_archive_unlink(source, model, receipt)
    source.write_bytes(original)
    with (
        mock.patch.object(Path, "unlink", side_effect=error),
        mock.patch.object(archive, "_resolve_archive_unlink_failure") as resolve,
    ):
        archive.finish_archive_unlink(source, model, receipt)
    resolve.assert_called_once_with(source, model, receipt, error)


def test_publication_byte_reader_translates_non_absence_io(tmp_path: Path) -> None:
    path = tmp_path / "bytes"
    path.write_bytes(b"payload")
    assert archive.read_publication_bytes(path) == b"payload"
    assert archive.read_publication_bytes(tmp_path / "missing") is None
    assert archive._publication_digest(None) == hashlib.sha256(b"").hexdigest()
    with pytest.raises(LegacyBridgeError, match="unreadable"):
        archive.read_publication_bytes(tmp_path)
