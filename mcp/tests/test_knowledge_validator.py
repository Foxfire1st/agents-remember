"""MIK-R22: the mandatory knowledge validator's rules, over converted fixture trees.

Each test names the packet rule it proves. The trees are the MIK-R21 Doc14 fixtures assembled into
one converted memory tree (``knowledge_validator_test_support``); a test changes one thing and checks
that exactly the owning rule answers, with the file, the field and the rule named.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from agents_remember.memory_quality.knowledge_validator import (
    Finding,
    KnowledgeValidationError,
    ValidationContext,
    ValidationReport,
    ValidationRule,
    register_rule,
    registered_rules,
    require_valid_commit,
    validate_tree,
    validation_applies,
)
from agents_remember.memory_quality.knowledge_validator import registry as registry_module
from agents_remember.memory_quality.knowledge_validator.markers import find_markers
from agents_remember.memory_quality.knowledge_validator.trees import is_excluded_from_knowledge
from agents_remember.models.knowledge_files import history_path
from agents_remember.models.knowledge_files.history import empty_history
from knowledge_validator_test_support import (
    DIRECT_LANDING,
    DIRECT_LANDING_CARD,
    DIRECT_LANDING_SIDECAR,
    INTEGRATE,
    INTEGRATE_CARD,
    INTEGRATE_SIDECAR,
    INVARIANT,
    ROUTE_CARD,
    ROUTE_SIDECAR,
    code,
    edit_json,
    encode,
    fixture_tree_files,
    invariant_document,
    invariant_path,
    tree,
)

BLOB = "0" * 40
CONTENT = "sha256:" + "a" * 64


def _validate(files: dict[str, bytes], **kwargs: Any) -> ValidationReport:
    kwargs.setdefault("code", code())
    return validate_tree(tree(files), **kwargs)


def _rules(report: ValidationReport, *, refusing: bool = True) -> set[str]:
    selected = report.refusals if refusing else report.reports
    return {violation.rule for violation in selected}


def _only(report: ValidationReport, rule: str) -> list[str]:
    assert _rules(report) == {rule}, report.render()
    return [violation.render() for violation in report.refusals]


def _code_anchor(path: str | None = None, name: str = "helper") -> dict[str, Any]:
    anchor: dict[str, Any] = {
        "blob": BLOB,
        "content": CONTENT,
        "locator": {"kind": "symbol", "name": name},
    }
    if path is not None:
        anchor["path"] = path
    return anchor


def _add_reference(number: str, target: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    def change(document: dict[str, Any]) -> None:
        document["references"][number] = {"targets": [target]}

    return change


def test_converted_fixture_tree_passes_every_rule() -> None:
    report = _validate(fixture_tree_files())

    assert report.ok, report.render()
    assert report.violations == ()
    rule_ids = [rule.id for rule in registered_rules()]
    assert len(rule_ids) == len(set(rule_ids))
    assert {
        rule.id
        for rule in registered_rules()
        if rule.report_only and rule.owner.startswith("MIK-R22")
    } == {
        "R22.3-sidecar-without-markdown",
        "R22.3-unresolved-target",
        "R22.6-carried-stale",
    }


# --------------------------------------------------------------------------------------------------
# Rule 1: shape and canonical formatting
# --------------------------------------------------------------------------------------------------


def test_shape_refuses_a_bad_field_and_names_file_field_and_rule() -> None:
    files = edit_json(fixture_tree_files(), INVARIANT, lambda doc: doc.update(revision=0))

    [line] = _only(_validate(files), "R22.1-shape")

    assert line.startswith(f"{INVARIANT}: revision: [R22.1-shape]")


def test_non_canonical_file_names_the_formatter_command() -> None:
    files = fixture_tree_files()
    files[INVARIANT] = files[INVARIANT].replace(b"  ", b"    ")

    [line] = _only(_validate(files), "R22.1-canonical")

    assert f"agents-remember knowledge-format {INVARIANT}" in line


def test_a_file_outside_the_layout_and_a_misplaced_sidecar_are_shape_violations() -> None:
    files = fixture_tree_files()
    files["knowledge/notes.json"] = files[INVARIANT]
    files["onboarding/elsewhere/overview.json"] = files.pop(ROUTE_SIDECAR)
    files["onboarding/elsewhere/overview.md"] = files.pop(ROUTE_CARD)

    lines = _only(_validate(files), "R22.1-shape")

    assert any("knowledge/notes.json" in line and "location" in line for line in lines)
    assert any("this sidecar lives at" in line for line in lines)


# --------------------------------------------------------------------------------------------------
# Rule 2: identity
# --------------------------------------------------------------------------------------------------


def test_filename_must_begin_with_the_record_id() -> None:
    files = fixture_tree_files()
    files[invariant_path("INV-AAAAAA")] = files.pop(invariant_path("INV-R8M2TD"))

    lines = _only(_validate(files), "R22.2-identity")

    assert "the filename begins with INV-AAAAAA, not the record's ID INV-R8M2TD" in lines[0]


def test_duplicate_ids_after_a_merge_are_a_conflict_naming_both_files() -> None:
    base = fixture_tree_files()
    left = dict(base)
    left[invariant_path("INV-D0P111", "left")] = encode(invariant_document("INV-D0P111"))
    right = dict(base)
    right[invariant_path("INV-D0P111", "right")] = encode(invariant_document("INV-D0P111"))
    merged = {**left, **right}

    report = _validate(merged, bases=[tree(left, "left"), tree(right, "right")])

    [line] = _only(report, "R22.2-identity")
    assert "merge conflict: ID INV-D0P111" in line
    assert invariant_path("INV-D0P111", "left") in line
    assert invariant_path("INV-D0P111", "right") in line


def test_parallel_leaves_minting_different_ids_merge_cleanly() -> None:
    base = fixture_tree_files()
    left = {**base, invariant_path("INV-AAAAAA"): encode(invariant_document("INV-AAAAAA"))}
    right = {**base, invariant_path("INV-BBBBBB"): encode(invariant_document("INV-BBBBBB"))}

    report = _validate({**left, **right}, bases=[tree(left, "left"), tree(right, "right")])

    assert report.ok, report.render()


def test_duplicate_entry_ids_across_sidecars_are_refused() -> None:
    files = edit_json(
        fixture_tree_files(),
        INTEGRATE_SIDECAR,
        lambda doc: doc["realizes"][0].update(id="RLZ-D1RW4N"),
    )

    [line] = _only(_validate(files), "R22.2-identity")

    assert "RLZ-D1RW4N" in line and DIRECT_LANDING_SIDECAR in line and INTEGRATE_SIDECAR in line


# --------------------------------------------------------------------------------------------------
# Rule 3: markers, references, record links, relations, unresolved targets
# --------------------------------------------------------------------------------------------------


def test_a_hand_added_marker_without_a_reference_is_refused() -> None:
    """Packet non-conforming example: ``[4]`` added to ``integrate.py.md`` with no entry."""

    files = fixture_tree_files()
    files[INTEGRATE_CARD] += b"\nThe landing pair is recorded here [4].\n"

    [line] = _only(_validate(files), "R22.3-markers")

    assert line.startswith(f"{INTEGRATE_CARD}: line 3: [R22.3-markers] marker [4] has no reference")


def test_an_unused_reference_is_refused() -> None:
    files = fixture_tree_files()
    files[DIRECT_LANDING_CARD] = files[DIRECT_LANDING_CARD].replace(b" [7]", b"")

    [line] = _only(_validate(files), "R22.3-markers")

    assert line.startswith(f"{DIRECT_LANDING_SIDECAR}: references.7:")


def test_markdown_without_a_sidecar_and_a_record_card_have_no_markers() -> None:
    files = fixture_tree_files()
    files["onboarding/mcp/README.md.md"] = b"# README\n\nSee [1].\n"
    files[INVARIANT.replace(".json", ".md")] = b"# Landing pair\n\nProved by [2].\n"

    lines = _only(_validate(files), "R22.3-markers")

    assert len(lines) == 2
    assert all("without a sidecar" in line for line in lines)


def test_a_file_sidecar_without_markdown_is_reported_not_refused() -> None:
    files = fixture_tree_files()
    del files[INTEGRATE_CARD]

    report = _validate(files)

    assert report.ok, report.render()
    assert _rules(report, refusing=False) == {"R22.3-sidecar-without-markdown"}


def test_a_file_sidecar_without_markdown_holds_no_references() -> None:
    files = edit_json(
        fixture_tree_files(),
        INTEGRATE_SIDECAR,
        _add_reference("1", {"kind": "invariant", "id": "INV-7K3F9Q"}),
    )
    del files[INTEGRATE_CARD]

    assert _only(_validate(files), "R22.3-markers")[0].startswith(
        f"{INTEGRATE_SIDECAR}: references.1"
    )


def test_every_referenced_or_linked_record_id_exists() -> None:
    files = edit_json(
        fixture_tree_files(),
        DIRECT_LANDING_SIDECAR,
        lambda doc: doc["references"]["5"]["targets"][0].update(id="INV-M1SS11"),
    )
    decision = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    files = edit_json(files, decision, lambda doc: doc["links"][0].update(target="INV-M1SS22"))

    lines = _only(_validate(files), "R22.3-record-links")

    assert any("references.5.targets.0.id" in line and "INV-M1SS11" in line for line in lines)
    assert any(f"{decision}: links.0.target" in line for line in lines)


def test_a_retired_record_still_resolves() -> None:
    files = edit_json(fixture_tree_files(), INVARIANT, lambda doc: doc.update(status="retired"))

    assert _validate(files).ok


def test_an_unresolved_target_is_reported_not_refused() -> None:
    files = edit_json(
        fixture_tree_files(),
        DIRECT_LANDING_SIDECAR,
        lambda doc: doc["references"]["7"]["targets"].append(
            {"kind": "unresolved", "text": "legacy citation"}
        ),
    )

    report = _validate(files)

    assert report.ok and _rules(report, refusing=False) == {"R22.3-unresolved-target"}


def test_a_disallowed_relation_is_refused_under_the_relations_rule_by_field() -> None:
    decision = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    files = edit_json(
        fixture_tree_files(), decision, lambda doc: doc["links"][1].update(relation="bounds")
    )

    [line] = _only(_validate(files), "R22.3-relations")

    assert line.startswith(f"{decision}: links.1.relation: [R22.3-relations] relation 'bounds'")


def test_a_dot_named_card_and_its_sidecar_are_validated() -> None:
    """A card named for a dot-file (``.git-blame-ignore-revs``) is knowledge like any other."""

    files = fixture_tree_files()
    files["onboarding/.git-blame-ignore-revs.md"] = b"# .git-blame-ignore-revs\n\nSee [1].\n"
    files["onboarding/.git-blame-ignore-revs.json"] = encode(
        {
            "schema": "ar-onboarding-file/v1",
            "path": ".git-blame-ignore-revs",
            "references": {},
            "realizes": [],
        }
    )

    [line] = _only(_validate(files), "R22.3-markers")

    assert line.startswith("onboarding/.git-blame-ignore-revs.md: line 3:")
    assert "has no reference in onboarding/.git-blame-ignore-revs.json" in line  # sidecar read
    assert is_excluded_from_knowledge("onboarding/.ar-index/overview.json")
    assert is_excluded_from_knowledge("onboarding/mcp/overview.index.json")
    assert not is_excluded_from_knowledge("onboarding/.git-blame-ignore-revs.json")


# --------------------------------------------------------------------------------------------------
# Rules 4 and 5: single owner, family members
# --------------------------------------------------------------------------------------------------


def test_an_invariant_record_may_not_list_its_realizations() -> None:
    files = edit_json(
        fixture_tree_files(), INVARIANT, lambda doc: doc.update(realizations=["RLZ-D1RW4N"])
    )

    [line] = _only(_validate(files), "R22.4-single-owner")

    assert "'realizations' is not a field of this record" in line


def test_every_family_member_exists() -> None:
    family = "knowledge/families/FAM-SEQNTS6C-attribution-and-landing-pairing.json"
    files = fixture_tree_files()
    del files[invariant_path("INV-R8M2TD")]

    [line] = _only(_validate(files), "R22.5-family-members")

    assert line.startswith(f"{family}: members.1:") and "INV-R8M2TD" in line


# --------------------------------------------------------------------------------------------------
# Rule 6: anchors
# --------------------------------------------------------------------------------------------------


def test_an_added_anchor_must_name_an_existing_path() -> None:
    target = {"kind": "code", "anchor": _code_anchor("mcp/src/gone.py")}
    files = edit_json(fixture_tree_files(), DIRECT_LANDING_SIDECAR, _add_reference("8", target))
    files[DIRECT_LANDING_CARD] += b"\nAlso [8].\n"

    [line] = _only(_validate(files, bases=[tree(fixture_tree_files(), "K_B")]), "R22.6-anchor-path")

    assert "references.8.targets.0.anchor.path" in line and "mcp/src/gone.py" in line


def test_a_carried_anchor_at_a_deleted_path_is_reported_stale_not_refused() -> None:
    files = fixture_tree_files()

    report = _validate(files, bases=[tree(files, "K_B")], code=code(frozenset()))

    assert report.ok, report.render()
    # The family's carried routes are gone with the code, too: reported the same way (MIK-R04).
    assert _rules(report, refusing=False) == {"R22.6-carried-stale", "R04.1-carried-route-absent"}


def test_merge_where_one_parent_deleted_a_file_the_other_parents_card_cites() -> None:
    """Packet boundary example: the anchors are equal in one parent, so they are carried."""

    cites = fixture_tree_files()
    for number in ("8", "9", "10"):
        target = {"kind": "code", "anchor": _code_anchor("cli/knowledge_ingest.py", f"f{number}")}
        cites = edit_json(cites, DIRECT_LANDING_SIDECAR, _add_reference(number, target))
    cites[DIRECT_LANDING_CARD] += b"\nIngest [8], [9] and [10].\n"
    deleted = fixture_tree_files()

    report = _validate(
        cites, bases=[tree(cites, "citing parent"), tree(deleted, "deleting parent")]
    )

    assert report.ok, report.render()
    stale = [v for v in report.reports if v.rule == "R22.6-carried-stale"]
    assert len(stale) == 3


def test_a_re_anchored_entry_is_checked_and_a_moved_entry_carries_nothing() -> None:
    def move(document: dict[str, Any]) -> None:
        document["realizes"][0]["anchor"]["locator"]["name"] = "renamed"

    files = edit_json(fixture_tree_files(), INTEGRATE_SIDECAR, move)
    paths = frozenset({DIRECT_LANDING, "mcp/tests/test_direct_landing.py"})
    paths |= {"mcp/src/agents_remember/application/lifecycle/direct_landing.py"}

    report = _validate(files, bases=[tree(fixture_tree_files(), "K_B")], code=code(paths))

    [line] = _only(report, "R22.6-anchor-path")
    assert line.startswith(f"{INTEGRATE_SIDECAR}: realizes.RLZ-4E1C9A.anchor") and INTEGRATE in line


def test_locator_kind_and_content_hash_are_anchor_rules() -> None:
    def bad(document: dict[str, Any]) -> None:
        document["realizes"][0]["anchor"]["locator"] = {"kind": "regex", "pattern": "x"}

    files = edit_json(fixture_tree_files(), INTEGRATE_SIDECAR, bad)
    assert _rules(_validate(files)) == {"R22.6-locator"}

    files = edit_json(
        fixture_tree_files(),
        INTEGRATE_SIDECAR,
        lambda doc: doc["realizes"][0]["anchor"].update(content="md5:abc"),
    )
    assert _rules(_validate(files)) == {"R22.6-content"}


def test_an_unconverted_base_is_refused_and_a_standalone_conversion_checks_no_path() -> None:
    files = fixture_tree_files()
    unconverted = {"onboarding/x.md": b"# x\n"}

    report = _validate(files, bases=[tree(unconverted, "K_B")])
    assert _rules(report) == {"R22.6-base-converted"}

    conversion = validate_tree(
        tree(files), bases=[tree(unconverted, "K_B")], code=None, conversion=True
    )
    assert conversion.ok and conversion.violations == ()
    with pytest.raises(ValueError, match="paired code tree"):
        validate_tree(tree(files))


# --------------------------------------------------------------------------------------------------
# Rule 7: history files
# --------------------------------------------------------------------------------------------------


def _history(owner: str, *, closed: bool, anchor_path: str = INTEGRATE) -> bytes:
    document = empty_history("leaf", owner, closed=closed).to_document()
    document["rows"] = [
        {
            "id": "ROW-AAAAAA",
            "subject": "INV-RET1R3",
            "disposition": "moved",
            "reason": "re-anchored after the rename",
            "items": [],
            "revision": 1,
            "covers": [
                {
                    "id": "RLZ-4E1C9A",
                    "before": _code_anchor(anchor_path, "old"),
                    "after": _code_anchor(INTEGRATE, "new"),
                }
            ],
        }
    ]
    return encode(document)


def test_a_closed_history_file_is_frozen() -> None:
    """Packet non-conforming example: a closed leaf's history file is edited later."""

    path = history_path("260928-MIK-L01")
    base = {**fixture_tree_files(), path: _history("260928-MIK-L01", closed=True)}
    edited = dict(base)
    edited[path] = edited[path].replace(b"re-anchored", b"moved")
    deleted = {key: value for key, value in base.items() if key != path}

    assert _only(_validate(edited, bases=[tree(base, "K_B")]), "R22.7-history-frozen")
    assert (
        "was deleted"
        in _only(_validate(deleted, bases=[tree(base, "K_B")]), "R22.7-history-frozen")[0]
    )
    merged_bases = [tree(fixture_tree_files(), "left"), tree(base, "right")]
    assert _only(_validate(edited, bases=merged_bases), "R22.7-history-frozen")


