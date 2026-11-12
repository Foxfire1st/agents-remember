"""MIK-R194 rules 4 to 7: a ``still_rejected`` row that re-points a fired link whose code is gone."""

from __future__ import annotations

import json
from typing import Any

import pytest
from agents_remember.models.knowledge_files.history import ReconsiderationRow
from pydantic import ValidationError
from test_reconsideration_surfacing import (
    D18_PATH,
    EDITED_THIRD,
    RERUN_ROW,
    REVIEW,
    REVIEW_V1,
    S18B,
    SHIFTED,
    _candidates,
    _d18_links,
    _reauthor_link,
    _revision,
    _v2_approved,
    _write,
)

pytest_plugins = ("test_reconsideration_surfacing",)

GONE = REVIEW_V1.replace("def third(value):\n    return value * 2\n", "")
REPOINT = {"link": 2, "path": REVIEW, "locator": {"kind": "symbol", "value": "fourth"}}
REASON = (
    "fourth carries the reveal now, and it is not the landing; the condition is not met (D428)."
)


def _row(**extra: Any) -> dict[str, Any]:
    return {"subject": S18B, "disposition": "still_rejected", "reason": REASON, **extra}


def _stored_row(world: Any, report: Any) -> dict[str, Any]:
    (outcome,) = report.rows
    history = json.loads((world.memory / outcome.history).read_text(encoding="utf-8"))
    return next(one for one in history["rows"] if one["id"] == outcome.id)


def test_a_still_rejected_row_repoints_the_link_whose_code_is_gone(world: Any) -> None:
    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed)
    report = _write(world, [_row(repoint=[REPOINT])], None, worklist=worklist)
    assert report.state == "written", report.render()
    target = _d18_links(world)[2]["target"]
    assert (target["path"], target["locator"]) == (REVIEW, {"kind": "symbol", "name": "fourth"})
    # The decision's revision goes up once in the leaf, as any judged change to its links does.
    assert _revision(world) == 2
    stored = _stored_row(world, report)
    (item,) = stored["repoint"]
    assert item["link"] == 2
    assert item["before"]["path"] == REVIEW
    assert item["before"]["locator"] == {"kind": "symbol", "name": "third"}
    assert item["after"]["locator"] == {"kind": "symbol", "name": "fourth"}
    assert item["after"]["content"] == target["content"]
    # The report lists the re-point with its link, both targets and the reason.
    (note,) = [one for one in report.notes if "link 2" in one]
    assert REVIEW in note and "fourth" in note and REASON in note
    # A recompute answers the item: the row it holds is this one.
    world.memory_commit({}, removed)
    answered = _candidates(world.worklist(removed))[S18B]
    assert answered["satisfiedBy"] == report.rows[0].id


def test_a_rerun_of_the_same_repoint_writes_the_same_bytes_and_bumps_nothing(
    world: Any,
) -> None:
    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed)
    row = _row(repoint=[REPOINT])
    assert _write(world, [row], None, worklist=worklist).state == "written"
    links = _d18_links(world)
    decision = (world.memory / D18_PATH).read_bytes()
    again = _write(world, [row], None, worklist=worklist)
    assert again.state == "written", again.render()
    assert again.records == ()
    assert _revision(world) == 2
    assert _d18_links(world) == links
    assert (world.memory / D18_PATH).read_bytes() == decision


def test_only_the_named_link_is_repointed_and_the_other_fires_as_today(
    world: Any, tmp_path: Any
) -> None:
    coordination = _v2_approved(tmp_path)
    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed, coordination_root=coordination)
    item = _candidates(worklist)[S18B]
    triggers = {one["trigger"] for one in item["facts"]["changed"]}
    assert triggers == {"anchor", "requirement_version"}
    report = _write(world, [_row(repoint=[REPOINT])], None, worklist=worklist)
    assert report.state == "written", report.render()
    links = _d18_links(world)
    assert links[2]["target"]["locator"] == {"kind": "symbol", "name": "fourth"}
    assert links[3]["target"]["version"] == "v2"
    assert _revision(world) == 2


def test_a_repoint_on_a_link_that_still_maps_is_refused(world: Any) -> None:
    shifted = world.code_commit({REVIEW: SHIFTED.replace("value * 2", "value * 9")})
    worklist = world.worklist(shifted)
    before = (world.memory / D18_PATH).read_bytes()
    report = _write(world, [_row(repoint=[REPOINT])], None, worklist=worklist)
    text = report.render()
    assert report.refused, text
    assert "links.2: the fired target maps to C" in text
    assert "remove its repoint" in text
    assert (world.memory / D18_PATH).read_bytes() == before


def test_a_link_re_authored_in_the_leaf_is_refused_with_the_way_out(world: Any) -> None:
    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed)
    # The leaf points link 2 at another construct by hand: not the base target, not this re-point.
    other = world.anchor(removed, REVIEW, {"kind": "symbol", "name": "other"})
    before = _reauthor_link(world, 2, target={**other, "path": REVIEW})
    report = _write(world, [_row(repoint=[REPOINT])], None, worklist=worklist)
    text = report.render()
    assert report.refused, text
    assert "the link was re-authored in the leaf" in text
    assert "restore its base target in `records`, or `raise`" in text
    assert (world.memory / D18_PATH).read_bytes() == before


