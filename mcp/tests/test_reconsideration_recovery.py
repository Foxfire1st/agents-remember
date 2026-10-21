"""MIK-R194 rules 7 and 8: the judgment stays, and every refusal's advice leads somewhere.

One test runs the whole D430 sequence on a leaf that removed the code a ``reconsider_on`` link names:
the item opens, an empty hand-off changes nothing, and a Curator-authored row closes it. The other
follows each rule-7 refusal's own advice to an admitted row or a raise, so no refusal offers a route
that cannot work.
"""

from __future__ import annotations

import json
from typing import Any

from agents_remember.application.knowledge_currentness.observe import CodeTree, open_code_tree
from agents_remember.application.knowledge_gate.predicates import (
    GateContext,
    reconsideration_row_open,
)
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.memory.knowledge_index import git_tree_snapshot
from agents_remember.models.knowledge_files.reconsideration import reconsideration_item_open
from knowledge_writer_test_support import git
from test_reconsideration_repoint import EDITED_THIRD, RERUN_ROW
from test_reconsideration_surfacing import (
    D18_PATH,
    REVIEW,
    REVIEW_V1,
    S18B,
    SHIFTED,
    _candidates,
    _d18_links,
    _reauthor_link,
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


def _rows_by_subject(world: Any, report: Any) -> dict[str, str]:
    """The leaf's stored history rows, by subject, as the gate reads them."""

    (outcome,) = report.rows
    history = json.loads((world.memory / outcome.history).read_text(encoding="utf-8"))
    return {one["subject"]: one["id"] for one in history["rows"]}


def test_the_d430_sequence_opens_the_item_and_a_curator_row_closes_it(world: Any) -> None:
    """A removed symbol opens the item; nothing answers it on its own; a row does."""

    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed)
    item = _candidates(worklist)[S18B]
    assert item["kind"] == "reconsideration_candidate"
    assert reconsideration_item_open(item, {}) is True
    links_before = _d18_links(world)
    memory_before = (world.memory / D18_PATH).read_bytes()

    # An empty hand-off answers nothing and changes nothing.
    empty = _write(world, [], None, worklist=worklist)
    assert empty.state != "refused", empty.render()
    assert _d18_links(world) == links_before
    assert (world.memory / D18_PATH).read_bytes() == memory_before
    assert reconsideration_item_open(item, {}) is True

    # A Curator-authored row with the item's subject closes it: it re-points the fired link, because
    # the link's code is gone and only a judged re-point may replace it (rule 4).
    report = _write(world, [_row(items=[item["id"]], repoint=[REPOINT])], None, worklist=worklist)
    assert report.state == "written", report.render()
    assert reconsideration_item_open(item, _rows_by_subject(world, report)) is False


def test_the_re_authored_refusals_advice_restores_the_link_and_admits(world: Any) -> None:
    """``restore its base target in `records` `` is advice that works: doing it admits the row."""

    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed)
    item = _candidates(worklist)[S18B]
    base_target = _d18_links(world)[2]["target"]
    other = world.anchor(removed, REVIEW, {"kind": "symbol", "name": "other"})
    _reauthor_link(world, 2, target={**other, "path": REVIEW})
    refused = _write(world, [_row(items=[item["id"]], repoint=[REPOINT])], None, worklist=worklist)
    assert refused.refused, refused.render()
    assert "restore its base target in `records`, or `raise`" in refused.render()

    # Following the advice: put the base target back byte for byte, then the row is admitted.
    _reauthor_link(world, 2, target=base_target)
    admitted = _write(world, [_row(items=[item["id"]], repoint=[REPOINT])], None, worklist=worklist)
    assert admitted.state == "written", admitted.render()


def test_the_still_maps_refusals_advice_removes_the_repoint_and_admits(world: Any) -> None:
    """``remove its repoint`` is advice that works: the row then stands on the refresh alone."""

    shifted = world.code_commit({REVIEW: SHIFTED.replace("value * 2", "value * 9")})
    worklist = world.worklist(shifted)
    item = _candidates(worklist)[S18B]
    refused = _write(world, [_row(items=[item["id"]], repoint=[REPOINT])], None, worklist=worklist)
    assert refused.refused, refused.render()
    assert "remove its repoint" in refused.render()

    admitted = _write(world, [_row(items=[item["id"]])], None, worklist=worklist)
    assert admitted.state == "written", admitted.render()


def test_the_refresh_refusals_advice_names_a_route_that_works(world: Any) -> None:
    """``recompute the worklist, or `raise` `` names two routes, and neither is a dead end.

    The refusal names the recompute that produces the item to answer and the raise that hands the
    judgment on. This test holds the refusal and the unchanged state; the recompute route is the
    rerun the sibling re-point tests take, and the raise route is proven for a task-owned leaf in the
    command suite.
    """

    _write(
        world, [RERUN_ROW], None, worklist=world.worklist(world.code_commit({REVIEW: EDITED_THIRD}))
    )
    first = world.code_commit({REVIEW: EDITED_THIRD})
    world.memory_commit({}, first)
    reverted = world.code_commit({REVIEW: REVIEW_V1})
    worklist = world.worklist(reverted)
    item = _candidates(worklist)[S18B]
    other = world.anchor(reverted, REVIEW, {"kind": "symbol", "name": "other"})
    _reauthor_link(world, 2, target={**other, "path": REVIEW})
    before = (world.memory / D18_PATH).read_bytes()

    refused = _write(world, [_row(items=[item["id"]], repoint=[REPOINT])], None, worklist=worklist)
    assert refused.refused, refused.render()
    assert "the link's current target is neither the earlier refresh" in refused.render()
    assert "recompute the worklist, or `raise`" in refused.render()
    assert (world.memory / D18_PATH).read_bytes() == before


def test_an_empty_prose_row_leaves_the_gate_item_unanswered(world: Any) -> None:
    """L186-R1-F12: the empty row goes through the writer, and the GATE still says unanswered.

    The writer is asked for a row with nothing in its prose; whatever it does with it, the gate's own
    predicate must still find the item open, and the decision and history files must be untouched.
    """

    removed = world.code_commit({REVIEW: GONE})
    worklist = world.worklist(removed)
    item = _candidates(worklist)[S18B]
    decision_before = {
        path: (world.memory / path).read_bytes()
        for path in (world.memory / "knowledge/decisions").glob("*.json")
    }
    history_before = {
        path: path.read_bytes() for path in (world.memory / "knowledge/history").glob("*.json")
    }

    submitted = _write(
        world,
        [{"subject": S18B, "disposition": "still_rejected", "reason": ""}],
        None,
        worklist=worklist,
    )
    rows = {} if submitted.refused else _rows_by_subject(world, submitted)

    candidate = KnowledgeSide.from_snapshot(
        "K_C", git_tree_snapshot(world.memory, git(world.memory, "rev-parse", "HEAD"))
    )
    opened = open_code_tree(CodeTree(world.code, removed))
    assert opened.problem is None, opened.problem
    context = GateContext(owner=None, history=None, candidate=candidate, code=opened)
    reason = reconsideration_row_open(item, context)
    assert reason is not None, "the gate found the item answered by an empty row"
    assert "no satisfying row or change answers it" in reason

    for path, before in decision_before.items():
        assert (world.memory / path).read_bytes() == before
    for path, before in history_before.items():
        assert (world.memory / path).read_bytes() == before
    assert reconsideration_item_open(item, rows) is True
