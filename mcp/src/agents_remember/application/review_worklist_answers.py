"""Gate judgments for a worklist bound to an exact comparison, never a reviewer predicate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_gate.gate import GateTrees, item_verdicts
from agents_remember.application.knowledge_worklist import read_leaf_worklist
from agents_remember.application.knowledge_worklist.classification_context import (
    WorklistClassification,
)
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.models.knowledge.item_verdict import ItemVerdict
from agents_remember.worktrees.worktree_contract import WorktreeContract


def stored_unexplained_open(contract: WorktreeContract | None, trees: ReviewTrees) -> int | None:
    """Only matching, readable stored work has a measured open count; attribution is separate."""
    if contract is None:
        return None
    try:
        document = read_leaf_worklist(contract.contract_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        # This auxiliary count is optional; the historical worklist reader stays strict.
        return None
    if (
        not document
        or document.get("state") != "complete"
        or not worklist_bound(document.get("pairing") or {}, trees)
    ):
        return None
    items = [
        item
        for item in document.get("items", ())
        if item.get("kind") in ("unexplained_hunk", "unexplained_file")
    ]
    states = worklist_answers({**document, "items": items}, trees)
    if any(states[str(item["id"])].state == "undecided" for item in items):
        return None
    return sum(states[str(item["id"])].state == "open" for item in items)


def worklist_bound(pairing: Mapping[str, Any], trees: ReviewTrees) -> bool:
    record = trees.record
    try:
        return (
            pairing["base"]["tree"] == record.code_base.tree
            and pairing["candidate"]["tree"] == record.code_candidate.tree
            and pairing["memoryBase"]["commit"] == record.memory_base.commit
            and pairing["memoryBase"]["tree"] == record.memory_base.tree
            and pairing["memoryCandidate"]["tree"] == record.memory_candidate.tree
        )
    except (KeyError, TypeError):
        return False


def worklist_answers(
    document: Mapping[str, Any] | None,
    trees: ReviewTrees,
    classification: WorklistClassification | None = None,
) -> dict[str, ItemVerdict]:
    if not document or not worklist_bound(document.get("pairing") or {}, trees):
        return {}
    items = document.get("items") or ()
    if document.get("state") != "complete":
        reason = (
            "; ".join(str(one.get("detail")) for one in document.get("incomplete", ()))
            or "the worklist computation is incomplete"
        )
        return {str(item["id"]): ItemVerdict(state="undecided", reason=reason) for item in items}
    record = trees.record
    candidate = GateTrees(
        Path(record.code_candidate.repository),
        record.code_candidate.tree,
        Path(record.memory_candidate.repository),
        record.memory_candidate.tree,
        (),
        record.code_base.commit or record.code_base.tree,
    )
    if (
        classification is not None
        and classification.classifier is not None
        and classification.candidate is not None
        and worklist_bound(classification.pairing, trees)
    ):
        return item_verdicts(
            items,
            candidate,
            record.leaf_id,
            knowledge=classification.candidate,
            code=classification.classifier.code,
        )
    return item_verdicts(items, candidate, record.leaf_id)
