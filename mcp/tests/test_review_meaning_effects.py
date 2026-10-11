"""MIK-R43: the reviewer shows the recorded effect of a meaning change.

Backend cases through ``compose_review`` over converted trees, each failing without the
change (the projection, the payload field and the outline labels do not exist there):
attempts 1, 2 and 10 in attempt order; another owner's row left out; the ``retire``
step; a selected invariant without a family; the two L37 sequences from the real
history files; a family whose own revision differs from every member's; an old family
row; a half-recorded family row, an incomplete index and an unreadable side as
``unavailable``; continuation members; and the outline's labels for a read and for an
unread family, equal to the review answer's steps. A vocabulary case pins the nine
explanations to ``ADMITTED_EFFECT_LABELS`` on both sides of the wire.
"""

from __future__ import annotations

import json as _json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_review import (
    compose_review,
    list_knowledge_review_entries,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_meaning_effects import (
    read_record_meaning,
    review_meaning_effects,
)
from agents_remember.memory.knowledge_index import KnowledgeIndex, text_uuid
from agents_remember.memory.knowledge_index.build import build_index
from agents_remember.memory.knowledge_index.tree import directory_snapshot
from agents_remember.models.knowledge.effect import ADMITTED_EFFECT_LABELS
from agents_remember.models.knowledge.read import FamilyIdentitySeed, InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge.review_meaning_effects import (
    EFFECT_EXPLANATIONS,
    NONE_TEXT,
)
from knowledge_index_test_support import (
    commit_all,
    init_repository,
    write_document,
)
from test_review_git_trees import (
    FAMILY,
    INVARIANT,
    LEAF,
    MASTER,
    REPO,
    World,
    _write,
    build_world,
    invariant,
)

pytestmark = pytest.mark.evidence_unit

OTHER_LEAF = "260101-TRV-L9"

# A second invariant with no family: the moved row's subject, and the familyless
# selection. History files hold one row per subject, so the moved row cannot share
# the first invariant's file entry.
OTHER_INVARIANT = "INV-CCCCCC"

# The reviewed leaf's three attempt files: plain (1), -attempt-2 and -attempt-10, plus
# another owner's file about the same records. Row ids are minted-shaped ROW- ids.
ATTEMPT_1_ROWS = [
    {
        "id": "ROW-A11111",
        "subject": INVARIANT,
        "disposition": "changed",
        "reason": "The statement was relaxed to cover derived values.",
        "items": [],
        "covers": [],
        "revision": 2,
        "effect": "weaken",
    },
    {
        "id": "ROW-A11112",
        "subject": FAMILY,
        "disposition": "changed",
        "reason": "The guarantee was restated without changing its scope.",
        "items": [],
        "examined": [{"id": INVARIANT, "revision": 2}],
        "revision": 2,
        "effect": "clarify",
    },
    {
        "id": "ROW-A11113",
        "subject": OTHER_INVARIANT,
        "disposition": "moved",
        "reason": "The entry was re-anchored without changing the statement.",
        "items": [],
        "covers": [
            {
                "id": "RLZ-000001",
                "before": {
                    "path": "pkg/a.py",
                    "locator": {"kind": "symbol", "name": "land"},
                    "blob": "0000000000000000000000000000000000000000",
                    "content": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                },
                "after": {
                    "path": "pkg/a.py",
                    "locator": {"kind": "symbol", "name": "keep"},
                    "blob": "0000000000000000000000000000000000000000",
                    "content": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                },
            }
        ],
        "revision": 2,
    },
]
ATTEMPT_2_ROWS = [
    {
        "id": "ROW-B11111",
        "subject": INVARIANT,
        "disposition": "changed",
        "reason": "Revision 3 tightens the wording back up.",
        "items": [],
        "covers": [],
        "revision": 3,
        "effect": "clarify",
    },
    {
        "id": "ROW-B11112",
        "subject": FAMILY,
        "disposition": "changed",
        "reason": "Examined all members. The guarantee is restated as a claim.",
        "items": [],
        "examined": [{"id": INVARIANT, "revision": 2}],
    },
]
ATTEMPT_10_ROWS = [
    {
        "id": "ROW-C11111",
        "subject": INVARIANT,
        "disposition": "changed",
        "reason": "Revision 4 restores the strict bound.",
        "items": [],
        "covers": [],
        "revision": 4,
        "effect": "strengthen",
    },
    {
        "id": "ROW-C11112",
        "subject": OTHER_INVARIANT,
        "disposition": "changed",
        "reason": "The second statement was relaxed.",
        "items": [],
        "covers": [],
        "revision": 2,
        "effect": "clarify",
    },
]
OTHER_OWNER_ROWS = [
    {
        "id": "ROW-X11111",
        "subject": INVARIANT,
        "disposition": "changed",
        "reason": "Another leaf's own judgment about the same record.",
        "items": [],
        "covers": [],
        "revision": 9,
        "effect": "replace",
    },
]

# A second invariant with no family (defined above, beside the row tables).


def _other_invariant_record() -> str:
    return _json.dumps(
        {
            "schema": "ar-invariant/v1",
            "id": OTHER_INVARIANT,
            "origin": {"task": "260101-OLD", "leaf": "260101-OLD-L1"},
            "revision": 1,
            "status": "accepted",
            "statement": "Second values land unchanged.",
            "applicability": "Always.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
        }
    )


def _history(children: list[dict[str, Any]], *, closed: bool = False) -> str:
    return _json.dumps(
        {"schema": "ar-history/v1", "leaf": LEAF, "closed": closed, "rows": children}
    )


def _history_for(owner: str, children: list[dict[str, Any]], *, closed: bool = False) -> str:
    return _json.dumps(
        {"schema": "ar-history/v1", "leaf": owner, "closed": closed, "rows": children}
    )


def _attempt_history(attempt: int, children: list[dict[str, Any]], *, closed: bool = False) -> str:
    return _json.dumps(
        {
            "schema": "ar-history/v1",
            "leaf": LEAF,
            "attempt": attempt,
            "closed": closed,
            "rows": children,
        }
    )


def _meaning_world(tmp_path: Path) -> World:
    """The converted world with three attempt files plus another owner's rows."""

    world = build_world(tmp_path)
    family_path = world.memory_worktree / f"knowledge/families/{FAMILY}-landing.json"
    # The family record sits at its own revision 6 while its members sit at 1: the
    # steps below still report each row's own judged revision, never the record's.
    family_path.write_text(
        family_path.read_text(encoding="utf-8").replace('"revision": 1', '"revision": 6'),
        encoding="utf-8",
    )
    _write(
        world.memory_worktree,
        {
            f"knowledge/invariants/{OTHER_INVARIANT}-second.json": _other_invariant_record(),
            f"knowledge/history/{LEAF}.json": _history(ATTEMPT_1_ROWS),
            f"knowledge/history/{LEAF}-attempt-2.json": _attempt_history(2, ATTEMPT_2_ROWS),
            f"knowledge/history/{LEAF}-attempt-10.json": _attempt_history(
                10, ATTEMPT_10_ROWS, closed=True
            ),
            f"knowledge/history/{OTHER_LEAF}.json": _history_for(OTHER_LEAF, OTHER_OWNER_ROWS),
        },
    )
    return world


def _resolve(world: World) -> ReviewCandidateResolution:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    return resolved


def _invariant_seed() -> InvariantIdentitySeed:
    return InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))


