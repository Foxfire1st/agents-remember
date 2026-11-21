"""The operation journal keeps only direct landings.

A closeout and an integration now run inside their own call and record nothing, so the
journal's only writer is a direct landing. Two facts hold for the records an older build
left behind: they still parse, and a control call addressed to one is refused with the
call that replaces it instead of offering a control that cannot run.

A leftover closeout record carries its own accepted input, so it cannot be produced from
a direct-landing record in a fixture; the refusal is checked on the seam that reads the
record's kind, and the legacy reader on the payload an older build wrote.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.worktree_tool_requests import OperationControlRequest
from agents_remember.application.worktree_tools import worktree_operation_control_tool
from agents_remember.mcp.tools.worktree import (
    worktree_operation_control_payload,
    worktree_status_payload,
)
from agents_remember.models.closeout.input import CloseoutCorrectedCall
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    LifecycleOperationKind,
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.preparation_state import SelectedPreparation
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.closeout_input import (
    normalize_closeout_input,
    raw_closeout_messages,
)
from agents_remember.worktrees.integration.closeout.task_intent_identity import (
    contract_task_intent,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    _refuse_retired_generation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.git import branch_commit
from test_completed_direct_landing_disposition import _interrupted, _landed
from test_direct_landing import _converted_series_fixture

RETIRED_WORKER_KEYS = frozenset(
    {
        "workerPid",
        "workerLease",
        "workerProcessFingerprint",
        "workerTermination",
        "workerTerminationHistory",
        "terminationReturnStatus",
        "terminationReturnPhase",
    }
)


def _journal_record(tmp_path: Path) -> LifecycleOperationRecord:
    """The direct landing record a converted fixture's journal holds."""

    fixture = _landed(tmp_path)
    store = LifecycleOperationStore(
        operation_record_path(fixture["contract"].worktree_group, "direct-landing")
    )
    record = store.read()
    assert record is not None
    return record


def test_a_record_with_retired_worker_authority_still_parses(tmp_path: Path) -> None:
    """An older build's seven worker fields are dropped, so the record still parses."""

    record = _journal_record(tmp_path)
    payload: dict[str, Any] = record.model_dump(mode="json")
    payload.update(
        {
            "workerPid": 4242,
            "workerLease": {"operationKey": "key", "generation": 1, "token": "t"},
            "workerProcessFingerprint": "f" * 64,
            "workerTermination": {"state": "exited", "pid": 4242},
            "workerTerminationHistory": [{"state": "exited", "pid": 4242}],
            "terminationReturnStatus": "exited",
            "terminationReturnPhase": "postflight",
        }
    )

    parsed = LifecycleOperationRecord.model_validate(payload)
    dumped = parsed.model_dump(mode="json")

    assert not (RETIRED_WORKER_KEYS & dumped.keys())
    assert parsed.operationKey == record.operationKey
    assert parsed.generation == record.generation
    assert parsed.status == record.status


def test_the_legacy_reader_drops_a_stored_preparation_command() -> None:
    """The retired preparation command sat inside a leg; the reader drops it there."""

    cleaned = cast(Any, LifecycleOperationRecord)._drop_retired_operation_authority(
        {
            "workerPid": 4242,
            "preparation": {
                "legs": [
                    {
                        "leg": "code",
                        "commands": [{"kind": "closeout", "arguments": {}, "issuedAt": None}],
                    }
                ]
            },
        }
    )

    assert "workerPid" not in cleaned
    assert cleaned["preparation"]["legs"][0] == {"leg": "code"}


@pytest.mark.parametrize(
    ("kind", "next_tool"),
    (("closeout", "worktree_closeout_apply"), ("integrate", "worktree_integrate")),
)
def test_a_closeout_or_integration_record_names_the_tool_that_replaces_it(
    kind: str, next_tool: str
) -> None:
    """The refusal names the exact record and the call that replaces it."""

    record = LifecycleOperationRecord.model_construct(
        operationKind=kind,
        generation=3,
        status="running",
        contractPath="/old-coordination/leaf/leaf.json",
    )

    with pytest.raises(LifecycleControlError) as caught:
        _refuse_retired_generation(record, contract_path="/coordination/leaf/leaf.json")

    refused = caught.value
    assert refused.status == "lifecycle-operation-retired"
    assert refused.next_tool == next_tool
    assert refused.next_args["contract_path"] == "/coordination/leaf/leaf.json"
    assert refused.observed["generation"] == 3
    assert refused.observed["operationKind"] == kind
    assert next_tool in str(refused)


def test_a_direct_landing_generation_is_not_refused() -> None:
    """The journal's only remaining writer keeps its controls."""

    record = LifecycleOperationRecord.model_construct(operationKind="direct-landing")

    _refuse_retired_generation(record, contract_path="/coordination/leaf/leaf.json")


