"""Structural proof for architect-owned requirement compilation before task topology."""

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


def test_architect_gate_compiles_and_approves_requirements_before_topology() -> None:
    _assert_terms(
        LIFECYCLE / "roles/architect.md",
        "Mandatory Requirement-Compilation Gate — Before Task Topology",
        "stable ID and explicit version",
        "violated, reviewed, owned, evidenced, or superseded independently",
        "version-addressed packet per ID + version",
        "fresh agent without the planning transcript",
        "Present the complete corpus and stop for developer approval",
        "Only after approval",
        "Each leaf owns exactly one primary requirement revision",
        "Invalidate acceptance state",
        "rebrief the affected leaves",
    )
    _assert_terms(
        LIFECYCLE / "SKILL.md",
        "Requirement compilation precedes task topology",
        "canonical requirement index with a stable ID and explicit version",
        "creates task topology only after that approval",
        "filtered ID + version + canonical-packet links",
    )


def test_requirement_packet_is_self_contained_and_cold_readable() -> None:
    packet = TASK_WORKFLOW / "requirement-packet-template.md"
    _assert_terms(
        packet,
        "## Normative Requirement",
        "## Problem",
        "## Required Behavior",
        "## Rationale",
        "## Scope",
        "## Exclusions",
        "## Preservation Boundaries",
        "## Failure And Recovery Behavior",
        "## Examples",
        "## Forbidden Overreach",
        "## Interaction Diagram",
        "## Expected Evidence",
        "### Deliverable Evidence",
        "### Verification Evidence",
        "## Authority And Provenance",
        "## Dependencies",
        "## Open Truth Gaps",
        "## Cold-Read Verification",
        "<stable-id>-<version>-<slug>.md",
        "Do not overwrite the approved prior packet",
        "What changes?",
        "What remains unchanged?",
        "Important failure states?",
        "What proves conformance?",
    )


def test_task_workflow_projects_canonical_revisions_without_rewriting_them() -> None:
    _assert_terms(
        TASK_WORKFLOW / "workflow.md",
        "Phase 0 — Compile And Approve Requirements Before Task Topology",
        "requirements/README.md",
        "<stable-id>-<version>-<slug>.md",
        "present the complete corpus to the developer and stop for approval",
        "Only after the corpus is approved may Phase 1 create task documents",
        "Every leaf owns exactly one primary requirement revision",
        "Changed requirement | Increment the existing ID's version",
    )
    _assert_terms(
        TASK_WORKFLOW / "template.md",
        "## Requirement Projection",
        "**R1 @ v1** — `primary` — [canonical packet]",
        "requirements/R1-v1-<slug>.md",
        "the linked packets are the requirement contracts and this task does not rewrite them",
        "A leaf owns exactly one `primary` requirement revision",
        "invalidates affected acceptance state",
    )
    _assert_terms(
        TASK_WORKFLOW / "master-template.md",
        "## Filtered Requirement Projection",
        "| Stable ID | Version | Canonical packet | Manifestation sub-task(s) |",
        "## Primary Requirement Revision",
        "## Adjacent Requirement Constraints",
        "a leaf that would close multiple independently falsifiable requirements must be split",
    )


def test_handoffs_and_reviews_bind_the_same_requirement_revision() -> None:
    _assert_terms(
        LIFECYCLE / "roles/manager.md",
        "is approved, and cites the durable corpus ruling",
        "unapproved, version-mismatched",
    )
    _assert_terms(
        LIFECYCLE / "templates/worker-brief.md",
        "Owned primary requirement (exactly one stable-ID + version)",
        "Canonical packet:",
        "Approved revision:",
        "version-mismatched",
    )
    _assert_terms(
        LIFECYCLE / "roles/worker.md",
        "is not approved, or lacks its durable corpus-ruling citation",
    )
    _assert_terms(
        LIFECYCLE / "roles/reviewer.md",
        "is approved, and records the durable corpus ruling",
        "missing, unapproved, or mismatched version",
    )
    _assert_terms(
        LIFECYCLE / "templates/turn-report.md",
        "Requirement Acceptance Envelope (exactly once for the owned primary stable ID + version)",
        "Canonical packet inspected:",
        "durable corpus-ruling citation",
    )
    _assert_terms(
        LIFECYCLE / "templates/verdict.md",
        "Mandatory Requirement Adjudication Block (repeat once per stable ID + version",
        "Canonical packet inspection:",
        "approved state",
        "mismatch/unapproved, therefore rejected",
    )
    _assert_terms(
        LIFECYCLE / "templates/curator-brief.md",
        "Primary requirement revision: `<stable-id>@<version>`",
        "canonical packet `<packet-path>`",
        "reviewer's independent",
        "worker-blocked revision",
        "exact approved requirement revision and the reviewer's accepted adjudication",
    )
    _assert_terms(
        LIFECYCLE / "roles/curator.md",
        "stable-ID + version canonical packet",
        "missing, unapproved, or version-mismatched",
        "rejected or worker-blocked requirement",
        "exact accepted requirement revision, accepted reviewer adjudication, and separate durable developer ruling",
    )
