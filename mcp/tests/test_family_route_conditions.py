"""MIK-R06@v2: family route maintenance, raised as ``family_route_condition`` worklist items.

Every case builds a real code repository and a converted memory repository under ``tmp_path``
(through the MIK-R08 worklist fixture) with four families:

* ``FAM-R00001`` -- routes ``svc/application`` and ``svc/worktrees``, realized in three files;
* ``FAM-W00001`` -- an ``unrealized_family`` routed at ``svc/legacy``;
* ``FAM-X00001`` -- an exported family with no routes (``route_unassigned``);
* ``FAM-T00001`` -- a retired family routed at ``svc/legacy``.

A case commits a code candidate C and a memory candidate K_C and computes the worklist exactly as
the leaf route does; the satisfying row is a family row in the leaf's own history file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist import (
    ITEM_KINDS,
    family_route_item_open,
    satisfying_row,
)
from agents_remember.memory_quality.knowledge_validator import validate_tree
from agents_remember.memory_quality.knowledge_validator.trees import (
    code_tree_from_git,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_worklist_section import knowledge_worklist_lines
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import HistoryFile
from test_knowledge_worklist import World, _init, git, invariant

OWNER = "260928-MIK-L99"
KIND = "family_route_condition"
ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
LEDGER = "svc/worktrees/ledger.py"
INTEGRATE = "svc/worktrees/integrate.py"
KEEP = "svc/worktrees/keep.py"
APP = "svc/application/app.py"
MISC = "svc/other/misc.py"
OLD = "svc/legacy/old.py"
CODE = {
    LEDGER: "def ledger():\n    return 1\n",
    INTEGRATE: "def integrate():\n    return 2\n",
    KEEP: "def keep():\n    return 0\n",
    APP: "def app():\n    return 3\n",
    MISC: "def misc():\n    return 5\n",
    OLD: "def old():\n    return 6\n",
}
ENTRIES: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "RLZ-R00001": ("INV-R1R1R1", LEDGER, {"kind": "symbol", "name": "ledger"}, "realizes"),
    "RLZ-R00002": ("INV-R2R2R2", INTEGRATE, {"kind": "symbol", "name": "integrate"}, "realizes"),
    "RLZ-R00003": ("INV-R3R3R3", APP, {"kind": "symbol", "name": "app"}, "realizes"),
    "RLZ-X00001": ("INV-X1X1X1", MISC, {"kind": "symbol", "name": "misc"}, "realizes"),
}
ROUTED = "FAM-R00001"
UNREALIZED = "FAM-W00001"
UNASSIGNED = "FAM-X00001"
RETIRED = "FAM-T00001"


def family(
    family_id: str,
    members: list[str],
    routes: list[str],
    *,
    revision: int = 1,
    status: str = "accepted",
) -> str:
    return canonical_text(
        {
            "schema": "ar-family/v1",
            "id": family_id,
            "revision": revision,
            "status": status,
            "title": family_id,
            "guarantee": "They hold together.",
            "members": members,
            "routes": routes,
            "admission": "legacy-unassessed",
            "origin": ORIGIN,
        }
    )


def families(**routes: list[str]) -> dict[str, str | bytes | None]:
    """The four family records; keyword arguments replace a family's routes (by short name)."""

    return {
        f"knowledge/families/{ROUTED}-group.json": family(
            ROUTED,
            ["INV-R1R1R1", "INV-R2R2R2", "INV-R3R3R3"],
            routes.get("routed", ["svc/application", "svc/worktrees"]),
            revision=2 if "routed" in routes else 1,
        ),
        f"knowledge/families/{UNREALIZED}-group.json": family(
            UNREALIZED,
            ["INV-W1W1W1"],
            routes.get("unrealized", ["svc/legacy"]),
            revision=2 if "unrealized" in routes else 1,
        ),
        f"knowledge/families/{UNASSIGNED}-group.json": family(
            UNASSIGNED,
            ["INV-X1X1X1"],
            routes.get("unassigned", []),
            revision=2 if "unassigned" in routes else 1,
        ),
        f"knowledge/families/{RETIRED}-group.json": family(
            RETIRED, ["INV-T1T1T1"], ["svc/legacy"], status="retired"
        ),
    }


