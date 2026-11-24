"""A leaf's comparisons use the memory merge recorded in its contract."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import test_knowledge_gate_routes
from agents_remember.application.knowledge_worklist.leaf import (
    leaf_onboarding_trace_sides,
    leaf_worklist,
    persist_worklist,
    worklist_path,
)
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    trace_context,
    worktree_code_blobs,
)
from agents_remember.application.knowledge_write_route import WriteSubmission, leaf_write_report
from agents_remember.application.knowledge_writer.writer import WriteReport
from agents_remember.application.review_tree_comparison import ReviewTrees, live_review_trees
from agents_remember.cli.__main__ import main as cli_main
from agents_remember.models.knowledge.review_refusal import ReviewRefusal
from agents_remember.worktrees.knowledge_gate import leaf_gate_refusal
from agents_remember.worktrees.modules.closeout_external import leaf_pair_refusal
from agents_remember.worktrees.modules.onboarding_trace import onboarding_trace_result
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_knowledge_closeout_gate import (
    B,
    Gated,
    build_gated,
    commit,
    git,
    init,
    invariant,
    write,
)

ports = test_knowledge_gate_routes.ports

INCOMING_SUBJECT = "INV-AAAAAA"
OTHER_SUBJECT = "INV-BBBBBB"
CARD = f"onboarding/{B}.md"
CARD_SUBJECT = f"onboarding:{B}"


@pytest.fixture
def merged_leaf(tmp_path: Path) -> Gated:
    world = build_gated(tmp_path)
    git(world.memory, "checkout", "-q", "main")
    incoming = commit(
        world.memory,
        {
            "knowledge/invariants/INV-AAAAAA-land.json": invariant(
                INCOMING_SUBJECT, "Lands as given.", 2
            )
        },
        trailer=world.code_base,
    )
    # The other memory line names a code commit newer than the leaf's B, so only the incoming
    # line's attributed commit qualifies when searching backward from B.
    git(world.code, "checkout", "-q", "-b", "other")
    other_code = commit(world.code, {"unrelated.py": "Z = 3\n"})
    git(world.code, "checkout", "-q", "leaf")
    git(world.memory, "checkout", "-q", "-b", "other", world.memory_base)
    other = commit(
        world.memory,
        {
            "knowledge/invariants/INV-BBBBBB-sibling.json": invariant(
                OTHER_SUBJECT, "The sibling returns two.", 2
            ),
            CARD: "# b\n\nReturns two for its callers.\n",
        },
        trailer=other_code,
    )
    git(world.memory, "checkout", "-q", "main")
    git(world.memory, "merge", "-q", "--no-ff", "-m", "Sync the memory lines", "other")
    merge, *parents = git(world.memory, "rev-list", "--parents", "-1", "HEAD").split()
    assert parents == [incoming, other]
    assert "Code-Commit:" not in git(world.memory, "show", "-s", "--format=%B", merge)
    git(world.memory, "checkout", "-q", "leaf")
    git(world.memory, "merge", "-q", "--ff-only", merge)
    return replace(world, memory_base=merge)


def _plan_row(
    contract: WorktreeContract, subject: str, disposition: str, **fields: Any
) -> WriteReport:
    report = leaf_write_report(
        contract,
        WriteSubmission(
            document={
                "history": [
                    {
                        "subject": subject,
                        "disposition": disposition,
                        "reason": "Reviewed.",
                        **fields,
                    }
                ]
            },
            handoff_path="rows.json",
            authorization_ref="fixture",
        ),
    )
    assert isinstance(report, WriteReport), report
    return report


def _persist(contract: WorktreeContract, items: list[dict[str, Any]]) -> None:
    path = worklist_path(contract)
    assert path is not None
    persist_worklist(path, {"items": items})


@pytest.fixture(params=["own_trailer", "code_base"])
def converted_leaf(tmp_path: Path, request: pytest.FixtureRequest) -> Gated:
    code, memory = tmp_path / "code", tmp_path / "memory"
    init(code)
    init(memory)
    guide = "docs/guide.md"
    guide_text = "# Guide\n\nDescribes sibling.\n"
    before = commit(code, {B: "X = 1\n", guide: guide_text})
    # A fallback to the wrong code side would leave the guide reference unresolved.
    if request.param == "own_trailer":
        git(code, "rm", "-q", guide)
    code_base = commit(code, {})
    if request.param == "code_base":
        git(code, "rm", "-q", guide)
        commit(code, {})
    git(code, "switch", "-qc", "leaf")
    trailer = before if request.param == "own_trailer" else None
    legacy = (
        f"# {B}\n\nIt holds one.\n\n"
        "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        f"| Describes the flow. | — | {guide} |\n"
    )
    memory_base = commit(memory, {CARD: legacy, "onboarding/overview.md": "# Demo\n"}, trailer)
    git(memory, "switch", "-qc", "leaf")
    world = Gated(tmp_path, code, memory, tmp_path / "task", code_base, memory_base)
    world.task_root.mkdir()
    world.task_document()
    assert (
        cli_main(
            [
                "knowledge-convert",
                str(memory),
                "--code",
                str(code),
                "--code-commit",
                trailer or code_base,
            ]
        )
        == 0
    )
    assert git(memory, "rev-parse", "HEAD") == memory_base
    write(code, {B: "X = 2\n", guide: guide_text})
    return world


def test_writer_counts_only_real_card_edits_after_conversion(converted_leaf: Gated) -> None:
    contract = converted_leaf.contract
    card = converted_leaf.memory / CARD
    converted = card.read_text(encoding="utf-8")
    assert "- Describes the flow. [1]" in converted
    reviewed = converted + "\nNow holds two.\n"
    blobs = worktree_code_blobs(converted_leaf.code, [B])

    def counted() -> bool:
        worklist = leaf_worklist(contract)
        assert worklist is not None and worklist["state"] == "complete", worklist
        (item,) = [x for x in worklist["items"] if x["subject"] == CARD_SUBJECT]
        return bool(item["facts"]["countedChange"])

    def trace_card():
        sides = leaf_onboarding_trace_sides(contract)
        assert sides is not None and sides.incomplete is None, sides
        traced = onboarding_trace_result(trace_context(contract), [B], sides, code_blobs=blobs)
        return next(item for item in traced.items if item.subject == CARD_SUBJECT)

    assert not counted()
    # Restoring the conversion-only text leaves a true flag from the earlier authored edit.
    card.write_text(reviewed, encoding="utf-8")
    assert counted()
    card.write_text(converted, encoding="utf-8")
    assert not trace_card().counted
    mechanical = _plan_row(contract, CARD_SUBJECT, "updated")
    assert mechanical.state == "refused", mechanical.render()
    assert "needs a counted change" in mechanical.render()

    assert not counted()
    card.write_text(reviewed, encoding="utf-8")
    authored = _plan_row(contract, CARD_SUBJECT, "updated")
    assert authored.state == "planned", authored.render()
    converted_leaf.rows(
        {
            "subject": CARD_SUBJECT,
            "disposition": "updated",
            "reason": "Reviewed.",
            "sources": [{"path": B, "blob": blobs[B]}],
        }
    )
    traced = trace_card()
    assert traced.counted and not traced.open


def test_worklist_marks_neither_parent_lines_invariant_as_changed(merged_leaf: Gated) -> None:
    document = leaf_worklist(merged_leaf.contract)
    assert document is not None and document["state"] == "complete", document
    assert not [
        item for item in document["items"] if item["subject"] in (INCOMING_SUBJECT, OTHER_SUBJECT)
    ]
    assert document["pairing"]["memoryBase"]["commit"] == merged_leaf.memory_base


def test_writer_accepts_no_impact_for_inherited_record_and_refuses_unchanged_revision(
    merged_leaf: Gated,
) -> None:
    contract = merged_leaf.contract
    assert leaf_worklist(contract) is not None
    honest = _plan_row(contract, OTHER_SUBJECT, "no_impact")
    assert honest.state == "planned", honest.render()
    false_change = _plan_row(contract, OTHER_SUBJECT, "changed", effect="clarify")
    assert false_change.refused
    assert "K_B revision 2 plus one" in false_change.render()


def test_trace_gate_refuses_an_updated_row_for_an_inherited_card(merged_leaf: Gated) -> None:
    write(merged_leaf.code, {B: (merged_leaf.code / B).read_text() + "\n# comment\n"})
    blobs = worktree_code_blobs(merged_leaf.code, [B])
    merged_leaf.rows(
        {
            "subject": CARD_SUBJECT,
            "disposition": "updated",
            "reason": "The upstream branch wrote it.",
            "sources": [{"path": B, "blob": blobs[B]}],
        }
    )
    contract = merged_leaf.contract
    sides = leaf_onboarding_trace_sides(contract)
    assert sides is not None and sides.incomplete is None, sides
    traced = onboarding_trace_result(trace_context(contract), [B], sides, code_blobs=blobs)
    card = next(item for item in traced.items if item.subject == CARD_SUBJECT)
    assert card.open
    assert not card.counted


def test_writer_refuses_an_updated_row_for_an_inherited_card(merged_leaf: Gated) -> None:
    contract = merged_leaf.contract
    assert leaf_worklist(contract) is not None
    report = _plan_row(contract, CARD_SUBJECT, "updated")
    assert report.refused
    assert "needs a counted change" in report.render()


def test_closeout_gate_predicates_accept_the_inherited_records(
    merged_leaf: Gated, ports: None
) -> None:
    candidate = merged_leaf.candidate()
    refusal = leaf_gate_refusal(
        merged_leaf.contract, code_tree=candidate.code, memory_tree=candidate.memory
    )
    assert refusal is None, refusal


def test_pair_gate_accepts_the_inherited_records(merged_leaf: Gated, ports: None) -> None:
    refusal = leaf_pair_refusal(
        merged_leaf.contract,
        code_commit=merged_leaf.code_base,
        memory_commit=merged_leaf.memory_base,
        bases=(merged_leaf.memory_base,),
        persist_worklist=False,
    )
    assert refusal is None, refusal


def test_live_review_compares_against_the_recorded_merge(merged_leaf: Gated) -> None:
    code_tree = git(merged_leaf.code, "rev-parse", "HEAD^{tree}")
    review = live_review_trees(merged_leaf.root, merged_leaf.contract, code_tree)
    assert isinstance(review, ReviewTrees), review
    assert review.record.memory_base.commit == merged_leaf.memory_base


def _other_commit(world: Gated) -> str:
    """Same bytes as the merged tree, with only one of the merge's parents in its history."""

    return git(
        world.memory,
        "commit-tree",
        f"{world.memory_base}^{{tree}}",
        "-p",
        f"{world.memory_base}^1",
        "-m",
        "Same tree on one parent line",
    )


