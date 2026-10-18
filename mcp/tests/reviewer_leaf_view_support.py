"""Parity adapters for the isolated leaf-wide view, preserving the worklist's own bytes."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_worklist.leaf import (
    CandidateTrees,
    CapturedBase,
    leaf_worklist,
)
from agents_remember.application.review_leaf_facts import leaf_facts
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.models.knowledge.review_trees import ReviewTreesResult
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.worktree_contract import WorktreeContract


def direct_leaf_view(
    contract: WorktreeContract,
    *,
    candidate: CandidateTrees,
    base: CapturedBase,
    trees: ReviewTrees,
    processes: ReviewerWorklistProcesses | None = None,
) -> dict[str, Any]:
    document = leaf_worklist(contract, persist=False, candidate=candidate, base=base)
    return {**(document or {}), "_review": leaf_facts(document, trees)}


def observe_child(replies: list[dict[str, Any]], reply: Callable[..., dict[str, Any]]):
    def observed(data: bytes, request: dict[str, Any]) -> dict[str, Any]:
        answer = reply(data, request)
        replies.append({**answer, "bytes": len(data), "request_payload": request["payload"]})
        return answer

    return observed


def classified_leaf_view(
    config: McpRuntimeConfig, query: ReviewTreesQuery, observations: Path
) -> ReviewTreesResult:
    """Measure actual child classification operations, separately from cached blob reads."""
    from test_reviewer_worklist_reads import _child_script  # noqa: PLC0415

    script = """
import json, sys
from collections import Counter
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
from agents_remember.application import review_leaf_facts as facts
from agents_remember.application import review_lane_classification as lane
from agents_remember.application.knowledge_worklist.classify import Classifier
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.linkage import _Linkage
from agents_remember.application.review_lane_classification import TreeLane
counts = {name: Counter() for name in ('entries', 'gate_paths', 'review_paths', 'code_opens', 'intersections', 'knowledge_sides')}
entry, gate_path, review_path = Classifier._classify, _Linkage._change, TreeLane.classify
code_open = CodeTrees.open
knowledge_side = KnowledgeSide.from_snapshot
def counted_knowledge(label, snapshot):
    counts['knowledge_sides'][label] += 1
    return knowledge_side(label, snapshot)
KnowledgeSide.from_snapshot = staticmethod(counted_knowledge)
intersects = lane._intersects
def counted_intersection(hunk, side, span):
    counts['intersections'][side] += 1
    return intersects(hunk, side, span)
lane._intersects = counted_intersection
def counted_open(*args, **kwargs):
    counts['code_opens']['open'] += 1
    return code_open(*args, **kwargs)
def counted_entry(self, indexed):
    counts['entries'][indexed.entry.id] += 1
    return entry(self, indexed)
def counted_gate_path(self, change, renamed_to):
    counts['gate_paths'][change.path] += 1
    return gate_path(self, change, renamed_to)
def counted_review_path(self, change):
    counts['review_paths'][change.path] += 1
    return review_path(self, change)
Classifier._classify = counted_entry
_Linkage._change = counted_gate_path
TreeLane.classify = counted_review_path
CodeTrees.open = counted_open
before_facts = {}
new_in_facts = {}
owners = []
reused_entries, reused_paths = Counter(), Counter()
in_facts = False
classify, project = Classifier.classify, TreeLane.from_worklist
def reused_entry(self, entry_id):
    if in_facts:
        assert self is owners[-1].classifier
        reused_entries[entry_id] += 1
    return classify(self, entry_id)
def reused_path(self, classified):
    assert classified is owners[-1].paths[classified.change.path]
    assert self.code is owners[-1].classifier.code
    reused_paths[classified.change.path] += 1
    return project(self, classified)
Classifier.classify = reused_entry
TreeLane.from_worklist = reused_path
compute = facts.code_changes
def measured_facts(trees, classification=None):
    global in_facts
    owners.append(classification)
    before_facts.update({name: dict(values) for name, values in counts.items()})
    in_facts = True
    try:
        result = compute(trees, classification)
    finally:
        in_facts = False
    new_in_facts.update({
        name: dict(values - Counter(before_facts[name])) for name, values in counts.items()
    })
    return result
facts.code_changes = measured_facts
gate_reconstruction = {}
answers = facts.worklist_answers
def measured_answers(*args, **kwargs):
    before = {name: values.copy() for name, values in counts.items()}
    result = answers(*args, **kwargs)
    gate_reconstruction.update({
        name: dict(values - before[name]) for name, values in counts.items()
    })
    return result
facts.worklist_answers = measured_answers
answer = child._run(json.load(sys.stdin))
Path(OBSERVATIONS).write_text(json.dumps({
    'before_facts': before_facts, 'new_in_facts': new_in_facts, 'total': counts,
    'reused_entries': reused_entries, 'reused_paths': reused_paths,
    'gate_reconstruction': gate_reconstruction,
    'reclaimed': all(owner is not None and owner.classifier is None
                     and getattr(owner, 'candidate', None) is None
                     and not owner.paths and not owner.pairing for owner in owners),
    'error': answer['error'],
}))
json.dump(answer, sys.stdout)
""".replace("OBSERVATIONS", repr(str(observations)))
    with _child_script(script):
        result = read_review_trees(config, query)
    measured = json.loads(observations.read_text())
    assert measured["error"] is None, measured
    assert measured["before_facts"]["entries"] and measured["before_facts"]["gate_paths"]
    assert all(count == 1 for count in measured["total"]["entries"].values()), measured
    assert not any(measured["new_in_facts"].values()), measured
    assert not any(measured["gate_reconstruction"].values()), measured
    assert measured["total"]["knowledge_sides"] == {"K_B": 1, "K_C": 1}, measured
    assert measured["reused_entries"] and measured["reused_paths"], measured
    assert all(count == 1 for count in measured["total"]["gate_paths"].values()), measured
    assert measured["reclaimed"], measured
    return result
