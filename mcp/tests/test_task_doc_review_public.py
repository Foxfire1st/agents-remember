from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from test_task_document import _config


def _target() -> TaskDocTarget:
    return TaskDocTarget(repo_id="agents-remember", task_name="review-api", slug="review_api")


def _create(config: McpRuntimeConfig) -> dict[str, Any]:
    return task_doc_tool(
        config,
        TaskDocTarget(repo_id="agents-remember", task_name="review-api"),
        operation="create",
        edit=TaskDocEdit(
            fields={
                "id": "review-api",
                "slug": "review_api",
                "title": "Review API",
                "kind": "subTask",
                "repo": "agents-remember",
                "type": "Code",
                "createdAt": "2026-01-01T00:00",
            }
        ),
    )


def _call(
    config: McpRuntimeConfig,
    operation: str,
    *,
    review: dict[str, object] | None = None,
    fields: dict[str, object] | None = None,
) -> dict[str, Any]:
    return task_doc_tool(
        config,
        _target(),
        operation=operation,
        edit=TaskDocEdit(review=review, fields=fields),
    )


def test_public_review_operations_seal_then_shrink_findings(tmp_path: Path) -> None:
    config = _config(tmp_path)
    created = _create(config)
    assert created["reviewState"] == {
        "round": 0,
        "pending": False,
        "baselineFindings": [],
        "remainingFindingIds": [],
    }

    begun = _call(config, "begin_review", review={})
    assert begun["reviewState"]["round"] == 1
    assert begun["reviewState"]["pending"] is True
    resumed = _call(config, "begin_review", review={})
    assert resumed["reviewState"] == begun["reviewState"]

    first = _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [
                {"findingId": "A", "description": "first issue"},
                {"findingId": "B", "description": "second issue"},
            ],
        },
    )
    assert first["reviewState"]["pending"] is False
    assert first["reviewState"]["remainingFindingIds"] == ["A", "B"]

    _call(config, "begin_review", review={})
    second = _call(
        config,
        "record_review",
        review={"verdict": "block", "remainingFindingIds": ["B"]},
    )
    assert second["reviewState"]["round"] == 2
    assert second["reviewState"]["baselineFindings"] == first["reviewState"]["baselineFindings"]
    assert second["reviewState"]["remainingFindingIds"] == ["B"]

    _call(config, "begin_review", review={})
    final = _call(
        config,
        "record_review",
        review={"verdict": "pass", "remainingFindingIds": []},
    )
    assert final["reviewState"]["round"] == 3
    assert final["reviewState"]["remainingFindingIds"] == []


def test_public_review_api_requires_begin_and_rejects_generic_state_reset(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)

    with pytest.raises(TaskDocError, match="review-admission-required"):
        _call(config, "record_review", review={"verdict": "pass", "findings": []})

    _call(config, "begin_review", review={})
    with pytest.raises(TaskDocError, match="cannot add, remove, or change reviewState"):
        _call(
            config,
            "replace",
            fields={
                "id": "review-api",
                "slug": "review_api",
                "title": "Review API changed",
                "kind": "subTask",
                "repo": "agents-remember",
                "type": "Code",
                "createdAt": "2026-01-01T00:00",
            },
        )


def test_successor_cannot_add_a_new_finding(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    _call(config, "begin_review", review={})
    _call(
        config,
        "record_review",
        review={"verdict": "block", "findings": [{"findingId": "A", "description": "issue"}]},
    )
    _call(config, "begin_review", review={})

    with pytest.raises(TaskDocError, match="review-successor-new-findings"):
        _call(
            config,
            "record_review",
            review={
                "verdict": "block",
                "findings": [{"findingId": "B", "description": "new issue"}],
            },
        )


def test_public_review_api_enforces_cap_and_carries_explicit_extra_rounds(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)

    first_pending = _call(config, "begin_review", review={})
    assert _call(config, "begin_review", review={})["reviewState"] == first_pending["reviewState"]
    _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "issue"}],
        },
    )
    for expected_round in (2, 3):
        begun = _call(config, "begin_review", review={})
        assert begun["reviewState"]["round"] == expected_round
        _call(
            config,
            "record_review",
            review={"verdict": "block", "remainingFindingIds": ["A"]},
        )

    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=3.*limit=3"):
        _call(config, "begin_review", review={})

    approval = {
        "developerApproval": "Developer authorizes one extra fix verification.",
        "additionalRounds": 1,
    }
    fourth_pending = _call(config, "begin_review", review=approval)
    state = fourth_pending["reviewState"]
    assert state["round"] == 4
    assert state["pending"] is True
    assert state["developerApproval"] == approval["developerApproval"]
    assert state["additionalRounds"] == 1
    assert state["baselineFindings"] == [{"findingId": "A", "description": "issue"}]
    assert state["remainingFindingIds"] == ["A"]

    assert _call(config, "begin_review", review=approval)["reviewState"] == state
    _call(
        config,
        "record_review",
        review={"verdict": "block", "remainingFindingIds": ["A"]},
    )
    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=4.*limit=4"):
        _call(config, "begin_review", review={})

    fifth_pending = _call(
        config,
        "begin_review",
        review={
            "developerApproval": "Developer authorizes one further verification.",
            "additionalRounds": 1,
        },
    )
    state = fifth_pending["reviewState"]
    assert state["round"] == 5
    assert state["additionalRounds"] == 2
    assert state["remainingFindingIds"] == ["A"]
