"""Focused proof for the journal-owned organizational reset boundary."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.integration import organizational_completion_repair as repair
from agents_remember.worktrees.integration.integration_branch_types import IntegrationTarget
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _evidence(**overrides: object) -> Any:
    fields: dict[str, object] = {
        "operationKey": "operation",
        "candidateState": "candidate",
        "contractPath": "/tmp/contract.json",
        "taskId": "task-id",
        "taskName": "task-name",
        "sprintTaskDocument": "agents-remember/sprint/task.json",
        "candidateTaskDocument": "agents-remember/sprint/leaf.json",
        "owningMasterTaskDocument": "agents-remember/sprint/master.json",
        "codeCommit": "code",
        "memoryContentCommit": "memory",
        "ledgerCommit": "ledger",
        "acceptedContractSha256": "accepted",
        "resetContractSha256": "reset",
    }
    fields.update(overrides)
    return _value(**fields)


def _authority(**overrides: object) -> Any:
    fields: dict[str, object] = {
        "targetKind": "sprint-super",
        "codeRepository": "/tmp/code",
        "codeSourceBranch": "super",
        "codeSourceRef": "refs/heads/super",
        "codeSourceCommit": "code-base",
        "codeCandidateCommit": "code",
        "memoryRepository": "/tmp/memory",
        "memorySourceBranch": "memory-super",
        "memorySourceRef": "refs/heads/memory-super",
        "memorySourceCommit": "memory-base",
        "memoryContentCommit": "memory",
        "ledgerCommit": "ledger",
    }
    fields.update(overrides)
    return _value(**fields)


def _record(**overrides: object) -> Any:
    authority = overrides.pop("integrationAuthority", _authority())
    fields: dict[str, object] = {
        "operationKind": "integrate",
        "contractPath": "/tmp/contract.json",
        "input": _value(contractPath="/tmp/contract.json"),
        "taskId": "task-id",
        "taskName": "task-name",
        "operationKey": "operation",
        "candidateState": "candidate",
        "integrationAuthority": authority,
        "organizationalRepair": _evidence(),
        "result": {"state": "organizational-completion-gate-failed"},
        "status": "cancelled",
        "cancelRequested": True,
        "finishedAt": "2026-08-25T00:00:00+00:00",
        "queuedAt": "2026-08-24T00:00:00+00:00",
        "integrationPublication": None,
    }
    fields.update(overrides)
    return _value(**fields)


def _contract(**overrides: object) -> Any:
    fields: dict[str, object] = {
        "contract_path": Path("/tmp/contract.json"),
        "task_id": "task-id",
        "task_name": "task-name",
        "memory_mode": "external",
        "code_base_commit": "code-base",
        "memory_base_commit": "memory-base",
        "code_commit": "code",
        "memory_content_commit": "memory",
        "ledger_commit": "ledger",
        "kind": "leaf",
        "closeout_status": "completed",
        "integration_status": "not-started",
        "approved_for_commit": True,
        "closeout_door": None,
        "coordination_root": Path("/tmp/coordination"),
        "repo_name": "agents-remember",
        "code_repo_path": Path("/tmp/code"),
        "memory_repo_path": Path("/tmp/memory"),
    }
    fields.update(overrides)
    return _value(**fields)


def test_repair_scalar_and_evidence_validators_are_total() -> None:
    assert repair._required_next_action("cancel") == "cancel"
    assert repair._publication_failure(None) == {}
    assert repair._publication_failure({"stage": "write"}) == {
        "publicationFailure": {"stage": "write"}
    }
    with pytest.raises(TypeError, match="next_action"):
        repair._required_next_action(None)
    with pytest.raises(TypeError, match="publication_failure"):
        repair._publication_failure("invalid")

    record = _record()
    evidence = record.organizationalRepair
    repair._require_failed_gate_result(record)
    assert repair._required_repair_evidence(record) is evidence
    repair._require_matching_repair_commits(("code", "memory", "ledger"), evidence)
    repair._require_repair_evidence(record, evidence)

    sprint = TaskDocumentRef(repository="agents-remember", path="sprint/task.json")
    candidate = TaskDocumentRef(repository="agents-remember", path="sprint/leaf.json")
    repair._require_matching_repair_binding(
        sprint, candidate, "agents-remember/sprint/master.json", evidence
    )

    invalid_calls = (
        lambda: repair._require_failed_gate_result(_record(result={})),
        lambda: repair._required_repair_evidence(_record(organizationalRepair=None)),
        lambda: repair._require_matching_repair_commits(("other", "memory", "ledger"), evidence),
        lambda: repair._require_matching_repair_binding(sprint, candidate, "other", evidence),
        lambda: repair._require_repair_evidence(record, _evidence(taskName="other")),
    )
    for call in invalid_calls:
        with pytest.raises(CloseoutQueueError):
            call()


def test_operation_authority_validation_covers_both_memory_modes() -> None:
    contract = _contract()
    authority = _authority()
    record = _record(integrationAuthority=authority)
    code = IntegrationTarget("code", "sprint-super", Path("/tmp/code"), "super", "owner")
    memory = IntegrationTarget(
        "memory", "sprint-super", Path("/tmp/memory"), "memory-super", "owner"
    )
    with mock.patch.object(repair, "integration_targets", return_value=(code, memory)):
        assert repair._require_operation_identity(contract, record) is authority

    code_only_authority = _authority(
        memoryRepository="",
        memorySourceBranch="",
        memorySourceRef="",
        memorySourceCommit="",
        memoryContentCommit="",
        ledgerCommit="",
    )
    code_only = _contract(memory_mode="disabled")
    with mock.patch.object(repair, "integration_targets", return_value=(code,)):
        assert (
            repair._require_operation_identity(
                code_only, _record(integrationAuthority=code_only_authority)
            )
            is code_only_authority
        )

    invalid_calls = (
        lambda: repair._require_base_operation_identity(
            contract, _record(operationKind="closeout")
        ),
        lambda: repair._require_code_operation_authority(
            contract,
            authority,
            IntegrationTarget("code", "sprint-super", Path("/tmp/code"), "other", "owner"),
        ),
        lambda: repair._require_memory_operation_authority(contract, authority, None),
        lambda: repair._require_no_memory_operation_authority(authority),
    )
    for call in invalid_calls:
        with pytest.raises(CloseoutQueueError):
            call()


def test_contract_and_source_preconditions_cover_success_and_refusal() -> None:
    contract = _contract()
    expected = ("code", "memory", "ledger")
    repair._require_reopenable_contract(contract, expected)
    with pytest.raises(CloseoutQueueError, match="closed, unintegrated"):
        repair._require_reopenable_contract(_contract(approved_for_commit=False), expected)

    assert repair._successor_repair_door(None, _record()) is None
    with pytest.raises(CloseoutQueueError, match="claimed"):
        repair._successor_repair_door(_value(disposition="waiting"), _record())
    claimed = _value(disposition="claimed")
    successor = _value(disposition="waiting")
    with mock.patch.object(repair, "successor_waiting_door", return_value=successor) as create:
        assert repair._successor_repair_door(claimed, _record()) is successor
    create.assert_called_once_with(
        claimed,
        declared_by="organizational-quality-repair",
        declared_at="2026-08-24T00:00:00+00:00",
    )

    code = IntegrationTarget("code", "sprint-super", Path("/tmp/code"), "super", "owner")
    memory = IntegrationTarget(
        "memory", "sprint-super", Path("/tmp/memory"), "memory-super", "owner"
    )
    with mock.patch.object(
        repair,
        "branch_commit",
        side_effect=("code-base", "memory-base"),
    ):
        repair._require_code_source_unmoved(contract, code)
        repair._require_memory_source_unmoved(contract, memory)
    with mock.patch.object(repair, "branch_commit", return_value="moved"):
        with pytest.raises(CloseoutQueueError, match="code super moved"):
            repair._require_code_source_unmoved(contract, code)
        with pytest.raises(CloseoutQueueError, match="memory super moved"):
            repair._require_memory_source_unmoved(contract, memory)
    with pytest.raises(CloseoutQueueError, match="memory super moved"):
        repair._require_memory_source_unmoved(_contract(memory_repo_path=None), None)


def test_publication_helpers_distinguish_reset_interruption_and_conflict() -> None:
    contract = _contract()
    reset = _contract(contract_path=Path("/tmp/reset.json"))
    evidence = _evidence()
    accepted = repair.OrganizationalRepairState("accepted", {}, {})
    published = repair.OrganizationalRepairState("reset", {}, {})
    conflict = repair.OrganizationalRepairState("developer-decision", {}, {})
    assert not repair._reset_already_published(accepted, evidence=evidence, observed=contract)
    assert repair._reset_already_published(published, evidence=evidence, observed=contract)
    with (
        mock.patch.object(
            repair, "_classify_organizational_repair_evidence", return_value=conflict
        ),
        pytest.raises(repair.OrganizationalRepairPublicationError),
    ):
        repair._reset_already_published(conflict, evidence=evidence, observed=contract)

    with mock.patch.object(repair, "write_contract") as write:
        repair._write_reset_contract(contract, reset, evidence)
    write.assert_called_once_with(reset.contract_path, reset)
    with (
        mock.patch.object(repair, "write_contract", side_effect=OSError("cut")),
        mock.patch.object(repair, "_raise_reset_write_failure") as translate,
    ):
        repair._write_reset_contract(contract, reset, evidence)
    translate.assert_called_once()

    with (
        mock.patch.object(repair, "load_contract", return_value=reset),
        mock.patch.object(repair, "_quality_repair_is_complete", return_value=True),
    ):
        repair._raise_reset_write_failure(contract, evidence, OSError("cut"))
        repair._require_published_reset(reset, evidence)

    with (
        mock.patch.object(repair, "load_contract", return_value=contract),
        mock.patch.object(repair, "_quality_repair_is_complete", return_value=False),
        mock.patch.object(repair, "_contract_sha256", return_value="accepted"),
        mock.patch.object(
            repair, "_classify_organizational_repair_evidence", return_value=accepted
        ),
        pytest.raises(repair.OrganizationalRepairPublicationError, match="unchanged"),
    ):
        repair._raise_reset_write_failure(contract, evidence, OSError("cut"))
    with (
        mock.patch.object(repair, "load_contract", return_value=contract),
        mock.patch.object(repair, "_quality_repair_is_complete", return_value=False),
        mock.patch.object(repair, "_contract_sha256", return_value="third"),
        mock.patch.object(
            repair, "_classify_organizational_repair_evidence", return_value=conflict
        ),
        pytest.raises(repair.OrganizationalRepairPublicationError, match="third byte state"),
    ):
        repair._raise_reset_write_failure(contract, evidence, RuntimeError("cut"))
    with (
        mock.patch.object(repair, "_quality_repair_is_complete", return_value=False),
        mock.patch.object(
            repair, "_classify_organizational_repair_evidence", return_value=conflict
        ),
        pytest.raises(repair.OrganizationalRepairPublicationError, match="journaled bytes"),
    ):
        repair._require_published_reset(contract, evidence)


def test_prepare_and_publish_orchestrate_the_exact_durable_generation() -> None:
    contract = _contract()
    reset = _contract(contract_path=contract.contract_path)
    record = _record()
    evidence = record.organizationalRepair
    authority = record.integrationAuthority
    sprint = TaskDocumentRef(repository="agents-remember", path="sprint/task.json")
    candidate = TaskDocumentRef(repository="agents-remember", path="sprint/leaf.json")
    accepted = repair.OrganizationalRepairState("accepted", {}, {})

    store = _value(read=lambda: record)
    with mock.patch.object(repair, "located_lifecycle_operation_store", return_value=store):
        assert repair._durable_cancelled_repair_record(contract) is record
    with (
        mock.patch.object(
            repair, "located_lifecycle_operation_store", return_value=_value(read=lambda: None)
        ),
        pytest.raises(CloseoutQueueError, match="durable cancelled"),
    ):
        repair._durable_cancelled_repair_record(contract)

    with (
        mock.patch.object(repair, "_durable_cancelled_repair_record", return_value=record),
        mock.patch.object(
            repair, "classify_organizational_completion_repair", return_value=accepted
        ),
        mock.patch.object(repair, "_require_operation_identity", return_value=authority),
        mock.patch.object(
            repair,
            "_repair_binding",
            return_value=(sprint, candidate, "agents-remember/sprint/master.json"),
        ),
        mock.patch.object(repair, "operation_state_fingerprint", return_value="candidate"),
        mock.patch.object(repair, "_quality_repair_contract", return_value=reset),
        mock.patch.object(repair, "_quality_repair_is_complete", return_value=True),
        mock.patch.object(repair, "_publish_reset") as publish,
        mock.patch.object(repair, "load_contract", return_value=reset),
    ):
        assert repair.prepare_organizational_completion_repair(contract) is reset
    publish.assert_called_once_with(
        contract=contract, reset=reset, evidence=evidence, record=record
    )

    loads = iter((contract, reset))
    with (
        mock.patch.object(repair, "task_publication_lock", return_value=nullcontext()),
        mock.patch.object(repair, "load_contract", side_effect=lambda _path: next(loads)),
        mock.patch.object(repair, "require_unchanged_integration_refs"),
        mock.patch.object(
            repair, "_classify_organizational_repair_evidence", return_value=accepted
        ),
        mock.patch.object(repair, "_require_sources_unmoved"),
        mock.patch.object(repair, "_write_reset_contract") as write,
        mock.patch.object(repair, "_require_published_reset") as require_published,
    ):
        repair._publish_reset(contract=contract, reset=reset, evidence=evidence, record=record)
    write.assert_called_once_with(contract, reset, evidence)
    require_published.assert_called_once_with(reset, evidence)


def test_repair_binding_requires_the_exact_claimed_task_topology() -> None:
    sprint = TaskDocumentRef(repository="agents-remember", path="sprint/task.json")
    master = TaskDocumentRef(repository="agents-remember", path="sprint/master.json")
    candidate = TaskDocumentRef(repository="agents-remember", path="sprint/leaf.json")
    door = _value(
        disposition="claimed",
        taskDocumentRef=candidate,
        owningMasterTaskDocumentRef=master,
        sprintTaskDocumentRef=sprint,
    )
    contract = _contract(closeout_door=door)

    with mock.patch.object(repair, "TaskDocumentTopology") as topology_type:
        topology_type.return_value.parent.side_effect = (master, sprint)
        assert repair._repair_binding(contract) == (sprint, candidate, master.key)

    with mock.patch.object(repair, "TaskDocumentTopology") as topology_type:
        topology_type.return_value.parent.return_value = None
        with pytest.raises(CloseoutQueueError, match="owning master"):
            repair._repair_binding(contract)

    other = TaskDocumentRef(repository="agents-remember", path="other/task.json")
    with mock.patch.object(repair, "TaskDocumentTopology") as topology_type:
        topology_type.return_value.parent.side_effect = (master, other)
        with pytest.raises(CloseoutQueueError, match="owning master"):
            repair._repair_binding(contract)

    assert repair._required_claimed_door(door) is door
    for invalid in (None, _value(disposition="waiting")):
        with pytest.raises(CloseoutQueueError, match="claimed door candidate"):
            repair._required_claimed_door(invalid)


def test_reset_construction_and_source_checks_preserve_one_exact_generation() -> None:
    contract = _contract()
    record = _record()
    expected = ("code", "memory", "ledger")
    successor = _value(disposition="waiting")
    replaced = _value(closeout_door=successor)
    amended = _value(state="reset")
    with (
        mock.patch.object(repair, "_require_reopenable_contract") as require,
        mock.patch.object(repair, "_successor_repair_door", return_value=successor) as door,
        mock.patch.object(repair, "replace", return_value=replaced) as replace_contract,
        mock.patch.object(repair, "amend_contract", return_value=amended) as amend,
    ):
        assert (
            repair._quality_repair_contract(
                contract,
                expected_commits=expected,
                repair_record=record,
            )
            is amended
        )
    require.assert_called_once_with(contract, expected)
    door.assert_called_once_with(contract.closeout_door, record)
    replace_contract.assert_called_once()
    amend.assert_called_once()

    code = IntegrationTarget("code", "sprint-super", Path("/tmp/code"), "super", "owner")
    memory = IntegrationTarget(
        "memory", "sprint-super", Path("/tmp/memory"), "memory-super", "owner"
    )
    with (
        mock.patch.object(repair, "integration_targets", return_value=(code, memory)),
        mock.patch.object(repair, "_require_code_source_unmoved") as require_code,
        mock.patch.object(repair, "_require_memory_source_unmoved") as require_memory,
    ):
        repair._require_sources_unmoved(contract)
        repair._require_sources_unmoved(_contract(memory_mode="disabled"))
    assert require_code.call_count == 2
    require_memory.assert_called_once_with(contract, memory)


def test_publishing_an_already_reset_generation_is_idempotent() -> None:
    contract = _contract()
    evidence = _evidence()
    record = _record()
    reset_state = repair.OrganizationalRepairState("reset", {}, {})
    with (
        mock.patch.object(repair, "task_publication_lock", return_value=nullcontext()),
        mock.patch.object(repair, "load_contract", return_value=contract) as load,
        mock.patch.object(repair, "require_unchanged_integration_refs"),
        mock.patch.object(
            repair, "_classify_organizational_repair_evidence", return_value=reset_state
        ),
        mock.patch.object(repair, "_require_sources_unmoved") as require_sources,
        mock.patch.object(repair, "_write_reset_contract") as write,
    ):
        repair._publish_reset(
            contract=contract,
            reset=contract,
            evidence=evidence,
            record=record,
        )
    load.assert_called_once_with(contract.contract_path)
    require_sources.assert_not_called()
    write.assert_not_called()