def test_a_raise_carrying_a_repoint_is_refused(world: Any) -> None:
    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed)
    before = (world.memory / D18_PATH).read_bytes()
    report = _write(
        world, [{**_row(), "disposition": "raise", "repoint": [REPOINT]}], None, worklist=worklist
    )
    assert report.refused, report.render()
    assert "a raise carries no 'repoint'" in report.render()
    assert not report.questions_published
    assert (world.memory / D18_PATH).read_bytes() == before


def test_a_repoint_answers_an_earlier_refresh_that_no_longer_stands(world: Any) -> None:
    assert (
        _write(
            world,
            [RERUN_ROW],
            None,
            worklist=world.worklist(world.code_commit({REVIEW: EDITED_THIRD})),
        ).state
        == "written"
    )
    first = world.code_commit({REVIEW: EDITED_THIRD})
    world.memory_commit({}, first)
    reverted = world.code_commit({REVIEW: REVIEW_V1})
    worklist = world.worklist(reverted)
    item = _candidates(worklist)[S18B]
    (fired,) = item["facts"]["changed"]
    assert fired["trigger"] == "refresh_not_current"
    row = _row(
        items=[item["id"]],
        repoint=[{"link": 2, "path": REVIEW, "locator": {"kind": "symbol", "value": "third"}}],
    )
    report = _write(world, [row], None, worklist=worklist)
    assert report.state == "written", report.render()
    target = _d18_links(world)[2]["target"]
    assert target["locator"] == {"kind": "symbol", "name": "third"}
    stored = _stored_row(world, report)
    (one,) = stored["repoint"]
    # The recorded ``before`` is the target the item fired on, not the refresh that no longer stands.
    assert one["before"]["content"] == fired["baseTarget"]["content"]
    assert one["after"]["locator"] == {"kind": "symbol", "name": "third"}


def test_a_refresh_not_current_repoint_must_name_the_item(world: Any) -> None:
    assert (
        _write(
            world,
            [RERUN_ROW],
            None,
            worklist=world.worklist(world.code_commit({REVIEW: EDITED_THIRD})),
        ).state
        == "written"
    )
    first = world.code_commit({REVIEW: EDITED_THIRD})
    world.memory_commit({}, first)
    reverted = world.code_commit({REVIEW: REVIEW_V1})
    worklist = world.worklist(reverted)
    before = (world.memory / D18_PATH).read_bytes()
    report = _write(
        world,
        [
            _row(
                repoint=[
                    {"link": 2, "path": REVIEW, "locator": {"kind": "symbol", "value": "third"}}
                ]
            )
        ],
        None,
        worklist=worklist,
    )
    assert report.refused, report.render()
    assert "name that item in the row's 'items'" in report.render()
    assert (world.memory / D18_PATH).read_bytes() == before


def test_the_stored_shape_refuses_a_repoint_on_a_raise_or_without_paths() -> None:
    anchor = {
        "path": REVIEW,
        "locator": {"kind": "symbol", "name": "third"},
        "blob": "a" * 40,
        "content": "sha256:" + "0" * 64,
    }
    row = {
        "id": "ROW-AAAAAA",
        "subject": S18B,
        "disposition": "raise",
        "reason": "May be met.",
        "items": [],
        "repoint": [{"link": 2, "before": anchor, "after": anchor}],
    }
    with pytest.raises(ValidationError, match="only a still_rejected row re-points a link"):
        ReconsiderationRow.model_validate(row)
    without_path = {key: value for key, value in anchor.items() if key != "path"}
    with pytest.raises(ValidationError):
        ReconsiderationRow.model_validate(
            {
                **row,
                "disposition": "still_rejected",
                "repoint": [
                    {"link": 2, "before": without_path, "after": anchor},
                ],
            }
        )
    with pytest.raises(ValidationError, match="appear more than once"):
        ReconsiderationRow.model_validate(
            {
                **row,
                "disposition": "still_rejected",
                "repoint": [
                    {"link": 2, "before": anchor, "after": anchor},
                    {"link": 2, "before": anchor, "after": anchor},
                ],
            }
        )


def test_an_intervening_answer_is_not_overwritten(world: Any) -> None:
    """L186-R1-F07: a refresh answer may replace only the earlier refresh it fired on."""

    assert (
        _write(
            world,
            [RERUN_ROW],
            None,
            worklist=world.worklist(world.code_commit({REVIEW: EDITED_THIRD})),
        ).state
        == "written"
    )
    first = world.code_commit({REVIEW: EDITED_THIRD})
    world.memory_commit({}, first)
    reverted = world.code_commit({REVIEW: REVIEW_V1})
    worklist = world.worklist(reverted)
    item = _candidates(worklist)[S18B]
    (fired,) = item["facts"]["changed"]
    assert fired["trigger"] == "refresh_not_current"

    # Another answer re-points the link between the item's computation and the row's arrival.
    other = world.anchor(reverted, REVIEW, {"kind": "symbol", "name": "other"})
    _reauthor_link(world, 2, target={**other, "path": REVIEW})
    decision_before = (world.memory / D18_PATH).read_bytes()
    history_before = (world.memory / "knowledge/history/260928-MIK-L99.json").read_bytes()

    report = _write(world, [_row(items=[item["id"]], repoint=[REPOINT])], None, worklist=worklist)
    assert report.refused, report.render()
    assert "the link's current target is neither the earlier refresh" in report.render()
    assert (world.memory / D18_PATH).read_bytes() == decision_before
    assert (world.memory / "knowledge/history/260928-MIK-L99.json").read_bytes() == history_before
