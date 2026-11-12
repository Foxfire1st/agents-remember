"""The whole leaf's code facts, reusing the family implementation-fact calculation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import apsw

from agents_remember.application.knowledge_worklist.classification_context import (
    WorklistClassification,
)
from agents_remember.application.review_change_kinds import implementation_facts
from agents_remember.application.review_lane_classification import (
    LaneReadError,
    bounded_text,
    open_tree_lane,
)
from agents_remember.application.review_tree_comparison import ReviewTrees, TreeKnowledge
from agents_remember.application.review_worklist_answers import worklist_bound
from agents_remember.models.knowledge.review_code_changes import ReviewCodeChanges
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
)


def tree_request(trees: ReviewTrees) -> dict[str, Any]:
    """The exact admitted comparison sent through the child pipe, never a new resolution."""
    return {
        "record": trees.record.model_dump(mode="json"),
        "live": trees.live,
        "sides": [
            {
                "wire": side.wire.model_dump(mode="json"),
                "database": None if side.database is None else str(side.database),
            }
            for side in (trees.before, trees.after)
        ],
    }


def trees_from_request(document: dict[str, Any]) -> ReviewTrees:
    record = ReviewTreeComparisonRecord.model_validate(document["record"])
    sides = tuple(
        TreeKnowledge(
            ReviewKnowledgeSide.model_validate(side["wire"]),
            None if side["database"] is None else Path(side["database"]),
        )
        for side in document["sides"]
    )
    return ReviewTrees(
        record,
        None,
        Path(record.memory_candidate.repository),
        sides[0],
        sides[1],
        live=document["live"],
    )


def code_changes(
    trees: ReviewTrees, classification: WorklistClassification | None = None
) -> ReviewCodeChanges:
    """Use this run's classifications when supplied; no roster, subject or excerpt is loaded."""
    if classification is not None and not worklist_bound(classification.pairing, trees):
        classification = None
    code = (
        classification.classifier.code
        if classification is not None and classification.classifier is not None
        else None
    )
    try:
        with open_tree_lane(trees, code=code) as lane:
            missing = [
                side.detail or f"the {side.name} memory side could not be read"
                for side in (lane.before, lane.after)
                if side.index is None
            ]
            missing.extend(
                f"the {side.name} entry sidecar {path} cannot be read: {reason}"
                for side in (lane.before, lane.after)
                for path, reason in side.problems.items()
                if path.startswith("onboarding/")
            )
            if missing:
                return ReviewCodeChanges(
                    state="unavailable", detail=bounded_text("; ".join(missing))
                )
            observed = lane.observe()
            if not observed.available:
                return ReviewCodeChanges(
                    state="unavailable",
                    detail=bounded_text(observed.detail or "the code comparison cannot be read"),
                )
            return ReviewCodeChanges(
                state="facts", invariants=implementation_facts(lane, classification)
            )
    except (LaneReadError, apsw.Error) as error:
        return ReviewCodeChanges(state="unavailable", detail=bounded_text(str(error)))
