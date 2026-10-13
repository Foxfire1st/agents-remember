"""Completed and interrupted direct landings through the lifecycle control.

MIK-R143: retire is case (a) — the owner of a completed direct landing
retires it, ownership decided on what ``direct_landing_record`` writes by one
predicate shared by the projection's offer and the check's admission (rules 2,
3a). Supersede is case (b): a direct landing claims no door, so supersede is
neither offered nor admitted, and its refusal names a new ``direct_landing``
call (rule 3, ruling ``notes/run3/L143-A-DIRECT-LANDING-IS-RETIRED-BY-ITS-
OWNER-AND-NEVER-SUPERSEDED-BECAUSE-IT-HAS-NO-DOOR-RULING.json`` e55c0093,
which withdraws point 2 of ruling 08236bba). An interrupted direct landing is
continued by the control's recover on what the record carries, with the kept
closing receipt settled in the same call (rule 3b, first branch).

Completed-case tests run on the plain series fixture (no invariant gate); the
interrupted case runs converted, where the kept closing receipt exists.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.direct_execution_grants import give_grant
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.application.worktree_tool_requests import OperationControlRequest
from agents_remember.application.worktree_tools import worktree_operation_control_tool
from agents_remember.models.closeout.source import (
    CandidateAdmissionFacts,
    SchedulingGradeInput,
)
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.door import (
    CloseoutDoorGeneration,
    DoorPublicationEvidence,
)
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    IntegrationPublicationIntent,
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees import direct_landing as direct_landing_owner
from agents_remember.worktrees.direct_landing import DirectLandingRequest
from agents_remember.worktrees.integration.direct_landing import direct_landing_execution
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    direct_landing_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_completed_disposition import (
    require_completed_disposition,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    LifecycleControlProjectionContext,
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    completed_direct_owner,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.git import branch_commit, head_commit, require_git
from test_direct_landing import (
    _converted_request,
    _converted_series_fixture,
    _kept_closings,
    _series_fixture,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    TRACES,
    A,
    _answered,
    commit,
    family_row,
    trace_rows,
    write,
)
from test_worktree_support import git

MASTER_REF = TaskDocumentRef(repository="repo-a", path="direct-task/task.json")


def _plain_landing_request(fixture: dict, code_commit: str) -> DirectLandingRequest:
    return DirectLandingRequest(
        contract_path=fixture["contract"].contract_path.as_posix(),
        code_commit=code_commit,
        candidate_tree=require_git(fixture["code"], ["rev-parse", f"{code_commit}^{{tree}}"]),
        memory_commit_message="publish plain tree",
        intent_note="fixture approval",
        leaf_task_document_ref=fixture["leaf_ref"],
    )


def _landed(tmp_path: Path, name: str = "landed") -> dict:
    """A plain series fixture with one completed direct landing."""

    fixture = _series_fixture(tmp_path / name)
    bind_worktree_services(build_default_worktree_services())
    landed = direct_landing_tool(
        fixture["config"], _plain_landing_request(fixture, fixture["code_head"])
    )
    assert landed["ok"], landed
    return fixture


def _with_disposition_topology(fixture: dict) -> None:
    """A minimal standalone master so disposition authority resolves to the
    fixture's architect; it keeps the granted leaf listed (L94 bound leaf)."""

    write_task_doc(
        fixture["contract"].task_root,
        TaskDocument.model_validate(
            {
                "id": "DIRECT-TASK",
                "slug": "direct-task",
                "title": "Direct task",
                "kind": "master",
                "status": "inProgress",
                "repo": fixture["contract"].repo_name,
                "createdAt": "2026-08-22T00:00:00+00:00",
                "subTasks": [
                    SubTaskRef(
                        number=LEAF,
                        name="Direct leaf",
                        file="leaf.md",
                        status="planning",
                    ).model_dump(mode="json"),
                ],
            }
        ),
    )


def _owner() -> DeclaredCaller:
    return DeclaredCaller(role="architect", task_document_ref=MASTER_REF)


def _stranger() -> DeclaredCaller:
    return DeclaredCaller(role="worker", task_document_ref=MASTER_REF)


