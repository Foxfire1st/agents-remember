"""Task-addressed start, observe, recover, cancel, and projection for lifecycle jobs."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import git_environment
from agents_remember.kernel.platform_subprocess import (
    native_command,
    native_subprocess_environment,
)
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationInput,
    LifecycleOperationKind,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
    operation_report_path,
)
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

STALE_HEARTBEAT_SECONDS = 30.0
COMMAND_EVIDENCE_LIMIT = 320
OperationLauncher = Callable[[WorktreeContract, LifecycleOperationRecord], None]


@dataclass(frozen=True)
class _CandidateIdentity:
    state: str
    tree: str | None
    fingerprint: str


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def operation_fingerprint(operation_input: LifecycleOperationInput) -> str:
    return _fingerprint_payload(operation_input.model_dump(mode="json"))


def operation_state_fingerprint(contract: WorktreeContract) -> str:
    """Hash lifecycle cells that change only when a sequential operation advanced the task."""
    return _fingerprint_payload(
        {
            "closeoutStatus": contract.closeout_status,
            "codeCommit": contract.code_commit,
            "memoryContentCommit": contract.memory_content_commit,
            "ledgerCommit": contract.ledger_commit,
            "integrationStatus": contract.integration_status,
            "integratedCodeCommit": contract.integrated_code_commit,
            "integratedMemoryContentCommit": contract.integrated_memory_content_commit,
            "integratedLedgerCommit": contract.integrated_ledger_commit,
            "cleanup": contract.cleanup,
        }
    )


def _fingerprint_payload(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def operation_key(contract_path: Path, kind: LifecycleOperationKind, fingerprint: str) -> str:
    identity = f"{contract_path.resolve().as_posix()}\0{kind}\0{fingerprint}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def start_or_observe_operation(
    operation_input: LifecycleOperationInput,
    *,
    launcher: OperationLauncher | None = None,
    now: datetime | None = None,
) -> LifecycleOperationProjection:
    contract = load_contract(Path(operation_input.contractPath))
    _validate_input_identity(contract, operation_input)
    candidate_state = operation_state_fingerprint(contract)
    candidate_tree = _candidate_tree(contract, operation_input.kind)
    fingerprint = _fingerprint_payload(
        {
            "input": operation_input.model_dump(mode="json"),
            "candidateState": candidate_state,
            "candidateTree": candidate_tree,
        }
    )
    store = _store(contract, operation_input.kind)
    timestamp = (now or datetime.now(UTC)).replace(microsecond=0)
    candidate = _queued_record(
        contract,
        operation_input,
        _CandidateIdentity(candidate_state, candidate_tree, fingerprint),
        timestamp,
    )
    current, created = store.create(candidate)
    if current.fingerprint != fingerprint:
        if current.status not in {"completed", "failed", "cancelled"}:
            raise RuntimeError(
                f"conflicting {operation_input.kind} operation already exists for task "
                f"{contract.task_name}; wait for or resolve that task-bound operation"
            )
        if current.status == "completed" and current.candidateState == candidate_state:
            raise RuntimeError(
                f"conflicting {operation_input.kind} parameters target an already completed "
                f"task state for {contract.task_name}; the task state has not advanced"
            )
        current = store.replace_terminal(candidate)
        created = True
    should_launch = created
    if not created and _should_recover(current, timestamp):
        current, should_launch = store.replace_for_recovery(
            lambda record: _requeued(record, timestamp),
            expected_attempt=current.attempt,
        )
    if should_launch:
        _launch_or_fail(contract, current, launcher or launch_detached_worker, store)
        current = store.read() or current
    return operation_projection(current, now=timestamp)


def observe_operation(
    contract_path: Path, kind: LifecycleOperationKind
) -> LifecycleOperationProjection | None:
    contract = load_contract(contract_path)
    current = _store(contract, kind).read()
    return None if current is None else operation_projection(current)


def latest_operation_projection(contract_path: Path) -> LifecycleOperationProjection | None:
    contract = load_contract(contract_path)
    records = [
        record
        for kind in ("closeout", "integrate")
        if (record := _store(contract, kind).read()) is not None
    ]
    if not records:
        return None
    active = [
        record for record in records if record.status in {"queued", "running", "input-required"}
    ]
    selected = max(active or records, key=_record_sort_stamp)
    return operation_projection(selected)


def cancel_operation(
    contract_path: Path, kind: LifecycleOperationKind
) -> LifecycleOperationProjection:
    contract = load_contract(contract_path)
    store = _store(contract, kind)

    def request(record: LifecycleOperationRecord) -> LifecycleOperationRecord:
        if record.status in {"completed", "failed", "cancelled"}:
            return record
        if record.irreversibleBoundaryEntered:
            raise RuntimeError(
                f"{kind} has entered its irreversible boundary; cancellation is refused and "
                "recovery must reconcile or complete the same task-bound operation"
            )
        return record.model_copy(
            update={
                "status": "cancelled",
                "phase": "cancelled",
                "cancelRequested": True,
                "finishedAt": now_iso(),
                "guidance": "The task-bound operation was cancelled before approval claim.",
            }
        )

    current = store.update(request)
    if current.status == "cancelled" and current.workerPid is not None:
        _terminate_worker_group(current.workerPid)
    return operation_projection(current)


def operation_projection(
    record: LifecycleOperationRecord, *, now: datetime | None = None
) -> LifecycleOperationProjection:
    current = now or datetime.now(UTC)
    start = _parse_stamp(record.startedAt or record.queuedAt)
    finish = _parse_stamp(record.finishedAt) if record.finishedAt else current
    return LifecycleOperationProjection(
        kind=record.operationKind,
        status=record.status,
        phase=record.phase,
        startedAt=record.startedAt,
        heartbeatAt=record.heartbeatAt,
        finishedAt=record.finishedAt,
        elapsedSeconds=max(0.0, (finish - start).total_seconds()),
        currentCommand=record.currentCommand[:COMMAND_EVIDENCE_LIMIT],
        reportPath=record.reportPath,
        result=record.result,
        failure=record.failure,
        guidance=record.guidance,
        cancellable=(
            record.status in {"queued", "running", "input-required"}
            and not record.irreversibleBoundaryEntered
        ),
    )


def launch_detached_worker(contract: WorktreeContract, record: LifecycleOperationRecord) -> None:
    reports_root = contract.worktree_group / "reports"
    report = Path(record.reportPath)
    atomic_write_text(report, "")
    env = native_subprocess_environment(git_environment(), temp_root=reports_root / "tmp")
    command = native_command(
        [
            sys.executable,
            "-m",
            "agents_remember.application.lifecycle_operation_worker",
            "--contract-path",
            record.contractPath,
            "--kind",
            record.operationKind,
        ],
        env,
    )
    with report.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=contract.code_worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    LifecycleOperationStore(
        operation_record_path(contract.worktree_group, record.operationKind)
    ).update(
        lambda current: (
            current.model_copy(update={"workerPid": process.pid})
            if current.status == "queued" and current.fingerprint == record.fingerprint
            else current
        )
    )


def _queued_record(
    contract: WorktreeContract,
    operation_input: LifecycleOperationInput,
    candidate: _CandidateIdentity,
    timestamp: datetime,
) -> LifecycleOperationRecord:
    stamp = timestamp.isoformat()
    return LifecycleOperationRecord(
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract.contract_path.as_posix(),
        operationKind=operation_input.kind,
        candidateState=candidate.state,
        candidateTree=candidate.tree,
        fingerprint=candidate.fingerprint,
        operationKey=operation_key(
            contract.contract_path, operation_input.kind, candidate.fingerprint
        ),
        input=operation_input,
        status="queued",
        phase="queued",
        queuedAt=stamp,
        currentCommand=f"waiting to start {operation_input.kind}",
        reportPath=operation_report_path(contract.worktree_group, operation_input.kind).as_posix(),
    )


def _candidate_tree(contract: WorktreeContract, kind: LifecycleOperationKind) -> str | None:
    """Materialize the closeout request's full candidate in an isolated Git index.

    This captures tracked edits, deletions, and non-ignored untracked files without touching
    the developer-visible index. The worker compares the same tree at the exact reset-and-stage
    seam, so a delayed asynchronous start cannot silently absorb later worktree edits.
    """
    if kind != "closeout":
        return None
    return worktree_candidate_tree(
        contract.code_worktree,
        contract.worktree_group / "reports" / ".closeout-candidate.index",
    )


def _requeued(record: LifecycleOperationRecord, timestamp: datetime) -> LifecycleOperationRecord:
    return record.model_copy(
        update={
            "status": "queued",
            "phase": "recovering-after-claim" if record.irreversibleBoundaryEntered else "queued",
            "queuedAt": timestamp.isoformat(),
            "heartbeatAt": None,
            "finishedAt": None,
            "failure": None,
            "guidance": None,
            "currentCommand": "recovering the same task-bound operation",
            "attempt": record.attempt + 1,
            "workerPid": None,
            "cancelRequested": False,
        }
    )


def _recoverable_stale(record: LifecycleOperationRecord, now: datetime) -> bool:
    if record.status not in {"queued", "running"}:
        return False
    stamp = _parse_stamp(record.heartbeatAt or record.queuedAt)
    return (now - stamp).total_seconds() > STALE_HEARTBEAT_SECONDS


def _should_recover(record: LifecycleOperationRecord, now: datetime) -> bool:
    return (
        _recoverable_stale(record, now)
        or (record.status == "input-required" and record.irreversibleBoundaryEntered)
        or (record.status in {"failed", "cancelled"} and not record.irreversibleBoundaryEntered)
    )


def _launch_or_fail(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    launcher: OperationLauncher,
    store: LifecycleOperationStore,
) -> None:
    try:
        launcher(contract, record)
    except Exception as error:
        stamp = now_iso()
        failure = f"detached worker could not start: {error}"
        store.update(
            lambda current: current.model_copy(
                update={
                    "status": "failed",
                    "phase": "failed",
                    "finishedAt": stamp,
                    "failure": failure,
                    "guidance": "Fix the native runner environment, then start the same task operation again.",
                }
            )
        )
        raise


def _validate_input_identity(
    contract: WorktreeContract, operation_input: LifecycleOperationInput
) -> None:
    if Path(operation_input.contractPath).resolve() != contract.contract_path.resolve():
        raise RuntimeError("lifecycle operation input does not resolve to its loaded contract")
    if operation_input.kind == "closeout" and not operation_input.approvalNote.strip():
        raise RuntimeError("closeout apply requires a non-empty approval intent note")


def _store(contract: WorktreeContract, kind: LifecycleOperationKind) -> LifecycleOperationStore:
    return LifecycleOperationStore(operation_record_path(contract.worktree_group, kind))


def _record_sort_stamp(record: LifecycleOperationRecord) -> str:
    return record.finishedAt or record.heartbeatAt or record.startedAt or record.queuedAt


def _parse_stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _terminate_worker_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
