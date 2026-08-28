"""Structural proof for immutable requirement-attempt journal doctrine."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = REPOSITORY_ROOT / "skills/l-01-agent-lifecycles"
TASK_WORKFLOW = REPOSITORY_ROOT / "skills/w-02-light-task-workflow"

FAILURE_CLASSES = (
    "implementation defect",
    "evidence gap",
    "requirement contradiction/overconstraint",
    "test/tool defect",
    "external blocker",
)


def _text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _assert_terms(path: Path, *terms: str) -> None:
    text = _text(path)
    missing = [term for term in terms if term not in text]
    assert not missing, f"{path.relative_to(REPOSITORY_ROOT)} missing {missing!r}"


def test_worker_appends_an_immutable_candidate_bound_attempt_before_handoff() -> None:
    _assert_terms(
        LIFECYCLE / "roles/worker.md",
        "append one immutable `worker-delivery-attempt` record",
        "Before a review handoff",
        "requirement revision, leaf manifestation, leaf-local attempt ID",
        "exact candidate tree/commit",
        "content-addressed reference to the immutable expanded evidence artifact",
        "do not copy the complete master acceptance-envelope document",
        "append a successor attempt",
        "never rewrite the requirement",
    )
    _assert_terms(
        LIFECYCLE / "templates/worker-brief.md",
        "Leaf Requirement Attempt Journal:",
        "one physical append-only journal",
        "Leaf manifestation:",
        "Next handoff attempt ID:",
        "Predecessor attempt and carried findings:",
        "Exact candidate identity class:",
        "Never edit a prior record",
    )
    _assert_terms(
        LIFECYCLE / "templates/turn-report.md",
        "Requirement Attempt Journal Records Appended For This Handoff",
        "single physical append-only",
        "Independent reviewers append separate adjudication records to that same journal",
        "Record kind: `worker-delivery-attempt`",
        "Predecessor attempt:",
        "Exact candidate:",
        "Expanded evidence artifact:",
        "Record appended at:",
        "Prior blocks are immutable",
        "transient authoring input only",
        "remove this rendered scaffold from the completed turn report",
        "must never remain as a duplicate `Worker Attempt Record` authority",
        "Requirement Acceptance Envelope (exactly once for the owned primary stable ID + version)",
        "Experimental Protocol Events (separate from delivery attempts)",
        "## Checks",
    )


def test_reviewer_appends_an_independent_exact_attempt_adjudication() -> None:
    _assert_terms(
        LIFECYCLE / "roles/reviewer.md",
        "Per-Requirement Independent Attempt Adjudication",
        "exact requirement revision, leaf manifestation, leaf-local attempt ID",
        "append a separate immutable reviewer record",
        "without modifying the worker record",
        "unadjudicated candidate for this manifestation moved during review",
        "require a successor worker attempt plus reviewer record",
    )
    verdict = LIFECYCLE / "templates/verdict.md"
    _assert_terms(
        verdict,
        "Adjudicate every requirement attempt separately",
        "same single physical leaf",
        "link that exact journal anchor instead of copying",
        "Record kind: `reviewer-attempt-adjudication`",
        "Worker attempt ID:",
        "Worker record reference:",
        "Exact candidate inspected:",
        "Reviewer adjudication: `accepted` | `rejected`",
        "This verdict cannot reopen work by itself",
    )
    for failure_class in FAILURE_CLASSES:
        assert failure_class in _text(verdict)
    _assert_terms(
        LIFECYCLE / "roles/reviewer.md",
        "Append a new reviewer record for the successor attempt to the authoritative leaf Requirement Attempt Journal",
        "link its exact journal anchor from the verdict artifact",
    )
    _assert_terms(
        verdict,
        "newly developer-authorized changed deliveries",
        "approved new requirement version cannot inherit the prior version's acceptance",
    )


def test_acceptance_reopens_only_through_bounded_owner_authority() -> None:
    for path in (
        LIFECYCLE / "SKILL.md",
        LIFECYCLE / "roles/reviewer.md",
        LIFECYCLE / "roles/manager.md",
        LIFECYCLE / "templates/manager-brief.md",
        TASK_WORKFLOW / "workflow.md",
        TASK_WORKFLOW / "template.md",
        TASK_WORKFLOW / "master-template.md",
    ):
        _assert_terms(
            path,
            "independent reviewer proves",
            "owning manager",
            "architect in a flat run",
            "bounded invalidation",
        )
        _assert_terms(path, "unrelated later candidate", "does not reopen")
    _assert_terms(
        LIFECYCLE / "SKILL.md",
        "accepted attempt, reviewer record, regressing candidate, and affected set",
        "cannot reopen acceptance unilaterally",
    )


def test_failure_taxonomy_and_requirement_revision_authority_are_closed() -> None:
    for path in (
        LIFECYCLE / "SKILL.md",
        LIFECYCLE / "roles/worker.md",
        LIFECYCLE / "roles/reviewer.md",
        LIFECYCLE / "roles/manager.md",
        LIFECYCLE / "templates/worker-brief.md",
        LIFECYCLE / "templates/verdict.md",
        TASK_WORKFLOW / "template.md",
        TASK_WORKFLOW / "master-template.md",
    ):
        text = _text(path)
        for failure_class in FAILURE_CLASSES:
            assert failure_class in text, (
                f"{path.relative_to(REPOSITORY_ROOT)} missing {failure_class!r}"
            )
    _assert_terms(
        LIFECYCLE / "roles/architect.md",
        "cannot edit the contract",
        "presents any proposed semantic revision to the developer",
        "only that handoff, or a successor handoff after reviewer rejection, advances the delivery-attempt lineage",
    )


def test_leaf_journals_are_authority_and_master_summary_is_rebuildable_non_gating() -> None:
    _assert_terms(
        LIFECYCLE / "SKILL.md",
        "detailed per-leaf worker and reviewer records are authority",
        "rebuildable summary",
        "attempts, rejection history, current state, and dominant open failure class",
        "never a requirement contract, lifecycle/closeout gate, queue authority, or task-authoring lock",
    )
    _assert_terms(
        LIFECYCLE / "roles/manager.md",
        "Maintain the rebuildable master Requirement Attempt Summary",
        "rejection history/count",
        "leaf journal references",
        "never authorizes or blocks task authoring, lifecycle, closeout, integration, or queue operations",
        "leaf records win",
    )
    _assert_terms(
        TASK_WORKFLOW / "master-template.md",
        "Requirement Attempt Summary (rebuildable projection — never a gate)",
        "Authoritative leaf journal refs",
        "Rebuild this table from the detailed append-only leaf worker/reviewer records",
        "leaf journals win",
        "never a task/lifecycle/closeout/integration/queue gate or authority",
    )


def test_task_workflow_preserves_attempt_lineage_without_runtime_fallbacks() -> None:
    _assert_terms(
        TASK_WORKFLOW / "workflow.md",
        "advance a worker delivery attempt only when an exact candidate is handed to independent review",
        "Internal implementation, test, and evidence reruns are experimental protocol events, not attempts",
        "rejected-attempt repair creates a successor attempt",
        "unrelated later candidate does not reopen an accepted attempt",
        "append a separate immutable reviewer record without changing the worker record",
        "Requirement Attempt Journal and remain authoritative",
        "Missing or stale summary state is rebuilt from leaf journals and cannot block work",
    )
    _assert_terms(
        TASK_WORKFLOW / "template.md",
        "Keep semantic requirement versions separate from delivery attempts",
        "prior records are never edited or deleted",
        "Requirement problems require developer-approved revision",
    )


def test_internal_protocol_runs_do_not_advance_delivery_attempts() -> None:
    for path in (
        LIFECYCLE / "SKILL.md",
        LIFECYCLE / "roles/worker.md",
        LIFECYCLE / "roles/architect.md",
        LIFECYCLE / "templates/worker-brief.md",
        LIFECYCLE / "templates/turn-report.md",
        LIFECYCLE / "templates/manager-brief.md",
        TASK_WORKFLOW / "workflow.md",
        TASK_WORKFLOW / "template.md",
        TASK_WORKFLOW / "master-template.md",
    ):
        _assert_terms(path, "experimental protocol event")
    _assert_terms(
        LIFECYCLE / "templates/turn-report.md",
        "Candidate identity",
        "Exact command",
        "Failure cause",
        "Repair made",
        "Expected proof next run",
        "never consume a worker-attempt ID",
    )


def test_attempt_records_are_lightweight_content_addressed_views() -> None:
    for path in (
        LIFECYCLE / "SKILL.md",
        LIFECYCLE / "roles/worker.md",
        LIFECYCLE / "templates/worker-brief.md",
        LIFECYCLE / "templates/turn-report.md",
        LIFECYCLE / "templates/manager-brief.md",
        TASK_WORKFLOW / "workflow.md",
        TASK_WORKFLOW / "template.md",
        TASK_WORKFLOW / "master-template.md",
    ):
        _assert_terms(path, "content-addressed")
        text = _text(path)
        assert "complete master" in text or "complete acceptance-envelope" in text


def test_attempt_boundary_distinguishes_pre_handoff_correction_from_rejected_successor() -> None:
    for path in (
        LIFECYCLE / "SKILL.md",
        LIFECYCLE / "roles/worker.md",
        LIFECYCLE / "roles/manager.md",
        LIFECYCLE / "roles/reviewer.md",
        LIFECYCLE / "templates/worker-brief.md",
        LIFECYCLE / "templates/turn-report.md",
        LIFECYCLE / "templates/manager-brief.md",
        LIFECYCLE / "templates/verdict.md",
        TASK_WORKFLOW / "workflow.md",
        TASK_WORKFLOW / "template.md",
        TASK_WORKFLOW / "master-template.md",
    ):
        _assert_terms(
            path,
            "malformed pre-handoff row",
            "`non-attempt-correction`/void reference",
            "malformed handed-off row",
        )
    _assert_terms(
        LIFECYCLE / "SKILL.md",
        "one logical formal-attempt boundary",
        "consumes no attempt ID",
        "independent reviewer rejects it",
        "worker never self-rejects",
    )
    _assert_terms(
        LIFECYCLE / "roles/worker.md",
        "no formal attempt was consumed",
        "do not self-reject it",
        "successor is appended only with the next candidate handoff",
    )
    _assert_terms(
        LIFECYCLE / "roles/reviewer.md",
        "formal attempt. Reject it independently",
        "require a successor only when the worker hands off the next exact candidate",
    )