def _family_seed() -> FamilyIdentitySeed:
    return FamilyIdentitySeed(family_id=text_uuid("identity", FAMILY))


def _review(world: World, resolved: ReviewCandidateResolution, **fields: Any):
    request = ReviewSurfaceRequest(repository_id=REPO, master=MASTER, leaf_id=LEAF, **fields)
    result = compose_review(resolved, request)
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def test_attempts_read_in_attempt_order_not_row_or_revision_order(
    tmp_path: Path,
) -> None:
    """Attempts 1, 2 and 10 read as steps in attempt order: 10 comes after 2."""

    world = _meaning_world(tmp_path)
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    answer = payload.meaning_effects.for_record(_invariant_seed().invariant_id)
    assert answer is not None and answer.state == "steps"
    assert [(step.attempt, step.effect, step.revision) for step in answer.steps] == [
        (1, "weaken", 2),
        (2, "clarify", 3),
        (10, "strengthen", 4),
    ]
    assert [step.row_id for step in answer.steps] == ["ROW-A11111", "ROW-B11111", "ROW-C11111"]
    assert all(step.history_path for step in answer.steps)
    assert all(step.reason for step in answer.steps)


def test_another_owners_row_is_never_a_step(tmp_path: Path) -> None:
    """Another leaf's changed row about the same record is left out, not a failure."""

    world = _meaning_world(tmp_path)
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    answer = payload.meaning_effects.for_record(_invariant_seed().invariant_id)
    assert answer is not None and answer.state == "steps"
    assert "ROW-X11111" not in [step.row_id for step in answer.steps]
    assert "replace" not in [step.effect for step in answer.steps]


