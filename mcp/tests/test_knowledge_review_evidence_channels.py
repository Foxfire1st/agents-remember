"""The tree review exposes judgment and proofs, not retired record channels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
from agents_remember.application import review_source_content
from agents_remember.application.knowledge_review import read_complete_knowledge_review
from agents_remember.application.review_record_rendering import submission
from agents_remember.application.review_relationship_display import source_locations
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import KnowledgeReviewPayload, ReviewSideState
from agents_remember.models.knowledge.review_external_movement import ExternalGitTransition
from agents_remember.models.knowledge.review_judgment import ReviewJudgmentPane
from agents_remember.models.knowledge.review_source_content import ReviewSourceContentRequest
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEntry,
    SubjectAssessmentState,
)
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    FAMILY,
    INVARIANT,
    INVARIANT_PATH,
    LEAF,
    _write,
    build_world,
)
from pydantic import ValidationError
from test_review_git_trees import _seed
from test_review_meaning_effects import (
    OTHER_INVARIANT,
    _attempt_history,
    _family_seed,
    _invariant_seed,
    _meaning_world,
    _other_invariant_record,
    _resolve,
    _review,
)

pytestmark = pytest.mark.evidence_unit


def test_the_wire_requires_judgment_and_has_no_retired_channels(tmp_path: Path) -> None:
    assert set(get_args(ExternalGitTransition)) == {
        "unchanged",
        "ordinary-append",
        "rebase",
        "branch-switch",
    }
    world = build_world(tmp_path)
    result = read_complete_knowledge_review(world.config, world.review(selector=_seed()))
    assert result.state == "review", result.refusal
    assert result.payload is not None
    wire = result.payload.model_dump(mode="json")
    assert "judgment" in wire
    assert "evidence" not in wire
    assert set(wire["knowledge"]).isdisjoint(
        {"signals", "assessment", "assessments", "context", "applicability"}
    )
    assert "judgment" in KnowledgeReviewPayload.model_json_schema()["required"]
    schema = json.dumps(KnowledgeReviewPayload.model_json_schema())
    for removed in (
        "ReviewRecordChannel",
        "ReviewEvidencePane",
        "ReviewObservation",
        "ReviewAssessmentDisplay",
        "ReviewSignal",
        "ReviewApplicabilitySummary",
    ):
        assert removed not in schema


def test_tree_revision_selections_produce_only_the_retained_wire_states(tmp_path: Path) -> None:
    assert (
        "ambiguous"
        not in ReviewRevisionSelection.model_json_schema()["properties"]["state"]["enum"]
    )
    world = build_world(tmp_path)
    seen_selections, seen_sides = set(), set()

    def read(seed: InvariantIdentitySeed, **fields: object):
        payload = _review(world, _resolve(world), selector=seed, **fields)
        selection = payload.knowledge.revision_selection
        assert selection is not None
        seen_selections.add(selection.state)
        seen_sides.update(
            (payload.knowledge.before_statement.state, payload.knowledge.after_statement.state)
        )
        for heads, retained in (
            (selection.before_heads, selection.before_retained),
            (selection.after_heads, selection.after_retained),
        ):
            assert heads == retained
            assert len(heads) <= 1
        return payload

    read(_invariant_seed())
    paged = read(_invariant_seed(), page_of="knowledge", page_size=1)
    while paged.page is not None and paged.page.continuation is not None:
        paged = read(
            _invariant_seed(),
            page_of="knowledge",
            page_size=1,
            continuation=paged.page.continuation,
        )
    (world.memory_worktree / INVARIANT_PATH).unlink()
    read(_invariant_seed())
    _write(
        world.memory_worktree,
        {
            f"knowledge/invariants/{OTHER_INVARIANT}-second.json": _other_invariant_record(),
        },
    )
    read(InvariantIdentitySeed(invariant_id=text_uuid("identity", OTHER_INVARIANT)))

    assert seen_selections == {"compared", "added", "removed", "unresolved"}
    assert seen_sides == {"present", "absent", "unresolved"}
    assert seen_sides == set(get_args(ReviewSideState))
    assert seen_selections == set(
        ReviewRevisionSelection.model_json_schema()["properties"]["state"]["enum"]
    )


def test_source_currentness_literals_have_real_tree_producers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    world.edit()
    resolved = _resolve(world)
    assert resolved.baseline_code_tree_id is not None
    assert resolved.candidate_code_tree_id is not None
    request = ReviewSourceContentRequest(
        repository_id=world.review().repository_id,
        master=world.review().master,
        leaf_id=LEAF,
        path=CODE_FILE,
        before_code_tree_id=resolved.baseline_code_tree_id,
        after_code_tree_id=resolved.candidate_code_tree_id,
    )
    first = review_source_content.read_review_source_content(world.config, request)
    assert first.expansion is not None and first.expansion.currentness == "current"
    listed = first.expansion.after.text
    source = world.code_worktree / CODE_FILE
    source.write_text(source.read_text() + "\n# continued\n")
    second = review_source_content.read_review_source_content(world.config, request)
    assert second.expansion is not None and second.expansion.currentness == "superseded"
    assert second.expansion.after.text == listed
    read_side = review_source_content._side_content

    def edit_during_read(*args, **kwargs):
        answer = read_side(*args, **kwargs)
        source.write_text(source.read_text() + "\n# moved during read\n")
        return answer

    monkeypatch.setattr(review_source_content, "_side_content", edit_during_read)
    third = review_source_content.read_review_source_content(world.config, request)
    assert third.expansion is not None and third.expansion.currentness == "unmeasured"
    assert third.expansion.after.text == listed
    assert "could not be measured" in third.expansion.currentness_detail


def test_judgment_keeps_all_attempts_and_membership_rows(tmp_path: Path) -> None:

    world = _meaning_world(tmp_path)
    _write(
        world.memory_worktree,
        {
            f"knowledge/history/{LEAF}-attempt-3.json": _attempt_history(
                3,
                [
                    {
                        "id": "ROW-N11111",
                        "subject": INVARIANT,
                        "disposition": "no_impact",
                        "reason": "The obligation holds without another meaning change.",
                        "revision": 4,
                        "items": [],
                        "covers": [],
                        "because": ["DEC-A11111"],
                    },
                    {
                        "id": "ROW-N11112",
                        "subject": f"membership:{INVARIANT}",
                        "disposition": "no_family",
                        "reason": "No joint obligation.",
                        "revision": 4,
                        "items": [],
                        "considered": [],
                    },
                ],
                closed=True,
            ),
        },
    )
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    assert payload.judgment.state == "rows"
    rows = payload.judgment.rows
    assert [(row.attempt, row.row_id) for row in rows] == [
        (1, "ROW-A11111"),
        (2, "ROW-B11111"),
        (3, "ROW-N11111"),
        (3, "ROW-N11112"),
        (10, "ROW-C11111"),
    ]
    assert rows[2].disposition == "no_impact"
    assert rows[2].because == ("DEC-A11111",)
    assert rows[2].covered_entries == 0
    assert rows[2].examined_members is None
    assert rows[2].closed is True
    assert rows[2].reason == "The obligation holds without another meaning change."
    assert rows[2].history_path == f"knowledge/history/{LEAF}-attempt-3.json"
    assert rows[3].subject == f"membership:{INVARIANT}"
    assert rows[0].effect == "weaken"
    assert "ROW-X11111" not in {row.row_id for row in rows}
    family = _review(world, _resolve(world), selector=_family_seed())
    assert [(row.attempt, row.effect, row.examined_members) for row in family.judgment.rows] == [
        (1, "clarify", 1),
        (2, None, 1),
    ]
    for state in ("none", "unavailable"):
        with pytest.raises(ValidationError, match="rows state"):
            ReviewJudgmentPane.model_validate({"state": state, "rows": rows, "detail": "Reason"})
    with pytest.raises(ValidationError, match="rows state"):
        ReviewJudgmentPane(state="rows", detail="Reason")


def test_proofs_survive_a_history_failure_and_use_after_code_state(tmp_path: Path) -> None:

    world = build_world(tmp_path)
    sidecar = world.memory_worktree / f"onboarding/{CODE_FILE}.json"
    document = json.loads(sidecar.read_text())
    realization = document["realizes"][0]
    document["proves"] = [
        {
            "id": "PRF-A11111",
            "invariant": realization["invariant"],
            "anchor": realization["anchor"],
            "facet": "Landing keeps the value.",
        }
    ]
    sidecar.write_text(json.dumps(document))
    first = _review(world, _resolve(world), selector=_invariant_seed())
    assert first.judgment.state == "none"
    assert first.judgment.detail == "This leaf recorded no judgment about this record"
    assert len(first.judgment.proofs) == 1
    proof = first.judgment.proofs[0]
    assert (proof.id, proof.path, proof.symbol, proof.facet, proof.state) == (
        "PRF-A11111",
        CODE_FILE,
        "land",
        "Landing keeps the value.",
        "current",
    )
    world.edit()
    history = world.memory_worktree / f"knowledge/history/{LEAF}.json"
    history.write_text("{broken")
    broken = _review(world, _resolve(world), selector=_invariant_seed())
    assert broken.judgment.state == "unavailable"
    assert broken.judgment.rows == ()
    assert f"knowledge/history/{LEAF}.json" in broken.judgment.detail
    assert len(broken.judgment.proofs) == 1
    assert broken.judgment.proofs[0].state == "stale"
    assert broken.judgment.proofs[0].detail
    (world.code_worktree / CODE_FILE).unlink()
    missing = _review(world, _resolve(world), selector=_invariant_seed())
    assert missing.judgment.state == "unavailable"
    assert missing.judgment.proofs[0].state == "stale"
    assert missing.judgment.proofs[0].detail
    (world.code_worktree / CODE_FILE).write_bytes(b"\xff")
    unreadable = _review(world, _resolve(world), selector=_invariant_seed())
    assert unreadable.judgment.proofs[0].state == "unverifiable"
    assert "UTF-8" in unreadable.judgment.proofs[0].detail


def test_family_proofs_include_members_beyond_the_roster_page(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    _write(
        world.memory_worktree,
        {
            f"knowledge/invariants/{OTHER_INVARIANT}-second.json": _other_invariant_record(),
        },
    )
    family_path = world.memory_worktree / f"knowledge/families/{FAMILY}-landing.json"
    family = json.loads(family_path.read_text())
    family["members"].append(OTHER_INVARIANT)
    family_path.write_text(json.dumps(family))
    sidecar = world.memory_worktree / f"onboarding/{CODE_FILE}.json"
    document = json.loads(sidecar.read_text())
    anchor = document["realizes"][0]["anchor"]
    document["proves"] = [
        {"id": proof, "invariant": invariant, "anchor": anchor, "facet": "Values land."}
        for proof, invariant in (("PRF-A11111", INVARIANT), ("PRF-B11111", OTHER_INVARIANT))
    ]
    sidecar.write_text(json.dumps(document))
    payload = _review(
        world, _resolve(world), selector=_family_seed(), page_of="family_members", page_size=1
    )
    assert payload.family_context is not None
    page = payload.family_context.entries[0].after
    assert page is not None
    assert page.page is not None and page.page.counts.primary_items_remaining > 0
    assert {proof.invariant for proof in payload.judgment.proofs} == {INVARIANT, OTHER_INVARIANT}
    assert {proof.state for proof in payload.judgment.proofs} == {"current"}


def test_family_statement_sides_show_their_own_guarantee(tmp_path: Path) -> None:

    world = build_world(tmp_path)
    family_path = world.memory_worktree / f"knowledge/families/{FAMILY}-landing.json"
    family = json.loads(family_path.read_text())
    family["guarantee"] = "Landing holds for every admitted member."
    family_path.write_text(json.dumps(family))
    payload = _review(world, _resolve(world), selector=_family_seed())
    assert payload.knowledge.before_statement.state == "present"
    assert payload.knowledge.after_statement.state == "present"
    assert payload.knowledge.before_statement.text == "Landing holds."
    assert payload.knowledge.after_statement.text == "Landing holds for every admitted member."


def test_one_address_keeps_both_movements_in_its_location_rows(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    original = next(
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind == "realization" and movement.before and movement.after
    )
    assert original.after is not None
    movements = tuple(
        original.model_copy(
            update={
                "transition": "moved",
                "after": original.after.model_copy(update={"path": path}),
                "statement": f"The recorded entry moved to {path}",
            }
        )
        for path in ("pkg/first.py", "pkg/second.py")
    )
    old_address = [
        location for location in source_locations(movements) if location.path == CODE_FILE
    ]
    assert [location.movement for location in old_address] == list(movements)


def test_record_origin_keeps_each_sides_task_leaf_and_handoff_entry(tmp_path: Path) -> None:

    world = build_world(tmp_path)
    path = world.memory_worktree / INVARIANT_PATH
    document = json.loads(path.read_text())
    document["origin"] = {
        "task": "260101-NEW",
        "wave": "260101-NEW-W1",
        "handoffEntry": "entry-seven",
        "handoff": {"path": "handoff.json", "evidence": ["The authored hand-off context."]},
        "legacyId": "source-record-seven",
    }
    path.write_text(json.dumps(document))
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    assert payload.knowledge.before_origin is not None
    assert payload.knowledge.after_origin is not None
    assert payload.knowledge.before_origin.task == "260101-OLD"
    assert payload.knowledge.before_origin.leaf == "260101-OLD-L1"
    assert payload.knowledge.after_origin.task == "260101-NEW"
    assert payload.knowledge.after_origin.wave == "260101-NEW-W1"
    assert payload.knowledge.after_origin.handoff_entry == "entry-seven"
    assert (
        payload.knowledge.after_origin.model_dump(mode="json", by_alias=True, exclude_none=True)
        == document["origin"]
    )


def test_unresolved_count_must_match_the_entries_not_only_the_total() -> None:

    with pytest.raises(ValidationError, match="unresolved count"):
        SubjectAssessmentState(
            status="current",
            assessmentCount=1,
            unresolvedCount=0,
            staleCount=0,
            assessments=(
                AssessmentEntry(
                    assessmentId="one", disposition="unresolved", currentness="current"
                ),
            ),
        )
    with pytest.raises(ValidationError, match="unresolved count"):
        SubjectAssessmentState(
            status="unresolved",
            assessmentCount=1,
            unresolvedCount=1,
            staleCount=0,
            assessments=(
                AssessmentEntry(
                    assessmentId="one", disposition="no_concern_found", currentness="current"
                ),
            ),
        )


def test_read_surface_submission_words_name_the_judgment_writer() -> None:
    for stale in (False, True):
        answer = submission(stale)
        visible = f"{answer.reason} {answer.next_action}".lower()
        assert not any(word in visible for word in ("assessment", "evidence claim", "detection"))
