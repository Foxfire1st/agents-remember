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
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.git_command import git_environment
from agents_remember.kernel.platform_subprocess import (
    native_command,
    native_subprocess_environment,
)
from agents_remember.kernel.primitives.runtime_config import RepositoryScope, load_config
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationConflictTransaction,
    IntegrationOperationAuthority,
    LifecycleOperationInput,
    LifecycleOperationKind,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.closeout_queue_lifecycle import (
    prepare_queue_candidate_conflict_resolution,
    release_queue_candidate_after_reversible_operation,
)
from agents_remember.worktrees.integration_branch_authority import integration_targets
from agents_remember.worktrees.lifecycle_operation_identity import operation_state_fingerprint
from agents_remember.worktrees.lifecycle_operation_lease import contract_lifecycle_lease
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
    operation_report_path,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    is_ancestor,
    repository_identity,
)
from agents_remember.worktrees.modules.start_contract import memory_mode_for_repository
from agents_remember.worktrees.organizational_completion_repair import (
    prepare_organizational_completion_repair,
)
from agents_remember.worktrees.route_review import code_candidate_tree
from agents_remember.worktrees.task_resolver import leaf_enclosure_path, series_contract_path
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
    require_configured_contract_repositories(contract, operation_input.configPath)
    with contract_lifecycle_lease(contract, operation_kind=operation_input.kind):
        return _start_or_observe_operation(contract, operation_input, launcher=launcher, now=now)


