"""MIK-R07: per-leaf history files (``ar-history/v1``), their rows, revision binding and freezing.

The writer's own tests belong to MIK-R12 and the validator's freeze test to MIK-R22; these cases pin
the model-level contract both of them build on: each disposition and row shape, the revision
binding, the freeze predicate, and that two leaves' history files merge without conflict.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any, get_args

import pytest
from agents_remember.models.knowledge.effect import ADMITTED_EFFECT_LABELS, EffectLabel
from agents_remember.models.knowledge_files import (
    HISTORY_ROW_KINDS,
    Anchor,
    FamilyRow,
    HistoryFile,
    InvariantRow,
    canonical_text,
    frozen_history_violation,
    history_path,
    parse_document,
    parse_history_document,
)
from agents_remember.models.knowledge_files.canonical import format_text
from agents_remember.models.knowledge_files.history import (
    FAMILY_DISPOSITIONS,
    INVARIANT_DISPOSITIONS,
    changed_family_row_violation,
    changed_record_row_violation,
    empty_history,
    invariant_revision_violation,
    is_closed_history,
    reanchor_mismatches,
    row_kind_for_subject,
    sidecar_entry_anchors,
    stale_examined_members,
    unknown_subjects,
)
from agents_remember.models.knowledge_files.ids import mint_id
from agents_remember.models.knowledge_files.sidecars import FileSidecar
from pydantic import ValidationError

LEAF = "260928-MIK-L07"
PATH = "mcp/src/agents_remember/review_source_admission.py"
INV = "INV-7K3F9Q"
FAM = "FAM-2M8HXR"


def _anchor(content: str = "1a", *, blob: str = "0", path: str | None = PATH) -> dict[str, Any]:
    anchor: dict[str, Any] = {
        "locator": {"kind": "symbol", "name": "_not_listed"},
        "blob": blob * 40,
        "content": "sha256:" + (content * 64)[:64],
    }
    if path is not None:
        anchor["path"] = path
    return anchor


def _cover(entry: str = "RLZ-0QYX992Q", before: Any = None, after: Any = None) -> dict[str, Any]:
    return {
        "id": entry,
        "before": _anchor("1a") if before is None else before,
        "after": _anchor("9c", blob="1") if after is None else after,
    }


def _invariant_row(**fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "ROW-3F9Q7K",
        "subject": INV,
        "disposition": "no_impact",
        "reason": "extracted a helper; refusal unchanged",
        "items": ["sha256:" + "e" * 64],
        "covers": [_cover()],
        "revision": 2,
    }
    return {**row, **fields}


def _family_row(**fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "ROW-9Q7K3F",
        "subject": FAM,
        "disposition": "no_impact",
        "reason": "every member still holds together",
        "items": [],
        "examined": [{"id": "INV-ZZZZZZ", "revision": 1}, {"id": INV, "revision": 2}],
    }
    return {**row, **fields}


def _history(*rows: dict[str, Any], closed: bool = False, **owner: str) -> dict[str, Any]:
    return {"schema": "ar-history/v1", **(owner or {"leaf": LEAF}), "closed": closed, "rows": rows}


def _refused(document: dict[str, Any], match: str | None = None) -> None:
    with pytest.raises(ValidationError, match=match):
        HistoryFile.model_validate(document)


# ------------------------------------------------------------------------------------------------
# The file and the packet's conforming example
# ------------------------------------------------------------------------------------------------


def test_conforming_example_parses_at_its_owner_path_and_formats_canonically() -> None:
    document = _history(_family_row(), _invariant_row())
    text = format_text(json.dumps(document))
    history = parse_history_document(history_path(LEAF), text)
    assert history_path(LEAF) == "knowledge/history/260928-MIK-L07.json"
    assert isinstance(parse_document(json.loads(text)), HistoryFile)
    assert history.owner_kind == "leaf" and history.owner_id == LEAF and not history.closed
    invariant_row, family_row = history.row_about(INV), history.row_about(FAM)
    assert isinstance(invariant_row, InvariantRow) and isinstance(family_row, FamilyRow)
    assert invariant_row.covers[0].after != invariant_row.covers[0].before
    # The model writes back exactly what was read, and the formatter sorts rows, covers, examined.
    assert canonical_text(history.to_document()) == text
    formatted = json.loads(text)
    assert [row["id"] for row in formatted["rows"]] == ["ROW-3F9Q7K", "ROW-9Q7K3F"]
    assert [m["id"] for m in formatted["rows"][1]["examined"]] == [INV, "INV-ZZZZZZ"]
    with pytest.raises(ValueError, match=r"lives at knowledge/history/260928-MIK-L07\.json"):
        parse_history_document("knowledge/history/260928-MIK-L08.json", text)
    # A no-impact judgment has no place in the invariant record file (non-conforming example).
    record = {
        "schema": "ar-invariant/v1",
        "id": INV,
        "origin": {"task": "260928-MIK", "leaf": LEAF},
        "revision": 2,
        "status": "accepted",
        "statement": "s",
        "applicability": "a",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        "history": [_invariant_row()],
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_document(record)


def test_file_shape_owner_and_row_uniqueness_are_enforced() -> None:
    wave = HistoryFile.model_validate(_history(wave="260928-MIK-W01"))
    crossing = HistoryFile.model_validate(_history(crossing="260928-MIK-crossing-2"))
    assert (wave.owner_kind, crossing.owner_kind) == ("wave", "crossing")
    _refused(_history(leaf=LEAF, wave="260928-MIK-W01"), "exactly one")
    _refused({**_history(), "leaf": None}, "explicit null")
    _refused(_history(crossing="260928-MIK-L07"), "pattern")
    _refused(_history(leaf="../x"), "pattern")
    _refused({**_history(), "closed": "true"}, "boolean")
    _refused({**_history(), "closed": 1}, "boolean")
    _refused(_history(_invariant_row(), _invariant_row(id="ROW-000000")), "row subjects")
    _refused(_history(_invariant_row(), _family_row(id="ROW-3F9Q7K")), "row ids")
    _refused(_history(_invariant_row(id="RLZ-000000")), "pattern")
    _refused(_history(_invariant_row(reason=" ")), "blank")
    _refused(_history(_invariant_row(items=["a", "a"])), "repeat")
    # A subject no registered row kind claims is refused until its kind registers (MIK-R08 rule 1).
    _refused(_history({**_invariant_row(), "subject": "hunk:abc"}), "no registered")
    _refused(_history({**_invariant_row(), "subject": FAM}), "Extra inputs")
    assert mint_id("history_row").startswith("ROW-")


def test_row_kind_registry_owns_disjoint_subject_forms() -> None:
    # MIK-R30's onboarding_trace kind is registered by MIK-R24 rule 8 step 1, its first writer;
    # MIK-R11 registers the planned row, MIK-R10 the unexplained-change row and MIK-R14 the
    # reconsideration row.
    # Leaves register in landing order, so the registry is compared as a set.
    assert {kind.name for kind in HISTORY_ROW_KINDS} == {
        "invariant",
        "family",
        "onboarding_trace",
        "planned",
        "unexplained",
        "reconsideration",
    }
    assert row_kind_for_subject(INV).model is InvariantRow
    assert row_kind_for_subject(FAM).model is FamilyRow
    assert row_kind_for_subject("onboarding:mcp/x.py").name == "onboarding_trace"
    for subject in ("DEC-7K3F9Q", "INV-0143"):
        with pytest.raises(ValueError, match="no registered"):
            row_kind_for_subject(subject)
    # D28: the seven dispositions (D6's four, extended, rerouted, assigned) plus the family's
    # `retired`, which MIK-R07 rule 5 lists; the effect vocabulary is the unchanged nine.
    assert INVARIANT_DISPOSITIONS == ("changed", "moved", "deleted", "extended", "no_impact")
    assert FAMILY_DISPOSITIONS == ("changed", "rerouted", "assigned", "retired", "no_impact")
    # The nine are declared twice: as the tuple the planned subject key is built from, and as the
    # literal type a row's effect validates against. Both are pinned to the same nine names, so a
    # tenth label cannot enter one of them and be admitted by a row or by a planned subject.
    assert ADMITTED_EFFECT_LABELS == (
        "restore",
        "clarify",
        "introduce",
        "strengthen",
        "weaken",
        "replace",
        "split",
        "merge",
        "retire",
    )
    assert get_args(EffectLabel) == ADMITTED_EFFECT_LABELS
    for effect in ADMITTED_EFFECT_LABELS:
        HistoryFile.model_validate(_history(_invariant_row(disposition="changed", effect=effect)))
        assert row_kind_for_subject(f"planned:invariant:{INV}#{effect}").name == "planned"
    _refused(_history(_invariant_row(disposition="changed", effect="preserve")))
    with pytest.raises(ValueError, match="no registered"):
        row_kind_for_subject(f"planned:invariant:{INV}#preserve")


# ------------------------------------------------------------------------------------------------
# Dispositions and row shapes (rules 2, 3 and 5)
# ------------------------------------------------------------------------------------------------

ABSENT = "absent"
_ADDED = _cover("RLZ-000001", before=ABSENT)
_REMOVED = _cover("RLZ-000002", after=ABSENT)
_UNMOVED = _cover("RLZ-000003", after=_anchor("1a"))
_REQUIREMENT = {
    "task": {"repository": "agents-remember", "path": "260928_maintained-invariant-knowledge"},
    "packet": "requirements/MIK-R09-v2-mandatory-invariant-closeout-gate.md",
    "id": "MIK-R09",
    "version": "v2",
}


@pytest.mark.parametrize(
    ("fields", "refusal"),
    [
        ({"disposition": "changed", "effect": "strengthen", "revision": 3}, None),
        (
            {"disposition": "changed", "covers": [], "because": ["DEC-7K3F9Q", _REQUIREMENT]},
            "effect",
        ),
        ({"disposition": "moved"}, None),
        ({"disposition": "moved", "covers": [_UNMOVED]}, "re-anchored"),
        # A blob-only change (same locator and content) is a re-anchoring and satisfies `moved`.
        ({"disposition": "moved", "covers": [_cover(after=_anchor("1a", blob="1"))]}, None),
        ({"disposition": "moved", "covers": [_cover(), _UNMOVED]}, None),
        ({"disposition": "moved", "covers": [_cover(), _ADDED]}, "none added or removed"),
        ({"disposition": "moved", "covers": [_cover(), _REMOVED]}, "none added or removed"),
        ({"disposition": "moved", "effect": "clarify"}, "carries no effect"),
        ({"disposition": "deleted", "covers": [_REMOVED]}, None),
        ({"disposition": "deleted", "covers": [], "effect": "retire"}, None),
        ({"disposition": "deleted", "covers": [_UNMOVED]}, "removed entry"),
        ({"disposition": "deleted", "covers": [_REMOVED], "effect": "weaken"}, "only be retire"),
        ({"disposition": "extended", "covers": [_ADDED, _UNMOVED]}, None),
        ({"disposition": "extended"}, "added entry"),
        ({"disposition": "no_impact", "covers": [_UNMOVED, _cover()]}, None),
        ({"disposition": "no_impact", "covers": [_ADDED]}, "none added or removed"),
        ({"disposition": "retired"}, "not allowed on a invariant row"),
        ({"covers": [_cover(before=ABSENT, after=ABSENT)]}, "at most one side"),
        ({"covers": [_cover(before=_anchor(path=None))]}, "names its path"),
        ({"covers": [_cover(), _cover()]}, "repeat"),
        ({"covers": [_cover("INV-000000")]}, "pattern"),
        ({"revision": 0}, "greater than or equal"),
        ({"revision": "2"}, "valid integer"),
        ({"because": []}, "at least 1"),
    ],
)
def test_invariant_row_dispositions_and_shape(fields: dict[str, Any], refusal: str | None) -> None:
    document = _history(_invariant_row(**fields))
    if refusal is not None:
        _refused(document, refusal)
        return
    history = HistoryFile.model_validate(document)
    assert history.to_document() == json.loads(json.dumps(document))


def test_family_row_dispositions_and_examined_members() -> None:
    for disposition in FAMILY_DISPOSITIONS:
        HistoryFile.model_validate(_history(_family_row(disposition=disposition)))
    for disposition in ("moved", "extended", "deleted"):
        _refused(_history(_family_row(disposition=disposition)), "not allowed on a family row")
    _refused(_history(_family_row(examined=[{"id": INV, "revision": 1}] * 2)), "repeat")
    _refused(_history(_family_row(examined=[{"id": FAM, "revision": 1}])), "pattern")
    _refused(_history(_family_row(examined=[{"id": INV}])), "Field required")
    _refused(_history(_family_row(effect="clarify")), "its no_impact row")


# ------------------------------------------------------------------------------------------------
# Writer support: re-anchoring, subjects and the revision binding (rules 2, 4 and 5)
# ------------------------------------------------------------------------------------------------


def test_after_anchor_must_equal_the_entry_anchor_in_the_candidate() -> None:
    row = InvariantRow.model_validate(
        _invariant_row(disposition="deleted", covers=[_cover(), _REMOVED])
    )
    entry = {
        "id": "RLZ-0QYX992Q",
        "invariant": INV,
        "anchor": _anchor("9c", blob="1", path=None),
        "role": "enforcement",
        "rationale": "refuses unlisted sources",
    }
    sidecar = FileSidecar.model_validate(
        {"schema": "ar-onboarding-file/v1", "path": PATH, "references": {}, "realizes": [entry]}
    )
    anchors = sidecar_entry_anchors(sidecar.path, sidecar.realizes)
    assert reanchor_mismatches(row, anchors) == []
    stale = copy.deepcopy(entry) | {"anchor": _anchor("77", blob="2", path=None)}
    moved = FileSidecar.model_validate({**sidecar.to_document(), "realizes": [stale]})
    assert reanchor_mismatches(row, sidecar_entry_anchors(PATH, moved.realizes)) == ["RLZ-0QYX992Q"]
    # The removed entry's `after` is absent, so it must be absent in K_C too.
    removed_back = anchors | {"RLZ-000002": Anchor.model_validate(_anchor())}
    assert reanchor_mismatches(row, removed_back) == ["RLZ-000002"]
    history = HistoryFile.model_validate(_history(_invariant_row(), _family_row()))
    assert unknown_subjects(history, {INV, FAM}) == []
    assert unknown_subjects(history, {INV}) == [FAM]


def test_revision_binding_for_invariant_and_family_rows() -> None:
    def violation(disposition: str, base: int | None, candidate: int, **extra: Any) -> str | None:
        row = InvariantRow.model_validate(
            _invariant_row(disposition=disposition, revision=candidate, **extra)
        )
        return invariant_revision_violation(row, base_revision=base, candidate_revision=candidate)

    assert violation("changed", 2, 3, effect="strengthen") is None
    assert "plus one" in (violation("changed", 3, 3, effect="clarify") or "")
    assert "plus one" in (violation("changed", 2, 4, effect="clarify") or ""), "no jump"
    assert violation("no_impact", 2, 2) is None
    assert "keeps the K_B revision" in (violation("moved", 2, 3) or "")
    assert violation("deleted", 2, 3, covers=[], effect="retire") is None
    # A changed row at an unchanged revision restates the leaf's own changed row of that step only.
    restated = InvariantRow.model_validate(
        _invariant_row(disposition="changed", revision=3, effect="replace")
    )
    for earlier, accepted in ((3, True), (2, False), (None, False)):
        answer = invariant_revision_violation(
            restated, base_revision=3, candidate_revision=3, restated_revision=earlier
        )
        assert (answer is None) is accepted, earlier
    # While the leaf changed the record, only its changed (or deleted) row may govern it.
    for disposition, base, candidate, refused in (
        ("no_impact", 1, 2, True),
        ("moved", 1, 3, True),
        ("changed", 1, 3, False),
        ("deleted", 1, 2, False),
        ("no_impact", 2, 2, False),
        ("no_impact", None, 1, False),
        ("no_impact", 1, None, False),
    ):
        extra = {"deleted": {"effect": "retire", "covers": []}, "changed": {"effect": "clarify"}}
        governing = InvariantRow.model_validate(
            _invariant_row(
                disposition=disposition, revision=candidate or 1, **extra.get(disposition, {})
            )
        )
        answer = changed_record_row_violation(
            governing, base_revision=base, candidate_revision=candidate
        )
        assert (answer is not None) is refused, (disposition, base, candidate)
        assert answer is None or f"a {disposition} row governs" in answer
    # A family whose guarantee the leaf changed is governed by its changed row, or by the row that
    # retires it; a route row or a no_impact row would replace that judgment.
    of_families = {
        disposition: changed_family_row_violation(
            FamilyRow.model_validate(_family_row(disposition=disposition)), guarantee_changed=True
        )
        for disposition in FAMILY_DISPOSITIONS
    }
    assert [one for one, why in of_families.items() if why is None] == ["changed", "retired"]
    assert "an assigned row governs a family whose guarantee" in (of_families["assigned"] or "")
    unchanged = FamilyRow.model_validate(_family_row())
    assert changed_family_row_violation(unchanged, guarantee_changed=False) is None
    row = InvariantRow.model_validate(_invariant_row(revision=2))
    assert "not the K_C revision 3" in (
        invariant_revision_violation(row, base_revision=2, candidate_revision=3) or ""
    )
    # A family row binds every member revision it examined: a sibling whose meaning changed after
    # the row was written makes it stale (D7); a member absent from K_C does not.
    family = FamilyRow.model_validate(_family_row())
    assert stale_examined_members(family, {INV: 2, "INV-ZZZZZZ": 1}) == []
    assert stale_examined_members(family, {INV: 3, "INV-ZZZZZZ": 1}) == [INV]
    assert stale_examined_members(family, {INV: 2}) == []


# ------------------------------------------------------------------------------------------------
# Freezing (rule 7) and parallel leaves (rule 9)
# ------------------------------------------------------------------------------------------------


def _bytes(history: HistoryFile) -> bytes:
    return canonical_text(history.to_document()).encode("utf-8")


def test_closed_in_base_implies_byte_identical_in_candidate() -> None:
    open_file = HistoryFile.model_validate(_history(_invariant_row()))
    closed = open_file.closed_copy()
    assert closed.closed and closed.closed_copy() is closed and closed.rows == open_file.rows
    edited = HistoryFile.model_validate(
        _history(_invariant_row(reason="later correction"), closed=True)
    )
    base, reformatted = _bytes(closed), json.dumps(closed.to_document()).encode("utf-8")
    assert is_closed_history(base) and not is_closed_history(_bytes(open_file))
    assert not is_closed_history(None) and not is_closed_history(b"\xff")
    assert not is_closed_history(b'{"closed": true, "schema": "ar-onboarding-file/v1"}\n')
    # Rows are editable until closeout; afterwards only the identical bytes pass.
    assert not frozen_history_violation([_bytes(open_file)], _bytes(edited))
    assert not frozen_history_violation([None], base)
    assert not frozen_history_violation([base], base)
    assert frozen_history_violation([base], _bytes(edited))
    assert frozen_history_violation([base], reformatted)
    assert frozen_history_violation([base], None)
    # A merge: closed in any parent binds the result.
    assert frozen_history_violation([None, base], _bytes(edited))
    # The closeout creates the file, closed and empty, when the leaf wrote no rows.
    empty = empty_history("leaf", LEAF, closed=True)
    assert empty.to_document() == {
        "schema": "ar-history/v1",
        "leaf": LEAF,
        "closed": True,
        "rows": [],
    }
    assert is_closed_history(_bytes(empty))


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_two_parallel_leaves_merge_their_history_files_without_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "memory"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "knowledge" / "history").mkdir(parents=True)
    (repo / "knowledge" / "layout.json").write_text('{"conversion": "v2"}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    written = {}
    for leaf, row in (("260928-MIK-L07", _invariant_row()), ("260928-MIK-L08", _family_row())):
        _git(repo, "checkout", "-q", "-b", leaf, "main")
        history = HistoryFile.model_validate(_history(row, leaf=leaf)).closed_copy()
        written[leaf] = _bytes(history)
        (repo / history_path(leaf)).parent.mkdir(parents=True, exist_ok=True)
        (repo / history_path(leaf)).write_bytes(written[leaf])
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", leaf)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-edit", "260928-MIK-L07")
    _git(repo, "merge", "-q", "--no-edit", "260928-MIK-L08")
    assert _git(repo, "status", "--porcelain") == ""
    for leaf, data in written.items():
        merged = (repo / history_path(leaf)).read_bytes()
        assert not frozen_history_violation([data], merged)
        parse_history_document(history_path(leaf), merged.decode("utf-8"))
