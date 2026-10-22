"""A landed leaf's review opens the pair it delivered, not its last numbered record (MIK-R195).

The composed case is the one that slipped through: a leaf whose last record was captured before
curation (equal memory operands, so nothing changed) and whose worktree cleanup has removed. Its
default review must compare the code it landed and the memory it delivered, while that earlier
record stays sealed, numbered and selectable as history.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    list_comparison_review_entries,
    read_knowledge_review,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_committed_leaf import resolve_review_comparison
from agents_remember.application.review_comparison_publication import publish_comparison_record
from agents_remember.application.review_delivered_comparison import (
    _record,
    delivered_pair,
    recorded_latest,
)
from agents_remember.application.review_intent_summary import intent_summary_of
from agents_remember.application.review_tree_comparison import (
    TreeSideUnreadable,
    comparison_directory,
    comparison_records,
)
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from agents_remember_test_support.testing.review_git_trees import (
    INVARIANT,
    INVARIANT_PATH,
    LEAF,
    MASTER,
    REPO,
    World,
    _write,
    build_world,
    commit,
    git,
    invariant,
)

pytestmark = pytest.mark.evidence_unit


def _contract(world: World) -> WorktreeContract:
    return load_contract(world.task_root / "enclosures" / LEAF.lower() / "series-contract.md")


def _code_tree(world: World, commit_id: str) -> str:
    return git(world.code, "rev-parse", f"{commit_id}^{{tree}}")


def _memory_tree(world: World, commit_id: str) -> str:
    return git(world.memory, "rev-parse", f"{commit_id}^{{tree}}")


def _record_bytes(world: World, number: int) -> bytes:
    return (comparison_directory(world.task_root, LEAF) / f"{number}.json").read_bytes()


def _landed_world(tmp_path: Path) -> tuple[World, str, str]:
    """A curated, landed leaf whose last record predates curation and whose worktree is gone."""

    world = build_world(tmp_path)
    # The record the bug defaults to: captured before curation, so both memory sides are the base.
    stale = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(stale, ReviewCandidateResolution), stale
    assert stale.trees is not None and stale.trees.record.number == 1
    assert stale.trees.record.memory_base.tree == stale.trees.record.memory_candidate.tree
    world.edit()
    code_commit = commit(world.code_worktree, {})
    memory_content = commit(world.memory_worktree, {})
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={
            "status": "completed",
            "strategy": "ff-only",
            "code_commit": code_commit,
            "memory_content_commit": memory_content,
        },
        memory_content_commit=memory_content,
    )
    shutil.rmtree(world.root / "group")
    return world, code_commit, memory_content


def test_a_landed_leaf_opens_the_pair_it_delivered_and_keeps_its_earlier_record(
    tmp_path: Path,
) -> None:
    world, code_commit, memory_content = _landed_world(tmp_path)
    landed = _code_tree(world, code_commit)
    delivered_memory = _memory_tree(world, memory_content)
    stale_bytes = _record_bytes(world, 1)

    pair = delivered_pair(_contract(world))
    assert pair is not None and not isinstance(pair, ReviewRefusal), pair
    assert pair.code_candidate.commit == code_commit
    assert pair.memory_candidate.commit == memory_content

    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.trees is not None
    record = resolved.trees.record
    assert record.number == 2
    assert record.code_base.commit == world.code_base
    assert record.code_candidate.commit == code_commit
    assert record.code_candidate.tree == landed
    assert record.memory_base.commit == world.memory_base
    assert record.memory_candidate.commit == memory_content
    assert record.memory_candidate.tree == delivered_memory
    assert resolved.baseline_code_tree_id == world.code_base
    assert resolved.candidate_code_tree_id == landed

    # The earlier record keeps its number, its four trees and its bytes, and is still readable.
    assert _record_bytes(world, 1) == stale_bytes
    stale = resolve_review_comparison(world.config, REPO, MASTER, LEAF, 1)
    assert isinstance(stale, ReviewCandidateResolution)
    assert stale.trees is not None
    assert stale.trees.record.memory_base.tree == stale.trees.record.memory_candidate.tree
    # Its zero count is correct for its own pair; the delivered pair's count is not zero.
    stale_counts = intent_summary_of(stale, MASTER).counts
    assert stale_counts is not None and stale_counts.invariants.after_only == 0
    delivered_counts = intent_summary_of(resolved, MASTER).counts
    assert delivered_counts is not None and delivered_counts.invariants.after_only == 1
    # Opening the default again reuses the one delivered record: no duplicate numbering.
    again = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(again, ReviewCandidateResolution) and again.trees is not None
    assert again.trees.record.number == 2
    assert [each.number for each in comparison_records(world.task_root, LEAF)] == [1, 2]
    # The catalogue and the statement read the same comparison the default shows.
    entries = list_comparison_review_entries(world.config, REPO, MASTER, LEAF, 2)
    assert entries.comparison == 2 and entries.state == "entries"
    assert entries.invariant_total > 0
    subject = read_knowledge_review(
        world.config,
        world.review(selector=InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))),
    )
    assert subject.payload is not None, subject.refusal
    statement = subject.payload.knowledge.after_statement
    assert statement.state == "present" and "exactly as given" in (statement.text or "")
    # Both sides are the delivered pair's own operands: the recorded base text is the before side.
    before = subject.payload.knowledge.before_statement
    assert before.state == "present" and "Values land unchanged." in (before.text or "")


def test_a_replayed_landing_is_the_delivered_code_candidate(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.edit()
    closeout = commit(world.code_worktree, {})
    memory_content = commit(world.memory_worktree, {})
    # The landing replays onto the source line: a different commit, the reviewed change intact.
    _write(world.code, {"pkg/landing-marker.txt": "replayed\n"})
    replayed = commit(world.code, {})
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": closeout},
        integration={
            "status": "completed",
            "strategy": "replay",
            "code_commit": replayed,
            "memory_content_commit": memory_content,
        },
        memory_content_commit=memory_content,
    )
    shutil.rmtree(world.root / "group")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.trees is not None
    assert resolved.trees.record.code_candidate.commit == replayed
    assert resolved.trees.record.code_candidate.tree == _code_tree(world, replayed)
    assert resolved.trees.record.code_candidate.commit != closeout


def test_an_exact_delivered_record_older_than_the_last_record_is_reused(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.edit()
    code_commit = commit(world.code_worktree, {})
    delivered_memory = commit(world.memory_worktree, {})
    world.contract(closeout={"code_commit": code_commit})
    # The delivered pair is recorded while the leaf is still live: record 1.
    live = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(live, ReviewCandidateResolution) and live.trees is not None
    assert live.trees.record.number == 1
    assert live.trees.record.memory_candidate.tree == _memory_tree(world, delivered_memory)
    # A later attempt is recorded and then discarded, so the last record is not the delivered pair.
    _write(world.memory_worktree, {INVARIANT_PATH: invariant(3, "Values land untouched.")})
    commit(world.memory_worktree, {})
    later = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(later, ReviewCandidateResolution) and later.trees is not None
    assert later.trees.record.number == 2
    delivered_bytes = _record_bytes(world, 1)
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={
            "status": "completed",
            "strategy": "ff-only",
            "code_commit": code_commit,
            "memory_content_commit": delivered_memory,
        },
        memory_content_commit=delivered_memory,
    )
    shutil.rmtree(world.root / "group")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.trees is not None
    assert resolved.trees.record.number == 1
    assert resolved.trees.record.memory_candidate.tree == _memory_tree(world, delivered_memory)
    assert recorded_latest(_contract(world)) == 2
    assert _record_bytes(world, 1) == delivered_bytes


def test_a_matching_last_record_is_the_delivered_default_and_keeps_its_number(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    world.edit()
    code_commit = commit(world.code_worktree, {})
    delivered_memory = commit(world.memory_worktree, {})
    world.contract(closeout={"code_commit": code_commit})
    recorded = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(recorded, ReviewCandidateResolution) and recorded.trees is not None
    assert recorded.trees.record.number == 1
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={
            "status": "completed",
            "strategy": "ff-only",
            "code_commit": code_commit,
            "memory_content_commit": delivered_memory,
        },
        memory_content_commit=delivered_memory,
    )
    shutil.rmtree(world.root / "group")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.trees is not None
    # The last record already is the delivered pair: it is reused, and no second number is minted.
    assert resolved.trees.record.number == 1
    assert resolved.trees.record.memory_candidate.tree == _memory_tree(world, delivered_memory)
    assert comparison_records(world.task_root, LEAF)[-1].number == 1
    assert recorded_latest(_contract(world)) == 1


def test_a_delivery_git_cannot_produce_is_named_and_never_replaced(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.edit()
    recorded = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(recorded, ReviewCandidateResolution) and recorded.trees is not None
    code_commit = commit(world.code_worktree, {})
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={"status": "completed", "code_commit": code_commit},
        memory_content_commit="0" * 40,
    )
    shutil.rmtree(world.root / "group")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewRefusal), resolved
    assert resolved.code == "candidate_unresolved"
    assert "memory candidate" in resolved.detail and "0" * 40 in resolved.detail
    assert resolved.offending_input == "memory candidate"
    # History stays reachable: the earlier record is still the leaf's own recorded comparison.
    history = resolve_review_comparison(world.config, REPO, MASTER, LEAF, 1)
    assert isinstance(history, ReviewCandidateResolution)
    assert history.trees is not None and history.trees.record.number == 1


def test_a_leaf_that_records_no_delivery_keeps_its_recorded_default(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.edit()
    recorded = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(recorded, ReviewCandidateResolution) and recorded.trees is not None
    world.contract(code_commit=commit(world.code_worktree, {}))
    shutil.rmtree(world.root / "group")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.trees is not None
    assert resolved.trees.record.number == recorded.trees.record.number
    assert delivered_pair(_contract(world)) is None


def _trees(world: World, number: int | None):
    return read_review_trees(
        world.config,
        ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF, number=number),
    )


def test_the_line_says_which_pair_was_delivered(tmp_path: Path) -> None:
    world, code_commit, memory_content = _landed_world(tmp_path)
    delivered = _trees(world, None)
    assert delivered.state == "trees" and delivered.delivered is not None
    assert delivered.delivered.state == "delivered"
    assert delivered.delivered.comparison == 2
    assert delivered.delivered.code_candidate_tree == _code_tree(world, code_commit)
    assert delivered.delivered.memory_candidate_tree == _memory_tree(world, memory_content)
    assert delivered.latest == 2
    # The panel's other reads sit on that same comparison: both knowledge sides and both code sides.
    sides = {side.side: side.tree for side in delivered.knowledge_sides}
    assert sides["before"] == _memory_tree(world, world.memory_base)
    assert sides["after"] == _memory_tree(world, memory_content)
    codes = {side.side: side.tree for side in delivered.code_sides}
    assert codes["base"] == _code_tree(world, world.code_base)
    assert codes["candidate"] == _code_tree(world, code_commit)
    stale = _trees(world, 1)
    assert stale.state == "trees" and stale.delivered is not None
    assert stale.delivered.state == "differs"
    assert stale.delivered.shown == 1
    assert stale.delivered.comparison == 2
    assert "Comparison #1" in stale.delivered.statement
    assert "exactly as given" not in json.dumps(stale.delivered.statement)


# -- the delivery is the closeout's, never the landing's ------------------------------------------


def test_a_closeout_without_its_memory_cell_refuses_and_publishes_nothing(tmp_path: Path) -> None:
    """F01: the landing record's memory cell is not a second answer to what the leaf delivered."""

    world, code_commit, memory_content = _landed_world(tmp_path)
    recorded = comparison_records(world.task_root, LEAF)
    # The enclosure still names the delivered memory on its landing record: substitution is possible.
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={
            "status": "completed",
            "strategy": "ff-only",
            "code_commit": code_commit,
            "memory_content_commit": memory_content,
        },
    )
    contract = _contract(world)
    assert contract.integrated_memory_content_commit == memory_content
    assert contract.memory_content_commit == ""
    pair = delivered_pair(contract)
    assert isinstance(pair, ReviewRefusal)
    assert pair.offending_input == "memory candidate"
    assert "memory_content_commit" in pair.detail
    assert "no commit" in pair.detail
    # The production default refuses by the same name, and no comparison is published for it.
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewRefusal)
    assert "memory_content_commit" in resolved.detail
    assert comparison_records(world.task_root, LEAF) == recorded


