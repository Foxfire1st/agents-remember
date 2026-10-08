"""Attribution conclusions use the actual tree indexes and retain each unread side's limit.

All cases resolve the existing converted World enclosure. Empty knowledge is text with a layout
marker, and unavailable history comes from an actual recorded Git tree that can no longer be read.
Canonical origin and candidate-receipt operations are retired.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import read_knowledge_review
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.worktree_contract import load_contract
from test_knowledge_review_source_endpoints import _public_resolution
from test_review_git_trees import CODE_FILE, INVARIANT, World, build_world, commit, git

pytestmark = pytest.mark.evidence_unit

UNMAPPED_PATH = "pkg/unregistered.py"


def _world(tmp_path: Path) -> World:
    world = build_world(tmp_path)
    world.edit()
    (world.code_worktree / UNMAPPED_PATH).write_text("# no registered realization\n")
    captured = capture_future_code_candidate(load_contract(world.contract())).codeCandidateTree
    trees = CodeTrees.open(world.code, captured, captured)
    blob = trees.base()[CODE_FILE]
    sidecar_path = world.memory_worktree / f"onboarding/{CODE_FILE}.json"
    sidecar = json.loads(sidecar_path.read_text())
    for entry in sidecar["realizes"]:
        anchor = trees.resolve(CODE_FILE, entry["anchor"]["locator"], blob, blob)
        assert anchor is not None
        entry["anchor"] = {
            "locator": entry["anchor"]["locator"],
            "blob": blob,
            "content": anchor.content,
        }
    sidecar_path.write_text(canonical_text(sidecar))
    return world


def _subject(world: World):
    return world.review(
        selector=InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))
    )


def _unknown(payload, side: str) -> None:
    partition = payload.source.attribution
    assert partition is not None and partition.state == "measured" and not partition.complete
    assert [entry.state for entry in partition.sides] == (
        ["unavailable", "inspected"] if side == "before" else ["inspected", "unavailable"]
    )
    by_path = {entry.path: entry for entry in partition.paths}
    assert by_path[CODE_FILE].bucket == "attributed"
    assert by_path[CODE_FILE].mapped_sides == (("after",) if side == "before" else ("before",))
    assert "additional mappings may be unknown" in by_path[CODE_FILE].detail
    assert (
        by_path[UNMAPPED_PATH].bucket == "unknown_attribution"
        and by_path[UNMAPPED_PATH].link is None
    )
    assert payload.source.attributed_changed_paths == partition.attributed_paths
    assert (
        payload.source.unattributed_changed_paths == ()
        and partition.confirmed_unregistered_total == 0
    )
    assert UNMAPPED_PATH in payload.source.unknown_attribution_changed_paths
    counts = {entry.name: entry.value for entry in payload.source.remaining}
    assert counts["unattributed_changed_paths"] == 0
    assert counts["unknown_attribution_changed_paths"] == partition.unknown_attribution_total
    assert partition.unknown_attribution_total
    assert "limitation:unknown_attribution_changed_paths" in payload.limitations
    assert (
        f"omitted:attribution_not_determined:{partition.unknown_attribution_total}"
        in payload.limitations
    )
    missing = next(entry for entry in partition.sides if entry.side == side)
    assert missing.registered_mapping_count is None and missing.detail
    row = next(
        entry
        for entry in payload.source.remaining
        if entry.name == "unknown_attribution_changed_paths"
    )
    assert row.value is not None and row.reason is None


def test_one_unreadable_knowledge_half_leaves_the_readable_side_attributed_and_the_rest_unknown(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    resolved = _public_resolution(world)
    assert resolved.trees is not None
    tree = resolved.trees.record.memory_candidate.tree
    ref = resolved.trees.record.memory_candidate.ref
    assert ref is not None
    world.contract(live=False)
    git(world.memory, "update-ref", "-d", ref)
    git(world.memory, "worktree", "remove", "--force", str(world.memory_worktree))
    git(world.memory, "reflog", "expire", "--expire=now", "--all")
    git(world.memory, "gc", "-q", "--prune=now")
    result = read_knowledge_review(world.config, world.review())
    assert result.state == "review" and result.payload is not None, result.refusal
    payload = result.payload
    assert payload.source.inventory.state == "measured"
    _unknown(payload, "after")
    assert "after" in (payload.knowledge.selection_detail or "")
    assert tree in (payload.knowledge.selection_detail or "")
    assert "history:intent:after:unavailable-history" in payload.limitations
    named = read_knowledge_review(world.config, _subject(world))
    assert named.state == "refused" and named.refusal is not None
    assert named.refusal.code == "candidate_dataset_absent" and tree in named.refusal.detail


def test_an_empty_converted_base_counts_as_completely_inspected_for_absence(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    # A real empty converted base is inspected. No canonical origin record invents its emptiness.
    git(
        world.memory,
        "rm",
        "-q",
        "-r",
        "knowledge/invariants",
        "knowledge/families",
        f"onboarding/{CODE_FILE}.json",
    )
    world.memory_base = commit(world.memory, {}, trailer=world.code_base)
    result = read_knowledge_review(world.config, world.review())
    assert result.state == "review" and result.payload is not None, result.refusal
    partition = result.payload.source.attribution
    assert partition is not None and partition.complete
    assert [entry.state for entry in partition.sides] == ["inspected", "inspected"]
    assert partition.sides[0].registered_mapping_count == 0
    by_path = {entry.path: entry for entry in partition.paths}
    assert by_path[CODE_FILE].bucket == "attributed"
    assert by_path[UNMAPPED_PATH].bucket == "confirmed_unregistered"
    assert result.payload.source.unknown_attribution_changed_paths == ()
    counts = {entry.name: entry.value for entry in result.payload.source.remaining}
    assert counts["unattributed_changed_paths"] == len(
        result.payload.source.unattributed_changed_paths
    )
    assert partition.unknown_attribution_total == 0


def test_the_same_pair_read_completely_confirms_absence_where_the_lost_half_could_not(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    result = read_knowledge_review(world.config, world.review())
    assert result.state == "review" and result.payload is not None, result.refusal
    partition = result.payload.source.attribution
    assert partition is not None and partition.complete
    assert [entry.state for entry in partition.sides] == ["inspected", "inspected"]
    assert {entry.path: entry.bucket for entry in partition.paths}[
        UNMAPPED_PATH
    ] == "confirmed_unregistered"
    assert (
        result.payload.source.unknown_attribution_changed_paths == ()
        and partition.unknown_attribution_total == 0
    )
    assert partition.changed_total == len(result.payload.source.inventory.entries)
    buckets = (
        partition.attributed_total,
        partition.confirmed_unregistered_total,
        partition.unknown_attribution_total,
    )
    assert None not in buckets
    assert partition.changed_total == sum(bucket for bucket in buckets if bucket is not None)


def test_a_damaged_knowledge_half_cannot_support_a_negative_attribution_conclusion(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    damaged = world.memory_worktree / "knowledge/invariants/INV-BBBBBB-damaged.json"
    damaged.write_text("not a readable knowledge document\n")
    result = read_knowledge_review(world.config, world.review())
    assert result.state == "review" and result.payload is not None, result.refusal
    payload = result.payload
    assert payload.source.inventory.state == "measured"
    assert "knowledge-index:after:partial" in payload.limitations
    partition = payload.source.attribution
    assert partition is not None and partition.state == "measured" and not partition.complete
    by_path = {entry.path: entry for entry in partition.paths}
    assert by_path[CODE_FILE].bucket == "attributed"
    assert by_path[CODE_FILE].mapped_sides == ("before", "after")
    assert "additional mappings may be unknown" in by_path[CODE_FILE].detail
    assert by_path[UNMAPPED_PATH].bucket == "unknown_attribution"
    assert payload.source.unattributed_changed_paths == ()
    assert partition.confirmed_unregistered_total == 0 and partition.unknown_attribution_total
    assert "limitation:unknown_attribution_changed_paths" in payload.limitations
    named = read_knowledge_review(world.config, _subject(world))
    assert named.state == "review" and named.payload is not None, named.refusal
    assert "knowledge-index:after:partial" in named.payload.limitations
    selected = named.payload.source.attribution
    assert selected is not None and not selected.complete
    assert {entry.path: entry.bucket for entry in selected.paths}[
        UNMAPPED_PATH
    ] == "unknown_attribution"
    assert selected.confirmed_unregistered_total == 0
    assert "limitation:unknown_attribution_changed_paths" in named.payload.limitations
