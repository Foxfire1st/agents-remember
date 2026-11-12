"""Proof entries for one selected invariant or every after-tree member of a family."""

from __future__ import annotations

from agents_remember.application.knowledge_currentness.observe import (
    CodeTree,
    observe_entry,
    open_code_tree,
)
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.models.knowledge.review_judgment import ReviewJudgmentProof


def judgment_proofs(
    index: KnowledgeIndex, subject: str, resolved: ReviewCandidateResolution
) -> tuple[ReviewJudgmentProof, ...]:
    """Observe each proof at the bound after code tree, independent of history readability."""

    invariants = index.family(subject).value.members if subject.startswith("FAM-") else (subject,)
    root, tree = resolved.candidate_code_root, resolved.candidate_code_tree_id
    code = open_code_tree(None if root is None or tree is None else CodeTree(root, tree))
    displayed: list[ReviewJudgmentProof] = []
    for entry in index.proofs_of(invariants).value:
        anchor = entry.document.get("anchor") or {}
        locator = anchor.get("locator") or {}
        observed = observe_entry(entry, code)
        displayed.append(
            ReviewJudgmentProof(
                id=entry.id,
                invariant=entry.invariant,
                path=entry.path,
                symbol=locator.get("name") if locator.get("kind") == "symbol" else None,
                facet=entry.document["facet"],
                state=observed.state,
                detail=observed.reason or "The proof entry is current at the after code tree.",
            )
        )
    return tuple(displayed)