def test_a_recorded_tree_id_is_not_a_delivered_commit(tmp_path: Path) -> None:
    """F02: a commit cell names a commit; a tree, a branch name or a short id is refused by name."""

    world, code_commit, memory_content = _landed_world(tmp_path)
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={"status": "completed", "code_commit": code_commit},
        memory_content_commit=memory_content,
    )
    contract = _contract(world)
    tree = _memory_tree(world, memory_content)
    coerced = delivered_pair(replace(contract, memory_content_commit=tree))
    assert isinstance(coerced, ReviewRefusal)
    assert tree in coerced.detail and "tree rather than a commit" in coerced.detail
    for token in ("HEAD", memory_content[:8], "main"):
        answer = delivered_pair(replace(contract, memory_content_commit=token))
        assert isinstance(answer, ReviewRefusal), token
        assert "not a full commit id" in answer.detail, token
    # The recorded commit still resolves, with the tree Git derives from it.
    pair = delivered_pair(contract)
    assert pair is not None and not isinstance(pair, ReviewRefusal)
    assert pair.memory_candidate.commit == memory_content
    assert pair.memory_candidate.tree == tree


def _unconverted_memory_commit(world: World, place: Path) -> str:
    """A memory commit that holds no converted knowledge, on a branch of the same repository."""

    git(world.memory, "worktree", "add", "-q", "-b", "legacy-unconverted", str(place))
    try:
        for path in place.iterdir():
            if path.name == ".git":
                continue
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        return commit(place, {"README.md": "unconverted retained memory\n"})
    finally:
        git(world.memory, "worktree", "remove", "--force", str(place))