def _grade() -> SchedulingGradeInput:
    return SchedulingGradeInput(priority="normal", judgmentId="TEST-FIXTURE-GRADE")


def _control(
    fixture: dict,
    action: str,
    *,
    caller: DeclaredCaller | None = None,
    with_source: bool = False,
    expected_generation: int = 1,
) -> dict:
    grade = _grade() if with_source else None
    admission = CandidateAdmissionFacts() if with_source else None
    return worktree_operation_control_tool(
        fixture["config"],
        OperationControlRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            operation_kind="direct-landing",
            action=action,  # type: ignore[arg-type]
            expected_generation=expected_generation,
            intent_note=f"fixture {action} of the direct landing",
            grade=grade,
            admission=admission,
            caller=caller,
        ),
    )


def _advance_plain_code(fixture: dict) -> str:
    code = fixture["code"]
    git(code, "checkout", "ar/direct-task")
    (code / "feature.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-m", "advance code past the landing")
    return git(code, "rev-parse", "HEAD")


def test_owner_retire_admitted_and_supersede_refused_at_check(
    tmp_path: Path,
) -> None:
    """Evidence 1 (MIK-R143 rules 2, 3): the exact owner of a completed direct
    landing — which carries no door publication — is admitted for retire, and a
    supersede is refused in words that name a new direct_landing call."""

    fixture = _landed(tmp_path)
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    assert record.status == "completed"
    assert record.doorPublication is None
    require_completed_disposition(fixture["contract"], record, "retire")
    with pytest.raises(LifecycleControlError) as refused:
        require_completed_disposition(fixture["contract"], record, "supersede")
    assert refused.value.status == "lifecycle-direct-supersede-unavailable"
    assert refused.value.next_action == "direct-landing"
    assert refused.value.next_tool == "direct_landing"
    assert "no door" in refused.value.detail
    assert "direct_landing" in refused.value.detail


def test_owner_offered_controls_match_admission(tmp_path: Path) -> None:
    """Evidence 3 (MIK-R143 rule 3a): the owner is offered retire and a
    stranger is offered nothing — the same predicate the check admits."""

    fixture = _landed(tmp_path)
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    owner_actions = {
        item["action"]
        for item in legal_operation_controls(
            fixture["contract"],
            record,
            context=LifecycleControlProjectionContext(allow_completed_disposition=True),
        )
    }
    assert owner_actions == {"retire"}
    stranger_actions = [
        item["action"]
        for item in legal_operation_controls(
            fixture["contract"],
            record,
            context=LifecycleControlProjectionContext(allow_completed_disposition=False),
        )
    ]
    assert stranger_actions == []


def test_owner_compared_values_each_refuse(tmp_path: Path) -> None:
    """Evidence 2 (MIK-R143 rule 2): a change of any one compared value leaves
    the owner unrecognised and the refusal is lifecycle-disposition-not-allowed."""

    fixture = _landed(tmp_path)
    contract = fixture["contract"]
    record = direct_landing_store(contract).read()
    assert record is not None
    assert completed_direct_owner(record) is True

    mutated = {
        "disposition": record.model_copy(update={"generationDisposition": "retired"}),
        "status": record.model_copy(update={"status": "running"}),
        "fingerprint": record.model_copy(update={"fingerprint": "c" * 64}),
        "key": record.model_copy(update={"operationKey": "d" * 64}),
    }
    for name, altered in mutated.items():
        assert completed_direct_owner(altered) is False, name
        with pytest.raises(LifecycleControlError) as refused:
            require_completed_disposition(contract, altered, "retire")
        assert refused.value.status == "lifecycle-disposition-not-allowed", name

    shifted = record.model_copy(update={"generation": record.generation + 1})
    assert completed_direct_owner(shifted, expected_generation=record.generation) is False
    assert completed_direct_owner(shifted) is True  # the control pins it upstream

    other_kind = record.model_copy()
    other_kind.operationKind = "closeout"  # a journal no reader validates, only the predicate
    assert completed_direct_owner(other_kind) is False