def family_row(family_id: str, disposition: str, members: list[str]) -> dict[str, Any]:
    return {
        "id": f"ROW-{family_id[4:]}",
        "subject": family_id,
        "disposition": disposition,
        "reason": "The routes follow the code.",
        "items": [],
        "examined": [{"id": member, "revision": 1} for member in members],
    }


def history(*rows: dict[str, Any]) -> dict[str, str | bytes | None]:
    document = {"schema": "ar-history/v1", "leaf": OWNER, "closed": False, "rows": list(rows)}
    return {f"knowledge/history/{OWNER}.json": canonical_text(document)}


@pytest.fixture
def world(tmp_path: Path) -> World:
    world = World(root=tmp_path, code=tmp_path / "code", memory=tmp_path / "memory")
    _init(world.code)
    world.code_base = world.code_commit(dict(CODE))
    _init(world.memory)
    invariants = sorted({one[0] for one in ENTRIES.values()} | {"INV-W1W1W1", "INV-T1T1T1"})
    files: dict[str, str | bytes | None] = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        **{f"knowledge/invariants/{one}-rule.json": invariant(one) for one in invariants},
        **families(),
        **world.sidecars(world.code_base, ENTRIES),
    }
    world.memory_base = world.memory_commit(files, world.code_base)
    return world


def moved(entries: dict[str, str]) -> dict[str, tuple[str, str, dict[str, Any], str]]:
    """ENTRIES with the given entries' paths replaced: the curator's re-location in K_C."""

    return {
        entry_id: (inv, entries.get(entry_id, path), locator, key)
        for entry_id, (inv, path, locator, key) in ENTRIES.items()
    }


def relocate(world: World, code: str, entries: dict[str, str]) -> dict[str, str | bytes | None]:
    """K_C's sidecars after the curator re-located ``entries``; old sidecars removed."""

    files: dict[str, str | bytes | None] = dict(world.sidecars(code, moved(entries)))
    for entry_id in entries:
        old = f"onboarding/{ENTRIES[entry_id][1]}.json"
        if (world.memory / old).is_file():
            files.setdefault(old, None)
    return files


