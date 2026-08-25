"""Live Git evidence used to authorize lifecycle cancellation and recovery."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.lifecycles.termination import LifecycleCancellationEvidence
from agents_remember.worktrees.integration.closeout.recovery_projection import (
    derive_closeout_recovery_commits,
)
from agents_remember.worktrees.integration.integration_ref_state import (
    classify_integration_refs,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    git_mutation_snapshot,
    reconcile_closeout_mutations,
)


def prove_cancellable_git(
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    publish: bool,
) -> tuple[LifecycleCancellationEvidence, LifecycleOperationRecord]:
    """Derive exact no-output evidence, publishing reconciliation only on apply."""

    if record.operationKind in {"closeout", "direct-landing"}:
        original = record
        record = _reconciled_closeout_record(original)
        facts = _cancellable_closeout_facts(record, publish=publish)
        if publish and record != original:
            current, matched = store.update_if_current(original, lambda _current: record)
            if not matched:
                # Cancellation already persisted its stop request, so a new
                # complete current record is bounded. Reclassify it rather than
                # overwriting worker/controller progress from the stale snapshot.
                record = _reconciled_closeout_record(current)
                facts = _cancellable_closeout_facts(record, publish=publish)
                if record != current:
                    record, matched = store.update_if_current(
                        current,
                        lambda _current: record,
                    )
                    if not matched:
                        raise LifecycleControlError(
                            "lifecycle-record-advanced",
                            "lifecycle evidence advanced during cancellation reconciliation",
                            expected={"generation": original.generation},
                            observed={
                                "generation": record.generation,
                                "status": record.status,
                                "phase": record.phase,
                            },
                            next_action="recover",
                        )
    else:
        facts = unchanged_integration_refs(record)
    return (
        LifecycleCancellationEvidence(
            operationKind=record.operationKind,
            generation=record.generation,
            workerExitProven=record.workerPid is None,
            expected=facts,
            observed=facts,
            provenAt=datetime.now(UTC).replace(microsecond=0).isoformat(),
        ),
        record,
    )


def _reconciled_closeout_record(
    record: LifecycleOperationRecord,
) -> LifecycleOperationRecord:
    if record.legacyMigration is not None:
        raise LifecycleControlError(
            "legacy-output-recovery-required",
            "migrated legacy code output is proven and cannot be cancelled or revised",
            expected={"codeCommit": record.legacyMigration.codeCommit},
            observed={"legacyDigest": record.legacyMigration.originalSha256},
            next_action="recover",
        )
    reconciled = reconcile_closeout_mutations(record, purpose="cancellation")
    recovery = derive_closeout_recovery_commits(record, mutations=reconciled)
    if reconciled != record.mutationEvidence or recovery != record.recoveryCommits:
        resolved = record.model_copy(
            update={
                "mutationEvidence": reconciled,
                "recoveryCommits": recovery,
                "irreversibleBoundaryEntered": any(
                    item.state == "commit-proven" for item in reconciled.values()
                ),
            }
        )
        record = resolved
    unsafe = {
        leg: item.state
        for leg, item in record.mutationEvidence.items()
        if item.state in {"mutation-intent", "commit-proven"}
    }
    if unsafe:
        raise LifecycleControlError(
            "lifecycle-output-recovery-required",
            "Git output is ambiguous or proven and must reconcile in this generation",
            expected={"allowed": ["pre-mutation", "reconciled-unchanged"]},
            observed=unsafe,
            next_action="recover",
        )
    return record


def _cancellable_closeout_facts(
    record: LifecycleOperationRecord,
    *,
    publish: bool,
) -> dict[str, str]:
    if not record.mutationEvidence:
        return {"mutationState": "not-applicable"}
    facts: dict[str, str] = {}
    report_root = Path(record.reportPath).parent
    for leg, evidence in record.mutationEvidence.items():
        accepted = evidence.acceptedBefore
        if accepted is None:
            raise LifecycleControlError(
                "lifecycle-cancellation-prestate-missing",
                "the generation predates exact accepted Git snapshots and cannot be cancelled",
                expected={"acceptedSnapshot": "required", "leg": leg},
                observed={"acceptedSnapshot": "missing", "leg": leg},
                next_action="recover",
            )
        if evidence.state == "reconciled-unchanged" and evidence.observed != accepted:
            raise LifecycleControlError(
                "lifecycle-output-recovery-required",
                "reconciled-unchanged evidence lost its exact live snapshot",
                next_action="recover",
            )
        if publish:
            snapshot = git_mutation_snapshot(
                Path(evidence.repository),
                report_root / f".{leg}-cancellation-evidence.index",
            )
        else:
            with tempfile.TemporaryDirectory(
                prefix=f"ar-{leg}-cancellation-preview-",
            ) as temporary:
                snapshot = git_mutation_snapshot(
                    Path(evidence.repository),
                    Path(temporary) / "index",
                )
        if snapshot != accepted:
            raise LifecycleControlError(
                "lifecycle-cancellation-git-changed",
                "live Git state no longer matches the generation's accepted prestate",
                expected={"leg": leg, **accepted.model_dump(mode="json")},
                observed={"leg": leg, **snapshot.model_dump(mode="json")},
                next_action="recover",
            )
        facts[f"{leg}State"] = evidence.state
        facts[f"{leg}Repository"] = evidence.repository
        for field in (
            "headRef",
            "head",
            "headTree",
            "indexTree",
            "candidateTree",
            "statusFingerprint",
            "refLogFingerprint",
        ):
            facts[f"{leg}{field[0].upper()}{field[1:]}"] = getattr(snapshot, field)
    return facts


def unchanged_integration_refs(record: LifecycleOperationRecord) -> dict[str, str]:
    """Require every accepted protected ref to remain at its exact before object."""

    facts = classify_integration_refs(record)
    if facts.state == "unchanged":
        return facts.before
    if facts.state == "conflict":
        raise LifecycleControlError(
            "integration-ref-conflict",
            "a protected source ref has an unexpected object",
            expected={"before": facts.before, "intended": facts.intended},
            observed=facts.observed_payload(),
            next_action="developer-decision",
        )
    raise LifecycleControlError(
        "integration-ref-recovery-required",
        "one or more protected refs moved to this generation's intended output",
        expected={"before": facts.before, "intended": facts.intended},
        observed=facts.observed_payload(),
        next_action="recover",
    )