def test_direct_record_carrying_a_door_is_not_recognised(tmp_path: Path) -> None:
    """Ruling e55c0093 point 5: a stored direct landing of the earlier shape,
    which carries a door publication, is offered no control and is no error."""

    fixture = _landed(tmp_path)
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    for state in ("proven", "intent"):
        old_shape = record.model_copy()
        old_shape.doorPublication = DoorPublicationEvidence.model_construct(
            state=state,
            generation=CloseoutDoorGeneration.model_construct(
                disposition="claimed",
                operationKind="direct-landing",
                operationFingerprint=record.fingerprint,
                claimedOperationKey=record.operationKey,
            ),
        )
        assert completed_direct_owner(old_shape) is False, state
        with pytest.raises(LifecycleControlError) as refused:
            require_completed_disposition(fixture["contract"], old_shape, "retire")
        assert refused.value.status == "lifecycle-disposition-not-allowed", state
        for allow in (True, False):
            offered = legal_operation_controls(
                fixture["contract"],
                old_shape,
                context=LifecycleControlProjectionContext(allow_completed_disposition=allow),
            )
            assert [item["action"] for item in offered] == [], (state, allow)


def test_owner_retire_proof_through_public_tool(tmp_path: Path) -> None:
    """Ruling e55c0093 point 6: the owner retires a completed direct landing
    and its disposition is retired; a second retire answers the same; a
    stranger is refused; a supersede is refused with the text of point 3; and
    after the retire the next direct_landing with advanced code is accepted
    and completes."""

    fixture = _landed(tmp_path)
    _with_disposition_topology(fixture)
    owner, stranger = _owner(), _stranger()
    store = direct_landing_store(fixture["contract"])

    refused_supersede = _control(fixture, "supersede", caller=stranger, with_source=True)
    assert refused_supersede["ok"] is False
    assert refused_supersede["status"] == "lifecycle-disposition-caller-unauthorized"
    refused_retire = _control(fixture, "retire", caller=stranger)
    assert refused_retire["ok"] is False
    assert refused_retire["status"] == "lifecycle-disposition-caller-unauthorized"

    retired = _control(fixture, "retire", caller=owner)
    assert retired["ok"], retired
    record = store.read()
    assert record is not None
    assert record.generationDisposition == "retired"

    again = _control(fixture, "retire", caller=owner)
    assert again["ok"], again
    assert again["state"] == retired["state"]
    current = store.read()
    assert current is not None
    assert current.generationDisposition == "retired"

    stranger_after = _control(fixture, "retire", caller=stranger)
    assert stranger_after["ok"] is False
    assert stranger_after["status"] == "lifecycle-disposition-caller-unauthorized"

    no_door = _control(fixture, "supersede", caller=owner, with_source=True)
    assert no_door["ok"] is False
    assert no_door["status"] == "lifecycle-direct-supersede-unavailable"
    assert no_door["nextAction"] == "direct-landing"
    assert no_door["nextTool"] == "direct_landing"
    assert "no door" in str(no_door["detail"])
    assert "direct_landing" in str(no_door["detail"])

    advanced = _advance_plain_code(fixture)
    relanded = direct_landing_tool(fixture["config"], _plain_landing_request(fixture, advanced))
    assert relanded["ok"], relanded
    landed = store.read()
    assert landed is not None
    assert (landed.status, landed.generation) == ("completed", 2)


def _interrupted(tmp_path: Path, name: str = "interrupted") -> dict:
    """A converted landing interrupted after its first leg: the code commit is
    verified, the memory commit is journaled as intent but never proven."""

    fixture = _converted_series_fixture(tmp_path / name)
    world = fixture["world"]
    code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
    bind_worktree_services(build_default_worktree_services())
    with mock.patch.object(
        direct_landing_execution, "prove_git_commit", side_effect=RuntimeError("cut")
    ):
        interrupted = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
    assert interrupted["ok"] is False
    assert interrupted["status"] == "direct-landing-recovery-required"
    fixture["code_commit"] = code
    return fixture


