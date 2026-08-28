"""Structural proof for the per-requirement acceptance-envelope doctrine."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = REPOSITORY_ROOT / "skills/l-01-agent-lifecycles"
TASK_WORKFLOW = REPOSITORY_ROOT / "skills/w-02-light-task-workflow"


def _text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _assert_terms(path: Path, *terms: str) -> None:
    text = _text(path)
    missing = [term for term in terms if term not in text]
    assert not missing, f"{path.relative_to(REPOSITORY_ROOT)} missing {missing!r}"


def test_worker_role_brief_and_report_require_one_complete_primary_block() -> None:
    _assert_terms(
        LIFECYCLE / "roles/worker.md",
        "one complete acceptance block for the owned primary requirement",
        "Write exactly one block for that owned primary stable ID + version",
        "inherited dependency and preservation revisions as separate preservation checks",
        "status `blocked` + the checks result + an exact escalation + respawn/recovery state",
        "status: exactly `satisfied`, `blocked`, or `approved-change`",
        "delivery/implementation rationale",
        "verification rationale explaining what behavior the evidence demonstrates",
        "the exact command and result, or a durable evidence reference",
        "cite the durable developer approval/ruling",
        "non-code work uses the deliverable path + section/anchor",
    )
    _assert_terms(
        LIFECYCLE / "templates/worker-brief.md",
        "Owned primary requirement (exactly one stable-ID + version)",
        "Repeat this block exactly once",
        "dependency/preservation constraints",
        "Required deliverable evidence class",
        "Required verification evidence class",
        'General prose and an aggregate "requirements addressed" statement do not satisfy',
    )
    _assert_terms(
        LIFECYCLE / "templates/turn-report.md",
        "Requirement Acceptance Envelope (exactly once for the owned primary stable ID + version)",
        "Canonical packet inspected:",
        "Delivery/implementation citations",
        "Demonstrated behavior",
        "Failure caught",
        "Test/verification citations",
        "Exact evidence:",
        "Developer approval/ruling:",
        "## Checks",
        "Exact command",
        "Durable evidence reference",
    )


def test_reviewer_role_and_verdict_require_independent_adjudication_per_id() -> None:
    _assert_terms(
        LIFECYCLE / "roles/reviewer.md",
        "Adjudicate every requirement revision separately as exactly `accepted` or `rejected`",
        "independently open the cited deliverable/implementation artifacts",
        "Missing rationale, missing or wrong-class evidence, an invalid citation, or absent developer approval forces `rejected`",
        "overall recommendation cannot be PASS or PASS-WITH-NOTES while any requirement is rejected",
    )
    verdict = LIFECYCLE / "templates/verdict.md"
    _assert_terms(
        verdict,
        "Mandatory Requirement Adjudication Block (repeat once per stable ID + version in every variant)",
        "Canonical packet inspection:",
        "Reviewer adjudication: `accepted` | `rejected`",
        "Evidence-class check:",
        "Reviewer rationale:",
        "Refutation attempted:",
        "Durable developer ruling:",
        "PASS and PASS-WITH-NOTES are forbidden while any requirement is rejected",
    )
    assert _text(verdict).count("## Requirement Adjudication") >= 3


def test_manager_and_task_workflow_preserve_primary_ownership_and_adjacent_context() -> None:
    _assert_terms(
        LIFECYCLE / "roles/manager.md",
        "enumerate the leaf's one owned primary revision by stable ID + version",
        "dependency/preservation constraints",
        "Require one independent `accepted`/`rejected` adjudication for that ID",
    )
    _assert_terms(
        LIFECYCLE / "templates/manager-brief.md",
        "compile the leaf's one owned primary revision",
        "Missing, duplicate, unstable, unapproved, version-mismatched, or aggregate-only identities make the dispatch invalid",
        "Dispatch the exact same owned primary stable-ID + version and worker envelope to the reviewer",
        "distinct from both the leaf's builder seat and the seat that authored the plan",
    )
    _assert_terms(
        TASK_WORKFLOW / "workflow.md",
        "maintain one acceptance block for the task's owned primary stable requirement ID + version",
        "independently review the owned primary stable-ID + version acceptance block as `accepted | rejected`",
    )
    _assert_terms(
        TASK_WORKFLOW / "template.md",
        "**R1 @ v1** — `primary` — [canonical packet]",
        "Builder per-requirement acceptance envelope:",
        "Independent per-requirement adjudication:",
    )
    _assert_terms(
        TASK_WORKFLOW / "master-template.md",
        "## Filtered Requirement Projection",
        "Every applicable ID + version must appear as the owned primary of at least one manifestation",
        "Every worker brief identifies exactly one owned primary requirement ID",
        "accepted adjudication still carries worker status `blocked`",
    )


def test_packet_supersession_and_leaf_gate_boundaries_are_explicit() -> None:
    _assert_terms(
        TASK_WORKFLOW / "requirement-packet-template.md",
        "State at packet freeze",
        "requirements/README.md` is the append-only authority that marks the prior version superseded",
        "immutable prior packet keeps its frozen approved state",
    )
    _assert_terms(
        TASK_WORKFLOW / "master-template.md",
        "change-set-scoped acceptance checks",
        "full-repository gate runs once at the master integration boundary",
    )


def test_durable_evidence_hold_point_is_explicitly_separate() -> None:
    for path in (
        LIFECYCLE / "SKILL.md",
        LIFECYCLE / "roles/worker.md",
        LIFECYCLE / "roles/reviewer.md",
        LIFECYCLE / "templates/worker-brief.md",
        LIFECYCLE / "templates/turn-report.md",
        LIFECYCLE / "templates/verdict.md",
        TASK_WORKFLOW / "workflow.md",
        TASK_WORKFLOW / "template.md",
        TASK_WORKFLOW / "master-template.md",
    ):
        text = _text(path).casefold()
        assert "durable-evidence" in text
        assert "separate" in text