def _start_or_observe_operation(
    contract: WorktreeContract,
    operation_input: LifecycleOperationInput,
    *,
    launcher: OperationLauncher | None,
    now: datetime | None,
) -> LifecycleOperationProjection:
    store = _store(contract, operation_input.kind)
    timestamp = (now or datetime.now(UTC)).replace(microsecond=0)
    recovering = _irreversible_recovery_record(store.read(), operation_input)
    if recovering is None:
        candidate_state = operation_state_fingerprint(contract)
        candidate_tree = _candidate_tree(contract, operation_input.kind)
        integration_authority = _integration_authority(contract, operation_input)
        fingerprint = _fingerprint_payload(
            {
                "input": operation_input.model_dump(mode="json"),
                "candidateState": candidate_state,
                "candidateTree": candidate_tree,
                "integrationAuthority": (
                    integration_authority.model_dump(mode="json")
                    if integration_authority is not None
                    else None
                ),
            }
        )
    else:
        candidate_state = recovering.candidateState
        candidate_tree = recovering.candidateTree
        integration_authority = recovering.integrationAuthority
        fingerprint = recovering.fingerprint
    candidate = _queued_record(
        contract,
        operation_input,
        _CandidateIdentity(candidate_state, candidate_tree, fingerprint),
        integration_authority,
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


def _irreversible_recovery_record(
    current: LifecycleOperationRecord | None,
    operation_input: LifecycleOperationInput,
) -> LifecycleOperationRecord | None:
    """Keep one accepted identity after its own protected-ref movement changed Git facts."""

    if (
        current is None
        or not current.irreversibleBoundaryEntered
        or current.input != operation_input
    ):
        return None
    if current.status in {"queued", "running", "input-required"}:
        return current
    return None


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
    with contract_lifecycle_lease(contract, operation_kind=kind):
        return _cancel_operation(contract, kind)


def _cancel_operation(
    contract: WorktreeContract, kind: LifecycleOperationKind
) -> LifecycleOperationProjection:
    store = _store(contract, kind)
    worker_pid: int | None = None

    def request(record: LifecycleOperationRecord) -> LifecycleOperationRecord:
        nonlocal worker_pid
        if record.status in {"completed", "failed", "cancelled"}:
            return record
        if record.irreversibleBoundaryEntered:
            raise RuntimeError(
                f"{kind} has entered its irreversible boundary; cancellation is refused and "
                "recovery must reconcile or complete the same task-bound operation"
            )
        worker_pid = record.workerPid
        conflict = (
            record.integrationAuthority.conflictTransaction
            if record.integrationAuthority is not None
            else None
        )
        guidance = "The task-bound operation was cancelled before approval claim."
        result = record.result
        organizational_repair = record.organizationalRepair
        if conflict is not None:
            guidance = (
                "The stale certified candidate was retired and closeout was reset. Absorb "
                "the recorded source delta in this leaf, then declare and close it again."
            )
            result = {
                "state": "conflict-resolution-prepared",
                "conflictTransaction": conflict.model_dump(mode="json"),
                "nextOperation": "resolve_leaf_then_redeclare",
            }
        elif isinstance(result, dict) and result.get("state") == (
            "organizational-completion-gate-failed"
        ):
            if organizational_repair is None:
                raise RuntimeError(
                    "organizational completion cancellation requires its gate-bound "
                    "durable repair evidence"
                )
            guidance = (
                "The failed final candidate was retired and the same leaf closeout was "
                "reset. Repair the leaf, then declare and close it again."
            )
        return record.model_copy(
            update={
                "status": "cancelled",
                "phase": "cancelled",
                "cancelRequested": True,
                "finishedAt": now_iso(),
                "result": result,
                "organizationalRepair": organizational_repair,
                "guidance": guidance,
                "workerPid": None,
            }
        )

    current = store.update(request)
    try:
        if current.status == "cancelled" and not current.irreversibleBoundaryEntered:
            _publish_cancellation_repair(contract, current, kind)
    finally:
        # The store has already cleared workerPid. Always signal the captured
        # process group before surfacing a queue-release failure, or a retry can
        # launch a second worker while the cancelled one is still running.
        if worker_pid is not None:
            _terminate_worker_group(worker_pid)
    return operation_projection(current)


def _publish_cancellation_repair(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    kind: LifecycleOperationKind,
) -> None:
    authority = record.integrationAuthority
    if authority is not None and authority.conflictTransaction is not None:
        prepare_queue_candidate_conflict_resolution(
            contract,
            operation_key=record.operationKey,
            authority=authority,
        )
        return
    if isinstance(record.result, dict) and record.result.get("state") == (
        "organizational-completion-gate-failed"
    ):
        prepare_organizational_completion_repair(contract)
        return
    release_queue_candidate_after_reversible_operation(
        contract,
        operation_key=record.operationKey,
        operation_kind=kind,
    )


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
    integration_authority: IntegrationOperationAuthority | None,
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
        integrationAuthority=integration_authority,
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
    return code_candidate_tree(contract)


def _integration_authority(
    contract: WorktreeContract, operation_input: LifecycleOperationInput
) -> IntegrationOperationAuthority | None:
    if operation_input.kind != "integrate":
        return None
    if not isinstance(operation_input, IntegrateOperationInput):
        raise RuntimeError("integrate operation authority requires integrate input")
    if contract.closeout_status != "completed" or not contract.code_commit:
        raise RuntimeError("integration authority requires a completed closeout code commit")
    targets = {target.side: target for target in integration_targets(contract)}
    code_target = targets["code"]
    code_source_commit = branch_commit(contract.code_repo_path, code_target.branch)
    code_replay_required = not is_ancestor(
        contract.code_repo_path, code_source_commit, contract.code_commit
    )
    memory_source_commit = ""
    memory_replay_required = False
    if contract.memory_mode == "external":
        if (
            contract.memory_repo_path is None
            or not contract.memory_content_commit
            or not contract.ledger_commit
        ):
            raise RuntimeError(
                "external-memory integration authority requires repo and closeout commits"
            )
        memory_target = targets["memory"]
        memory_source_commit = branch_commit(contract.memory_repo_path, memory_target.branch)
        memory_replay_required = not is_ancestor(
            contract.memory_repo_path, memory_source_commit, contract.ledger_commit
        )
    else:
        memory_target = None
    conflict = None
    if operation_input.strategy == "replay" and (code_replay_required or memory_replay_required):
        if contract.kind == "series":
            raise RuntimeError(
                "atomic series integration cannot open a leaf conflict worktree; source drift "
                "requires orchestrator-owned block recovery or graph reshape"
            )
        conflict = IntegrationConflictTransaction(
            codeReplayRequired=code_replay_required,
            memoryReplayRequired=memory_replay_required,
            codeSourceRef=f"refs/heads/{code_target.branch}",
            codeSourceCommit=code_source_commit,
            codeCandidateCommit=contract.code_commit,
            memorySourceRef=(
                f"refs/heads/{memory_target.branch}" if memory_target is not None else ""
            ),
            memorySourceCommit=memory_source_commit,
            memoryContentCommit=contract.memory_content_commit,
            ledgerCommit=contract.ledger_commit,
            codeWorktree=contract.code_worktree.resolve().as_posix(),
            memoryWorktree=(
                contract.memory_worktree.resolve().as_posix()
                if contract.memory_worktree is not None
                else ""
            ),
        )
    return IntegrationOperationAuthority(
        targetKind=code_target.kind,
        codeRepository=code_target.repository.as_posix(),
        codeSourceBranch=code_target.branch,
        codeSourceRef=f"refs/heads/{code_target.branch}",
        codeSourceCommit=code_source_commit,
        codeCandidateCommit=contract.code_commit,
        memoryRepository=(memory_target.repository.as_posix() if memory_target is not None else ""),
        memorySourceBranch=(memory_target.branch if memory_target is not None else ""),
        memorySourceRef=(f"refs/heads/{memory_target.branch}" if memory_target is not None else ""),
        memorySourceCommit=memory_source_commit,
        memoryContentCommit=contract.memory_content_commit,
        ledgerCommit=contract.ledger_commit,
        conflictTransaction=conflict,
    )


def require_configured_contract_repositories(
    contract: WorktreeContract,
    config_path: str,
) -> None:
    """Bind a task contract to the repository identities selected by MCP authority."""

    config = load_config(config_path)
    configured = require_repo(config, contract.repo_name)
    _require_configured_task_identity(contract, config.coordination_root)
    code_identity = repository_identity(configured.path)
    contract_code_identity = repository_identity(contract.code_repo_path)
    if code_identity is None or contract_code_identity != code_identity:
        raise RuntimeError("task contract code repository does not match configured authority")
    candidate_code_identity = repository_identity(contract.code_worktree)
    if candidate_code_identity != code_identity:
        raise RuntimeError("task contract code candidate belongs to another repository")
    expected_memory_mode = memory_mode_for_repository(configured.path, configured.memory_root)
    if contract.memory_mode != expected_memory_mode:
        raise RuntimeError(
            "task contract memory mode does not match configured repository authority"
        )
    if contract.memory_mode != "external":
        return
    _require_external_memory_authority(contract, configured, code_identity)


def _require_external_memory_authority(
    contract: WorktreeContract,
    configured: RepositoryScope,
    code_identity: Path,
) -> None:
    if configured.memory_root is None or contract.memory_repo_path is None:
        raise RuntimeError("external-memory task contract does not match configured authority")
    memory_identity = repository_identity(configured.memory_root)
    contract_memory_identity = repository_identity(contract.memory_repo_path)
    if memory_identity is None or contract_memory_identity != memory_identity:
        raise RuntimeError("task contract memory repository does not match configured authority")
    if memory_identity == code_identity:
        raise RuntimeError("external memory must not share the code repository Git common-dir")
    if contract.kind == "leaf":
        if contract.memory_worktree is None:
            raise RuntimeError("external-memory leaf contract is missing its candidate worktree")
        if repository_identity(contract.memory_worktree) != memory_identity:
            raise RuntimeError("task contract memory candidate belongs to another repository")


def _require_configured_task_identity(
    contract: WorktreeContract,
    configured_coordination_root: Path,
) -> None:
    coordination_root = configured_coordination_root.resolve()
    if contract.coordination_root.resolve() != coordination_root:
        raise RuntimeError("task contract coordination root does not match configured authority")
    repository_task_root = (coordination_root / "tasks" / contract.repo_name).resolve()
    task_root = contract.task_root.resolve()
    if not task_root.is_relative_to(repository_task_root):
        raise RuntimeError("task contract task root is outside the configured repository task tree")
    if contract.task_artifact.resolve() != (task_root / "task.md").resolve():
        raise RuntimeError("task contract task artifact is not canonical for its task root")
    expected_contract = (
        leaf_enclosure_path(task_root, contract.leaf_id)
        if contract.kind == "leaf"
        else series_contract_path(task_root)
    )
    if contract.contract_path.resolve() != expected_contract.resolve():
        raise RuntimeError("task contract path is not canonical for its task identity")
    if contract.kind == "series":
        if contract.worktree_group.resolve() != (task_root / "enclosures").resolve():
            raise RuntimeError("series contract worktree group is not its task enclosure root")
        return
    worktree_root = (coordination_root / "worktrees" / contract.repo_name).resolve()
    group = contract.worktree_group.resolve()
    if not group.is_relative_to(worktree_root):
        raise RuntimeError("leaf contract worktree group is outside configured authority")
    if contract.code_worktree.resolve().parent != group:
        raise RuntimeError("leaf contract code worktree is not owned by its worktree group")
    if contract.memory_mode == "external" and (
        contract.memory_worktree is None or contract.memory_worktree.resolve().parent != group
    ):
        raise RuntimeError("leaf contract memory worktree is not owned by its worktree group")


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
    stale = (now - stamp).total_seconds() > STALE_HEARTBEAT_SECONDS
    if not stale or record.workerPid is None:
        return stale
    return not _worker_process_group_alive(record.workerPid)


def _worker_process_group_alive(pid: int) -> bool:
    """Treat a reused/live process group as owned until a human resolves the stale record."""

    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _should_recover(record: LifecycleOperationRecord, now: datetime) -> bool:
    restored_ref_failure = (
        record.status == "failed"
        and record.irreversibleBoundaryEntered
        and isinstance(record.result, dict)
        and record.result.get("safeToReplace") is True
    )
    return (
        _recoverable_stale(record, now)
        or (record.status == "input-required" and record.irreversibleBoundaryEntered)
        or (record.status in {"failed", "cancelled"} and not record.irreversibleBoundaryEntered)
        or restored_ref_failure
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