def test_moved_rows_carry_no_effect_and_are_not_steps(tmp_path: Path) -> None:
    """A ``moved`` row is no step; a familyless selected invariant still gets its block."""

    world = _meaning_world(tmp_path)
    seed = InvariantIdentitySeed(invariant_id=text_uuid("identity", OTHER_INVARIANT))
    payload = _review(world, _resolve(world), selector=seed)
    answer = payload.meaning_effects.for_record(seed.invariant_id)
    assert answer is not None and answer.state == "steps"
    assert [step.row_id for step in answer.steps] == ["ROW-C11112"]
    assert "ROW-A11113" not in [step.row_id for step in answer.steps]


def test_family_steps_carry_the_rows_own_revision(tmp_path: Path) -> None:
    """The family answer reports each row's own revision, with the old row unrecorded."""

    world = _meaning_world(tmp_path)
    payload = _review(world, _resolve(world), selector=_family_seed())
    answers = [entry for entry in payload.meaning_effects.records if entry.kind == "family"]
    assert len(answers) == 1
    answer = answers[0]
    assert answer.state == "steps"
    assert [(step.attempt, step.effect, step.revision) for step in answer.steps] == [
        (1, "clarify", 2),
        (2, None, None),
    ]
    old = answer.steps[1]
    assert old.row_id == "ROW-B11112"


def test_a_leaf_with_no_row_about_the_record_states_none(tmp_path: Path) -> None:
    """Readable complete history with no step of this leaf is ``none``, never a failure."""

    world = build_world(tmp_path)
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    answer = payload.meaning_effects.for_record(_invariant_seed().invariant_id)
    assert answer is not None
    assert answer.state == "none"
    assert answer.detail == NONE_TEXT
    assert answer.steps == ()


def test_a_deleted_row_with_retire_is_a_step(tmp_path: Path) -> None:
    """A ``deleted`` row with the effect ``retire`` is a recorded effect; without it, none."""

    root = tmp_path / "retire-tree"
    init_repository(root)
    write_document(
        root, "knowledge/layout.json", {"schema": "ar-memory-layout/v2", "conversion": "test"}
    )
    write_document(
        root,
        f"knowledge/history/{LEAF}.json",
        {
            "schema": "ar-history/v1",
            "leaf": LEAF,
            "closed": True,
            "rows": [
                {
                    "id": "ROW-D11111",
                    "subject": INVARIANT,
                    "disposition": "deleted",
                    "reason": "The obligation ends with this leaf.",
                    "items": [],
                    "covers": [],
                    "revision": 3,
                    "effect": "retire",
                },
                {
                    "id": "ROW-D11112",
                    "subject": OTHER_INVARIANT,
                    "disposition": "deleted",
                    "reason": "The record was removed.",
                    "items": [],
                    "covers": [
                        {
                            "id": "RLZ-000002",
                            "before": {
                                "path": "pkg/a.py",
                                "locator": {"kind": "symbol", "name": "land"},
                                "blob": "0000000000000000000000000000000000000000",
                                "content": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                            },
                            "after": "absent",
                        }
                    ],
                    "revision": 1,
                },
            ],
        },
    )
    commit_all(root)
    destination = tmp_path / "retire.sqlite"
    build_index(directory_snapshot(root), destination)
    with KnowledgeIndex(destination) as index:
        retired = read_record_meaning(index, LEAF, INVARIANT, "invariant")
        assert retired.state == "steps"
        assert [(step.attempt, step.revision, step.effect) for step in retired.steps] == [
            (1, 3, "retire")
        ]
        plain = read_record_meaning(index, LEAF, OTHER_INVARIANT, "invariant")
        assert plain.state == "none"
        assert plain.detail == NONE_TEXT