@pytest.fixture(params=["missing", "unreadable", "unrelated"])
def invalid_base(merged_leaf: Gated, request: pytest.FixtureRequest) -> WorktreeContract:
    base = (
        _other_commit(merged_leaf)
        if request.param == "unrelated"
        else {"missing": "", "unreadable": "0" * 40}[request.param]
    )
    return replace(merged_leaf.contract, memory_base_commit=base)


def test_unestablished_base_makes_worklist_incomplete(invalid_base: WorktreeContract) -> None:
    document = leaf_worklist(invalid_base)
    assert document is not None and document["state"] == "incomplete", document
    assert document["incomplete"][0]["input"] == "K_B"


def test_unestablished_base_makes_trace_sides_incomplete(invalid_base: WorktreeContract) -> None:
    sides = leaf_onboarding_trace_sides(invalid_base)
    assert sides is not None and sides.incomplete and "base" in sides.incomplete, sides


def test_unestablished_base_refuses_live_review(
    merged_leaf: Gated, invalid_base: WorktreeContract
) -> None:
    code_tree = git(merged_leaf.code, "rev-parse", "HEAD^{tree}")
    review = live_review_trees(merged_leaf.root, invalid_base, code_tree)
    assert isinstance(review, ReviewRefusal) and review.code == "candidate_unresolved", review
    assert review.offending_input == "memory base"


