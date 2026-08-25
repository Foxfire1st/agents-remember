"""Focused proof for organizational sibling and completion-marker invariants."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.kernel.memory_ledger import LedgerError
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.integration import organizational_completion as completion


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _refs() -> tuple[TaskDocumentRef, TaskDocumentRef]:
    return (
        TaskDocumentRef(repository="agents-remember", path="sprint/task.json"),
        TaskDocumentRef(repository="agents-remember", path="sprint/leaf.json"),
    )


def _contracts(**overrides: object) -> tuple[Any, Any, completion._SiblingExpectation]:
    sprint, child = _refs()
    root = Path("/tmp/organizational")
    completing_fields: dict[str, object] = {
        "task_id": "task-id",
        "task_name": "task-name",
        "repo_name": "agents-remember",
        "coordination_root": root,
        "task_root": root / "task",
        "parent_task_name": "sprint",
        "memory_mode": "external",
        "code_repo_path": root / "code",
        "memory_repo_path": root / "memory",
        "code_base_commit": "super-code",
        "memory_base_commit": "super-memory",
        "ledger_commit": "super-ledger",
    }
    completing = _value(**completing_fields)
    contract_path = root / "task" / "leaf" / "contract.json"
    door = _value(
        disposition="claimed",
        sprintTaskDocumentRef=sprint,
        taskDocumentRef=child,
    )
    fields: dict[str, object] = {
        **completing_fields,
        "kind": "leaf",
        "contract_path": contract_path,
        "leaf_id": "L1",
        "parent_contract_path": None,
        "closeout_door": door,
        "code_source_branch": "super",
        "code_base_commit": "base-code",
        "code_commit": "landed-code",
        "integrated_code_commit": "landed-code",
        "memory_base_commit": "base-memory",
        "memory_content_commit": "landed-memory",
        "integrated_memory_content_commit": "landed-memory",
        "ledger_commit": "landed-ledger",
        "integrated_ledger_commit": "landed-ledger",
    }
    fields.update(overrides)
    contract = _value(**fields)
    expected = completion._SiblingExpectation(
        completing_contract=completing,
        contract_path=contract_path,
        sprint_ref=sprint,
        child_ref=child,
        child_id="L1",
        source_branch="super",
    )
    return contract, completing, expected


def test_landed_sibling_routes_exact_code_and_memory_proof() -> None:
    contract, _completing, expected = _contracts(memory_mode="disabled")
    with (
        mock.patch.object(completion, "_require_sibling_contract_identity"),
        mock.patch.object(completion, "_same_repository", return_value=True),
        mock.patch.object(completion, "integration_targets"),
        mock.patch.object(completion, "_require_sibling_code_ancestry") as ancestry,
        mock.patch.object(completion, "_require_landed_sibling_memory") as memory,
    ):
        result = completion._require_landed_sibling(contract, expected)
    assert result == {
        "child": expected.child_ref.key,
        "code": "landed-code",
        "memory": "landed-memory",
        "ledger": "landed-ledger",
        "codeBase": "base-code",
        "memoryBase": "base-memory",
    }
    ancestry.assert_called_once()
    memory.assert_not_called()

    contract.memory_mode = "external"
    with (
        mock.patch.object(completion, "_require_sibling_contract_identity"),
        mock.patch.object(completion, "_same_repository", return_value=True),
        mock.patch.object(completion, "integration_targets"),
        mock.patch.object(completion, "_require_sibling_code_ancestry"),
        mock.patch.object(completion, "_require_landed_sibling_memory") as memory,
    ):
        completion._require_landed_sibling(contract, expected)
    memory.assert_called_once_with(contract, expected)

    with (
        mock.patch.object(completion, "_require_sibling_contract_identity"),
        mock.patch.object(completion, "_same_repository", return_value=False),
        pytest.raises(completion.OrganizationalCompletionError, match="code repository"),
    ):
        completion._require_landed_sibling(contract, expected)


def test_sibling_code_identity_and_ancestry_fail_closed() -> None:
    contract, completing, expected = _contracts()
    completion._require_sibling_contract_identity(contract, expected)
    with pytest.raises(completion.OrganizationalCompletionError, match="landed code edge"):
        invalid, _other, invalid_expected = _contracts(kind="master")
        completion._require_sibling_contract_identity(invalid, invalid_expected)

    with mock.patch.object(completion, "is_ancestor", side_effect=(True, True)):
        completion._require_sibling_code_ancestry(contract, completing, expected.child_ref)
    with (
        mock.patch.object(completion, "is_ancestor", side_effect=(True, False)),
        pytest.raises(completion.OrganizationalCompletionError, match="sprint super"),
    ):
        completion._require_sibling_code_ancestry(contract, completing, expected.child_ref)


def test_sibling_memory_identity_ancestry_and_mapping_are_exact() -> None:
    contract, completing, expected = _contracts()
    mapping = _value(memory_commit="landed-memory")
    with mock.patch.object(completion, "_same_repository", return_value=True):
        completion._require_sibling_memory_identity(contract, completing, expected.child_ref)
    completion._require_sibling_memory_mapping(contract, expected.child_ref, mapping, mapping)

    with (
        mock.patch.object(completion, "_same_repository", return_value=False),
        pytest.raises(completion.OrganizationalCompletionError, match="memory repository"),
    ):
        completion._require_sibling_memory_identity(contract, completing, expected.child_ref)
    missing, other, missing_expected = _contracts(memory_repo_path=None)
    with pytest.raises(completion.OrganizationalCompletionError, match="landed memory edge"):
        completion._require_sibling_memory_identity(missing, other, missing_expected.child_ref)

    with mock.patch.object(completion, "is_ancestor", side_effect=(True, True, True, True)):
        completion._require_sibling_memory_ancestry(contract, completing, expected.child_ref)
    with (
        mock.patch.object(completion, "is_ancestor", side_effect=(True, True, False, True)),
        pytest.raises(completion.OrganizationalCompletionError, match="memory mapping"),
    ):
        completion._require_sibling_memory_ancestry(contract, completing, expected.child_ref)

    with pytest.raises(completion.OrganizationalCompletionError, match="sprint super"):
        completion._require_sibling_memory_mapping(contract, expected.child_ref, None, mapping)
    with pytest.raises(completion.OrganizationalCompletionError, match="proposed final ledger"):
        completion._require_sibling_memory_mapping(contract, expected.child_ref, mapping, None)


def test_sibling_memory_mapping_reader_translates_ledger_ambiguity() -> None:
    contract, completing, expected = _contracts()
    first = _value(memory_commit="landed-memory")
    second = _value(memory_commit="landed-memory")
    with (
        mock.patch.object(completion, "require_git", side_effect=("first", "second")),
        mock.patch.object(completion, "parse_ledger_text", side_effect=("one", "two")),
        mock.patch.object(completion, "find_unique_mapping", side_effect=(first, second)),
    ):
        assert completion._sibling_memory_mappings(contract, completing, expected.child_ref) == (
            first,
            second,
        )

    with (
        mock.patch.object(completion, "require_git", return_value="ledger"),
        mock.patch.object(completion, "parse_ledger_text", return_value="parsed"),
        mock.patch.object(completion, "find_unique_mapping", side_effect=LedgerError("duplicate")),
        pytest.raises(completion.OrganizationalCompletionError, match="duplicate code mappings"),
    ):
        completion._sibling_memory_mappings(contract, completing, expected.child_ref)


def test_repository_and_contract_path_guards_cover_success_escape_and_symlink(
    tmp_path: Path,
) -> None:
    with mock.patch.object(completion, "repository_identity", side_effect=("same", "same")):
        assert completion._same_repository(tmp_path, tmp_path / "other")
    with mock.patch.object(completion, "repository_identity", side_effect=(None, None)):
        assert not completion._same_repository(tmp_path, tmp_path)

    root = tmp_path / "master"
    inside = root / "leaf" / "contract.json"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}", encoding="utf-8")
    _sprint, child = _refs()
    completion._require_confined_sibling_contract_path(root, inside, child)
    assert not completion._path_crosses_symlink(root, inside)

    outside = tmp_path / "outside" / "contract.json"
    outside.parent.mkdir()
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(completion.OrganizationalCompletionError, match="master task root"):
        completion._require_confined_sibling_contract_path(
            root, root / ".." / "outside" / "contract.json", child
        )

    link = root / "linked"
    link.symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(completion.OrganizationalCompletionError, match="symlink"):
        completion._require_confined_sibling_contract_path(root, link / "contract.json", child)
    with pytest.raises(completion.OrganizationalCompletionError, match="no readable"):
        completion._resolved_sibling_paths(root, root / "missing.json", child)


def test_completion_marker_parser_accepts_only_canonical_digest() -> None:
    prefix = completion._COMPLETION_RATIONALE_PREFIX
    digest = "a" * 64
    valid = _value(decision=completion._COMPLETION_DECISION, rationale=prefix + digest)
    assert completion._completion_marker_rationale(valid) == prefix + digest
    assert completion._completion_marker_fingerprint(valid) == digest
    assert completion._valid_completion_fingerprint(digest) == digest
    assert completion._has_completion_marker(_value(decisions=[valid]))
    assert completion._has_completion_marker(_value(decisions=[valid]), fingerprint=digest)

    invalid = (
        _value(decision="other", rationale=prefix + digest),
        _value(decision=completion._COMPLETION_DECISION, rationale=None),
        _value(decision=completion._COMPLETION_DECISION, rationale=digest),
        _value(decision=completion._COMPLETION_DECISION, rationale=prefix + "not-a-digest"),
    )
    for decision in invalid:
        assert completion._completion_marker_fingerprint(decision) is None
    assert completion._valid_completion_fingerprint("A" * 64) is None
    assert not completion._has_completion_marker(_value(decisions=list(invalid)))
