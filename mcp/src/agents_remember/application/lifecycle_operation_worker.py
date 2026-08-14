"""Application entry point for the canonical detached lifecycle runner."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agents_remember.application.completion_cleanup import auto_complete_seats
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.kernel.primitives.checkout_coordination import (
    declare_lifecycle_operation_process,
)
from agents_remember.kernel.primitives.gate_policy import (
    DecisionRole,
    GatePolicy,
    GatePolicyRule,
    make_gate_policy,
)
from agents_remember.kernel.primitives.gate_vocab import GateKind
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
    LifecycleOperationKind,
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout import closeout_result
from agents_remember.worktrees.modules.integrate import integrate_result
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import load_contract

HEARTBEAT_SECONDS = 5.0
QUALITY_PROGRESS_REPORT = "quality-progress.json"


class OperationCancelled(RuntimeError):
    pass


def _stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _policy(operation_input: CloseoutOperationInput | IntegrateOperationInput) -> GatePolicy:
    rules = [
        GatePolicyRule(
            kind=cast(GateKind, rule.kind),
            delegated_role=cast(DecisionRole | None, rule.delegatedRole),
            require_reviewer_verdict=rule.requireReviewerVerdict,
        )
        for rule in operation_input.gatePolicy
    ]
    return make_gate_policy(rules)


class OperationRuntime:
    def __init__(self, store: LifecycleOperationStore) -> None:
        self.store = store
        self.stop = threading.Event()

    def start(self) -> LifecycleOperationRecord:
        stamp = _stamp()

        def running(record: LifecycleOperationRecord) -> LifecycleOperationRecord:
            if record.status != "queued":
                return record
            return record.model_copy(
                update={
                    "status": "running",
                    "phase": "recovering-after-claim"
                    if record.irreversibleBoundaryEntered
                    else "preflight",
                    "startedAt": record.startedAt or stamp,
                    "heartbeatAt": stamp,
                    "currentCommand": "recover task state"
                    if record.irreversibleBoundaryEntered
                    else "validate lifecycle operation",
                    "workerPid": os.getpid(),
                }
            )

        return self.store.update(running)

    def progress(self, phase: str, evidence: Mapping[str, object]) -> None:
        stamp = _stamp()
        recovery_value = evidence.get("recovery_commits")
        recovery_commits = (
            LifecycleOperationRecoveryCommits.model_validate(recovery_value)
            if recovery_value is not None
            else None
        )

        def advance(record: LifecycleOperationRecord) -> LifecycleOperationRecord:
            if record.cancelRequested or record.status == "cancelled":
                return record
            return record.model_copy(
                update={
                    "status": "running",
                    "phase": phase,
                    "heartbeatAt": stamp,
                    "currentCommand": str(evidence.get("current_command") or phase),
                    "irreversibleBoundaryEntered": (
                        record.irreversibleBoundaryEntered
                        or bool(evidence.get("irreversible_boundary"))
                    ),
                    "approvalClaimed": (
                        record.approvalClaimed or bool(evidence.get("approval_claimed"))
                    ),
                    "recoveryCommits": (recovery_commits or record.recoveryCommits),
                }
            )

        current = self.store.update(advance)
        if current.status == "cancelled":
            raise OperationCancelled("operation cancelled before irreversible boundary")
        print(f"phase={phase} command={current.currentCommand}", flush=True)

    def heartbeat(self) -> None:
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                current_command = self._quality_command()

                def beat(
                    record: LifecycleOperationRecord,
                    command_evidence: str | None = current_command,
                ) -> LifecycleOperationRecord:
                    if record.status != "running":
                        return record
                    return record.model_copy(
                        update={
                            "heartbeatAt": _stamp(),
                            "currentCommand": command_evidence or record.currentCommand,
                        }
                    )

                self.store.update(beat)
            except Exception as error:  # pragma: no cover - reported by terminal worker log
                print(f"heartbeat failed: {error}", flush=True)
                return

    def _quality_command(self) -> str | None:
        """Read the wrapper's atomic report without making it operation authority."""
        path = self.store.path.parent / QUALITY_PROGRESS_REPORT
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._clean_quality_command()
        if not isinstance(payload, dict) or payload.get("status") != "running":
            return self._clean_quality_command()
        step = payload.get("step")
        detail = payload.get("detail")
        if not isinstance(step, str) or not isinstance(detail, str):
            return self._clean_quality_command()
        return f"quality: {step} — {detail}"

    def _clean_quality_command(self) -> str | None:
        path = self.store.path.parent / "dagger-progress.log"
        try:
            latest = path.read_text(encoding="utf-8").splitlines()[-1]
        except (IndexError, OSError):
            return None
        return f"Dagger quality: {latest[:240]}"

    def finish(self, result: dict[str, object], *, ok: bool) -> None:
        stamp = _stamp()

        def terminal(record: LifecycleOperationRecord) -> LifecycleOperationRecord:
            if record.status == "cancelled":
                return record
            if ok:
                return record.model_copy(
                    update={
                        "status": "completed",
                        "phase": "completed",
                        "heartbeatAt": stamp,
                        "finishedAt": stamp,
                        "currentCommand": "operation completed",
                        "result": result,
                        "guidance": "Observe the task contract for the next lifecycle edge.",
                        "workerPid": None,
                    }
                )
            needs_recovery = record.irreversibleBoundaryEntered
            needs_input = needs_recovery or bool(result.get("developer_decision_required"))
            return record.model_copy(
                update={
                    "status": "input-required" if needs_input else "failed",
                    "phase": "contract-finalization" if needs_recovery else "failed",
                    "heartbeatAt": stamp,
                    "finishedAt": None if needs_input else stamp,
                    "currentCommand": "reconcile the same operation"
                    if needs_recovery
                    else "operation failed",
                    "result": result,
                    "failure": str(result.get("reason") or result.get("summary") or result),
                    "guidance": (
                        "Restart this exact task operation; its consumed approval remains bound "
                        "to the same internal fingerprint and recovery will not replay a different mutation."
                        if needs_recovery
                        else (
                            "Resolve the reported developer decision, cancel this pre-boundary "
                            "attempt, then start the task operation with the selected input."
                            if needs_input
                            else "Fix the reported preflight failure, then restart this task operation."
                        )
                    ),
                    # This callback runs in the worker's final stack frame. Retaining its
                    # numeric PID after this point lets a delayed cancellation signal an
                    # unrelated process group if the kernel reuses that id.
                    "workerPid": None,
                }
            )

        self.store.update(terminal)

    def fail(self, error: Exception) -> None:
        self.finish({"reason": str(error)}, ok=False)


