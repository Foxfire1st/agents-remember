"""The facts computed alongside the leaf worklist in its isolated run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.application.knowledge_worklist.classification_context import (
    WorklistClassification,
)
from agents_remember.application.review_code_changes import code_changes
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.application.review_worklist_answers import worklist_answers


def leaf_facts(
    document: Mapping[str, Any] | None,
    trees: ReviewTrees,
    classification: WorklistClassification | None = None,
) -> dict[str, Any]:
    return {
        "code_changes": code_changes(trees, classification).model_dump(mode="json"),
        "item_states": {
            key: value.model_dump(mode="json")
            for key, value in worklist_answers(document, trees, classification).items()
        },
    }