def test_an_open_history_file_may_change() -> None:
    path = history_path("260928-MIK-L02")
    base = {**fixture_tree_files(), path: _history("260928-MIK-L02", closed=False)}
    edited = {**base, path: _history("260928-MIK-L02", closed=True)}

    assert _validate(edited, bases=[tree(base, "K_B")]).ok


def test_history_is_checked_for_shape_only() -> None:
    """Packet boundary example: a closed history names a renamed path and a retired subject."""

    path = history_path("260928-MIK-L03")
    renamed = "mcp/src/agents_remember/worktrees/modules/integrate_old.py"
    files = {
        **fixture_tree_files(),
        path: _history("260928-MIK-L03", closed=True, anchor_path=renamed),
    }

    assert _validate(files, bases=[tree(files, "K_B")]).ok
    files[path] = files[path].replace(b'"closed": true', b'"closed": "yes"')
    assert _rules(_validate(files)) == {"R22.1-shape"}


# --------------------------------------------------------------------------------------------------
# Rule 8: applicability, and the commit-route entry point
# --------------------------------------------------------------------------------------------------


def test_commit_routes_validate_only_when_a_side_has_the_layout_marker() -> None:
    unconverted = tree({"onboarding/x.md": b"# x [1]\n"}, "unconverted")
    converted = tree(fixture_tree_files(), "K_B")
    marker_deleted = {k: v for k, v in fixture_tree_files().items() if k != "knowledge/layout.json"}

    assert not validation_applies(unconverted, [unconverted])
    assert require_valid_commit(unconverted, bases=[unconverted], code=code()) is None
    assert require_valid_commit(converted, bases=[converted], code=code()) is not None
    with pytest.raises(KnowledgeValidationError) as refused:
        require_valid_commit(tree(marker_deleted), bases=[converted], code=code())
    assert "knowledge/layout.json: -: [R22.1-shape] the layout marker is missing" in str(
        refused.value
    )