def test_control_recovers_interrupted_direct_landing_through_public_tool(
    tmp_path: Path,
) -> None:
    """Evidence 4 / ruling 08236bba point 5: recover continues the interrupted
    generation itself to completion through the public control, the kept
    closing receipt is settled in that call, a second recover is refused
    because the generation is completed, and a stranger is refused.

    The second refusal is ``lifecycle-control-not-legal`` with the completed
    status observed: recover is offered only where the check behind it can
    admit it (rule 3a), so a completed generation offers no recover to refuse
    as terminal."""

    fixture = _interrupted(tmp_path)
    world = fixture["world"]
    store = direct_landing_store(fixture["contract"])
    record = store.read()
    assert record is not None
    assert (record.status, record.generationDisposition) == ("input-required", "active")

    continued = _control(fixture, "recover")
    assert continued["ok"], continued
    current = store.read()
    assert current is not None
    assert (current.status, current.generation) == ("completed", 1)
    assert current.operationKey == record.operationKey
    assert _kept_closings(fixture["contract"]) == []
    closed = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
    assert closed["closed"] is True
    assert git(world.memory, "status", "--porcelain") == ""

    again = _control(fixture, "recover")
    assert again["ok"] is False
    assert again["status"] == "lifecycle-control-not-legal"
    assert again["observed"]["status"] == "completed"

    stranger = _control(fixture, "recover", expected_generation=2)
    assert stranger["ok"] is False
    assert stranger["status"] == "lifecycle-generation-changed"


def test_direct_landing_refusals_that_name_recover_hold(tmp_path: Path) -> None:
    """Ruling 08236bba point 5, last line: the texts of ``direct_landing``'s
    own refusals that name recover stay true — a different request names
    recover first, and the control behind that name admits it."""

    fixture = _interrupted(tmp_path, "refusal-texts")
    conflicted = direct_landing_tool(
        fixture["config"],
        DirectLandingRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            code_commit=fixture["code_commit"],
            candidate_tree=require_git(
                fixture["world"].code,
                ["rev-parse", f"{fixture['code_commit']}^{{tree}}"],
            ),
            memory_commit_message="publish exact converted tree",
            intent_note="another intent",
            leaf_task_document_ref=fixture["leaf_ref"],
        ),
    )
    assert conflicted["ok"] is False
    assert conflicted["status"] == "direct-landing-input-conflict"
    assert "recover" in str(conflicted["detail"])


def _old_shape(record, *, state: str):
    """A stored direct landing of the earlier shape (finding L143-CODE-R1-F02)."""

    shaped = record.model_copy()
    shaped.doorPublication = DoorPublicationEvidence.model_construct(
        state=state,
        generation=CloseoutDoorGeneration.model_construct(
            disposition="claimed",
            operationKind="direct-landing",
            operationFingerprint=record.fingerprint,
            claimedOperationKey=record.operationKey,
        ),
    )
    return shaped


def test_old_shape_nonterminal_direct_is_offered_nothing(tmp_path: Path) -> None:
    """Finding L143-CODE-R1-F02: a nonterminal earlier-shape direct record is
    offered no control and raises no error, for proven and intent doors."""

    fixture = _interrupted(tmp_path, "old-shape-running")
    store = direct_landing_store(fixture["contract"])
    record = store.read()
    assert record is not None
    assert record.status == "input-required"
    for state in ("proven", "intent"):
        old_shape = _old_shape(record, state=state)
        for allow in (True, False):
            offered = legal_operation_controls(
                fixture["contract"],
                old_shape,
                context=LifecycleControlProjectionContext(allow_completed_disposition=allow),
            )
            assert [item["action"] for item in offered] == [], (state, allow)


def test_old_shape_cancelled_direct_is_offered_nothing(tmp_path: Path) -> None:
    """Finding L143-CODE-R1-F02: a cancelled earlier-shape direct record is
    offered no control and raises no error."""

    fixture = _series_fixture(tmp_path / "old-shape-cancelled")
    bind_worktree_services(build_default_worktree_services())
    code = fixture["code_head"]
    with mock.patch.object(
        direct_landing_owner,
        "execute_or_require_direct_landing_recovery",
        side_effect=DirectLandingError("cut-before-execution", "stopped after admission"),
    ):
        stopped = direct_landing_tool(fixture["config"], _plain_landing_request(fixture, code))
    assert stopped["ok"] is False
    store = direct_landing_store(fixture["contract"])
    record = store.read()
    assert record is not None
    assert record.status == "running"
    cancelled = store.update(
        lambda current: current.model_copy(
            update={
                "status": "cancelled",
                "phase": "cancelled",
                "finishedAt": current.heartbeatAt,
                "cancelRequested": True,
                "generationDisposition": "cancelled",
            }
        )
    )
    assert cancelled.status == "cancelled"
    for state in ("proven", "intent"):
        old_shape = _old_shape(cancelled, state=state)
        for allow in (True, False):
            offered = legal_operation_controls(
                fixture["contract"],
                old_shape,
                context=LifecycleControlProjectionContext(allow_completed_disposition=allow),
            )
            assert [item["action"] for item in offered] == [], (state, allow)


