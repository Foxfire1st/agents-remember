"""State conversion normalizes review verdict references to durable ``notes/reports/<rel>`` paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.memory.conversion.state_conversion import plan_state_conversion
from agents_remember.memory.conversion.state_owners import Owner, review_fact
from agents_remember.memory.conversion.task_review_facts import PassedReview, TaskOwners
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.converted_reviews import (
    CONVERTED_REVIEWS_PATH,
    ConvertedReviews,
)

AT = "2026-11-26T01:45:00+01:00"
TASK = "261112-KNO"
OWNER = Owner("leaf", "261112-KNO-L4")
DOC = Path("/tasks/agents-remember/261112_kno/04_leaf.json")


@pytest.mark.parametrize(
    ("raw_ref", "expected"),
    [
        pytest.param(
            "/home/firefox/projects/ar-coordination/worktrees/agents-remember/"
            "04_the-brief-of-a-file-0df6c23e90-ar/role-reports/reviewer/"
            "9fb0e423-7dfb-4dd7-9525-d306ca394f01/memory-round-4/VERDICT.md",
            "notes/reports/reviewer/9fb0e423-7dfb-4dd7-9525-d306ca394f01/memory-round-4/VERDICT.md",
            id="absolute-worktree-role-reports",
        ),
        pytest.param(
            "role-reports/reviewer/e0769575-0bab-4e1f-86a6-44c8cb307f53/memory-round-3/verdict.md",
            "notes/reports/reviewer/e0769575-0bab-4e1f-86a6-44c8cb307f53/memory-round-3/verdict.md",
            id="relative-role-reports",
        ),
        pytest.param(
            "./role-reports/reviewer/e0769575/role-launch/verdict.md",
            "notes/reports/reviewer/e0769575/role-launch/verdict.md",
            id="dot-slash-role-reports",
        ),
        pytest.param(
            "/home/firefox/projects/ar-coordination/tasks/agents-remember/"
            "261112_knowledge-tools-and-retrieval/notes/reports/reviewer/req-1/M2-verdict.md",
            "notes/reports/reviewer/req-1/M2-verdict.md",
            id="absolute-notes-reports",
        ),
        pytest.param(
            "notes/reports/reviewer/req-1/M2-verdict.md",
            "notes/reports/reviewer/req-1/M2-verdict.md",
            id="already-relative-notes-reports",
        ),
        pytest.param(
            "./notes/reports/reviewer/req-1/M2-verdict.md",
            "notes/reports/reviewer/req-1/M2-verdict.md",
            id="dot-slash-notes-reports",
        ),
        pytest.param(
            r"  C:\worktrees\leaf-ar\role-reports\reviewer\req-2\verdict.md  ",
            "notes/reports/reviewer/req-2/verdict.md",
            id="windows-backslashes-and-whitespace",
        ),
        pytest.param(
            r".\role-reports\reviewer\req-2\verdict.md",
            "notes/reports/reviewer/req-2/verdict.md",
            id="relative-backslashes",
        ),
    ],
)
def test_review_fact_normalizes_report_verdict_refs(raw_ref: str, expected: str) -> None:
    owners = TaskOwners(tasks={OWNER.id: TASK})
    passed = PassedReview(round=3, verdict_ref=raw_ref, tree=None, document=DOC)

    fact = review_fact(OWNER, passed, owners, at=AT)

    assert fact.verdict_ref == expected


@pytest.mark.parametrize(
    ("raw_ref", "expected"),
    [
        pytest.param("role-reports/", "role-reports/", id="empty-rel-role-reports"),
        pytest.param(
            "/worktrees/leaf/role-reports/",
            "/worktrees/leaf/role-reports/",
            id="empty-rel-abs-role-reports",
        ),
        pytest.param("notes/reports/", "notes/reports/", id="empty-rel-notes-reports"),
        pytest.param(
            "role-reports/../task.json",
            "role-reports/../task.json",
            id="traversal-relative-role-reports",
        ),
        pytest.param(
            "/worktrees/leaf/role-reports/reviewer/../../secrets.md",
            "/worktrees/leaf/role-reports/reviewer/../../secrets.md",
            id="traversal-absolute-role-reports",
        ),
        pytest.param(
            "notes/reports/../task.json",
            "notes/reports/../task.json",
            id="traversal-notes-reports",
        ),
        pytest.param(
            "verdicts/custom-round-1.md",
            "verdicts/custom-round-1.md",
            id="non-matching-usable-ref",
        ),
        pytest.param(
            "https://example.invalid/role-reports/verdict.md",
            "https://example.invalid/role-reports/verdict.md",
            id="url-containing-role-reports",
        ),
    ],
)
def test_review_fact_leaves_invalid_or_non_report_refs_unchanged(
    raw_ref: str, expected: str
) -> None:
    owners = TaskOwners(tasks={OWNER.id: TASK})
    passed = PassedReview(round=1, verdict_ref=raw_ref, tree=None, document=DOC)

    fact = review_fact(OWNER, passed, owners, at=AT)

    assert fact.verdict_ref == expected


def test_plan_state_conversion_writes_durable_verdict_refs_in_converted_reviews() -> None:
    l1, l2, l3 = "261112-KNO-L1", "261112-KNO-L2", "261112-KNO-L3"
    files = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ).encode(),
        f"knowledge/history/{l1}.json": canonical_text(
            {"schema": "ar-history/v1", "leaf": l1, "closed": True, "rows": []}
        ).encode(),
        f"knowledge/history/{l2}.json": canonical_text(
            {"schema": "ar-history/v1", "leaf": l2, "closed": True, "rows": []}
        ).encode(),
        f"knowledge/history/{l3}.json": canonical_text(
            {"schema": "ar-history/v1", "leaf": l3, "closed": True, "rows": []}
        ).encode(),
    }
    owners = TaskOwners(
        tasks={l1: TASK, l2: TASK, l3: TASK},
        passed={
            l1: PassedReview(
                round=2,
                verdict_ref="/home/u/ar-coordination/worktrees/app/01-ar/role-reports/reviewer/r1/v.md",
                tree=None,
                document=DOC,
            ),
            l2: PassedReview(
                round=1,
                verdict_ref="role-reports/reviewer/r2/v.md",
                tree=None,
                document=DOC,
            ),
            l3: PassedReview(
                round=4,
                verdict_ref="/home/u/ar-coordination/tasks/app/261112_kno/notes/reports/reviewer/r3/v.md",
                tree=None,
                document=DOC,
            ),
        },
    )

    plan = plan_state_conversion(files, owners, at=AT)

    raw_converted = plan.changes[CONVERTED_REVIEWS_PATH]
    parsed = ConvertedReviews.model_validate(json.loads(raw_converted.decode("utf-8")))
    by_owner = {entry.owner: entry.fact.verdict_ref for entry in parsed.reviews}
    assert by_owner == {
        l1: "notes/reports/reviewer/r1/v.md",
        l2: "notes/reports/reviewer/r2/v.md",
        l3: "notes/reports/reviewer/r3/v.md",
    }