@pytest.mark.parametrize("persisted_count", [None, False, True])
def test_writer_uses_held_card_count_or_operation_base_if_recorded_base_is_invalid(
    merged_leaf: Gated, invalid_base: WorktreeContract, persisted_count: bool | None
) -> None:
    items = []
    if persisted_count is not None:
        items.append(
            {
                "kind": "onboarding_trace",
                "subject": CARD_SUBJECT,
                "facts": {"countedChange": persisted_count, "sources": [B]},
            }
        )
    if persisted_count is False:
        # A new card edit would pass comparison against HEAD. The held false still decides.
        write(merged_leaf.memory, {CARD: "# Local update\n\nA change made by this leaf.\n"})
    _persist(invalid_base, items)
    report = _plan_row(invalid_base, CARD_SUBJECT, "updated")
    if persisted_count is True:
        assert report.state == "planned", report.render()
    else:
        assert report.refused and "needs a counted change" in report.render(), report.render()


def test_pair_gate_checks_ancestry_of_the_judged_commit_even_when_the_tree_is_unchanged(
    tmp_path: Path, ports: None
) -> None:
    world = build_gated(tmp_path)
    contract = world.contract
    same_tree = git(
        world.memory,
        "commit-tree",
        f"{world.memory_base}^{{tree}}",
        "-m",
        "Same tree on an independent root",
    )
    accepted = leaf_pair_refusal(
        contract,
        code_commit=world.code_base,
        memory_commit=world.memory_base,
        bases=(world.memory_base,),
    )
    assert accepted is None, accepted
    refusal = leaf_pair_refusal(
        contract,
        code_commit=world.code_base,
        memory_commit=same_tree,
        bases=(world.memory_base,),
    )
    assert refusal is not None and "knowledge-worklist-incomplete" in refusal, refusal
    assert "ancestor" in refusal