def execute_operation(record: LifecycleOperationRecord, runtime: OperationRuntime) -> None:
    operation_input = record.input
    config = load_config(operation_input.configPath)
    common = {
        "contract_path": Path(operation_input.contractPath),
        "approved": True,
        "gate_policy": _policy(operation_input),
        "operation_key": record.operationKey,
        "candidate_tree": record.candidateTree,
        "approval_claimed": record.approvalClaimed,
        "recovery_commits": record.recoveryCommits,
        "operation_progress": runtime.progress,
    }
    if isinstance(operation_input, CloseoutOperationInput):
        args = WorktreeArgs(
            **common,
            approval_note=operation_input.approvalNote,
            code_commit_message=operation_input.codeCommitMessage,
            memory_commit_message=operation_input.memoryCommitMessage,
            ledger_commit_message=operation_input.ledgerCommitMessage,
        )
        result = closeout_result(args)
        payload = {
            **result.payload,
            "ok": result.returncode == 0,
            "operation": "worktree_closeout_apply",
        }
    else:
        args = WorktreeArgs(
            **common,
            strategy=operation_input.strategy,
            ledger_commit_message=operation_input.ledgerCommitMessage,
        )
        result = integrate_result(args)
        payload = integration_completion_payload(config, operation_input, result)
    runtime.finish(payload, ok=result.returncode == 0)


def integration_completion_payload(
    config: McpRuntimeConfig,
    operation_input: IntegrateOperationInput,
    result: WorktreeCommandResult,
) -> dict[str, object]:
    """Apply the integration edge's completion cleanup inside the detached owner."""
    payload: dict[str, object] = {
        **result.payload,
        "ok": result.returncode == 0,
        "operation": "worktree_integrate",
    }
    if result.returncode == 0 and operation_input.autoCompleteSeats:
        payload.update(
            auto_complete_seats(
                config,
                Path(operation_input.contractPath),
                reason="auto-close: leaf integrated into master",
                edge="leaf-integration",
            )
        )
    return payload


def run_worker(contract_path: Path, kind: LifecycleOperationKind) -> int:
    contract = load_contract(contract_path)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, kind))
    current = store.read()
    if current is None or current.operationKind != kind:
        raise RuntimeError(f"no {kind} operation is queued for {contract.task_name}")
    runtime = OperationRuntime(store)
    current = runtime.start()
    if current.status in {"cancelled", "completed"}:
        return 0
    if current.status != "running":
        raise RuntimeError(f"{kind} worker cannot start from durable state {current.status!r}")
    heartbeat = threading.Thread(target=runtime.heartbeat, daemon=True)
    heartbeat.start()
    try:
        execute_operation(current, runtime)
    except OperationCancelled:
        return 0
    except Exception as error:
        print(f"operation failed: {error}", flush=True)
        runtime.fail(error)
        return 1
    finally:
        runtime.stop.set()
        heartbeat.join(timeout=HEARTBEAT_SECONDS)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resume one durable task lifecycle operation")
    parser.add_argument("--contract-path", type=Path, required=True)
    parser.add_argument("--kind", choices=("closeout", "integrate"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    declare_lifecycle_operation_process()
    bind_worktree_services(build_default_worktree_services())
    return run_worker(args.contract_path, cast(LifecycleOperationKind, args.kind))


if __name__ == "__main__":
    sys.exit(main())