def _seeded_generation(fixture: dict, kind: LifecycleOperationKind) -> tuple[Any, int]:
    """Write the record an older build left for a closeout or integration generation."""

    contract = fixture["contract"]
    if kind == "closeout":
        accepted = CloseoutOperationInput(
            configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
            contractPath=contract.contract_path.as_posix(),
            effectiveInput=normalize_closeout_input(
                contract,
                raw_closeout_messages(code="close code candidate", memory="close external memory"),
                route="worktree",
                corrected_call=CloseoutCorrectedCall(
                    tool="worktree_closeout_apply",
                    arguments={"contract_path": contract.contract_path.as_posix()},
                ),
            ),
            approvalNote="developer approved this exact candidate",
        )
    else:
        accepted = IntegrateOperationInput(
            configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
            contractPath=contract.contract_path.as_posix(),
        )
    record = LifecycleOperationRecord(
        taskId=contract.leaf_id or "LEAF",
        taskName=str(contract.worktree_group),
        queuedAt="2026-08-01T00:00:00+00:00",
        reportPath=(contract.worktree_group / "reports" / f"{kind}-operation.log").as_posix(),
        contractPath=contract.contract_path.as_posix(),
        operationKind=kind,
        candidateState="a" * 64,
        fingerprint="b" * 64,
        operationKey="c" * 64,
        generation=1,
        input=accepted,
        taskIntent=(TaskIntentIdentity(digest="d" * 64) if kind == "closeout" else None),
        integrationAuthority=(
            IntegrationOperationAuthority(
                targetKind="sprint-super",
                codeRepository=contract.code_repo_path.as_posix(),
                codeSourceBranch=contract.code_source_branch,
                codeSourceRef=f"refs/heads/{contract.code_source_branch}",
                codeSourceCommit=branch_commit(
                    contract.code_repo_path, contract.code_source_branch
                ),
                codeCandidateCommit=branch_commit(
                    contract.code_repo_path, contract.code_source_branch
                ),
            )
            if kind == "integrate"
            else None
        ),
        status="running",
        phase="preflight",
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, kind))
    store.create(record)
    return fixture, record.generation


@pytest.mark.parametrize("action", ("retry", "recover", "resume"))
@pytest.mark.parametrize(
    ("kind", "next_tool"),
    (("closeout", "worktree_closeout_apply"), ("integrate", "worktree_integrate")),
)
def test_a_leftover_generation_refuses_every_control_through_the_public_tool(
    tmp_path: Path, kind: LifecycleOperationKind, next_tool: str, action: str
) -> None:
    """The mounted public control answers a closeout or integration generation a build left.

    The refusal travels as the tool's own payload, which is how every control refusal reaches
    a caller, so the assertion is on the public surface rather than on an internal exception.
    """

    fixture = _converted_series_fixture(tmp_path)
    _, generation = _seeded_generation(fixture, kind)

    payload = worktree_operation_control_tool(
        fixture["config"],
        OperationControlRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            operation_kind=kind,
            action=cast(Any, action),
            expected_generation=generation,
            intent_note=f"fixture {action} of a leftover {kind} generation",
        ),
    )

    assert payload["ok"] is False, payload
    assert payload["state"] == "refused", payload
    assert payload["status"] == "lifecycle-operation-retired", payload
    assert payload["nextTool"] == next_tool, payload
    assert payload["nextAction"] == "re-apply", payload
    assert payload["nextArgs"]["contract_path"] == fixture["contract"].contract_path.as_posix()
    if kind == "closeout":
        assert payload["nextArgs"]["intent_note"] == "<fresh developer intent>"
    assert payload["observed"]["operationKind"] == kind
    assert payload["observed"]["generation"] == generation
    assert next_tool in payload["detail"]


# The record the base build itself wrote, read from the project root so the evidence catalog can
# derive this fixture's consumer from the source.
OLD_WRITER_RECORD = Path("mcp/tests/fixtures/lifecycle/old-writer-closeout-record.json")
OLD_WRITER_RECORD_SHA256 = "2a6312ce95b2a50d6ce81ae1974121565e56d2ca0d3f0affaab283166cb5988d"
OLD_WRITER_INTEGRATE_RECORD = Path("mcp/tests/fixtures/lifecycle/old-writer-integrate-record.json")
OLD_WRITER_INTEGRATE_RECORD_SHA256 = (
    "d73d1548c1028a98052c4e9f5906f70e0f0e012c4796622bf66b51f929b79d44"
)

OLD_WRITER_COMMAND = {
    "kind": "create",
    "cwd": "/coordination/worktrees/leaf/code",
    "argv": ["git", "commit", "-m", "prepared code"],
    "workerPid": 4242,
    "workerLease": "e" * 64,
    "workerProcessFingerprint": "f" * 64,
    "startedAt": "2026-08-01T00:00:00+00:00",
    "terminal": {"outcome": "succeeded", "observedAt": "2026-08-01T00:00:05+00:00"},
}