# --------------------------------------------------------------------------------------------------
# Rule 9: the registry
# --------------------------------------------------------------------------------------------------


def test_a_later_packets_rule_runs_everywhere_and_report_only_never_refuses() -> None:
    def always(context: ValidationContext) -> list[Finding]:
        return [Finding(INVARIANT, "statement", f"checked {len(context.parsed.records)} records")]

    report_only = ValidationRule("TEST.report", "test", "reports", always, report_only=True)
    refusing = ValidationRule("TEST.refuse", "test", "refuses", always)
    converted = tree(fixture_tree_files(), "K_B")
    try:
        register_rule(report_only)
        with pytest.raises(ValueError, match="already registered"):
            register_rule(report_only)
        assert require_valid_commit(converted, bases=[converted], code=code()) is not None
        register_rule(refusing)
        with pytest.raises(KnowledgeValidationError, match=r"\[TEST.refuse\] checked 6 records"):
            require_valid_commit(converted, bases=[converted], code=code())
    finally:
        registry_module._REGISTRY.pop("TEST.report", None)
        registry_module._REGISTRY.pop("TEST.refuse", None)


# --------------------------------------------------------------------------------------------------
# Markers
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See [1] and [12].", ["1", "12"]),
        ("Escaped \\[3], code `x[4]`, double ``y`[5]`` and \\\\[6].", ["6"]),
        ("```\n[7]\n```\n~~~~\n[8]\n~~~\n[9]\n~~~~\nafter [10]", ["10"]),
        ("signals[0] and [01]", ["0", "01"]),
        ("an unclosed ` backtick [2]", ["2"]),
    ],
)
def test_markers_are_unescaped_bracketed_numbers_outside_code(
    text: str, expected: list[str]
) -> None:
    assert [marker.number for marker in find_markers(text)] == expected


def test_an_invalid_marker_number_names_how_to_write_it_as_text() -> None:
    files = fixture_tree_files()
    files[INTEGRATE_CARD] += b"\nThe first slot signals[0] is empty.\n"

    [line] = _only(_validate(files), "R22.3-markers")

    assert "[0] is not a reference number" in line and "\\[n]" in line