def route_items(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assert document["state"] == "complete", document.get("incomplete")
    return {item["subject"]: item for item in document["items"] if item["kind"] == KIND}


def k_c_history(world: World) -> HistoryFile | None:
    path = world.memory / f"knowledge/history/{OWNER}.json"
    if not path.is_file():
        return None
    return HistoryFile.model_validate_json(path.read_text(encoding="utf-8"))


def assert_predicate_agrees(world: World, found: dict[str, dict[str, Any]]) -> None:
    """The stored-item predicate gives what the live run recorded in ``satisfiedBy``."""

    rows = k_c_history(world)
    for item in found.values():
        assert family_route_item_open(item, rows) is (item["satisfiedBy"] is None), item


def move_out_of_worktrees(world: World) -> str:
    """The conforming example's code move: two family files into a new ``svc/landing``."""

    (world.code / "svc/landing").mkdir(parents=True)
    git(world.code, "mv", LEDGER, "svc/landing/ledger.py")
    git(world.code, "mv", INTEGRATE, "svc/landing/integrate.py")
    return world.code_commit({})


# --------------------------------------------------------------------------------------------------
# The conforming example, end to end
# --------------------------------------------------------------------------------------------------


def test_a_directory_move_raises_emptied_and_uncovered_and_a_rerouted_row_satisfies_them(
    world: World,
) -> None:
    code = move_out_of_worktrees(world)
    landing = {"RLZ-R00001": "svc/landing/ledger.py", "RLZ-R00002": "svc/landing/integrate.py"}
    # Review N1: the first worklist, before the curator moves any entry, already shows both
    # conditions -- K_C's entries count where their renamed files lie at C.
    world.memory_commit({}, code)
    first = route_items(world.worklist(code))
    assert set(first) == {f"{ROUTED}#route_emptied", f"{ROUTED}#realization_uncovered"}
    assert first[f"{ROUTED}#route_emptied"]["facts"]["recordSatisfiesRoutes"] is False
    # Review R2-N1: the routes already name the target and a rerouted row exists, but K_C still
    # records the entries at the old files. As recorded, the record breaks MIK-R04 (its entries lie
    # under no route), so nothing is answered yet -- judging only the locations at C would pass.
    members = ["INV-R1R1R1", "INV-R2R2R2", "INV-R3R3R3"]
    world.memory_commit(
        {
            **families(routed=["svc/application", "svc/landing"]),
            **history(family_row(ROUTED, "rerouted", members)),
        },
        code,
    )
    early = route_items(world.worklist(code))
    assert set(early) == set(first)
    assert {item["facts"]["recordSatisfiesRoutes"] for item in early.values()} == {False}
    assert {item["satisfiedBy"] for item in early.values()} == {None}
    assert_predicate_agrees(world, early)
    world.memory_commit({**families(), f"knowledge/history/{OWNER}.json": None}, code)
    # One entry moved, then both: the IDs never follow the curator's intermediate states.
    one = {"RLZ-R00001": moved(landing)["RLZ-R00001"]}
    world.memory_commit({**world.sidecars(code, one), f"onboarding/{LEDGER}.json": None}, code)
    halfway = route_items(world.worklist(code))
    world.memory_commit(relocate(world, code, landing), code)
    found = route_items(world.worklist(code))
    assert set(found) == {f"{ROUTED}#route_emptied", f"{ROUTED}#realization_uncovered"}
    for subject, item in found.items():
        assert first[subject]["id"] == halfway[subject]["id"] == item["id"], subject
    emptied = found[f"{ROUTED}#route_emptied"]
    uncovered = found[f"{ROUTED}#realization_uncovered"]
    assert emptied["facts"]["affected"]["base"] == ["svc/worktrees"]
    assert {one["entry"] for one in uncovered["facts"]["affected"]["base"]} == set(landing)
    assert emptied["facts"]["renameCandidates"] == ["svc/landing"]
    assert emptied["facts"]["suggestion"]["label"] == "mechanical"
    assert emptied["facts"]["suggestion"]["routes"] == ["svc/application", "svc/landing"]
    assert emptied["facts"]["recordSatisfiesRoutes"] is False
    assert emptied["id"] != uncovered["id"]  # two conditions of one family: two items
    assert {emptied["satisfiedBy"], uncovered["satisfiedBy"]} == {None}
    assert_predicate_agrees(world, found)

    # The curator reroutes the family and records a rerouted row: the items stay (the base view),
    # keep their IDs, and are satisfied.
    members = ["INV-R1R1R1", "INV-R2R2R2", "INV-R3R3R3"]
    world.memory_commit(
        {
            **families(routed=["svc/application", "svc/landing"]),
            **history(family_row(ROUTED, "rerouted", members)),
        },
        code,
    )
    after = route_items(world.worklist(code))
    assert set(after) == set(found)
    assert {item["id"] for item in after.values()} == {item["id"] for item in found.values()}
    assert {item["satisfiedBy"] for item in after.values()} == {f"ROW-{ROUTED[4:]}"}
    assert all(item["facts"]["recordSatisfiesRoutes"] for item in after.values())
    assert after[f"{ROUTED}#route_emptied"]["facts"]["affected"]["candidate"] is None
    assert_predicate_agrees(world, after)


def test_a_no_impact_row_never_satisfies_and_a_kept_dead_route_keeps_the_item_open(
    world: World,
) -> None:
    code = move_out_of_worktrees(world)
    landing = {"RLZ-R00001": "svc/landing/ledger.py", "RLZ-R00002": "svc/landing/integrate.py"}
    members = ["INV-R1R1R1", "INV-R2R2R2", "INV-R3R3R3"]
    # Routes fixed, but the row says no_impact: refused.
    world.memory_commit(
        {
            **relocate(world, code, landing),
            **families(routed=["svc/application", "svc/landing"]),
            **history(family_row(ROUTED, "no_impact", members)),
        },
        code,
    )
    found = route_items(world.worklist(code))
    assert found and {item["satisfiedBy"] for item in found.values()} == {None}
    assert all(item["facts"]["recordSatisfiesRoutes"] for item in found.values())
    assert_predicate_agrees(world, found)
    # Non-conforming: the family keeps svc/worktrees after its last entry left. A rerouted row does
    # not close it: the K_C record violates MIK-R04.
    world.memory_commit(
        {
            **families(routed=["svc/application", "svc/landing", "svc/worktrees"]),
            **history(family_row(ROUTED, "rerouted", members)),
        },
        code,
    )
    kept = route_items(world.worklist(code))
    emptied = kept[f"{ROUTED}#route_emptied"]
    assert emptied["facts"]["affected"]["candidate"] == ["svc/worktrees"]
    assert emptied["facts"]["recordSatisfiesRoutes"] is False
    assert {item["satisfiedBy"] for item in kept.values()} == {None}
    assert_predicate_agrees(world, kept)


# --------------------------------------------------------------------------------------------------
# route_path_absent: the carried dead route (L04 ruling 1), retired families
# --------------------------------------------------------------------------------------------------


def test_a_carried_dead_route_the_validator_only_reports_is_a_mandatory_item(world: World) -> None:
    """L04 ruling 1: ``R04.1-carried-route-absent`` is report-only; here it becomes an item.

    ``svc/legacy`` is deleted. No realization entry lies there, so the worklist reaches no family
    through its entries -- the item is still raised, and only a family row (not no_impact) with a
    record that satisfies MIK-R04 answers it. The retired family on the same route raises nothing.
    """

    code = world.code_commit({OLD: None})
    world.memory_commit({}, code)
    report = validate_tree(
        knowledge_tree_from_git(world.memory, "HEAD", label="K_C"),
        bases=[knowledge_tree_from_git(world.memory, world.memory_base, label="K_B")],
        code=code_tree_from_git(world.code, code, label="C"),
    )
    assert report.ok, report.render()
    carried = [one for one in report.reports if one.rule == "R04.1-carried-route-absent"]
    assert [UNREALIZED in one.message for one in carried] == [True]

    document = world.worklist(code)
    found = route_items(document)
    # Non-empty is waived for an unrealized family: no route_emptied beside the absent path.
    assert set(found) == {f"{UNREALIZED}#route_path_absent"}
    absent = found[f"{UNREALIZED}#route_path_absent"]
    assert absent["facts"]["affected"] == {"base": ["svc/legacy"], "candidate": ["svc/legacy"]}
    assert absent["satisfiedBy"] is None
    assert not any(subject.startswith(RETIRED) for subject in found)  # retired: nothing
    assert UNREALIZED not in document["scope"]["reachedFamilies"]

    # A changed row alone does not answer it while the dead route stays in the record.
    world.memory_commit(history(family_row(UNREALIZED, "changed", ["INV-W1W1W1"])), code)
    still = route_items(world.worklist(code))[f"{UNREALIZED}#route_path_absent"]
    assert still["satisfiedBy"] is None and still["facts"]["recordSatisfiesRoutes"] is False
    # Rerouted to an existing directory: the unrealized family's Non-empty is waived, so it holds.
    world.memory_commit(
        {
            **families(unrealized=["svc/other"]),
            **history(family_row(UNREALIZED, "rerouted", ["INV-W1W1W1"])),
        },
        code,
    )
    fixed = route_items(world.worklist(code))
    assert fixed[f"{UNREALIZED}#route_path_absent"]["satisfiedBy"] == f"ROW-{UNREALIZED[4:]}"
    assert_predicate_agrees(world, fixed)


def test_a_reached_retired_family_raises_nothing(world: World) -> None:
    code = world.code_commit({LEDGER: None, INTEGRATE: None, KEEP: None})
    world.memory_commit(
        {
            f"knowledge/families/{ROUTED}-group.json": family(
                ROUTED,
                ["INV-R1R1R1", "INV-R2R2R2", "INV-R3R3R3"],
                ["svc/application", "svc/worktrees"],
                status="retired",
            )
        },
        code,
    )
    document = world.worklist(code)
    assert ROUTED in document["scope"]["reachedFamilies"]
    assert not any(subject.startswith(ROUTED) for subject in route_items(document))


# --------------------------------------------------------------------------------------------------
# route_unassigned, the boundary example, ambiguity, the registry
# --------------------------------------------------------------------------------------------------


def test_a_reached_family_without_routes_is_route_unassigned_until_an_assigned_row(
    world: World,
) -> None:
    code = world.code_commit({MISC: "def misc():\n    return 55\n"})
    world.memory_commit({}, code)
    found = route_items(world.worklist(code))
    assert set(found) == {f"{UNASSIGNED}#route_unassigned"}  # Coverage waived: no uncovered item
    item = found[f"{UNASSIGNED}#route_unassigned"]
    assert item["facts"]["suggestion"]["routes"] == ["svc/other"]
    # Ruling Q3: the legacy-unassessed waiver does not answer it; the leaf assigns routes.
    assert item["facts"]["recordSatisfiesRoutes"] is False and item["satisfiedBy"] is None
    world.memory_commit(history(family_row(UNASSIGNED, "changed", ["INV-X1X1X1"])), code)
    changed = route_items(world.worklist(code))[f"{UNASSIGNED}#route_unassigned"]
    assert changed["satisfiedBy"] is None  # a changed row with routes: [] is not enough
    assert changed["facts"]["recordSatisfiesRoutes"] is False
    world.memory_commit(
        {
            **families(unassigned=["svc/other"]),
            **history(family_row(UNASSIGNED, "assigned", ["INV-X1X1X1"])),
        },
        code,
    )
    after = route_items(world.worklist(code))
    assert after[f"{UNASSIGNED}#route_unassigned"]["satisfiedBy"] == f"ROW-{UNASSIGNED[4:]}"
    assert after[f"{UNASSIGNED}#route_unassigned"]["id"] == item["id"]
    assert_predicate_agrees(world, after)


def test_an_entry_moving_to_another_file_inside_its_route_raises_no_route_item(
    world: World,
) -> None:
    git(world.code, "mv", LEDGER, "svc/worktrees/ledger_moved.py")
    code = world.code_commit({})
    world.memory_commit(
        relocate(world, code, {"RLZ-R00001": "svc/worktrees/ledger_moved.py"}), code
    )
    document = world.worklist(code)
    assert route_items(document) == {}
    kinds = {(item["kind"], item["subject"]) for item in document["items"]}
    assert ("touched_invariant", "INV-R1R1R1") in kinds  # the invariant's own row is still due


def test_an_ambiguous_rename_target_lists_every_candidate_and_suggests_nothing(
    world: World,
) -> None:
    (world.code / "svc/landing").mkdir(parents=True)
    (world.code / "svc/docking").mkdir(parents=True)
    git(world.code, "mv", LEDGER, "svc/landing/ledger.py")
    git(world.code, "mv", INTEGRATE, "svc/docking/integrate.py")
    code = world.code_commit({})
    world.memory_commit(
        relocate(
            world,
            code,
            {"RLZ-R00001": "svc/landing/ledger.py", "RLZ-R00002": "svc/docking/integrate.py"},
        ),
        code,
    )
    document = world.worklist(code)
    emptied = route_items(document)[f"{ROUTED}#route_emptied"]
    assert emptied["facts"]["renameCandidates"] == ["svc/docking", "svc/landing"]
    assert emptied["facts"]["suggestion"] is None
    # The curator checklist names the condition, every candidate, and what answers it.
    [line] = [one for one in knowledge_worklist_lines(document, None) if "#route_emptied" in one]
    assert "route_emptied: svc/worktrees" in line
    assert "renamed to svc/docking, svc/landing" in line and "no suggestion" in line
    assert "never no_impact" in line


def test_the_kind_is_registered_and_its_row_is_the_familys_row() -> None:
    kind = ITEM_KINDS[KIND]
    assert kind.owner == "MIK-R06"
    assert kind.accepts_subject(f"{ROUTED}#route_path_absent")
    assert not kind.accepts_subject(ROUTED) and not kind.accepts_subject(f"{ROUTED}#other")
    rows = HistoryFile.model_validate(
        {
            "schema": "ar-history/v1",
            "leaf": OWNER,
            "closed": False,
            "rows": [family_row(ROUTED, "no_impact", ["INV-R1R1R1"])],
        }
    )
    row = satisfying_row(KIND, f"{ROUTED}#route_emptied", rows)
    assert row is not None and row.subject == ROUTED
    item = {"subject": f"{ROUTED}#route_emptied", "facts": {"recordSatisfiesRoutes": True}}
    assert family_route_item_open(item, rows)  # no_impact never satisfies
    assert family_route_item_open(item, None)
    assert family_route_item_open({"subject": item["subject"]}, rows)  # no facts: open


# --------------------------------------------------------------------------------------------------
# Rulings round: Q1 (a route dead at B is not this leaf's) and Q5 (rename-mapped suggestion)
# --------------------------------------------------------------------------------------------------


def test_an_unreached_familys_route_already_dead_at_b_is_not_charged_to_the_leaf(
    world: World,
) -> None:
    """Ruling Q1: an unreached family is charged only for a route this leaf's range killed."""

    dead_at_b = world.code_commit({OLD: None})
    candidate = world.code_commit({KEEP: "def keep():\n    return 1\n"})
    world.memory_commit({}, candidate)
    document = world.worklist(candidate, base=dead_at_b)
    assert not any(subject.startswith(UNREALIZED) for subject in route_items(document))
    # The same dead route, killed inside the range, is raised (the carried-route test above).
    killed = world.worklist(candidate)
    assert f"{UNREALIZED}#route_path_absent" in route_items(killed)


def test_the_suggestion_follows_renames_of_files_absent_at_c_and_is_withheld_without_one(
    world: World,
) -> None:
    """Ruling Q5: entries still recorded at paths absent at C count where their files went."""

    (world.code / "svc/landing").mkdir(parents=True)
    for one in (LEDGER, INTEGRATE, KEEP):
        git(world.code, "mv", one, one.replace("svc/worktrees", "svc/landing"))
    renamed = world.code_commit({})
    absent = route_items(world.worklist(renamed))[f"{ROUTED}#route_path_absent"]
    assert absent["facts"]["renameCandidates"] == ["svc/landing"]
    assert absent["facts"]["suggestion"]["routes"] == ["svc/application", "svc/landing"]
    assert absent["facts"]["unmappedLocations"] == []

    deleted = world.code_commit(
        {one.replace("svc/worktrees", "svc/landing"): None for one in (LEDGER, INTEGRATE, KEEP)},
        "delete",
    )
    gone = route_items(world.worklist(deleted))[f"{ROUTED}#route_path_absent"]
    assert gone["facts"]["suggestion"] is None
    assert gone["facts"]["unmappedLocations"] == [INTEGRATE, LEDGER]