OLD_WRITER_WORKER_MEMBERS = {
    "workerPid": 4242,
    "workerLease": "e" * 64,
    "workerProcessFingerprint": "f" * 64,
    "workerTermination": {"state": "exited", "pid": 4242},
    "workerTerminationHistory": [{"state": "exited", "pid": 4242}],
    "terminationReturnStatus": "exited",
    "terminationReturnPhase": "postflight",
}


def _reference(kind: str, digest: str) -> dict[str, object]:
    return {"kind": kind, "semanticDigest": digest, "contentSha256": digest, "sizeBytes": 1}


def _old_writer_leg() -> dict[str, Any]:
    """A complete older closeout leg: an output with its three recorded commands."""

    return {
        "leg": "code",
        "intent": _reference("preparation-intent", "a" * 64),
        "output": _reference("prepared-output", "b" * 64),
        "commands": [
            {**OLD_WRITER_COMMAND, "kind": "create"},
            {**OLD_WRITER_COMMAND, "kind": "materialize"},
            {**OLD_WRITER_COMMAND, "kind": "commit"},
        ],
    }


def _old_writer_payload(record: LifecycleOperationRecord) -> dict[str, Any]:
    """The bytes an older writer emitted for this generation, retired members included."""

    payload: dict[str, Any] = record.model_dump(mode="json")
    payload.update(OLD_WRITER_WORKER_MEMBERS)
    return payload


def test_the_old_command_bearing_leg_is_read_and_dropped() -> None:
    """A complete older leg with its three commands loads, and loses only ``commands``.

    The base model accepts exactly this leg (evidence/F10-base-fcb1e364-probe.txt, section 2:
    an output with three recorded commands is valid, with one or two it is refused). The current
    reader must keep the intent and the output and drop the command bodies.
    """

    old_leg = _old_writer_leg()
    assert [command["kind"] for command in old_leg["commands"]] == [
        "create",
        "materialize",
        "commit",
    ]

    cleaned = cast(Any, LifecycleOperationRecord)._drop_retired_operation_authority(
        {"preparation": {"legs": [old_leg]}}
    )
    leg = SelectedPreparation.model_validate(cleaned["preparation"]["legs"][0])

    assert leg.leg == "code"
    assert leg.output is not None and leg.output.kind == "prepared-output"
    assert not hasattr(leg, "commands")


def test_an_old_writer_closeout_record_reaches_the_replacement_route(tmp_path: Path) -> None:
    """Old bytes load through the reader, the store and the public control, and stay for audit."""

    fixture = _converted_series_fixture(tmp_path)
    _, generation = _seeded_generation(fixture, "closeout")
    contract = fixture["contract"]
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    seeded = store.read()
    assert seeded is not None

    old_bytes = _old_writer_payload(seeded)
    # The older writer wrote the raw record; a current reader has to accept exactly those bytes.
    store.path.write_text(json.dumps(old_bytes, indent=2), encoding="utf-8")

    reread = store.read()
    assert reread is not None
    dumped = reread.model_dump(mode="json")
    assert not (RETIRED_WORKER_KEYS & dumped.keys())
    assert reread.preparation is None

    for action in ("retry", "recover", "resume"):
        payload = worktree_operation_control_tool(
            fixture["config"],
            OperationControlRequest(
                contract_path=contract.contract_path.as_posix(),
                operation_kind="closeout",
                action=cast(Any, action),
                expected_generation=generation,
                intent_note=f"old writer record, {action}",
            ),
        )
        assert payload["ok"] is False, payload
        assert payload["status"] == "lifecycle-operation-retired", payload
        assert payload["nextTool"] == "worktree_closeout_apply", payload
        assert payload["observed"]["operationKind"] == "closeout"

    # Audit is preserved: the record still reads, and it offers no control.
    assert store.read() is not None
    assert legal_operation_controls(contract, reread) == []


