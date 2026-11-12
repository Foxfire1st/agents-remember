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
from collections.abc import Iterable
from types import SimpleNamespace

import pytest
from agents_remember.application.knowledge_currentness.observe import CodeTree, open_code_tree
from agents_remember.application.knowledge_gate.predicates import GateContext, item_open_reason
from agents_remember.application.knowledge_worklist import generated_copies
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.memory_quality.knowledge_validator.trees import knowledge_tree_from_git
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.reconsideration import link_target_key
from agents_remember.worktrees.modules.onboarding_trace import (
    OnboardingTraceSides,
    onboarding_trace_result,
)
from test_knowledge_worklist import (
    LINES,
    OTHER,
    REVIEW,
    REVIEW_V1,
    TESTS,
    TESTS_V1,
    family,
    git,
    invariant,
)
from test_reconsideration_surfacing import (
    D18,
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


def test_route_chain_is_candidate_context_not_item_identity(world: World) -> None:
    candidate = world.code_commit(
        {REVIEW: REVIEW_V1.replace("return path not in LISTED", "return False")}
    )
    before = world.worklist(candidate)
    assert before["state"] == "complete", before.get("incomplete")
    touched = next(item for item in before["items"] if item["subject"] == "INV-AAAAAA")
    chain = touched["facts"]["context"]["routeChain"][REVIEW]
    assert chain == {
        "label": "mechanical",
        "families": [
            {"id": "FAM-F00001", "title": "FAM-F00001", "routes": ["pkg"]},
            {"id": "FAM-F00002", "title": "FAM-F00002", "routes": ["pkg"]},
        ],
    }
    path = "knowledge/families/FAM-F00001-group.json"
    changed = json.loads((world.memory / path).read_text())
    changed.update(title="Renamed family", revision=2)
    world.memory_commit({path: canonical_text(changed)}, candidate)
    after = world.worklist(candidate)
    revised = next(item for item in after["items"] if item["subject"] == touched["subject"])
    assert revised["id"] == touched["id"]
    assert (
        revised["facts"]["context"]["routeChain"][REVIEW]["families"][0]["title"]
        == "Renamed family"
    )


def test_ten_equal_uncovered_changes_share_the_card_representative(world: World) -> None:
    paths = [f"copies/copy-{n}.txt" for n in range(10)]
    representative = paths[-1]
    world.code_base = world.code_commit(dict.fromkeys(paths, "old\n"))
    world.memory_base = world.memory_commit(
        {f"onboarding/{representative}.md": "The canonical card.\n"}, world.code_base
    )
    candidate = world.code_commit(dict.fromkeys(paths, "new\n"))
    document = world.worklist(candidate)
    assert document["state"] == "complete", document.get("incomplete")
    unexplained = [item for item in document["items"] if item["kind"] == "unexplained_hunk"]
    assert len(unexplained) == 1
    item = unexplained[0]
    assert item["facts"]["path"] == representative
    blobs = {path: git(world.code, "rev-parse", f"{candidate}:{path}") for path in paths}
    assert item["facts"]["sourceBlobs"] == blobs
    assert set(item["facts"]["context"]["routeChain"]) == set(paths)
    assert item["facts"]["onboardingTrace"]["sourceBlobs"] == blobs
    row = {
        "id": "ROW-ABCDEF",
        "subject": f"onboarding:{representative}",
        "disposition": "no_impact",
        "reason": "All copies were examined.",
        "items": [],
        "sources": [{"path": path, "blob": blob} for path, blob in blobs.items()],
    }
    world.memory_commit({f"knowledge/history/{LEAF}.json": _history(row)}, candidate)
    answered = world.worklist(candidate)
    assert answered["state"] == "complete", answered.get("incomplete")
    answered_item = next(item for item in answered["items"] if item["kind"] == "unexplained_hunk")
    assert answered_item["satisfiedBy"] == row["id"]
    assert item_open_reason(answered_item, _gate_context(world, candidate)) is None
    later = world.code_commit({paths[0]: "later\n"})
    later_document = world.worklist(later)
    later_item = next(one for one in later_document["items"] if one["subject"] == item["subject"])
    assert item_open_reason(later_item, _gate_context(world, later)) is not None
    card = f"onboarding/{representative}.md"
    sides = OnboardingTraceSides(
        owner=LEAF,
        base={card: b"The canonical card.\n"},
        candidate={
            card: b"The canonical card.\n",
            f"knowledge/history/{LEAF}.json": _history(row).encode(),
        },
    )
    context = SimpleNamespace(storage=StorageSettings(), code_repository_name="fixture")
    trace = onboarding_trace_result(
        context, paths, sides, code_blobs=blobs, source_groups=document["identicalChanges"]
    )
    assert len(trace.items) == 1
    assert trace.items[0].sources == tuple(paths)
    assert trace.items[0].blobs == blobs
    assert trace.ok


@pytest.mark.parametrize("case", ["different_base", "two_cards", "covered"])
def test_equal_candidate_blobs_do_not_group_distinct_judgments(world: World, case: str) -> None:
    paths = ["pkg/first.txt", "pkg/second.txt"]
    world.code_base = world.code_commit(
        {paths[0]: "old\n", paths[1]: "other\n" if case == "different_base" else "old\n"}
    )
    memory: dict[str, str | bytes | None] = {}
    if case == "two_cards":
        memory = {f"onboarding/{path}.md": "An independent card.\n" for path in paths}
    if case == "covered":
        memory = world.sidecars(
            world.code_base,
            {
                "RLZ-A11111": (
                    "INV-AAAAAA",
                    paths[0],
                    {"kind": "line_range", "start": 1, "end": 1},
                    "realizes",
                ),
                "RLZ-A22222": (
                    "INV-AAAAAA",
                    paths[1],
                    {"kind": "line_range", "start": 1, "end": 1},
                    "realizes",
                ),
            },
        )
    world.memory_base = world.memory_commit(memory, world.code_base)
    candidate = world.code_commit(dict.fromkeys(paths, "new\n"))
    document = world.worklist(candidate)
    assert document["state"] == "complete", document.get("incomplete")
    assert document["identicalChanges"] == {}


def test_a_deleted_route_triggers_without_a_history_row(world: World) -> None:
    world.code_base = world.code_commit({"gone/last.txt": "one\n"})
    routed = decision(
        ROUTED,
        _alternatives(
            ("Local", "chosen", None), ("Global", "rejected", "The directory disappears.")
        ),
        [{"relation": "reconsider_on", "target": "route:gone", "alternative": 1}],
    )
    world.memory_base = world.memory_commit(
        {f"knowledge/decisions/{ROUTED}-routes.json": routed}, world.code_base
    )
    candidate = world.code_commit({"gone/last.txt": None})
    found = _candidates(world.worklist(candidate))
    assert found[f"reconsider:{ROUTED}#1"]["facts"]["changed"][0]["trigger"] == "route_absent"


def test_a_family_reroute_inside_the_linked_directory_triggers(world: World) -> None:
    routed = decision(
        ROUTED,
        _alternatives(("Local", "chosen", None), ("Global", "rejected", "Nested routes move.")),
        [{"relation": "reconsider_on", "target": "route:pkg", "alternative": 1}],
    )
    path = "knowledge/families/FAM-F00002-group.json"
    record = json.loads((world.memory / path).read_text())
    record["routes"] = ["pkg/nested"]
    world.memory_base = world.memory_commit(
        {path: canonical_text(record), f"knowledge/decisions/{ROUTED}-routes.json": routed},
        world.code_base,
    )
    record.update(routes=["tests"], revision=2)
    row = {
        "id": "ROW-ABCDEF",
        "subject": "FAM-F00002",
        "disposition": "rerouted",
        "reason": "The nested route moved.",
        "items": [],
        "examined": [{"id": "INV-CCCCCC", "revision": 1}],
    }
    world.memory_commit(
        {path: canonical_text(record), f"knowledge/history/{LEAF}.json": _history(row)},
        world.code_base,
    )
    found = _candidates(world.worklist(world.code_base))
    changed = found[f"reconsider:{ROUTED}#1"]["facts"]["changed"][0]
    assert changed["trigger"] == "history_row"
    assert changed["rowSubject"] == row["subject"]


def _gate_context(world: World, candidate: str) -> GateContext:
    side = KnowledgeSide.from_tree(
        "K_C", "candidate", knowledge_tree_from_git(world.memory, "HEAD", label="K_C")
    )
    return GateContext(
        owner=LEAF,
        history=side.history(LEAF),
        candidate=side,
        code=open_code_tree(CodeTree(world.code, candidate)),
    )


def test_a_noncurrent_refresh_stays_open_despite_a_row_until_restored(world: World) -> None:
    path = f"knowledge/decisions/{D18}-text-files.json"
    base = json.loads((world.memory / path).read_text())
    changed_code = world.code_commit({REVIEW: REVIEW_V1.replace("value * 2", "value * 3")})
    refreshed = json.loads(canonical_text(base))
    refreshed["revision"] = 2
    refreshed["links"][2]["target"] = {
        **world.anchor(changed_code, REVIEW, {"kind": "symbol", "name": "third"}),
        "path": REVIEW,
    }
    row = {
        "id": "ROW-ABCDEF",
        "subject": f"reconsider:{D18}#2",
        "disposition": "still_rejected",
        "reason": "The link was refreshed.",
        "items": [],
    }
    world.memory_commit(
        {path: canonical_text(refreshed), f"knowledge/history/{LEAF}.json": _history(row)},
        changed_code,
    )
    reverted = world.code_commit({REVIEW: REVIEW_V1})
    document = world.worklist(reverted)
    item = _candidates(document)[row["subject"]]
    assert item["facts"]["changed"][0]["trigger"] == "refresh_not_current"
    assert item["satisfiedBy"] is None
    assert item_open_reason(item, _gate_context(world, reverted)) is not None
    # Current at C closes it; a stored candidate is checked against the actual current link.
    current = world.code_commit({REVIEW: REVIEW_V1.replace("value * 2", "value * 3")})
    assert item_open_reason(item, _gate_context(world, current)) is None
    restored = json.loads(canonical_text(refreshed))
    restored["links"] = base["links"]
    world.memory_commit({path: canonical_text(restored)}, reverted)
    assert row["subject"] not in _candidates(world.worklist(reverted))
    assert item_open_reason(item, _gate_context(world, reverted)) is None


def test_generated_copy_interface_is_collected_once_and_only_divergence_raises(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ["copies/equal.txt", "copies/manual.txt", "copies/binary.bin"]
    candidate = world.code_commit(
        {paths[0]: "equal\n", paths[1]: "manual\n", paths[2]: b"\x00payload"}
    )
    calls = []

    def facts(code: object, changed: Iterable[str]) -> dict[str, dict[str, str | bool]]:
        calls.append(sorted(changed))
        return {
            path: {"canonicalPath": "canonical/rule.txt", "matches": path != paths[1]}
            for path in paths
        }

    monkeypatch.setattr(generated_copies, "generated_copy_facts", facts)
    document = world.worklist(candidate)
    assert document["state"] == "complete", document.get("incomplete")
    raised = [
        item
        for item in document["items"]
        if item["kind"] in {"unexplained_hunk", "unexplained_file"}
    ]
    assert len(raised) == 1
    assert raised[0]["facts"]["path"] == paths[1]
    assert raised[0]["facts"]["generatedCopy"] == {
        "canonicalPath": "canonical/rule.txt",
        "matches": False,
    }
    assert calls == [sorted(paths)]


@pytest.mark.parametrize("change", ["delete", "rename", "symbol", "route"])
def test_removed_link_targets_hold_until_the_record_removes_or_restates_them(
    world: World, change: str
) -> None:
    record_id = "DEC-TGT001"
    path = f"knowledge/decisions/{record_id}-links.json"
    edits: dict[str, str | bytes | None]
    if change == "route":
        world.code_base = world.code_commit({"gone/last.txt": "last\n"})
        target = "route:gone"
        edits = {"gone/last.txt": None}
    else:
        target = {
            **world.anchor(world.code_base, REVIEW, {"kind": "symbol", "name": "third"}),
            "path": REVIEW,
        }
        edits = {REVIEW: REVIEW_V1.replace("def third(value):\n    return value * 2\n", "")}
        if change in {"delete", "rename"}:
            edits = {REVIEW: None}
        if change == "rename":
            edits["pkg/renamed.py"] = REVIEW_V1
    authored = json.loads(
        decision(
            record_id,
            _alternatives(("Keep", "chosen", None)),
            [{"relation": "explains", "target": target}],
        )
    )
    world.memory_base = world.memory_commit({path: canonical_text(authored)}, world.code_base)
    candidate = world.code_commit(edits)
    document = world.worklist(candidate)
    assert document["state"] == "complete", document.get("incomplete")
    subject = f"link:{record_id}#{link_target_key(target)}"
    item = next(
        item
        for item in document["items"]
        if item["kind"] == "link_target" and item["subject"] == subject
    )
    assert item["satisfiedBy"] is None
    assert item_open_reason(item, _gate_context(world, candidate)) is not None
    # A row about the owner, and even a record revision, answer no link_target item.
    authored["revision"] = 2
    row = {
        "id": "ROW-ABCDEF",
        "subject": record_id,
        "disposition": "changed",
        "revision": 2,
        "effect": "clarify",
        "reason": "The record was read, but its link is unchanged.",
        "items": [],
    }
    world.memory_commit(
        {path: canonical_text(authored), f"knowledge/history/{LEAF}.json": _history(row)}, candidate
    )
    assert item_open_reason(item, _gate_context(world, candidate)) is not None
    if change == "rename":
        authored["links"][-1]["target"] = {
            **world.anchor(candidate, "pkg/renamed.py", {"kind": "symbol", "name": "third"}),
            "path": "pkg/renamed.py",
        }
    else:
        authored["links"].pop()
    world.memory_commit({path: canonical_text(authored)}, candidate)
    assert item_open_reason(item, _gate_context(world, candidate)) is None
    assert not any(one["subject"] == subject for one in world.worklist(candidate)["items"])


def test_an_unavailable_recorded_line_blob_is_named_in_item_facts(world: World) -> None:
    sidecar = world.sidecar_of(LINES)
    missing = "f" * 40
    sidecar["realizes"][0]["anchor"]["blob"] = missing
    world.memory_base = world.memory_commit(
        {f"onboarding/{LINES}.json": canonical_text(sidecar)}, world.code_base
    )
    document = world.worklist(world.code_base, maintenance_scope=True)
    assert document["state"] == "complete", document.get("incomplete")
    item = next(
        item
        for item in document["items"]
        if item["kind"] == "stale_invariant" and item["subject"] == "INV-EEEEEE"
    )
    assert (
        item["facts"]["entries"][0]["reason"]
        == f"the blob {missing} the line range was recorded against is unavailable"
    )


def test_a_root_overview_file_with_a_card_is_refused_by_name() -> None:
    context = SimpleNamespace(storage=StorageSettings(), code_repository_name="fixture")
    card = {"onboarding/overview.md": b"The root card and overview collide.\n"}
    result = onboarding_trace_result(
        context,
        ["overview"],
        OnboardingTraceSides(owner=LEAF, base=card, candidate=card),
        code_blobs={"overview": "a" * 40},
    )
    assert result.problems
    assert result.problems[0][1] == "overview"
    assert "root route" in result.problems[0][2]
    assert not result.items


def test_a_family_absent_at_base_has_no_route_condition_item(world: World) -> None:
    world.code_base = world.code_commit({"gone/last.txt": "last\n"})
    candidate = world.code_commit({"gone/last.txt": None})
    record = json.loads(family("FAM-NW0001", ["INV-AAAAAA"]))
    record["routes"] = ["gone"]
    world.memory_commit(
        {"knowledge/families/FAM-NW0001-new.json": canonical_text(record)}, candidate
    )
    document = world.worklist(candidate)
    assert document["state"] == "complete", document.get("incomplete")
    assert not any(
        item["kind"] == "family_route_condition" and item["facts"]["family"] == record["id"]
        for item in document["items"]
    )


@pytest.mark.parametrize("disposition", ["moved", "extended"])
def test_changed_invariant_governing_sentence_precedes_missing_covers(
    world: World, disposition: str
) -> None:
    candidate = world.code_commit(
        {
            REVIEW: REVIEW_V1.replace("return path not in LISTED", "return False"),
            TESTS: TESTS_V1.replace('assert _not_listed("b")', 'assert _not_listed("c")'),
        }
    )
    locator = {"kind": "symbol", "name": "_not_listed"}
    after = {**world.anchor(candidate, REVIEW, locator), "path": REVIEW}
    before = {**world.anchor(world.code_base, REVIEW, locator), "path": REVIEW}
    memory: dict[str, str | bytes | None] = {
        "knowledge/invariants/INV-AAAAAA-rule.json": invariant(
            "INV-AAAAAA", 2, "A changed guarantee."
        )
    }
    covers = [{"id": "RLZ-A00001", "before": before, "after": after}]
    if disposition == "extended":
        memory.update(
            world.sidecars(
                candidate,
                {
                    "RLZ-A99999": (
                        "INV-AAAAAA",
                        OTHER,
                        {"kind": "symbol", "name": "helper"},
                        "realizes",
                    )
                },
            )
        )
        covers = [
            {
                "id": "RLZ-A99999",
                "before": "absent",
                "after": {
                    **world.anchor(candidate, OTHER, {"kind": "symbol", "name": "helper"}),
                    "path": OTHER,
                },
            }
        ]
    row = {
        "id": "ROW-ABCDEF",
        "subject": "INV-AAAAAA",
        "disposition": disposition,
        "reason": "A later structural row cannot hide the changed guarantee.",
        "revision": 2,
        "covers": covers,
        "items": [],
    }
    memory[f"knowledge/history/{LEAF}.json"] = _history(row)
    world.memory_commit(memory, candidate)
    document = world.worklist(candidate)
    assert document["state"] == "complete", document.get("incomplete")
    item = next(
        one
        for one in document["items"]
        if one["kind"] == "touched_invariant" and one["subject"] == "INV-AAAAAA"
    )
    reason = item_open_reason(item, _gate_context(world, candidate))
    assert reason is not None
    assert reason.startswith("row ROW-ABCDEF:")
    assert "governs again" in reason
    assert "does not cover" in reason
    assert reason.index("governs again") < reason.index("does not cover")


def test_route_context_includes_the_named_rename_destination(world: World) -> None:
    destination = "tests/renamed.py"
    candidate = world.code_commit({REVIEW: None, destination: REVIEW_V1})
    document = world.worklist(candidate)
    assert document["state"] == "complete", document.get("incomplete")
    item = next(
        one
        for one in document["items"]
        if one["kind"] == "touched_invariant" and one["subject"] == "INV-AAAAAA"
    )
    assert item["facts"]["context"]["routeChain"][destination] == {
        "label": "mechanical",
        "families": [],
    }