def test_an_unconverted_delivery_opens_the_legacy_unavailable_review(tmp_path: Path) -> None:
    """F03: a delivery with no converted knowledge is source-only, never an available zero pair."""

    world = build_world(tmp_path)
    legacy = _unconverted_memory_commit(world, tmp_path / "legacy-memory")
    world.memory_base = legacy
    code_commit = commit(world.code_worktree, {})
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={
            "status": "completed",
            "strategy": "ff-only",
            "code_commit": code_commit,
            "memory_content_commit": legacy,
        },
        memory_content_commit=legacy,
    )
    shutil.rmtree(world.root / "group")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    # No tree comparison at all: the knowledge sides are named, not indexed as an empty baseline.
    assert resolved.trees is None
    states = {side: (state, detail) for side, state, detail in resolved.knowledge_unavailable}
    assert states["before"][0] == "legacy-unavailable"
    assert states["after"][0] == "legacy-unavailable"
    assert "unconverted" in states["before"][1]
    # The delivered source identities stay readable, and no comparison record was minted for it.
    assert resolved.baseline_code_tree_id == world.code_base
    assert resolved.candidate_code_tree_id == _code_tree(world, code_commit)
    assert comparison_records(world.task_root, LEAF) == ()
    # The tree view reports the same thing: no available side, and no delivery line at all.
    trees = _trees(world, None)
    assert trees.state in {"not-converted", "refused"}
    assert trees.delivered is None