def test_the_retained_old_build_record_loads_with_its_command_leg(tmp_path: Path) -> None:
    """One complete older-build closeout record, command leg and all, through the successor.

    The fixture is a record the base build (``fcb1e3645``) itself produced: every preparation step
    passed that build's ``validate_preparation_transition`` and the finished record passed that
    build's ``LifecycleOperationRecord``, so these are bytes the older writer could persist. Nothing
    here rewrites them; the successor has to accept exactly what the older build wrote.
    """

    raw = OLD_WRITER_RECORD.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == OLD_WRITER_RECORD_SHA256
    written = json.loads(raw)
    leg_written = written["preparation"]["legs"][0]
    assert [command["kind"] for command in leg_written["commands"]] == [
        "create",
        "materialize",
        "commit",
    ]
    assert all(command["terminal"]["outcome"] == "succeeded" for command in leg_written["commands"])
    assert written["certification"] is not None

    fixture = _converted_series_fixture(tmp_path)
    contract = fixture["contract"]
    path = operation_record_path(contract.worktree_group, "closeout")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    store = LifecycleOperationStore(path)

    record = store.read()
    assert record is not None
    assert record.generation == written["generation"]
    assert record.preparation is not None
    leg = record.preparation.legs[0]
    # The reader keeps the intent and the prepared output and drops the command bodies.
    assert leg.leg == "code"
    assert leg.output is not None
    assert not hasattr(leg, "commands")

    for action in ("retry", "recover", "resume"):
        payload = worktree_operation_control_tool(
            fixture["config"],
            OperationControlRequest(
                contract_path=contract.contract_path.as_posix(),
                operation_kind="closeout",
                action=cast(Any, action),
                expected_generation=written["generation"],
                intent_note=f"retained old build record, {action}",
            ),
        )
        assert payload["ok"] is False, payload
        assert payload["status"] == "lifecycle-operation-retired", payload
        assert payload["nextTool"] == "worktree_closeout_apply", payload
        assert payload["observed"]["generation"] == written["generation"]

    # Audit inspection survives the refusal: the same bytes still read, and offer no control.
    assert store.read() is not None
    assert legal_operation_controls(contract, record) == []


@pytest.mark.integration
def test_registered_status_keeps_retired_history_and_direct_landing_controls(
    tmp_path: Path,
) -> None:
    fixture = _interrupted(tmp_path)
    config = fixture["config"]
    contract = fixture["contract"]
    contract_path = contract.contract_path.as_posix()
    task = TaskRef(repo_id=contract.repo_name, contract_path=contract_path)
    direct_path = operation_record_path(contract.worktree_group, "direct-landing")
    # The fixture reaches a real recovery-required state. Seed the separate task-intent
    # stamp the status reader requires to publish controls on a stored direct landing.
    direct_record = json.loads(direct_path.read_bytes())
    intent = contract_task_intent(contract, candidate_ref=fixture["leaf_ref"])
    direct_record["taskIntent"] = intent.model_dump(mode="json")
    direct_bytes = LifecycleOperationRecord.model_validate(direct_record).model_dump_json().encode()
    direct_path.write_bytes(direct_bytes)
    with tool_call_context("worktree_status", {"contract_path": contract_path}, config=config):
        before = worktree_status_payload(config, task)
        baseline = json.loads(Path(before["log"]).read_text(encoding="utf-8"))
        assert baseline["lifecycleOperationCount"] == 1
        direct = baseline["lifecycleOperations"][0]
        assert direct["kind"] == "direct-landing"
        assert direct["status"] == "input-required"
        assert direct["recommendedAction"]["action"] == "recover"
        assert [control["action"] for control in direct["legalControls"]] == ["recover"]

        journals: dict[LifecycleOperationKind, tuple[Path, str, str]] = {
            "closeout": (
                OLD_WRITER_RECORD,
                "running",
                "preflight",
            ),
            "integrate": (
                OLD_WRITER_INTEGRATE_RECORD,
                "termination-required",
                "termination-required",
            ),
        }
        for kind, (source, _, _) in journals.items():
            path = operation_record_path(contract.worktree_group, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.read_bytes())

        answer = worktree_status_payload(config, task)
        whole = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))

    assert answer["ok"], answer
    assert "runningOperation" not in answer
    assert answer["lifecycleOperationCount"] == 3
    operations = {operation["kind"]: operation for operation in whole["lifecycleOperations"]}
    assert operations["direct-landing"]["recommendedAction"] == direct["recommendedAction"]
    assert operations["direct-landing"]["legalControls"] == direct["legalControls"]
    for kind, (source, status, phase) in journals.items():
        recorded = json.loads(source.read_bytes())
        projection = operations[kind]
        assert (projection["status"], projection["phase"]) == (status, phase), projection
        assert projection["generation"] == recorded["generation"]
        assert projection["identity"]["recordRevision"] == recorded["recordRevision"]
        assert projection["legalControls"] == []
        assert "recommendedAction" not in projection
        assert "guidance" not in projection
        assert projection["cancellable"] is False
        path = operation_record_path(contract.worktree_group, kind)
        assert path.read_bytes() == source.read_bytes()

    assert direct_path.read_bytes() == direct_bytes
    control = operations["direct-landing"]["legalControls"][0]
    assert control["tool"] == "worktree_operation_control"
    recovered = worktree_operation_control_payload(
        config, OperationControlRequest(**control["arguments"])
    )
    assert recovered["ok"], recovered
    completed = LifecycleOperationStore(direct_path).read()
    assert completed is not None and (completed.status, completed.generation) == (
        "completed",
        direct["generation"],
    )
