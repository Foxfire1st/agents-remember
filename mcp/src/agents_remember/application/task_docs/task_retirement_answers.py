"""What the retire operation answers: the result of a request, and what a dry run would change."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.controlplane.closeout_queue_store import queue_store_paths
from agents_remember.kernel.file_lock import lock_path_for
from agents_remember.models.task_retirement import MasterRetirementProof
from agents_remember.tasks import SubTaskRef, TaskDocument
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.task_retirement import MasterRetirementScope, RetirementReadiness

from .task_retirement_records import Observed, proof_path
from .task_retirement_shared import RESTART_NOTICE, archive_hook, hook_failed, hook_remainder
from .task_sprint_context import SprintLinkageRequest

RetirementState = Literal[
    "not-recorded",
    "sprint-edited",
    "proof-written",
    "folder-archived",
    "hook-failed",
    "hook-finished",
]


@dataclass(frozen=True)
class RetirementPlan:
    """One admitted request: the state it found and everything its transitions need."""

    state: RetirementState
    observed: Observed
    proof: MasterRetirementProof
    owner: ResolvedTaskDocument | None
    candidate: TaskDocument | None
    row: SubTaskRef | None
    readiness: RetirementReadiness | None
    scope: MasterRetirementScope

    @property
    def resumed(self) -> bool:
        return self.state != "not-recorded"

    @property
    def same_request(self) -> str:
        return (
            "masterRef, reason and removeEdges"
            if self.owner is not None
            else "masterRef and reason"
        )


def preview_answer(
    request: SprintLinkageRequest, plan: RetirementPlan, result: dict[str, Any], folder: Path
) -> dict[str, Any]:
    """A dry run says exactly what a real run would change, and that nothing would when so.

    ``wouldChange`` lists the steps in words and ``wouldWrite`` every file a real run writes: the
    sprint's document and its rendered page, the retirement record, the queue projection and the
    cleanup receipts. The folder move and the deletions of the hook are steps, not written files.
    """

    report = archive_hook(request, folder)
    result["taskArchive"]["reviewArtifacts"] = report
    remainder = hook_remainder(report)
    changes = _pending_steps(plan)
    written = _pending_files(plan, result)
    if result["projectionEffects"]:
        assert plan.owner is not None
        state_file = queue_store_paths(request.coordination_root, plan.owner.ref)[0]
        changes.append(f"closeout-queue projection of the sprint: refresh {state_file}")
        written += [state_file, lock_path_for(state_file)]
    if remainder:
        changes.append(
            f"review artifacts: delete {remainder} (listed under taskArchive.reviewArtifacts)"
        )
    if hook_failed(report):
        changes.append(
            "review artifacts: retry what the hook cannot reach (listed under "
            "taskArchive.reviewArtifacts.failures)"
        )
    if report.get("receipt") == "would-write":
        # The hook runs on the archived folder; a dry run before the move looked at the live one.
        receipts = [
            plan.observed.archive / Path(name).relative_to(folder)
            for name in report.get("receiptFiles", [])
        ]
        changes.append(
            f"cleanup receipt: write receipt {report.get('attempt')} at "
            f"{receipts[-1] if receipts else plan.observed.archive}"
        )
        written += receipts
    result["wouldChange"] = changes
    result["wouldWrite"] = [path.as_posix() for path in written]
    if plan.state == "folder-archived":
        result["state"] = "would-clean-up" if changes else "retired"
        result["retirementState"] = _archived_state(report, remainder, pending=bool(changes))
        if not changes:
            result["detail"] = (
                "Nothing would change: the retirement is recorded, the folder is archived and "
                f"the cleanup is complete. {RESTART_NOTICE}"
            )
    return result


def _archived_state(report: dict[str, Any], remainder: int, *, pending: bool) -> RetirementState:
    """The state of a retirement whose folder is archived, read from the hook and its receipts.

    ``hook-failed`` while the hook names a failure, or while its last attempt recorded one and
    something is still left to do; ``hook-finished`` when the hook has nothing left to do;
    ``folder-archived`` otherwise, which is before the hook's first completed attempt.
    """

    if hook_failed(report) or (pending and report.get("previousAttempt") == "failed"):
        return "hook-failed"
    if not remainder and report.get("receipt") != "would-write":
        return "hook-finished"
    return "folder-archived"


def _pending_steps(plan: RetirementPlan) -> list[str]:
    """The transitions before the cleanup that a real run would still perform, in plain words."""

    observed, steps = plan.observed, []
    if plan.state == "not-recorded" and plan.owner is not None:
        assert plan.row is not None
        steps.append(
            f"sprint {plan.owner.ref.key}: remove {len(plan.proof.removedOrchestrates)} membership "
            f"entry(ies), {plan.proof.removedGraphNodes} graph node(s) and "
            f"{len(plan.proof.removedEdges)} edge(s); record the retirement on row "
            f"{plan.row.number!r}; write its task.json and its rendered task.md"
        )
    if plan.state == "sprint-edited":
        assert plan.owner is not None
        steps.append(
            f"sprint {plan.owner.ref.key}: write its task.json and its rendered task.md again, as "
            "the recorded retirement reads (the page may be behind the document)"
        )
    if plan.state == "not-recorded" and plan.owner is None:
        steps.append(f"retirement record: write {proof_path(observed.live)}")
    if plan.state != "folder-archived":
        steps.append(f"task folder: move {observed.live} to {observed.archive}")
    return steps


def _pending_files(plan: RetirementPlan, result: dict[str, Any]) -> list[Path]:
    """The files the transitions before the cleanup write: the sprint's pair, or the record."""

    if plan.state == "folder-archived":
        return []
    if plan.owner is None:
        return [proof_path(plan.observed.live)] if plan.state == "not-recorded" else []
    return [
        Path(document[key])
        for document in result["documents"]
        for key in ("docPath", "renderedPath")
    ]


def first_answer(request: SprintLinkageRequest, plan: RetirementPlan) -> dict[str, Any]:
    """The answer every request starts from: the record, the state found, where the folder is."""

    observed, proof = plan.observed, plan.proof
    archived = plan.state == "folder-archived"
    result: dict[str, Any] = {
        "ok": True,
        "operation": "task_doc.retire_master",
        "state": "would-retire" if request.dry_run else "retired",
        "dryRun": request.dry_run,
        "masterRef": proof.masterRef.model_dump(mode="json"),
        "retirementState": plan.state,
        "retirementResumed": plan.resumed,
        "detail": RESTART_NOTICE,
        "removedOrchestrates": proof.removedOrchestrates,
        "removedGraphNodes": proof.removedGraphNodes,
        "removedEdges": [edge.model_dump(mode="json") for edge in proof.removedEdges],
        "readinessFacts": list(proof.readinessFacts),
        "documents": [],
        "projectionEffects": [],
        "taskArchive": {
            "state": "would-archive" if request.dry_run and not archived else "archived",
            "taskRoot": observed.live.as_posix(),
            "archivePath": observed.archive.as_posix(),
        },
    }
    if plan.owner is None:
        result["retirementProof"] = proof.model_dump(mode="json")
        return result
    assert plan.row is not None
    result["sprintTaskDocumentRef"] = plan.owner.ref.model_dump(mode="json")
    result["retirementRow"] = plan.row.model_dump(mode="json", exclude_none=True)
    if plan.row.file:
        # A retired row carries a file cell only when it took the place of a legacy seat row.
        result["replacedLegacyRow"] = {"number": plan.row.number, "file": plan.row.file}
    return result