def test_a_required_conversion_that_fails_is_refused_not_read_as_an_empty_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F03: an unconvertible base is named, never indexed as a raw tree with nothing in it."""

    world, code_commit, memory_content = _landed_world(tmp_path)
    legacy = _unconverted_memory_commit(world, tmp_path / "legacy-memory")
    world.memory_base = legacy
    world.contract(
        live=False,
        closeout={"status": "completed", "code_commit": code_commit},
        integration={
            "status": "completed",
            "strategy": "ff-only",
            "code_commit": code_commit,
            "memory_content_commit": memory_content,
        },
        memory_content_commit=memory_content,
    )

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise TreeSideUnreadable("memory base", "the recorded base cannot be converted")

    monkeypatch.setattr(
        "agents_remember.application.review_delivered_comparison.converted_base_side", unreadable
    )
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewRefusal), resolved
    assert resolved.code == "candidate_dataset_absent"
    assert resolved.offending_input == "memory base"
    assert "cannot be converted" in resolved.detail


def test_a_numbered_leaf_wide_read_records_no_comparison(tmp_path: Path) -> None:
    """F04: asking for a recorded number -- or one never recorded -- publishes nothing."""

    world, _code_commit, _memory_content = _landed_world(tmp_path)
    before = comparison_records(world.task_root, LEAF)
    assert [record.number for record in before] == [1]
    known = _trees(world, 1)
    assert known.state == "trees"
    assert comparison_records(world.task_root, LEAF) == before
    unknown = _trees(world, 99)
    assert unknown.state == "refused"
    assert unknown.refusal is not None and unknown.refusal.code == "candidate_unresolved"
    assert comparison_records(world.task_root, LEAF) == before
    # The default read is the one that publishes the delivered comparison, once.
    default = _trees(world, None)
    assert default.state == "trees"
    assert [record.number for record in comparison_records(world.task_root, LEAF)] == [1, 2]


# -- publication is create-only -------------------------------------------------------------------


def test_publishing_a_record_never_replaces_one_that_exists(tmp_path: Path) -> None:
    """F05: the first writer's bytes are the record; a later writer's are never published."""

    path = tmp_path / "comparisons" / "2.json"
    assert publish_comparison_record(path, b'{"recorded_at":"first"}') is True
    first = path.read_bytes()
    assert publish_comparison_record(path, b'{"recorded_at":"second"}') is False
    assert path.read_bytes() == first


def test_a_record_that_loses_its_number_to_another_pair_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F05: the loser of a real race answers a named refusal, never a second write of the number."""

    world, _code_commit, _memory_content = _landed_world(tmp_path)
    pair = delivered_pair(_contract(world))
    assert pair is not None and not isinstance(pair, ReviewRefusal)
    monkeypatch.setattr(
        "agents_remember.application.review_tree_comparison.publish_comparison_record",
        lambda _path, _payload: False,
    )
    made = _record(_contract(world), pair.draft(_contract(world)))
    assert isinstance(made, ReviewRefusal)
    assert made.code == "reviewer_busy"
    assert made.offending_input == "2"
    assert [record.number for record in comparison_records(world.task_root, LEAF)] == [1]
