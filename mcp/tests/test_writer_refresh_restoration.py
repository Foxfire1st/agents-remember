"""MIK-R54 writer corrections, exercised against real paired Git trees."""

from __future__ import annotations

from agents_remember.application.knowledge_writer import WriteRequest, write_knowledge
from test_knowledge_worklist import World as ReconsiderWorld
from test_reconsideration_surfacing import (
    EDITED_THIRD,
    RERUN_ROW,
    REVIEW,
    REVIEW_V1,
    S18B,
    _d18_links,
)
from test_reconsideration_surfacing import _write as reconsider_write
from writer_corrections_test_support import OWNER

pytest_plugins = ("test_reconsideration_surfacing",)


def test_disappeared_fired_item_restores_same_leaf_refresh(world: ReconsiderWorld) -> None:
    base = _d18_links(world)
    first = world.code_commit({REVIEW: EDITED_THIRD})
    assert (
        reconsider_write(world, [RERUN_ROW], None, worklist=world.worklist(first)).state
        == "written"
    )
    assert _d18_links(world)[2] != base[2]
    reverted = world.code_commit({REVIEW: REVIEW_V1})
    worklist = world.worklist(reverted)
    assert not any(item["subject"] == S18B for item in worklist["items"])
    report = write_knowledge(
        WriteRequest(
            world.memory, world.code, OWNER, "handoff.json", [], commit=True, worklist=worklist
        )
    )
    assert report.state == "written", report.render()
    assert _d18_links(world)[2] == base[2]


def test_committed_refresh_remains_fired_after_code_revert_until_link_is_current(
    world: ReconsiderWorld,
) -> None:
    base = _d18_links(world)
    first = world.code_commit({REVIEW: EDITED_THIRD})
    assert (
        reconsider_write(world, [RERUN_ROW], None, worklist=world.worklist(first)).state
        == "written"
    )
    refreshed = _d18_links(world)
    assert refreshed[2] != base[2]
    committed = world.memory_commit({}, first)
    assert committed != world.memory_base
    reverted = world.code_commit({REVIEW: REVIEW_V1})
    worklist = world.worklist(reverted)
    fired = [item for item in worklist["items"] if item["subject"] == S18B]
    assert len(fired) == 1 and fired[0]["facts"]["changed"][0]["trigger"] == "refresh_not_current"
    report = write_knowledge(
        WriteRequest(
            world.memory,
            world.code,
            OWNER,
            "handoff.json",
            [],
            commit=True,
            worklist=worklist,
            memory_base=world.memory_base,
        )
    )
    assert report.state == "written", report.render()
    # R55.3: this is not R54.7's disappeared-item case; keep the authored refresh.
    assert _d18_links(world) == refreshed