def test_a_half_recorded_family_row_makes_the_display_unavailable(
    tmp_path: Path,
) -> None:
    """A changed family row with only one of effect/revision is invalid (MIK-R48 rule 3).

    Validated writes refuse such a row, so the case is built by filing it into a copy
    of the after index directly: the projection still refuses to show a step for it.
    """

    world = _meaning_world(tmp_path)
    resolved = _resolve(world)
    copied = tmp_path / "half-recorded.sqlite"
    shutil.copyfile(resolved.candidate_database, copied)
    document = _json.dumps(
        {
            "id": "ROW-H11111",
            "subject": FAMILY,
            "disposition": "changed",
            "reason": "The guarantee was restated.",
            "items": [],
            "examined": [{"id": INVARIANT, "revision": 2}],
            "revision": 2,
        }
    )
    connection = sqlite3.connect(copied)
    try:
        connection.execute(
            "INSERT INTO ix_history_row (id, owner, owner_kind, closed, path, subject, "
            "disposition, document) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ROW-H11111",
                LEAF,
                "leaf",
                0,
                f"knowledge/history/{LEAF}.json",
                FAMILY,
                "changed",
                document,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    with KnowledgeIndex(copied) as index:
        answer = read_record_meaning(index, LEAF, FAMILY, "family")
    assert answer.state == "unavailable"
    assert "ROW-H11111" in answer.detail


def test_an_incomplete_index_is_unavailable_never_none(tmp_path: Path) -> None:
    """History an incomplete index did not read cannot be reported as absent."""

    world = _meaning_world(tmp_path)
    _write(world.memory_worktree, {"knowledge/invariants/INV-BBBBBB-broken.json": "{}\n"})
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    assert "knowledge-index:after:partial" in payload.limitations
    answer = payload.meaning_effects.for_record(_invariant_seed().invariant_id)
    assert answer is not None
    assert answer.state == "unavailable"
    assert "incomplete" in answer.detail


def test_an_unreadable_after_side_is_unavailable(tmp_path: Path) -> None:
    """An index file that does not open is ``unavailable`` with the reason."""

    missing = tmp_path / "no-such-index.sqlite"
    answer = review_meaning_effects(missing, LEAF, [(INVARIANT, "invariant")])
    assert len(answer.records) == 1
    assert answer.records[0].state == "unavailable"
    assert "cannot be read" in answer.records[0].detail


def test_a_legacy_comparison_is_unavailable(tmp_path: Path) -> None:
    """No converted after tree is ``unavailable``, never ``none``."""

    answer = review_meaning_effects(
        None, LEAF, [(INVARIANT, "invariant")], legacy_detail="legacy-unavailable"
    )
    assert answer.records[0].state == "unavailable"


def test_the_task_context_review_carries_an_empty_field(tmp_path: Path) -> None:
    """A current build without the field is invalid, so the task entry carries it empty."""

    world = _meaning_world(tmp_path)
    payload = _review(world, _resolve(world))
    assert payload.meaning_effects.records == ()


def test_the_outline_labels_equal_the_review_steps_for_a_read_family(
    tmp_path: Path,
) -> None:
    """The outline's labels for a read family are the review answer's step labels."""

    world = _meaning_world(tmp_path)
    resolved = _resolve(world)
    payload = _review(world, resolved, selector=_family_seed())
    entries = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert entries.state == "entries"
    assert entries.outline is not None and entries.outline.state == "outlined"
    outline = entries.outline
    family_id = text_uuid("identity", FAMILY)
    outlined = next(entry for entry in outline.families if entry.selector_id == family_id)
    assert outlined.meaning.state == "steps"
    assert list(outlined.meaning.labels) == ["clarify", "effect not recorded"]
    answer = payload.meaning_effects.for_record(family_id)
    assert answer is not None and answer.state == "steps"
    assert [step.label for step in answer.steps] == list(outlined.meaning.labels)


def test_the_outline_labels_an_unread_family_the_same_way(tmp_path: Path) -> None:
    """A row shows its effect whether or not its family has been read."""

    world = _meaning_world(tmp_path)
    resolved = _resolve(world)
    payload = _review(world, resolved, selector=_invariant_seed())
    entries = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert entries.state == "entries"
    assert entries.outline is not None and entries.outline.state == "outlined"
    family_id = text_uuid("identity", FAMILY)
    outlined = next(entry for entry in entries.outline.families if entry.selector_id == family_id)
    assert outlined.meaning.state == "steps"
    selected = payload.meaning_effects.for_record(family_id)
    assert selected is not None and selected.state == "steps"
    assert [step.label for step in selected.steps] == list(outlined.meaning.labels)


def test_the_l37_sequences_from_the_real_history_files(tmp_path: Path) -> None:
    """The packet's real case: the two L37 sequences from portable history fixtures.

    The fixtures hold the exact rows for the three packet subjects, extracted from
    the real `260928-MIK-L37` history files (memory base
    `ec67a42ff210cdccbc3696ee628881aa220d9476`: attempt 1 `ROW-A2NA0E`/`ROW-VT6B4T`, attempt 2 `ROW-DQN7HY`/
    `ROW-KY2EJG`, old family rows `ROW-FQSRA9`/`ROW-N46VKD`). They travel with the
    checkout, so the test proves the regression anywhere instead of proving that
    one disposable worktree exists.
    """

    fixture_root = tmp_path / "l37-memory"
    init_repository(fixture_root)
    (fixture_root / "knowledge" / "history").mkdir(parents=True, exist_ok=True)
    write_document(
        fixture_root,
        "knowledge/layout.json",
        {"schema": "ar-memory-layout/v2", "conversion": "test"},
    )
    for name, stem in (
        ("260928-MIK-L37.json", "l43_l37_attempt-1.json"),
        ("260928-MIK-L37-attempt-2.json", "l43_l37_attempt-2.json"),
    ):
        fixture = Path(__file__).resolve().parent / "fixtures" / stem
        assert fixture.is_file(), f"portable L37 fixture missing: {fixture}"
        (fixture_root / "knowledge" / "history" / name).write_text(
            fixture.read_text(encoding="utf-8"), encoding="utf-8"
        )
    destination = tmp_path / "l37.sqlite"
    build_index(directory_snapshot(fixture_root), destination)
    with KnowledgeIndex(destination) as index:
        first = read_record_meaning(index, "260928-MIK-L37", "INV-9NCBGQSY", "invariant")
        assert first.state == "steps"
        assert [(step.attempt, step.revision, step.effect) for step in first.steps] == [
            (1, 3, "weaken"),
            (2, 4, "clarify"),
        ]
        assert [step.row_id for step in first.steps] == ["ROW-VT6B4T", "ROW-DQN7HY"]
        second = read_record_meaning(index, "260928-MIK-L37", "INV-1XKX9ERN", "invariant")
        assert second.state == "steps"
        assert [(step.attempt, step.revision, step.effect) for step in second.steps] == [
            (1, 3, "replace"),
            (2, 4, "weaken"),
        ]
        assert [step.row_id for step in second.steps] == ["ROW-A2NA0E", "ROW-KY2EJG"]
        family = read_record_meaning(index, "260928-MIK-L37", "FAM-7B6N3Y4Q", "family")
        assert family.state == "steps"
        assert all(step.effect is None and step.revision is None for step in family.steps)
        assert [step.row_id for step in family.steps] == ["ROW-FQSRA9", "ROW-N46VKD"]


def test_the_explanations_cover_the_vocabulary_on_both_sides_of_the_wire() -> None:
    """Nine labels explained: the backend dict and the dashboard component agree.

    The component is read from the checkout under test, derived from this file's
    own path, so another checkout inspects its own component and never this leaf's
    disposable worktree.
    """

    assert tuple(EFFECT_EXPLANATIONS) == tuple(ADMITTED_EFFECT_LABELS)
    component = (
        Path(__file__).resolve().parent.parent.parent
        / "dashboard"
        / "src"
        / "panels"
        / "review"
        / "MeaningEffects.tsx"
    )
    assert component.is_file(), f"dashboard component missing: {component}"
    text = component.read_text(encoding="utf-8")
    for label, sentence in EFFECT_EXPLANATIONS.items():
        assert label in text
        assert sentence in text


# A three-member family for the roster continuation case: each member has a recorded
# step, and the second page's member carries its steps in the continued answer.
ROSTER_MEMBERS = ("INV-D11111", "INV-D11112", "INV-D11113")
ROSTER_FAMILY = "FAM-D11111"
ROSTER_EFFECTS = ("weaken", "clarify", "strengthen")


def _roster_world(tmp_path: Path) -> World:
    world = build_world(tmp_path)
    files: dict[str, str] = {}
    for member in ROSTER_MEMBERS:
        files[f"knowledge/invariants/{member}-roster.json"] = _json.dumps(
            {
                "schema": "ar-invariant/v1",
                "id": member,
                "origin": {"task": "260101-OLD", "leaf": "260101-OLD-L1"},
                "revision": 1,
                "status": "accepted",
                "statement": f"{member} holds.",
                "applicability": "Always.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
            }
        )
    files[f"knowledge/families/{ROSTER_FAMILY}-roster.json"] = _json.dumps(
        {
            "schema": "ar-family/v1",
            "id": ROSTER_FAMILY,
            "origin": {"task": "260101-OLD", "leaf": "260101-OLD-L1"},
            "revision": 1,
            "status": "accepted",
            "title": "Roster",
            "guarantee": "The three members hold together.",
            "members": list(ROSTER_MEMBERS),
            "routes": ["pkg"],
            "admission": "legacy-unassessed",
        }
    )
    rows = [
        {
            "id": f"ROW-E1111{number}",
            "subject": member,
            "disposition": "changed",
            "reason": f"{member} was judged.",
            "items": [],
            "covers": [],
            "revision": 2,
            "effect": effect,
        }
        for number, (member, effect) in enumerate(
            zip(ROSTER_MEMBERS, ROSTER_EFFECTS, strict=True), 1
        )
    ]
    rows.append(
        {
            "id": "ROW-E11114",
            "subject": ROSTER_FAMILY,
            "disposition": "changed",
            "reason": "The guarantee was restated.",
            "items": [],
            "examined": [{"id": member, "revision": 1} for member in ROSTER_MEMBERS],
            "revision": 2,
            "effect": "clarify",
        }
    )
    files[f"knowledge/history/{LEAF}.json"] = _history(rows)
    _write(world.memory_worktree, files)
    return world


def test_a_roster_continuation_carries_the_steps_of_the_members_it_returns(
    tmp_path: Path,
) -> None:
    """Members returned only by a roster continuation still carry their steps."""

    world = _roster_world(tmp_path)
    resolved = _resolve(world)
    seed = FamilyIdentitySeed(family_id=text_uuid("identity", ROSTER_FAMILY))
    seen: dict[str, list[str | None]] = {}
    cursor: str | None = None
    for _ in range(6):
        if cursor is None:
            payload = _review(world, resolved, selector=seed, page_size=1)
        else:
            payload = _review(
                world,
                resolved,
                selector=seed,
                page_of="family_members",
                continuation=cursor,
                page_size=1,
            )
        covered = {
            entry.record
            for entry in payload.meaning_effects.records
            if entry.kind == "invariant" and entry.state == "steps"
        }
        for record in covered:
            seen.setdefault(
                record,
                [
                    step.effect
                    for entry in payload.meaning_effects.records
                    if entry.record == record
                    for step in entry.steps
                ],
            )
        # Every covered member is a member this page returned: the continuation
        # carries the steps of the members it returns, no more and no fewer.
        returned = {
            member.invariant_id
            for context in payload.family_context.entries
            for side in (context.before, context.after)
            for member in side.members
            if member.invariant_id is not None
        }
        assert covered <= returned
        continuations = [
            side.page.continuation
            for context in payload.family_context.entries
            for side in (context.before, context.after)
            if side.page is not None and side.page.continuation is not None
        ]
        if not continuations:
            break
        cursor = continuations[0]
    assert set(seen) == {text_uuid("identity", member) for member in ROSTER_MEMBERS}
    assert sorted(
        effect for effects in seen.values() for effect in effects if effect is not None
    ) == sorted(ROSTER_EFFECTS)


def _retired_record() -> str:
    record = _json.loads(invariant())
    record["status"] = "retired"
    return _json.dumps(record)


def _retire_world(tmp_path: Path) -> World:
    """The converted world whose invariant record is retired in the candidate.

    The baseline still projects it, so its UUID maps there; the after tree holds
    only its raw record and history. Attempt files carry changed rows plus the
    deleted+retire row.
    """

    world = build_world(tmp_path)
    _write(
        world.memory_worktree,
        {
            f"knowledge/invariants/{INVARIANT}-land.json": _retired_record(),
            f"knowledge/history/{LEAF}.json": _history(
                [
                    {
                        "id": "ROW-R11111",
                        "subject": INVARIANT,
                        "disposition": "changed",
                        "reason": "The statement was relaxed.",
                        "items": [],
                        "covers": [],
                        "revision": 2,
                        "effect": "weaken",
                    }
                ],
            ),
            f"knowledge/history/{LEAF}-attempt-2.json": _attempt_history(
                2,
                [
                    {
                        "id": "ROW-R11112",
                        "subject": INVARIANT,
                        "disposition": "changed",
                        "reason": "The wording was tightened.",
                        "items": [],
                        "covers": [],
                        "revision": 3,
                        "effect": "clarify",
                    }
                ],
            ),
            f"knowledge/history/{LEAF}-attempt-10.json": _attempt_history(
                10,
                [
                    {
                        "id": "ROW-R11113",
                        "subject": INVARIANT,
                        "disposition": "deleted",
                        "reason": "The obligation ends with this leaf.",
                        "items": [],
                        "covers": [],
                        "revision": 4,
                        "effect": "retire",
                    }
                ],
                closed=True,
            ),
        },
    )
    return world


def test_a_retired_selection_reads_its_steps_through_compose(tmp_path: Path) -> None:
    """A retired record selected by UUID reads its after-history steps, not unavailable."""

    world = _retire_world(tmp_path)
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    answer = payload.meaning_effects.for_record(_invariant_seed().invariant_id)
    assert answer is not None and answer.state == "steps"
    assert [(step.attempt, step.revision, step.effect) for step in answer.steps] == [
        (1, 2, "weaken"),
        (2, 3, "clarify"),
        (10, 4, "retire"),
    ]
    assert [step.row_id for step in answer.steps] == ["ROW-R11111", "ROW-R11112", "ROW-R11113"]
    entries = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert entries.state == "entries"
    assert entries.outline is not None and entries.outline.state == "outlined"
    outlined = next(
        entry
        for entry in entries.outline.invariants
        if entry.selector_id == _invariant_seed().invariant_id
    )
    assert outlined.meaning.state == "steps"
    assert list(outlined.meaning.labels) == ["weaken", "clarify", "retire"]
    assert [step.label for step in answer.steps] == list(outlined.meaning.labels)


def test_a_before_only_member_reads_its_steps(tmp_path: Path) -> None:
    """A member the after tree no longer holds reads its steps by baseline identity."""

    world = build_world(tmp_path)
    (world.memory_worktree / f"knowledge/invariants/{INVARIANT}-land.json").unlink()
    _write(
        world.memory_worktree,
        {
            f"knowledge/history/{LEAF}.json": _history(
                [
                    {
                        "id": "ROW-M11111",
                        "subject": INVARIANT,
                        "disposition": "changed",
                        "reason": "The member statement was relaxed.",
                        "items": [],
                        "covers": [],
                        "revision": 2,
                        "effect": "weaken",
                    }
                ],
            ),
        },
    )
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    answer = payload.meaning_effects.for_record(_invariant_seed().invariant_id)
    assert answer is not None and answer.state == "steps"
    assert [(step.attempt, step.revision, step.effect) for step in answer.steps] == [
        (1, 2, "weaken")
    ]
    entries = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert entries.state == "entries"
    assert entries.outline is not None and entries.outline.state == "outlined"
    member_labels = [
        member.meaning.labels
        for family in entries.outline.families
        for member in family.members
        if member.selector_id == _invariant_seed().invariant_id
    ]
    assert [list(labels) for labels in member_labels] == [["weaken"]]
