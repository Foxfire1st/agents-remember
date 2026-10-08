"""MIK-R14 rule 1, erratum: a ``changed`` family row fires a ``route:`` reconsider target.

A family has one governing row (MIK-R07). A leaf that changes a family's guarantee together with
its routes writes that row as ``changed``, and the row answers the route conditions too. The route
trigger of the reconsideration surfacing read only ``rerouted``, ``retired`` and ``deleted`` rows,
so such a reroute raised nothing (L37 review R6). It now also fires on a ``changed`` row about a
family that has the route when the family's routes differ between K_B and K_C.

Beside ``test_reconsideration_surfacing.py`` rather than inside it for the repository's file-size
rail; the case that a ``changed`` row with unchanged routes raises nothing stays there
(``test_a_route_target_fires_only_through_a_rerouting_or_retiring_row``).
"""

from __future__ import annotations

import json

from agents_remember.models.knowledge_files.canonical import canonical_text
from test_reconsideration_surfacing import (
    LEAF,
    ROUTED,
    World,
    _alternatives,
    _candidates,
    _history,
    decision,
    world,
)

__all__ = ["world"]  # the fixture this module's case takes


def test_a_changed_family_row_fires_the_route_target_when_the_familys_routes_differ(
    world: World,
) -> None:
    """Erratum to MIK-R14 rule 1 (L37 review R6): a family has one governing row, and a leaf that
    changes its guarantee together with its routes writes it as ``changed``. That row stands for
    the reroute, so a decision whose ``reconsider_on`` target is the route is raised.

    Catches a reroute that hides behind a ``changed`` row: the decision's alternative would never
    be looked at again. A ``changed`` row about a family whose routes are the same on both sides
    still raises nothing (the case above).
    """

    routed = decision(
        ROUTED,
        _alternatives(("Local", "chosen", None), ("Global", "rejected", "The tree flattens.")),
        [{"relation": "reconsider_on", "target": "route:pkg", "alternative": 1}],
    )
    world.memory_base = world.memory_commit(
        {f"knowledge/decisions/{ROUTED}-routes.json": routed}, world.code_base
    )
    subject = f"reconsider:{ROUTED}#1"
    record = "knowledge/families/FAM-F00002-group.json"
    moved = json.loads((world.memory / record).read_text(encoding="utf-8"))
    assert moved["routes"] == ["pkg"]
    moved.update(revision=2, guarantee="They hold together, in the tests too.", routes=["tests"])
    row = {
        "id": "ROW-AAAAAA",
        "subject": "FAM-F00002",
        "disposition": "changed",
        "reason": "The guarantee was widened and the family now lives under tests.",
        "items": [],
        "examined": [{"id": "INV-CCCCCC", "revision": 1}],
    }
    world.memory_commit(
        {record: canonical_text(moved), f"knowledge/history/{LEAF}.json": _history(row)},
        world.code_base,
    )

    found = _candidates(world.worklist(world.code_base))

    assert subject in found
    (changed,) = found[subject]["facts"]["changed"]
    assert (changed["trigger"], changed["rowSubject"], changed["disposition"]) == (
        "history_row",
        "FAM-F00002",
        "changed",
    )