def test_integrated_contract_offers_nothing_and_refuses(tmp_path: Path) -> None:
    """Finding L143-CODE-R1-F01: offer and admission agree that an integrated
    contract retires nothing — the owner is offered no control and the check
    refuses the same contract and record."""

    fixture = _landed(tmp_path, "integrated")
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    integrated = replace(fixture["contract"], integration_status="completed")
    offered = legal_operation_controls(
        integrated,
        record,
        context=LifecycleControlProjectionContext(allow_completed_disposition=True),
    )
    assert [item["action"] for item in offered] == []
    with pytest.raises(LifecycleControlError) as refused:
        require_completed_disposition(integrated, record, "retire")
    assert refused.value.status == "lifecycle-disposition-not-allowed"


def _write_integrate_record(
    fixture: dict, *, status: str = "running", disposition: str = "active"
) -> None:
    """A journaled integrate generation with a live publication claim."""

    contract = fixture["contract"]
    code_head = branch_commit(contract.code_repo_path, contract.code_work_branch)
    memory_head = head_commit(contract.memory_repo_path)
    key = "e" * 64
    direct = direct_landing_store(contract).read()
    assert direct is not None
    integrate = LifecycleOperationRecord(
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract.contract_path.as_posix(),
        operationKind="integrate",
        candidateState="f" * 64,
        fingerprint="a" * 64,
        operationKey=key,
        input=IntegrateOperationInput(
            configPath=contract.contract_path.as_posix(),
            contractPath=contract.contract_path.as_posix(),
        ),
        status=status,  # type: ignore[arg-type]
        phase="preflight",
        queuedAt="2026-10-11T00:00:00+00:00",
        startedAt="2026-10-11T00:00:00+00:00",
        heartbeatAt="2026-10-11T00:00:00+00:00",
        currentCommand="fixture integrate",
        reportPath=contract.contract_path.as_posix(),
        recoveryCommits=LifecycleOperationRecoveryCommits(
            codeCommit=code_head, memoryContentCommit=memory_head
        ),
        integrationAuthority=IntegrationOperationAuthority(
            targetKind="atomic-integration",
            codeRepository=contract.code_repo_path.as_posix(),
            codeSourceBranch=contract.code_work_branch,
            codeSourceRef=f"refs/heads/{contract.code_work_branch}",
            codeSourceCommit=code_head,
            codeCandidateCommit=code_head,
            memoryRepository=(contract.memory_repo_path or Path()).as_posix(),
            memorySourceBranch=contract.memory_work_branch,
            memorySourceRef=f"refs/heads/{contract.memory_work_branch}",
            memorySourceCommit=memory_head,
            memoryContentCommit=memory_head,
        ),
        integrationPublication=IntegrationPublicationIntent(
            operationKey=key,
            generation=1,
            preparedAt="2026-10-11T00:00:00+00:00",
            claimState="intent",
            sprintTaskDocument="repo-a/direct-task/task.json",
            candidateTaskDocument="repo-a/direct-task/task.json",
            doorGenerationId="b" * 64,
            sourceOperationKind="direct-landing",
            sourceOperationGeneration=direct.generation,
            sourceOperationFingerprint=direct.fingerprint,
            sourceOperationKey=direct.operationKey,
            sourceJournalSha256="c" * 64,
        ),
        generationDisposition=disposition,  # type: ignore[arg-type]
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
    created, _ = store.create(integrate)
    assert created.generation == 1


def test_active_integration_claim_offers_nothing_and_refuses(tmp_path: Path) -> None:
    """Finding L143-CODE-R1-F01: while the integration journal holds a live
    claim, the owner is offered no retire and the check refuses with the
    claim error; the idle claim (no journal) admits, as the retire proof shows."""

    fixture = _landed(tmp_path, "claimed")
    _write_integrate_record(fixture)
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    offered = legal_operation_controls(
        fixture["contract"],
        record,
        context=LifecycleControlProjectionContext(allow_completed_disposition=True),
    )
    assert [item["action"] for item in offered] == []
    with pytest.raises(LifecycleControlError) as refused:
        require_completed_disposition(fixture["contract"], record, "retire")
    assert refused.value.status == "lifecycle-integration-claim-active"


def test_idle_cancelled_claim_still_admits_owner_retire(tmp_path: Path) -> None:
    """Finding L143-CODE-R1-F01: a cancelled integration claim is idle, so the
    owner retire is still offered and admitted."""

    fixture = _landed(tmp_path, "idle-claim")
    _write_integrate_record(fixture, status="cancelled", disposition="cancelled")
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    offered = legal_operation_controls(
        fixture["contract"],
        record,
        context=LifecycleControlProjectionContext(allow_completed_disposition=True),
    )
    assert [item["action"] for item in offered] == ["retire"]
    require_completed_disposition(fixture["contract"], record, "retire")


def _admitted_running(tmp_path: Path, name: str = "first-leg") -> dict:
    """A converted landing interrupted between admission and execution: the
    code commit is verified, the memory leg has not moved (finding
    L143-CODE-R1-F03, first-leg-only cut)."""

    fixture = _converted_series_fixture(tmp_path / name)
    world = fixture["world"]
    code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
    bind_worktree_services(build_default_worktree_services())
    with mock.patch.object(
        direct_landing_owner,
        "execute_or_require_direct_landing_recovery",
        side_effect=DirectLandingError("cut-before-execution", "stopped after admission"),
    ):
        stopped = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
    assert stopped["ok"] is False
    assert stopped["status"] == "cut-before-execution"
    fixture["code_commit"] = code
    return fixture


def test_control_recovers_first_leg_direct_landing_through_public_tool(
    tmp_path: Path,
) -> None:
    """Finding L143-CODE-R1-F03: the required first-leg-only interruption —
    code verified and moved, memory HEAD still at its accepted before-state —
    is continued by the control's recover to completion with the kept receipt
    settled in that call; a second recover and a stranger are refused."""

    fixture = _admitted_running(tmp_path)
    world = fixture["world"]
    contract = fixture["contract"]
    store = direct_landing_store(contract)
    record = store.read()
    assert record is not None
    assert (record.status, record.generationDisposition) == ("running", "active")
    accepted = record.input
    assert isinstance(accepted, DirectLandingOperationInput)
    assert branch_commit(contract.code_repo_path, contract.code_work_branch) == (
        accepted.codeCommit
    )
    assert head_commit(world.memory) == accepted.memoryBefore.head
    assert all(evidence.state != "commit-proven" for evidence in record.mutationEvidence.values())
    assert (record.recoveryCommits is None) or not (record.recoveryCommits.memoryContentCommit)
    assert _kept_closings(contract) != []

    continued = _control(fixture, "recover")
    assert continued["ok"], continued
    current = store.read()
    assert current is not None
    assert (current.status, current.generation) == ("completed", 1)
    assert current.operationKey == record.operationKey
    assert _kept_closings(contract) == []
    closed = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
    assert closed["closed"] is True
    assert git(world.memory, "status", "--porcelain") == ""

    again = _control(fixture, "recover")
    assert again["ok"] is False
    assert again["status"] == "lifecycle-control-not-legal"
    assert again["observed"]["status"] == "completed"

    stranger = _control(fixture, "recover", expected_generation=2)
    assert stranger["ok"] is False
    assert stranger["status"] == "lifecycle-generation-changed"


def test_missing_generation_normalizes_without_exception(tmp_path: Path) -> None:
    """Ruling 167195eb point 2: no proof of a missing generation is owed —
    instead one test: a stored record whose generation field is absent parses
    to generation 1, and the owner predicate answers for it like for any
    other record, with no exception."""

    fixture = _landed(tmp_path, "missing-generation")
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    reloaded = LifecycleOperationRecord.model_validate(
        record.model_dump(mode="json", exclude={"generation"})
    )
    assert reloaded.generation == 1
    assert completed_direct_owner(reloaded) is True
    require_completed_disposition(fixture["contract"], reloaded, "retire")


def _grant_sibling_leaf(fixture: dict, leaf_id: str) -> TaskDocumentRef:
    """A second planning leaf of the fixture master with its own grant (L94
    sibling apparatus): the successor names another leaf by task ref and
    history, because the bound ref wins over a bare ``leaf_id``."""

    tasks = fixture["world"].task_root
    sibling = TaskDocument.model_validate(
        {
            "id": leaf_id,
            "slug": "second",
            "title": "Second direct leaf",
            "kind": "subTask",
            "status": "planning",
            "repo": "repo-a",
            "createdAt": "2026-10-06T00:00:00+00:00",
            "objective": "Exercise the converted successor.",
            "requirements": ["The second leaf lands after the first retires."],
        }
    )
    write_task_doc(tasks, sibling)
    master = read_task_doc(tasks / "task.json")
    write_task_doc(
        tasks,
        master.model_copy(
            update={
                "subTasks": [
                    *master.subTasks,
                    SubTaskRef(
                        number=leaf_id,
                        name="Second direct leaf",
                        file="second.md",
                        status="planning",
                    ),
                ]
            }
        ),
    )
    sibling_ref = TaskDocumentRef(repository="repo-a", path="direct-task/second.json")
    give_grant(fixture["config"], sibling_ref, granted_by="architect", granter_agent_id="fixture")
    return sibling_ref


def test_owner_retire_converted_successor_names_another_leaf(tmp_path: Path) -> None:
    """F04 converted next-leaf successor (ruling e55c0093 point 7, owed until L52
    landed): on the converted substrate the owner retires the completed first
    landing, and the next landing names another leaf by ``leaf_id`` — it is
    accepted and completes as generation 2, closing that leaf's history while
    the first leaf's history stays closed.

    The other leaf's open history is written after the retire, and its answers
    go into its own open file: the shared ``_answered`` helper would rewrite
    the first leaf's closed history as open (``rows()`` defaults
    ``closed=False``)."""

    other = "260928-MIK-L96"
    fixture = _converted_series_fixture(tmp_path / "converted-successor")
    world = fixture["world"]
    code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
    bind_worktree_services(build_default_worktree_services())

    first = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
    assert first["ok"], first
    store = direct_landing_store(fixture["contract"])
    _with_disposition_topology(fixture)
    retired = _control(fixture, "retire", caller=_owner())
    assert retired["ok"], retired

    advanced = commit(world.code, {A: CODE_A.replace("return value", "return value * 2")})
    before = world.reanchor("RLZ-A00001")
    numbered = [
        {"id": f"ROW-{index:06d}", "items": [], **row}
        for index, row in enumerate(
            (
                world.invariant_row("INV-AAAAAA", before),
                family_row({"INV-AAAAAA": 1, "INV-BBBBBB": 1}),
                *trace_rows(*TRACES),
            )
        )
    ]
    write(
        world.memory,
        {
            f"knowledge/history/{other}.json": canonical_text(
                {
                    "schema": "ar-history/v1",
                    "leaf": other,
                    "closed": False,
                    "rows": numbered,
                }
            )
        },
    )

    second = direct_landing_tool(
        fixture["config"],
        replace(
            _converted_request(fixture, advanced),
            leaf_task_document_ref=_grant_sibling_leaf(fixture, other),
            leaf_id=other,
        ),
    )
    assert second["ok"], second
    landed = store.read()
    assert landed is not None
    assert (landed.status, landed.generation) == ("completed", 2)
    assert isinstance(landed.input, DirectLandingOperationInput)
    assert landed.input.leafId == other
    closed_other = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{other}.json"))
    assert closed_other["closed"] is True and closed_other["leaf"] == other
    closed_first = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
    assert closed_first["closed"] is True and closed_first["leaf"] == LEAF
    assert git(world.memory, "status", "--porcelain") == ""
