"""Owner-level preflights for high-fanout test prerequisites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from agents_remember.application.lifecycle.lifecycle_operation_worker import (
    terminal_operation_record,
)
from agents_remember.code_quality.dependency_ownership import (
    DependencyOwnershipGraph,
    SelectionReasonKind,
    TestImpact,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
    OrganizationalCompletionRepairEvidence,
)
from agents_remember.testing.causal_failures import (
    CAUSAL_REPORT_SCHEMA,
    FailureClass,
    write_causal_report,
)
from agents_remember.testing.dagger_admission import (
    DaggerAdmissionError,
    require_dagger_admission,
)

QUALITY_ATTEMPT_NONCE_ENV = "AR_QUALITY_ATTEMPT_NONCE"
PROVEN_EDGE_KINDS = frozenset(
    {SelectionReasonKind.IMPORT_CONSUMER, SelectionReasonKind.DECLARED_CONSUMER}
)


class _OwnershipGraph(Protocol):
    def resolve(self, changed: Sequence[Path]) -> TestImpact: ...


@dataclass(frozen=True)
class PreflightSpec:
    cause_id: str
    owner: Path
    evidence_altitude: str
    corrective_owner: Path
    validator: Callable[[], None]


def evaluate_preflights(
    specs: Sequence[PreflightSpec],
    graph: _OwnershipGraph,
    *,
    candidate: dict[str, object],
) -> dict[str, object]:
    """Evaluate owners once and derive only graph-proven blocked consumers."""

    outcomes: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    for spec in specs:
        try:
            spec.validator()
        except Exception as error:
            outcomes.append(_failed_outcome(spec, error))
            blocked.extend(_blocked_consumers(spec, graph.resolve((spec.owner,))))
        else:
            outcomes.append(_passed_outcome(spec))
    failures = [row for row in outcomes if row["status"] == "failed"]
    return {
        "schemaVersion": CAUSAL_REPORT_SCHEMA,
        "candidate": candidate,
        "status": "failed" if failures else "passed",
        "acceptanceEligible": False,
        "firstCausalFailure": failures[0]["causeId"] if failures else None,
        "preflights": outcomes,
        "blockedGroups": blocked,
        "runtimeEvidence": {
            "pytestExitCode": None,
            "blockedNodes": [],
            "independentFailures": [],
        },
    }


def _passed_outcome(spec: PreflightSpec) -> dict[str, object]:
    return {
        "causeId": spec.cause_id,
        "status": "passed",
        "failureClass": FailureClass.SHARED_DEPENDENCY.value,
        "evidenceAltitude": spec.evidence_altitude,
        "owner": spec.owner.as_posix(),
        "correctiveOwner": spec.corrective_owner.as_posix(),
        "detail": "owned prerequisite contract is compatible",
    }


def _failed_outcome(spec: PreflightSpec, error: Exception) -> dict[str, object]:
    detail = " ".join(str(error).split()) or type(error).__name__
    return {
        "causeId": spec.cause_id,
        "status": "failed",
        "failureClass": FailureClass.SHARED_DEPENDENCY.value,
        "evidenceAltitude": spec.evidence_altitude,
        "owner": spec.owner.as_posix(),
        "correctiveOwner": spec.corrective_owner.as_posix(),
        "detail": f"{type(error).__name__}: {detail}",
    }


def _blocked_consumers(spec: PreflightSpec, impact: TestImpact) -> list[dict[str, object]]:
    if not impact.complete:
        return []
    blocked: list[dict[str, object]] = []
    for owned in impact.ownership:
        reasons = tuple(reason for reason in owned.reasons if reason.kind in PROVEN_EDGE_KINDS)
        if not reasons:
            continue
        blocked.append(
            {
                "causeId": spec.cause_id,
                "failureClass": FailureClass.SHARED_DEPENDENCY.value,
                "evidenceAltitude": spec.evidence_altitude,
                "correctiveOwner": spec.corrective_owner.as_posix(),
                "testPath": owned.path.as_posix(),
                "dependencyChain": [
                    spec.owner.as_posix(),
                    *(reason.render() for reason in reasons),
                    owned.path.as_posix(),
                ],
            }
        )
    return blocked


def _validate_lifecycle_terminalization() -> None:
    record = _organizational_repair_record()
    transitioned = terminal_operation_record(
        record,
        {"reason": "later downstream symptom"},
        ok=False,
        stamp="2026-08-25T00:00:01+00:00",
    )
    if transitioned.result != record.result:
        raise RuntimeError("terminalization replaced the canonical organizational handoff")
    LifecycleOperationRecord.model_validate(transitioned.model_dump(mode="json"))


def _organizational_repair_record() -> LifecycleOperationRecord:
    contract_path = "/candidate/worktree.json"
    operation_key = "d" * 64
    generation = 1
    result = _canonical_repair_result(contract_path, generation)
    authority = IntegrationOperationAuthority(
        targetKind="sprint-super",
        codeRepository="/candidate/repository",
        codeSourceBranch="ar/master",
        codeSourceRef="refs/heads/ar/master",
        codeSourceCommit="a" * 40,
        codeCandidateCommit="b" * 40,
    )
    repair = OrganizationalCompletionRepairEvidence(
        operationKey=operation_key,
        candidateState="c" * 64,
        contractPath=contract_path,
        taskId="task-id",
        taskName="task-name",
        sprintTaskDocument="tasks/sprint/task.json",
        candidateTaskDocument="tasks/sprint/leaf.json",
        owningMasterTaskDocument="tasks/sprint/master.json",
        codeCommit=authority.codeCandidateCommit,
        acceptedContractSha256="e" * 64,
        resetContractSha256="f" * 64,
    )
    return LifecycleOperationRecord(
        taskId=repair.taskId,
        taskName=repair.taskName,
        contractPath=contract_path,
        operationKind="integrate",
        candidateState=repair.candidateState,
        candidateTree="1" * 40,
        fingerprint="2" * 64,
        operationKey=operation_key,
        generation=generation,
        integrationAuthority=authority,
        input=IntegrateOperationInput(
            configPath="/candidate/agents-remember.yaml",
            contractPath=contract_path,
        ),
        status="input-required",
        phase="contract-finalization",
        queuedAt="2026-08-25T00:00:00+00:00",
        currentCommand="await exact organizational repair",
        reportPath="/candidate/integration-operation.json",
        result=result,
        organizationalRepair=repair,
    )


def _canonical_repair_result(contract_path: str, generation: int) -> dict[str, object]:
    preview = {
        "contract_path": contract_path,
        "operation_kind": "integrate",
        "action": "cancel",
        "expected_generation": generation,
        "dry_run": True,
    }
    return {
        "state": "organizational-completion-gate-failed",
        "developerDecisionRequired": True,
        "safeToReplace": False,
        "superRefsMoved": False,
        "ok": False,
        "operation": "worktree_integrate",
        "nextTool": "worktree_operation_control",
        "nextArgs": preview,
        "applyStep": {
            "nextTool": "worktree_operation_control",
            "nextArgs": {**preview, "dry_run": False},
        },
    }


PREFLIGHTS = (
    PreflightSpec(
        cause_id="schema:lifecycle-operation-terminalization:v1",
        owner=Path("mcp/src/agents_remember/application/lifecycle/lifecycle_operation_worker.py"),
        evidence_altitude="integration-lifecycle-schema",
        corrective_owner=Path(
            "mcp/src/agents_remember/application/lifecycle/lifecycle_operation_worker.py"
        ),
        validator=_validate_lifecycle_terminalization,
    ),
)


def preflight_scope_units() -> str:
    """Render the non-vacuous population from the canonical preflight registry."""

    if not PREFLIGHTS:
        raise RuntimeError("causal preflight registry is empty")
    return (
        f"{len(PREFLIGHTS)} registered owner preflight(s) plus candidate-resolved "
        "consumer populations"
    )


def candidate_identity(project_root: Path) -> dict[str, object]:
    tree = _candidate_tree(project_root)
    attempt = _quality_attempt_nonce()
    environment, environment_id = _environment_identity()
    return {
        "tree": tree,
        "environmentId": environment_id,
        "attemptNonceSha256": hashlib.sha256(attempt.encode()).hexdigest(),
        "environment": environment,
        "rawInputs": {
            "preflightOwners": [spec.owner.as_posix() for spec in PREFLIGHTS],
            "qualityAttemptNoncePresent": True,
        },
    }


def _candidate_tree(project_root: Path) -> str:
    completed = run_git(project_root.resolve(), ["write-tree"])
    tree = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", tree) is None:
        raise RuntimeError("causal preflight could not bind the candidate index tree")
    return tree


def _quality_attempt_nonce() -> str:
    attempt = os.environ.get(QUALITY_ATTEMPT_NONCE_ENV, "")
    if re.fullmatch(r"[0-9a-f]{32}", attempt) is None:
        raise RuntimeError(f"{QUALITY_ATTEMPT_NONCE_ENV} is absent or invalid")
    return attempt


def _environment_identity() -> tuple[dict[str, str], str]:
    environment = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }
    environment_json = json.dumps(environment, sort_keys=True, separators=(",", ":"))
    return environment, hashlib.sha256(environment_json.encode()).hexdigest()


def failed_report(path: Path) -> bool:
    """Whether a nonzero preflight step produced a valid owned-cause artifact."""

    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(raw, dict)
        and raw.get("schemaVersion") == CAUSAL_REPORT_SCHEMA
        and raw.get("status") == "failed"
        and isinstance(raw.get("preflights"), list)
        and isinstance(raw.get("blockedGroups"), list)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="validate high-fanout prerequisite owners")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        require_dagger_admission(subject="Agents Remember causal preflights")
    except DaggerAdmissionError as error:
        print(str(error))
        return 2
    args = build_parser().parse_args(argv)
    root = args.project_root.resolve()
    payload = evaluate_preflights(
        PREFLIGHTS,
        DependencyOwnershipGraph(root),
        candidate=candidate_identity(root),
    )
    write_causal_report(args.report, payload)
    blocked = cast(list[dict[str, object]], payload["blockedGroups"])
    print(f"causal-preflight: {payload['status']} ({len(blocked)} graph-proven dependent groups)")
    return 1 if payload["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
